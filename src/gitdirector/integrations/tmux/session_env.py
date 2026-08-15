"""Environment isolation for gitdirector-managed tmux sessions.

A tmux pane does not inherit its environment from the process that asked
for it. It inherits the tmux **server**'s global environment merged with
its **session**'s environment. The global environment is captured once,
from whichever process happened to start the server -- for a gitdirector
user that is usually the ``gd`` CLI itself.

Without scrubbing, that means every session and every agent gitdirector
launches inherits the environment ``gd`` was launched with, for the
entire lifetime of the tmux server: the virtualenv gitdirector runs
under, the directory it was started from, and -- when ``gd`` is launched
from inside an AI agent session -- that agent's own session identity.

The last case is the dangerous one. An agent started in a gitdirector
session would see a *parent* agent's session id and exec path and can
resolve its context from the parent instead of from its own pane, which
defeats the whole point of giving each repository an isolated session.

Isolation is applied in depth, because no single layer covers every
case:

1. :func:`sanitized_environ` is handed to every tmux client gitdirector
   spawns and to the TUI's PTY, so a tmux server *forked by gitdirector*
   starts from a clean global environment.
2. :func:`session_scrub_names` drives ``set-environment -r`` at tmux
   **session** scope. Session scope overrides the server's global
   environment, so this holds even when the server was already running
   with a dirty environment long before gitdirector started. This is the
   layer that actually guarantees the invariant; the others are
   hardening.
3. :func:`child_unset_names` feeds the ``env -u`` prefix used when a pane
   is respawned to run an agent -- the last line of defence for the
   agent command itself.

Only **runtime identity** is scrubbed. Credentials and user
configuration are deliberately preserved: ``ANTHROPIC_API_KEY``,
``CLAUDE_CODE_USE_BEDROCK``, ``GITDIRECTOR_GITHUB_PAT`` and friends all
survive, because a session that cannot authenticate is not isolated, it
is broken. Anything scrubbed here must be state describing *the process
that launched gitdirector*, never something the user set on purpose for
their tools to read.

The policy is extensible at runtime through
``GITDIRECTOR_SESSION_ENV_SCRUB`` (see :data:`SCRUB_POLICY_ENV_VAR`) so a
newly shipped agent variable can be contained without a code change.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

#: Comma- or space-separated extra entries to scrub. A trailing ``*``
#: makes an entry a prefix rule (``FOO_*``); anything else is an exact
#: variable name. The variable itself is never scrubbed, so it keeps
#: working for nested gitdirector invocations.
SCRUB_POLICY_ENV_VAR = "GITDIRECTOR_SESSION_ENV_SCRUB"

# Where the launching process was, rather than anything a session should
# know. Shells recompute PWD/OLDPWD for themselves, so dropping these is
# invisible to an interactive session.
_LAUNCH_CONTEXT_NAMES = (
    "PWD",
    "OLDPWD",
    "INIT_CWD",
    "ITERM_SESSION_ID",
    "TERM_SESSION_ID",
)

# The Python runtime gitdirector itself happens to run under. Leaking
# these leaves every session in a half-activated gitdirector virtualenv,
# where `python`, `pip` and anything honouring VIRTUAL_ENV resolve
# against gitdirector's interpreter instead of the repository's.
_PYTHON_RUNTIME_NAMES = (
    "VIRTUAL_ENV",
    "VIRTUAL_ENV_PROMPT",
    "VIRTUAL_ENV_DISABLE_PROMPT",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "PYTHONEXECUTABLE",
    "PYTHONNOUSERSITE",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "__PYVENV_LAUNCHER__",
)

# Session identity of an AI agent that launched `gd`. These are what make
# a nested agent believe it is a continuation of its parent. Every name
# here is runtime state -- never configuration, never a credential.
_AGENT_RUNTIME_NAMES = (
    "CLAUDECODE",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
)

_STATIC_SCRUB_NAMES = (
    *_LAUNCH_CONTEXT_NAMES,
    *_PYTHON_RUNTIME_NAMES,
    *_AGENT_RUNTIME_NAMES,
)

# Prefix rules, for families whose members cannot be enumerated ahead of
# time. Deliberately narrow: `GITDIRECTOR_`/`GD_` are *not* here because
# that namespace also carries credentials (GITDIRECTOR_GITHUB_PAT).
_STATIC_SCRUB_PREFIXES = (
    "npm_",
    "NPM_CONFIG_",
    "PYTEST_",
)

# PATH entries that must survive PATH sanitisation no matter what, so a
# misdetected "gitdirector runtime" directory can never strip the system
# tools out of a session.
_PROTECTED_PATH_DIRS = frozenset(
    {
        "/bin",
        "/sbin",
        "/usr/bin",
        "/usr/sbin",
        "/usr/local/bin",
        "/usr/local/sbin",
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
    }
)


def _policy_extras(environ: Mapping[str, str]) -> tuple[frozenset[str], tuple[str, ...]]:
    """Parse :data:`SCRUB_POLICY_ENV_VAR` into (exact names, prefixes)."""
    raw = environ.get(SCRUB_POLICY_ENV_VAR, "") or ""
    names: set[str] = set()
    prefixes: set[str] = set()
    for entry in raw.replace(",", " ").split():
        entry = entry.strip()
        if not entry or entry == "*":
            continue
        if entry.endswith("*"):
            prefixes.add(entry[:-1])
        else:
            names.add(entry)
    return frozenset(names), tuple(sorted(prefixes))


def _policy(environ: Mapping[str, str] | None = None) -> tuple[frozenset[str], tuple[str, ...]]:
    environ = os.environ if environ is None else environ
    extra_names, extra_prefixes = _policy_extras(environ)
    names = frozenset(_STATIC_SCRUB_NAMES) | extra_names
    prefixes = tuple(sorted(set(_STATIC_SCRUB_PREFIXES) | set(extra_prefixes)))
    return names, prefixes


def is_scrubbed(name: str, environ: Mapping[str, str] | None = None) -> bool:
    """Return True when *name* must never reach a gitdirector session."""
    if not name or name == SCRUB_POLICY_ENV_VAR:
        return False
    names, prefixes = _policy(environ)
    if name in names:
        return True
    return any(prefix and name.startswith(prefix) for prefix in prefixes)


def static_scrub_names(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Names scrubbed unconditionally, whether or not they are set here.

    Emitted even when absent from gitdirector's own environment: the
    value being removed may live in the tmux server's global environment,
    which gitdirector did not necessarily create.
    """
    names, _ = _policy(environ)
    return tuple(sorted(names))


