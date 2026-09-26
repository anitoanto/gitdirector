from __future__ import annotations

from pathlib import Path

import pytest
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


@pytest.fixture
def doctor_env(config, monkeypatch):
    """A home with GitDirector's state, a zsh user, and tmux 3.7c on PATH."""
    _mock_version(monkeypatch)
    home = config.config_dir.parent
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.setattr(doctor_module, "_tool_version", lambda _path: "tmux 3.7c")
    return home


def _tools(monkeypatch, **paths: str) -> None:
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: paths.get(name))


def _doctor():
    return CliRunner().invoke(cli, ["doctor"])


def test_doctor_reports_ok_when_tools_are_available(doctor_env, monkeypatch):
    completions_dir = doctor_env / ".zsh/completions"
    completions_dir.mkdir(parents=True)
    (completions_dir / "_gitdirector").write_text("#compdef gitdirector\n")
    _tools(
        monkeypatch,
        git="/usr/bin/git",
        tmux="/usr/bin/tmux",
        opencode="/usr/local/bin/opencode",
        codex="/usr/local/bin/codex",
    )

    result = _doctor()

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].startswith("✓  GitDirector  1.2.3, up to date")
    assert "✓  git          /usr/bin/git" in result.output
    assert "✓  tmux         3.7c (/usr/bin/tmux)" in result.output
    assert "installed in ~/.zsh/completions/_gitdirector" in result.output
    assert "2 of 5 installed" in result.output
    assert "OpenCode: /usr/local/bin/opencode" in result.output
    assert "Pi: not installed" in result.output
    assert lines[-1] == "No issues found."


def test_doctor_fails_without_git_or_tmux(doctor_env, monkeypatch):
    _tools(monkeypatch)

    result = _doctor()

    assert result.exit_code == 1, result.output
    assert "✗  git          not installed" in result.output
    assert "fix: Install tmux 3.7 or newer." in result.output
    assert 'fix: Add to ~/.zshrc: eval "$(gitdirector completion zsh)"' in result.output
    assert "none installed" in result.output
    assert result.output.splitlines()[-1] == "2 checks failed · 2 warnings"


def test_doctor_fails_on_old_tmux(doctor_env, monkeypatch):
    _tools(monkeypatch, git="/usr/bin/git", tmux="/usr/bin/tmux")
    monkeypatch.setattr(doctor_module, "_tool_version", lambda _path: "tmux 3.6b")

    result = _doctor()

    assert result.exit_code == 1, result.output
    assert "tmux 3.6b is too old" in result.output


@pytest.mark.parametrize(
    "text,expected",
    [
        ("tmux 3.7c", (3, 7, "c")),
        ("tmux 3.2a", (3, 2, "a")),
        ("tmux next-3.6", (3, 6, "")),
        ("tmux master", None),
    ],
)
def test_parse_tmux_version(text, expected):
    assert doctor_module._parse_tmux_version(text) == expected


def test_doctor_fails_when_config_is_not_writable(doctor_env, monkeypatch):
    _tools(monkeypatch, git="/usr/bin/git", tmux="/usr/bin/tmux")
    monkeypatch.setattr(
        doctor_module, "_config_writable", lambda _config: (False, "permission denied")
    )

    result = _doctor()

    assert result.exit_code == 1, result.output
    assert "~/.gitdirector is not writable" in result.output
    assert "permission denied" in result.output


def test_config_writable_reports_permission_error(config, monkeypatch):
    def deny(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(doctor_module.tempfile, "mkstemp", deny)

    ok, detail = doctor_module._config_writable(config)

    assert ok is False
    assert "denied" in detail


def test_zsh_completion_is_read_from_zdotdir(doctor_env, monkeypatch):
    zdotdir = doctor_env / ".config/zsh"
    zdotdir.mkdir(parents=True)
    monkeypatch.setenv("ZDOTDIR", str(zdotdir))
    (doctor_env / ".zshrc").write_text('eval "$(gitdirector completion zsh)"\n')

    assert doctor_module._completion_installed("zsh", doctor_env)[0] is False
    _tools(monkeypatch, git="/usr/bin/git", tmux="/usr/bin/tmux")
    assert 'fix: Add to ~/.config/zsh/.zshrc: eval "$(gitdirector completion zsh)"' in (
        _doctor().output
    )

    (zdotdir / ".zshrc").write_text('eval "$(gitdirector completion zsh)"\n')
    assert doctor_module._completion_installed("zsh", doctor_env) == (
        True,
        "set up in ~/.config/zsh/.zshrc",
    )


def test_doctor_reports_corrupted_gitdirector_file(doctor_env, config, monkeypatch):
    (config.config_dir / "panels.yaml").write_text("panels: nope\n")
    _tools(monkeypatch, git="/usr/bin/git", tmux="/usr/bin/tmux")

    result = _doctor()

    assert result.exit_code == 1, result.output
    assert "Corrupted: panels.yaml:" in result.output


def test_doctor_warns_but_passes_when_optional_checks_fail(doctor_env, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/tcsh")
    _tools(monkeypatch, git="/usr/bin/git", tmux="/usr/bin/tmux")

    result = _doctor()

    assert result.exit_code == 0, result.output
    assert "$SHELL is not bash, zsh, or fish" in result.output


def test_doctor_reports_when_gitdirector_update_is_available(doctor_env, monkeypatch):
    _mock_version(monkeypatch, current="1.2.3", latest="1.3.0")
    _tools(monkeypatch, git="/usr/bin/git", tmux="/usr/bin/tmux")

    result = _doctor()

    assert result.exit_code == 0, result.output
    assert "1.2.3, 1.3.0 is available" in result.output
    assert "fix: pip install -U gitdirector" in result.output
