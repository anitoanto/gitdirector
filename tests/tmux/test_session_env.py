"""Unit tests for the tmux session environment isolation policy.

The policy has two failure modes and they pull in opposite directions:
leaking gitdirector's launch context into a session, or scrubbing so
broadly that a session loses credentials or configuration the user set
on purpose. Both are covered here.
"""

from __future__ import annotations

import os

from gitdirector.integrations.tmux import core, session_env


class TestScrubPolicy:
    def test_agent_session_identity_is_scrubbed(self):
        """The variables that make a nested agent adopt its parent's session."""
        for name in (
            "CLAUDECODE",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_ENTRYPOINT",
            "CLAUDE_CODE_EXECPATH",
            "CLAUDE_CODE_CHILD_SESSION",
            "CLAUDE_PID",
        ):
            assert session_env.is_scrubbed(name), f"{name} must not reach a session"

    def test_launch_context_is_scrubbed(self):
        for name in ("PWD", "OLDPWD", "INIT_CWD"):
            assert session_env.is_scrubbed(name)

    def test_gitdirector_python_runtime_is_scrubbed(self):
        for name in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__"):
            assert session_env.is_scrubbed(name)

    def test_credentials_and_user_config_survive(self):
        """A session that cannot authenticate is broken, not isolated."""
        for name in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
            "GITDIRECTOR_GITHUB_PAT",
            "GITDIRECTOR_GITHUB_USERNAME",
            "GITHUB_TOKEN",
            "OPENAI_API_KEY",
            "SSH_AUTH_SOCK",
            "HOME",
            "PATH",
            "SHELL",
            "LANG",
        ):
            assert not session_env.is_scrubbed(name), f"{name} must survive"

    def test_prefix_rules_apply(self):
        assert session_env.is_scrubbed("npm_package_name")
        assert session_env.is_scrubbed("npm_config_registry")
        assert session_env.is_scrubbed("PYTEST_CURRENT_TEST")

    def test_static_names_are_sorted_and_unique(self):
        names = session_env.static_scrub_names()
        assert list(names) == sorted(set(names))

    def test_scrubbing_never_contradicts_what_gitdirector_injects(self):
        """Drift guard.

        ``core`` deliberately sets a handful of variables on every pane
        (TERM, colour capability advertisement, and the Claude Code
        truecolor opt-out). Scrubbing one of those would mean gitdirector
        setting and unsetting the same name, so the two lists must stay
        disjoint as either grows.
        """
        injected = set(core._TMUX_CHILD_ENV)
        scrubbed = set(session_env.static_scrub_names())
        assert injected & scrubbed == set()
        assert not any(session_env.is_scrubbed(name) for name in injected)


class TestPolicyExtension:
    def test_extra_exact_name(self, monkeypatch):
        monkeypatch.setenv(session_env.SCRUB_POLICY_ENV_VAR, "SOME_NEW_AGENT_SESSION")
        assert session_env.is_scrubbed("SOME_NEW_AGENT_SESSION")
        assert "SOME_NEW_AGENT_SESSION" in session_env.static_scrub_names()

    def test_extra_prefix_glob(self, monkeypatch):
        monkeypatch.setenv(session_env.SCRUB_POLICY_ENV_VAR, "NEWAGENT_*")
        assert session_env.is_scrubbed("NEWAGENT_SESSION_ID")
        assert not session_env.is_scrubbed("UNRELATED_SESSION_ID")

    def test_comma_and_space_separated(self, monkeypatch):
        monkeypatch.setenv(session_env.SCRUB_POLICY_ENV_VAR, "A_VAR, B_VAR  C_*")
        assert session_env.is_scrubbed("A_VAR")
        assert session_env.is_scrubbed("B_VAR")
        assert session_env.is_scrubbed("C_ANYTHING")

    def test_bare_star_is_ignored(self, monkeypatch):
        """A lone ``*`` must not scrub the entire environment."""
        monkeypatch.setenv(session_env.SCRUB_POLICY_ENV_VAR, "*")
        assert not session_env.is_scrubbed("HOME")
        assert not session_env.is_scrubbed("PATH")

    def test_policy_variable_never_scrubs_itself(self, monkeypatch):
        """It must keep working for a nested gitdirector invocation."""
        monkeypatch.setenv(session_env.SCRUB_POLICY_ENV_VAR, session_env.SCRUB_POLICY_ENV_VAR)
        assert not session_env.is_scrubbed(session_env.SCRUB_POLICY_ENV_VAR)


