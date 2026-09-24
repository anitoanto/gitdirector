"""The deck: a session shown beside the session sidebar."""

from __future__ import annotations

import shutil
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from gitdirector.integrations.tmux import deck as D
from gitdirector.integrations.tmux.core import _is_helper_session, _run_tmux, _session_exists

from ._shared import _cleanup_tmux_tmpdir, _make_short_tmux_tmpdir, _tmux_integration_lock


def _result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["tmux"], returncode, stdout, "")


class TestNames:
    def test_decks_and_their_views_are_helpers(self):
        assert _is_helper_session("gd/deck/123-abc")
        assert _is_helper_session("gd/view/deck-123-abc-def")

    def test_deck_names_are_not_repository_sessions(self):
        name = D._new_deck_name()
        assert name.startswith("gd/deck/")
        assert not D.is_repo_session(name)
        assert D.is_repo_session("gd/alpha_abcde/claude/1")

    def test_views_are_named_after_their_deck(self):
        assert D._new_view_name("gd/deck/12-ab").startswith("gd/view/deck-12-ab-")


class TestSidebarWidth:
    @pytest.mark.parametrize(
        ("window", "collapsed", "expected"),
        [(200, False, 32), (96, False, 32), (95, False, 31), (60, False, 20), (200, True, 5)],
    )
    def test_width(self, window, collapsed, expected):
        assert D.sidebar_width(window, collapsed) == expected


class TestReadDeckState:
    def _state(self, stdout: str):
        with patch.object(D, "_run_tmux", return_value=_result(stdout)):
            return D.read_deck_state("gd/deck/1-a")

    def test_parses_panes_clients_and_sessions(self):
        state = self._state(
            "D\tgd/r/claude/1\t%2\t%3\t1\n"
            "P\t%2\t/dev/ttys002\tbash\t\t0\n"
            "P\t%3\t/dev/ttys003\tpython3\t\t1\n"
            "C\t/dev/ttys002\n"
            "C\t/dev/ttys009\n"
            "S\tgd/r/claude/1\n"
            "S\tgd/deck/1-a\n"
        )
        assert state.target == "gd/r/claude/1"
        assert state.main.pane_id == "%2"
        assert state.sidebar.pane_id == "%3"
        assert state.main_attached
        assert state.sidebar_focused
        assert state.live_sessions == {"gd/r/claude/1", "gd/deck/1-a"}

    def test_unattached_deck_never_has_sidebar_focus(self):
        state = self._state("D\t\t%2\t%3\t0\nP\t%2\t/dev/a\tsleep\t1\t0\nP\t%3\t/dev/b\tpy\t\t1\n")
        assert state.target is None
        assert state.main.placeholder
        assert not state.main_attached
        assert not state.sidebar_focused

    def test_closed_panes_are_none(self):
        state = self._state("D\tgd/r/claude/1\t%2\t%3\t1\nP\t%3\t/dev/b\tpy\t\t1\n")
        assert state.main is None
        assert state.sidebar is not None

    def test_gone_deck(self):
        with patch.object(D, "_run_tmux", return_value=_result("", 1)):
            assert D.read_deck_state("gd/deck/1-a") is None


def _tmux_calls(mock_run: MagicMock) -> list[list[str]]:
    commands: list[list[str]] = []
    for call in mock_run.call_args_list:
        current: list[str] = []
        for token in call.args[0]:
            if token == ";":
                commands.append(current)
                current = []
            else:
                current.append(token)
        commands.append(current)
    return commands


def _binding(commands: list[list[str]], key: str) -> list[str]:
    return next(c for c in commands if c[:1] == ["bind-key"] and c[c.index("prefix") + 1] == key)


_WRAPPED_S = (
    'bind-key    -T prefix s       if-shell -F "#{m:gd/deck/*,#{session_name}}" '
    '"run-shell -C x" "choose-tree -Zs"\n'
)


def _run_with(list_keys: str, options: dict[str, str] | None = None) -> list[list[str]]:
    options = options or {}

    def respond(args, **_):
        if args[0] == "list-keys":
            return _result(list_keys)
        if args[:2] == ["show-options", "-gqv"]:
            return _result(options.get(args[2], ""))
        return _result()

    mock_run = MagicMock(side_effect=respond)
    with patch.object(D, "_run_tmux", mock_run):
        D.ensure_deck_prefix_bindings()
    return _tmux_calls(mock_run)