def scrub_names_in(
    candidates: Iterable[str], environ: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    """Select the names in *candidates* that the policy scrubs.

    Used to apply prefix rules to a set of variables discovered at
    runtime -- typically the tmux server's live global environment.
    """
    return tuple(sorted({name for name in candidates if is_scrubbed(name, environ)}))


def session_scrub_names(
    server_env_names: Iterable[str] = (),
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Every name to remove at tmux session scope.

    Combines the unconditional list with prefix-rule matches found in
    *server_env_names*, so a dirty pre-existing server is cleaned for
    gitdirector's sessions without touching the user's own.
    """
    discovered = set(scrub_names_in(server_env_names, environ))
    return tuple(sorted(set(static_scrub_names(environ)) | discovered))


def child_unset_names(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Names to pass as ``env -u`` when respawning a pane for an agent."""
    return static_scrub_names(environ)


def _normalize_dir(value: str) -> str:
    if not value:
        return ""
    normalized = os.path.normpath(value)
    return normalized.rstrip("/") or "/"


def gitdirector_runtime_bin_dirs(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """PATH entries that exist only because of gitdirector's own runtime.

    Removing ``VIRTUAL_ENV`` while leaving its ``bin/`` on ``PATH`` would
    be worse than doing nothing -- the session would silently resolve
    ``python`` to gitdirector's interpreter with no marker saying so. So
    the two are always handled together.

    Returns an empty tuple when gitdirector runs outside a virtualenv,
    where no PATH entry can be attributed to it.
    """
    environ = os.environ if environ is None else environ
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    candidates: list[str] = []

    virtual_env = (environ.get("VIRTUAL_ENV") or "").strip()
    if virtual_env:
        candidates.append(str(Path(virtual_env) / "bin"))
        candidates.append(str(Path(virtual_env) / "Scripts"))
    if in_venv:
        candidates.append(str(Path(sys.prefix) / "bin"))
        executable = getattr(sys, "executable", "") or ""
        if executable:
            candidates.append(str(Path(executable).parent))

    base_bin = _normalize_dir(str(Path(getattr(sys, "base_prefix", sys.prefix)) / "bin"))
    resolved: list[str] = []
    for candidate in candidates:
        normalized = _normalize_dir(candidate)
        if not normalized or normalized in _PROTECTED_PATH_DIRS or normalized == base_bin:
            continue
        if normalized not in resolved:
            resolved.append(normalized)
    return tuple(resolved)


def sanitize_path_value(path_value: str, environ: Mapping[str, str] | None = None) -> str:
    """Drop gitdirector's own runtime ``bin`` directories from a PATH."""
    if not path_value:
        return path_value
    drop = set(gitdirector_runtime_bin_dirs(environ))
    if not drop:
        return path_value
    kept = [entry for entry in path_value.split(os.pathsep) if _normalize_dir(entry) not in drop]
    # Never hand a session an empty PATH; an over-eager rule must degrade
    # to "leaked a directory", not to "no executables at all".
    if not [entry for entry in kept if entry.strip()]:
        return path_value
    return os.pathsep.join(kept)


def sanitized_environ(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of *environ* safe to hand to a tmux client or a PTY."""
    source = os.environ if environ is None else environ
    result = {name: value for name, value in source.items() if not is_scrubbed(name, source)}
    path_value = result.get("PATH")
    if path_value:
        result["PATH"] = sanitize_path_value(path_value, source)
    return result


def leaked_names(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Names present in *environ* that the policy would scrub.

    Diagnostic helper -- used by tests and by ``gd doctor`` style
    reporting to show what would have leaked.
    """
    source = os.environ if environ is None else environ
    return scrub_names_in(source.keys(), source)


__all__ = [
    "SCRUB_POLICY_ENV_VAR",
    "child_unset_names",
    "gitdirector_runtime_bin_dirs",
    "is_scrubbed",
    "leaked_names",
    "sanitize_path_value",
    "sanitized_environ",
    "scrub_names_in",
    "session_scrub_names",
    "static_scrub_names",
]
