"""The CLI's session commands: sessions, gd-kill, gd-tmux --agent, panel, and pull targets."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from gitdirector.agents import AGENTS_BY_KEY
from gitdirector.cli import cli


def _entry(name: str, repo: str, purpose: str, description: str = "-") -> dict[str, str]:
    return {
        "session_name": name,
        "repo": repo,
        "repo_slug": name.split("/")[1],
        "purpose": purpose,
        "description": description,
    }


@pytest.fixture
def monitor(monkeypatch):
    fake = MagicMock()
    fake.refresh.return_value = {
        "gd/web_aaaaa/claude-auto/2": "waiting",
        "gd/web_aaaaa/shell/10": "running",
        "gd/api_bbbbb/shell/1": "idle",
    }
    fake.entries.return_value = [
        _entry("gd/api_bbbbb/shell/1", "api", "shell"),
        _entry("gd/web_aaaaa/shell/10", "web", "shell", "Vite: dev server"),
        _entry("gd/web_aaaaa/claude-auto/2", "web", "claude-auto", "Claude: fix tests"),
    ]
    monkeypatch.setattr("gitdirector.integrations.tmux.TmuxMonitor", lambda: fake)
    return fake


class TestSessions:
    def test_table_groups_sessions_by_repo(self, monitor):
        result = CliRunner().invoke(cli, ["sessions"])

        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        assert lines[0].split() == ["REPOSITORY", "STATUS", "SESSION", "DESCRIPTION"]
        assert lines[1].split()[:4] == ["api", "○", "idle", "gd/api_bbbbb/shell/1"]
        # The repo is named once; its second session leaves the column blank.
        assert lines[2].split()[:3] == ["web", "●", "waiting"]
        assert lines[3].split()[:3] == ["●", "running", "gd/web_aaaaa/shell/10"]
        assert lines[-1] == "3 sessions · 1 waiting · 1 running"

    def test_json(self, monitor):
        result = CliRunner().invoke(cli, ["sessions", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data[0] == {
            "session": "gd/api_bbbbb/shell/1",
            "repo": "api",
            "purpose": "shell",
            "status": "idle",
            "description": None,
        }
        assert [s["session"] for s in data][1:] == [
            "gd/web_aaaaa/claude-auto/2",
            "gd/web_aaaaa/shell/10",
        ]

    def test_none(self, monitor):
        monitor.entries.return_value = None

        result = CliRunner().invoke(cli, ["sessions"])

        assert result.exit_code == 0
        assert "No live sessions" in result.output
        assert json.loads(CliRunner().invoke(cli, ["sessions", "--json"]).stdout) == []


class TestGdKill:
    def test_kills_the_session(self, monkeypatch):
        kill = MagicMock(return_value=True)
        monkeypatch.setattr("gitdirector.integrations.tmux.kill_tmux_session", kill)
        monkeypatch.setattr("gitdirector.integrations.tmux.sync_panel_tmux_config", MagicMock())

        result = CliRunner().invoke(cli, ["gd-kill", "gd/web_aaaaa/shell/1"])

        assert result.exit_code == 0, result.output
        kill.assert_called_once_with("gd/web_aaaaa/shell/1")
        assert result.stdout == "Killed gd/web_aaaaa/shell/1\n"

    def test_session_not_running(self, monkeypatch):
        monkeypatch.setattr("gitdirector.integrations.tmux.kill_tmux_session", lambda _n: False)

        result = CliRunner().invoke(cli, ["gd-kill", "gd/web_aaaaa/shell/1"])

        assert result.exit_code == 1
        assert "is not running" in result.stderr

    def test_rejects_a_partial_name(self, monkeypatch):
        kill = MagicMock()
        monkeypatch.setattr("gitdirector.integrations.tmux.kill_tmux_session", kill)

        result = CliRunner().invoke(cli, ["gd-kill", "gd/web"])

        assert result.exit_code == 1
        kill.assert_not_called()


@pytest.fixture
def tracked_repo(config, monkeypatch, tmp_path):
    monkeypatch.setattr("gitdirector.manager.Config", lambda: config)
    repo = tmp_path / "web"
    (repo / ".git").mkdir(parents=True)
    config.add_repository(repo)
    return repo


@pytest.fixture
def fake_tmux(monkeypatch):
    fake = MagicMock()
    fake.create_tmux_session.side_effect = lambda name, path, purpose, **_: f"gd/{name}/{purpose}/1"
    monkeypatch.setitem(sys.modules, "gitdirector.integrations.tmux", fake)
    return fake


class TestGdTmuxAgent:
    def test_starts_the_agent_with_its_status_hooks(self, tracked_repo, fake_tmux):
        result = CliRunner().invoke(cli, ["gd-tmux", "web", "--agent", "opencode", "-d", "fix"])

        assert result.exit_code == 0, result.output
        assert result.stdout == "gd/web/opencode/1\n"
        fake_tmux.create_tmux_session.assert_called_once_with(
            "web", tracked_repo, purpose="opencode", description="fix", shell=False
        )
        fake_tmux.launch_command_in_tmux_session.assert_called_once_with(
            "gd/web/opencode/1", AGENTS_BY_KEY["opencode"].launch_command
        )

    def test_claude_defaults_to_auto_mode(self, tracked_repo, fake_tmux):
        result = CliRunner().invoke(cli, ["gd-tmux", "web", "-a", "claude"])

        assert result.exit_code == 0, result.output
        assert result.stdout == "gd/web/claude-auto/1\n"

    @pytest.mark.parametrize("args", [[], ["ls", "--agent", "codex"]])
    def test_needs_exactly_one_of_command_or_agent(self, tracked_repo, fake_tmux, args):
        result = CliRunner().invoke(cli, ["gd-tmux", "web", *args])

        assert result.exit_code == 2
        assert "pass either COMMAND or --agent" in result.output
        fake_tmux.create_tmux_session.assert_not_called()

    def test_a_failed_launch_removes_the_session(self, tracked_repo, fake_tmux):
        from gitdirector.integrations.tmux.core import TmuxError

        fake_tmux.launch_command_in_tmux_session.side_effect = TmuxError("no ptys")

        result = CliRunner().invoke(cli, ["gd-tmux", "web", "sleep 1"])

        assert result.exit_code == 1
        assert "Couldn't start the session" in result.stderr
        fake_tmux.kill_tmux_session.assert_called_once_with("gd/web/shell/1")


class TestPanel:
    @pytest.fixture
    def store(self, monkeypatch):
        from gitdirector.commands.tui.panels import Panel

        store = MagicMock()
        store.panels = [Panel("dev", 1, 2, {1: "gd/web_aaaaa/shell/1", 2: None})]
        store.get.side_effect = lambda name: next((p for p in store.panels if p.name == name), None)
        monkeypatch.setattr("gitdirector.commands.panel._panel_store", lambda: store)
        return store

    def test_lists_panels(self, store, monkeypatch):
        monkeypatch.setattr(
            "gitdirector.integrations.tmux.core._list_sessions", lambda: ["gd/web_aaaaa/shell/1"]
        )

        result = CliRunner().invoke(cli, ["panel"])

        assert result.exit_code == 0, result.output
        assert result.output.splitlines()[1].split() == ["dev", "1×2", "1/2", "live"]

    def test_opens_a_panel(self, store, monkeypatch):
        rebuild = MagicMock(return_value="gd/panel/dev")
        attach = MagicMock()
        monkeypatch.setattr("gitdirector.integrations.tmux.core._session_exists", lambda _n: False)
        monkeypatch.setattr("gitdirector.integrations.tmux.rebuild_panel_tmux_session", rebuild)
        monkeypatch.setattr("gitdirector.integrations.tmux.attach_tmux_session", attach)

        result = CliRunner().invoke(cli, ["panel", "dev"])

        assert result.exit_code == 0, result.output
        assert rebuild.call_args.args[0] == "dev"
        attach.assert_called_once_with("gd/panel/dev")

    def test_unknown_panel(self, store):
        result = CliRunner().invoke(cli, ["panel", "nope"])

        assert result.exit_code == 1
        assert "No panel named 'nope'" in result.stderr


class TestPullTargets:
    def test_named_repositories_are_pulled_without_asking(self, tracked_repo):
        with patch(
            "gitdirector.commands.pull.pull_repository",
            return_value=("web", True, "Already up to date."),
        ) as pull:
            result = CliRunner().invoke(cli, ["pull", "web"])

        assert result.exit_code == 0, result.output
        pull.assert_called_once_with(tracked_repo)
        assert "[Y/n]" not in result.output
        assert "✓ up to date" in result.output

    def test_unknown_target_fails_before_pulling(self, tracked_repo):
        with patch("gitdirector.commands.pull.pull_repository") as pull:
            result = CliRunner().invoke(cli, ["pull", "nope"])

        assert result.exit_code == 1
        assert "No tracked repository named: nope" in result.stderr
        pull.assert_not_called()

    def test_without_a_terminal_the_prompt_points_at_yes(self, tracked_repo):
        result = CliRunner().invoke(cli, ["pull"], input="")

        assert result.exit_code == 1
        assert "pass --yes" in result.stderr


def test_repository_arguments_accept_paths(tracked_repo, fake_tmux):
    result = CliRunner().invoke(cli, ["gd-tmux", str(tracked_repo), "ls"])

    assert result.exit_code == 0, result.output
    assert fake_tmux.create_tmux_session.call_args.args[1] == Path(tracked_repo)