class TestPrefixBindings:
    def test_wraps_the_original_binding(self):
        commands = _run_with(
            "bind-key    -T prefix b       display-panes\n"
            "bind-key -r -T prefix Left    select-pane -L\n"
        )
        bind_b = _binding(commands, "b")
        assert bind_b[4:7] == ["if-shell", "-F", "#{m:gd/deck/*,#{session_name}}"]
        assert bind_b[-1] == "display-panes"
        assert ["set-option", "-g", "@gd_prefix_original_b", "display-panes"] in commands
        # Nothing was bound to Tab: outside a deck it stays unbound in effect.
        bind_tab = _binding(commands, "Tab")
        assert len(bind_tab) == 8
        assert ["set-option", "-gu", "@gd_prefix_original_tab"] in commands

    def test_rewrapping_keeps_the_stored_original(self):
        wrapped_b = (
            'bind-key    -T prefix b       if-shell -F "#{m:gd/deck/*,#{session_name}}" '
            '"run-shell -C x" "display-panes"\n'
        )
        commands = _run_with(wrapped_b, {"@gd_prefix_original_b": "my-own-b\n"})
        assert _binding(commands, "b")[-1] == "my-own-b"
        assert not any(c[:3] == ["set-option", "-g", "@gd_prefix_original_b"] for c in commands)

    def test_prefix_s_is_given_back_its_original(self):
        commands = _run_with(_WRAPPED_S, {"@gd_prefix_original_s": "choose-tree -Zs\n"})
        assert ["bind-key", "-T", "prefix", "s", "choose-tree -Zs"] in commands
        assert ["set-option", "-gu", "@gd_prefix_original_s"] in commands

    def test_prefix_s_without_a_stored_original_is_unbound(self):
        commands = _run_with(_WRAPPED_S)
        assert ["unbind-key", "-T", "prefix", "s"] in commands

    def test_an_unwrapped_prefix_s_is_left_alone(self):
        commands = _run_with("bind-key    -T prefix s       my-sessions\n")
        assert not any("s" in c[3:5] for c in commands if c[:1] in (["bind-key"], ["unbind-key"]))


class TestReapStaleDecks:
    def test_only_old_unattached_decks_and_views(self):
        now = int(time.time())
        listing = "\n".join(
            [
                f"gd/deck/1-a\t0\t{now - 600}",
                f"gd/deck/2-b\t1\t{now - 600}",
                f"gd/deck/3-c\t0\t{now}",
                f"gd/view/deck-1-a-x\t0\t{now - 600}",
                f"gd/view/panel-1\t0\t{now - 600}",
                f"gd/r_abcde/shell/1\t0\t{now - 600}",
            ]
        )
        with (
            patch.object(D, "_run_tmux", return_value=_result(listing)),
            patch.object(D, "kill_tmux_session", return_value=True) as kill,
        ):
            assert D.reap_stale_decks() == ["gd/deck/1-a", "gd/view/deck-1-a-x"]
        assert [call.args[0] for call in kill.call_args_list] == [
            "gd/deck/1-a",
            "gd/view/deck-1-a-x",
        ]


class TestCloseDeck:
    def test_switches_clients_back_before_killing(self):
        mock_run = MagicMock(side_effect=[_result("host\n/dev/ttys001\n/dev/ttys002\n"), _result()])
        with (
            patch.object(D, "_run_tmux", mock_run),
            patch.object(D, "_session_exists", return_value=True),
            patch.object(D, "kill_tmux_session") as kill,
        ):
            D.close_deck("gd/deck/1-a")
        commands = _tmux_calls(MagicMock(call_args_list=mock_run.call_args_list[1:]))
        assert commands == [
            ["switch-client", "-c", "/dev/ttys001", "-t", "=host"],
            ["switch-client", "-c", "/dev/ttys002", "-t", "=host"],
        ]
        kill.assert_called_once_with("gd/deck/1-a")

    def test_without_a_return_session_the_deck_is_just_killed(self):
        mock_run = MagicMock(return_value=_result("\n/dev/ttys001\n"))
        with (
            patch.object(D, "_run_tmux", mock_run),
            patch.object(D, "kill_tmux_session") as kill,
        ):
            D.close_deck("gd/deck/1-a")
        assert mock_run.call_count == 1
        kill.assert_called_once_with("gd/deck/1-a")


