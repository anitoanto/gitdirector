"""Session lifecycle and naming tests for tmux integration."""

import os
import subprocess
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest

from gitdirector.integrations.tmux import (
    TmuxError,
    _is_persistent_panel_session,
    _is_temp_panel_session,
    _make_session_name,
    _parse_gd_session_name,
    _repo_session_name_segment,
    _session_exists,
    attach_tmux_session,
    create_tmux_session,
    ensure_temp_panel_tmux_session,
    kill_panel_tmux_session,
    kill_tmux_session,
    list_all_gd_sessions,
    list_repo_sessions,
    open_in_tmux,
)

_TMUX_ENV_ARGS = [
    "-e",
    "NO_COLOR=",
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
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert _session_exists("gd/repo/shell/1") is True
        mock_run.assert_called_once_with(
            ["tmux", "has-session", "-t", "=gd/repo/shell/1"],
            capture_output=True,
        )

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_not_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert _session_exists("gd/repo/shell/1") is False


class TestListRepoSessions:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_returns_matching_sessions(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="gd/my-repo/shell/1\ngd/my-repo/claude/1\ngd/other/shell/1\n",
        )
        result = list_repo_sessions("my-repo")
        assert result == ["gd/my-repo/claude/1", "gd/my-repo/shell/1"]

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_no_sessions_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert list_repo_sessions("my-repo") == []

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_no_matching_sessions(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="gd/other/shell/1\n")
        assert list_repo_sessions("my-repo") == []

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_skips_temp_panel_wrappers_for_matching_repo(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=("gd/my-repo/shell/1\ngd/temp/panel/my-repo/shell/1\ngd/my-repo/claude/1\n"),
        )

        result = list_repo_sessions("my-repo")

        assert result == ["gd/my-repo/claude/1", "gd/my-repo/shell/1"]


class TestListAllGdSessions:
    @patch("gitdirector.integrations.tmux.subprocess.run")
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

    @patch("gitdirector.integrations.tmux.subprocess.run")
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

    def test_temp_panel_match_requires_wrapper_shape(self):
        assert _is_temp_panel_session("gd/temp/panel/repo/shell/1") is True
        assert _is_temp_panel_session("gd/temp/panel/1") is False


class TestCreateTmuxSession:
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core._list_sessions", return_value=[])
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_creates_and_returns_name(self, mock_run, _mock_list, mock_sync):
        mock_run.return_value = MagicMock(returncode=0)
        path = Path("/tmp/my-repo")
        session_name = f"gd/{_repo_session_name_segment(path)}/shell/1"

        name = create_tmux_session("my-repo", path)

        assert name == session_name
        assert mock_run.call_count == 2
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
                "/tmp/my-repo",
            ],
            capture_output=True,
            text=True,
        )
        mock_run.assert_any_call(
            ["tmux", "set-option", "-t", f"={session_name}:", "destroy-unattached", "off"],
            capture_output=True,
        )
        mock_sync.assert_called_once_with()

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        side_effect=[[], []],
    )
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_raises_when_tmux_new_session_fails_without_collision(
        self, mock_run, _mock_list, mock_sync
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            ["tmux", "new-session"],
            returncode=1,
            stderr="permission denied",
        )

        with pytest.raises(TmuxError):
            create_tmux_session("r", Path("/tmp/r"))

        mock_sync.assert_not_called()

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core._list_sessions")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_retries_on_tmux_name_collision_with_fresh_session_list(
        self, mock_run, mock_list, mock_sync
    ):
        path = Path("/tmp/r")
        repo_slug = _repo_session_name_segment(path)
        first_name = f"gd/{repo_slug}/shell/1"
        second_name = f"gd/{repo_slug}/shell/2"
        mock_list.side_effect = [[], [first_name]]

        def fake_run(args, **_kwargs):
            if args[:3] == ["tmux", "new-session", "-d"]:
                session_name = args[args.index("-s") + 1]
                return MagicMock(returncode=1 if session_name == first_name else 0)
            return MagicMock(returncode=0)

        mock_run.side_effect = fake_run

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
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_creates_with_purpose(self, mock_run, _mock_list, mock_sync):
        mock_run.return_value = MagicMock(returncode=0)
        path = Path("/tmp/my-repo")
        session_name = f"gd/{_repo_session_name_segment(path)}/claude/1"

        name = create_tmux_session("my-repo", path, purpose="claude")

        assert name == session_name
        assert mock_run.call_args_list[0].args[0] == [
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
            "/tmp/my-repo",
        ]
        mock_sync.assert_called_once_with()

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core._set_session_repo_label")
    @patch("gitdirector.integrations.tmux.core._list_sessions", return_value=[])
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_creates_with_repo_label(self, mock_run, _mock_list, mock_set_label, _mock_sync):
        mock_run.return_value = MagicMock(returncode=0)
        path = Path("/tmp/work")
        session_name = f"gd/{_repo_session_name_segment(path)}/shell/1"

        name = create_tmux_session("work", path, repo_label="group_work")

        assert name == session_name
        mock_set_label.assert_called_once_with(session_name, "group_work")

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core._set_session_description")
    @patch("gitdirector.integrations.tmux.core._list_sessions", return_value=[])
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_creates_with_description(self, mock_run, _mock_list, mock_set_desc, _mock_sync):
        mock_run.return_value = MagicMock(returncode=0)
        path = Path("/tmp/my-repo")
        session_name = f"gd/{_repo_session_name_segment(path)}/shell/1"

        name = create_tmux_session("my-repo", path, description="ready to ship")

        assert name == session_name
        mock_set_desc.assert_called_once_with(session_name, "ready to ship")

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core._set_session_description")
    @patch("gitdirector.integrations.tmux.core._list_sessions", return_value=[])
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_creates_without_description_does_not_set_option(
        self, mock_run, _mock_list, mock_set_desc, _mock_sync
    ):
        mock_run.return_value = MagicMock(returncode=0)
        path = Path("/tmp/my-repo")
        session_name = f"gd/{_repo_session_name_segment(path)}/shell/1"

        name = create_tmux_session("my-repo", path)

        assert name == session_name
        mock_set_desc.assert_not_called()


