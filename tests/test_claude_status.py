"""The Claude Code status hook that GitDirector passes inline to Claude."""

import json
import shutil
import subprocess
import sys

import pytest

from gitdirector.agents import (
    AGENT_APPROVAL,
    AGENT_HELPERS_OPTION,
    AGENT_STATE_OPTION,
    AGENT_TRANSCRIPT_OPTION,
    AGENT_WAITER_OPTION,
    CLAUDE_STATUS_EVENTS,
    claude_status_hook_path,
)
from gitdirector.integrations import claude_status as H

from ._timeouts import TMUX_CMD_TIMEOUT
from .tmux._shared import _cleanup_tmux_tmpdir, _make_short_tmux_tmpdir, _tmux_integration_lock

PANE = "%7"
NOW = 1790000000.25


def _commands(event: str, **payload) -> list[list[str]]:
    return H.tmux_commands({"hook_event_name": event, **payload}, PANE, NOW)


def _state_set(commands) -> str | None:
    for command in commands:
        if command[:5] == ["set-option", "-p", "-t", PANE, AGENT_STATE_OPTION]:
            return command[5]
    return None


class TestOptionNames:
    def test_the_script_and_the_monitor_agree(self):
        assert H.STATE_OPTION == AGENT_STATE_OPTION
        assert H.WAITER_OPTION == AGENT_WAITER_OPTION
        assert H.TRANSCRIPT_OPTION == AGENT_TRANSCRIPT_OPTION
        assert H.APPROVAL == AGENT_APPROVAL
        assert H.HELPERS_OPTION == AGENT_HELPERS_OPTION

    def test_every_listened_event_means_something(self):
        for event in CLAUDE_STATUS_EVENTS:
            # SubagentStop only speaks for a subagent (it carries an agent_id).
            assert event in ("SessionEnd", "SubagentStop") or _commands(event), event


class TestMainThread:
    @pytest.mark.parametrize(
        ("event", "status"),
        [
            ("SessionStart", "idle"),
            ("UserPromptSubmit", "running"),
            ("PreToolUse", "running"),
            ("PostToolUse", "running"),
            ("PostToolUseFailure", "running"),
            ("PostToolBatch", "running"),
            ("PermissionDenied", "running"),
            ("Elicitation", "waiting"),
            ("ElicitationResult", "running"),
            ("PreCompact", "running"),
            ("Stop", "idle"),
            ("StopFailure", "idle"),
        ],
    )
    def test_event_maps_to_a_stamped_status(self, event, status):
        assert _state_set(_commands(event)) == f"{status} {NOW:.3f}"

    def test_a_tool_permission_request_waits_for_approval(self):
        assert _state_set(_commands("PermissionRequest", tool_name="Bash")) == (
            f"waiting {NOW:.3f} {AGENT_APPROVAL}"
        )

    @pytest.mark.parametrize("tool", ["AskUserQuestion", "ExitPlanMode"])
    def test_a_question_waits_without_approval(self, tool):
        # Its answer fires PostToolUse, so nothing needs inferring.
        assert _state_set(_commands("PreToolUse", tool_name=tool)).startswith("running")
        assert _state_set(_commands("PermissionRequest", tool_name=tool)) == f"waiting {NOW:.3f}"

    def test_a_manual_compact_ends_idle_and_an_automatic_one_keeps_running(self):
        assert _state_set(_commands("PostCompact", trigger="manual")).startswith("idle")
        assert _state_set(_commands("PostCompact", trigger="auto")).startswith("running")

    def test_a_new_prompt_clears_a_subagent_waiter(self):
        unset = ["set-option", "-p", "-u", "-t", PANE, AGENT_WAITER_OPTION]
        assert unset in _commands("UserPromptSubmit")
        assert unset in _commands("SessionStart")
        assert unset not in _commands("PostToolUse")

    def test_the_transcript_is_recorded(self):
        commands = _commands("SessionStart", transcript_path="/t/s.jsonl")
        assert ["set-option", "-p", "-t", PANE, AGENT_TRANSCRIPT_OPTION, "/t/s.jsonl"] in commands

    def test_unknown_events_do_nothing(self):
        assert _commands("Notification", notification_type="agent_completed") == []
        assert _commands("SubagentStop") == []
        assert _commands("MessageDisplay") == []

    def test_session_end_clears_everything(self):
        commands = _commands("SessionEnd")
        for option in (AGENT_STATE_OPTION, AGENT_WAITER_OPTION, AGENT_TRANSCRIPT_OPTION):
            assert ["set-option", "-p", "-u", "-t", PANE, option] in commands


