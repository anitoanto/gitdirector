"""Tests for gitdirector.integrations.tmux."""

import fcntl
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gitdirector.integrations.tmux import (
    _AGENT_PURPOSES,
    _BELL_GRACE_SECS,
    _SHELL_COMMANDS,
    _SILENCE_THRESHOLD_SECS,
    TmuxMonitor,
    _build_layout_spec,
    _build_panel_layout,
    _capture_pane_text,
    _configure_panel_window,
    _ControlModeReader,
    _current_window_target,
    _distribute_equal,
    _embedded_tmux_attach_command,
    _ensure_panel_prefix_bindings,
    _ensure_panel_resize_tracking,
    _get_process_snapshot,
    _hash_content,
    _is_persistent_panel_session,
    _is_temp_panel_session,
    _layout_checksum,
    _live_panel_sessions,
    _live_repo_tmux_sessions,
    _load_panel_tmux_config,
    _make_agent_ready_marker,
    _make_session_name,
    _normalize_process_command,
    _panel_attach_fragment,
    _panel_border_format,
    _panel_pane_command,
    _panel_pane_title,
    _panel_tmux_config,
    _panel_window_status_format,
    _parse_gd_session_name,
    _repo_session_name_segment,
    _resolve_pane_command,
    _resolved_panel_theme_name,
    _sanitize_repo_name,
    _session_exists,
    _session_tmux_config,
    _span_size,
    _tmux_theme_config,
    attach_tmux_session,
    cleanup_panel_attached_session,
    create_tmux_session,
    get_all_session_statuses,
    kill_panel_tmux_session,
    kill_tmux_session,
    launch_agent_in_tmux_session,
    list_all_gd_sessions,
    list_repo_sessions,
    open_in_tmux,
    rebuild_panel_tmux_session,
    rebuild_temp_panel_tmux_session,
    resolve_pane_status,
    sync_panel_tmux_config,
)
from gitdirector.ui_theme import resolve_panel_theme

REAL_TMUX_MONITOR_START = TmuxMonitor.start
REAL_TMUX_MONITOR_STOP = TmuxMonitor.stop


@contextmanager
def _tmux_integration_lock():
    lock_path = Path(tempfile.gettempdir()) / "gitdirector-tmux-integration.lock"
    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestSanitizeRepoName:
    def test_lowercases(self):
        assert _sanitize_repo_name("MyRepo") == "myrepo"

    def test_keeps_hyphens(self):
        assert _sanitize_repo_name("my-repo") == "my-repo"

    def test_replaces_dots_and_slashes(self):
        assert _sanitize_repo_name("foo.bar/baz") == "foo-bar-baz"

    def test_strips_special_chars(self):
        assert _sanitize_repo_name("a b@c!d") == "a-b-c-d"

    def test_collapses_hyphens(self):
        assert _sanitize_repo_name("a--b---c") == "a-b-c"

    def test_strips_leading_trailing_hyphens(self):
        assert _sanitize_repo_name("-repo-") == "repo"

    def test_leaves_alphanumeric_untouched(self):
        assert _sanitize_repo_name("abc123") == "abc123"

    def test_empty_string(self):
        assert _sanitize_repo_name("") == ""


class TestBuildPanelLayout:
    @patch(
        "gitdirector.integrations.tmux._list_window_panes_row_major",
        return_value=["%0", "%1", "%2"],
    )
    @patch("gitdirector.integrations.tmux._tmux_output")
    def test_builds_tall_left_layout(self, mock_tmux_output, mock_list_panes):
        mock_tmux_output.side_effect = ["%1", "%2"]

        pane_ids = _build_panel_layout("gd/panel/focus", 2, 2, "tall_left")

        assert pane_ids == ["%0", "%1", "%2"]
        assert mock_tmux_output.call_args_list[0].args == (
            "split-window",
            "-h",
            "-l",
            "50%",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            "=gd/panel/focus:0.0",
            "cat",
        )
        assert mock_tmux_output.call_args_list[1].args == (
            "split-window",
            "-v",
            "-l",
            "50%",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            "%1",
            "cat",
        )
        mock_list_panes.assert_called_once_with("gd/panel/focus")

    @patch(
        "gitdirector.integrations.tmux._list_window_panes_row_major",
        return_value=["%0", "%1", "%2", "%3"],
    )
    @patch("gitdirector.integrations.tmux._tmux_output")
    def test_builds_two_by_two_grid_layout(self, mock_tmux_output, mock_list_panes):
        mock_tmux_output.side_effect = ["%1", "%2", "%3"]

        pane_ids = _build_panel_layout("gd/panel/grid", 2, 2, "grid_2x2")

        assert pane_ids == ["%0", "%1", "%2", "%3"]
        assert [call.args for call in mock_tmux_output.call_args_list] == [
            (
                "split-window",
                "-v",
                "-l",
                "50%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "=gd/panel/grid:0.0",
                "cat",
            ),
            (
                "split-window",
                "-h",
                "-l",
                "50%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "=gd/panel/grid:0.0",
                "cat",
            ),
            (
                "split-window",
                "-h",
                "-l",
                "50%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "%1",
                "cat",
            ),
        ]
        mock_list_panes.assert_called_once_with("gd/panel/grid")

    @patch(
        "gitdirector.integrations.tmux._list_window_panes_row_major",
        return_value=["%0", "%1", "%2"],
    )
    @patch("gitdirector.integrations.tmux._tmux_output")
    def test_builds_wide_bottom_layout(self, mock_tmux_output, mock_list_panes):
        mock_tmux_output.side_effect = ["%1", "%2"]

        pane_ids = _build_panel_layout("gd/panel/focus", 2, 2, "wide_bottom")

        assert pane_ids == ["%0", "%1", "%2"]
        assert mock_tmux_output.call_args_list[0].args == (
            "split-window",
            "-v",
            "-l",
            "50%",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            "=gd/panel/focus:0.0",
            "cat",
        )
        assert mock_tmux_output.call_args_list[1].args == (
            "split-window",
            "-h",
            "-l",
            "50%",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            "=gd/panel/focus:0.0",
            "cat",
        )
        mock_list_panes.assert_called_once_with("gd/panel/focus")

    @patch(
        "gitdirector.integrations.tmux._list_window_panes_row_major",
        return_value=["%0", "%1", "%2", "%3", "%4"],
    )
    @patch("gitdirector.integrations.tmux._tmux_output")
    def test_builds_two_by_three_top_left_duo_layout(self, mock_tmux_output, mock_list_panes):
        mock_tmux_output.side_effect = ["%1", "%2", "%3", "%4"]

        pane_ids = _build_panel_layout("gd/panel/wall", 2, 3, "duo_top_left_2x3")

        assert pane_ids == ["%0", "%1", "%2", "%3", "%4"]
        assert [call.args for call in mock_tmux_output.call_args_list] == [
            (
                "split-window",
                "-v",
                "-l",
                "50%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "=gd/panel/wall:0.0",
                "cat",
            ),
            (
                "split-window",
                "-h",
                "-l",
                "33%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "=gd/panel/wall:0.0",
                "cat",
            ),
            (
                "split-window",
                "-h",
                "-l",
                "67%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "%1",
                "cat",
            ),
            (
                "split-window",
                "-h",
                "-l",
                "50%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "%3",
                "cat",
            ),
        ]
        mock_list_panes.assert_called_once_with("gd/panel/wall")

    @patch(
        "gitdirector.integrations.tmux._list_window_panes_row_major",
        return_value=["%0", "%1", "%2", "%3", "%4", "%5", "%6", "%7"],
    )
    @patch("gitdirector.integrations.tmux._tmux_output")
    def test_builds_three_by_three_top_left_duo_layout(self, mock_tmux_output, mock_list_panes):
        mock_tmux_output.side_effect = ["%1", "%2", "%3", "%4", "%5", "%6", "%7"]

        pane_ids = _build_panel_layout("gd/panel/grid", 3, 3, "duo_top_left_3x3")

        assert pane_ids == ["%0", "%1", "%2", "%3", "%4", "%5", "%6", "%7"]
        assert len(mock_tmux_output.call_args_list) == 7
        assert mock_tmux_output.call_args_list[0].args == (
            "split-window",
            "-v",
            "-l",
            "67%",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            "=gd/panel/grid:0.0",
            "cat",
        )
        assert mock_tmux_output.call_args_list[1].args == (
            "split-window",
            "-h",
            "-l",
            "33%",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            "=gd/panel/grid:0.0",
            "cat",
        )
        assert mock_tmux_output.call_args_list[2].args == (
            "split-window",
            "-v",
            "-l",
            "50%",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            "%1",
            "cat",
        )
        mock_list_panes.assert_called_once_with("gd/panel/grid")

    @patch(
        "gitdirector.integrations.tmux._list_window_panes_row_major",
        return_value=["%0", "%1", "%2", "%3", "%4", "%5", "%6", "%7", "%8"],
    )
    @patch("gitdirector.integrations.tmux._tmux_output")
    def test_builds_three_by_three_grid_layout(self, mock_tmux_output, mock_list_panes):
        mock_tmux_output.side_effect = ["%1", "%2", "%3", "%4", "%5", "%6", "%7", "%8"]

        pane_ids = _build_panel_layout("gd/panel/grid", 3, 3, "grid_3x3")

        assert pane_ids == ["%0", "%1", "%2", "%3", "%4", "%5", "%6", "%7", "%8"]
        assert [call.args for call in mock_tmux_output.call_args_list] == [
            (
                "split-window",
                "-v",
                "-l",
                "67%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "=gd/panel/grid:0.0",
                "cat",
            ),
            (
                "split-window",
                "-h",
                "-l",
                "67%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "=gd/panel/grid:0.0",
                "cat",
            ),
            (
                "split-window",
                "-h",
                "-l",
                "50%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "%2",
                "cat",
            ),
            (
                "split-window",
                "-v",
                "-l",
                "50%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "%1",
                "cat",
            ),
            (
                "split-window",
                "-h",
                "-l",
                "67%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "%1",
                "cat",
            ),
            (
                "split-window",
                "-h",
                "-l",
                "50%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "%5",
                "cat",
            ),
            (
                "split-window",
                "-h",
                "-l",
                "67%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "%4",
                "cat",
            ),
            (
                "split-window",
                "-h",
                "-l",
                "50%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "%7",
                "cat",
            ),
        ]
        mock_list_panes.assert_called_once_with("gd/panel/grid")

    @patch(
        "gitdirector.integrations.tmux._list_window_panes_row_major",
        return_value=["%0", "%1", "%2", "%3", "%4", "%5"],
    )
    @patch("gitdirector.integrations.tmux._tmux_output")
    def test_builds_three_by_three_top_left_quad_layout(self, mock_tmux_output, mock_list_panes):
        mock_tmux_output.side_effect = ["%1", "%2", "%3", "%4", "%5"]

        pane_ids = _build_panel_layout("gd/panel/studio", 3, 3, "quad_top_left_3x3")

        assert pane_ids == ["%0", "%1", "%2", "%3", "%4", "%5"]
        assert [call.args for call in mock_tmux_output.call_args_list] == [
            (
                "split-window",
                "-v",
                "-l",
                "33%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "=gd/panel/studio:0.0",
                "cat",
            ),
            (
                "split-window",
                "-h",
                "-l",
                "33%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "=gd/panel/studio:0.0",
                "cat",
            ),
            (
                "split-window",
                "-v",
                "-l",
                "50%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "%2",
                "cat",
            ),
            (
                "split-window",
                "-h",
                "-l",
                "67%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "%1",
                "cat",
            ),
            (
                "split-window",
                "-h",
                "-l",
                "50%",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                "%4",
                "cat",
            ),
        ]
        mock_list_panes.assert_called_once_with("gd/panel/studio")


