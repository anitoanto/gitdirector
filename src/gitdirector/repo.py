from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum
from functools import lru_cache
from pathlib import Path

_RUNNING_GIT_PROCESSES: set[subprocess.Popen] = set()
_RUNNING_GIT_PROCESSES_LOCK = threading.Lock()

# SHA-1 hash of the empty tree, used to diff an unborn branch. Only a fallback:
# a SHA-256 repository reports a different hash, so it is asked for the real
# value first.
_EMPTY_TREE_SHA1 = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

_NETWORK_ERROR_RE = re.compile(
    r"connection reset"
    r"|connection refused"
    r"|connection timed out"
    r"|network is unreachable"
    r"|name or service not known"
    r"|could not resolve"
    r"|kex_exchange_identification"
    r"|ssh_exchange_identification",
    re.IGNORECASE,
)

# "permission denied" and "unable to access" also appear in purely local
# errors (a locked index, an unreadable config), so only their remote forms
# count: ssh's "Permission denied (publickey)" and an http(s) URL.
_AUTH_ERROR_RE = re.compile(
    r"could not read username"
    r"|authentication failed"
    r"|permission denied(?: \(|, please try again)"
    r"|terminal prompts disabled"
    r"|could not read from remote repository"
    r"|unable to access '[a-z][a-z0-9+.-]*://"
    r"|returned error: 40[13]"
    r"|invalid credentials"
    r"|logon failed"
    r"|repository not found"
    r"|support for password authentication was removed",
    re.IGNORECASE,
)

# A rejected or missing host key is neither a network nor a credential
# problem: it prints the same "could not read from remote repository" tail as
# an auth failure, so it has to be recognised first.
_HOST_KEY_ERROR_RE = re.compile(
    r"host key verification failed"
    r"|remote host identification has changed"
    r"|no matching host key type"
    r"|host key for .+ has changed",
    re.IGNORECASE,
)

#: The status message of a tracked repository whose directory is gone.
MISSING_REPOSITORY_MESSAGE = "Repository path not found or invalid"

# Pin the output format against user config (diff.noprefix,
# diff.mnemonicPrefix, diff.external, core.quotepath) the parser relies on.
_DIFF_CONFIG = ("-c", "core.quotepath=false")
_DIFF_OPTIONS = ("--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/")
# git's own binary heuristic looks for a NUL in the first 8000 bytes.
_BINARY_SNIFF_BYTES = 8000

_NO_COMMITS_RE = re.compile(
    r"does not have any commits yet|bad default revision 'HEAD'|ambiguous argument 'HEAD'",
    re.IGNORECASE,
)


def is_git_repository(path: Path) -> bool:
    """Return True when *path* is the top level of a git checkout.

    ``.git`` is a directory in an ordinary clone and a file in worktrees and
    submodules; both are complete checkouts that every git command accepts.
    """
    return path.is_dir() and (path / ".git").exists()


def _is_network_error(stderr: str) -> bool:
    return _NETWORK_ERROR_RE.search(stderr) is not None


def _is_host_key_error(stderr: str) -> bool:
    return _HOST_KEY_ERROR_RE.search(stderr) is not None


# Git follows this with the same "could not read from remote repository" tail
# as an auth failure, so it is checked first.
_NO_REMOTE_RE = re.compile(r"'([^']+)' does not appear to be a git repository", re.IGNORECASE)


# Matched by identity where a remote was out of reach (pull retries once).
NETWORK_ERROR_MESSAGE = "network error: could not reach remote"


def _classify_remote_error(stderr: str) -> str | None:
    missing = _NO_REMOTE_RE.search(stderr)
    if missing is not None:
        return f"no remote named {missing.group(1)}"
    if _is_network_error(stderr):
        return NETWORK_ERROR_MESSAGE
    if _is_host_key_error(stderr):
        return (
            "host key rejected: the remote's SSH host key changed or is not in ~/.ssh/known_hosts"
        )
    if _AUTH_ERROR_RE.search(stderr):
        return "authentication failed: configure git credentials for this remote"
    return None


_REMOTE_GIT_COMMANDS = frozenset({"fetch", "pull", "push", "ls-remote"})


def _is_remote_command(args: tuple[str, ...]) -> bool:
    # Local commands (commit hooks especially) can print text that looks
    # like a remote failure; only network commands are classified or retried.
    return bool(args) and args[0] in _REMOTE_GIT_COMMANDS


