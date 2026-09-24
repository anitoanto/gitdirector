"""The AI coding agents GitDirector knows how to launch and monitor.

Every place that lists agents (the TUI launch menu, session-status
detection, ``gitdirector doctor``) reads this table, so adding an agent is
a single entry here.

Agents that expose lifecycle hooks report their own status through tmux
(see :data:`AGENT_STATE_OPTION`), and the session monitor trusts it instead
of watching the pane. Claude Code and OpenCode do this today. The hooks are
passed inline on the launch command line, so nothing is written to the
user's configuration or anywhere else.
"""

from __future__ import annotations

import json
import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

#: tmux pane option an agent's hooks stamp with its current status. Pane
#: scoped because a session shown in a panel is also reachable through a
#: second, grouped session, and a session option set through ``$TMUX_PANE``
#: lands on whichever of the two tmux picks. The value is one of
#: :data:`AGENT_STATES`, optionally followed by a space and the epoch of the
#: report (``"running 1788714352.120"``); unset means "no report".
AGENT_STATE_OPTION = "@gitdirector_agent_state"
#: ``<agent id> <epoch>`` of a Claude Code subagent blocked on the user.
AGENT_WAITER_OPTION = "@gitdirector_agent_waiter"
#: Claude Code's transcript, where an interrupt (which fires no hook) shows.
AGENT_TRANSCRIPT_OPTION = "@gitdirector_agent_transcript"

AGENT_STATE_WAITING = "waiting"
AGENT_STATE_RUNNING = "running"
AGENT_STATE_IDLE = "idle"
AGENT_STATES: frozenset[str] = frozenset(
    {AGENT_STATE_WAITING, AGENT_STATE_RUNNING, AGENT_STATE_IDLE}
)

#: Claude Code events the status hook listens to (see ``claude_status.py``).
#: ``Notification`` is left out: most of its types (``agent_completed``,
#: ``elicitation_response``, ``auth_success``...) are not about the user
#: being needed, and ``PermissionRequest`` already reports the ones that are.
#: ``SubagentStop`` fires for Claude's own helpers after a turn has ended.
CLAUDE_STATUS_EVENTS: tuple[str, ...] = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PermissionDenied",
    "PostToolUse",
    "PostToolUseFailure",
    "PostToolBatch",
    "Elicitation",
    "ElicitationResult",
    "PreCompact",
    "PostCompact",
    "Stop",
    "StopFailure",
    "SessionEnd",
)
# Hooks run synchronously; a hung tmux must never hold Claude up for long.
_CLAUDE_HOOK_TIMEOUT_SECS = 5


def claude_status_hook_path() -> Path:
    """The Claude Code hook script shipped with GitDirector."""
    return Path(str(resources.files("gitdirector.integrations").joinpath("claude_status.py")))


def _claude_hook_settings() -> dict:
    """Claude Code ``--settings`` payload running the status hook on every event."""
    command = shlex.join([sys.executable, "-I", "-S", str(claude_status_hook_path())])
    hook = [
        {"hooks": [{"type": "command", "command": command, "timeout": _CLAUDE_HOOK_TIMEOUT_SECS}]}
    ]
    return {"hooks": {event: hook for event in CLAUDE_STATUS_EVENTS}}


def claude_launch_command(command: str) -> str:
    """*command* plus Claude Code's inline ``--settings`` carrying the status hooks."""
    settings = json.dumps(_claude_hook_settings(), separators=(",", ":"))
    return f"{command} --settings {shlex.quote(settings)}"


def opencode_status_plugin_path() -> Path:
    """The OpenCode plugin shipped with GitDirector that reports status."""
    return Path(str(resources.files("gitdirector.integrations").joinpath("opencode_status.js")))


def opencode_launch_command(command: str) -> str:
    """*command* with the status plugin injected through ``OPENCODE_CONFIG_CONTENT``.

    OpenCode merges that JSON with the user's own configuration in memory,
    for this process only, so only the plugin list is added and nothing is
    written to disk.
    """
    config = json.dumps({"plugin": [opencode_status_plugin_path().as_uri()]}, separators=(",", ":"))
    return f"OPENCODE_CONFIG_CONTENT={shlex.quote(config)} {command}"


