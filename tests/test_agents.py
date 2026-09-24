"""Tests for the agent registry and the inline status integrations."""

import json
import shlex
import sys

import pytest

from gitdirector.agents import (
    AGENT_STATE_OPTION,
    AGENTS,
    AGENTS_BY_KEY,
    CLAUDE_STATUS_EVENTS,
    agent_tools,
    claude_status_hook_path,
    opencode_status_plugin_path,
)


def _claude_settings(command: str) -> dict:
    argv = shlex.split(command)
    assert argv[-2] == "--settings"
    return json.loads(argv[-1])


class TestClaudeLaunchCommand:
    @pytest.mark.parametrize("mode", ["default", "auto", "bypass"])
    def test_every_claude_mode_injects_the_hooks_inline(self, mode):
        agent = AGENTS_BY_KEY["claude"]
        assert agent.reports_status
        argv = shlex.split(agent.launch_command_for(mode))
        command = shlex.split(agent.command_for(mode))
        assert argv[: len(command)] == command
        settings = _claude_settings(agent.launch_command_for(mode))
        # Only hooks: nothing else of the user's settings is overridden.
        assert list(settings) == ["hooks"]
        assert set(settings["hooks"]) == set(CLAUDE_STATUS_EVENTS)

    def test_every_event_runs_the_shipped_hook_script_in_isolated_python(self):
        settings = _claude_settings(AGENTS_BY_KEY["claude"].launch_command)
        commands = {
            hook["command"]
            for entries in settings["hooks"].values()
            for entry in entries
            for hook in entry["hooks"]
        }
        (command,) = commands
        assert shlex.split(command) == [sys.executable, "-I", "-S", str(claude_status_hook_path())]
        for entries in settings["hooks"].values():
            (hook,) = entries[0]["hooks"]
            assert hook["type"] == "command"
            assert hook["timeout"] > 0

    def test_hook_script_ships_with_the_package(self):
        assert claude_status_hook_path().is_file()

    def test_noisy_events_are_left_out(self):
        # Most notification types are not about the user being needed, and
        # SubagentStop fires for Claude's own helpers after a turn.
        assert "Notification" not in CLAUDE_STATUS_EVENTS
        assert "SubagentStop" not in CLAUDE_STATUS_EVENTS

    def test_other_agents_launch_unchanged(self):
        for agent in AGENTS:
            if agent.key in ("claude", "opencode"):
                continue
            assert not agent.reports_status
            assert agent.launch_command == agent.command


class TestClaudeModes:
    def test_modes_map_to_permission_flags(self):
        agent = AGENTS_BY_KEY["claude"]
        assert agent.command_for("default") == "claude"
        assert agent.command_for("auto") == "claude --permission-mode auto"
        assert agent.command_for("bypass") == "claude --dangerously-skip-permissions"

    def test_auto_is_the_default_and_unknown_modes_fall_back_to_it(self):
        agent = AGENTS_BY_KEY["claude"]
        assert agent.default_mode == "auto"
        assert agent.command_for(None) == "claude --permission-mode auto"
        assert agent.command_for("nope") == "claude --permission-mode auto"
        assert agent.launch_command == agent.launch_command_for("auto")

    def test_only_bypass_is_flagged_dangerous(self):
        dangerous = [mode.key for mode in AGENTS_BY_KEY["claude"].modes if mode.dangerous]
        assert dangerous == ["bypass"]

    def test_each_mode_names_its_sessions_with_a_short_slug(self):
        agent = AGENTS_BY_KEY["claude"]
        assert agent.purpose_for("default") == "claude-default"
        assert agent.purpose_for("auto") == "claude-auto"
        assert agent.purpose_for("bypass") == "claude-bypass"
        assert agent.purpose_for(None) == "claude-auto"
        assert AGENTS_BY_KEY["codex"].purpose_for("auto") == "codex"

    def test_agents_without_modes_ignore_a_mode(self):
        agent = AGENTS_BY_KEY["codex"]
        assert agent.mode("auto") is None
        assert agent.launch_command_for("auto") == "codex"


class TestOpenCodeLaunchCommand:
    def test_injects_the_status_plugin_through_the_environment(self):
        agent = AGENTS_BY_KEY["opencode"]
        assert agent.reports_status
        assignment, *rest = shlex.split(agent.launch_command)
        assert rest == ["opencode"]
        name, _, value = assignment.partition("=")
        assert name == "OPENCODE_CONFIG_CONTENT"
        config = json.loads(value)
        assert list(config) == ["plugin"]
        (plugin_uri,) = config["plugin"]
        assert plugin_uri == opencode_status_plugin_path().as_uri()
        assert plugin_uri.startswith("file://")

    def test_plugin_ships_with_the_package_and_speaks_the_protocol(self):
        source = opencode_status_plugin_path().read_text(encoding="utf-8")
        assert "export const GitDirectorStatus" in source
        assert AGENT_STATE_OPTION in source
        assert "TMUX_PANE" in source
        for event in (
            "session.status",
            "session.idle",
            "permission.asked",
            "permission.replied",
            "question.asked",
            "question.replied",
        ):
            assert f'"{event}"' in source
        for state in ("running", "waiting", "idle"):
            assert f'"{state}"' in source
        # A retrying turn is still a turn in progress.
        assert 'props.status?.type === "idle"' in source


class TestAgentTools:
    def test_one_entry_per_product(self):
        labels = [label for label, _ in agent_tools()]
        assert labels.count("Claude Code") == 1
        assert dict(agent_tools())["Claude Code"] == ("claude", "claude-code")