_MISSING_REMOTE_REF_RE = re.compile(
    r"couldn't find remote ref|^no remote named ", re.IGNORECASE | re.MULTILINE
)


def _is_missing_remote_ref_error(stderr: str) -> bool:
    return _MISSING_REMOTE_REF_RE.search(stderr) is not None


def _is_no_commits_error(stderr: str) -> bool:
    return _NO_COMMITS_RE.search(stderr) is not None


def _is_auth_error(stderr: str) -> bool:
    # No credential fixes an untrusted host key, so retrying such a command
    # with a token would only double the wait before the same failure.
    if _is_host_key_error(stderr) or _NO_REMOTE_RE.search(stderr):
        return False
    return _AUTH_ERROR_RE.search(stderr) is not None


def _github_credentials_from_config() -> tuple[str, str] | None:
    from .config import Config

    try:
        config = Config()
    except Exception:
        return None
    if not config.github_username or not config.github_PAT:
        return None
    return config.github_username, config.github_PAT


def _add_env_git_config(env: dict[str, str], key: str, value: str) -> None:
    try:
        index = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        index = 0
    env[f"GIT_CONFIG_KEY_{index}"] = key
    env[f"GIT_CONFIG_VALUE_{index}"] = value
    env["GIT_CONFIG_COUNT"] = str(index + 1)


def _apply_github_auth_env(env: dict[str, str], username: str, token: str) -> None:
    # -I: git runs the helper from the repo root, which would otherwise shadow the module.
    helper = f"!{shlex.quote(sys.executable)} -I -m gitdirector.github_credential_helper"
    _add_env_git_config(env, "credential.helper", "")
    _add_env_git_config(env, "credential.helper", helper)
    env["GITDIRECTOR_GITHUB_USERNAME"] = username
    env["GITDIRECTOR_GITHUB_PAT"] = token


# What `git rev-parse --local-env-vars` lists: they tie git to one repository.
# A parent git process (a hook, `rebase -x`, `bisect run`) leaves them set,
# and every command would then act on that repository instead.
_REPOSITORY_ENV_NAMES = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_DIR",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)


def git_env() -> dict[str, str]:
    """The environment every git command runs in: non-interactive and repository-neutral."""
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in _REPOSITORY_ENV_NAMES
        and not name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    # git asks an askpass program (VS Code's terminal sets one) before it
    # looks at GIT_TERMINAL_PROMPT; an empty one turns them all off.
    env["GIT_ASKPASS"] = ""
    env["SSH_ASKPASS_REQUIRE"] = "never"
    # A status or diff would otherwise hold .git/index.lock while refreshing
    # the index, failing the user's own `git add` or commit in that repository.
    env["GIT_OPTIONAL_LOCKS"] = "0"
    # Messages are matched in English, and paths are shown unquoted.
    env["LANGUAGE"] = "C"
    _add_env_git_config(env, "core.quotepath", "false")
    return env


def _register_running_git_process(process: subprocess.Popen) -> None:
    with _RUNNING_GIT_PROCESSES_LOCK:
        _RUNNING_GIT_PROCESSES.add(process)


def _unregister_running_git_process(process: subprocess.Popen) -> None:
    with _RUNNING_GIT_PROCESSES_LOCK:
        _RUNNING_GIT_PROCESSES.discard(process)


def _signal_process_group(process: subprocess.Popen, sig: signal.Signals) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, sig)
    except OSError:
        pass


# git removes its .lock files when terminated, never when killed: a killed
# commit or status leaves index.lock behind and every later git command in
# that repository fails until it is deleted by hand.
_TERMINATE_GRACE_SECS = 2.0


def _stop_git_processes(processes: Iterable[subprocess.Popen]) -> None:
    """Terminate *processes* and their helpers, killing whatever outlives the grace period."""
    processes = list(processes)
    for process in processes:
        _signal_process_group(process, signal.SIGTERM)
    deadline = time.monotonic() + _TERMINATE_GRACE_SECS
    for process in processes:
        try:
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _signal_process_group(process, signal.SIGKILL)


# Commit hooks (tests, linters) and large transfers routinely outlast the
# default 30 s; killing them midway leaves a half-finished commit or push.
_LONG_GIT_TIMEOUT = 600

# BatchMode: no password or passphrase prompt, which nothing could answer.
_BASE_SSH_COMMAND = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
_SSH_HOST_KEY_OPTIONS = ["-o", "StrictHostKeyChecking=accept-new"]


