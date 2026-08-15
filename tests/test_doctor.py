from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from gitdirector.cli import cli
from gitdirector.commands import doctor as doctor_module
from gitdirector.version_check import UpdateStatus


def _mock_version(monkeypatch, current: str = "1.2.3", latest: str | None = "1.2.3") -> None:
    monkeypatch.setattr(
        doctor_module.version_check,
        "get_update_status",
        lambda: UpdateStatus(current_version=current, latest_version=latest),
    )


def test_doctor_reports_ok_when_tools_are_available(config, monkeypatch, tmp_path):
    _mock_version(monkeypatch)
    home = config.config_dir.parent
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("SHELL", "/bin/zsh")

    completions_dir = home / ".zsh/completions"
    completions_dir.mkdir(parents=True)
    (completions_dir / "_gitdirector").write_text("#compdef gitdirector\n")

    tool_paths = {
        "tmux": "/usr/bin/tmux",
        "pbcopy": "/usr/bin/pbcopy",
        "opencode": "/usr/local/bin/opencode",
        "codex": "/usr/local/bin/codex",
    }
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: tool_paths.get(name))

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "GitDirector Doctor" not in result.output
    assert "GitDirector [1.2.3]" in result.output.splitlines()[0]
    assert "[✓]" in result.output
    assert "Up to date" in result.output
    assert "Current version: 1.2.3" not in result.output
    assert "Tmux" in result.output
    assert "Clipboard Integration" in result.output
    assert "detected" in result.output
    assert "~/.gitdirector folder valid" in result.output
    assert "OpenCode: /usr/local/bin/opencode" in result.output
    assert "Codex: /usr/local/bin/codex" in result.output
    assert "Pi: not installed" in result.output
    assert "optional check needs attention" in result.output


def test_doctor_warns_when_optional_tools_are_missing(config, monkeypatch):
    _mock_version(monkeypatch)
    home = config.config_dir.parent
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr(doctor_module.shutil, "which", lambda _name: None)

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 1, result.output
    assert "Needed for `gitdirector console`" in result.output
    assert "Looked for: pbcopy, wl-copy, xclip, xsel, clip.exe." in result.output
    assert "Shell Completion" in result.output
    assert "OpenCode: not installed" in result.output
    assert "Fix:" in result.output
    assert "optional checks need attention" in result.output
    assert "critical check failed" in result.output


def test_doctor_fails_when_config_is_not_writable(monkeypatch, tmp_path):
    _mock_version(monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(doctor_module.shutil, "which", lambda _name: "/usr/bin/tmux")
    monkeypatch.setattr(
        doctor_module, "_config_writable", lambda _config: (False, "permission denied")
    )

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 1, result.output
    assert "permission denied" in result.output
    assert "critical" in result.output


def test_doctor_reports_corrupted_gitdirector_file(config, monkeypatch):
    _mock_version(monkeypatch)
    home = config.config_dir.parent
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("SHELL", "/bin/zsh")

    (config.config_dir / "panels.yaml").write_text("panels: nope\n")

    tool_paths = {
        "tmux": "/usr/bin/tmux",
        "pbcopy": "/usr/bin/pbcopy",
        "opencode": "/usr/local/bin/opencode",
    }
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: tool_paths.get(name))

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 1, result.output
    assert "Corrupted: panels.yaml:" in result.output
    assert "critical" in result.output


def test_doctor_reports_when_gitdirector_update_is_available(config, monkeypatch):
    _mock_version(monkeypatch, current="1.2.3", latest="1.3.0")
    home = config.config_dir.parent
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(doctor_module.shutil, "which", lambda _name: "/usr/bin/tmux")

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "GitDirector [1.2.3]" in result.output
    assert "GitDirector 1.3.0 is available" in result.output
    assert "Update GitDirector." in result.output
