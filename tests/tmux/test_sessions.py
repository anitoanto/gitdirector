"""Session lifecycle and naming tests for tmux integration."""

import subprocess
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest

from gitdirector.integrations.tmux import (
    TmuxError,
    attach_tmux_session,
    create_tmux_session,
    kill_all_gd_sessions,
    kill_panel_tmux_session,
    kill_tmux_session,
    list_all_gd_sessions,
    list_repo_sessions,
    open_in_tmux,
)
from gitdirector.integrations.tmux.core import (
    _is_helper_session,
    _is_persistent_panel_session,
    _make_session_name,
    _parse_gd_session_name,
    _repo_session_name_segment,
    _session_exists,
)

_TMUX_ENV_ARGS = [
    "-e",
    "TERM=tmux-256color",
    "-e",
    "COLORTERM=truecolor",
    "-e",
    "FORCE_COLOR=3",
    "-e",
    "CLICOLOR_FORCE=1",
    "-e",
    "CLAUDE_CODE_TMUX_TRUECOLOR=1",
]


def _fake_tmux_run(repo_path, *, new_session=None):
    """A ``subprocess.run`` stand-in that behaves like a real tmux server.

    ``create_tmux_session`` reads the server's environment (to know what
    to scrub) and reads the new pane's working directory back (to prove
    the session landed in the repository), so a blanket
    ``MagicMock(returncode=0)`` is no longer a faithful stand-in: the
    pane path would come back as a mock and fail verification.

    *new_session* optionally overrides the result of ``new-session``
    calls, for collision-retry tests.
    """

    def run(args, **_kwargs):
        argv = list(args)
        if new_session is not None and argv[:3] == ["tmux", "new-session", "-d"]:
            return new_session(argv)
        if "show-environment" in argv:
            return MagicMock(returncode=0, stdout="")
        if "display-message" in argv:
            return MagicMock(returncode=0, stdout=f"{repo_path}\n")
        return MagicMock(returncode=0)

    return run