@lru_cache(maxsize=1)
def _default_ssh_command() -> str:
    """The ``GIT_SSH_COMMAND`` used when the caller has not set one.

    Git runs without a terminal here, so ssh can never ask "are you sure you
    want to continue connecting?": an unknown host key used to fail every
    fetch until the user connected once by hand. With ``accept-new`` (OpenSSH
    7.6+) a host is trusted on first contact and recorded in
    ``~/.ssh/known_hosts`` just as answering "yes" would, while a key that
    later changes is still refused. If the local ssh rejects the option it is
    dropped, keeping the previous behaviour rather than breaking every remote
    command.
    """
    if not _ssh_accepts(_SSH_HOST_KEY_OPTIONS):
        return shlex.join(_BASE_SSH_COMMAND)
    return shlex.join([*_BASE_SSH_COMMAND, *_SSH_HOST_KEY_OPTIONS])


def _ssh_accepts(options: list[str]) -> bool:
    """Whether the local ssh parses *options* (``ssh -G`` only reads config)."""
    try:
        probe = subprocess.run(
            ["ssh", *options, "-G", "gitdirector.invalid"],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _run_git_process(
    command: list[str], *, env: dict[str, str], timeout: int
) -> subprocess.CompletedProcess[str]:
    """Run one git command, tracking it so a shutdown can kill it.

    Each command runs in its own session so that killing it also kills any
    helper it spawned (ssh, credential helpers). Raises
    :class:`subprocess.TimeoutExpired` after killing a command that overran
    *timeout*, and :class:`FileNotFoundError` when git is not installed.
    """
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # Diffs and paths are not guaranteed UTF-8; a strict decode would
        # fail the whole command over one Latin-1 line.
        encoding="utf-8",
        errors="replace",
        env=env,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    _register_running_git_process(process)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_git_processes([process])
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        raise
    finally:
        _unregister_running_git_process(process)
    returncode = process.returncode if process.returncode is not None else 1
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def _normalize_git_result(
    returncode: int,
    stdout: str,
    stderr: str,
    *,
    strip_stdout: bool,
    remote: bool = True,
) -> tuple[int, str, str]:
    normalized_stdout = stdout.strip() if strip_stdout else stdout
    normalized_stderr = stderr.strip()

    if returncode < 0:
        return 1, normalized_stdout, normalized_stderr or "git command cancelled"

    if returncode != 0 and remote:
        classified = _classify_remote_error(normalized_stderr)
        if classified:
            return returncode, normalized_stdout, classified

    return returncode, normalized_stdout, normalized_stderr


def _parse_ahead_behind(text: str) -> tuple[int, int] | None:
    """Parse porcelain v2's ``# branch.ab +A -B`` payload."""
    parts = text.split()
    if len(parts) != 2 or not parts[0].startswith("+") or not parts[1].startswith("-"):
        return None
    try:
        return int(parts[0][1:]), int(parts[1][1:])
    except ValueError:
        return None


def _no_remote_branch_message(name: str) -> str:
    return f"No {name} branch"


def _sync_status_from_counts(ahead: int, behind: int) -> tuple[RepoStatus, str]:
    if ahead > 0 and behind > 0:
        return RepoStatus.DIVERGED, f"ahead {ahead}, behind {behind}"
    if ahead > 0:
        return RepoStatus.AHEAD, f"ahead {ahead}"
    if behind > 0:
        return RepoStatus.BEHIND, f"behind {behind}"
    return RepoStatus.UP_TO_DATE, ""


@dataclass(frozen=True)
class SyncTarget:
    """What a branch is compared with, fetched from and pulled from: its
    upstream, or ``origin/<branch>`` when it has none."""

    #: ``.`` when the upstream is a local branch.
    remote: str
    #: The branch's name on *remote*.
    branch: str
    #: The ref holding its last known state: ``refs/remotes/origin/main``.
    ref: str
    #: How git names it: ``origin/main``.
    name: str

    @classmethod
    def origin(cls, branch: str) -> SyncTarget:
        return cls("origin", branch, f"refs/remotes/origin/{branch}", f"origin/{branch}")

    @property
    def pull_arguments(self) -> list[str]:
        return ["pull", "--ff-only", self.remote, self.branch]


@dataclass(frozen=True)
class _Sync:
    counts: tuple[int, int] | None
    message: str = ""
    upstream: str = ""
    missing: bool = False
    fetch_error: str = ""


class RepoStatus(Enum):
    UP_TO_DATE = "up-to-date"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    UNKNOWN = "unknown"


@dataclass
class RepositoryInfo:
    path: Path
    name: str
    status: RepoStatus
    branch: str | None = None
    message: str = ""
    staged: bool = False
    unstaged: bool = False
    staged_files: list[str] | None = None
    unstaged_files: list[str] | None = None
    last_updated: str | None = None
    last_commit_timestamp: int | None = None
    size: int | None = None
    #: True when the remote could not be fetched, so the sync status was
    #: computed from the refs already on disk and may be out of date.
    sync_stale: bool = False
    #: Commits HEAD is ahead of and behind :attr:`upstream`.
    ahead: int = 0
    behind: int = 0
    #: Why git could not read the repository at all ("" when it could).
    error: str = ""
    #: :attr:`upstream` does not exist (yet), so there is nothing to sync with.
    no_remote_branch: bool = False
    #: What the branch is compared with (``origin/main``), "" when detached.
    upstream: str = ""

    def __repr__(self) -> str:
        return f"{self.name:<30} {self.status.value:<12} {self.branch or 'N/A':<15}"


class Repository:
    def __init__(self, path: Path):
        if not is_git_repository(path):
            raise ValueError(f"Not a git repository: {path}")
        self.path = path
        self.name = path.name

    @classmethod
    def kill_running_git_commands(cls) -> None:
        with _RUNNING_GIT_PROCESSES_LOCK:
            processes = tuple(_RUNNING_GIT_PROCESSES)
        _stop_git_processes(processes)

    def _git_env(
        self, *, remote: bool = False, github_auth: tuple[str, str] | None = None
    ) -> dict[str, str]:
        env = git_env()
        if remote and not self._has_own_ssh_command(env):
            env["GIT_SSH_COMMAND"] = _default_ssh_command()
        if github_auth is not None:
            username, token = github_auth
            _apply_github_auth_env(env, username, token)
        return env

    def _has_own_ssh_command(self, env: dict[str, str]) -> bool:
        """Whether the user chose how git runs ssh here, which then wins over ours."""
        if "GIT_SSH_COMMAND" in env or "GIT_SSH" in env:
            return True
        try:
            result = _run_git_process(
                ["git", "-C", str(self.path), "config", "--get", "core.sshCommand"],
                env=env,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return bool(result.stdout.strip())

    def _run_git_once(
        self,
        *args: str,
        _strip: bool,
        _timeout: int,
        _github_auth: tuple[str, str] | None = None,
    ) -> tuple[int, str, str, str]:
        env = self._git_env(remote=_is_remote_command(args), github_auth=_github_auth)
        command = ["git", "-C", str(self.path), *args]
        try:
            result = _run_git_process(command, env=env, timeout=_timeout)
        except subprocess.TimeoutExpired:
            return 1, "", "git command timed out", "git command timed out"
        except FileNotFoundError:
            return 1, "", "git not found", "git not found"

        code, out, err = _normalize_git_result(
            result.returncode,
            result.stdout,
            result.stderr,
            strip_stdout=_strip,
            remote=_is_remote_command(args),
        )
        return code, out, err, result.stderr.strip()

    def _run_git(self, *args: str, _strip: bool = True, _timeout: int = 30) -> tuple[int, str, str]:
        code, out, err, raw_err = self._run_git_once(*args, _strip=_strip, _timeout=_timeout)
        if code == 0 or not _is_remote_command(args) or not _is_auth_error(raw_err or err):
            return code, out, err

        github_auth = _github_credentials_from_config()
        if github_auth is None:
            return code, out, err

        retry_code, retry_out, retry_err, _ = self._run_git_once(
            *args,
            _strip=_strip,
            _timeout=_timeout,
            _github_auth=github_auth,
        )
        return retry_code, retry_out, retry_err

    def get_current_branch(self) -> str | None:
        code, out, _ = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        return out if code == 0 and out not in {"", "HEAD"} else None

    def get_pull_target(self) -> tuple[SyncTarget | None, str | None]:
        """The current branch's :class:`SyncTarget`, or None and why there is none."""
        code, branch, err = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        if code != 0:
            return None, err or "Could not determine current branch"
        if branch in {"", "HEAD"}:
            return None, "Cannot pull in detached HEAD"
        return self.sync_target(branch), None

    def sync_target(self, branch: str) -> SyncTarget:
        """*branch*'s upstream, as git has it configured; ``origin/<branch>`` without one."""
        refname = f"refs/heads/{branch}"
        code, out, _ = self._run_git(
            "for-each-ref",
            "--format=%(refname)%00%(upstream)%00%(upstream:short)"
            "%00%(upstream:remotename)%00%(upstream:remoteref)",
            refname,
        )
        for line in out.splitlines() if code == 0 else ():
            name, ref, short, remote, remote_ref = (line.split("\0") + [""] * 5)[:5]
            if name == refname and ref and remote and remote_ref.startswith("refs/heads/"):
                return SyncTarget(remote, remote_ref.removeprefix("refs/heads/"), ref, short)
        return SyncTarget.origin(branch)

    def _read_only_output(
        self,
        *args: str,
        empty_text: str,
        failure_text: str,
        allow_no_commits: bool = False,
    ) -> tuple[bool, str]:
        code, out, err = self._run_git(*args, _strip=False)
        output = out.rstrip()
        if code == 0:
            return True, output or empty_text
        if allow_no_commits and _is_no_commits_error(err):
            return True, empty_text
        return False, err or failure_text

    def status_output(self) -> tuple[bool, str]:
        return self._read_only_output(
            "-c",
            "color.status=always",
            "status",
            empty_text="Working tree clean.",
            failure_text="git status failed",
        )

    def timeline_output(self) -> tuple[bool, str]:
        return self._read_only_output(
            "log",
            "--max-count=1000",
            "--graph",
            "--decorate",
            "--all",
            "--color=always",
            "--date=short",
            "--pretty=format:%C(auto)%h%Creset %C(blue)%ad%Creset %C(auto)%d%Creset %s",
            empty_text="No commits yet.",
            failure_text="git log failed",
            allow_no_commits=True,
        )

    def branches_output(self) -> tuple[bool, str]:
        return self._read_only_output(
            "branch",
            "-a",
            "--color=always",
            empty_text="No branches found.",
            failure_text="git branch -a failed",
        )

    def remotes_output(self) -> tuple[bool, str]:
        return self._read_only_output(
            "remote",
            "-v",
            empty_text="No remotes configured.",
            failure_text="git remote -v failed",
        )

    def _fetch(self, target: SyncTarget) -> str:
        """Fetch *target*; "" once done, or when the remote has no such branch."""
        if target.remote == ".":
            return ""
        code, _, err = self._run_git("fetch", target.remote, f"refs/heads/{target.branch}")
        # The remote answered: it just has no such remote or branch.
        if code == 0 or _is_missing_remote_ref_error(err):
            return ""
        return err or f"could not fetch from {target.remote}"

    def _compare(self, target: SyncTarget) -> _Sync:
        code, out, err = self._run_git(
            "rev-list", "--left-right", "--count", f"HEAD...{target.ref}"
        )
        if code != 0:
            ref_code, _, _ = self._run_git("show-ref", "--verify", "--quiet", target.ref)
            if ref_code != 0:
                message = _no_remote_branch_message(target.name)
                return _Sync(None, message, target.name, missing=True)
            return _Sync(None, err or f"Could not compare HEAD with {target.name}", target.name)
        parts = out.split()
        try:
            return _Sync((int(parts[0]), int(parts[1])), upstream=target.name)
        except (IndexError, ValueError):
            return _Sync(None, "Could not determine sync status", target.name)

    def _sync(
        self,
        branch: str | None,
        upstream: str | None,
        ahead_behind: tuple[int, int] | None,
        *,
        fetch: bool,
    ) -> _Sync:
        """How *branch* compares with its :class:`SyncTarget`.

        *upstream* and *ahead_behind* are what ``git status`` already found
        for the configured upstream; they hold unless a fetch moved it. No
        counts for an upstream means it is gone, or git's line was unreadable:
        rev-list and show-ref tell which.
        """
        if branch is None:
            return _Sync(None, "Detached HEAD")
        fetch_error = ""
        if fetch:
            target = self.sync_target(branch)
            fetch_error = self._fetch(target)
            if not fetch_error:
                return self._compare(target)
        if upstream is None:
            sync = self._compare(SyncTarget.origin(branch))
        elif ahead_behind is None:
            sync = self._compare(self.sync_target(branch))
        else:
            sync = _Sync(ahead_behind, upstream=upstream)
        return replace(sync, fetch_error=fetch_error)

    def get_last_commit_info(self) -> tuple[str | None, int | None]:
        code, out, _ = self._run_git("log", "-1", "--format=%cd%n%ct", "--date=relative")
        if code != 0 or not out:
            return None, None
        lines = out.split("\n", 1)
        date = lines[0] or None
        ts: int | None = None
        if len(lines) > 1 and lines[1]:
            try:
                ts = int(lines[1])
            except ValueError:
                ts = None
        return date, ts

    def get_tracked_size(self) -> int | None:
        """Return total byte size of all tracked files (respects .gitignore)."""
        code, out, _ = self._run_git("ls-tree", "-r", "-l", "--full-tree", "HEAD", _strip=False)
        if code != 0 or not out:
            return None
        total = 0
        for line in out.split("\n"):
            parts = line.split(None, 4)
            if len(parts) < 4:
                continue
            try:
                total += int(parts[3])
            except ValueError:
                continue
        return total

    def get_status(self, *, fetch: bool = False, include_size: bool = True) -> RepositoryInfo:
        code, out, err = self._run_git("status", "--porcelain=v2", "--branch", _strip=False)
        if code != 0:
            # git's own words: a corrupt index, "detected dubious ownership", ...
            error = err.removeprefix("fatal: ").strip() or "git status failed"
            return RepositoryInfo(
                self.path, self.name, RepoStatus.UNKNOWN, None, error, error=error
            )

        branch = None
        upstream: str | None = None
        ahead_behind: tuple[int, int] | None = None
        staged = False
        unstaged = False
        staged_files: list[str] = []
        unstaged_files: list[str] = []

        for line in out.splitlines():
            if line.startswith("# branch.head "):
                branch = line[14:]
                if branch == "(detached)":
                    branch = None
            elif line.startswith("# branch.upstream "):
                upstream = line[18:]
            elif line.startswith("# branch.ab "):
                ahead_behind = _parse_ahead_behind(line[12:])
            elif line.startswith(("1 ", "2 ")):
                xy = line[2:4]
                x, y = xy[0], xy[1]
                if line.startswith("1 "):
                    parts = line.split(" ", 8)
                    filename = parts[8] if len(parts) > 8 else ""
                else:
                    parts = line.split(" ", 9)
                    filename = parts[9].split("\t")[0] if len(parts) > 9 else ""
                if x not in (".", "?"):
                    staged = True
                    staged_files.append(filename)
                if y not in (".", "?"):
                    unstaged = True
                    unstaged_files.append(filename)
            elif line.startswith("u "):
                parts = line.split(" ", 10)
                filename = parts[10] if len(parts) > 10 else ""
                staged = True
                unstaged = True
                staged_files.append(filename)
                unstaged_files.append(filename)
            elif line.startswith("? "):
                filename = line[2:]
                unstaged = True
                unstaged_files.append(filename)

        sync = self._sync(branch, upstream, ahead_behind, fetch=fetch)
        counts, msg, fetch_error = sync.counts, sync.message, sync.fetch_error
        if counts is None:
            status = RepoStatus.UNKNOWN
        else:
            status, msg = _sync_status_from_counts(*counts)

        if fetch_error:
            # A remote that cannot be reached says nothing about how this
            # branch compares with the ref already on disk: report that
            # (possibly stale) comparison instead of blanking every repository
            # to "unknown".
            msg = f"{msg} ({fetch_error})" if msg else fetch_error

        last_updated, last_commit_ts = self.get_last_commit_info()
        size = self.get_tracked_size() if include_size else None

        return RepositoryInfo(
            self.path,
            self.name,
            status,
            branch,
            msg,
            staged,
            unstaged,
            staged_files or None,
            unstaged_files or None,
            last_updated,
            last_commit_ts,
            size,
            bool(fetch_error),
            *(counts or (0, 0)),
            no_remote_branch=sync.missing,
            upstream=sync.upstream,
        )

    def pull(self, *, retries: int = 1) -> tuple[bool, str]:
        target, err = self.get_pull_target()
        if target is None:
            return False, err or "Could not determine pull target"

        attempts = max(1, 1 + retries)
        for attempt in range(attempts):
            code, out, err = self._run_git(*target.pull_arguments, _timeout=_LONG_GIT_TIMEOUT)
            if code == 0:
                return True, out
            if attempt < attempts - 1 and err == NETWORK_ERROR_MESSAGE:
                continue
            return False, err
        return False, "git pull failed"

    def add(self, paths: list[str] | None = None) -> tuple[bool, str]:
        """Stage files for the next commit.

        With ``paths=None`` runs ``git add -A`` to stage every change
        (tracked modifications plus untracked, minus paths excluded by
        the repo's ``.gitignore``). With an explicit ``paths`` list runs
        ``git add -- <paths>`` to stage only those relative paths.
        """
        if paths:
            code, out, err = self._run_git("add", "--", *paths)
        else:
            code, out, err = self._run_git("add", "-A")
        if code != 0:
            return False, err or out or "git add failed"
        return True, out

    def commit(self, message: str) -> tuple[bool, str]:
        """Create a commit with ``message``; returns ``(ok, output)``."""
        if not message or not message.strip():
            return False, "commit message is empty"
        code, out, err = self._run_git("commit", "-m", message, _timeout=_LONG_GIT_TIMEOUT)
        if code != 0:
            return False, err or out or "git commit failed"
        return True, out

    def push_arguments(self) -> list[str]:
        """``push`` for a branch that tracks a remote one; ``push -u origin
        <branch>`` for one pushed for the first time, so it tracks it after."""
        code, _, _ = self._run_git(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
        )
        if code == 0:
            return ["push"]
        branch = self.get_current_branch()
        return ["push", "-u", "origin", branch] if branch else ["push"]

    def push(self, arguments: list[str] | None = None) -> tuple[bool, str]:
        """Push the current branch with *arguments*, :meth:`push_arguments` by default."""
        arguments = self.push_arguments() if arguments is None else arguments
        code, out, err = self._run_git(*arguments, _timeout=_LONG_GIT_TIMEOUT)
        if code != 0:
            return False, err or out or "git push failed"
        return True, out

    def _empty_tree_hash(self) -> str:
        """Return the empty tree's hash in this repository's object format."""
        code, out, _ = self._run_git("hash-object", "-t", "tree", "--stdin")
        if code == 0 and re.fullmatch(r"[0-9a-f]{40,64}", out):
            return out
        return _EMPTY_TREE_SHA1

    def get_diff_against_head(self) -> tuple[bool, str, list[str]]:
        """Return combined uncommitted diff plus list of untracked file paths.

        The diff covers both staged and unstaged changes for tracked files,
        produced by ``git diff HEAD`` so reviewers see a single coherent
        picture of uncommitted work. Untracked files are returned separately
        because git cannot produce a diff for files that have no HEAD entry.
        """
        code, diff_text, err = self._run_git(
            *_DIFF_CONFIG, "diff", "HEAD", *_DIFF_OPTIONS, _strip=False, _timeout=_LONG_GIT_TIMEOUT
        )
        if code != 0 and _is_no_commits_error(err):
            code, diff_text, err = self._run_git(
                *_DIFF_CONFIG,
                "diff",
                self._empty_tree_hash(),
                *_DIFF_OPTIONS,
                _strip=False,
                _timeout=_LONG_GIT_TIMEOUT,
            )
        if code != 0:
            return False, err or "git diff failed", []

        untracked = self._list_untracked_files()
        if diff_text and not diff_text.endswith("\n"):
            diff_text += "\n"
        return True, diff_text, untracked

    def _list_untracked_files(self) -> list[str]:
        code, out, _ = self._run_git(
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            _strip=False,
            _timeout=_LONG_GIT_TIMEOUT,
        )
        if code != 0 or not out:
            return []
        return [chunk for chunk in out.split("\x00") if chunk]

    def read_file_text(self, rel_path: str) -> str | None:
        """Read a tracked or untracked file's whole text content.

        Returns ``None`` if the file is missing, binary, or unreadable.
        """
        candidate = self.path / rel_path
        if not candidate.is_file():
            return None
        try:
            with candidate.open("rb") as fh:
                # A large binary is rejected without reading the rest of it.
                head = fh.read(_BINARY_SNIFF_BYTES)
                if b"\x00" in head:
                    return None
                rest = fh.read()
        except OSError:
            return None
        if b"\x00" in rest:
            return None
        data = head + rest if rest else head
        return data.decode("utf-8", errors="replace")
