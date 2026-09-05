"""The AI coding agents GitDirector knows how to launch and monitor.

Every place that lists agents (the TUI launch menu, session-status
detection, ``gitdirector doctor``) reads this table, so adding an agent is
a single entry here.

Agents that expose lifecycle hooks report their own status through tmux
(see :data:`AGENT_STATE_OPTION`), which the session monitor trusts over its
heuristics. Claude Code and OpenCode do this today; the hooks are injected
on the launch command line, so nothing in the user's own settings is
touched.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

#: tmux session option an agent's hooks stamp with its current status. The
#: value is one of :data:`AGENT_STATES`; unset means "no report, use the
#: heuristics".
AGENT_STATE_OPTION = "@gitdirector_agent_state"
#: Set to :data:`AGENT_INTERRUPTS_UNREPORTED` by agents whose hooks stay
#: silent when the user interrupts a turn; the monitor then watches for a
#: "running" report the screen no longer backs up.
AGENT_INTERRUPTS_OPTION = "@gitdirector_agent_interrupts"
AGENT_INTERRUPTS_UNREPORTED = "unreported"

AGENT_STATE_WAITING = "waiting"
AGENT_STATE_RUNNING = "running"
AGENT_STATE_IDLE = "idle"
AGENT_STATES: frozenset[str] = frozenset(
    {AGENT_STATE_WAITING, AGENT_STATE_RUNNING, AGENT_STATE_IDLE}
)


def agent_state_report_command(state: str | None, *, interrupts_unreported: bool = False) -> str:
    """POSIX shell that stamps *state* on the tmux session the agent runs in.

    ``None`` clears the options. The command never fails (an agent must not
    be blocked by a reporting hiccup) and only acts inside a tmux pane.
    """
    if state is None:
        actions = [
            f'tmux set-option -u -t "$TMUX_PANE" {AGENT_STATE_OPTION}',
            f'tmux set-option -u -t "$TMUX_PANE" {AGENT_INTERRUPTS_OPTION}',
        ]
    else:
        if state not in AGENT_STATES:
            raise ValueError(f"unknown agent state: {state!r}")
        actions = [f'tmux set-option -t "$TMUX_PANE" {AGENT_STATE_OPTION} {state}']
        if interrupts_unreported:
            actions.append(
                f'tmux set-option -t "$TMUX_PANE" {AGENT_INTERRUPTS_OPTION} '
                f"{AGENT_INTERRUPTS_UNREPORTED}"
            )
    body = "; ".join(f"{action} >/dev/null 2>&1" for action in actions)
    return f'[ -n "$TMUX_PANE" ] && {{ {body}; }}; exit 0'


def _claude_hook_settings() -> dict:
    """Claude Code ``--settings`` payload mapping lifecycle hooks to states.

    * ``UserPromptSubmit``, ``PreToolUse``, ``PostToolUse``: working.
    * ``PreToolUse`` for ``AskUserQuestion``, ``PermissionRequest``, and
      ``Notification`` (except the periodic ``idle_prompt`` reminder, which
      would turn a finished session back into "waiting"): waiting for the
      user.
    * ``SessionStart``, ``Stop`` (turn finished, prompt is back), and
      ``PermissionDenied`` (the user dismissed or refused a prompt; if Claude
      carries on, the next event flips it back): idle.
    * ``SessionEnd``: report cleared.

    ``Stop`` does not fire when the user interrupts a turn with Escape; the
    monitor covers that case by noticing a "running" session that has gone
    completely quiet (see ``_AGENT_REPORT_STALE_SECS`` in the monitor).

    Hooks read their JSON payload from stdin; the shell fragments below only
    look for the substrings they need so they stay independent of key order
    and spacing.
    """
    running = agent_state_report_command(AGENT_STATE_RUNNING)
    waiting = agent_state_report_command(AGENT_STATE_WAITING)
    idle = agent_state_report_command(AGENT_STATE_IDLE)
    started = agent_state_report_command(AGENT_STATE_IDLE, interrupts_unreported=True)
    cleared = agent_state_report_command(None)
    ask_user_or_running = (
        'input=$(cat); case "$input" in *AskUserQuestion*) '
        + agent_state_report_command(AGENT_STATE_WAITING).replace("; exit 0", ";;")
        + " *) "
        + agent_state_report_command(AGENT_STATE_RUNNING).replace("; exit 0", ";;")
        + " esac; exit 0"
    )
    notification = (
        'input=$(cat); case "$input" in *idle_prompt*) ;; *) '
        + waiting.replace("; exit 0", ";;")
        + " esac; exit 0"
    )

    def hook(command: str) -> list[dict]:
        return [{"hooks": [{"type": "command", "command": command}]}]

    return {
        "hooks": {
            "SessionStart": hook(started),
            "UserPromptSubmit": hook(running),
            "PreToolUse": hook(ask_user_or_running),
            "PostToolUse": hook(running),
            "PostToolUseFailure": hook(running),
            "PermissionRequest": hook(waiting),
            "PermissionDenied": hook(idle),
            "Notification": hook(notification),
            "Stop": hook(idle),
            "SessionEnd": hook(cleared),
        }
    }


def claude_launch_command(command: str) -> str:
    """*command* plus Claude Code's ``--settings`` carrying the status hooks."""
    settings = json.dumps(_claude_hook_settings(), separators=(",", ":"))
    return f"{command} --settings {shlex.quote(settings)}"


