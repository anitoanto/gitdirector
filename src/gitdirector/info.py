"""Repository file analysis with gitignore awareness."""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import tiktoken

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
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        cwd=str(repo_path),
        timeout=30,
    )
    if result.returncode != 0:
        return []
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


_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def _count_tokens(text: str) -> int:
    encoder = _get_encoder()
    try:
        return len(encoder.encode_ordinary(text))
    except AttributeError:
        return len(encoder.encode(text, disallowed_special=()))


def gather_repo_info(repo_path: Path, *, full: bool = False) -> RepoInfoResult:
    files = _get_non_ignored_files(repo_path)
    total_files = len(files)

    if total_files == 0:
        return RepoInfoResult(
            total_files=0, file_types=[], total_lines=0, total_tokens=0, max_depth=0
        )

    ext_counter: Counter[str] = Counter()
    ext_line_counts: dict[str, int] = {}
    ext_token_counts: dict[str, int] = {}
    ext_has_text: dict[str, bool] = {}
    max_depth = 0
    total_lines = 0
    total_tokens = 0

    for rel_path in files:
        depth = rel_path.count("/")
        if depth > max_depth:
            max_depth = depth

        _, ext = os.path.splitext(rel_path)
        ext = ext.lower() if ext else "(no ext)"
        ext_counter[ext] += 1

        if ext != "(no ext)" and ext in _BINARY_EXTENSIONS:
            ext_has_text.setdefault(ext, False)
            continue

        full_path = repo_path / rel_path
        text = _read_text(full_path)
        if text is not None:
            lines = _count_lines_from_text(text)
            tokens = _count_tokens(text)
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
