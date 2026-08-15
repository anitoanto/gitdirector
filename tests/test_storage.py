"""Tests for ``gitdirector.storage`` — path normalization, atomic writes, and locks.

These target real durability and concurrency concerns that the original test
suite didn't cover:

* ``~`` expansion and ``.``/``..`` resolution in ``normalize_repository_path``
* ``atomic_write_text`` cleans up its temp file on failure
* the advisory file lock is actually exclusive across threads
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from gitdirector.storage import (
    advisory_file_lock,
    atomic_write_text,
    normalize_repository_path,
)

# ---------------------------------------------------------------------------
# normalize_repository_path
# ---------------------------------------------------------------------------


class TestNormalizeRepositoryPath:
    def test_absolute_path_unchanged(self, tmp_path):
        p = tmp_path.resolve()
        assert normalize_repository_path(p) == p

    def test_relative_path_made_absolute(self):
        result = normalize_repository_path(Path("."))
        assert result.is_absolute()

    def test_tilde_expanded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = normalize_repository_path(Path("~/sub/dir"))
        assert result == (tmp_path / "sub" / "dir").resolve()

    def test_tilde_only_expands_to_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = normalize_repository_path(Path("~"))
        assert result == tmp_path.resolve()

    def test_already_normalized_path_is_idempotent(self, tmp_path):
        once = normalize_repository_path(tmp_path)
        twice = normalize_repository_path(once)
        assert once == twice

    def test_path_with_dotdot_components_resolved(self, tmp_path):
        weird = tmp_path / "a" / ".." / "b" / "." / "c"
        result = normalize_repository_path(weird)
        # `a/../b/./c` collapses to `b/c` under tmp_path.
        assert result == (tmp_path / "b" / "c").resolve()

    def test_string_path_accepted(self, tmp_path):
        """The public contract accepts both Path and string-like values."""
        result = normalize_repository_path(str(tmp_path / "foo"))
        assert result == (tmp_path / "foo").resolve()


# ---------------------------------------------------------------------------
# atomic_write_text
# ---------------------------------------------------------------------------


class TestAtomicWriteText:
    def test_writes_content_to_destination(self, tmp_path):
        target = tmp_path / "out.txt"
        atomic_write_text(target, "hello")
        assert target.read_text() == "hello"

    def test_no_temp_files_left_on_success(self, tmp_path):
        target = tmp_path / "out.txt"
        atomic_write_text(target, "data")
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".out.txt.")]
        assert leftovers == []

    def test_no_temp_files_left_on_failure(self, tmp_path, monkeypatch):
        """If writing fails mid-way, the temp file must be cleaned up.

        Simulates a disk-full scenario by raising from ``os.fsync`` on the
        first call. Without the cleanup branch the temp file would survive and
        pollute the directory (and on a real failure, leak into the user's
        next run).
        """
        target = tmp_path / "out.txt"
        call_count = {"n": 0}

        real_fsync = os.fsync

        def fail_fsync(fd):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("simulated disk full")
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", fail_fsync)
        with pytest.raises(OSError, match="simulated disk full"):
            atomic_write_text(target, "data")

        # Target must not have been moved into place.
        assert not target.exists()
        # Temp file must have been cleaned up.
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".out.txt.")]
        assert leftovers == [], f"temp file leaked: {leftovers}"

    def test_overwrites_existing_file_atomically(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("old")
        atomic_write_text(target, "new")
        assert target.read_text() == "new"

    def test_creates_parent_directory(self, tmp_path):
        target = tmp_path / "nested" / "deeper" / "out.txt"
        atomic_write_text(target, "hi")
        assert target.read_text() == "hi"


# ---------------------------------------------------------------------------
# advisory_file_lock — actual blocking behaviour
# ---------------------------------------------------------------------------


class TestAdvisoryFileLockExclusivity:
    def test_second_writer_blocks_until_first_releases(self, tmp_path):
        """Two threads acquiring the same lock must serialize, not interleave.

        Without the lock, concurrent `link` and `unlink` CLI invocations could
        race on the config file and lose a repository entry. This test guards
        the actual blocking semantics on POSIX (the test suite is Linux/macOS
        only; the Windows branch uses a different API and is exercised on CI).
        """
        lock_path = tmp_path / "lock"
        order: list[str] = []
        first_acquired = threading.Event()
        second_attempting = threading.Event()
        first_can_release = threading.Event()

        def first():
            with advisory_file_lock(lock_path):
                order.append("first-acquired")
                first_acquired.set()
                assert first_can_release.wait(timeout=30)
                order.append("first-released")

        def second():
            assert first_acquired.wait(timeout=30)
            second_attempting.set()
            with advisory_file_lock(lock_path):
                order.append("second-acquired")

        t1 = threading.Thread(target=first)
        t2 = threading.Thread(target=second)
        t1.start()
        t2.start()
        try:
            assert first_acquired.wait(timeout=30)
            assert second_attempting.wait(timeout=30)
            assert order == ["first-acquired"]
        finally:
            first_can_release.set()
            t1.join(timeout=30)
            t2.join(timeout=30)
        assert not t1.is_alive()
        assert not t2.is_alive()
        assert order == ["first-acquired", "first-released", "second-acquired"]

    def test_lock_file_is_created_on_first_acquire(self, tmp_path):
        lock_path = tmp_path / "subdir" / "lock"
        # Parent dir doesn't exist yet — the lock helper must create it.
        with advisory_file_lock(lock_path):
            assert lock_path.parent.is_dir()
            assert lock_path.exists()