class TestMakeSessionName:
    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=[],
    )
    def test_first_session(self, _mock_list):
        repo_path = Path("/tmp/my-repo")
        repo_slug = _repo_session_name_segment(repo_path)

        name = _make_session_name(repo_path)

        assert name == f"gd/{repo_slug}/shell/1"

    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=[
            f"gd/{_repo_session_name_segment(Path('/tmp/my-repo'))}/shell/1",
            f"gd/{_repo_session_name_segment(Path('/tmp/my-repo'))}/shell/2",
        ],
    )
    def test_increments_past_existing(self, _mock_list):
        repo_path = Path("/tmp/my-repo")
        repo_slug = _repo_session_name_segment(repo_path)

        name = _make_session_name(repo_path)

        assert name == f"gd/{repo_slug}/shell/3"

    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=[
            f"gd/{_repo_session_name_segment(Path('/tmp/my-repo'))}/shell/1",
            f"gd/{_repo_session_name_segment(Path('/tmp/my-repo'))}/shell/3",
        ],
    )
    def test_increments_past_max_with_gap(self, _mock_list):
        repo_path = Path("/tmp/my-repo")
        repo_slug = _repo_session_name_segment(repo_path)

        name = _make_session_name(repo_path)

        assert name == f"gd/{repo_slug}/shell/4"

    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=[],
    )
    def test_accepts_explicit_session_snapshot(self, mock_list):
        repo_path = Path("/tmp/my-repo")
        repo_slug = _repo_session_name_segment(repo_path)

        name = _make_session_name(
            repo_path,
            sessions=[
                f"gd/{repo_slug}/shell/1",
                f"gd/{repo_slug}/shell/9",
            ],
        )

        assert name == f"gd/{repo_slug}/shell/10"
        mock_list.assert_not_called()

    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=[],
    )
    def test_ignores_malformed_sequences_and_temp_wrappers(self, _mock_list):
        repo_path = Path("/tmp/my-repo")
        repo_slug = _repo_session_name_segment(repo_path)

        name = _make_session_name(
            repo_path,
            sessions=[
                f"gd/{repo_slug}/shell/not-a-number",
                f"gd/{repo_slug}/shell/0",
                f"gd/{repo_slug}/shell/2/extra",
                f"gd/temp/panel/{repo_slug}/shell/8",
                f"gd/{repo_slug}/shell/7",
            ],
        )

        assert name == f"gd/{repo_slug}/shell/8"

    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=[f"gd/{_repo_session_name_segment(Path('/tmp/my-repo'))}/claude/1"],
    )
    def test_purpose_shell_independent_of_agent(self, _mock_list):
        repo_path = Path("/tmp/my-repo")
        repo_slug = _repo_session_name_segment(repo_path)

        name = _make_session_name(repo_path, "shell")

        assert name == f"gd/{repo_slug}/shell/1"

    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=[f"gd/{_repo_session_name_segment(Path('/tmp/my-repo'))}/claude/1"],
    )
    def test_purpose_agent(self, _mock_list):
        repo_path = Path("/tmp/my-repo")
        repo_slug = _repo_session_name_segment(repo_path)

        name = _make_session_name(repo_path, "claude")

        assert name == f"gd/{repo_slug}/claude/2"

    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=[],
    )
    def test_special_chars_sanitized(self, _mock_list):
        repo_path = Path("/tmp/foo.bar@baz")

        name = _make_session_name(repo_path)

        assert name.startswith("gd/foo-bar-baz_")
        assert name.endswith("/shell/1")

    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=[],
    )
    def test_purpose_with_slashes_is_sanitized(self, _mock_list):
        """A purpose containing ``/`` must be sanitized so the session name
        always has exactly four ``/``-separated parts. Otherwise
        ``_parse_gd_session_name`` would not recognise the session and the
        TUI Sessions tab would silently drop it.
        """
        repo_path = Path("/tmp/my-repo")
        repo_slug = _repo_session_name_segment(repo_path)

        name = _make_session_name(repo_path, "python /path/to/script.py")

        assert name == f"gd/{repo_slug}/python-path-to-script-py/1"
        parsed = _parse_gd_session_name(name)
        assert parsed is not None
        assert parsed[1] == "python-path-to-script-py"

    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=[],
    )
    def test_purpose_only_special_chars_falls_back_to_cmd(self, _mock_list):
        """A purpose that is all-special-chars sanitizes to empty; we fall
        back to ``cmd`` so the name still has a purpose segment.
        """
        repo_path = Path("/tmp/my-repo")
        repo_slug = _repo_session_name_segment(repo_path)

        name = _make_session_name(repo_path, "///")

        assert name == f"gd/{repo_slug}/cmd/1"

    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=[],
    )
    def test_string_repo_only_special_chars_falls_back_to_repo(self, _mock_list):
        name = _make_session_name("///")

        assert name == "gd/repo/shell/1"


class TestSessionExists:
    @patch("subprocess.run")
    def test_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert _session_exists("gd/repo/shell/1") is True
        mock_run.assert_called_once_with(
            ["tmux", "has-session", "-t", "=gd/repo/shell/1"],
            capture_output=True,
            env=ANY,
            cwd=ANY,
            timeout=ANY,
        )

    @patch("subprocess.run")
    def test_not_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert _session_exists("gd/repo/shell/1") is False


class TestListRepoSessions:
    @patch("subprocess.run")
    def test_returns_matching_sessions(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "gd/my-repo_abcd2/shell/1\ngd/my-repo_abcd2/claude/1\n"
                "gd/other_abcd2/shell/1\ngd/my-repo/shell/2\n"
            ),
        )
        result = list_repo_sessions("my-repo")
        assert result == ["gd/my-repo_abcd2/claude/1", "gd/my-repo_abcd2/shell/1"]

    @patch("subprocess.run")
    def test_no_sessions_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert list_repo_sessions("my-repo") == []

    @patch("subprocess.run")
    def test_no_matching_sessions(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="gd/other/shell/1\n")
        assert list_repo_sessions("my-repo") == []

    @patch("subprocess.run")
    def test_skips_temp_panel_wrappers_for_matching_repo(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "gd/my-repo_abcd2/shell/1\ngd/temp/panel/my-repo_abcd2/shell/1\n"
                "gd/my-repo_abcd2/claude/1\n"
            ),
        )

        result = list_repo_sessions("my-repo")

        assert result == ["gd/my-repo_abcd2/claude/1", "gd/my-repo_abcd2/shell/1"]