class TestSessionDescriptionOption:
    @patch("gitdirector.integrations.tmux.core.subprocess.run")
    def test_get_session_description_returns_stripped_value(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="  ready to ship  \n")
        from gitdirector.integrations.tmux import _get_session_description

        assert _get_session_description("gd/repo/shell/1") == "ready to ship"
        mock_run.assert_called_once()
        assert "show-option" in mock_run.call_args.args[0]
        assert "@gitdirector_description" in mock_run.call_args.args[0]

    @patch("gitdirector.integrations.tmux.core.subprocess.run")
    def test_get_session_description_falls_back_to_placeholder_on_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        from gitdirector.integrations.tmux import _get_session_description

        assert _get_session_description("gd/repo/shell/1") == "-"

    @patch("gitdirector.integrations.tmux.core.subprocess.run")
    def test_set_session_description_uses_set_option(self, mock_run):
        from gitdirector.integrations.tmux import _set_session_description

        _set_session_description("gd/repo/shell/1", "ready")
        mock_run.assert_called_once()
        args = mock_run.call_args.args[0]
        assert "set-option" in args
        assert "@gitdirector_description" in args
        assert args[-1] == "ready"

    @patch("gitdirector.integrations.tmux.core.subprocess.run")
    def test_set_session_description_unsets_when_empty(self, mock_run):
        from gitdirector.integrations.tmux import _set_session_description

        _set_session_description("gd/repo/shell/1", "")
        mock_run.assert_called_once()
        args = mock_run.call_args.args[0]
        assert "set-option" in args
        assert "-u" in args
        assert "@gitdirector_description" in args

    @patch("gitdirector.integrations.tmux.core.subprocess.run")
    def test_set_session_description_strips_whitespace(self, mock_run):
        from gitdirector.integrations.tmux import _set_session_description

        _set_session_description("gd/repo/shell/1", "  trim me  ")
        mock_run.assert_called_once()
        args = mock_run.call_args.args[0]
        assert args[-1] == "trim me"