class TestMakeSessionName:
    @patch(
        "gitdirector.integrations.tmux._list_sessions",
        return_value=[],
    )
    def test_first_session(self, _mock_list):
        repo_path = Path("/tmp/my-repo")
        repo_slug = _repo_session_name_segment(repo_path)

        name = _make_session_name(repo_path)

        assert name == f"gd/{repo_slug}/shell/1"

    @patch(
        "gitdirector.integrations.tmux._list_sessions",
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
        "gitdirector.integrations.tmux._list_sessions",
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
        "gitdirector.integrations.tmux._list_sessions",
        return_value=[f"gd/{_repo_session_name_segment(Path('/tmp/my-repo'))}/claude/1"],
    )
    def test_purpose_shell_independent_of_agent(self, _mock_list):
        repo_path = Path("/tmp/my-repo")
        repo_slug = _repo_session_name_segment(repo_path)

        name = _make_session_name(repo_path, "shell")

        assert name == f"gd/{repo_slug}/shell/1"

    @patch(
        "gitdirector.integrations.tmux._list_sessions",
        return_value=[f"gd/{_repo_session_name_segment(Path('/tmp/my-repo'))}/claude/1"],
    )
    def test_purpose_agent(self, _mock_list):
        repo_path = Path("/tmp/my-repo")
        repo_slug = _repo_session_name_segment(repo_path)

        name = _make_session_name(repo_path, "claude")

        assert name == f"gd/{repo_slug}/claude/2"

    @patch(
        "gitdirector.integrations.tmux._list_sessions",
        return_value=[],
    )
    def test_special_chars_sanitized(self, _mock_list):
        repo_path = Path("/tmp/foo.bar@baz")

        name = _make_session_name(repo_path)

        assert name.startswith("gd/foo-bar-baz_")
        assert name.endswith("/shell/1")


class TestRebuildPanelTmuxSession:
    @patch("gitdirector.integrations.tmux.shutil.get_terminal_size", return_value=(80, 24))
    @patch("gitdirector.integrations.tmux._ensure_panel_prefix_bindings")
    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux._load_panel_tmux_config")
    @patch("gitdirector.integrations.tmux._configure_panel_window")
    @patch("gitdirector.integrations.tmux._equalize_panel_layout")
    @patch(
        "gitdirector.integrations.tmux._build_panel_layout", return_value=["%0", "%1", "%2", "%3"]
    )
    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_enables_pane_headers_before_building_layout(
        self,
        mock_run,
        mock_kill,
        mock_build_layout,
        mock_equalize,
        mock_configure,
        mock_load,
        mock_sync,
        mock_bindings,
        _mock_term_size,
    ):
        def assert_border_enabled_first(*args, **kwargs):
            assert [call.args for call in mock_run.call_args_list] == [
                (
                    [
                        "tmux",
                        "new-session",
                        "-d",
                        "-s",
                        "gd/panel/main",
                        "-n",
                        "Main",
                        "-x",
                        "80",
                        "-y",
                        "24",
                        "-c",
                        str(Path.home()),
                        "cat",
                    ],
                ),
                (
                    [
                        "tmux",
                        "set-option",
                        "-t",
                        "=gd/panel/main:",
                        "destroy-unattached",
                        "off",
                    ],
                ),
                (
                    [
                        "tmux",
                        "set-window-option",
                        "-t",
                        "=gd/panel/main:0",
                        "pane-border-status",
                        "top",
                    ],
                ),
            ]
            return ["%0", "%1", "%2", "%3"]

        mock_build_layout.side_effect = assert_border_enabled_first

        session_name = rebuild_panel_tmux_session(
            "Main",
            2,
            2,
            {1: None, 2: None, 3: None, 4: None},
            layout_key="grid_2x2",
            theme_name="rose-pine",
        )

        assert session_name == "gd/panel/main"
        mock_kill.assert_called_once_with("Main")
        mock_equalize.assert_called_once()
        mock_configure.assert_called_once()
        mock_load.assert_called_once()
        mock_sync.assert_called_once_with("rose-pine")
        mock_bindings.assert_called_once_with()


class TestPanelPrefixBindings:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_panel_prefix_bindings_include_overlay_alias_and_numeric_focus(self, mock_run):
        _ensure_panel_prefix_bindings()

        commands = [call.args[0] for call in mock_run.call_args_list]

        assert commands[0] == [
            "tmux",
            "bind-key",
            "-T",
            "prefix",
            "b",
            "if-shell",
            "-F",
            "#{m:gd/panel/*,#{session_name}}",
            "display-panes",
        ]
        assert [
            "tmux",
            "bind-key",
            "-T",
            "prefix",
            "1",
            "if-shell",
            "-F",
            "#{m:gd/panel/*,#{session_name}}",
            "select-pane -t:.1",
            "select-window -t :=1",
        ] in commands
        assert [
            "tmux",
            "bind-key",
            "-T",
            "prefix",
            "9",
            "if-shell",
            "-F",
            "#{m:gd/panel/*,#{session_name}}",
            "select-pane -t:.9",
            "select-window -t :=9",
        ] in commands


class TestDistributeEqual:
    def test_divides_evenly(self):
        assert _distribute_equal(30, 3) == [10, 10, 10]

    def test_remainder_goes_to_first_parts(self):
        assert _distribute_equal(10, 3) == [4, 3, 3]

    def test_single_part(self):
        assert _distribute_equal(42, 1) == [42]


class TestSpanSize:
    def test_single_cell(self):
        assert _span_size([10, 10, 10], 1, 1) == 10

    def test_multi_span_adds_separators(self):
        assert _span_size([10, 10, 10], 0, 2) == 21

    def test_full_span(self):
        assert _span_size([10, 10, 10], 0, 3) == 32


class TestLayoutChecksum:
    def test_known_layout_checksum(self):
        spec = "80x24,0,0{40x24,0,0,0,39x24,41,0,1}"
        csum = _layout_checksum(spec)
        assert isinstance(csum, int)
        assert 0 <= csum <= 0xFFFF

    def test_deterministic(self):
        spec = "200x60,0,0[200x40,0,0,0,200x19,0,41,1]"
        assert _layout_checksum(spec) == _layout_checksum(spec)


class TestBuildLayoutSpec:
    def test_single_pane_leaf(self):
        spec = _build_layout_spec(
            ((0, 0, 1, 1),),
            {(0, 0): 0},
            [24],
            [80],
            0,
            0,
        )
        assert spec == "80x24,0,0,0"

    def test_two_column_split(self):
        spec = _build_layout_spec(
            ((0, 0, 1, 1), (0, 1, 1, 1)),
            {(0, 0): 0, (0, 1): 1},
            [24],
            [40, 39],
            0,
            0,
        )
        assert spec == "80x24,0,0{40x24,0,0,0,39x24,41,0,1}"

    def test_two_row_split(self):
        spec = _build_layout_spec(
            ((0, 0, 1, 1), (1, 0, 1, 1)),
            {(0, 0): 0, (1, 0): 1},
            [12, 11],
            [80],
            0,
            0,
        )
        assert spec == "80x24,0,0[80x12,0,0,0,80x11,0,13,1]"

    def test_quad_top_left_3x3_equal_proportions(self):
        placements = (
            (0, 0, 2, 2),
            (0, 2, 1, 1),
            (1, 2, 1, 1),
            (2, 0, 1, 1),
            (2, 1, 1, 1),
            (2, 2, 1, 1),
        )
        pane_id_map = {(0, 0): 0, (0, 2): 1, (1, 2): 2, (2, 0): 3, (2, 1): 4, (2, 2): 5}
        row_heights = _distribute_equal(60 - 2, 3)
        col_widths = _distribute_equal(200 - 2, 3)

        spec = _build_layout_spec(placements, pane_id_map, row_heights, col_widths, 0, 0)

        assert "0" in spec
        for pane_num in range(6):
            assert f",{pane_num}" in spec or spec.endswith(f",{pane_num}")