class TestAttachDeck:
    @patch.object(D, "kill_tmux_session", return_value=False)
    @patch.object(D, "create_deck", return_value="gd/deck/1-a")
    @patch.object(D, "_run_tmux", return_value=_result())
    def test_outside_tmux_attaches_then_arms_destroy_unattached(self, mock_run, create, _kill):
        with patch.dict("os.environ", {}, clear=True):
            assert D.attach_deck("gd/r/claude/1") is True
        create.assert_called_once_with("gd/r/claude/1", return_to=None)
        assert mock_run.call_args.args[0] == [
            "attach-session",
            "-t",
            "=gd/deck/1-a",
            ";",
            "set-option",
            "-t",
            "=gd/deck/1-a:",
            "destroy-unattached",
            "on",
        ]
        assert mock_run.call_args.kwargs["timeout"] is None

    @patch.object(D, "kill_tmux_session", return_value=True)
    @patch.object(D, "create_deck", return_value="gd/deck/1-a")
    @patch.object(D, "_run_tmux", return_value=_result(returncode=1))
    def test_failed_attach_with_the_deck_still_there_raises(self, _run, _create, _kill):
        with patch.dict("os.environ", {}, clear=True), pytest.raises(D.TmuxError):
            D.attach_deck("gd/r/claude/1")

    @patch.object(D, "create_deck", return_value="gd/deck/1-a")
    @patch.object(D, "_current_session", return_value="host")
    @patch.object(D, "_run_tmux", return_value=_result())
    def test_inside_tmux_switches_and_remembers_where_it_came_from(
        self, mock_run, _current, create
    ):
        with patch.dict("os.environ", {"TMUX": "/tmp/x,1,0"}):
            assert D.attach_deck("gd/r/claude/1") is False
        create.assert_called_once_with("gd/r/claude/1", return_to="host")
        assert mock_run.call_args.args[0][:3] == ["switch-client", "-t", "=gd/deck/1-a"]


# -- real tmux ---------------------------------------------------------------


