"""Where GitDirector keeps its files: one folder, ``~/.gitdirector`` by default.

* the folder itself: what the user sets (``config.yaml``, ``secrets.yaml``,
  ``panels.yaml``)
* ``cache/``: whatever is rebuilt on demand, ``cache/temp/`` for files that
  live only while a command runs
* ``state/``: lock files

``GITDIRECTOR_HOME`` moves the whole folder. Nothing else on disk is written.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

HOME_ENV_VAR = "GITDIRECTOR_HOME"

# What older versions kept at the top of the folder, now under cache/ or state/.
_LEGACY_FILES = (
    "config.lock",
    "panels.lock",
    "version_check.lock",
    "version_check.yaml",
    "tmux_design.conf",
    "sidebar.log",
)
_LEGACY_DIRS = ("terminfo",)
_OWN_ENTRIES = ("config.yaml", "secrets.yaml", "panels.yaml", "cache", "state")


def home_override() -> str | None:
    value = os.environ.get(HOME_ENV_VAR, "").strip()
    return value or None


def home_dir() -> Path:
    override = home_override()
    if override:
        return Path(os.path.abspath(os.path.expanduser(override)))
    return Path.home() / ".gitdirector"


def cache_dir() -> Path:
    return home_dir() / "cache"


def temp_dir() -> Path:
    return cache_dir() / "temp"


def state_dir() -> Path:
    return home_dir() / "state"


def lock_file(name: str) -> Path:
    return state_dir() / f"{name}.lock"


def remove_legacy_files() -> None:
    """Drop the files older versions kept at the top of the folder."""
    if home_override():
        # Only the default folder ever had the old layout.
        return
    home = home_dir()
    for name in _LEGACY_FILES:
        try:
            (home / name).unlink(missing_ok=True)
        except OSError:
            pass
    for name in _LEGACY_DIRS:
        shutil.rmtree(home / name, ignore_errors=True)


def remove_all() -> None:
    """Delete everything GitDirector keeps, and the folder once it is empty.

    Only GitDirector's own entries go, so a ``GITDIRECTOR_HOME`` pointed at
    a folder that holds anything else leaves that alone.
    """
    home = home_dir()
    if not home.exists():
        return
    remove_legacy_files()
    for name in _OWN_ENTRIES:
        path = home / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    # Leftovers of an interrupted atomic write.
    for leftover in home.glob(".*.tmp"):
        leftover.unlink(missing_ok=True)
    try:
        home.rmdir()
    except OSError:
        pass