class TestPanelPaneTitles:
    def test_panel_pane_title_uses_session_slug(self):
        assert _panel_pane_title(1, "gd/my-repo/copilot/3") == "copilot my-repo/3"

    def test_panel_pane_title_marks_empty_slots(self):
        assert _panel_pane_title(2, None) == "empty"

    def test_panel_border_format_styles_badge_separately(self):
        theme = resolve_panel_theme("rose-pine")
        border_format = _panel_border_format("rose-pine")

        assert "#{pane_index}" in border_format
        assert " #{pane_title} " in border_format
        assert f"bg={theme.badge_active_bg}" in border_format
        assert f"bg={theme.label_active_bg}" in border_format

    def test_panel_border_format_can_hide_pane_number(self):
        border_format = _panel_border_format("rose-pine", show_pane_number=False)

        assert "#{pane_index}" not in border_format
        assert "#{pane_title}" in border_format

    def test_panel_window_status_format_uses_active_pane(self):
        assert _panel_window_status_format() == " #{pane_index}:#{pane_title} "

    @patch("gitdirector.integrations.tmux.Config")
    def test_panel_border_format_defaults_to_config_theme(self, mock_config):
        mock_config.return_value.theme = "nord"
        theme = resolve_panel_theme("nord")

        border_format = _panel_border_format()

        assert f"bg={theme.badge_active_bg}" in border_format
        assert f"bg={theme.label_active_bg}" in border_format

    @patch("gitdirector.integrations.tmux.Config")
    def test_resolved_panel_theme_name_uses_config(self, mock_config):
        mock_config.return_value.theme = "gruvbox"

        assert _resolved_panel_theme_name() == "gruvbox"

    def test_panel_tmux_config_themes_bottom_status_line(self):
        theme = resolve_panel_theme("rose-pine")
        config = _panel_tmux_config("Main", "gd/panel/main", "rose-pine")

        assert "set-option -t =gd/panel/main: status-position bottom" in config
        assert "set-option -t =gd/panel/main: status-left" in config
        assert "set-option -t =gd/panel/main: status-right" in config
        assert "window-status-current-format ' #{pane_index}:#{pane_title} '" in config
        assert f'message-style "fg={theme.badge_active_fg},bg={theme.badge_active_bg}"' in config
        assert (
            f'window-status-current-style "fg={theme.badge_active_fg},bg={theme.badge_active_bg},bold"'
            in config
        )

    def test_panel_pane_command_hides_session_status_while_attached(self):
        command = _panel_pane_command("Main", 1, "gd/my-repo/copilot/3")

        assert "tmux new-session -d -t =gd/my-repo/copilot/3 -s" not in command
        assert "tmux set-option -q -t =gd/my-repo/copilot/3: status off" in command
        assert "tmux attach-session -t =gd/my-repo/copilot/3" in command
        assert "SESSION CLOSED" in command
        assert "Once all panes are closed, this panel will autodelete" not in command
        assert "Reopen the panel from GitDirector to attach again." not in command

    def test_panel_pane_command_shows_closed_message_for_closed_empty_pane(self):
        command = _panel_pane_command("Main", 1, None, closed=True)

        assert "SESSION CLOSED" in command
        assert "Once all panes are closed, this panel will autodelete" not in command
        assert "Pane 1: unassigned" not in command

    def test_embedded_tmux_attach_command_reapplies_session_chrome_when_not_in_panel(self):
        command = _embedded_tmux_attach_command("gd/my-repo/copilot/3")

        assert command.startswith("sh -c ")
        assert "tmux set-option -t =gd/my-repo/copilot/3: status-position bottom" in command
        assert "tmux set-option -t =gd/my-repo/copilot/3: status-left" in command
        assert "tmux set-option -q -t =gd/my-repo/copilot/3: status off" not in command
        assert 'tmux set-window-option -q -t "$panel_window" pane-border-status off' not in command
        assert "tmux attach-session -t =gd/my-repo/copilot/3" in command

    @patch(
        "gitdirector.integrations.tmux._current_window_target", return_value="gd/my-repo/shell/1:2"
    )
    def test_session_tmux_config_themes_regular_sessions(self, _mock_target):
        theme = resolve_panel_theme("rose-pine")
        config = _session_tmux_config("gd/my-repo/shell/1", "rose-pine")

        assert "set-option -t =gd/my-repo/shell/1: status-left" in config
        assert "SHELL" in config
        assert "my-repo/shell/1" in config
        assert "window-status-current-format ' #I:#W '" in config
        assert "set-window-option -t =gd/my-repo/shell/1:2 pane-border-style" in config
        assert f'pane-active-border-style "fg={theme.border_active}"' in config
        assert "pane-border-lines" not in config
        assert "pane-border-status top" not in config
        assert "pane-border-format" not in config

    @patch(
        "gitdirector.integrations.tmux._current_window_target",
        return_value="gd/my-repo/copilot/1:0",
    )
    def test_session_tmux_config_themes_agent_sessions(self, _mock_target):
        config = _session_tmux_config("gd/my-repo/copilot/1", "rose-pine")

        assert "COPILOT" in config
        assert "my-repo/copilot/1" in config
        assert "pane-border-status top" not in config

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_load_panel_tmux_config_writes_and_sources_file(self, mock_run, tmp_path):
        config_path = tmp_path / "gd-tmux.conf"

        with patch("gitdirector.integrations.tmux._gd_tmux_config_path", return_value=config_path):
            written_path = _load_panel_tmux_config("Main", "gd/panel/main", "nord")

        assert written_path == config_path
        assert config_path.exists()
        content = config_path.read_text()
        assert "set-option -t =gd/panel/main: status-position bottom" in content
        assert "set-window-option -t =gd/panel/main:0 pane-border-lines heavy" in content
        mock_run.assert_called_once_with(["tmux", "source-file", str(config_path)], check=True)

    @patch("gitdirector.integrations.tmux._session_exists", side_effect=[True, False])
    @patch("gitdirector.commands.tui.panels.PanelStore")
    def test_live_panel_sessions_filters_running_sessions(self, mock_store, _mock_exists):
        mock_store.return_value.panels = [
            SimpleNamespace(name="Main"),
            SimpleNamespace(name="Other"),
        ]

        assert _live_panel_sessions() == [("Main", "gd/panel/main")]

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_sync_panel_tmux_config_writes_all_live_sessions(self, mock_run, tmp_path):
        config_path = tmp_path / "gd-tmux.conf"
        with patch("gitdirector.integrations.tmux._gd_tmux_config_path", return_value=config_path):
            with patch(
                "gitdirector.integrations.tmux._live_panel_sessions",
                return_value=[("Main", "gd/panel/main"), ("Me2", "gd/panel/me2")],
            ):
                with patch(
                    "gitdirector.integrations.tmux._live_repo_tmux_sessions", return_value=[]
                ):
                    written_path = sync_panel_tmux_config("nord")

        assert written_path == config_path
        content = config_path.read_text()
        assert "# theme: nord" in content
        assert "set-option -t =gd/panel/main: status-position bottom" in content
        assert "set-option -t =gd/panel/me2: status-position bottom" in content
        mock_run.assert_called_once_with(["tmux", "source-file", str(config_path)], check=True)

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_sync_panel_tmux_config_writes_regular_sessions(self, mock_run, tmp_path):
        config_path = tmp_path / "gd-tmux.conf"
        with patch("gitdirector.integrations.tmux._gd_tmux_config_path", return_value=config_path):
            with patch("gitdirector.integrations.tmux._live_panel_sessions", return_value=[]):
                with patch(
                    "gitdirector.integrations.tmux._live_repo_tmux_sessions",
                    return_value=["gd/my-repo/shell/1"],
                ):
                    with patch(
                        "gitdirector.integrations.tmux._current_window_target",
                        return_value="gd/my-repo/shell/1:2",
                    ):
                        written_path = sync_panel_tmux_config("nord")

        assert written_path == config_path
        content = config_path.read_text()
        assert "# theme: nord" in content
        assert "set-option -t =gd/my-repo/shell/1: status-left" in content
        assert "SHELL" in content
        assert "set-window-option -t =gd/my-repo/shell/1:2 pane-border-style" in content
        mock_run.assert_called_once_with(["tmux", "source-file", str(config_path)], check=True)

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_sync_panel_tmux_config_skips_source_when_no_live_sessions(self, mock_run, tmp_path):
        config_path = tmp_path / "gd-tmux.conf"
        with patch("gitdirector.integrations.tmux._gd_tmux_config_path", return_value=config_path):
            with patch("gitdirector.integrations.tmux._live_panel_sessions", return_value=[]):
                with patch(
                    "gitdirector.integrations.tmux._live_repo_tmux_sessions", return_value=[]
                ):
                    written_path = sync_panel_tmux_config("rose-pine")

        assert written_path == config_path
        content = config_path.read_text()
        assert "# theme: rose-pine" in content
        mock_run.assert_not_called()

    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions", side_effect=Exception("tmux error")
    )
    def test_live_repo_tmux_sessions_handles_listing_error(self, _mock_list):
        assert _live_repo_tmux_sessions() == []

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_sync_panel_tmux_config_ignores_source_file_failure(self, mock_run, tmp_path):
        config_path = tmp_path / "gd-tmux.conf"
        mock_run.side_effect = __import__("subprocess").CalledProcessError(
            1, ["tmux", "source-file", str(config_path)]
        )

        with patch("gitdirector.integrations.tmux._gd_tmux_config_path", return_value=config_path):
            with patch("gitdirector.integrations.tmux._live_panel_sessions", return_value=[]):
                with patch(
                    "gitdirector.integrations.tmux._live_repo_tmux_sessions",
                    return_value=["gd/my-repo/shell/1"],
                ):
                    with patch(
                        "gitdirector.integrations.tmux._current_window_target",
                        return_value="gd/my-repo/shell/1:0",
                    ):
                        written_path = sync_panel_tmux_config("rose-pine")

        assert written_path == config_path
        assert config_path.exists()
        mock_run.assert_called_once_with(["tmux", "source-file", str(config_path)], check=True)

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_configure_panel_window_sets_titles_with_slugs(self, mock_run):
        theme = resolve_panel_theme("nord")
        _configure_panel_window(
            "gd/panel/main",
            ["%1", "%2"],
            {1: "gd/my-repo/copilot/3", 2: None},
            "nord",
        )

        commands = [call.args[0] for call in mock_run.call_args_list]

        assert ["tmux", "select-pane", "-t", "%1", "-T", "copilot my-repo/3"] in commands
        assert ["tmux", "select-pane", "-t", "%2", "-T", "empty"] in commands
        assert [
            "tmux",
            "set-window-option",
            "-t",
            "=gd/panel/main:0",
            "pane-border-lines",
            "heavy",
        ] in commands
        assert [
            "tmux",
            "set-window-option",
            "-t",
            "=gd/panel/main:0",
            "pane-border-style",
            f"fg={theme.border_inactive}",
        ] in commands
        assert [
            "tmux",
            "set-window-option",
            "-t",
            "=gd/panel/main:0",
            "pane-active-border-style",
            f"fg={theme.border_active}",
        ] in commands
        assert [
            "tmux",
            "set-window-option",
            "-t",
            "=gd/panel/main:0",
            "pane-border-format",
            _panel_border_format("nord"),
        ] in commands