@dataclass(frozen=True)
class AgentMode:
    """A launch variant picked inline in the launch menu (Claude Code's permission modes)."""

    key: str
    label: str
    #: Purpose segment of sessions launched in this mode.
    purpose: str
    #: Appended to the agent's command.
    flags: str = ""
    #: Rendered as a warning in the menu.
    dangerous: bool = False


@dataclass(frozen=True)
class AgentSpec:
    #: Identifier used in TUI menu actions (``agent:<key>[:<mode>]``).
    key: str
    #: Human-readable product name.
    label: str
    #: Shell command run inside the tmux session.
    command: str
    #: Purpose segment of the session name (``gd/<repo>/<purpose>/<N>``).
    purpose: str
    #: Executable names ``doctor`` looks for on ``PATH``.
    executables: tuple[str, ...]
    #: Wraps :attr:`command` so the agent reports its status through tmux.
    status_launcher: Callable[[str], str] | None = None
    modes: tuple[AgentMode, ...] = ()
    #: Mode preselected in the launch menu.
    default_mode: str | None = None

    @property
    def reports_status(self) -> bool:
        return self.status_launcher is not None

    def mode(self, key: str | None) -> AgentMode | None:
        """The mode called *key*, else the default one; ``None`` without modes."""
        by_key = {mode.key: mode for mode in self.modes}
        return by_key.get(key or "") or by_key.get(self.default_mode or "")

    def purpose_for(self, mode_key: str | None = None) -> str:
        mode = self.mode(mode_key)
        return mode.purpose if mode is not None else self.purpose

    def command_for(self, mode_key: str | None = None) -> str:
        mode = self.mode(mode_key)
        if mode is None or not mode.flags:
            return self.command
        return f"{self.command} {mode.flags}"

    def launch_command_for(self, mode_key: str | None = None) -> str:
        """The command GitDirector runs in the session, status hooks included."""
        command = self.command_for(mode_key)
        if self.status_launcher is None:
            return command
        return self.status_launcher(command)

    @property
    def launch_command(self) -> str:
        return self.launch_command_for()


CLAUDE_MODES: tuple[AgentMode, ...] = (
    AgentMode("default", "default", "claude-default"),
    AgentMode("auto", "auto", "claude-auto", "--permission-mode auto"),
    AgentMode(
        "bypass", "bypass", "claude-bypass", "--dangerously-skip-permissions", dangerous=True
    ),
)

AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        "claude",
        "Claude Code",
        "claude",
        "claude",
        ("claude", "claude-code"),
        status_launcher=claude_launch_command,
        modes=CLAUDE_MODES,
        default_mode="auto",
    ),
    AgentSpec(
        "opencode",
        "OpenCode",
        "opencode",
        "opencode",
        ("opencode",),
        status_launcher=opencode_launch_command,
    ),
    AgentSpec("codex", "Codex", "codex", "codex", ("codex",)),
    AgentSpec(
        "copilot",
        "GitHub Copilot",
        "copilot",
        "copilot",
        ("copilot", "github-copilot-cli"),
    ),
    AgentSpec("pi", "Pi", "pi", "pi", ("pi",)),
)

AGENTS_BY_KEY: dict[str, AgentSpec] = {agent.key: agent for agent in AGENTS}


def agent_tools() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """``(label, executables)`` pairs with one entry per distinct product."""
    tools: dict[str, tuple[str, ...]] = {}
    for agent in AGENTS:
        tools.setdefault(agent.label, agent.executables)
    return tuple(tools.items())


__all__ = [
    "AGENTS",
    "AGENTS_BY_KEY",
    "AGENT_STATES",
    "AGENT_STATE_IDLE",
    "AGENT_STATE_OPTION",
    "AGENT_STATE_RUNNING",
    "AGENT_STATE_WAITING",
    "AGENT_TRANSCRIPT_OPTION",
    "AGENT_WAITER_OPTION",
    "AgentMode",
    "AgentSpec",
    "CLAUDE_MODES",
    "CLAUDE_STATUS_EVENTS",
    "agent_tools",
    "claude_launch_command",
    "claude_status_hook_path",
    "opencode_launch_command",
    "opencode_status_plugin_path",
]
