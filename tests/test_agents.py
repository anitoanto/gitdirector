"""Tests for the agent registry and the hook-based status protocol."""

import json
import shlex

import pytest

from gitdirector.agents import (
    AGENT_INTERRUPTS_OPTION,
    AGENT_STATE_OPTION,
    AGENTS,
    AGENTS_BY_KEY,
    agent_state_report_command,
    agent_tools,
    opencode_status_plugin_path,
)


class TestAgentStateReportCommand:
    def test_sets_option_on_the_pane_session_and_never_fails(self):
        command = agent_state_report_command("waiting")
        assert command.startswith('[ -n "$TMUX_PANE" ] && ')
        assert f'tmux set-option -t "$TMUX_PANE" {AGENT_STATE_OPTION} waiting' in command
        assert command.endswith("; exit 0")

    def test_none_clears_both_options(self):
        command = agent_state_report_command(None)
        assert f'tmux set-option -u -t "$TMUX_PANE" {AGENT_STATE_OPTION}' in command
        assert f'tmux set-option -u -t "$TMUX_PANE" {AGENT_INTERRUPTS_OPTION}' in command

    def test_interrupt_flag_is_stamped_alongside_the_state(self):
        command = agent_state_report_command("idle", interrupts_unreported=True)
        assert f"{AGENT_STATE_OPTION} idle" in command
        assert f"{AGENT_INTERRUPTS_OPTION} unreported" in command
        assert AGENT_INTERRUPTS_OPTION not in agent_state_report_command("idle")

    def test_rejects_unknown_states(self):
        with pytest.raises(ValueError):
            agent_state_report_command("busy")


class TestClaudeLaunchCommand:
    @pytest.mark.parametrize("key", ["claude", "claude-skip-permissions"])
    def test_both_claude_variants_inject_hook_settings(self, key):
        agent = AGENTS_BY_KEY[key]
        assert agent.reports_status
        argv = shlex.split(agent.launch_command)
        assert argv[: len(shlex.split(agent.command))] == shlex.split(agent.command)
        assert argv[-2] == "--settings"
        settings = json.loads(argv[-1])
        hooks = settings["hooks"]
        assert set(hooks) == {
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "PostToolUseFailure",
            "PermissionRequest",
            "PermissionDenied",
            "Notification",
            "Stop",
            "SessionEnd",
        }
        for entries in hooks.values():
            for entry in entries:
                for hook in entry["hooks"]:
                    assert hook["type"] == "command"
                    assert hook["command"].endswith("exit 0")

    def test_hook_states_match_the_lifecycle(self):
        settings = json.loads(shlex.split(AGENTS_BY_KEY["claude"].launch_command)[-1])

        def command(event: str) -> str:
            return settings["hooks"][event][0]["hooks"][0]["command"]

        assert f"{AGENT_STATE_OPTION} idle" in command("SessionStart")
        # Claude cannot report an Escape, so it asks the monitor to watch for one.
        assert f"{AGENT_INTERRUPTS_OPTION} unreported" in command("SessionStart")
        assert AGENT_INTERRUPTS_OPTION not in command("Stop")
        assert f"{AGENT_STATE_OPTION} idle" in command("Stop")
        assert f"{AGENT_STATE_OPTION} running" in command("UserPromptSubmit")
        assert f"{AGENT_STATE_OPTION} running" in command("PostToolUse")
        assert f"{AGENT_STATE_OPTION} waiting" in command("PermissionRequest")
        assert f"{AGENT_STATE_OPTION} idle" in command("PermissionDenied")
        assert f"{AGENT_STATE_OPTION} running" in command("PostToolUseFailure")
        assert "set-option -u" in command("SessionEnd")
        # Asking the user a question is waiting; any other tool is work.
        pre_tool = command("PreToolUse")
        assert "AskUserQuestion" in pre_tool
        assert f"{AGENT_STATE_OPTION} waiting" in pre_tool
        assert f"{AGENT_STATE_OPTION} running" in pre_tool
        # The periodic idle reminder must not flip a finished session back.
        notification = command("Notification")
        assert "idle_prompt" in notification
        assert f"{AGENT_STATE_OPTION} waiting" in notification

    def test_other_agents_launch_unchanged(self):
        for agent in AGENTS:
            if agent.key.startswith("claude") or agent.key == "opencode":
                continue
            assert not agent.reports_status
            assert agent.launch_command == agent.command


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
        # Interrupts are reported by OpenCode itself, so no flag is requested.
        assert AGENT_INTERRUPTS_OPTION not in source


class TestAgentTools:
    def test_one_entry_per_product(self):
        labels = [label for label, _ in agent_tools()]
        assert labels.count("Claude Code") == 1
        assert dict(agent_tools())["Claude Code"] == ("claude", "claude-code")