class TestListAllGdSessions:
    @patch("subprocess.run")
    def test_skips_non_gd_malformed_and_temp_panel_sessions(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "gd/alpha_abcd2/shell/1\t\t\n"
                "other-session\t\t\n"
                "gd/bad\t\t\n"
                "gd/alpha_abcd2/shell/latest\t\t\n"
                "gd/alpha_abcd2/shell/0\t\t\n"
                "gd/beta_efgh2/claude/2\t\t\n"
                "gd/temp/panel/alpha/shell/1\t\t\n"
                "gd/panel/main\t\t\n"
            ),
        )

        assert list_all_gd_sessions() == [
            {
                "session_name": "gd/alpha_abcd2/shell/1",
                "repo": "alpha",
                "repo_slug": "alpha_abcd2",
                "purpose": "shell",
                "description": "-",
            },
            {
                "session_name": "gd/beta_efgh2/claude/2",
                "repo": "beta",
                "repo_slug": "beta_efgh2",
                "purpose": "claude",
                "description": "-",
            },
        ]

    @patch("subprocess.run")
    def test_uses_stored_repo_label_when_present(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="gd/work_abcd2/shell/1\tgroup_work\t-\n",
        )

        assert list_all_gd_sessions() == [
            {
                "session_name": "gd/work_abcd2/shell/1",
                "repo": "group_work",
                "repo_slug": "work_abcd2",
                "purpose": "shell",
                "description": "-",
            }
        ]


class TestSessionNamespaceHelpers:
    def test_parse_gd_session_name_skips_temp_panel_wrapper_sessions(self):
        assert _parse_gd_session_name("gd/temp/panel/repo/shell/1") is None

    def test_parse_gd_session_name_accepts_regular_sessions_named_panel(self):
        assert _parse_gd_session_name("gd/panel/shell/1") == ("panel", "shell", "1")

    def test_parse_gd_session_name_rejects_non_numeric_sequence(self):
        assert _parse_gd_session_name("gd/repo/shell/latest") is None

    def test_parse_gd_session_name_rejects_zero_sequence(self):
        assert _parse_gd_session_name("gd/repo/shell/0") is None

    def test_persistent_panel_match_requires_exact_panel_shape(self):
        assert _is_persistent_panel_session("gd/panel/main") is True
        assert _is_persistent_panel_session("gd/panel/shell/1") is False
        assert _is_persistent_panel_session("gd/panel/") is False

    def test_helper_sessions_are_views_and_panel_builds(self):
        assert _is_helper_session("gd/view/main-2-4242") is True
        assert _is_helper_session("gd/build/0123abcd-4242") is True
        assert _is_helper_session("gd/temp/panel/repo/shell/1") is False
        assert _is_helper_session("gd/repo/shell/1") is False
        assert _is_helper_session("gd/panel/main") is False


