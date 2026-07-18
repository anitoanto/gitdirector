"""Tests for the reset command.

This module installs an autouse safety fixture that patches every tmux
subprocess path used by the reset command, so a missed mock cannot result
in a real ``tmux kill-session`` against live sessions on the developer's
machine. The CLI tests then patch the high-level helpers in
``gitdirector.commands.reset``; the helper tests patch the tmux integration
module directly.
"""

import builtins
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gitdirector.cli import cli

# ---------------------------------------------------------------------------
# Safety net: block every real tmux invocation this test file might reach.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_tmux_during_reset_tests(monkeypatch):
    """Fail loudly if the reset code path reaches a real tmux subprocess.

    Tests in this file either patch the high-level helpers in
    ``gitdirector.commands.reset`` or patch the tmux integration directly.
    Anything that still tries to call into ``_run_tmux`` or the
    ``kill_tmux_session`` / ``list_all_gd_sessions`` boundaries is a
    test-isolation bug, so we replace those entry points with sentinels
    that raise to surface the bug instead of silently touching the host
    tmux server.
    """
    import gitdirector.integrations.tmux as tmux_pkg
    import gitdirector.integrations.tmux.core as tmux_core

    def _sentinel(*_args, **_kwargs):
        raise AssertionError(
            "Real tmux call reached during reset tests; "
            "patch the boundary in gitdirector.commands.reset "
            "or gitdirector.integrations.tmux.core instead."
        )

    monkeypatch.setattr(tmux_core, "_run_tmux", _sentinel)
    monkeypatch.setattr(tmux_core, "kill_tmux_session", _sentinel)
    monkeypatch.setattr(tmux_core, "list_all_gd_sessions", _sentinel)
    monkeypatch.setattr(tmux_pkg, "kill_all_gd_sessions", _sentinel)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_config_dir(path: Path) -> None:
    """Populate a directory with realistic gitdirector files for wipe tests."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.yaml").write_text(
        "repositories:\n- /tmp/some-repo\nmax_workers: 5\ntheme: monokai\n"
    )
    (path / "panels.yaml").write_text("panels: []\n")
    (path / "tmux_design.conf").write_text("# stale\n")
    (path / "config.lock").write_text("")


@pytest.fixture
def isolated_home(config_dir, monkeypatch):
    """Point Path.home() at the temporary parent used by ``config_dir``."""
    monkeypatch.setattr(Path, "home", lambda: config_dir.parent)
    return config_dir


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


class TestResetCommand:
    def test_reset_kills_sessions_wipes_dir_and_recreates_config(self, runner, isolated_home):
        """End-to-end: kills sessions, removes dir, writes a fresh config.yaml."""
        _seed_config_dir(isolated_home)

        killed_sessions = [
            "gd/repo/shell/1",
            "gd/panel/dev",
            "gd/temp/panel/gd/repo/shell/1",
        ]

        with patch(
            "gitdirector.commands.reset._kill_all_sessions",
            return_value=killed_sessions,
        ) as mock_kill:
            with patch("gitdirector.commands.reset._wipe_config_dir") as mock_wipe:
                with patch("gitdirector.commands.reset._recreate_config") as mock_recreate:
                    mock_recreate.return_value = MagicMock(
                        config_file=isolated_home / "config.yaml"
                    )
                    result = runner.invoke(cli, ["reset", "--yes"])

        assert result.exit_code == 0, result.output
        mock_kill.assert_called_once_with()
        mock_wipe.assert_called_once_with(isolated_home)
        mock_recreate.assert_called_once_with()
        for name in killed_sessions:
            assert name in result.output
        assert "Wiped and recreated" in result.output

    def test_reset_with_no_sessions_still_wipes(self, runner, isolated_home):
        """When no sessions are running, reset still wipes and recreates."""
        _seed_config_dir(isolated_home)

        with patch("gitdirector.commands.reset._kill_all_sessions", return_value=[]):
            with patch("gitdirector.commands.reset._wipe_config_dir") as mock_wipe:
                with patch("gitdirector.commands.reset._recreate_config") as mock_recreate:
                    mock_recreate.return_value = MagicMock(
                        config_file=isolated_home / "config.yaml"
                    )
                    result = runner.invoke(cli, ["reset", "--yes"])

        assert result.exit_code == 0, result.output
        assert "No active gd tmux sessions" in result.output
        assert "Wiped and recreated" in result.output
        mock_wipe.assert_called_once_with(isolated_home)

    def test_reset_cancelled_keeps_state(self, runner, isolated_home):
        """Declining the confirmation prompt cancels the reset entirely."""
        _seed_config_dir(isolated_home)

        with patch("gitdirector.commands.reset._kill_all_sessions") as mock_kill:
            with patch("gitdirector.commands.reset._wipe_config_dir") as mock_wipe:
                with patch("gitdirector.commands.reset._recreate_config") as mock_recreate:
                    result = runner.invoke(cli, ["reset"], input="n\n")

        assert result.exit_code == 0
        assert "Cancelled" in result.output
        mock_kill.assert_not_called()
        mock_wipe.assert_not_called()
        mock_recreate.assert_not_called()
        assert isolated_home.exists()
        assert (isolated_home / "config.yaml").exists()

    def test_reset_confirmed_proceeds(self, runner, isolated_home):
        """Answering 'y' to the prompt proceeds with the reset."""
        _seed_config_dir(isolated_home)

        with patch("gitdirector.commands.reset._kill_all_sessions", return_value=[]):
            with patch("gitdirector.commands.reset._wipe_config_dir"):
                with patch("gitdirector.commands.reset._recreate_config") as mock_recreate:
                    mock_recreate.return_value = MagicMock(
                        config_file=isolated_home / "config.yaml"
                    )
                    result = runner.invoke(cli, ["reset"], input="y\n")

        assert result.exit_code == 0
        assert "Cancelled" not in result.output
        assert "Wiped and recreated" in result.output
        mock_recreate.assert_called_once_with()

    def test_reset_missing_config_dir_still_succeeds(self, runner, isolated_home):
        """Reset succeeds and recreates config even when ~/.gitdirector is missing."""
        from gitdirector.commands.reset import _wipe_config_dir

        with patch("gitdirector.commands.reset._kill_all_sessions", return_value=[]):
            with patch(
                "gitdirector.commands.reset._wipe_config_dir",
                side_effect=_wipe_config_dir,
            ) as mock_wipe:
                with patch("gitdirector.commands.reset._recreate_config") as mock_recreate:
                    mock_recreate.return_value = MagicMock(
                        config_file=isolated_home / "config.yaml"
                    )
                    result = runner.invoke(cli, ["reset", "--yes"])

        assert result.exit_code == 0, result.output
        assert "Wiped and recreated" in result.output
        mock_wipe.assert_called_once_with(isolated_home)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestKillAllSessionsHelper:
    def test_invokes_tmux_helper(self):
        """_kill_all_sessions forwards to the tmux integration helper."""
        from gitdirector.commands.reset import _kill_all_sessions

        fake_module = MagicMock()
        fake_module.kill_all_gd_sessions.return_value = ["gd/repo/shell/1"]
        with patch.dict(
            "sys.modules",
            {"gitdirector.integrations.tmux": fake_module},
        ):
            result = _kill_all_sessions()

        assert result == ["gd/repo/shell/1"]
        fake_module.kill_all_gd_sessions.assert_called_once_with()

    def test_handles_import_error(self, monkeypatch):
        """When tmux integration is unavailable, returns empty list."""
        from gitdirector.commands.reset import _kill_all_sessions

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "gitdirector.integrations.tmux":
                raise ImportError("tmux unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert _kill_all_sessions() == []

    def test_handles_helper_exception(self):
        """If the tmux helper raises, we still return an empty list."""
        from gitdirector.commands.reset import _kill_all_sessions

        fake_module = MagicMock()
        fake_module.kill_all_gd_sessions.side_effect = RuntimeError("tmux crashed")
        with patch.dict(
            "sys.modules",
            {"gitdirector.integrations.tmux": fake_module},
        ):
            assert _kill_all_sessions() == []


class TestWipeConfigDirHelper:
    def test_removes_existing_directory(self, tmp_path):
        from gitdirector.commands.reset import _wipe_config_dir

        target = tmp_path / ".gitdirector"
        _seed_config_dir(target)
        assert target.exists()

        _wipe_config_dir(target)
        assert not target.exists()

    def test_noop_when_missing(self, tmp_path):
        from gitdirector.commands.reset import _wipe_config_dir

        target = tmp_path / ".gitdirector"
        assert not target.exists()

        _wipe_config_dir(target)
        assert not target.exists()

    def test_raises_runtime_error_on_failure(self, tmp_path):
        from gitdirector.commands.reset import _wipe_config_dir

        target = tmp_path / ".gitdirector"
        _seed_config_dir(target)

        with patch(
            "gitdirector.commands.reset.shutil.rmtree",
            side_effect=OSError("denied"),
        ):
            with pytest.raises(RuntimeError, match="denied"):
                _wipe_config_dir(target)
