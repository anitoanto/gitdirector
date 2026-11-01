"""The script that hands a copy made in a GitDirector session to the user's terminal."""

import subprocess
import sys

import pytest

from gitdirector.integrations import clipboard_forward as F
from gitdirector.integrations.tmux.deck import clipboard_forward_path

from ._timeouts import TMUX_CMD_TIMEOUT

NOW = 1790000000.0

# A deck: Ghostty's client on the deck, whose main pane runs the nested
# client on the view; the view is grouped with the work session.
PANES = [
    ["/dev/ttys002", "gd/deck/1-a"],
    ["/dev/ttys003", "gd/deck/1-a"],
    ["/dev/ttys004", "gd/view/deck-1-a-b"],
    ["/dev/ttys004", "gd/repo_aaaaa/shell/1"],
]
CLIENTS = [
    ["/dev/ttys000", "/dev/ttys000", "gd/deck/1-a"],
    ["/dev/ttys002", "/dev/ttys002", "gd/view/deck-1-a-b"],
]


class TestNewestBuffer:
    def test_a_copy_just_made_is_forwarded(self):
        rows = [[str(int(NOW) - 100), "buffer0"], [str(int(NOW) - 1), "buffer1"]]
        assert F.newest_buffer(rows, NOW) == ("buffer1", f"{int(NOW) - 1} buffer1")

    def test_an_old_buffer_is_no_copy_of_this_key(self):
        # The program in the pane took the click: nothing was copied.
        assert F.newest_buffer([[str(int(NOW) - 60), "buffer0"]], NOW) is None

    def test_no_buffers(self):
        assert F.newest_buffer([], NOW) is None
        assert F.newest_buffer([["garbage", "b"]], NOW) is None


class TestTerminalClients:
    def test_a_nested_client_climbs_to_the_terminal(self):
        assert F.terminal_clients("/dev/ttys002", PANES, CLIENTS) == ["/dev/ttys000"]

    def test_a_client_in_a_terminal_already_got_the_copy(self):
        assert F.terminal_clients("/dev/ttys000", PANES, CLIENTS) == []

    def test_every_terminal_showing_the_deck_gets_it(self):
        clients = [*CLIENTS, ["/dev/ttys009", "/dev/ttys009", "gd/deck/1-a"]]
        assert F.terminal_clients("/dev/ttys002", PANES, clients) == [
            "/dev/ttys000",
            "/dev/ttys009",
        ]

    def test_a_loop_of_clients_ends(self):
        panes = [["/dev/a", "s1"], ["/dev/b", "s2"]]
        clients = [["/dev/a", "a", "s2"], ["/dev/b", "b", "s1"]]
        assert F.terminal_clients("/dev/a", panes, clients) == []


class TestNativeCopyCommand:
    @pytest.fixture
    def installed(self, monkeypatch):
        tools: set[str] = set()
        monkeypatch.setattr(F.shutil, "which", lambda name: name if name in tools else None)
        return tools

    def test_macos(self, installed):
        installed.add("pbcopy")
        assert F.native_copy_command("darwin", {}) == ["pbcopy"]

    def test_wayland_before_x11(self, installed):
        installed.update({"wl-copy", "xclip"})
        environ = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
        assert F.native_copy_command("linux", environ) == ["wl-copy"]

    def test_x11_takes_whichever_tool_is_installed(self, installed):
        installed.add("xsel")
        assert F.native_copy_command("linux", {"DISPLAY": ":0"}) == [
            "xsel",
            "--clipboard",
            "--input",
        ]

    def test_wsl(self, installed):
        installed.add("clip.exe")
        assert F.native_copy_command("linux", {"WSL_DISTRO_NAME": "Ubuntu"}) == ["clip.exe"]

    def test_none_without_a_display_or_tool(self, installed):
        # Over SSH, or a headless box: OSC 52 is all there is.
        assert F.native_copy_command("linux", {}) is None
        assert F.native_copy_command("darwin", {}) is None


class TestForward:
    @pytest.fixture
    def tmux(self, monkeypatch):
        calls: list[tuple] = []
        state = {"forwarded": "", "buffers": f"{int(NOW)}\tbuffer3\n"}

        def fake(socket, *args, data=None):
            calls.append((args, data))
            if args[0] == "list-buffers":
                return state["buffers"].encode()
            if args[0] == "show-options":
                return state["forwarded"].encode()
            if args[0] == "list-panes":
                return "".join("\t".join(row) + "\n" for row in PANES).encode()
            if args[0] == "list-clients":
                return "".join("\t".join(row) + "\n" for row in CLIENTS).encode()
            if args[0] == "save-buffer":
                return b"copied text"
            return b""

        monkeypatch.setattr(F, "_tmux", fake)
        native: list[bytes] = []
        monkeypatch.setattr(F, "_native_copy", native.append)
        return calls, state, native

    def test_a_nested_copy_reaches_the_terminal_and_the_system_clipboard(self, tmux):
        calls, _, native = tmux
        F.forward("/sock", "/dev/ttys002", NOW)
        assert (
            ("load-buffer", "-w", "-b", "buffer3", "-t", "/dev/ttys000", "-"),
            b"copied text",
        ) in calls
        assert (("set-option", "-g", F.FORWARDED_OPTION, f"{int(NOW)} buffer3"), None) in calls
        assert native == [b"copied text"]

    def test_a_direct_copy_only_goes_to_the_system_clipboard(self, tmux):
        calls, _, native = tmux
        F.forward("/sock", "/dev/ttys000", NOW)
        assert not any(args[0] == "load-buffer" for args, _ in calls)
        assert native == [b"copied text"]

    def test_a_copy_is_forwarded_once(self, tmux):
        calls, state, native = tmux
        state["forwarded"] = f"{int(NOW)} buffer3"
        F.forward("/sock", "/dev/ttys002", NOW)
        assert [args[0] for args, _ in calls] == ["list-buffers", "show-options"]
        assert native == []

    def test_nothing_new_was_copied(self, tmux):
        calls, state, native = tmux
        state["buffers"] = f"{int(NOW) - 60}\tbuffer3\n"
        F.forward("/sock", "/dev/ttys002", NOW)
        assert [args[0] for args, _ in calls] == ["list-buffers"]
        assert native == []


def test_the_script_prints_nothing_and_succeeds_on_garbage():
    for argv in ([], ["/no/such/socket", "/dev/ttys999"]):
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(clipboard_forward_path()), *argv],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
            timeout=TMUX_CMD_TIMEOUT,
        )
        assert result.returncode == 0
        assert result.stdout == "" and result.stderr == ""