class TestCreateTmuxSession:
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core._list_sessions", return_value=[])
    @patch("subprocess.run")
    def test_creates_and_returns_name(self, mock_run, _mock_list, mock_sync, tmp_path):
        path = tmp_path / "my-repo"
        path.mkdir()
        mock_run.side_effect = _fake_tmux_run(path)
        session_name = f"gd/{_repo_session_name_segment(path)}/shell/1"

        name = create_tmux_session("my-repo", path)

        assert name == session_name
        mock_run.assert_any_call(
            [
                "tmux",
                "new-session",
                "-d",
                *_TMUX_ENV_ARGS,
                "-s",
                session_name,
                "-x",
                ANY,
                "-y",
                ANY,
                "-c",
                str(path),
            ],
            capture_output=True,
            text=True,
            env=ANY,
            cwd=ANY,
            timeout=ANY,
        )
        mock_run.assert_any_call(
            ["tmux", "set-option", "-t", f"={session_name}:", "destroy-unattached", "off"],
            capture_output=True,
            env=ANY,
            cwd=ANY,
            timeout=ANY,
        )
        mock_sync.assert_called_once_with()

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core._list_sessions", return_value=[])
    @patch("subprocess.run")
    def test_scrubs_launch_context_and_restarts_the_shell(
        self, mock_run, _mock_list, _mock_sync, tmp_path
    ):
        """The session must not inherit gitdirector's own environment.

        tmux hands a pane the server's global environment, so the leak has
        to be removed at session scope and the shell that ``new-session``
        already started has to be replaced.
        """
        path = tmp_path / "iso-repo"
        path.mkdir()
        mock_run.side_effect = _fake_tmux_run(path)

        session_name = create_tmux_session("iso-repo", path)

        argv_calls = [list(call.args[0]) for call in mock_run.call_args_list]
        scrub = next(argv for argv in argv_calls if "set-environment" in argv)
        for name in ("CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "VIRTUAL_ENV", "PWD"):
            assert name in scrub, f"{name} was not scrubbed from the session"
        assert "-r" in scrub

        assert ["tmux", "respawn-pane", "-k", "-t", f"={session_name}:"] in argv_calls

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core._list_sessions", return_value=[])
    @patch("subprocess.run")
    def test_agent_sessions_leave_the_shell_for_the_launch_to_replace(
        self, mock_run, _mock_list, _mock_sync, tmp_path
    ):
        path = tmp_path / "agent-repo"
        path.mkdir()
        mock_run.side_effect = _fake_tmux_run(path)

        create_tmux_session("agent-repo", path, shell=False)

        argv_calls = [list(call.args[0]) for call in mock_run.call_args_list]
        assert any("set-environment" in argv for argv in argv_calls)
        assert not any("respawn-pane" in argv for argv in argv_calls)

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core._list_sessions", return_value=[])
    @patch("gitdirector.integrations.tmux.core.kill_tmux_session")
    @patch("subprocess.run")
    def test_rejects_a_session_that_did_not_land_in_the_repository(
        self, mock_run, mock_kill, _mock_list, _mock_sync, tmp_path
    ):
        """tmux falls back to ``$HOME`` silently; that must not pass."""
        path = tmp_path / "real-repo"
        path.mkdir()
        mock_run.side_effect = _fake_tmux_run(tmp_path / "somewhere-else")

        with pytest.raises(TmuxError, match="instead of the repository path"):
            create_tmux_session("real-repo", path)

        mock_kill.assert_called_once()

    def test_rejects_a_missing_repository_path(self, tmp_path):
        with pytest.raises(TmuxError, match="not a directory"):
            create_tmux_session("gone", tmp_path / "does-not-exist")

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        side_effect=[[], []],
    )
    @patch("subprocess.run")
    def test_raises_when_tmux_new_session_fails_without_collision(
        self, mock_run, _mock_list, mock_sync, tmp_path
    ):
        repo = tmp_path / "r"
        repo.mkdir()
        mock_run.return_value = subprocess.CompletedProcess(
            ["tmux", "new-session"],
            returncode=1,
            stderr="permission denied",
        )

        with pytest.raises(TmuxError):
            create_tmux_session("r", repo)

        mock_sync.assert_not_called()

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core._list_sessions")
    @patch("subprocess.run")
    def test_retries_on_tmux_name_collision_with_fresh_session_list(
        self, mock_run, mock_list, mock_sync, tmp_path
    ):
        path = tmp_path / "r"
        path.mkdir()
        repo_slug = _repo_session_name_segment(path)
        first_name = f"gd/{repo_slug}/shell/1"
        second_name = f"gd/{repo_slug}/shell/2"
        mock_list.side_effect = [[], [first_name]]

        def new_session(argv):
            session_name = argv[argv.index("-s") + 1]
            return MagicMock(returncode=1 if session_name == first_name else 0)

        mock_run.side_effect = _fake_tmux_run(path, new_session=new_session)

        name = create_tmux_session("r", path)

        assert name == second_name
        new_session_names = [
            call.args[0][call.args[0].index("-s") + 1]
            for call in mock_run.call_args_list
            if call.args[0][:3] == ["tmux", "new-session", "-d"]
        ]
        assert new_session_names == [first_name, second_name]
        mock_sync.assert_called_once_with()

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core._list_sessions", return_value=[])
    @patch("subprocess.run")
    def test_creates_with_purpose(self, mock_run, _mock_list, mock_sync, tmp_path):
        path = tmp_path / "my-repo"
        path.mkdir()
        mock_run.side_effect = _fake_tmux_run(path)
        session_name = f"gd/{_repo_session_name_segment(path)}/claude/1"

        name = create_tmux_session("my-repo", path, purpose="claude")

        assert name == session_name
        mock_run.assert_any_call(
            [
                "tmux",
                "new-session",
                "-d",
                *_TMUX_ENV_ARGS,
                "-s",
                session_name,
                "-x",
                ANY,
                "-y",
                ANY,
                "-c",
                str(path),
            ],
            capture_output=True,
            text=True,
            env=ANY,
            cwd=ANY,
            timeout=ANY,
        )
        mock_sync.assert_called_once_with()

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core._set_session_repo_label")
    @patch("gitdirector.integrations.tmux.core._list_sessions", return_value=[])
    @patch("subprocess.run")
    def test_creates_with_repo_label(
        self, mock_run, _mock_list, mock_set_label, _mock_sync, tmp_path
    ):
        path = tmp_path / "work"
        path.mkdir()
        mock_run.side_effect = _fake_tmux_run(path)
        session_name = f"gd/{_repo_session_name_segment(path)}/shell/1"

        name = create_tmux_session("work", path, repo_label="group_work")

        assert name == session_name
        mock_set_label.assert_called_once_with(session_name, "group_work")

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core._set_session_description")
    @patch("gitdirector.integrations.tmux.core._list_sessions", return_value=[])
    @patch("subprocess.run")
    def test_creates_with_description(
        self, mock_run, _mock_list, mock_set_desc, _mock_sync, tmp_path
    ):
        path = tmp_path / "my-repo"
        path.mkdir()
        mock_run.side_effect = _fake_tmux_run(path)
        session_name = f"gd/{_repo_session_name_segment(path)}/shell/1"

        name = create_tmux_session("my-repo", path, description="ready to ship")

        assert name == session_name
        mock_set_desc.assert_called_once_with(session_name, "ready to ship")

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core._set_session_description")
    @patch("gitdirector.integrations.tmux.core._list_sessions", return_value=[])
    @patch("subprocess.run")
    def test_creates_without_description_does_not_set_option(
        self, mock_run, _mock_list, mock_set_desc, _mock_sync, tmp_path
    ):
        path = tmp_path / "my-repo"
        path.mkdir()
        mock_run.side_effect = _fake_tmux_run(path)
        session_name = f"gd/{_repo_session_name_segment(path)}/shell/1"

        name = create_tmux_session("my-repo", path)

        assert name == session_name
        mock_set_desc.assert_not_called()