# ---------------------------------------------------------------------------
# Subprocess-based functions
# ---------------------------------------------------------------------------


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
    @patch("gitdirector.integrations.tmux._list_sessions")
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
    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    @patch("gitdirector.integrations.tmux._session_exists", return_value=False)
    @patch(
        "gitdirector.integrations.tmux._make_session_name",
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

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    @patch(
        "gitdirector.integrations.tmux._session_exists",
        side_effect=[True, True, False],
    )
    @patch(
        "gitdirector.integrations.tmux._make_session_name",
        side_effect=["gd/r/shell/1", "gd/r/shell/2", "gd/r/shell/3"],
    )
    def test_retries_on_collision(self, _mock_name, _mock_exists, mock_run, mock_sync):
        name = create_tmux_session("r", Path("/tmp/r"))
        assert name == "gd/r/shell/3"
        mock_sync.assert_called_once_with()

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    @patch("gitdirector.integrations.tmux._session_exists", return_value=False)
    @patch(
        "gitdirector.integrations.tmux._make_session_name",
        return_value="gd/my-repo/claude/1",
    )
    def test_creates_with_purpose(self, _mock_name, _mock_exists, mock_run, mock_sync):
        path = Path("/tmp/my-repo")
        name = create_tmux_session("my-repo", path, purpose="claude")
        assert name == "gd/my-repo/claude/1"
        _mock_name.assert_called_with("my-repo", "claude", repo_path=path)
        mock_sync.assert_called_once_with()


class TestRebuildTempPanelTmuxSession:
    @patch("gitdirector.integrations.tmux._load_panel_tmux_config")
    @patch("gitdirector.integrations.tmux._configure_panel_window")
    @patch("gitdirector.integrations.tmux._build_panel_layout", return_value=["%0"])
    @patch(
        "gitdirector.integrations.tmux.shutil.get_terminal_size",
        return_value=os.terminal_size((80, 24)),
    )
    @patch("gitdirector.integrations.tmux._session_exists", return_value=False)
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

    @patch("gitdirector.integrations.tmux._load_panel_tmux_config")
    @patch("gitdirector.integrations.tmux._configure_panel_window")
    @patch("gitdirector.integrations.tmux._build_panel_layout", return_value=["%0"])
    @patch(
        "gitdirector.integrations.tmux.shutil.get_terminal_size",
        return_value=os.terminal_size((80, 24)),
    )
    @patch("gitdirector.integrations.tmux._session_exists", return_value=True)
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
        "gitdirector.integrations.tmux.rebuild_temp_panel_tmux_session",
        return_value="gd/temp/panel/repo/shell/1",
    )
    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
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
        "gitdirector.integrations.tmux.rebuild_temp_panel_tmux_session",
        return_value="gd/temp/panel/repo/shell/1",
    )
    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_outside_tmux_attaches_to_temp_panel(self, mock_run, mock_sync, mock_rebuild):
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("gd/repo/shell/1")
        mock_run.assert_called_once_with(
            ["tmux", "attach-session", "-t", "=gd/temp/panel/repo/shell/1"]
        )
        mock_sync.assert_called_once_with()
        mock_rebuild.assert_called_once_with("gd/repo/shell/1")

    @patch("gitdirector.integrations.tmux.rebuild_temp_panel_tmux_session")
    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_non_gd_session_skips_theme_sync(self, mock_run, mock_sync, mock_rebuild):
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("plain-session")
        mock_run.assert_called_once_with(["tmux", "attach-session", "-t", "=plain-session"])
        mock_sync.assert_not_called()
        mock_rebuild.assert_not_called()


class TestOpenInTmux:
    @patch("gitdirector.integrations.tmux.attach_tmux_session")
    @patch(
        "gitdirector.integrations.tmux.create_tmux_session",
        return_value="gd/my-repo/shell/1",
    )
    def test_creates_then_attaches(self, mock_create, mock_attach):
        path = Path("/tmp/my-repo")
        open_in_tmux("my-repo", path)
        mock_create.assert_called_once_with("my-repo", path)
        mock_attach.assert_called_once_with("gd/my-repo/shell/1")


class TestLaunchAgentInTmuxSession:
    @patch(
        "gitdirector.integrations.tmux._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_queues_cleanup_script(self, mock_run, _mock_marker):
        ready_marker = launch_agent_in_tmux_session("gd/my-repo/copilot/1", "copilot")
        cleanup_script = (
            "touch /tmp/gitdirector-agent.ready >/dev/null 2>&1 || true; "
            "clear; copilot; status=$?; "
            "rm -f /tmp/gitdirector-agent.ready >/dev/null 2>&1 || true; "
            f"tmux detach-client -s {shlex.quote('=gd/my-repo/copilot/1')} >/dev/null 2>&1 || true; "
            f"tmux kill-session -t {shlex.quote('=gd/my-repo/copilot/1')} >/dev/null 2>&1 || true; "
            "exit $status"
        )
        expected_command = f"sh -lc {shlex.quote(cleanup_script)}"
        assert ready_marker == Path("/tmp/gitdirector-agent.ready")
        mock_run.assert_called_once_with(
            [
                "tmux",
                "send-keys",
                "-t",
                "=gd/my-repo/copilot/1:",
                expected_command,
                "Enter",
            ],
            check=False,
        )


class TestMakeAgentReadyMarker:
    def test_returns_missing_marker_path(self):
        marker = _make_agent_ready_marker()

        assert marker.name.startswith("gitdirector-agent-")
        assert marker.suffix == ".ready"
        assert marker.exists() is False

    def test_ignores_missing_temp_file(self):
        with patch(
            "gitdirector.integrations.tmux.tempfile.mkstemp",
            return_value=(123, "/tmp/gitdirector-agent-test.ready"),
        ):
            with patch("gitdirector.integrations.tmux.os.close") as mock_close:
                with patch(
                    "gitdirector.integrations.tmux.Path.unlink", side_effect=FileNotFoundError
                ):
                    marker = _make_agent_ready_marker()

        assert marker == Path("/tmp/gitdirector-agent-test.ready")
        mock_close.assert_called_once_with(123)


class TestNormalizeProcessCommand:
    def test_empty_args_return_empty_string(self):
        assert _normalize_process_command("   ") == ""

    def test_returns_executable_basename(self):
        assert _normalize_process_command("/usr/local/bin/claude --model sonnet") == "claude"


class TestGetProcessSnapshot:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_failure_returns_empty_mappings(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        assert _get_process_snapshot() == ({}, {}, {}, {})

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_skips_malformed_rows(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="malformed row\n101 1 101 101 -zsh\n",
        )

        children_by_parent, commands_by_pid, pgid_by_pid, tpgid_by_pid = _get_process_snapshot()

        assert children_by_parent == {1: [101]}
        assert commands_by_pid == {101: "-zsh"}
        assert pgid_by_pid == {101: 101}
        assert tpgid_by_pid == {101: 101}


class TestResolvePaneCommand:
    def test_no_descendants_uses_fallback(self):
        assert _resolve_pane_command(1, "shell", "bash", {}, {}, {}, {}) == "bash"

    def test_cycle_skips_seen_pids(self):
        assert (
            _resolve_pane_command(
                1,
                "shell",
                "bash",
                {1: [2], 2: [1]},
                {2: "python"},
                {},
                {},
            )
            == "python"
        )

    def test_only_shell_descendants_pick_deepest_shell(self):
        assert (
            _resolve_pane_command(
                1,
                "shell",
                "bash",
                {1: [2], 2: [3]},
                {2: "-zsh", 3: "sh"},
                {},
                {},
            )
            == "sh"
        )

    def test_prefers_foreground_process_group(self):
        assert (
            _resolve_pane_command(
                1,
                "shell",
                "bash",
                {1: [2, 3]},
                {2: "git", 3: "python"},
                {2: 200, 3: 300},
                {1: 300},
            )
            == "python"
        )

    def test_falls_back_to_deepest_non_shell_without_foreground_match(self):
        assert (
            _resolve_pane_command(
                1,
                "shell",
                "bash",
                {1: [2, 3], 2: [4]},
                {2: "git", 3: "python", 4: "rg"},
                {2: 200, 3: 300, 4: 400},
                {1: 999},
            )
            == "rg"
        )


class TestGetAllSessionStatuses:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_empty_when_no_gd_panes_exist(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="other-session|bash|0|301\n")

        assert get_all_session_statuses() == {}

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_parses_output(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout=(
                    "gd/alpha/shell/1|zsh|0|101\n"
                    "gd/beta/claude/1|bash|0|201\n"
                    "other-session|bash|0|301\n"
                ),
            ),
            MagicMock(
                returncode=0,
                stdout=(
                    "201 1 201 202 -zsh\n202 201 202 202 sh -lc claude\n203 202 202 202 claude\n"
                ),
            ),
        ]
        result = get_all_session_statuses()
        assert result == {
            "gd/alpha/shell/1": {
                "command": "zsh",
                "dead": False,
            },
            "gd/beta/claude/1": {
                "command": "claude",
                "dead": False,
            },
        }

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_skips_panel_and_temp_wrapper_sessions(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout=(
                    "gd/panel/main|cat|0|101\n"
                    "gd/temp/panel/repo/shell/1|zsh|0|201\n"
                    "gd/repo/shell/1|zsh|0|301\n"
                ),
            ),
            MagicMock(returncode=0, stdout="301 1 301 301 zsh\n"),
        ]

        assert get_all_session_statuses() == {
            "gd/repo/shell/1": {
                "command": "zsh",
                "dead": False,
            }
        }

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_empty_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert get_all_session_statuses() == {}

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_dead_pane(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="gd/repo/shell/1|zsh|1|101\n",
            ),
            MagicMock(returncode=0, stdout="101 1 101 101 zsh\n"),
        ]
        result = get_all_session_statuses()
        assert result["gd/repo/shell/1"]["dead"] is True

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_skips_malformed_lines(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="gd/repo/bad\ngd/repo/shell/1|zsh|0|101\n",
            ),
            MagicMock(returncode=0, stdout="101 1 101 101 zsh\n"),
        ]
        result = get_all_session_statuses()
        assert len(result) == 1
        assert "gd/repo/shell/1" in result

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_invalid_pid_defaults_to_zero(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="gd/repo/shell/1|zsh|0|badnum\n",
            ),
            MagicMock(returncode=0, stdout="101 1 101 101 zsh\n"),
        ]
        result = get_all_session_statuses()
        assert "gd/repo/shell/1" in result

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_prefers_agent_command_over_helper_descendant(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="gd/repo/copilot/1|bash|0|70539\n",
            ),
            MagicMock(
                returncode=0,
                stdout=(
                    "70539 1 70539 70619 -zsh\n"
                    "70619 70539 70619 70619 sh -lc copilot\n"
                    "70624 70619 70619 70619 copilot\n"
                    "70625 70624 70619 70619 git status\n"
                ),
            ),
        ]

        result = get_all_session_statuses()

        assert result["gd/repo/copilot/1"]["command"] == "copilot"


