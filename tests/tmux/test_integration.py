"""Real tmux integration tests."""

import shutil
import subprocess
import time
import uuid

import pytest

from gitdirector.integrations.tmux import (
    create_tmux_session,
    list_repo_sessions,
    rebuild_panel_tmux_session,
    sync_panel_tmux_config,
)

from .._timeouts import POLL_TIMEOUT, TMUX_CMD_TIMEOUT
from ._shared import _cleanup_tmux_tmpdir, _make_short_tmux_tmpdir, _tmux_integration_lock


def _wait_for(predicate, timeout: float = POLL_TIMEOUT, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


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
            tmux_dir = _make_short_tmux_tmpdir()
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
                    timeout=TMUX_CMD_TIMEOUT,
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
                assert _wait_for(
                    lambda: (
                        base_a
                        not in run_tmux(
                            "list-sessions", "-F", "#{session_name}"
                        ).stdout.splitlines()
                    )
                )

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
                assert "1|tail" not in pane_commands
                assert any(line.startswith("2|") for line in pane_commands)
            finally:
                subprocess.run(
                    ["tmux", "kill-server"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=TMUX_CMD_TIMEOUT,
                )
                _cleanup_tmux_tmpdir(tmux_dir)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
class TestPanelRebuildOrphanIntegration:
    """Real-tmux regression tests for the orphan-session lifecycle.

    Reproduces the user's report: a panel with multiple panes going through
    reconfigure/rebuild must not leave stray sessions behind and must
    tolerate tmux's ``.`` -> ``_`` munging of session names.
    """

    def test_reconfigure_does_not_leak_orphan_session(
        self,
        tmp_path,
        monkeypatch,
    ):
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = _make_short_tmux_tmpdir()
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            suffix = uuid.uuid4().hex[:8]
            base_a = f"gd/repro-base-a-{suffix}"
            base_b = f"gd/repro-base-b-{suffix}"
            base_c = f"gd/repro-base-c-{suffix}"
            panel_name = f"repro-{suffix}"

            def run_tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["tmux", *args],
                    capture_output=True,
                    text=True,
                    check=check,
                    timeout=TMUX_CMD_TIMEOUT,
                )

            try:
                for s in (base_a, base_b, base_c):
                    run_tmux("new-session", "-d", "-s", s, "-n", "a")
                    run_tmux("set-option", "-t", f"={s}:", "destroy-unattached", "off")

                rebuild_panel_tmux_session(
                    panel_name,
                    2,
                    2,
                    {1: base_a, 2: base_b, 3: base_c},
                    layout_key="tall_left",
                )

                before = run_tmux("list-sessions", "-F", "#{session_name}").stdout.splitlines()
                assert not any("orphaned" in name for name in before), (
                    f"Initial build leaked an orphan: {before}"
                )

                rebuild_panel_tmux_session(
                    panel_name,
                    2,
                    2,
                    {1: base_a, 2: base_b, 3: base_c},
                    layout_key="wide_bottom",
                )

                after = run_tmux("list-sessions", "-F", "#{session_name}").stdout.splitlines()
                orphans = [name for name in after if "orphaned" in name]
                assert orphans == [], f"Reconfigure leaked orphan sessions: {orphans!r}"
                assert f"gd/panel/{panel_name}" in after, (
                    f"Reconfigure lost the panel session: {after}"
                )
                for s in (base_a, base_b, base_c):
                    assert s in after, f"Reconfigure killed inner session {s}"
            finally:
                subprocess.run(
                    ["tmux", "kill-server"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=TMUX_CMD_TIMEOUT,
                )
                _cleanup_tmux_tmpdir(tmux_dir)

    def test_three_session_two_row_layout_creates_no_orphan(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Mirror of the user's reported scenario: 3 sessions, 2 rows,
        first row 2 sessions — must not leave an orphan after reconfigure.
        """
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = _make_short_tmux_tmpdir()
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            suffix = uuid.uuid4().hex[:8]
            inner = [f"gd/browser-extension-template-{suffix}/opencode/{n}" for n in (1, 2, 3)]
            panel_name = "asd"

            def run_tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["tmux", *args],
                    capture_output=True,
                    text=True,
                    check=check,
                    timeout=TMUX_CMD_TIMEOUT,
                )

            try:
                for s in inner:
                    run_tmux("new-session", "-d", "-s", s, "-n", "w")
                    run_tmux("set-option", "-t", f"={s}:", "destroy-unattached", "off")

                rebuild_panel_tmux_session(
                    panel_name,
                    2,
                    2,
                    {1: inner[0], 2: inner[1], 3: inner[2]},
                    layout_key="tall_left",
                )

                after = run_tmux("list-sessions", "-F", "#{session_name}").stdout.splitlines()
                orphans = [name for name in after if "orphaned" in name]
                assert orphans == [], f"Orphan leaked: {orphans!r}"
                assert f"gd/panel/{panel_name}" in after
                for s in inner:
                    assert s in after, f"Inner session killed: {s}"
            finally:
                subprocess.run(
                    ["tmux", "kill-server"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=TMUX_CMD_TIMEOUT,
                )
                _cleanup_tmux_tmpdir(tmux_dir)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
class TestTempWrapperIntegration:
    def test_repo_discovery_and_tmux_config_ignore_temp_wrappers_when_wrappers_exist(
        self,
        tmp_path,
        monkeypatch,
    ):
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = _make_short_tmux_tmpdir()
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
                subprocess.run(
                    ["tmux", "kill-server"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=TMUX_CMD_TIMEOUT,
                )
                _cleanup_tmux_tmpdir(tmux_dir)
