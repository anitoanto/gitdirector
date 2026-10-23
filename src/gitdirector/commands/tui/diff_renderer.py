"""Diff rendering helpers for the TUI ``Review Diff`` screen.

This module owns every concern related to turning a raw ``git diff`` payload
into something a human can read in the terminal:

* Parsing unified-diff text into per-file metadata
  (path, status, additions, deletions, rename info, binary flag).
* Mapping a file path to a Pygments lexer so syntax highlighting is accurate.
* Building a richly styled ``rich.syntax.Syntax`` renderable for the right-hand
  diff panel, including line numbers and theme-aware colours.
* Building the compact one-line summary used in the left-hand file list.

Keeping this logic in its own module means the screen class stays focused on
layout, keybindings, and the threading model. The renderer is pure-Python and
fully unit-testable without booting Textual.
"""

from __future__ import annotations

import re
from array import array
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import accumulate
from pathlib import PurePosixPath

from pygments.lexer import Lexer
from pygments.lexers import get_lexer_by_name, guess_lexer_for_filename
from pygments.style import Style
from pygments.token import (
    Comment,
    Generic,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Text,
    Whitespace,
)
from pygments.util import ClassNotFound
from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.style import Style as RichStyle
from rich.syntax import PygmentsSyntaxTheme
from rich.text import Span
from rich.text import Text as RichText

_STATUS_LABEL: dict[str, str] = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "T": "type change",
    "U": "unmerged",
    "X": "unknown",
    "B": "broken pairing",
    "?": "untracked",
}


# ---------------------------------------------------------------------------
# GitHub-style palette
# ---------------------------------------------------------------------------
#
# Colours chosen to match GitHub's dark web UI as closely as a 256/truecolor
# terminal allows. The "added" and "removed" tones are intentionally muted
# (similar to a comfortable code review) rather than the eye-burning defaults
# of monokai. The bar/header background uses the TUI's $panel tone so the
# diff blends with the rest of the modal.
GITHUB_DARK_BG = "#0d1117"
GITHUB_DARK_SURFACE = "#161b22"
GITHUB_DARK_GUTTER = "#6e7681"
GITHUB_DARK_TEXT = "#c9d1d9"
GITHUB_DARK_MUTED = "#8b949e"
GITHUB_DARK_HEADING = "#79c0ff"
GITHUB_DARK_ADDED_BG = "#1a3a24"
GITHUB_DARK_ADDED_FG = "#aff5b4"
GITHUB_DARK_ADDED_PANEL_BG = "#0a1f12"
GITHUB_DARK_REMOVED_BG = "#3d1a20"
GITHUB_DARK_REMOVED_FG = "#ffdcd7"
GITHUB_DARK_REMOVED_PANEL_BG = "#1f0a0d"
GITHUB_DARK_HUNK_BG = "#1f6feb33"

# Status pill colours (used in the file list)
# The status letter as text on the dark diff background, for headers and
# the file list; the pill colours above are too dark to read that way.
STATUS_TEXT: dict[str, str] = {
    "A": "#3fb950",
    "M": "#d29922",
    "D": "#f85149",
    "R": "#58a6ff",
    "C": "#58a6ff",
    "?": "#bc8cff",
    "U": "#f85149",
}


def status_letter(status: str) -> RichText:
    label = "U" if status == "?" else (status[:1].upper() or "\u00b7")
    return RichText(label, style=f"bold {STATUS_TEXT.get(status, GITHUB_DARK_MUTED)}")


class GithubDarkStyle(Style):
    """Custom Pygments style mirroring GitHub's dark diff view.

    The diff lexer produces ``Generic.Heading`` (the ``diff --git`` line),
    ``Generic.Subheading`` (hunk ``@@`` markers), ``Generic.Inserted`` /
    ``Generic.Deleted`` for the +/- lines, and plain ``Text`` for context.
    Code content (when a file-specific lexer is used) inherits from
    ``DefaultStyle`` so the file's syntax colours still read clearly against
    the dark background.
    """

    background_color = GITHUB_DARK_BG

    styles = {
        # Default text and whitespace
        Text: GITHUB_DARK_TEXT,
        Whitespace: GITHUB_DARK_TEXT,
        # Diff heading / subheading
        Generic.Heading: f"bold {GITHUB_DARK_HEADING}",
        Generic.Subheading: f"bold {GITHUB_DARK_HEADING}",
        # Markdown / rst / similar: **bold**, *em*, and `code` would
        # otherwise fall through to Pygments' default style and render
        # as black on our dark background. Force them to legible tones.
        Generic.Strong: f"bold {GITHUB_DARK_TEXT}",
        Generic.Emph: f"italic {GITHUB_DARK_TEXT}",
        Generic.EmphStrong: f"bold italic {GITHUB_DARK_TEXT}",
        # Shell session output and prompts default to dark navy/grey
        # in Pygments' default style, both invisible on our dark bg.
        Generic.Output: GITHUB_DARK_MUTED,
        Generic.Prompt: f"bold {GITHUB_DARK_HEADING}",
        Generic.Traceback: "#ffa198",
        # Inserted (added) lines
        Generic.Inserted: f"{GITHUB_DARK_ADDED_FG} bg:{GITHUB_DARK_ADDED_BG}",
        Generic.Deleted: f"{GITHUB_DARK_REMOVED_FG} bg:{GITHUB_DARK_REMOVED_BG}",
        # Inside an inserted/deleted line, the line's lexer still emits
        # Operator / Punctuation / Keyword tokens. We do NOT want those
        # rules to win over the line-level background, so we leave them
        # at the default Text colour and let the line's bg come through.
        Punctuation: GITHUB_DARK_TEXT,
        Operator: GITHUB_DARK_TEXT,
        # Comments (hunk markers, etc.)
        Comment: f"italic {GITHUB_DARK_MUTED}",
        # Code-aware defaults (used for context lines and for any
        # lexer-recognised tokens inside a coloured line).
        Keyword: "#ff7b72",
        Name: GITHUB_DARK_TEXT,
        Name.Function: "#d2a8ff",
        Name.Class: "#ffa657",
        Name.Builtin: "#79c0ff",
        String: "#a5d6ff",
        Number: "#79c0ff",
    }