class TestResolvePaneStatus:
    def test_dead_returns_idle(self):
        assert resolve_pane_status("shell", "zsh", dead=True) == "idle"

    def test_shell_with_shell_purpose_returns_idle(self):
        assert resolve_pane_status("shell", "zsh", dead=False) == "idle"

    def test_shell_with_agent_purpose_returns_idle(self):
        assert resolve_pane_status("claude", "zsh", dead=False) == "idle"

    def test_agent_running_returns_running(self):
        assert resolve_pane_status("claude", "claude", dead=False) == "running"

    def test_login_shell_detected(self):
        assert resolve_pane_status("shell", "-zsh", dead=False) == "idle"

    def test_login_shell_with_agent_purpose(self):
        assert resolve_pane_status("opencode", "-bash", dead=False) == "idle"

    def test_non_shell_command_returns_running(self):
        assert resolve_pane_status("shell", "python", dead=False) == "running"

    def test_all_known_shells(self):
        for shell in _SHELL_COMMANDS:
            assert resolve_pane_status("shell", shell, dead=False) == "idle"

    def test_bell_returns_waiting(self):
        assert resolve_pane_status("shell", "zsh", dead=False, bell=True) == "waiting"

    def test_bell_overrides_idle(self):
        assert resolve_pane_status("shell", "zsh", dead=True, bell=True) == "waiting"

    def test_bell_overrides_running(self):
        assert resolve_pane_status("claude", "claude", dead=False, bell=True) == "waiting"

    @patch("gitdirector.integrations.tmux.time")
    def test_agent_silent_returns_idle(self, mock_time):
        mock_time.time.return_value = 1700000020.0
        old_output = 1700000020.0 - _SILENCE_THRESHOLD_SECS
        assert (
            resolve_pane_status("opencode", "opencode", dead=False, last_output_time=old_output)
            == "idle"
        )

    @patch("gitdirector.integrations.tmux.time")
    def test_agent_recent_activity_returns_running(self, mock_time):
        mock_time.time.return_value = 1700000020.0
        recent = 1700000020.0 - _SILENCE_THRESHOLD_SECS + 1
        assert (
            resolve_pane_status("claude", "claude", dead=False, last_output_time=recent)
            == "running"
        )

    @patch("gitdirector.integrations.tmux.time")
    def test_agent_child_command_ignores_silence_threshold(self, mock_time):
        mock_time.time.return_value = 1700000100.0
        assert (
            resolve_pane_status("copilot", "git", dead=False, last_output_time=1700000000.0)
            == "running"
        )

    @patch("gitdirector.integrations.tmux.time")
    def test_non_agent_purpose_ignores_silence_threshold(self, mock_time):
        mock_time.time.return_value = 1700000100.0
        assert (
            resolve_pane_status("lazygit", "lazygit", dead=False, last_output_time=1700000000.0)
            == "running"
        )

    def test_known_agent_purposes(self):
        assert _AGENT_PURPOSES == {"opencode", "claude", "copilot", "codex"}

    @patch("gitdirector.integrations.tmux.time")
    def test_shell_purpose_ignores_silence_threshold(self, mock_time):
        mock_time.time.return_value = 1700000100.0
        assert (
            resolve_pane_status("shell", "python", dead=False, last_output_time=1700000000.0)
            == "running"
        )

    def test_zero_output_time_no_idle(self):
        assert (
            resolve_pane_status("opencode", "opencode", dead=False, last_output_time=0.0)
            == "running"
        )

    @patch("gitdirector.integrations.tmux.time")
    def test_exactly_at_threshold_returns_idle(self, mock_time):
        mock_time.time.return_value = 1700000010.0
        output_time = 1700000010.0 - _SILENCE_THRESHOLD_SECS
        assert (
            resolve_pane_status("opencode", "opencode", dead=False, last_output_time=output_time)
            == "idle"
        )


class TestControlModeReader:
    @patch("gitdirector.integrations.tmux.threading.Thread")
    def test_start_spawns_thread(self, mock_thread_cls):
        thread = MagicMock()
        mock_thread_cls.return_value = thread
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)

        reader.start()

        assert reader._running is True
        mock_thread_cls.assert_called_once_with(target=reader._run, daemon=True)
        thread.start.assert_called_once_with()

    def test_stop_kills_process_if_terminate_fails(self):
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._process = MagicMock()
        reader._process.terminate.side_effect = RuntimeError("boom")

        reader.stop()

        reader._process.kill.assert_called_once_with()

    def test_stop_waits_for_process_when_terminate_succeeds(self):
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._process = MagicMock()

        reader.stop()

        reader._process.terminate.assert_called_once_with()
        reader._process.wait.assert_called_once_with(timeout=2)

    def test_stop_ignores_kill_failure(self):
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._process = MagicMock()
        reader._process.terminate.side_effect = RuntimeError("boom")
        reader._process.kill.side_effect = RuntimeError("still broken")

        reader.stop()

        reader._process.kill.assert_called_once_with()

    def test_is_alive_reflects_thread_state(self):
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._running = True
        reader._thread = MagicMock()
        reader._thread.is_alive.return_value = True

        assert reader.is_alive() is True

    def test_parse_bell(self):
        events = []
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: events.append((s, e)))
        reader._parse_line("%bell @0 0")
        assert events == [("gd/repo/shell/1", "bell")]

    def test_parse_output(self):
        events = []
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: events.append((s, e)))
        reader._parse_line("%output %0 some data here")
        assert events == [("gd/repo/shell/1", "output")]

    def test_parse_exit(self):
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._running = True
        reader._parse_line("%exit")
        assert reader._running is False

    def test_ignores_other_lines(self):
        events = []
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: events.append((s, e)))
        reader._parse_line("%begin 1234")
        reader._parse_line("%end 1234")
        reader._parse_line("%session-changed $0 mysession")
        reader._parse_line("some random text")
        assert events == []

    @patch("gitdirector.integrations.tmux.subprocess.Popen")
    def test_run_parses_output_and_cleans_up(self, mock_popen):
        events = []
        process = MagicMock()
        process.stdout = iter(["%bell @0 0\n", "%output %0 hello\n"])
        mock_popen.return_value = process
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: events.append((s, e)))
        reader._running = True

        reader._run()

        assert events == [
            ("gd/repo/shell/1", "bell"),
            ("gd/repo/shell/1", "output"),
        ]
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=2)
        assert reader._running is False
        assert reader._process is None

    @patch("gitdirector.integrations.tmux.subprocess.Popen")
    def test_run_stops_before_parsing_when_not_running(self, mock_popen):
        events = []
        process = MagicMock()
        process.stdout = iter(["%bell @0 0\n"])
        mock_popen.return_value = process
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: events.append((s, e)))
        reader._running = False

        reader._run()

        assert events == []
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=2)

    @patch("gitdirector.integrations.tmux.subprocess.Popen")
    def test_run_ignores_kill_failure_during_cleanup(self, mock_popen):
        process = MagicMock()
        process.stdout = iter(())
        process.terminate.side_effect = RuntimeError("boom")
        process.kill.side_effect = RuntimeError("still broken")
        mock_popen.return_value = process
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._running = True

        reader._run()

        process.kill.assert_called_once_with()
        assert reader._running is False
        assert reader._process is None

    @patch("gitdirector.integrations.tmux.subprocess.Popen", side_effect=RuntimeError("boom"))
    def test_run_ignores_popen_errors(self, _mock_popen):
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._running = True

        reader._run()

        assert reader._running is False
        assert reader._process is None