class TestSessionDescriptionOption:
    @patch("gitdirector.integrations.tmux.core.subprocess.run")
    def test_get_session_description_returns_stripped_value(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="  ready to ship  \n")
        from gitdirector.integrations.tmux.core import _get_session_description

        assert _get_session_description("gd/repo/shell/1") == "ready to ship"
        mock_run.assert_called_once()
        assert "show-option" in mock_run.call_args.args[0]
        assert "@gitdirector_description" in mock_run.call_args.args[0]

    @patch("gitdirector.integrations.tmux.core.subprocess.run")
    def test_get_session_description_falls_back_to_placeholder_on_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        from gitdirector.integrations.tmux.core import _get_session_description

        assert _get_session_description("gd/repo/shell/1") == "-"

    @patch("gitdirector.integrations.tmux.core.subprocess.run")
    def test_set_session_description_uses_set_option(self, mock_run):
        from gitdirector.integrations.tmux.core import _set_session_description

        _set_session_description("gd/repo/shell/1", "ready")
        mock_run.assert_called_once()
        args = mock_run.call_args.args[0]
        assert "set-option" in args
        assert "@gitdirector_description" in args
        assert args[-1] == "ready"

    @patch("gitdirector.integrations.tmux.core.subprocess.run")
    def test_set_session_description_unsets_when_empty(self, mock_run):
        from gitdirector.integrations.tmux.core import _set_session_description

        _set_session_description("gd/repo/shell/1", "")
        mock_run.assert_called_once()
        args = mock_run.call_args.args[0]
        assert "set-option" in args
        assert "-u" in args
        assert "@gitdirector_description" in args

    @patch("gitdirector.integrations.tmux.core.subprocess.run")
    def test_set_session_description_strips_whitespace(self, mock_run):
        from gitdirector.integrations.tmux.core import _set_session_description

        _set_session_description("gd/repo/shell/1", "  trim me  ")
        mock_run.assert_called_once()
        args = mock_run.call_args.args[0]
        assert args[-1] == "trim me"


