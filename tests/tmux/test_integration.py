"""Real tmux integration tests."""

import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from gitdirector.integrations.tmux import (
    TmuxMonitor,
    create_tmux_session,
    get_all_session_statuses,
    list_all_gd_sessions,
    list_repo_sessions,
    rebuild_panel_tmux_session,
    sync_panel_tmux_config,
)

from ._shared import _tmux_integration_lock

@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
class TestPanelExitIntegration:
    def test_exiting_one_panel_pane_keeps_panel_and_other_session_alive(
        self,
        tmp_path,
        monkeypatch,
    ):
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = Path(tempfile.mkdtemp(prefix="gd-tmux-"))
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            suffix = uuid.uuid4().hex[:8]
            base_a = f"gd/repro-base-a-{suffix}"
            base_b = f"gd/repro-base-b-{suffix}"
            panel_name = f"repro-{suffix}"

            def run_tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["tmux", *args],
                    capture_output=True,
                    text=True,
                    check=check,
                )

            try:
                run_tmux("new-session", "-d", "-s", base_a, "-n", "a")
                run_tmux("new-session", "-d", "-s", base_b, "-n", "b")

                panel_session = rebuild_panel_tmux_session(
                    panel_name,
                    1,
                    2,
                    {1: base_a, 2: base_b},
                    layout_key="grid_1x2",
                )

                sessions_before = run_tmux(
                    "list-sessions", "-F", "#{session_name}"
                ).stdout.splitlines()
                assert panel_session in sessions_before
                assert base_a in sessions_before
                assert base_b in sessions_before
                assert not any(
                    name.startswith(f"gd/temp/panel/{panel_name}/") for name in sessions_before
                )

                run_tmux("send-keys", "-t", f"={panel_session}:0.1", "exit", "Enter")
                time.sleep(1)

                sessions_after = run_tmux(
                    "list-sessions", "-F", "#{session_name}"
                ).stdout.splitlines()
                pane_commands = run_tmux(
                    "list-panes",
                    "-t",
                    f"={panel_session}:0",
                    "-F",
                    "#{pane_index}|#{pane_current_command}",
                ).stdout.splitlines()

                assert panel_session in sessions_after
                assert base_a not in sessions_after
                assert base_b in sessions_after
                assert not any(
                    name.startswith(f"gd/temp/panel/{panel_name}/") for name in sessions_after
                )
                assert "1|tail" in pane_commands
                assert any(line.startswith("2|") and line != "2|tail" for line in pane_commands)
            finally:
                subprocess.run(["tmux", "kill-server"], capture_output=True, text=True, check=False)
                shutil.rmtree(tmux_dir, ignore_errors=True)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
class TestIdleSessionPreservationIntegration:
    def test_idle_polling_keeps_repo_panel_and_temp_wrapper_sessions_alive(
        self,
        tmp_path,
        monkeypatch,
    ):
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = Path(tempfile.mkdtemp(prefix="gd-tmux-idle-"))
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            repo_a = home_dir / "repo-a"
            repo_b = home_dir / "repo-b"
            repo_a.mkdir()
            repo_b.mkdir()

            def run_tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["tmux", *args],
                    capture_output=True,
                    text=True,
                    check=check,
                )

            monitor = TmuxMonitor()
            try:
                session_a = create_tmux_session("repo-a", repo_a, purpose="shell")
                session_b = create_tmux_session("repo-b", repo_b, purpose="shell")
                panel_session = rebuild_panel_tmux_session(
                    "idle-guard",
                    1,
                    2,
                    {1: session_a, 2: session_b},
                    layout_key="grid_1x2",
                    theme_name="rose-pine",
                )
                temp_wrapper = f"gd/temp/panel/{session_a[3:]}"
                run_tmux("new-session", "-d", "-s", temp_wrapper, "-n", "wrapper", "cat")
                run_tmux("set-option", "-t", f"={temp_wrapper}:", "destroy-unattached", "off")
                expected_sessions = {session_a, session_b, panel_session, temp_wrapper}

                monitor.start()
                for _ in range(12):
                    assert [entry["session_name"] for entry in list_all_gd_sessions()] == [
                        session_a,
                        session_b,
                    ]
                    assert set(get_all_session_statuses()) == {session_a, session_b}
                    sync_panel_tmux_config("rose-pine")

                    live_sessions = set(
                        run_tmux("list-sessions", "-F", "#{session_name}").stdout.splitlines()
                    )
                    assert expected_sessions.issubset(live_sessions)
                    time.sleep(0.15)
            finally:
                monitor.stop()
                subprocess.run(["tmux", "kill-server"], capture_output=True, text=True, check=False)
                shutil.rmtree(tmux_dir, ignore_errors=True)

    def test_repo_discovery_and_tmux_config_ignore_temp_wrappers_when_wrappers_exist(
        self,
        tmp_path,
        monkeypatch,
    ):
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = Path(tempfile.mkdtemp(prefix="gd-tmux-monitor-"))
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            repo_a = home_dir / "repo-a"
            repo_b = home_dir / "repo-b"
            repo_a.mkdir()
            repo_b.mkdir()

            def run_tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["tmux", *args],
                    capture_output=True,
                    text=True,
                    check=check,
                )

            try:
                session_a = create_tmux_session("repo-a", repo_a, purpose="shell")
                session_b = create_tmux_session("repo-b", repo_b, purpose="shell")
                rebuild_panel_tmux_session(
                    "idle-guard",
                    1,
                    2,
                    {1: session_a, 2: session_b},
                    layout_key="grid_1x2",
                    theme_name="rose-pine",
                )
                temp_wrapper = f"gd/temp/panel/{session_a[3:]}"
                run_tmux("new-session", "-d", "-s", temp_wrapper, "-n", "wrapper", "cat")
                run_tmux("set-option", "-t", f"={temp_wrapper}:", "destroy-unattached", "off")

                assert list_repo_sessions("repo-a") == [session_a]
                assert list_repo_sessions("repo-b") == [session_b]

                config_path = sync_panel_tmux_config("rose-pine")
                config_text = config_path.read_text()

                assert session_a in config_text
                assert session_b in config_text
                assert temp_wrapper not in config_text
            finally:
                subprocess.run(["tmux", "kill-server"], capture_output=True, text=True, check=False)
                shutil.rmtree(tmux_dir, ignore_errors=True)
