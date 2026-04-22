"""Repository file analysis with gitignore awareness."""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from functools import lru_cache
from itertools import islice
from pathlib import Path

import tiktoken

_GIT_LS_FILES_TIMEOUT = 30
_INFO_PENDING_MULTIPLIER = 2
_MAX_INFO_WORKERS = 8

_BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".tiff",
        ".tif",
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".mkv",
        ".flac",
        ".ogg",
        ".aac",
        ".wma",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".zst",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".o",
        ".a",
        ".lib",
        ".pyc",
        ".pyo",
        ".class",
        ".wasm",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".eot",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".bin",
        ".dat",
        ".img",
        ".iso",
        ".jar",
        ".war",
        ".ear",
        ".pak",
        ".deb",
        ".rpm",
        ".dmg",
        ".msi",
    }
)


@dataclass
class FileTypeInfo:
    extension: str
    count: int
    line_count: int | None
    token_count: int | None


@dataclass
class RepoInfoResult:
    total_files: int
    file_types: list[FileTypeInfo]
    total_lines: int
    total_tokens: int
    max_depth: int


def _get_non_ignored_files(repo_path: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            cwd=str(repo_path),
            timeout=_GIT_LS_FILES_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"git ls-files timed out after {_GIT_LS_FILES_TIMEOUT}s for {repo_path}"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError("git not found") from exc
    except OSError as exc:
        raise RuntimeError(f"git ls-files failed for {repo_path}: {exc}") from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or f"git ls-files failed for {repo_path}")
    return [f for f in result.stdout.decode("utf-8", errors="replace").split("\0") if f]


def _read_text(file_path: Path) -> str | None:
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(8192)
            if b"\x00" in chunk:
                return None
            remaining = f.read()
            if b"\x00" in remaining:
                return None
            return (chunk + remaining).decode("utf-8", errors="replace")
    except (OSError, PermissionError):
        return None


def _count_lines_from_text(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _count_lines(file_path: Path) -> int | None:
    text = _read_text(file_path)
    if text is None:
        return None
    return _count_lines_from_text(text)


@lru_cache(maxsize=1)
def _get_encoder():
    return tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    encoder = _get_encoder()
    try:
        return len(encoder.encode_ordinary(text))
    except AttributeError:
        return len(encoder.encode(text, disallowed_special=()))


def _info_worker_count(total_files: int) -> int:
    cpu_count = os.cpu_count() or 1
    return max(1, min(total_files, cpu_count, _MAX_INFO_WORKERS))


def _process_file(repo_path: Path, rel_path: str) -> tuple[str, str, int | None, int | None]:
    _, ext = os.path.splitext(rel_path)
    ext = ext.lower() if ext else "(no ext)"

    if ext != "(no ext)" and ext in _BINARY_EXTENSIONS:
        return rel_path, ext, None, None

    text = _read_text(repo_path / rel_path)
    if text is None:
        return rel_path, ext, None, None

    lines = _count_lines_from_text(text)
    tokens = _count_tokens(text)
    return rel_path, ext, lines, tokens


def _iter_processed_files(repo_path: Path, files: list[str]):
    worker_count = _info_worker_count(len(files))
    pending_limit = worker_count * _INFO_PENDING_MULTIPLIER
    file_iter = iter(files)

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        pending = {
            pool.submit(_process_file, repo_path, rel_path): rel_path
            for rel_path in islice(file_iter, pending_limit)
        }

        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future, None)
                yield future.result()

            while len(pending) < pending_limit:
                try:
                    rel_path = next(file_iter)
                except StopIteration:
                    break
                pending[pool.submit(_process_file, repo_path, rel_path)] = rel_path


def gather_repo_info(repo_path: Path, *, full: bool = False) -> RepoInfoResult:
    files = _get_non_ignored_files(repo_path)
    total_files = len(files)

    if total_files == 0:
        return RepoInfoResult(
            total_files=0, file_types=[], total_lines=0, total_tokens=0, max_depth=0
        )

    _get_encoder()

    ext_counter: Counter[str] = Counter()
    ext_line_counts: dict[str, int] = {}
    ext_token_counts: dict[str, int] = {}
    ext_has_text: dict[str, bool] = {}
    max_depth = 0
    total_lines = 0
    total_tokens = 0

    for rel_path, ext, lines, tokens in _iter_processed_files(repo_path, files):
        depth = rel_path.count("/")
        if depth > max_depth:
            max_depth = depth

        ext_counter[ext] += 1

        if lines is not None and tokens is not None:
            total_lines += lines
            total_tokens += tokens
            ext_line_counts[ext] = ext_line_counts.get(ext, 0) + lines
            ext_token_counts[ext] = ext_token_counts.get(ext, 0) + tokens
            ext_has_text[ext] = True
        else:
            ext_has_text.setdefault(ext, False)

    sorted_exts = ext_counter.most_common()

    no_ext_entry = None
    regular_exts = []
    for ext, count in sorted_exts:
        if ext == "(no ext)":
            no_ext_entry = (ext, count)
        else:
            regular_exts.append((ext, count))

    if full:
        top_n = regular_exts
        others_list = []
    else:
        top_n = regular_exts[:10]
        others_list = regular_exts[10:]

    def _make_entry(ext: str, count: int) -> FileTypeInfo:
        if ext_has_text.get(ext, False):
            return FileTypeInfo(
                ext, count, ext_line_counts.get(ext, 0), ext_token_counts.get(ext, 0)
            )
        return FileTypeInfo(ext, count, None, None)

    file_types: list[FileTypeInfo] = [_make_entry(ext, count) for ext, count in top_n]
    file_types.sort(key=lambda ft: ft.line_count or 0, reverse=True)

    if no_ext_entry:
        file_types.append(_make_entry(*no_ext_entry))

    if others_list:
        others_count = sum(c for _, c in others_list)
        any_text = any(ext_has_text.get(ext, False) for ext, _ in others_list)
        if any_text:
            others_lines = sum(ext_line_counts.get(ext, 0) for ext, _ in others_list)
            others_tokens = sum(ext_token_counts.get(ext, 0) for ext, _ in others_list)
            file_types.append(FileTypeInfo("others", others_count, others_lines, others_tokens))
        else:
            file_types.append(FileTypeInfo("others", others_count, None, None))

    return RepoInfoResult(
        total_files=total_files,
        file_types=file_types,
        total_lines=total_lines,
        total_tokens=total_tokens,
        max_depth=max_depth,
    )