class TestKillTmuxSession:
    @patch("subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert kill_tmux_session("gd/repo/shell/1") is True

    @patch("subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert kill_tmux_session("gd/repo/shell/1") is False


class TestKillAllGdSessions:
    @patch("gitdirector.integrations.tmux.core.kill_tmux_session", return_value=True)
    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=[
            "gd/panel/main",
            "gd/panel/",
            "gd/panel/main/extra",
            "gd/build/0123abcd-4242",
            "gd/view/main-1-4242",
            "user-session",
        ],
    )
    @patch(
        "gitdirector.integrations.tmux.core.list_all_gd_sessions",
        return_value=[{"session_name": "gd/repo/shell/1"}],
    )
    def test_kills_regular_and_valid_panel_sessions(
        self, _mock_list_all, _mock_list_sessions, mock_kill
    ):
        assert kill_all_gd_sessions() == [
            "gd/build/0123abcd-4242",
            "gd/panel/main",
            "gd/repo/shell/1",
            "gd/view/main-1-4242",
        ]
        assert "user-session" not in [call.args[0] for call in mock_kill.call_args_list]


class TestKillPanelTmuxSession:
    @patch("subprocess.run")
    def test_kills_panel_session(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        assert kill_panel_tmux_session("Main") is True

        assert mock_run.call_args_list[0].args == (
            ["tmux", "kill-session", "-t", "=gd/panel/main"],
        )
        assert mock_run.call_args_list[0].kwargs == {
            "capture_output": True,
            "env": ANY,
            "cwd": ANY,
            "timeout": ANY,
        }


@pytest.fixture
def sidebar_off():
    with patch("gitdirector.integrations.tmux.core._sidebar_enabled", return_value=False):
        yield


@pytest.mark.usefixtures("sidebar_off")
class TestAttachTmuxSession:
    """With the sidebar off, sessions are attached directly; their header is part of the session."""

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("subprocess.run")
    def test_inside_tmux_switches_client_to_the_session(self, mock_run, mock_sync):
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,12345,0"}):
            assert attach_tmux_session("gd/repo/shell/1") is False
        assert mock_run.call_args_list[-1].args[0] == [
            "tmux",
            "switch-client",
            "-t",
            "=gd/repo/shell/1",
        ]
        mock_sync.assert_called_once_with()

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("subprocess.run")
    def test_outside_tmux_attaches_to_the_session(self, mock_run, mock_sync):
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict("os.environ", {}, clear=True):
            assert attach_tmux_session("gd/repo/shell/1") is True
        assert [call.args[0] for call in mock_run.call_args_list] == [
            ["tmux", "has-session", "-t", "=gd/repo/shell/1"],
            ["tmux", "attach-session", "-t", "=gd/repo/shell/1"],
        ]
        # The interactive attach blocks until detach, so it must run without
        # the default tmux command timeout.
        assert "timeout" not in mock_run.call_args.kwargs
        mock_sync.assert_called_once_with()

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("subprocess.run")
    def test_non_gd_session_skips_theme_sync(self, mock_run, mock_sync):
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("plain-session")
        mock_sync.assert_not_called()

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("subprocess.run")
    def test_skip_config_sync_skips_sync(self, mock_run, mock_sync):
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("gd/alpha/shell/1", skip_config_sync=True)
        mock_sync.assert_not_called()

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("subprocess.run")
    def test_missing_session_raises_before_attaching(self, mock_run, mock_sync):
        mock_run.return_value = MagicMock(returncode=1)
        with patch.dict("os.environ", {}, clear=True), pytest.raises(TmuxError):
            attach_tmux_session("gd/alpha/shell/1")
        assert mock_run.call_count == 1
        mock_sync.assert_not_called()

    @patch("subprocess.run")
    def test_session_ending_with_nonzero_exit_is_not_an_error(self, mock_run):
        """``[server exited]`` / ``[lost server]`` exit 1 after a normal session.

        The session is over, which is what the caller waits for. Raising here
        used to escape the TUI's ``suspend`` block and freeze the app.
        """
        mock_run.side_effect = [
            MagicMock(returncode=0),  # has-session
            MagicMock(returncode=1),  # attach-session: server went away
            MagicMock(returncode=1, stderr=b"no server running"),  # has-session
        ]
        with patch.dict("os.environ", {}, clear=True):
            assert attach_tmux_session("plain-session") is True

    @patch("subprocess.run")
    def test_failed_attach_with_live_session_raises(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # has-session
            MagicMock(returncode=1),  # attach-session: e.g. not a terminal
            MagicMock(returncode=0),  # has-session: target still alive
        ]
        with patch.dict("os.environ", {}, clear=True), pytest.raises(TmuxError) as excinfo:
            attach_tmux_session("plain-session")
        assert excinfo.value.returncode == 1


class TestAttachOpensDeck:
    """With the sidebar on (the default), repository sessions open in a deck."""

    @patch("gitdirector.integrations.tmux.deck.attach_deck", return_value=True)
    @patch("gitdirector.integrations.tmux.core._sidebar_enabled", return_value=True)
    @patch("subprocess.run")
    def test_repository_session_goes_through_the_deck(self, mock_run, _enabled, mock_deck):
        mock_run.return_value = MagicMock(returncode=0)
        assert attach_tmux_session("gd/repo/shell/1") is True
        mock_deck.assert_called_once_with("gd/repo/shell/1", None)
        assert [call.args[0] for call in mock_run.call_args_list] == [
            ["tmux", "has-session", "-t", "=gd/repo/shell/1"],
        ]

    @patch("gitdirector.integrations.tmux.deck.attach_deck", return_value=True)
    @patch("subprocess.run")
    def test_a_prepared_deck_is_attached_as_is(self, mock_run, mock_deck):
        mock_run.return_value = MagicMock(returncode=0)
        attach_tmux_session("gd/repo/shell/1", deck="gd/deck/1-a")
        mock_deck.assert_called_once_with("gd/repo/shell/1", "gd/deck/1-a")

    @patch("gitdirector.integrations.tmux.deck.open_deck", return_value="gd/deck/1-a")
    @patch("gitdirector.integrations.tmux.core._session_exists", return_value=True)
    def test_prepare_attach_builds_the_deck(self, _exists, mock_open):
        from gitdirector.integrations.tmux.core import prepare_attach

        with patch("gitdirector.integrations.tmux.core._sidebar_enabled", return_value=True):
            assert prepare_attach("gd/repo/shell/1") == "gd/deck/1-a"
            assert prepare_attach("gd/panel/dev") is None
        with patch("gitdirector.integrations.tmux.core._sidebar_enabled", return_value=False):
            assert prepare_attach("gd/repo/shell/1") is None
        mock_open.assert_called_once_with("gd/repo/shell/1")

    @patch("gitdirector.integrations.tmux.deck.attach_deck")
    @patch("gitdirector.integrations.tmux.core._sidebar_enabled", return_value=True)
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core.reflow_panel_tmux_session")
    @patch("gitdirector.integrations.tmux.panels._ensure_panel_prefix_bindings")
    @patch("subprocess.run")
    def test_panels_and_other_sessions_attach_directly(
        self, mock_run, _bindings, _reflow, _sync, _enabled, mock_deck
    ):
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("gd/panel/dev")
            attach_tmux_session("plain-session")
        mock_deck.assert_not_called()

    def test_sidebar_setting_is_read_from_config(self, config):
        from gitdirector.integrations.tmux.core import _sidebar_enabled

        assert _sidebar_enabled() is True
        config.sidebar = False
        config.save()
        assert _sidebar_enabled() is False

    @patch("gitdirector.integrations.tmux.core.Config", side_effect=ValueError("bad yaml"))
    def test_unreadable_config_keeps_the_sidebar_on(self, _config):
        from gitdirector.integrations.tmux.core import _sidebar_enabled

        assert _sidebar_enabled() is True


class TestOpenInTmux:
    @patch("gitdirector.integrations.tmux.core.attach_tmux_session")
    @patch(
        "gitdirector.integrations.tmux.core.create_tmux_session",
        return_value="gd/my-repo/shell/1",
    )
    def test_creates_then_attaches(self, mock_create, mock_attach):
        path = Path("/tmp/my-repo")
        open_in_tmux("my-repo", path)
        mock_create.assert_called_once_with("my-repo", path)
        mock_attach.assert_called_once_with("gd/my-repo/shell/1", skip_config_sync=True)