class TestEnsureTempPanelTmuxSession:
    @patch("gitdirector.integrations.tmux.panels._load_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.panels._configure_panel_window")
    @patch(
        "gitdirector.integrations.tmux.shutil.get_terminal_size",
        return_value=os.terminal_size((80, 24)),
    )
    @patch("gitdirector.integrations.tmux.panels._session_exists", return_value=False)
    @patch("gitdirector.integrations.tmux.panels.subprocess.run")
    def test_creates_temp_panel_when_missing(
        self,
        mock_run,
        _mock_exists,
        _mock_term_size,
        mock_configure,
        mock_load,
    ):
        mock_run.return_value = MagicMock(stdout="%0\n", returncode=0)
        session_name = ensure_temp_panel_tmux_session("gd/my-repo/shell/1", "rose-pine")

        assert session_name == "gd/temp/panel/my-repo/shell/1"
        assert mock_run.call_args_list[0].args[0] == [
            "tmux",
            "new-session",
            "-d",
            *_TMUX_ENV_ARGS,
            "-s",
            session_name,
            "-n",
            "shell my-repo/1",
            "-x",
            "80",
            "-y",
            "24",
            "-c",
            str(Path.home()),
            "-P",
            "-F",
            "#{pane_id}",
            "cat",
        ]
        mock_configure.assert_called_once_with(
            session_name,
            ["%0"],
            {1: "gd/my-repo/shell/1"},
            "rose-pine",
            show_pane_number=False,
        )
        mock_load.assert_called_once_with(
            "shell my-repo/1",
            session_name,
            "rose-pine",
        )
        assert mock_run.call_args_list[-1].args[0][0:5] == [
            "tmux",
            "respawn-pane",
            "-k",
            "-t",
            "%0",
        ]

    @patch("gitdirector.integrations.tmux.panels._create_temp_panel_tmux_session")
    @patch("gitdirector.integrations.tmux.panels._kill_temp_panel_session_and_wait")
    @patch(
        "gitdirector.integrations.tmux.panels._temp_panel_session_is_inactive", return_value=True
    )
    @patch("gitdirector.integrations.tmux.panels._session_exists", return_value=True)
    def test_recreates_inactive_existing_temp_panel(
        self,
        _mock_exists,
        mock_inactive,
        mock_kill_wait,
        mock_create,
    ):
        mock_kill_wait.return_value = True
        mock_create.return_value = "gd/temp/panel/my-repo/shell/1"

        session_name = ensure_temp_panel_tmux_session(
            "gd/my-repo/shell/1",
            "rose-pine",
            attach_delay_seconds=0.2,
        )

        assert session_name == "gd/temp/panel/my-repo/shell/1"
        mock_inactive.assert_called_once_with("gd/temp/panel/my-repo/shell/1")
        mock_kill_wait.assert_called_once_with("gd/temp/panel/my-repo/shell/1")
        mock_create.assert_called_once_with(
            "gd/my-repo/shell/1",
            "rose-pine",
            attach_delay_seconds=0.2,
        )

    @patch("gitdirector.integrations.tmux.panels._create_temp_panel_tmux_session")
    @patch("gitdirector.integrations.tmux.panels._respawn_temp_panel_pane")
    @patch("gitdirector.integrations.tmux.panels._kill_temp_panel_session_and_wait")
    @patch(
        "gitdirector.integrations.tmux.panels._temp_panel_session_is_inactive", return_value=False
    )
    @patch("gitdirector.integrations.tmux.panels._session_exists", return_value=True)
    def test_returns_active_existing_temp_panel_without_respawn(
        self,
        _mock_exists,
        mock_inactive,
        mock_kill_wait,
        mock_respawn,
        mock_create,
    ):
        session_name = ensure_temp_panel_tmux_session("gd/my-repo/shell/1", "rose-pine")

        assert session_name == "gd/temp/panel/my-repo/shell/1"
        mock_inactive.assert_called_once_with("gd/temp/panel/my-repo/shell/1")
        mock_kill_wait.assert_not_called()
        mock_respawn.assert_not_called()
        mock_create.assert_not_called()

    @patch("gitdirector.integrations.tmux.panels._settle_temp_panel_attach")
    @patch("gitdirector.integrations.tmux.panels._respawn_temp_panel_pane")
    @patch(
        "gitdirector.integrations.tmux.panels._kill_temp_panel_session_and_wait", return_value=False
    )
    @patch(
        "gitdirector.integrations.tmux.panels._temp_panel_session_is_inactive", return_value=True
    )
    @patch("gitdirector.integrations.tmux.panels._session_exists", return_value=True)
    def test_respawns_inactive_temp_panel_when_kill_does_not_remove_it(
        self,
        _mock_exists,
        _mock_inactive,
        _mock_kill_wait,
        mock_respawn,
        mock_settle,
    ):
        session_name = ensure_temp_panel_tmux_session(
            "gd/my-repo/shell/1",
            "rose-pine",
            attach_delay_seconds=0.2,
        )

        assert session_name == "gd/temp/panel/my-repo/shell/1"
        mock_respawn.assert_called_once_with(
            "gd/temp/panel/my-repo/shell/1",
            "gd/my-repo/shell/1",
            attach_delay_seconds=0.2,
        )
        mock_settle.assert_called_once_with()


class TestKillTmuxSession:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert kill_tmux_session("gd/repo/shell/1") is True

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert kill_tmux_session("gd/repo/shell/1") is False