class TestSubagents:
    def test_tool_use_after_the_turn_says_nothing(self):
        # A background subagent keeps working after the main turn stopped.
        for event in ("PreToolUse", "PostToolBatch", "Stop", "UserPromptSubmit"):
            assert _commands(event, agent_id="a4b6192679a9fea0e") == []
        # Its tool results can only clear a waiter it set itself.
        for command in _commands("PostToolUse", agent_id="a4b6192679a9fea0e"):
            assert command[0] == "if-shell"

    def test_a_permission_request_marks_the_waiter(self):
        commands = _commands("PermissionRequest", agent_id="a4b6", tool_name="Bash")
        assert commands == [
            [
                "set-option",
                "-p",
                "-t",
                PANE,
                AGENT_WAITER_OPTION,
                f"a4b6 {NOW:.3f} {AGENT_APPROVAL}",
            ]
        ]

    def test_only_the_waiter_clears_itself(self):
        (command,) = _commands("PostToolUse", agent_id="a4b6")
        assert command[:4] == ["if-shell", "-F", "-t", PANE]
        assert command[4] == f"#{{m:a4b6 *,#{{{AGENT_WAITER_OPTION}}}}}"
        assert command[5] == f"set-option -p -u -t {PANE} {AGENT_WAITER_OPTION}"

    def test_a_working_subagent_is_listed_once(self):
        (command,) = _commands("PreToolUse", agent_id="a4b6", agent_type="general-purpose")
        assert command[:4] == ["if-shell", "-F", "-t", PANE]
        assert command[4] == f"#{{m:* a4b6 *, #{{{AGENT_HELPERS_OPTION}}} }}"
        assert command[5:] == ["", f"set-option -p -a -t {PANE} {AGENT_HELPERS_OPTION} ' a4b6'"]

    def test_claude_code_own_helper_is_never_listed(self):
        # It has no agent_type, and only ever sends a SubagentStop.
        assert _commands("PreToolUse", agent_id="a4b6", agent_type="") == []

    def test_a_stopped_subagent_leaves_the_list_and_stops_asking(self):
        remove, clear_waiter = _commands("SubagentStop", agent_id="a4b6", agent_type="x")
        assert remove == [
            "set-option",
            "-p",
            "-F",
            "-t",
            PANE,
            AGENT_HELPERS_OPTION,
            f"#{{s/ a4b6//:{AGENT_HELPERS_OPTION}}}",
        ]
        assert clear_waiter[:5] == [
            "if-shell",
            "-F",
            "-t",
            PANE,
            "#{m:a4b6 *,#{@gitdirector_agent_waiter}}",
        ]

    def test_a_hand_back_leaves_the_list_too(self):
        # Seen live: a SubagentStop hook that never took effect left a
        # finished subagent listed. The hand-back is its last tool call.
        remove, clear_waiter = _commands(
            "PostToolUse", agent_id="a4b6", agent_type="x", tool_name="SubagentHandback"
        )
        assert remove[:4] == ["set-option", "-p", "-F", "-t"]
        assert remove[5:] == [AGENT_HELPERS_OPTION, f"#{{s/ a4b6//:{AGENT_HELPERS_OPTION}}}"]
        assert clear_waiter[0] == "if-shell"
        (clear_waiter,) = _commands(
            "PostToolUse", agent_id="a4b6", agent_type="x", tool_name="Bash"
        )
        assert clear_waiter[0] == "if-shell"

    def test_a_new_session_forgets_subagents_but_a_new_prompt_does_not(self):
        unset = ["set-option", "-p", "-u", "-t", PANE, AGENT_HELPERS_OPTION]
        assert unset in _commands("SessionStart")
        assert unset in _commands("SessionEnd")
        # Background subagents keep working while the user prompts again.
        assert unset not in _commands("UserPromptSubmit")

    def test_odd_agent_ids_are_ignored(self):
        assert _commands("PermissionRequest", agent_id="a b;kill") == []