@dataclass(frozen=True)
class ChangedFile:
    """One file in a diff, with metadata for the file list and diff panel."""

    path: str
    status: str
    additions: int = 0
    deletions: int = 0
    is_binary: bool = False
    is_image: bool = False
    is_rename: bool = False
    old_path: str | None = None
    diff_text: str = ""
    first_new_line: int | None = None
    last_new_line: int | None = None

    @property
    def status_label(self) -> str:
        return _STATUS_LABEL.get(self.status, "changed")

    @property
    def is_untracked(self) -> bool:
        return self.status == "?"

    @property
    def display_path(self) -> str:
        if self.is_rename and self.old_path:
            return f"{self.old_path} \u2192 {self.path}"
        return self.path


@dataclass
class DiffBundle:
    """Result of parsing a raw ``git diff`` payload.

    ``files`` is the per-file metadata that powers the left-hand list.
    ``raw`` is the original (possibly empty) diff text so we can show a
    'no changes' placeholder when the working tree is clean.
    """

    files: list[ChangedFile] = field(default_factory=list)
    raw: str = ""


_BINARY_RE = re.compile(r"^Binary files .* differ$")
# Used with ``match``, which anchors it, also at a line's offset in a text.
_HUNK_HEADER_RE = re.compile(
    r"@@[^\S\n]+-(?P<old>\d+)(?:,\d+)?[^\S\n]+\+(?P<new>\d+)(?:,\d+)?[^\S\n]+@@"
)
_NO_NEWLINE_MARKER = r"\ No newline at end of file"

_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
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
    }
)


def _closing_quote(text: str, start: int) -> int | None:
    """Index of the quote closing the quoted token that opens at *start*."""
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i
        i += 1
    return None


def _unquote_git_path(token: str) -> str:
    if len(token) >= 2 and token.startswith('"') and token.endswith('"'):
        return _unescape_git_path(token[1:-1])
    return token


def _strip_side_prefix(path: str, prefix: str) -> str:
    return path[len(prefix) :] if path.startswith(prefix) else path


def _split_header_tokens(rest: str) -> tuple[str, str] | None:
    """Split a ``diff --git`` payload into its old and new path tokens.

    git quotes each side on its own, so either, both, or neither may be
    quoted. Two unquoted sides are split in the middle when they name the
    same path, which is right even for a path containing `` b/``.
    """
    if rest.startswith('"'):
        end = _closing_quote(rest, 0)
        if end is None or rest[end + 1 : end + 2] != " ":
            return None
        return rest[: end + 1], rest[end + 2 :]
    if rest.endswith('"'):
        start = rest.find(' "')
        if start == -1:
            return None
        return rest[:start], rest[start + 1 :]
    middle = len(rest) // 2
    if len(rest) % 2 and rest[middle] == " " and rest[2:middle] == rest[middle + 3 :]:
        return rest[:middle], rest[middle + 1 :]
    sep = rest.find(" b/")
    if sep == -1:
        return None
    return rest[:sep], rest[sep + 1 :]


def _parse_diff_git_paths(line: str) -> tuple[str | None, str | None]:
    """Extract the old and new paths from a ``diff --git`` header line.

    Returns ``(None, None)`` when the line is not a well-formed header.
    """
    if not line.startswith("diff --git "):
        return None, None
    tokens = _split_header_tokens(line[len("diff --git ") :])
    if tokens is None:
        return None, None
    old_token, new_token = tokens
    return (
        _strip_side_prefix(_unquote_git_path(old_token), "a/"),
        _strip_side_prefix(_unquote_git_path(new_token), "b/"),
    )


