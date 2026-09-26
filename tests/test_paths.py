"""Where GitDirector keeps its files."""

from __future__ import annotations

from pathlib import Path

from gitdirector import paths
from gitdirector.integrations.tmux import core


def test_everything_lives_under_one_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / ".gitdirector"
    assert paths.home_dir() == home
    assert paths.cache_dir() == home / "cache"
    assert paths.temp_dir() == home / "cache" / "temp"
    assert paths.lock_file("config") == home / "state" / "config.lock"


def test_gitdirector_home_moves_the_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("GITDIRECTOR_HOME", str(tmp_path / "gd"))
    assert paths.home_dir() == tmp_path / "gd"
    assert paths.cache_dir() == tmp_path / "gd" / "cache"


def test_the_override_reaches_session_panes(tmp_path, monkeypatch):
    assert paths.HOME_ENV_VAR not in core._tmux_child_env()
    monkeypatch.setenv("GITDIRECTOR_HOME", str(tmp_path))
    assert core._tmux_child_env()[paths.HOME_ENV_VAR] == str(tmp_path)


def test_old_layout_files_are_removed_from_the_default_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / ".gitdirector"
    (home / "terminfo" / "78").mkdir(parents=True)
    for name in ("config.lock", "version_check.yaml", "tmux_design.conf", "sidebar.log"):
        (home / name).write_text("")
    (home / "config.yaml").write_text("repositories: []\n")

    paths.remove_legacy_files()

    assert sorted(p.name for p in home.iterdir()) == ["config.yaml"]


def test_an_overridden_folder_is_never_cleaned(tmp_path, monkeypatch):
    monkeypatch.setenv("GITDIRECTOR_HOME", str(tmp_path))
    (tmp_path / "sidebar.log").write_text("")

    paths.remove_legacy_files()

    assert (tmp_path / "sidebar.log").exists()
