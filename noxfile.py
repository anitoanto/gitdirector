"""Nox sessions for GitDirector.

Run with ``uv run nox -s <session>``.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
from pathlib import Path

import nox

# Sessions here operate on the checkout itself, so nox should not build a
# virtualenv for them -- `uv run` already supplies the environment.
nox.options.default_venv_backend = "none"

ROOT = Path(__file__).parent

# Never descend into these. Deleting __pycache__ inside .venv would corrupt the
# environment, and .git is not ours to touch.
PRUNE_DIRS = frozenset(
    {".git", ".venv", "venv", ".env", ".direnv", "node_modules", "site-packages"}
)

# Directories removed wholesale when their name matches.
CACHE_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".pytype",
        ".hypothesis",
        ".tox",
        "htmlcov",
        "build",
        "dist",
        "wheels",
    }
)

CACHE_DIR_GLOBS = ("*.egg-info",)

CACHE_FILE_GLOBS = (
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".coverage",
    ".coverage.*",
    "coverage.xml",
    "coverage.json",
    ".DS_Store",
)


def _is_cache_dir(name: str) -> bool:
    return name in CACHE_DIR_NAMES or any(fnmatch.fnmatch(name, p) for p in CACHE_DIR_GLOBS)


def _is_cache_file(name: str) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in CACHE_FILE_GLOBS)


def _find_targets(root: Path) -> tuple[list[Path], list[Path]]:
    """Return (directories, files) to delete, walking the tree once."""
    dirs: list[Path] = []
    files: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]

        matched = [d for d in dirnames if _is_cache_dir(d)]
        dirs.extend(Path(dirpath) / d for d in matched)
        # Already being removed wholesale -- no reason to walk inside them.
        dirnames[:] = [d for d in dirnames if d not in matched]

        files.extend(Path(dirpath) / f for f in filenames if _is_cache_file(f))

    return dirs, files


@nox.session(python=False)
def clean(session: nox.Session) -> None:
    """Remove caches, coverage data, and build artifacts from the whole project."""
    dirs, files = _find_targets(ROOT)

    for path in files:
        path.unlink(missing_ok=True)
    for path in dirs:
        shutil.rmtree(path, ignore_errors=True)

    if not dirs and not files:
        session.log("Nothing to clean.")
        return

    for path in sorted(dirs):
        session.log(f"removed {path.relative_to(ROOT)}/")
    session.log(f"Cleaned {len(dirs)} directories and {len(files)} files.")