class TestKillPanelTmuxSession:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_kills_panel_session(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        assert kill_panel_tmux_session("Main") is True

        assert mock_run.call_args_list[0].args == (
            ["tmux", "kill-session", "-t", "=gd/panel/main"],
        )
        assert mock_run.call_args_list[0].kwargs == {"capture_output": True}


class TestAttachTmuxSession:
    @patch("gitdirector.integrations.tmux.panels.cleanup_temp_panel_tmux_session")
    @patch(
        "gitdirector.integrations.tmux.panels.ensure_temp_panel_tmux_session",
        return_value="gd/temp/panel/repo/shell/1",
    )
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_inside_tmux_switches_client_to_temp_panel(
        self,
        mock_run,
        mock_sync,
        mock_ensure,
        mock_cleanup,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,12345,0"}):
            attach_tmux_session("gd/repo/shell/1")
        assert mock_run.call_args_list[-1].args[0] == [
            "tmux",
            "switch-client",
            "-t",
            "=gd/temp/panel/repo/shell/1",
        ]
        mock_sync.assert_called_once_with()
        mock_ensure.assert_called_once_with("gd/repo/shell/1", attach_delay_seconds=0.0)
        mock_cleanup.assert_not_called()

    @patch("gitdirector.integrations.tmux.panels.cleanup_temp_panel_tmux_session")
    @patch(
        "gitdirector.integrations.tmux.panels.ensure_temp_panel_tmux_session",
        return_value="gd/temp/panel/repo/shell/1",
    )
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_outside_tmux_attaches_to_temp_panel(
        self,
        mock_run,
        mock_sync,
        mock_ensure,
        mock_cleanup,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("gd/repo/shell/1")
        assert mock_run.call_args_list[-1].args[0] == [
            "tmux",
            "attach-session",
            "-t",
            "=gd/temp/panel/repo/shell/1",
        ]
        mock_sync.assert_called_once_with()
        mock_ensure.assert_called_once_with("gd/repo/shell/1", attach_delay_seconds=0.0)
        mock_cleanup.assert_called_once_with("gd/temp/panel/repo/shell/1")

    @patch("gitdirector.integrations.tmux.panels.ensure_temp_panel_tmux_session")
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_non_gd_session_skips_theme_sync(self, mock_run, mock_sync, mock_ensure):
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("plain-session")
        mock_run.assert_called_once_with(["tmux", "attach-session", "-t", "=plain-session"])
        mock_sync.assert_not_called()
        mock_ensure.assert_not_called()

    @patch("gitdirector.integrations.tmux.panels.cleanup_temp_panel_tmux_session")
    @patch(
        "gitdirector.integrations.tmux.panels.ensure_temp_panel_tmux_session",
        return_value="gd/temp/panel/alpha/shell/1",
    )
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_skip_config_sync_skips_outer_sync(
        self,
        mock_run,
        mock_sync,
        mock_ensure,
        mock_cleanup,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("gd/alpha/shell/1", skip_config_sync=True)
        mock_sync.assert_not_called()
        mock_ensure.assert_called_once_with("gd/alpha/shell/1", attach_delay_seconds=0.0)
        mock_cleanup.assert_called_once_with("gd/temp/panel/alpha/shell/1")
        assert mock_run.call_args_list[-1].args[0] == [
            "tmux",
            "attach-session",
            "-t",
            "=gd/temp/panel/alpha/shell/1",
        ]

    @patch("gitdirector.integrations.tmux.panels.cleanup_temp_panel_tmux_session")
    @patch(
        "gitdirector.integrations.tmux.panels.ensure_temp_panel_tmux_session",
        return_value="gd/temp/panel/alpha/shell/1",
    )
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_passes_inner_attach_delay_to_temp_panel(
        self,
        mock_run,
        mock_sync,
        mock_ensure,
        mock_cleanup,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("gd/alpha/shell/1", attach_delay_seconds=1.0)
        mock_sync.assert_called_once_with()
        mock_ensure.assert_called_once_with("gd/alpha/shell/1", attach_delay_seconds=1.0)
        mock_cleanup.assert_called_once_with("gd/temp/panel/alpha/shell/1")
        assert mock_run.call_args_list[-1].args[0] == [
            "tmux",
            "attach-session",
            "-t",
            "=gd/temp/panel/alpha/shell/1",
        ]

    @patch("gitdirector.integrations.tmux.panels.cleanup_temp_panel_tmux_session")
    @patch(
        "gitdirector.integrations.tmux.panels.ensure_temp_panel_tmux_session",
        return_value="gd/temp/panel/alpha/shell/1",
    )
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_outside_tmux_cleans_temp_panel_when_attach_fails(
        self,
        mock_run,
        _mock_sync,
        _mock_ensure,
        mock_cleanup,
    ):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=1, stderr="failed"),
        ]

        with patch.dict("os.environ", {}, clear=True), pytest.raises(TmuxError):
            attach_tmux_session("gd/alpha/shell/1")

        mock_cleanup.assert_called_once_with("gd/temp/panel/alpha/shell/1")


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
