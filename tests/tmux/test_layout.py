"""Layout and formatting tests for tmux integration helpers."""

from pathlib import Path
from unittest.mock import patch

from gitdirector.integrations.tmux import (
    _build_layout_spec,
    _build_panel_layout,
    _distribute_equal,
    _ensure_panel_prefix_bindings,
    _layout_checksum,
    _sanitize_repo_name,
    _span_size,
    rebuild_panel_tmux_session,
)

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
        "gitdirector.integrations.tmux.panels._list_window_panes_row_major",
        return_value=["%0", "%1", "%2"],
    )
    @patch("gitdirector.integrations.tmux.panels._tmux_output")
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
        "gitdirector.integrations.tmux.panels._list_window_panes_row_major",
        return_value=["%0", "%1", "%2", "%3"],
    )
    @patch("gitdirector.integrations.tmux.panels._tmux_output")
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
        "gitdirector.integrations.tmux.panels._list_window_panes_row_major",
        return_value=["%0", "%1", "%2"],
    )
    @patch("gitdirector.integrations.tmux.panels._tmux_output")
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
        "gitdirector.integrations.tmux.panels._list_window_panes_row_major",
        return_value=["%0", "%1", "%2", "%3", "%4"],
    )
    @patch("gitdirector.integrations.tmux.panels._tmux_output")
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
        "gitdirector.integrations.tmux.panels._list_window_panes_row_major",
        return_value=["%0", "%1", "%2", "%3", "%4", "%5", "%6", "%7"],
    )
    @patch("gitdirector.integrations.tmux.panels._tmux_output")
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
        "gitdirector.integrations.tmux.panels._list_window_panes_row_major",
        return_value=["%0", "%1", "%2", "%3", "%4", "%5", "%6", "%7", "%8"],
    )
    @patch("gitdirector.integrations.tmux.panels._tmux_output")
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
        "gitdirector.integrations.tmux.panels._list_window_panes_row_major",
        return_value=["%0", "%1", "%2", "%3", "%4", "%5"],
    )
    @patch("gitdirector.integrations.tmux.panels._tmux_output")
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

class TestRebuildPanelTmuxSession:
    @patch("gitdirector.integrations.tmux.shutil.get_terminal_size", return_value=(80, 24))
    @patch("gitdirector.integrations.tmux.panels._ensure_panel_prefix_bindings")
    @patch("gitdirector.integrations.tmux.panels.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.panels._load_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.panels._configure_panel_window")
    @patch("gitdirector.integrations.tmux.panels._equalize_panel_layout")
    @patch(
        "gitdirector.integrations.tmux.panels._build_panel_layout",
        return_value=["%0", "%1", "%2", "%3"],
    )
    @patch("gitdirector.integrations.tmux.panels.kill_panel_tmux_session")
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
