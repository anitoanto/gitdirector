"""Claude Code hook that reports the session's status on its tmux pane.

GitDirector hands this script to the Claude Code it launches, inline through
``--settings``, for every event in ``agents.CLAUDE_STATUS_EVENTS``; nothing is
written to the user's settings or to disk. It runs as ``python -I -S``, so it
uses the standard library only. It prints nothing (``SessionStart`` and
``UserPromptSubmit`` output would be added to Claude's context) and always
exits 0, so a reporting problem can never disturb the agent.

Everything lives in pane options, which go away with the pane:

* ``@gitdirector_agent_state``: the main thread's ``<status> <epoch>``.
* ``@gitdirector_agent_waiter``: ``<agent id> <epoch>`` of the subagent
  blocked on the user, if any. Subagent events run alongside the main
  thread, often after its turn is over, so this is all they report.
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
    "PreCompact": _RUNNING,
    "Stop": _IDLE,
    "StopFailure": _IDLE,
}
_ASKS = frozenset({"PermissionRequest", "Elicitation"})
_ANSWERS = frozenset({"PostToolUse", "PostToolUseFailure", "PermissionDenied", "ElicitationResult"})
# A new prompt means nothing is left waiting on the user.
_FRESH = frozenset({"SessionStart", "UserPromptSubmit"})


def main_status(event: str, payload: dict) -> str | None:
    """The main thread's status after *event*, or ``None`` when it says nothing."""
    if event == "PostCompact":
        # A manual /compact is not a turn; an automatic one happens inside one.
        return _IDLE if payload.get("trigger") == "manual" else _RUNNING
    return _MAIN_STATUS.get(event)


def tmux_commands(payload: dict, pane: str, now: float) -> list[list[str]]:
    """The tmux commands that record *payload*'s event on *pane*."""
    event = payload.get("hook_event_name") or ""
    if event == "SessionEnd":
        return [
            ["set-option", "-p", "-u", "-t", pane, option]
            for option in (STATE_OPTION, WAITER_OPTION, TRANSCRIPT_OPTION)
        ]
    agent = payload.get("agent_id")
    if agent:
        agent = str(agent)
        if not agent.replace("-", "").isalnum():
            return []
        if event in _ASKS:
            return [["set-option", "-p", "-t", pane, WAITER_OPTION, f"{agent} {now:.3f}"]]
        if event in _ANSWERS:
            # Only the subagent that asked can clear it, decided inside tmux.
            waiter = f"#{{m:{agent} *,#{{{WAITER_OPTION}}}}}"
            clear = f"set-option -p -u -t {pane} {WAITER_OPTION}"
            return [["if-shell", "-F", "-t", pane, waiter, clear]]
        return []
    status = main_status(event, payload)
    if status is None:
        return []
    commands = [["set-option", "-p", "-t", pane, STATE_OPTION, f"{status} {now:.3f}"]]
    if event in _FRESH:
        commands.append(["set-option", "-p", "-u", "-t", pane, WAITER_OPTION])
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
