"""Benchmark the diff viewer on a repository with a massive uncommitted diff.

Builds a real git repository (``--files`` changed files, about ``--adds``
added and ``--dels`` removed lines), opens the console's Review Diff screen
headlessly and times what a user feels: the load, each keypress moving
through the file list, paging, and scrolling the diff. Prints one JSON
report and writes it to ``--out``.

    uv run python stress/bench_diff.py --files 800 --adds 30000 --dels 12000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

EXTENSIONS = (".py", ".js", ".ts", ".md", ".json", ".go", ".rs", ".css", ".yaml")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def build_repo(root: Path, files: int, adds: int, dels: int, seed: int) -> Path:
    """A committed tree, then a working tree that differs from it as asked."""
    rng = random.Random(seed)
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "bench@example.com")
    _git(repo, "config", "user.name", "bench")
    weights = [rng.paretovariate(1.2) for _ in range(files)]
    total = sum(weights)
    plans = []
    for index, weight in enumerate(weights):
        path = repo / f"src/pkg{index % 37}/module_{index}{rng.choice(EXTENSIONS)}"
        path.parent.mkdir(parents=True, exist_ok=True)
        removed = int(dels * weight / total)
        added = max(1, int(adds * weight / total))
        keep = rng.randint(20, 200)
        original = [f"v{n} = f({n})" for n in range(keep + removed)]
        path.write_text("\n".join(original) + "\n")
        plans.append((path, original, removed, added, index))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    for path, original, removed, added, index in plans:
        lines = list(original)
        for _ in range(removed):
            del lines[rng.randrange(len(lines))]
        for n in range(added):
            lines.insert(
                rng.randrange(len(lines) + 1),
                f"    n{n} = g({rng.randint(0, 999)})",
            )
        path.write_text("\n".join(lines) + "\n")
    return repo


async def measure(repo: Path, keys: int) -> dict:
    from gitdirector.integrations.tmux import TmuxMonitor

    report: dict = {}
    # The console's session monitor is not what is measured, and it must
    # never reach a tmux server the benchmark does not own.
    with patch.object(TmuxMonitor, "start"), patch.object(TmuxMonitor, "stop"):
        return await _measure(report, repo, keys)


async def _measure(report: dict, repo: Path, keys: int) -> dict:
    from gitdirector.commands.tui import GitDirectorConsole
    from gitdirector.commands.tui.screens.diff import DiffReviewScreen
    from gitdirector.commands.tui.screens.diff_files import FileTileList

    app = GitDirectorConsole()
    app.manager = MagicMock()
    app.manager.config.repositories = []
    app.manager.config.repository_cache_token.return_value = {}
    app.manager.config.reload_if_changed.return_value = False
    app.manager.config.max_workers = 2
    app.manager.config.theme = "rose-pine"
    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.pause()
        screen = DiffReviewScreen(repo.name, repo, branch="main")
        started = time.perf_counter()
        app.push_screen(screen)
        while screen._loading:
            await pilot.pause(0.05)
        await pilot.pause()
        report["load_seconds"] = round(time.perf_counter() - started, 3)
        report["files"] = len(screen._files)
        report["additions"] = sum(f.additions for f in screen._files)
        report["deletions"] = sum(f.deletions for f in screen._files)

        async def per_key(label: str, key: str, count: int) -> None:
            started = time.perf_counter()
            for _ in range(count):
                await pilot.press(key)
            await pilot.pause()
            report[label] = round((time.perf_counter() - started) / count * 1000, 1)

        await per_key("next_file_ms", "j", keys)
        await per_key("prev_file_ms", "k", keys)
        await per_key("page_ms", "J", keys)
        files_list = app.screen.query_one("#diff-files-list", FileTileList)
        started = time.perf_counter()
        files_list.index = len(screen._files) - 1
        await pilot.pause()
        report["jump_to_last_ms"] = round((time.perf_counter() - started) * 1000, 1)
        await pilot.press("tab")
        await pilot.pause()
        await per_key("scroll_diff_ms", "j", keys)
        await per_key("scroll_diff_page_ms", "J", keys)
        await pilot.press("escape")
        await pilot.pause()
    report["max_rss_mb"] = round(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        / (1024 * 1024 if sys.platform == "darwin" else 1024),
        1,
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--files", type=int, default=800)
    parser.add_argument("--adds", type=int, default=30000)
    parser.add_argument("--dels", type=int, default=12000)
    parser.add_argument("--keys", type=int, default=20, help="presses per timed key")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path, default=None)
    opts = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="gd-bench-") as tmp:
        started = time.perf_counter()
        repo = build_repo(Path(tmp), opts.files, opts.adds, opts.dels, opts.seed)
        build_seconds = round(time.perf_counter() - started, 1)
        report = asyncio.run(measure(repo, opts.keys))
    report["build_repo_seconds"] = build_seconds
    text = json.dumps(report, indent=2)
    print(text)
    if opts.out:
        opts.out.parent.mkdir(parents=True, exist_ok=True)
        opts.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