def _patch_line_path(value: str, prefix: str) -> str | None:
    """Path from a ``---``/``+++`` value, or None for ``/dev/null``."""
    # git appends a tab to a name containing a space.
    value = value.rstrip("\t")
    if value == "/dev/null":
        return None
    return _strip_side_prefix(_unquote_git_path(value), prefix)


def _unescape_git_path(raw: str) -> str:
    """Decode git's quoted-pathway: ``\\\\`` → ``\\``, ``\\t`` → ``\\t``,
    ``\\"`` → ``"``.

    Git also wraps ``\\a``, ``\\b``, ``\\v``, ``\\f``, ``\\"``, ``\\``
    etc. We only reverse the cases we are likely to encounter in practice;
    the result is good enough to render filenames in the diff view.
    """
    result = bytearray()
    escapes = {
        "a": b"\a",
        "b": b"\b",
        "t": b"\t",
        "n": b"\n",
        "v": b"\v",
        "f": b"\f",
        "r": b"\r",
        '"': b'"',
        "\\": b"\\",
    }
    i = 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt in "01234567" and i + 3 < len(raw):
                octal = raw[i + 1 : i + 4]
                if all(char in "01234567" for char in octal):
                    result.append(int(octal, 8))
                    i += 4
                    continue
            if nxt in escapes:
                result.extend(escapes[nxt])
            else:
                result.extend(raw[i : i + 2].encode())
            i += 2
        else:
            result.extend(raw[i].encode())
            i += 1
    return result.decode("utf-8", "surrogateescape")


def is_image_file(path: str) -> bool:
    """Return ``True`` if ``path`` has a raster-image extension.

    Used to decide whether the diff viewer should suppress the right-hand
    diff/preview pane entirely for a file. SVG is intentionally excluded
    because it's text-based and the regular diff renderer handles it fine.
    """
    if not path:
        return False
    name = PurePosixPath(path).name.lower()
    if "." not in name:
        return False
    return "." + name.rsplit(".", 1)[1] in _IMAGE_EXTENSIONS


_PYGMENTS_LANG_OVERRIDES: dict[str, str] = {
    "py": "python",
    "js": "javascript",
    "jsx": "jsx",
    "ts": "typescript",
    "tsx": "tsx",
    "rb": "ruby",
    "rs": "rust",
    "go": "go",
    "java": "java",
    "kt": "kotlin",
    "swift": "swift",
    "c": "c",
    "h": "c",
    "cpp": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "hh": "cpp",
    "cs": "csharp",
    "php": "php",
    "sh": "bash",
    "bash": "bash",
    "zsh": "bash",
    "yaml": "yaml",
    "yml": "yaml",
    "json": "json",
    "toml": "toml",
    "md": "markdown",
    "markdown": "markdown",
    "html": "html",
    "htm": "html",
    "css": "css",
    "scss": "scss",
    "sass": "sass",
    "sql": "sql",
    "xml": "xml",
    "vue": "vue",
    "svelte": "svelte",
    "lua": "lua",
    "pl": "perl",
    "r": "r",
    "dart": "dart",
    "ex": "elixir",
    "exs": "elixir",
    "erl": "erlang",
    "hs": "haskell",
    "scala": "scala",
    "clj": "clojure",
    "dockerfile": "dockerfile",
}


def detect_language(path: str) -> str | None:
    """Best-effort mapping of a file path to a Pygments lexer name."""
    if not path:
        return None
    name = PurePosixPath(path).name.lower()
    if not name:
        return None
    _, ext = (name, "")
    if "." in name:
        _, ext = name.rsplit(".", 1)
    override = _PYGMENTS_LANG_OVERRIDES.get(ext)
    if override:
        return override
    if name in {"dockerfile", "makefile", "rakefile", "gemfile"}:
        return _PYGMENTS_LANG_OVERRIDES.get(name, name)
    try:
        return guess_lexer_for_filename(name, "").aliases[0]
    except (ClassNotFound, IndexError):
        return None


