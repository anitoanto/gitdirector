"""Claude Code hook that reports the session's status on its tmux pane.

GitDirector hands this script to the Claude Code it launches, inline through
``--settings``, for every event in ``agents.CLAUDE_STATUS_EVENTS``; nothing is
written to the user's settings or to disk. It runs as ``python -I -S``, so it
uses the standard library only. It prints nothing (``SessionStart`` and
``UserPromptSubmit`` output would be added to Claude's context) and always
exits 0, so a reporting problem can never disturb the agent.

Everything lives in pane options, which go away with the pane:

* ``@gitdirector_agent_state``: the main thread's ``<status> <epoch>``, with
  a trailing ``approval`` while a tool waits for permission.
* ``@gitdirector_agent_waiter``: ``<agent id> <epoch>`` of the subagent
  blocked on the user, if any, with the same ``approval`` suffix. Subagent
  events run alongside the main thread, often after its turn is over, so
  this is all they report.

Approving a tool fires no hook until the tool finishes, so the monitor
infers it from the tool's process; ``approval`` tells it to. Answering a
question (``AskUserQuestion``, ``ExitPlanMode``, an MCP elicitation) fires
``PostToolUse`` or ``ElicitationResult`` at once and needs no guessing.
* ``@gitdirector_agent_helpers``: `` <id> <id>`` of the subagents still
  working, so a main thread at its prompt still reads as running. A
  subagent is listed from its ``SubagentStart`` (or, on versions without
  it, its first tool call) until its hand-back or its ``SubagentStop``;
  Claude Code's own helper stops without ever starting.
* ``@gitdirector_agent_shells``: `` <id> <id>`` of the background shells
  started, likewise counted as running. A shell is listed when its Bash call
  returns its ``backgroundTaskId``; no hook marks its end.

Every ``Stop`` carries ``background_tasks``, the work still in flight, and
rewrites both lists from it. Hooks are best-effort, so the monitor checks
the subagents' transcripts and the shells' processes before it trusts them.
* ``@gitdirector_agent_transcript``: the transcript, where the monitor sees
  an interrupt, the one transition no hook reports.

Each event is a single tmux call, so hooks that fire together cannot
interleave a read and a write.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

STATE_OPTION = "@gitdirector_agent_state"
WAITER_OPTION = "@gitdirector_agent_waiter"
HELPERS_OPTION = "@gitdirector_agent_helpers"
SHELLS_OPTION = "@gitdirector_agent_shells"
TRANSCRIPT_OPTION = "@gitdirector_agent_transcript"

_RUNNING = "running"
_WAITING = "waiting"
_IDLE = "idle"

_MAIN_STATUS = {
    "SessionStart": _IDLE,
    "UserPromptSubmit": _RUNNING,
    "PreToolUse": _RUNNING,
    "PostToolUse": _RUNNING,
    "PostToolUseFailure": _RUNNING,
    "PostToolBatch": _RUNNING,
    # An auto-mode denial: Claude carries on with the turn.
    "PermissionDenied": _RUNNING,
    "PermissionRequest": _WAITING,
    "Elicitation": _WAITING,
    "ElicitationResult": _RUNNING,
    "Stop": _IDLE,
    "StopFailure": _IDLE,
}
_ASKS = frozenset({"PermissionRequest", "Elicitation"})
_ANSWERS = frozenset({"PostToolUse", "PostToolUseFailure", "PermissionDenied", "ElicitationResult"})
# A new prompt means nothing is left waiting on the user.
_FRESH = frozenset({"SessionStart", "UserPromptSubmit"})
# Tools whose permission prompt is a question to the user, not an approval.
_QUESTION_TOOLS = frozenset({"AskUserQuestion", "ExitPlanMode"})
# A subagent's final tool call: it delivers the report and then stops.
_HANDBACK_TOOL = "SubagentHandback"
# ``background_tasks`` types: everything else in flight is not counted.
_SUBAGENT_TASK = "subagent"
_SHELL_TASK = "shell"
APPROVAL = "approval"


def _stamp(payload: dict, event: str, now: float) -> str:
    """``<epoch>``, plus ``approval`` when *event* asks to approve a tool."""
    stamp = f"{now:.3f}"
    if event == "PermissionRequest" and payload.get("tool_name") not in _QUESTION_TOOLS:
        stamp += f" {APPROVAL}"
    return stamp


def main_status(event: str, payload: dict) -> str | None:
    """The main thread's status after *event*, or ``None`` when it says nothing."""
    if event in ("PreCompact", "PostCompact"):
        # Only /compact is the user's to wait on: an automatic compaction runs
        # inside a turn or, for idle compaction, at the prompt.
        if payload.get("trigger") != "manual":
            return None
        return _RUNNING if event == "PreCompact" else _IDLE
    if event == "SessionStart" and payload.get("source") == "compact":
        return None
    return _MAIN_STATUS.get(event)


def _is_id(value: str) -> bool:
    return bool(value) and value.replace("-", "").isalnum()


def _add_to_list(option: str, item: str, pane: str) -> list[str]:
    """Append *item* to the `` <id> <id>`` list in *option* unless it is listed."""
    listed = f"#{{m:* {item} *, #{{{option}}} }}"
    add = f"set-option -p -a -t {pane} {option} ' {item}'"
    return ["if-shell", "-F", "-t", pane, listed, "", add]