def _wait_for(predicate, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    return predicate()


def _out(*args: str) -> str:
    return _run_tmux(list(args), text=True).stdout.strip()


def _pane_widths(deck: str) -> dict[str, int]:
    rows = _out("list-panes", "-t", f"={deck}:^", "-F", "#{pane_id} #{pane_width}")
    return {pane: int(width) for pane, width in (line.split() for line in rows.splitlines())}


@pytest.fixture
def tmux_server(tmp_path, monkeypatch):
    with _tmux_integration_lock():
        tmux_dir = _make_short_tmux_tmpdir()
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
        monkeypatch.delenv("TMUX", raising=False)
        # The sidebar app is exercised on its own; here it only holds the pane.
        monkeypatch.setattr(D, "_sidebar_command", lambda deck: "cat")
        try:
            for name in ("gd/alpha_aaaaa/claude/1", "gd/beta_bbbbb/shell/1"):
                _run_tmux(["new-session", "-d", "-s", name, "-x", "120", "-y", "40", "cat"])
            yield
        finally:
            _run_tmux(["kill-server"])
            _cleanup_tmux_tmpdir(tmux_dir)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
@pytest.mark.usefixtures("tmux_server")
def test_deck_lifecycle():
    first, second = "gd/alpha_aaaaa/claude/1", "gd/beta_bbbbb/shell/1"
    deck = D.create_deck(first)

    state = _wait_for(lambda: (s := D.read_deck_state(deck)) and s.main_attached and s)
    assert state.target == first
    assert _out("display-message", "-p", "-t", f"={deck}:", "#{@gd_badge} #{@gd_label}") == (
        "CLAUDE alpha/claude/1"
    )
    views = [s for s in _out("list-sessions", "-F", "#{session_name}").split() if "view" in s]
    assert len(views) == 1

    # Switching reuses the client in the main pane: same pane, new view.
    D.show_session(deck, second)
    assert _wait_for(lambda: not _session_exists(views[0])), "the first view was not destroyed"
    state = D.read_deck_state(deck)
    assert state.target == second and state.main_attached
    assert _out("display-message", "-p", "-t", f"={deck}:", "#{pane_id}") == state.main.pane_id

    # The shown session's program exiting ends the session and its view:
    # the main pane is left waiting, for the sidebar to notice.
    _run_tmux(["send-keys", "-t", f"={second}:", "C-d"])
    state = _wait_for(
        lambda: (
            (s := D.read_deck_state(deck))
            and s.main.command == D.ATTACH_ENDED_COMMAND
            and not s.main_attached
            and s
        )
    )
    assert state, "the main pane never fell back to waiting"

    D.show_placeholder(deck, state.main.pane_id, "session ended", "shell beta/1", "hint")
    state = D.read_deck_state(deck)
    assert state.main.placeholder and state.target is None
    assert _out("display-message", "-p", "-t", f"={deck}:", "#{pane_id}") == state.sidebar.pane_id

    # Showing a session again respawns the pane with a fresh client.
    D.show_session(deck, first)
    state = _wait_for(lambda: (s := D.read_deck_state(deck)) and s.main_attached and s)
    assert state.target == first and not state.main.placeholder

    # A killed session is gone from tmux's list at once, even though the
    # view still holds its window: that is what the sidebar goes by.
    _run_tmux(["new-session", "-d", "-s", second, "cat"])
    D.show_session(deck, second)
    _wait_for(lambda: (s := D.read_deck_state(deck)) and s.main_attached)
    _run_tmux(["kill-session", "-t", f"={second}"])
    assert second not in D.read_deck_state(deck).live_sessions
    D.show_session(deck, first)
    state = _wait_for(lambda: (s := D.read_deck_state(deck)) and s.main_attached and s)

    # The sidebar keeps its width through resizes, collapsed or not.
    sidebar = state.sidebar.pane_id
    _run_tmux(["set-option", "-t", f"={deck}:", "window-size", "manual"])
    for width, expected in ((200, 32), (60, 20), (150, 32)):
        _run_tmux(["resize-window", "-t", f"={deck}:^", "-x", str(width), "-y", "40"])
        assert _wait_for(lambda: _pane_widths(deck)[sidebar] == expected), width
    D.set_sidebar_collapsed(deck, True)
    assert _pane_widths(deck)[sidebar] == D.SIDEBAR_RAIL_WIDTH
    _run_tmux(["resize-window", "-t", f"={deck}:^", "-x", "180", "-y", "40"])
    assert _wait_for(lambda: _pane_widths(deck)[sidebar] == D.SIDEBAR_RAIL_WIDTH)
    D.set_sidebar_collapsed(deck, False)
    assert _pane_widths(deck)[sidebar] == D.SIDEBAR_WIDTH

    # A closed main pane can be put back.
    _run_tmux(["kill-pane", "-t", state.main.pane_id])
    main = D.recreate_main_pane(deck, sidebar)
    assert D.read_deck_state(deck).main.pane_id == main

    # prefix Tab and b are wrapped; prefix s keeps tmux's own meaning.
    keys = {
        line.split()[3]: line
        for line in _out("list-keys", "-T", "prefix").splitlines()
        if line.split()[1:3] == ["-T", "prefix"]
    }
    assert "#{m:gd/deck/*,#{session_name}}" in keys["Tab"]
    assert "#{m:gd/deck/*,#{session_name}}" in keys["b"]
    assert keys["s"].split(None, 4)[4] == "choose-tree -Zs"

    D.close_deck(deck)
    assert D.read_deck_state(deck) is None
    assert _session_exists(first)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
@pytest.mark.usefixtures("tmux_server")
def test_stale_unattached_decks_are_reaped(monkeypatch):
    deck = D.create_deck("gd/alpha_aaaaa/claude/1")
    monkeypatch.setattr(D, "_STALE_DECK_SECS", -1)
    assert deck in D.reap_stale_decks()
    assert D.read_deck_state(deck) is None


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
@pytest.mark.usefixtures("tmux_server")
def test_prefix_s_wrapped_by_an_earlier_version_is_restored():
    _run_tmux(["set-option", "-g", "@gd_prefix_original_s", "choose-tree -Zs"])
    _run_tmux(
        [
            "bind-key",
            "-T",
            "prefix",
            "s",
            "if-shell",
            "-F",
            "#{m:gd/deck/*,#{session_name}}",
            "display-message old",
            "choose-tree -Zs",
        ]
    )
    D.ensure_deck_prefix_bindings()
    line = next(
        line for line in _out("list-keys", "-T", "prefix").splitlines() if line.split()[3] == "s"
    )
    assert line.split(None, 4)[4] == "choose-tree -Zs"
    assert D._global_option("@gd_prefix_original_s") == ""