def _split_lines(text: str) -> list[str]:
    """Split on ``\n`` only: ``str.splitlines`` also breaks on form feeds,
    ``\x1c``-``\x1e``, ``\x85`` and Unicode separators inside a line."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def _normalize_newlines(text: str) -> str:
    """Drop the ``\\r`` of every CRLF, as ``_split_lines`` does per line."""
    if "\r" not in text:
        return text
    text = text.replace("\r\n", "\n")
    return text[:-1] if text.endswith("\r") else text


def _line_offsets(text: str, prefix: str, start: int = 0, end: int | None = None) -> Iterator[int]:
    """Offsets of the lines in ``text[start:end]`` that begin with *prefix*.

    A substring search runs in C, so a diff of any size is scanned without
    a Python step per line.
    """
    end = len(text) if end is None else end
    if text.startswith(prefix, start, end):
        yield start
    needle = "\n" + prefix
    pos = text.find(needle, start, end)
    while pos != -1:
        yield pos + 1
        pos = text.find(needle, pos + 1, end)


def _count_lines_starting(text: str, prefix: str, start: int, end: int) -> int:
    return text.count("\n" + prefix, start, end) + text.startswith(prefix, start, end)


def _hunk_header_offsets(text: str, start: int = 0) -> list[tuple[int, re.Match[str]]]:
    headers = []
    for offset in _line_offsets(text, "@@", start):
        match = _HUNK_HEADER_RE.match(text, offset)
        if match:
            headers.append((offset, match))
    return headers


def _hunk_stats(chunk: str, hunks: list[tuple[int, re.Match[str]]]) -> tuple[int, int, int | None]:
    """Additions, deletions, and the new-side number of the last line shown.

    Every line in a hunk starts with its marker and a hunk header with "@@",
    so counting line starts, which runs in C, counts the changes exactly.
    """
    ends = [offset for offset, _ in hunks[1:]] + [len(chunk)]
    removed = [chunk.count("\n-", offset, end) for (offset, _), end in zip(hunks, ends)]
    last_new_line = None
    for (offset, match), end, hunk_removed in zip(
        reversed(hunks), reversed(ends), reversed(removed)
    ):
        body = chunk.find("\n", offset, end) + 1
        if body:
            shown = chunk.count("\n", body, end) - hunk_removed
            shown -= _count_lines_starting(chunk, "\\", body, end)
            if shown > 0:
                last_new_line = int(match["new"]) + shown - 1
                break
    return chunk.count("\n+", hunks[0][0]), sum(removed), last_new_line


def _file_chunks(diff_text: str) -> Iterator[str]:
    """Each file's part of a diff, from its ``diff --git`` line, newline-ended."""
    starts = list(_line_offsets(diff_text, "diff --git "))
    for start, end in zip(starts, [*starts[1:], len(diff_text)]):
        chunk = diff_text[start:end]
        yield chunk if chunk.endswith("\n") else chunk + "\n"


def _parse_file_chunk(chunk: str) -> ChangedFile | None:
    old_name, new_name = _parse_diff_git_paths(chunk[: chunk.find("\n")])
    if old_name is None or new_name is None:
        return None
    hunks = _hunk_header_offsets(chunk)
    body = hunks[0][0] if hunks else len(chunk)
    path, old_path, status, is_binary = new_name, old_name, "M", False
    for line in chunk[:body].split("\n")[1:]:
        if line.startswith("new file"):
            status = "A"
        elif line.startswith("deleted file"):
            status = "D"
        elif line.startswith(("rename from ", "copy from ")):
            verb, _, value = line.partition(" from ")
            status = "R" if verb == "rename" else "C"
            old_path = _unquote_git_path(value)
        elif line.startswith(("rename to ", "copy to ")):
            path = _unquote_git_path(line.partition(" to ")[2])
        elif line.startswith("--- "):
            side = _patch_line_path(line[4:], "a/")
            old_path = old_path if side is None else side
        elif line.startswith("+++ "):
            side = _patch_line_path(line[4:], "b/")
            path = path if side is None else side
        elif _BINARY_RE.match(line):
            is_binary = True
    is_rename = old_path is not None and old_path != path
    additions, deletions, last_new_line = _hunk_stats(chunk, hunks) if hunks else (0, 0, None)
    return ChangedFile(
        path=path,
        status=status,
        additions=additions,
        deletions=deletions,
        is_binary=is_binary,
        is_image=is_image_file(path),
        is_rename=is_rename,
        old_path=old_path if is_rename else None,
        diff_text=chunk,
        first_new_line=int(hunks[0][1]["new"]) if hunks else None,
        last_new_line=last_new_line,
    )


def parse_diff_files(diff_text: str) -> list[ChangedFile]:
    """Split a unified ``git diff`` payload into one ``ChangedFile`` per file."""
    files = []
    for chunk in _file_chunks(_normalize_newlines(diff_text)):
        file = _parse_file_chunk(chunk)
        if file is not None:
            files.append(file)
    return files


def _untracked_file(rel_path: str, text: str | None) -> ChangedFile:
    if text is None:
        return ChangedFile(
            path=rel_path,
            status="?",
            is_binary=True,
            is_image=is_image_file(rel_path),
            diff_text=(
                f"diff --git a/{rel_path} b/{rel_path}\n"
                f"new file mode 100644\n"
                f"Binary files /dev/null and b/{rel_path} differ"
            ),
        )
    text = _normalize_newlines(text)
    if text and not text.endswith("\n"):
        text += "\n"
    line_count = text.count("\n")
    # The hunk header is what marks the lines below it as the added body.
    diff_text = (
        f"diff --git a/{rel_path} b/{rel_path}\n"
        f"new file mode 100644\n"
        f"--- /dev/null\n"
        f"+++ b/{rel_path}\n"
        f"@@ -0,0 +1,{line_count} @@"
    )
    if text:
        diff_text += "\n+" + text[:-1].replace("\n", "\n+")
    return ChangedFile(
        path=rel_path,
        status="?",
        additions=line_count,
        is_image=is_image_file(rel_path),
        diff_text=diff_text,
    )