def _set_list(option: str, items: list[str], pane: str) -> list[str]:
    if not items:
        return ["set-option", "-p", "-u", "-t", pane, option]
    return ["set-option", "-p", "-t", pane, option, "".join(f" {item}" for item in items)]


def _shell_commands(event: str, payload: dict, pane: str) -> list[list[str]]:
    """List a shell the moment a Bash call (main thread or subagent) backgrounds it."""
    if event != "PostToolUse" or payload.get("tool_name") != "Bash":
        return []
    response = payload.get("tool_response")
    shell = response.get("backgroundTaskId") if isinstance(response, dict) else None
    if not isinstance(shell, str) or not _is_id(shell):
        return []
    return [_add_to_list(SHELLS_OPTION, shell, pane)]


def _background_tasks_commands(payload: dict, pane: str) -> list[list[str]]:
    """Rewrite the subagent and shell lists from a ``Stop``'s ``background_tasks``.

    Only the main thread's ``Stop`` is trusted with this: no subagent can be
    starting then, while a ``SubagentStop`` lists the subagent that is stopping.
    """
    tasks = payload.get("background_tasks")
    if not isinstance(tasks, list):
        return []
    found: dict[str, list[str]] = {_SUBAGENT_TASK: [], _SHELL_TASK: []}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        ids = found.get(task.get("type"))
        task_id = task.get("id")
        if ids is not None and isinstance(task_id, str) and _is_id(task_id):
            ids.append(task_id)
    return [
        _set_list(HELPERS_OPTION, found[_SUBAGENT_TASK], pane),
        _set_list(SHELLS_OPTION, found[_SHELL_TASK], pane),
    ]


def _helper_commands(event: str, payload: dict, agent: str, pane: str) -> list[list[str]]:
    """Keep *agent* in the helpers list while it works, decided inside tmux."""
    remove = [
        "set-option",
        "-p",
        "-F",
        "-t",
        pane,
        HELPERS_OPTION,
        f"#{{s/ {agent}//:{HELPERS_OPTION}}}",
    ]
    if event == "SubagentStop":
        waiter = f"#{{m:{agent} *,#{{{WAITER_OPTION}}}}}"
        return [
            remove,
            # A subagent stopped while it asked is no longer asking.
            ["if-shell", "-F", "-t", pane, waiter, f"set-option -p -u -t {pane} {WAITER_OPTION}"],
        ]
    # The hand-back is a subagent's last tool call: its result is in, whether
    # or not the SubagentStop that follows gets through.
    if event == "PostToolUse" and payload.get("tool_name") == _HANDBACK_TOOL:
        return [remove]
    # PreToolUse only, for versions without SubagentStart: Claude waits for
    # it, so it lands before the SubagentStop.
    if event == "SubagentStart" or (event == "PreToolUse" and payload.get("agent_type")):
        return [_add_to_list(HELPERS_OPTION, agent, pane)]
    return []


def tmux_commands(payload: dict, pane: str, now: float) -> list[list[str]]:
    """The tmux commands that record *payload*'s event on *pane*."""
    event = payload.get("hook_event_name") or ""
    if event == "SessionEnd":
        return [
            ["set-option", "-p", "-u", "-t", pane, option]
            for option in (
                STATE_OPTION,
                WAITER_OPTION,
                HELPERS_OPTION,
                SHELLS_OPTION,
                TRANSCRIPT_OPTION,
            )
        ]
    agent = payload.get("agent_id")
    if agent:
        agent = str(agent)
        if not _is_id(agent):
            return []
        commands = _helper_commands(event, payload, agent, pane)
        commands += _shell_commands(event, payload, pane)
        if event in _ASKS:
            waiter = f"{agent} {_stamp(payload, event, now)}"
            commands.append(["set-option", "-p", "-t", pane, WAITER_OPTION, waiter])
        elif event in _ANSWERS:
            # Only the subagent that asked can clear it, decided inside tmux.
            waiter = f"#{{m:{agent} *,#{{{WAITER_OPTION}}}}}"
            clear = f"set-option -p -u -t {pane} {WAITER_OPTION}"
            commands.append(["if-shell", "-F", "-t", pane, waiter, clear])
        return commands
    status = main_status(event, payload)
    if status is None:
        return []
    state = f"{status} {_stamp(payload, event, now)}"
    commands = [["set-option", "-p", "-t", pane, STATE_OPTION, state]]
    commands += _shell_commands(event, payload, pane)
    if event in _FRESH:
        commands.append(["set-option", "-p", "-u", "-t", pane, WAITER_OPTION])
    if event == "SessionStart":
        commands.append(["set-option", "-p", "-u", "-t", pane, HELPERS_OPTION])
        commands.append(["set-option", "-p", "-u", "-t", pane, SHELLS_OPTION])
    if event == "Stop":
        commands += _background_tasks_commands(payload, pane)
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str) and transcript:
        commands.append(["set-option", "-p", "-t", pane, TRANSCRIPT_OPTION, transcript])
    return commands


def run(commands: list[list[str]]) -> None:
    if not commands:
        return
    argv = ["tmux"]
    for index, command in enumerate(commands):
        if index:
            argv.append(";")
        argv.extend(command)
    subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=3,
        check=False,
    )


def main() -> None:
    try:
        pane = os.environ.get("TMUX_PANE")
        payload = json.loads(sys.stdin.read() or "{}")
        if pane and isinstance(payload, dict):
            run(tmux_commands(payload, pane, time.time()))
    except Exception:
        pass


if __name__ == "__main__":
    main()