class TestScript:
    def test_prints_nothing_and_succeeds_on_garbage(self):
        # SessionStart and UserPromptSubmit output would reach Claude's context.
        for stdin in ("not json", "[]", "", '{"hook_event_name": "Stop"}'):
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(claude_status_hook_path())],
                input=stdin,
                capture_output=True,
                text=True,
                env={"PATH": "/usr/bin:/bin"},
                timeout=TMUX_CMD_TIMEOUT,
            )
            assert result.returncode == 0
            assert result.stdout == "" and result.stderr == ""


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
class TestScriptAgainstTmux:
    def test_events_land_on_the_pane(self, monkeypatch):
        with _tmux_integration_lock():
            tmux_dir = _make_short_tmux_tmpdir()
            env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
            env["TMUX_TMPDIR"] = str(tmux_dir)

            def tmux(*args: str) -> str:
                return subprocess.run(
                    ["tmux", *args],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=TMUX_CMD_TIMEOUT,
                ).stdout.strip()

            def hook(**payload) -> None:
                subprocess.run(
                    [sys.executable, "-I", "-S", str(claude_status_hook_path())],
                    input=json.dumps(payload),
                    text=True,
                    env={**env, "TMUX_PANE": pane},
                    timeout=TMUX_CMD_TIMEOUT,
                    check=True,
                )

            def option(name: str) -> str:
                return tmux("display-message", "-p", "-t", pane, f"#{{{name}}}")

            try:
                tmux("-f", "/dev/null", "new-session", "-d", "-s", "probe", "sleep 600")
                pane = tmux("display-message", "-p", "-t", "probe", "#{pane_id}")

                hook(hook_event_name="SessionStart", session_id="s", transcript_path="/t.jsonl")
                assert option(AGENT_STATE_OPTION).startswith("idle ")
                assert option(AGENT_TRANSCRIPT_OPTION) == "/t.jsonl"

                hook(hook_event_name="UserPromptSubmit", session_id="s")
                hook(hook_event_name="Stop", session_id="s")
                hook(hook_event_name="PermissionRequest", session_id="s", agent_id="ab12")
                assert option(AGENT_STATE_OPTION).startswith("idle ")
                assert option(AGENT_WAITER_OPTION).startswith("ab12 ")

                hook(hook_event_name="PostToolUse", session_id="s", agent_id="zz99")
                assert option(AGENT_WAITER_OPTION).startswith("ab12 ")
                hook(hook_event_name="PostToolUse", session_id="s", agent_id="ab12")
                assert option(AGENT_WAITER_OPTION) == ""

                sub = {"session_id": "s", "agent_type": "general-purpose"}
                hook(hook_event_name="PreToolUse", agent_id="a1", **sub)
                hook(hook_event_name="PreToolUse", agent_id="b2", **sub)
                hook(hook_event_name="PreToolUse", agent_id="a1", **sub)
                assert option(AGENT_HELPERS_OPTION).split() == ["a1", "b2"]
                hook(hook_event_name="SubagentStop", agent_id="zz", session_id="s", agent_type="")
                hook(hook_event_name="SubagentStop", agent_id="a1", **sub)
                assert option(AGENT_HELPERS_OPTION).split() == ["b2"]
                hook(hook_event_name="PostToolUse", agent_id="b2", tool_name="Bash", **sub)
                assert option(AGENT_HELPERS_OPTION).split() == ["b2"]
                hook(
                    hook_event_name="PostToolUse",
                    agent_id="b2",
                    tool_name="SubagentHandback",
                    **sub,
                )
                assert option(AGENT_HELPERS_OPTION) == ""

                hook(hook_event_name="SessionEnd", session_id="s")
                assert option(AGENT_STATE_OPTION) == ""
                assert option(AGENT_TRANSCRIPT_OPTION) == ""
                assert option(AGENT_HELPERS_OPTION) == ""
            finally:
                tmux("kill-server")
                _cleanup_tmux_tmpdir(tmux_dir)