def build_diff_bundle(diff_text: str, untracked_paths: list[str], untracked_lookup) -> DiffBundle:
    """Combine a parsed diff with untracked file metadata.

    ``untracked_lookup`` is a callable ``(rel_path) -> str | None`` that
    returns the text content of an untracked file, or ``None`` if the file
    cannot be read (binary, missing).
    """
    files = parse_diff_files(diff_text)
    files.extend(_untracked_file(path, untracked_lookup(path)) for path in untracked_paths)
    return DiffBundle(files=files, raw=diff_text)


def render_file_diff(
    file: ChangedFile,
    *,
    width: int | None = None,
) -> RenderableType:
    """Build a richly-styled renderable for the right-hand panel.

    The layout matches GitHub's diff view: a header bar with status, path,
    line range and stats, then each hunk's ``@@`` line followed by its
    syntax-highlighted lines, with the old and new line numbers in the gutter.
    """
    pieces: list[RenderableType] = []

    pieces.append(_render_file_header(file))

    if file.is_image:
        return Group(*pieces)

    if file.is_binary:
        body = RichText(
            "\n  Binary file differs from HEAD.\n  Diff is not shown for binary files.\n",
            style="italic dim",
        )
        pieces.append(Padding(body, (1, 2)))
        return Group(*pieces)

    meta_lines, hunks = _split_diff_hunks(file.diff_text)
    if meta_lines:
        pieces.append(_render_diff_meta_lines(meta_lines))
    if hunks:
        pieces.append(_render_hunks(hunks, lexer=_file_lexer(file.path), width=width))
    return Group(*pieces)