class TestTmuxMonitor:
    @patch("gitdirector.integrations.tmux.threading.Thread")
    def test_start_spawns_sync_thread_once(self, mock_thread_cls):
        monitor = TmuxMonitor()
        thread = MagicMock()
        mock_thread_cls.return_value = thread

        REAL_TMUX_MONITOR_START(monitor)
        REAL_TMUX_MONITOR_START(monitor)

        assert monitor._running is True
        mock_thread_cls.assert_called_once_with(target=monitor._sync_sessions, daemon=True)
        thread.start.assert_called_once_with()

    def test_stop_stops_all_readers_and_clears_registry(self):
        monitor = TmuxMonitor()
        reader_one = MagicMock()
        reader_two = MagicMock()
        monitor._readers = {
            "gd/alpha/shell/1": reader_one,
            "gd/beta/claude/1": reader_two,
        }
        monitor._running = True

        REAL_TMUX_MONITOR_STOP(monitor)

        assert monitor._running is False
        assert monitor._readers == {}
        reader_one.stop.assert_called_once_with()
        reader_two.stop.assert_called_once_with()

    def test_stop_waits_for_sync_thread_to_exit(self):
        monitor = TmuxMonitor()
        sync_thread = MagicMock()
        sync_thread.is_alive.return_value = True
        monitor._sync_thread = sync_thread

        REAL_TMUX_MONITOR_STOP(monitor)

        assert monitor._sync_thread is None
        sync_thread.join.assert_called_once_with(timeout=3)

    @patch("gitdirector.integrations.tmux._ControlModeReader")
    def test_add_reader_starts_control_reader(self, mock_reader_cls):
        monitor = TmuxMonitor()
        reader = MagicMock()
        mock_reader_cls.return_value = reader

        monitor._add_reader("gd/repo/shell/1")

        assert monitor._readers["gd/repo/shell/1"] is reader
        reader.start.assert_called_once_with()

    def test_bell_event_sets_state(self):
        monitor = TmuxMonitor()
        monitor._on_event("gd/repo/shell/1", "bell")
        assert monitor.get_bell_state("gd/repo/shell/1") is True

    def test_output_event_updates_time(self):
        monitor = TmuxMonitor()
        before = time.time()
        monitor._on_event("gd/repo/shell/1", "output")
        after = time.time()
        last_output = monitor.get_last_output_time("gd/repo/shell/1")
        assert before <= last_output <= after

    def test_output_clears_bell_after_grace_period(self):
        monitor = TmuxMonitor()
        monitor._on_event("gd/repo/shell/1", "bell")
        assert monitor.get_bell_state("gd/repo/shell/1") is True

        with patch("gitdirector.integrations.tmux.time") as mock_time:
            bell_time = monitor._bell_time["gd/repo/shell/1"]
            mock_time.time.return_value = bell_time + _BELL_GRACE_SECS + 0.1
            monitor._on_event("gd/repo/shell/1", "output")

        assert monitor.get_bell_state("gd/repo/shell/1") is False

    def test_output_does_not_clear_bell_during_grace_period(self):
        monitor = TmuxMonitor()
        monitor._on_event("gd/repo/shell/1", "bell")
        bell_time = monitor._bell_time["gd/repo/shell/1"]

        with patch("gitdirector.integrations.tmux.time") as mock_time:
            mock_time.time.return_value = bell_time + _BELL_GRACE_SECS - 0.1
            monitor._on_event("gd/repo/shell/1", "output")

        assert monitor.get_bell_state("gd/repo/shell/1") is True

    def test_clear_bell(self):
        monitor = TmuxMonitor()
        monitor._on_event("gd/repo/shell/1", "bell")
        assert monitor.get_bell_state("gd/repo/shell/1") is True
        monitor.clear_bell("gd/repo/shell/1")
        assert monitor.get_bell_state("gd/repo/shell/1") is False

    def test_default_states(self):
        monitor = TmuxMonitor()
        assert monitor.get_bell_state("nonexistent") is False
        assert monitor.get_last_output_time("nonexistent") == 0.0
        assert monitor.get_last_content_change_time("nonexistent") == 0.0

    def test_remove_reader_clears_state(self):
        monitor = TmuxMonitor()
        monitor._on_event("gd/repo/shell/1", "bell")
        monitor._on_event("gd/repo/shell/1", "output")
        monitor._content_hashes["gd/repo/shell/1"] = "abc"
        monitor._last_content_change_time["gd/repo/shell/1"] = 100.0
        reader = MagicMock()
        monitor._readers["gd/repo/shell/1"] = reader
        monitor._remove_reader("gd/repo/shell/1")
        assert monitor.get_bell_state("gd/repo/shell/1") is False
        assert monitor.get_last_output_time("gd/repo/shell/1") == 0.0
        assert monitor.get_last_content_change_time("gd/repo/shell/1") == 0.0
        reader.stop.assert_called_once()

    @patch("gitdirector.integrations.tmux._capture_pane_text")
    def test_poll_content_changes_detects_new_content(self, mock_capture):
        monitor = TmuxMonitor()
        mock_capture.return_value = "hello world"
        monitor._poll_content_changes({"gd/repo/shell/1"})
        assert monitor.get_last_content_change_time("gd/repo/shell/1") > 0.0
        assert monitor._content_hashes["gd/repo/shell/1"] == _hash_content("hello world")

    @patch("gitdirector.integrations.tmux._capture_pane_text")
    def test_poll_content_changes_ignores_same_content(self, mock_capture):
        monitor = TmuxMonitor()
        mock_capture.return_value = "static screen"
        monitor._poll_content_changes({"gd/repo/shell/1"})
        first_time = monitor.get_last_content_change_time("gd/repo/shell/1")

        monitor._poll_content_changes({"gd/repo/shell/1"})
        assert monitor.get_last_content_change_time("gd/repo/shell/1") == first_time

    @patch("gitdirector.integrations.tmux._capture_pane_text")
    def test_poll_content_changes_updates_on_change(self, mock_capture):
        monitor = TmuxMonitor()
        mock_capture.return_value = "screen v1"
        monitor._poll_content_changes({"gd/repo/shell/1"})
        first_time = monitor.get_last_content_change_time("gd/repo/shell/1")

        mock_capture.return_value = "screen v2"
        monitor._poll_content_changes({"gd/repo/shell/1"})
        assert monitor.get_last_content_change_time("gd/repo/shell/1") > first_time

    @patch("gitdirector.integrations.tmux._capture_pane_text")
    def test_poll_content_changes_skips_failed_capture(self, mock_capture):
        monitor = TmuxMonitor()
        mock_capture.return_value = None
        monitor._poll_content_changes({"gd/repo/shell/1"})
        assert monitor.get_last_content_change_time("gd/repo/shell/1") == 0.0

    @patch("gitdirector.integrations.tmux._list_sessions")
    def test_sync_sessions_adds_removes_restarts_and_polls(self, mock_list_sessions):
        monitor = TmuxMonitor()
        monitor._running = True
        stale_reader = MagicMock()
        existing_reader = MagicMock()
        existing_reader.is_alive.return_value = False
        monitor._readers = {
            "gd/stale/shell/1": stale_reader,
            "gd/existing/shell/1": existing_reader,
        }
        mock_list_sessions.return_value = [
            "gd/new/shell/1",
            "gd/existing/shell/1",
            "other-session",
        ]
        added: list[str] = []
        removed: list[str] = []

        def add_reader(session_name: str):
            added.append(session_name)
            replacement = MagicMock()
            replacement.is_alive.return_value = True
            monitor._readers[session_name] = replacement

        def remove_reader(session_name: str):
            removed.append(session_name)
            monitor._readers.pop(session_name, None)

        monitor._add_reader = MagicMock(side_effect=add_reader)
        monitor._remove_reader = MagicMock(side_effect=remove_reader)
        monitor._poll_content_changes = MagicMock(
            side_effect=lambda sessions: setattr(monitor, "_running", False)
        )

        monitor._sync_sessions()

        assert set(added) == {"gd/new/shell/1", "gd/existing/shell/1"}
        assert set(removed) == {"gd/stale/shell/1", "gd/existing/shell/1"}
        monitor._poll_content_changes.assert_called_once_with(
            {"gd/new/shell/1", "gd/existing/shell/1"}
        )

    @patch("gitdirector.integrations.tmux._list_sessions")
    def test_sync_sessions_skips_panel_and_temp_wrapper_sessions(self, mock_list_sessions):
        monitor = TmuxMonitor()
        monitor._running = True
        mock_list_sessions.return_value = [
            "gd/repo/shell/1",
            "gd/panel/main",
            "gd/temp/panel/repo/shell/1",
        ]

        added: list[str] = []

        def add_reader(session_name: str):
            added.append(session_name)
            monitor._readers[session_name] = MagicMock(is_alive=MagicMock(return_value=True))

        monitor._add_reader = MagicMock(side_effect=add_reader)
        monitor._remove_reader = MagicMock()
        monitor._poll_content_changes = MagicMock(
            side_effect=lambda sessions: setattr(monitor, "_running", False)
        )

        monitor._sync_sessions()

        assert added == ["gd/repo/shell/1"]
        monitor._poll_content_changes.assert_called_once_with({"gd/repo/shell/1"})

    @patch("gitdirector.integrations.tmux.time.sleep")
    @patch("gitdirector.integrations.tmux._list_sessions", side_effect=RuntimeError("boom"))
    def test_sync_sessions_ignores_list_errors(self, _mock_list_sessions, mock_sleep):
        monitor = TmuxMonitor()
        monitor._running = True

        def stop_after_first_sleep(_seconds: float):
            monitor._running = False

        mock_sleep.side_effect = stop_after_first_sleep

        monitor._sync_sessions()

        mock_sleep.assert_called()


class TestCapturePaneText:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_returns_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="pane content\nhere\n")
        assert _capture_pane_text("gd/repo/shell/1") == "pane content\nhere\n"

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_returns_none_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _capture_pane_text("gd/repo/shell/1") is None