class TestSanitizedEnviron:
    def test_drops_leaks_and_keeps_the_rest(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-abc")
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-keep-me")
        monkeypatch.setenv("PWD", "/somewhere/gitdirector/was/launched")

        result = session_env.sanitized_environ()

        assert "CLAUDE_CODE_SESSION_ID" not in result
        assert "CLAUDECODE" not in result
        assert "PWD" not in result
        assert result["ANTHROPIC_API_KEY"] == "sk-keep-me"

    def test_does_not_mutate_the_source(self, monkeypatch):
        monkeypatch.setenv("CLAUDECODE", "1")
        session_env.sanitized_environ()
        assert os.environ["CLAUDECODE"] == "1"

    def test_accepts_an_explicit_mapping(self):
        source = {"CLAUDECODE": "1", "KEEP": "yes"}
        assert session_env.sanitized_environ(source) == {"KEEP": "yes"}

    def test_leaked_names_reports_what_would_have_escaped(self):
        source = {"CLAUDECODE": "1", "HOME": "/home/x", "VIRTUAL_ENV": "/v"}
        assert session_env.leaked_names(source) == ("CLAUDECODE", "VIRTUAL_ENV")


class TestPathSanitization:
    def test_strips_the_virtualenv_bin_gitdirector_runs_under(self):
        source = {"VIRTUAL_ENV": "/opt/gd/.venv"}
        path = os.pathsep.join(["/opt/gd/.venv/bin", "/usr/bin", "/bin"])
        assert session_env.sanitize_path_value(path, source) == os.pathsep.join(
            ["/usr/bin", "/bin"]
        )

    def test_keeps_path_untouched_without_a_virtualenv(self):
        source: dict[str, str] = {}
        path = os.pathsep.join(["/usr/local/bin", "/usr/bin", "/bin"])
        # Outside a venv there is no directory attributable to gitdirector.
        assert "/usr/bin" in session_env.sanitize_path_value(path, source)

    def test_never_strips_system_directories(self):
        """An over-eager rule must never remove the system tools."""
        source = {"VIRTUAL_ENV": "/usr/local"}
        path = os.pathsep.join(["/usr/local/bin", "/usr/bin"])
        assert session_env.sanitize_path_value(path, source) == path

    def test_never_returns_an_empty_path(self):
        source = {"VIRTUAL_ENV": "/opt/gd/.venv"}
        assert session_env.sanitize_path_value("/opt/gd/.venv/bin", source) == "/opt/gd/.venv/bin"

    def test_normalizes_before_comparing(self):
        source = {"VIRTUAL_ENV": "/opt/gd/.venv"}
        path = os.pathsep.join(["/opt/gd/.venv/bin/", "/usr/bin"])
        assert session_env.sanitize_path_value(path, source) == "/usr/bin"


class TestSessionScrubNames:
    def test_includes_static_names_even_when_unset_here(self):
        names = session_env.session_scrub_names(())
        assert "CLAUDE_CODE_SESSION_ID" in names

    def test_discovers_prefix_matches_from_the_server_environment(self):
        """Prefix rules must catch names gitdirector never saw itself.

        The tmux server may hold variables from whichever process started
        it, which is not necessarily gitdirector.
        """
        names = session_env.session_scrub_names(["npm_package_version", "HOME"])
        assert "npm_package_version" in names
        assert "HOME" not in names

    def test_result_is_sorted_and_unique(self):
        names = session_env.session_scrub_names(["CLAUDECODE", "npm_config_x"])
        assert list(names) == sorted(set(names))


class TestChildUnsetNames:
    def test_core_prefix_unsets_every_scrubbed_name(self):
        """The ``env -u`` fallback must cover the whole policy."""
        prefix = core._tmux_child_environment_prefix()
        for name in session_env.static_scrub_names():
            assert f"-u {name}" in prefix

    def test_core_keeps_the_colour_opt_out(self):
        assert "NO_COLOR" in core._tmux_child_unset_names()