def opencode_status_plugin_path() -> Path:
    """The OpenCode plugin shipped with GitDirector that reports status."""
    return Path(str(resources.files("gitdirector.integrations").joinpath("opencode_status.js")))


def opencode_launch_command(command: str) -> str:
    """*command* with the status plugin injected through ``OPENCODE_CONFIG_CONTENT``.

    OpenCode merges that JSON with the user's own configuration, so only the
    plugin list is added. OpenCode reports interrupted turns itself, so no
    interrupt flag is needed.
    """
    config = json.dumps({"plugin": [opencode_status_plugin_path().as_uri()]}, separators=(",", ":"))
    return f"OPENCODE_CONFIG_CONTENT={shlex.quote(config)} {command}"


@dataclass(frozen=True)
class AgentSpec:
    #: Identifier used in TUI menu actions (``agent:<key>``).
    key: str
    #: Human-readable product name.
    label: str
    #: Shell command run inside the tmux session.
    command: str
    #: Purpose segment of the session name (``gd/<repo>/<purpose>/<N>``).
    purpose: str
    #: Process name the running agent shows up as, for idle detection.
    process: str
    #: Executable names ``doctor`` looks for on ``PATH``.
    executables: tuple[str, ...]
    #: Extra text shown after the label in the launch menu.
    menu_note: str | None = None
    #: Wraps :attr:`command` so the agent reports its status through tmux.
    status_launcher: Callable[[str], str] | None = None

    @property
    def reports_status(self) -> bool:
        return self.status_launcher is not None

    @property
    def launch_command(self) -> str:
        """The command GitDirector runs in the session, status hooks included."""
        if self.status_launcher is None:
            return self.command
        return self.status_launcher(self.command)


_CLAUDE_EXECUTABLES = ("claude", "claude-code")

AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec("pi", "Pi", "pi", "pi", "pi", ("pi",)),
    AgentSpec(
        "opencode",
        "OpenCode",
        "opencode",
        "opencode",
        "opencode",
        ("opencode",),
        status_launcher=opencode_launch_command,
    ),
    AgentSpec(
        "claude",
        "Claude Code",
        "claude",
        "claude",
        "claude",
        _CLAUDE_EXECUTABLES,
        status_launcher=claude_launch_command,
    ),
    AgentSpec(
        "claude-skip-permissions",
        "Claude Code",
        "claude --dangerously-skip-permissions",
        # The launch flags would otherwise be sanitized into an unreadable
        # session label, so this variant carries its own purpose.
        "claude-dangerously-skip-permissions",
        "claude",
        _CLAUDE_EXECUTABLES,
        menu_note="--dangerously-skip-permissions",
        status_launcher=claude_launch_command,
    ),
    AgentSpec(
        "copilot",
        "GitHub Copilot",
        "copilot",
        "copilot",
        "copilot",
        ("copilot", "github-copilot-cli"),
    ),
    AgentSpec("codex", "Codex", "codex", "codex", "codex", ("codex",)),
)

AGENTS_BY_KEY: dict[str, AgentSpec] = {agent.key: agent for agent in AGENTS}

#: Session purpose -> process name the agent runs as.
AGENT_PURPOSE_PROCESSES: dict[str, str] = {agent.purpose: agent.process for agent in AGENTS}

AGENT_PURPOSES: frozenset[str] = frozenset(AGENT_PURPOSE_PROCESSES)

AGENT_PURPOSE_CLAUDE_SKIP_PERMISSIONS = AGENTS_BY_KEY["claude-skip-permissions"].purpose


def agent_tools() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """``(label, executables)`` pairs with one entry per distinct product."""
    tools: dict[str, tuple[str, ...]] = {}
    for agent in AGENTS:
        tools.setdefault(agent.label, agent.executables)
    return tuple(tools.items())


__all__ = [
    "AGENTS",
    "AGENTS_BY_KEY",
    "AGENT_PURPOSES",
    "AGENT_PURPOSE_CLAUDE_SKIP_PERMISSIONS",
    "AGENT_PURPOSE_PROCESSES",
    "AGENT_INTERRUPTS_OPTION",
    "AGENT_INTERRUPTS_UNREPORTED",
    "AGENT_STATES",
    "AGENT_STATE_IDLE",
    "AGENT_STATE_OPTION",
    "AGENT_STATE_RUNNING",
    "AGENT_STATE_WAITING",
    "AgentSpec",
    "agent_state_report_command",
    "agent_tools",
    "claude_launch_command",
    "opencode_launch_command",
    "opencode_status_plugin_path",
]