# ---------------------------------------------------------------------------
# Edge-case regression tests: tmux exact-match ``=`` prefix
# ---------------------------------------------------------------------------
# tmux uses *prefix matching* when ``-t`` targets don't match exactly.
# Without the ``=`` prefix every ``-t`` argument is vulnerable to accidentally
# matching a session whose name starts with the supplied string – the cascade
# kill bug.  The tests below guarantee the ``=`` prefix is always present.
# ---------------------------------------------------------------------------


class TestExactMatchSessionExists:
    """_session_exists must use ``=`` so ``gd/panel/dev`` doesn't match ``gd/panel/dev-tools``."""

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_has_session_uses_exact_prefix(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        _session_exists("gd/panel/dev")
        args = mock_run.call_args[0][0]
        assert args == ["tmux", "has-session", "-t", "=gd/panel/dev"]

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_similar_name_not_matched(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        result = _session_exists("gd/panel/dev")
        assert result is False
        target_arg = mock_run.call_args[0][0][3]
        assert target_arg.startswith("=")


class TestExactMatchKillTmuxSession:
    """kill_tmux_session must use ``=`` so killing one session can't cascade."""

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_kill_uses_exact_prefix(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        kill_tmux_session("gd/panel/dev")
        args = mock_run.call_args[0][0]
        assert args == ["tmux", "kill-session", "-t", "=gd/panel/dev"]

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_kill_cannot_prefix_match_similar_session(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        kill_tmux_session("gd/panel/dev")
        target = mock_run.call_args[0][0][3]
        assert target == "=gd/panel/dev"
        assert target != "gd/panel/dev"


class TestExactMatchAttachTmuxSession:
    """attach_tmux_session must use ``=`` for both switch-client and attach-session."""

    @patch(
        "gitdirector.integrations.tmux.rebuild_temp_panel_tmux_session",
        return_value="gd/temp/panel/repo/shell/1",
    )
    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_regular_session_switch_client_exact_temp_panel_target(
        self, mock_run, _mock_sync, _mock_rebuild
    ):
        with patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,12345,0"}):
            attach_tmux_session("gd/repo/shell/1")
        target = mock_run.call_args[0][0][3]
        assert target == "=gd/temp/panel/repo/shell/1"

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.reflow_panel_tmux_session")
    @patch("gitdirector.integrations.tmux._ensure_panel_resize_tracking")
    @patch("gitdirector.integrations.tmux._ensure_panel_prefix_bindings")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_switch_client_exact(
        self,
        mock_run,
        mock_prefix_bindings,
        mock_track_resize,
        mock_reflow,
        _mock_sync,
    ):
        with patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,12345,0"}):
            attach_tmux_session("gd/panel/dev")
        target = mock_run.call_args[0][0][3]
        assert target == "=gd/panel/dev"
        mock_prefix_bindings.assert_called_once_with()
        mock_track_resize.assert_called_once_with("gd/panel/dev")
        mock_reflow.assert_called_once_with("gd/panel/dev")

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.reflow_panel_tmux_session")
    @patch("gitdirector.integrations.tmux._ensure_panel_resize_tracking")
    @patch("gitdirector.integrations.tmux._ensure_panel_prefix_bindings")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_attach_session_exact(
        self,
        mock_run,
        mock_prefix_bindings,
        mock_track_resize,
        mock_reflow,
        _mock_sync,
    ):
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("gd/panel/dev")
        target = mock_run.call_args[0][0][3]
        assert target == "=gd/panel/dev"
        mock_prefix_bindings.assert_called_once_with()
        mock_track_resize.assert_called_once_with("gd/panel/dev")
        mock_reflow.assert_called_once_with("gd/panel/dev")


class TestPanelResizeTracking:
    @patch("gitdirector.integrations.tmux._session_exists", return_value=True)
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_sets_resize_hooks_on_panel_session_and_window(self, mock_run, _mock_exists):
        _ensure_panel_resize_tracking("gd/panel/dev")

        assert mock_run.call_args_list[0].args[0] == [
            "tmux",
            "set-window-option",
            "-q",
            "-t",
            "=gd/panel/dev:0",
            "aggressive-resize",
            "on",
        ]
        assert mock_run.call_args_list[1].args[0][:5] == [
            "tmux",
            "set-hook",
            "-t",
            "=gd/panel/dev:",
            "client-resized",
        ]
        assert mock_run.call_args_list[2].args[0][:6] == [
            "tmux",
            "set-hook",
            "-w",
            "-t",
            "=gd/panel/dev:0",
            "window-resized",
        ]

    @patch("gitdirector.integrations.tmux._session_exists", return_value=False)
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_skips_missing_panel_session(self, mock_run, _mock_exists):
        _ensure_panel_resize_tracking("gd/panel/dev")

        mock_run.assert_not_called()


class TestExactMatchPanelAttachFragment:
    """_panel_attach_fragment shell script must use ``=`` for all -t args."""

    def test_all_tmux_targets_use_equals(self):
        fragment = _panel_attach_fragment("gd/panel/dev")
        for part in fragment.split("tmux ")[1:]:
            if " -t " in part:
                target = part.split(" -t ")[1].split()[0]
                unquoted = target.strip("'\"")
                assert unquoted.startswith("=") or unquoted.startswith("$"), (
                    f"tmux -t target missing '=' prefix in fragment: ...tmux {part[:60]}..."
                )


class TestCleanupPanelAttachedSession:
    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux._current_window_target", return_value="gd/repo/shell/1:0")
    @patch("gitdirector.integrations.tmux._session_exists", return_value=True)
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_restores_session_chrome_when_last_panel_client_stops(
        self,
        mock_run,
        _mock_exists,
        _mock_window_target,
        mock_sync,
    ):
        def completed(stdout: str = "", returncode: int = 0):
            result = MagicMock()
            result.stdout = stdout
            result.returncode = returncode
            return result

        mock_run.side_effect = [
            completed("1\n"),
            completed("on\n"),
            completed("off\n"),
            completed("gd/repo/shell/1:2\n"),
            completed(),
            completed(),
            completed(),
            completed(),
            completed(),
            completed(),
        ]

        cleanup_panel_attached_session("gd/repo/shell/1", theme_name="rose-pine")

        assert mock_run.call_args_list[4].args[0] == [
            "tmux",
            "set-option",
            "-q",
            "-t",
            "=gd/repo/shell/1:",
            "status",
            "on",
        ]
        assert mock_run.call_args_list[5].args[0] == [
            "tmux",
            "set-window-option",
            "-q",
            "-t",
            "=gd/repo/shell/1:2",
            "pane-border-status",
            "off",
        ]
        mock_sync.assert_called_once_with("rose-pine")

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux._session_exists", return_value=True)
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_decrements_client_count_when_other_panel_clients_remain(
        self,
        mock_run,
        _mock_exists,
        mock_sync,
    ):
        result = MagicMock()
        result.stdout = "3\n"
        result.returncode = 0
        mock_run.side_effect = [result, MagicMock()]

        cleanup_panel_attached_session("gd/repo/shell/1")

        assert mock_run.call_args_list[1].args[0] == [
            "tmux",
            "set-option",
            "-q",
            "-t",
            "=gd/repo/shell/1:",
            "@gitdirector_panel_clients",
            "2",
        ]
        mock_sync.assert_not_called()


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
class TestPanelExitIntegration:
    def test_exiting_one_panel_pane_keeps_panel_and_other_session_alive(
        self,
        tmp_path,
        monkeypatch,
    ):
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = Path(tempfile.mkdtemp(prefix="gd-tmux-"))
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            suffix = uuid.uuid4().hex[:8]
            base_a = f"gd/repro-base-a-{suffix}"
            base_b = f"gd/repro-base-b-{suffix}"
            panel_name = f"repro-{suffix}"

            def run_tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["tmux", *args],
                    capture_output=True,
                    text=True,
                    check=check,
                )

            try:
                run_tmux("new-session", "-d", "-s", base_a, "-n", "a")
                run_tmux("new-session", "-d", "-s", base_b, "-n", "b")

                panel_session = rebuild_panel_tmux_session(
                    panel_name,
                    1,
                    2,
                    {1: base_a, 2: base_b},
                    layout_key="grid_1x2",
                )

                sessions_before = run_tmux(
                    "list-sessions", "-F", "#{session_name}"
                ).stdout.splitlines()
                assert panel_session in sessions_before
                assert base_a in sessions_before
                assert base_b in sessions_before
                assert not any(
                    name.startswith(f"gd/temp/panel/{panel_name}/") for name in sessions_before
                )

                run_tmux("send-keys", "-t", f"={panel_session}:0.1", "exit", "Enter")
                time.sleep(1)

                sessions_after = run_tmux(
                    "list-sessions", "-F", "#{session_name}"
                ).stdout.splitlines()
                pane_commands = run_tmux(
                    "list-panes",
                    "-t",
                    f"={panel_session}:0",
                    "-F",
                    "#{pane_index}|#{pane_current_command}",
                ).stdout.splitlines()

                assert panel_session in sessions_after
                assert base_a not in sessions_after
                assert base_b in sessions_after
                assert not any(
                    name.startswith(f"gd/temp/panel/{panel_name}/") for name in sessions_after
                )
                assert "1|tail" in pane_commands
                assert any(line.startswith("2|") and line != "2|tail" for line in pane_commands)
            finally:
                subprocess.run(["tmux", "kill-server"], capture_output=True, text=True, check=False)
                shutil.rmtree(tmux_dir, ignore_errors=True)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
class TestIdleSessionPreservationIntegration:
    def test_idle_polling_keeps_repo_panel_and_temp_wrapper_sessions_alive(
        self,
        tmp_path,
        monkeypatch,
    ):
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = Path(tempfile.mkdtemp(prefix="gd-tmux-idle-"))
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            repo_a = home_dir / "repo-a"
            repo_b = home_dir / "repo-b"
            repo_a.mkdir()
            repo_b.mkdir()

            def run_tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["tmux", *args],
                    capture_output=True,
                    text=True,
                    check=check,
                )

            monitor = TmuxMonitor()
            try:
                session_a = create_tmux_session("repo-a", repo_a, purpose="shell")
                session_b = create_tmux_session("repo-b", repo_b, purpose="shell")
                panel_session = rebuild_panel_tmux_session(
                    "idle-guard",
                    1,
                    2,
                    {1: session_a, 2: session_b},
                    layout_key="grid_1x2",
                    theme_name="rose-pine",
                )
                temp_wrapper = f"gd/temp/panel/{session_a[3:]}"
                run_tmux("new-session", "-d", "-s", temp_wrapper, "-n", "wrapper", "cat")
                run_tmux("set-option", "-t", f"={temp_wrapper}:", "destroy-unattached", "off")
                expected_sessions = {session_a, session_b, panel_session, temp_wrapper}

                monitor.start()
                for _ in range(12):
                    assert [entry["session_name"] for entry in list_all_gd_sessions()] == [
                        session_a,
                        session_b,
                    ]
                    assert set(get_all_session_statuses()) == {session_a, session_b}
                    sync_panel_tmux_config("rose-pine")

                    live_sessions = set(
                        run_tmux("list-sessions", "-F", "#{session_name}").stdout.splitlines()
                    )
                    assert expected_sessions.issubset(live_sessions)
                    time.sleep(0.15)
            finally:
                monitor.stop()
                subprocess.run(["tmux", "kill-server"], capture_output=True, text=True, check=False)
                shutil.rmtree(tmux_dir, ignore_errors=True)

    def test_repo_discovery_and_tmux_config_ignore_temp_wrappers_when_wrappers_exist(
        self,
        tmp_path,
        monkeypatch,
    ):
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = Path(tempfile.mkdtemp(prefix="gd-tmux-monitor-"))
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            repo_a = home_dir / "repo-a"
            repo_b = home_dir / "repo-b"
            repo_a.mkdir()
            repo_b.mkdir()

            def run_tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["tmux", *args],
                    capture_output=True,
                    text=True,
                    check=check,
                )

            try:
                session_a = create_tmux_session("repo-a", repo_a, purpose="shell")
                session_b = create_tmux_session("repo-b", repo_b, purpose="shell")
                rebuild_panel_tmux_session(
                    "idle-guard",
                    1,
                    2,
                    {1: session_a, 2: session_b},
                    layout_key="grid_1x2",
                    theme_name="rose-pine",
                )
                temp_wrapper = f"gd/temp/panel/{session_a[3:]}"
                run_tmux("new-session", "-d", "-s", temp_wrapper, "-n", "wrapper", "cat")
                run_tmux("set-option", "-t", f"={temp_wrapper}:", "destroy-unattached", "off")

                assert list_repo_sessions("repo-a") == [session_a]
                assert list_repo_sessions("repo-b") == [session_b]

                config_path = sync_panel_tmux_config("rose-pine")
                config_text = config_path.read_text()

                assert session_a in config_text
                assert session_b in config_text
                assert temp_wrapper not in config_text
            finally:
                subprocess.run(["tmux", "kill-server"], capture_output=True, text=True, check=False)
                shutil.rmtree(tmux_dir, ignore_errors=True)


class TestExactMatchEmbeddedTmuxAttachCommand:
    """_embedded_tmux_attach_command must use ``=`` in has-session check."""

    def test_has_session_uses_equals(self):
        cmd = _embedded_tmux_attach_command("gd/repo/shell/1")
        assert "has-session -t" in cmd
        has_session_part = cmd.split("has-session -t ")[1].split()[0]
        unquoted = has_session_part.strip("'\"")
        assert unquoted.startswith("="), f"has-session -t missing '=' prefix: {has_session_part}"

    def test_with_panel_proxy_uses_equals(self):
        cmd = _embedded_tmux_attach_command("gd/repo/shell/1", panel_name="Dev", pane_index=1)
        assert "has-session -t" in cmd
        has_session_part = cmd.split("has-session -t ")[1].split()[0]
        unquoted = has_session_part.strip("'\"")
        assert unquoted.startswith("=")


class TestExactMatchPanelPaneCommand:
    """_panel_pane_command must use ``=`` in has-session check."""

    def test_assigned_pane_uses_exact_has_session(self):
        cmd = _panel_pane_command("Dev", 1, "gd/repo/shell/1")
        assert "has-session -t" in cmd
        has_session_part = cmd.split("has-session -t ")[1].split()[0]
        unquoted = has_session_part.strip("'\"")
        assert unquoted.startswith("=")

    def test_unassigned_pane_has_no_tmux_target(self):
        cmd = _panel_pane_command("Dev", 1, None)
        script = shlex.split(cmd)[2]
        assert "has-session" not in cmd
        assert "UNASSIGNED" in cmd
        assert "printf '%s\\n' '' UNASSIGNED" in script
        assert "Panel: Dev" not in cmd
        assert "Pane 1: unassigned" not in cmd


class TestExactMatchLaunchAgent:
    """launch_agent_in_tmux_session must use exact session and pane targets."""

    @patch(
        "gitdirector.integrations.tmux._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_send_keys_target_uses_equals(self, mock_run, _mock_marker):
        launch_agent_in_tmux_session("gd/my-repo/copilot/1", "copilot")
        send_keys_args = mock_run.call_args[0][0]
        assert send_keys_args[0:3] == ["tmux", "send-keys", "-t"]
        assert send_keys_args[3] == "=gd/my-repo/copilot/1:"

    @patch(
        "gitdirector.integrations.tmux._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_cleanup_script_kill_session_uses_equals(self, mock_run, _mock_marker):
        launch_agent_in_tmux_session("gd/my-repo/copilot/1", "copilot")
        cleanup_cmd = mock_run.call_args[0][0][4]
        assert f"kill-session -t {shlex.quote('=gd/my-repo/copilot/1')}" in cleanup_cmd


class TestExactMatchCapturePaneText:
    """_capture_pane_text must use ``=`` prefix."""

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_capture_target_uses_equals(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="text")
        _capture_pane_text("gd/repo/shell/1")
        args = mock_run.call_args[0][0]
        assert args == ["tmux", "capture-pane", "-p", "-t", "=gd/repo/shell/1:"]


class TestExactMatchControlModeReader:
    """_ControlModeReader must use ``=`` prefix in attach-session."""

    def test_attach_command_uses_equals(self):
        reader = _ControlModeReader("gd/repo/shell/1", callback=lambda *a: None)
        with patch("gitdirector.integrations.tmux.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdout = iter([])
            mock_popen.return_value = mock_proc
            reader._run()
            popen_args = mock_popen.call_args[0][0]
            assert popen_args == ["tmux", "-C", "attach-session", "-t", "=gd/repo/shell/1", "-r"]


class TestExactMatchTmuxThemeConfig:
    """_tmux_theme_config must use ``=`` prefix in all set-option/set-window-option targets."""

    def test_all_config_lines_use_equals_prefix(self):
        config = _tmux_theme_config(
            badge_text="SHELL",
            label_text="my-repo",
            session_name="gd/my-repo/shell/1",
            pane_border_status="top",
            pane_border_format="test-format",
        )
        for line in config.strip().splitlines():
            if " -t " not in line:
                continue
            target = line.split(" -t ")[1].split()[0]
            unquoted = target.strip("'\"")
            assert unquoted.startswith("="), f"config line missing '=' prefix in -t target: {line}"

    def test_custom_window_target_gets_equals(self):
        config = _tmux_theme_config(
            badge_text="PANEL",
            label_text="dev",
            session_name="gd/panel/dev",
            window_target="gd/panel/dev:0",
        )
        for line in config.strip().splitlines():
            if "set-window-option" in line and " -t " in line:
                target = line.split(" -t ")[1].split()[0]
                unquoted = target.strip("'\"")
                assert unquoted.startswith("="), f"window target missing '=' prefix: {line}"


class TestExactMatchCurrentWindowTarget:
    """_current_window_target must use ``=`` prefix."""

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_display_message_uses_equals(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="gd/repo/shell/1:0\n")
        _current_window_target("gd/repo/shell/1")
        args = mock_run.call_args[0][0]
        assert "-t" in args
        t_index = args.index("-t")
        assert args[t_index + 1] == "=gd/repo/shell/1:"


class TestExactMatchSourceCodeAudit:
    """Scan tmux.py source for any subprocess ``-t`` arg missing the ``=`` prefix.

    This is a structural guard: any new code that passes ``-t`` to a subprocess
    call list without ``=`` will be caught here.
    """

    def test_all_subprocess_list_targets_use_equals(self):
        import ast
        import inspect

        import gitdirector.integrations.tmux as tmux_mod

        source = inspect.getsource(tmux_mod)
        tree = ast.parse(source)

        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.List):
                continue
            elts = node.elts
            for i, elt in enumerate(elts):
                if not (isinstance(elt, ast.Constant) and elt.value == "-t"):
                    continue
                if i + 1 >= len(elts):
                    continue
                next_elt = elts[i + 1]
                if isinstance(next_elt, ast.Constant):
                    val = str(next_elt.value)
                    if not val.startswith("="):
                        violations.append(f"Line {node.lineno}: literal '-t' followed by {val!r}")
                elif isinstance(next_elt, ast.JoinedStr):
                    first_val = next_elt.values[0] if next_elt.values else None
                    if isinstance(first_val, ast.Constant) and not str(first_val.value).startswith(
                        "="
                    ):
                        violations.append(
                            f"Line {node.lineno}: f-string '-t' target doesn't start with '='"
                        )
                    elif isinstance(first_val, ast.FormattedValue):
                        violations.append(
                            f"Line {node.lineno}: f-string '-t' target starts with a variable (should prefix '=')"
                        )
        assert violations == [], (
            "tmux subprocess -t targets missing '=' exact-match prefix:\n" + "\n".join(violations)
        )
