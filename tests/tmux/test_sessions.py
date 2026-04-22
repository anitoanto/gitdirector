"""Session lifecycle and naming tests for tmux integration."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from gitdirector.integrations.tmux import (
    _is_persistent_panel_session,
    _is_temp_panel_session,
    _make_session_name,
    _parse_gd_session_name,
    _repo_session_name_segment,
    _session_exists,
    attach_tmux_session,
    create_tmux_session,
    kill_panel_tmux_session,
    kill_tmux_session,
    list_all_gd_sessions,
    list_repo_sessions,
    open_in_tmux,
    rebuild_temp_panel_tmux_session,
)

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
    @patch("gitdirector.integrations.tmux.core._list_sessions")
    def test_skips_non_gd_malformed_and_temp_panel_sessions(self, mock_list):
        mock_list.return_value = [
            "gd/alpha_abcd2/shell/1",
            "other-session",
            "gd/bad",
            "gd/beta_efgh2/claude/2",
            "gd/temp/panel/alpha/shell/1",
        ]

        assert list_all_gd_sessions() == [
            {
                "session_name": "gd/alpha_abcd2/shell/1",
                "repo": "alpha",
                "repo_slug": "alpha_abcd2",
                "purpose": "shell",
            },
            {
                "session_name": "gd/beta_efgh2/claude/2",
                "repo": "beta",
                "repo_slug": "beta_efgh2",
                "purpose": "claude",
            },
        ]


class TestSessionNamespaceHelpers:
    def test_parse_gd_session_name_skips_temp_panel_wrapper_sessions(self):
        assert _parse_gd_session_name("gd/temp/panel/repo/shell/1") is None

    def test_parse_gd_session_name_accepts_regular_sessions_named_panel(self):
        assert _parse_gd_session_name("gd/panel/shell/1") == ("panel", "shell", "1")

    def test_persistent_panel_match_requires_exact_panel_shape(self):
        assert _is_persistent_panel_session("gd/panel/main") is True
        assert _is_persistent_panel_session("gd/panel/shell/1") is False

    def test_temp_panel_match_requires_wrapper_shape(self):
        assert _is_temp_panel_session("gd/temp/panel/repo/shell/1") is True
        assert _is_temp_panel_session("gd/temp/panel/1") is False


class TestCreateTmuxSession:
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    @patch("gitdirector.integrations.tmux.core._session_exists", return_value=False)
    @patch(
        "gitdirector.integrations.tmux.core._make_session_name",
        return_value="gd/my-repo/shell/1",
    )
    def test_creates_and_returns_name(self, _mock_name, _mock_exists, mock_run, mock_sync):
        path = Path("/tmp/my-repo")
        name = create_tmux_session("my-repo", path)
        assert name == "gd/my-repo/shell/1"
        assert mock_run.call_count == 2
        mock_run.assert_any_call(
            ["tmux", "new-session", "-d", "-s", "gd/my-repo/shell/1", "-c", "/tmp/my-repo"],
            check=True,
        )
        mock_run.assert_any_call(
            ["tmux", "set-option", "-t", "=gd/my-repo/shell/1:", "destroy-unattached", "off"],
            capture_output=True,
        )
        mock_sync.assert_called_once_with()

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    @patch(
        "gitdirector.integrations.tmux.core._session_exists",
        side_effect=[True, True, False],
    )
    @patch(
        "gitdirector.integrations.tmux.core._make_session_name",
        side_effect=["gd/r/shell/1", "gd/r/shell/2", "gd/r/shell/3"],
    )
    def test_retries_on_collision(self, _mock_name, _mock_exists, mock_run, mock_sync):
        name = create_tmux_session("r", Path("/tmp/r"))
        assert name == "gd/r/shell/3"
        mock_sync.assert_called_once_with()

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    @patch("gitdirector.integrations.tmux.core._session_exists", return_value=False)
    @patch(
        "gitdirector.integrations.tmux.core._make_session_name",
        return_value="gd/my-repo/claude/1",
    )
    def test_creates_with_purpose(self, _mock_name, _mock_exists, mock_run, mock_sync):
        path = Path("/tmp/my-repo")
        name = create_tmux_session("my-repo", path, purpose="claude")
        assert name == "gd/my-repo/claude/1"
        _mock_name.assert_called_with("my-repo", "claude", repo_path=path)
        mock_sync.assert_called_once_with()


class TestRebuildTempPanelTmuxSession:
    @patch("gitdirector.integrations.tmux.panels._load_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.panels._configure_panel_window")
    @patch("gitdirector.integrations.tmux.panels._build_panel_layout", return_value=["%0"])
    @patch(
        "gitdirector.integrations.tmux.shutil.get_terminal_size",
        return_value=os.terminal_size((80, 24)),
    )
    @patch("gitdirector.integrations.tmux.panels._session_exists", return_value=False)
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_builds_single_pane_temp_panel(
        self,
        mock_run,
        _mock_exists,
        _mock_term_size,
        mock_build_layout,
        mock_configure,
        mock_load,
    ):
        session_name = rebuild_temp_panel_tmux_session("gd/my-repo/shell/1", "rose-pine")

        assert session_name == "gd/temp/panel/my-repo/shell/1"
        assert mock_run.call_args_list[0].args[0] == [
            "tmux",
            "new-session",
            "-d",
            "-s",
            "gd/temp/panel/my-repo/shell/1",
            "-n",
            "shell my-repo/1",
            "-x",
            "80",
            "-y",
            "24",
            "-c",
            str(Path.home()),
            "cat",
        ]
        assert mock_run.call_args_list[1].args[0] == [
            "tmux",
            "set-option",
            "-t",
            "=gd/temp/panel/my-repo/shell/1:",
            "destroy-unattached",
            "off",
        ]
        mock_build_layout.assert_called_once_with("gd/temp/panel/my-repo/shell/1", 1, 1, "grid_1x1")
        mock_configure.assert_called_once_with(
            "gd/temp/panel/my-repo/shell/1",
            ["%0"],
            {1: "gd/my-repo/shell/1"},
            "rose-pine",
            show_pane_number=False,
        )
        mock_load.assert_called_once_with(
            "shell my-repo/1",
            "gd/temp/panel/my-repo/shell/1",
            "rose-pine",
        )
        assert mock_run.call_args_list[2].args[0][0:5] == [
            "tmux",
            "respawn-pane",
            "-k",
            "-t",
            "%0",
        ]

    @patch("gitdirector.integrations.tmux.panels._load_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.panels._configure_panel_window")
    @patch("gitdirector.integrations.tmux.panels._build_panel_layout", return_value=["%0"])
    @patch(
        "gitdirector.integrations.tmux.shutil.get_terminal_size",
        return_value=os.terminal_size((80, 24)),
    )
    @patch("gitdirector.integrations.tmux.panels._session_exists", return_value=True)
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_replaces_existing_temp_panel_with_same_session_name(
        self,
        mock_run,
        _mock_exists,
        _mock_term_size,
        _mock_build_layout,
        _mock_configure,
        _mock_load,
    ):
        rebuild_temp_panel_tmux_session("gd/my-repo/shell/1", "rose-pine")

        assert mock_run.call_args_list[0].args[0] == [
            "tmux",
            "kill-session",
            "-t",
            "=gd/temp/panel/my-repo/shell/1",
        ]
        assert mock_run.call_args_list[0].kwargs == {"check": False}


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
    @patch(
        "gitdirector.integrations.tmux.panels.rebuild_temp_panel_tmux_session",
        return_value="gd/temp/panel/repo/shell/1",
    )
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_inside_tmux_switches_client_to_temp_panel(self, mock_run, mock_sync, mock_rebuild):
        with patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,12345,0"}):
            attach_tmux_session("gd/repo/shell/1")
        mock_run.assert_called_once_with(
            ["tmux", "switch-client", "-t", "=gd/temp/panel/repo/shell/1"]
        )
        mock_sync.assert_called_once_with()
        mock_rebuild.assert_called_once_with("gd/repo/shell/1")

    @patch(
        "gitdirector.integrations.tmux.panels.rebuild_temp_panel_tmux_session",
        return_value="gd/temp/panel/repo/shell/1",
    )
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_outside_tmux_attaches_to_temp_panel(self, mock_run, mock_sync, mock_rebuild):
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("gd/repo/shell/1")
        mock_run.assert_called_once_with(
            ["tmux", "attach-session", "-t", "=gd/temp/panel/repo/shell/1"]
        )
        mock_sync.assert_called_once_with()
        mock_rebuild.assert_called_once_with("gd/repo/shell/1")

    @patch("gitdirector.integrations.tmux.panels.rebuild_temp_panel_tmux_session")
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_non_gd_session_skips_theme_sync(self, mock_run, mock_sync, mock_rebuild):
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("plain-session")
        mock_run.assert_called_once_with(["tmux", "attach-session", "-t", "=plain-session"])
        mock_sync.assert_not_called()
        mock_rebuild.assert_not_called()


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
        mock_attach.assert_called_once_with("gd/my-repo/shell/1")