def _split_diff_hunks(diff_text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Split a file's diff into its pre-hunk metadata and ``(header, lines)`` hunks."""
    meta_lines: list[str] = []
    hunks: list[tuple[str, list[str]]] = []
    for raw_line in _split_lines(diff_text):
        if _HUNK_HEADER_RE.match(raw_line):
            hunks.append((raw_line, []))
        elif not hunks:
            meta_lines.append(raw_line)
        elif not raw_line.startswith("\\"):
            hunks[-1][1].append(raw_line)
    return meta_lines, hunks


def _hunk_number_width(hunks: list[tuple[str, list[str]]]) -> int:
    highest = 1
    for header, lines in hunks:
        match = _HUNK_HEADER_RE.match(header)
        if match:
            span = len(lines)
            highest = max(highest, int(match["old"]) + span, int(match["new"]) + span)
    return len(str(highest))


def diff_gutter_width(file: ChangedFile) -> int:
    """Cells the line-number gutter and change marker take before the code."""
    _, hunks = _split_diff_hunks(file.diff_text)
    return 2 * _hunk_number_width(hunks) + 4


@lru_cache(maxsize=64)
def _lexer_for_language(name: str) -> Lexer | None:
    # Looking a lexer up costs ~15 ms; the instances are reusable.
    try:
        # stripnl would drop leading blank lines and misalign every row.
        return get_lexer_by_name(name, stripnl=False, ensurenl=True)
    except ClassNotFound:
        return None


def _file_lexer(path: str) -> Lexer | None:
    name = detect_language(path)
    return _lexer_for_language(name) if name else None


def warm_lexers(files: list[ChangedFile]) -> None:
    """Look up every language's lexer now, off the UI thread."""
    for name in {detect_language(file.path) for file in files}:
        if name:
            _lexer_for_language(name)


_SYNTAX_THEME = PygmentsSyntaxTheme(GithubDarkStyle)


@lru_cache(maxsize=None)
def _token_style(token_type) -> RichStyle:
    # Foreground only: the row decides the background.
    style = _SYNTAX_THEME.get_style_for_token(token_type)
    return RichStyle(color=style.color, bold=style.bold, italic=style.italic)


def _line_spans(lines: list[str], lexer: Lexer | None) -> list[list[Span]]:
    """Syntax spans per line, lexing *lines* as one block so multi-line
    constructs (docstrings, block comments) highlight correctly."""
    spans: list[list[Span]] = [[] for _ in lines]
    if lexer is None:
        return spans
    line_no = col = 0
    for token_type, value in lexer.get_tokens("\n".join(lines)):
        style = _token_style(token_type)
        for index, part in enumerate(value.split("\n")):
            if index:
                line_no += 1
                col = 0
            if part and line_no < len(spans):
                spans[line_no].append(Span(col, col + len(part), style))
            col += len(part)
    return spans


_LINE_BACKGROUND = {"+": GITHUB_DARK_ADDED_BG, "-": GITHUB_DARK_REMOVED_BG}
_MARKER_STYLE = {"+": "bold #3fb950", "-": "bold #f85149"}
_GUTTER_STYLE = RichStyle(color=GITHUB_DARK_GUTTER)
_HUNK_HEADER_STYLE = f"bold {GITHUB_DARK_HEADING} on #1f2d44"
_CAPTION_BACKGROUND = "on #161b22"


def _hunk_header_row(header: str) -> RichText:
    return RichText(f" {header} ", style=_HUNK_HEADER_STYLE)


def _hunk_start(header: str) -> tuple[int, int]:
    match = _HUNK_HEADER_RE.match(header)
    return (int(match["old"]), int(match["new"])) if match else (0, 0)


def _hunk_prefixes(header: str, lines: list[str], number_width: int) -> list[tuple[str, str]]:
    return _line_prefixes(lines, *_hunk_start(header), number_width)


def _line_prefixes(
    lines: list[str], old_no: int, new_no: int, number_width: int
) -> list[tuple[str, str]]:
    """``(marker, gutter text)`` per line: the old and new line numbers."""
    blank = " " * number_width
    prefixes: list[tuple[str, str]] = []
    for line in lines:
        marker = line[:1] if line[:1] in ("+", "-") else " "
        old_label = new_label = blank
        if marker != "+":
            old_label = f"{old_no:>{number_width}}"
            old_no += 1
        if marker != "-":
            new_label = f"{new_no:>{number_width}}"
            new_no += 1
        prefixes.append((marker, f"{old_label} {new_label} {marker} "))
    return prefixes


def _code_row(
    marker: str,
    prefix: str,
    content: str,
    spans: list[Span],
    gutter_width: int,
    pad_to: int | None,
) -> RichText:
    background = _LINE_BACKGROUND.get(marker)
    plain = prefix + content
    if background and pad_to:
        plain = plain.ljust(pad_to)
    row_spans = [Span(0, gutter_width - 2, _GUTTER_STYLE)]
    if marker in _MARKER_STYLE:
        row_spans.append(Span(gutter_width - 2, gutter_width - 1, _MARKER_STYLE[marker]))
    row_spans += [span.move(gutter_width) for span in spans]
    return RichText(plain, style=f"on {background}" if background else "", spans=row_spans)


def _render_hunks(
    hunks: list[tuple[str, list[str]]], *, lexer: Lexer | None, width: int | None
) -> RichText:
    """Render hunks with a gutter of real old/new line numbers."""
    number_width = _hunk_number_width(hunks)
    gutter_width = 2 * number_width + 4
    rows: list[RichText] = []
    for header, lines in hunks:
        rows.append(_hunk_header_row(header))
        code = [line[1:].expandtabs(4) for line in lines]
        prefixes = _hunk_prefixes(header, lines, number_width)
        for (marker, prefix), content, spans in zip(prefixes, code, _line_spans(code, lexer)):
            pad_to = width + gutter_width if width else None
            rows.append(_code_row(marker, prefix, content, spans, gutter_width, pad_to))
    return RichText("\n", no_wrap=True, overflow="crop").join(rows)


# Lines of a hunk highlighted together: scrolling into a new block lexes
# that block alone, however long the hunk is.
_BLOCK_LINES = 1000
# A block holding more text than this, like a minified bundle, is drawn
# plain: lexing it would stall the UI.
_HIGHLIGHT_MAX_CHARS = 256 * 1024
_BLOCK_CACHE_SIZE = 64


def _longest_line_cells(lines: list[str], text: str) -> int:
    if not lines:
        return 0
    if text.isascii():
        longest = max(map(len, lines))
        if "\t" in text:
            longest = max(longest, *(len(line.expandtabs(4)) for line in lines if "\t" in line))
        return longest
    return max(
        len(line) if line.isascii() and "\t" not in line else cell_len(line.expandtabs(4))
        for line in lines
    )


class _TextLines:
    """Random access to the lines of a text, with no ``str`` kept per line."""

    def __init__(self, text: str) -> None:
        self.text = text
        lines = text.split("\n")
        if lines[-1] == "":
            lines.pop()
        # A line's end offset, less the newlines before it.
        self._ends = array("q", accumulate(map(len, lines)))
        self.longest = _longest_line_cells(lines, text)

    def __len__(self) -> int:
        return len(self._ends)

    def start(self, index: int) -> int:
        return self._ends[index - 1] + index if index else 0

    def end(self, index: int) -> int:
        return self._ends[index] + index

    def __getitem__(self, index: int) -> str:
        line = self.text[self.start(index) : self.end(index)]
        return line[:-1] if line.endswith("\r") else line

    def span(self, first: int, last: int) -> list[str]:
        """Lines *first* through *last*."""
        text = self.text[self.start(first) : self.end(last)]
        lines = text.split("\n")
        if "\r" in text:
            lines = [line[:-1] if line.endswith("\r") else line for line in lines]
        return lines

    def index_of(self, offsets: list[int]) -> list[int]:
        """The line each of the ascending *offsets* falls on."""
        indices = []
        index = previous = 0
        for offset in offsets:
            index += self.text.count("\n", previous, offset)
            previous = offset
            indices.append(index)
        return indices


@dataclass
class _Block:
    code: list[str]
    prefixes: list[tuple[str, str]]
    spans: list[list[Span]]


class _Hunk:
    """A hunk's rows, built a block of lines at a time."""

    def __init__(self, header: str, first: int, end: int, skipped: list[int]) -> None:
        self.header = header
        self.first = first
        # "\ No newline at end of file" lines, which get no row.
        self.skipped = skipped
        self.size = end - first - len(skipped)
        # The old and new line numbers at the start of each block seen so far.
        self.numbers = [_hunk_start(header)]

    def line_index(self, row: int) -> int:
        index = self.first + row
        for skipped in self.skipped:
            if skipped > index:
                break
            index += 1
        return index

    def block_lines(self, block: int) -> tuple[int, int]:
        first_row = block * _BLOCK_LINES
        last_row = min(self.size, first_row + _BLOCK_LINES) - 1
        return self.line_index(first_row), self.line_index(last_row)

    def block_numbers(self, block: int, lines: _TextLines) -> tuple[int, int]:
        while len(self.numbers) <= block:
            done = len(self.numbers) - 1
            old_no, new_no = self.numbers[done]
            first, last = self.block_lines(done)
            start, end = lines.start(first), lines.end(last)
            rows = min(self.size - done * _BLOCK_LINES, _BLOCK_LINES)
            added = _count_lines_starting(lines.text, "+", start, end)
            removed = _count_lines_starting(lines.text, "-", start, end)
            self.numbers.append((old_no + rows - added, new_no + rows - removed))
        return self.numbers[block]


class DiffDocument:
    """A file's diff as lines drawn on demand.

    Only line offsets are indexed up front, so a file of any size opens at
    once; a row is built, and its block of lines syntax-highlighted, when
    the viewer first draws it.

    *width* is the widest line in cells, the gutter included.
    """

    def __init__(self, file: ChangedFile, *, code_width: int) -> None:
        self.file = file
        self._fixed = [_file_header_row(file)]
        self._lines: _TextLines | None = None
        self._lexer: Lexer | None = None
        self._meta_count = 0
        self._hunks: list[_Hunk] = []
        self._hunk_rows: list[int] = []
        self._blocks: dict[tuple[int, int], _Block] = {}
        self.gutter_width = 0
        if file.is_image or file.is_binary:
            if not file.is_image:
                for line in (
                    "",
                    "  Binary file differs from HEAD.",
                    "  Diff is not shown for binary files.",
                    "",
                ):
                    self._fixed.append(RichText(f"  {line}" if line else "", style="italic dim"))
            self._length = len(self._fixed)
            shown = _split_lines(file.diff_text) or [file.display_path]
            self.width = max(code_width, _longest_line_cells(shown, "\n".join(shown)))
            return

        lines = self._lines = _TextLines(file.diff_text)
        text = lines.text
        headers = _hunk_header_offsets(text)
        header_lines = lines.index_of([offset for offset, _ in headers])
        skipped_lines = lines.index_of(list(_line_offsets(text, "\\")))
        self._meta_count = header_lines[0] if headers else len(lines)
        row = len(self._fixed) + self._meta_count
        for at, end in zip(header_lines, [*header_lines[1:], len(lines)]):
            skipped = skipped_lines[
                bisect_right(skipped_lines, at) : bisect_right(skipped_lines, end - 1)
            ]
            hunk = _Hunk(lines[at], at + 1, end, skipped)
            self._hunks.append(hunk)
            self._hunk_rows.append(row)
            row += 1 + hunk.size
        self._length = row

        highest = max([1, *(max(_hunk_start(hunk.header)) + hunk.size for hunk in self._hunks)])
        self._number_width = len(str(highest))
        self.gutter_width = 2 * self._number_width + 4
        self._lexer = _file_lexer(file.path)
        self.width = max(code_width, lines.longest) + self.gutter_width

    def __len__(self) -> int:
        return self._length

    def line(self, index: int) -> RichText:
        if index < len(self._fixed):
            return self._fixed[index]
        assert self._lines is not None
        meta_index = index - len(self._fixed)
        if meta_index < self._meta_count:
            return _meta_row(self._lines[meta_index])
        position = bisect_right(self._hunk_rows, index) - 1
        offset = index - self._hunk_rows[position]
        hunk = self._hunks[position]
        if offset == 0:
            return _hunk_header_row(hunk.header)
        block_index, row = divmod(offset - 1, _BLOCK_LINES)
        block = self._block(position, block_index)
        marker, prefix = block.prefixes[row]
        return _code_row(marker, prefix, block.code[row], block.spans[row], self.gutter_width, None)

    def _block(self, position: int, block_index: int) -> _Block:
        key = (position, block_index)
        block = self._blocks.get(key)
        if block is not None:
            return block
        assert self._lines is not None
        hunk = self._hunks[position]
        raw = self._lines.span(*hunk.block_lines(block_index))
        if hunk.skipped:
            raw = [line for line in raw if not line.startswith("\\")]
        code = [line[1:].expandtabs(4) for line in raw]
        lexer = self._lexer if sum(map(len, code)) <= _HIGHLIGHT_MAX_CHARS else None
        block = _Block(
            code,
            _line_prefixes(raw, *hunk.block_numbers(block_index, self._lines), self._number_width),
            _line_spans(code, lexer),
        )
        if len(self._blocks) >= _BLOCK_CACHE_SIZE:
            del self._blocks[next(iter(self._blocks))]
        self._blocks[key] = block
        return block


def build_diff_document(file: ChangedFile, *, code_width: int) -> DiffDocument:
    """*code_width* is the narrowest the code column is drawn."""
    return DiffDocument(file, code_width=code_width)


def _meta_row(line: str) -> RichText:
    row = RichText("    ", style=_CAPTION_BACKGROUND)
    row.stylize("dim", 2, 4)
    if line.startswith("diff --git "):
        row.append(line, style=f"bold {GITHUB_DARK_HEADING}")
    else:
        row.append(line, style=f"italic {GITHUB_DARK_MUTED}")
    return row


def _file_header_row(file: ChangedFile) -> RichText:
    row = RichText("  ", style=_CAPTION_BACKGROUND)
    row.append_text(_file_header_text(file))
    return row


def _render_diff_meta_lines(lines: list[str]) -> RenderableType:
    """Render the pre-hunk metadata (``diff --git``, ``index``, ``---``, ...) as a small caption."""
    text = RichText()
    for i, line in enumerate(lines):
        if i:
            text.append("\n")
        text.append("  ", style="dim")
        if line.startswith("diff --git "):
            text.append(line, style=f"bold {GITHUB_DARK_HEADING}")
        else:
            text.append(line, style=f"italic {GITHUB_DARK_MUTED}")
    return Padding(text, (0, 2), style="on #161b22")


def _render_file_header(file: ChangedFile) -> RenderableType:
    """The file's header line: status letter, path, lines shown and counts."""
    return Padding(_file_header_text(file), (0, 2), style="on #161b22")


def _file_header_text(file: ChangedFile) -> RichText:
    text = RichText()
    text.append_text(status_letter(file.status))
    text.append("  ")
    text.append(file.display_path, style="bold white")
    if file.status != "D" and file.first_new_line is not None and file.last_new_line is not None:
        text.append(f"  L{file.first_new_line}-{file.last_new_line}", style="dim")
    if file.additions or file.deletions:
        text.append("   ")
        text.append(f"+{file.additions}", style="bold #3fb950")
        text.append(" ")
        text.append(f"-{file.deletions}", style="bold #f85149")
    if file.is_binary:
        text.append("   ")
        text.append("[binary]", style="dim")
    if file.status == "A":
        text.append("  ")
        text.append("new file", style="bold #aff5b4")
    elif file.status == "D":
        text.append("  ")
        text.append("deleted", style="bold #ffdcd7")
    elif file.status == "R":
        text.append("  ")
        text.append("renamed", style="bold #79c0ff")
    elif file.status == "?":
        text.append("  ")
        text.append("untracked", style="bold #d2a8ff")
    return text


def render_empty_state(repo_name: str, branch: str | None) -> RichText:
    text = RichText()
    text.append("\n  No uncommitted changes.\n\n", style="bold #3fb950")
    text.append(f"  {repo_name} ", style="white")
    if branch:
        text.append(f"(branch: {branch}) ", style="#79c0ff")
    text.append("is clean against HEAD.\n", style="dim")
    text.append("\n  Press ", style="dim")
    text.append("esc", style="bold")
    text.append(" to close.\n", style="dim")
    return text


def render_error(message: str) -> RichText:
    text = RichText()
    text.append("\n  Failed to load diff.\n\n", style="bold #f85149")
    text.append(f"  {message}\n", style="#ffa198")
    text.append("\n  Press ", style="dim")
    text.append("esc", style="bold")
    text.append(" to close.\n", style="dim")
    return text


__all__ = [
    "ChangedFile",
    "DiffBundle",
    "GITHUB_DARK_ADDED_BG",
    "GITHUB_DARK_ADDED_FG",
    "GITHUB_DARK_REMOVED_BG",
    "GITHUB_DARK_REMOVED_FG",
    "GithubDarkStyle",
    "STATUS_TEXT",
    "build_diff_bundle",
    "detect_language",
    "diff_gutter_width",
    "parse_diff_files",
    "render_empty_state",
    "render_error",
    "render_file_diff",
]
