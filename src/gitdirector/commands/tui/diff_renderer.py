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
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath

from pygments.lexer import Lexer
from pygments.lexers import DiffLexer, get_lexer_by_name, guess_lexer_for_filename
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
from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.syntax import Syntax
from rich.text import Text as RichText


class _DiffDelegatingLexer(Lexer):
    """Lexer that keeps the diff lexer's line-type tags and runs the
    file-specific lexer over context lines so we get both the coloured
    +/- backgrounds from the diff tags *and* accurate code highlighting.

    Pygments' built-in ``DelegatingLexer`` doesn't quite fit because the
    diff lexer doesn't produce a ``delegate`` token; instead the entire
    line is one token (``Generic.Inserted``/``Generic.Deleted``/``Text``).
    Walking the diff tokens first and only re-lexing the plain ``Text``
    parts with the file lexer is exactly what we need.
    """

    def __init__(self, file_lexer_name: str | None = None, **options) -> None:
        super().__init__(**options)
        self._diff = DiffLexer(**options)
        self._file_lexer = None
        if file_lexer_name:
            try:
                self._file_lexer = get_lexer_by_name(file_lexer_name, **options)
            except ClassNotFound:
                self._file_lexer = None

    def get_tokens(self, code):
        if self._file_lexer is None:
            yield from self._diff.get_tokens(code)
            return
        for tok, val in self._diff.get_tokens(code):
            if tok in (Generic.Heading, Generic.Subheading, Generic.Inserted, Generic.Deleted):
                yield tok, val
            elif tok is Whitespace:
                # Pass plain whitespace through untouched so we don't add
                # line breaks that the file lexer would otherwise inject.
                if val:
                    yield tok, val
            else:
                # The file-specific lexer tends to append a trailing
                # newline to its output, which would create phantom blank
                # lines in the rendered diff. Strip it before yielding.
                for sub_tok, sub_val in self._file_lexer.get_tokens(val):
                    if sub_val.endswith("\n"):
                        stripped = sub_val.rstrip("\n")
                        if stripped:
                            yield sub_tok, stripped
                    elif sub_val:
                        yield sub_tok, sub_val


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
STATUS_PILL_BG: dict[str, str] = {
    "A": "#238636",  # GitHub green
    "M": "#9e6a03",  # amber
    "D": "#da3633",  # GitHub red
    "R": "#1f6feb",  # blue
    "?": "#8957e5",  # purple
    "C": "#1f6feb",
    "T": "#9e6a03",
    "U": "#da3633",
    "X": "#6e7681",
    "B": "#6e7681",
}

STATUS_PILL_FG: dict[str, str] = {
    "A": "#ffffff",
    "M": "#ffffff",
    "D": "#ffffff",
    "R": "#ffffff",
    "?": "#ffffff",
    "C": "#ffffff",
    "T": "#ffffff",
    "U": "#ffffff",
    "X": "#ffffff",
    "B": "#ffffff",
}


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
        # in Pygments' default style — both invisible on our dark bg.
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
    is_rename: bool = False
    old_path: str | None = None
    diff_text: str = ""
    raw_untracked_text: str | None = None
    old_line_count: int = 0
    new_line_count: int = 0
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


_DIFF_GIT_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$")
_NEW_FILE_RE = re.compile(r"^new file")
_DELETED_FILE_RE = re.compile(r"^deleted file")
_RENAME_FROM_RE = re.compile(r"^rename from (.+)$")
_RENAME_TO_RE = re.compile(r"^rename to (.+)$")
_BINARY_RE = re.compile(r"^Binary files .* differ$")
_HUNK_HEADER_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@")


def _parse_hunk_start(header: str) -> tuple[int, int]:
    """Return ``(old_start, new_start)`` from an ``@@`` hunk header."""
    match = re.match(r"^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@", header)
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


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
        return guess_lexer_for_filename(name, "").name
    except ClassNotFound:
        return None


def parse_diff_files(diff_text: str) -> list[ChangedFile]:
    """Walk a unified ``git diff`` payload and return one ``ChangedFile`` per file."""
    if not diff_text:
        return []

    files: list[ChangedFile] = []
    current: ChangedFile | None = None
    current_lines: list[str] = []
    # Mutable per-file tracking that doesn't fit on a frozen dataclass.
    state: dict[str, int | None] = {
        "old_running": 0,
        "new_running": 0,
        "first_new": None,
        "last_new": None,
    }

    def _flush() -> None:
        nonlocal current, current_lines
        if current is None:
            return
        files.append(
            ChangedFile(
                path=current.path,
                status=current.status,
                additions=current.additions,
                deletions=current.deletions,
                is_binary=current.is_binary,
                is_rename=current.is_rename,
                old_path=current.old_path,
                diff_text="\n".join(current_lines).rstrip("\n"),
                old_line_count=current.old_line_count,
                new_line_count=current.new_line_count,
                first_new_line=state["first_new"],
                last_new_line=state["last_new"],
            )
        )
        current = None
        current_lines = []
        state.update({"old_running": 0, "new_running": 0, "first_new": None, "last_new": None})

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            _flush()
            match = _DIFF_GIT_HEADER_RE.match(raw_line)
            if not match:
                continue
            old_name = match.group(1)
            new_name = match.group(2)
            is_rename = old_name != new_name
            current = ChangedFile(
                path=new_name,
                status="M",
                is_rename=is_rename,
                old_path=old_name if is_rename else None,
            )
            current_lines = [raw_line]
            continue
        if current is None:
            continue
        current_lines.append(raw_line)
        if _NEW_FILE_RE.match(raw_line):
            current = replace(current, status="A")
        elif _DELETED_FILE_RE.match(raw_line):
            current = replace(current, status="D")
        elif (m := _RENAME_FROM_RE.match(raw_line)) and current.is_rename:
            current = replace(current, status="R", old_path=m.group(1))
        elif (m := _RENAME_TO_RE.match(raw_line)) and current.is_rename:
            current = replace(current, status="R", old_path=current.old_path or m.group(1))
        elif _BINARY_RE.match(raw_line):
            current = replace(current, is_binary=True)
        elif _HUNK_HEADER_RE.match(raw_line):
            old_start, new_start = _parse_hunk_start(raw_line)
            state["old_running"] = old_start
            state["new_running"] = new_start
            if state["first_new"] is None:
                state["first_new"] = new_start
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            additions = current.additions + 1
            current = replace(current, additions=additions)
            running = state["new_running"]
            if running:
                state["new_running"] = running + 1
                state["last_new"] = running
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            deletions = current.deletions + 1
            current = replace(current, deletions=deletions)
            old_running = state["old_running"]
            if old_running:
                state["old_running"] = old_running + 1
    _flush()
    return files


def build_diff_bundle(diff_text: str, untracked_paths: list[str], untracked_lookup) -> DiffBundle:
    """Combine a parsed diff with untracked file metadata.

    ``untracked_lookup`` is a callable ``(rel_path) -> str | None`` that
    returns the text content of an untracked file, or ``None`` if the file
    cannot be read (binary, missing, too large).
    """
    files = parse_diff_files(diff_text)
    for rel_path in untracked_paths:
        text = untracked_lookup(rel_path)
        if text is None:
            text = "[binary or unreadable file]\n"
        body_lines = text.splitlines()
        line_count = len(body_lines)
        # The synthetic diff MUST include a ``@@`` hunk header, otherwise
        # ``_split_diff_for_render`` can't tell where the meta block ends
        # and the body begins, and the whole thing ends up styled as the
        # muted-gray "meta" caption instead of the green "added" body.
        synthetic = (
            f"diff --git a/{rel_path} b/{rel_path}\n"
            f"new file mode 100644\n"
            f"--- /dev/null\n"
            f"+++ b/{rel_path}\n"
            f"@@ -0,0 +1,{line_count} @@\n"
            + "".join(f"+{line}\n" for line in body_lines)
        )
        files.append(
            ChangedFile(
                path=rel_path,
                status="?",
                additions=line_count,
                diff_text=synthetic.rstrip("\n"),
                raw_untracked_text=text,
            )
        )
    return DiffBundle(files=files, raw=diff_text)


def _status_pill_text(status: str) -> RichText:
    """Return a GitHub-style coloured pill for the given status code."""
    bg = STATUS_PILL_BG.get(status, "#6e7681")
    fg = STATUS_PILL_FG.get(status, "#ffffff")
    label = status if status != "?" else "U"
    return RichText(f" {label} ", style=f"bold {fg} on {bg}")


def _status_word(status: str) -> str:
    return _STATUS_LABEL.get(status, "changed")


def render_change_summary(
    file: ChangedFile, *, path_width: int = 56, show_new_badge: bool = True
) -> RichText:
    """Single-line summary used in the left-hand file list.

    Layout (GitHub-inspired): a coloured status pill, the file path, an
    optional ``new`` chip for additions, and ``+N -M`` stats.
    """
    text = RichText()
    text.append_text(_status_pill_text(file.status))
    text.append(" ")
    display = file.display_path
    if len(display) > path_width and path_width > 3:
        display = "\u2026" + display[-(path_width - 1) :]
    text.append(display, style="bold")
    if show_new_badge and file.status == "A":
        text.append(" ")
        text.append(" new ", style="bold #aff5b4 on #238636")
    if file.is_rename:
        text.append(" ")
        text.append("renamed", style="dim italic")
    if file.is_binary:
        text.append("  ")
        text.append("[binary]", style="dim")
    elif file.additions or file.deletions:
        text.append("  ")
        text.append(f"+{file.additions}", style="#3fb950")
        text.append(" ")
        text.append(f"-{file.deletions}", style="#f85149")
    return text


def render_file_diff(
    file: ChangedFile,
    *,
    width: int | None = None,
    theme=GithubDarkStyle,
) -> RenderableType:
    """Build a richly-styled renderable for the right-hand panel.

    The layout matches GitHub's diff view:

    1. Coloured header bar with status pill, file path, line range, stats.
    2. The hunk header (the ``@@`` lines) collapsed into a single chip.
    3. The actual file lines (context, +, -) rendered with syntax
       highlighting and a gutter showing the *real* file line number.
    """
    pieces: list[RenderableType] = []

    pieces.append(_render_file_header(file))

    if file.is_binary:
        body = RichText(
            "\n  Binary file differs from HEAD.\n" "  Diff is not shown for binary files.\n",
            style="italic dim",
        )
        pieces.append(Padding(body, (1, 2)))
        return Group(*pieces)

    body_text = file.diff_text

    # Split the diff text into:
    #   - header_lines: the diff --git / index / --- / +++ / new file mode /
    #     rename from / rename to / binary preamble
    #   - hunk_chips:   the @@ lines (rendered as small blue chips)
    #   - body_lines:   the actual file content (context, +, -)
    header_lines, hunk_chips, body_lines = _split_diff_for_render(body_text)

    if header_lines:
        pieces.append(_render_diff_meta_lines(header_lines))
    if hunk_chips:
        pieces.append(_render_hunk_chips(hunk_chips))
    if body_lines:
        pieces.append(
            _render_file_body(
                body_lines,
                file=file,
                width=width,
                theme=theme,
            )
        )

    return Group(*pieces)


def _split_diff_for_render(diff_text: str) -> tuple[list[str], list[str], list[str]]:
    """Split a diff payload into metadata, hunk headers, and file-body lines.

    The metadata section is everything before the first hunk header that is
    not a hunk header itself (the ``diff --git`` line, the ``index`` line,
    ``---``/``+++``, ``new file mode``/``rename from``/etc.). The body
    section is just the ``+``/``-``/context lines, which the Syntax
    renderer will then number with the *real* file line numbers via
    ``start_line``.
    """
    header_lines: list[str] = []
    hunk_chips: list[str] = []
    body_lines: list[str] = []
    in_hunk = False
    for raw_line in diff_text.splitlines():
        if _HUNK_HEADER_RE.match(raw_line):
            hunk_chips.append(raw_line)
            in_hunk = True
            continue
        if in_hunk:
            body_lines.append(raw_line)
        else:
            header_lines.append(raw_line)
    return header_lines, hunk_chips, body_lines


def _render_diff_meta_lines(lines: list[str]) -> RenderableType:
    """Render the pre-hunk metadata (``diff --git``, ``index``, ``---``, ...) as a small caption."""
    text = RichText()
    for i, line in enumerate(lines):
        if i:
            text.append("\n")
        if line.startswith("diff --git "):
            text.append("  ", style="dim")
            text.append(line, style=f"bold {GITHUB_DARK_HEADING}")
        elif line.startswith("index "):
            text.append("  ", style="dim")
            text.append(line, style=f"italic {GITHUB_DARK_MUTED}")
        elif line.startswith("--- ") or line.startswith("+++ "):
            text.append("  ", style="dim")
            text.append(line, style=f"italic {GITHUB_DARK_MUTED}")
        elif line.startswith("new file mode") or line.startswith("deleted file mode"):
            text.append("  ", style="dim")
            text.append(line, style=f"italic {GITHUB_DARK_MUTED}")
        elif line.startswith("rename ") or line.startswith("similarity "):
            text.append("  ", style="dim")
            text.append(line, style=f"italic {GITHUB_DARK_MUTED}")
        elif line.startswith("Binary files "):
            text.append("  ", style="dim")
            text.append(line, style=f"italic {GITHUB_DARK_MUTED}")
        else:
            text.append("  ", style="dim")
            text.append(line, style=f"italic {GITHUB_DARK_MUTED}")
    return Padding(text, (0, 2), style="on #161b22")


def _render_hunk_chips(hunk_lines: list[str]) -> RenderableType:
    """Render the ``@@ ... @@`` lines as small blue chips (one per hunk)."""
    text = RichText()
    for i, line in enumerate(hunk_lines):
        if i:
            text.append("\n")
        text.append("  ", style="dim")
        text.append(line, style=f"bold {GITHUB_DARK_HEADING} on #1f2d44")
    return Padding(text, (0, 2), style="on #161b22")


def _render_file_body(
    body_lines: list[str],
    *,
    file: ChangedFile,
    width: int | None,
    theme: Style,
) -> RenderableType:
    """Render the file-content portion of the diff with proper line numbers."""
    body_text = "\n".join(body_lines)
    lexer_name = detect_language(file.path)
    try:
        if lexer_name:
            lexer: str | Lexer = _DiffDelegatingLexer(file_lexer_name=lexer_name)
        else:
            lexer = DiffLexer()
    except Exception:
        lexer = "diff"
    start_line = file.first_new_line or 1
    # Tint the whole content area the same family as the file's
    # status so new/deleted files read as one cohesive block instead
    # of a dark strip with floating coloured chips. Rich's Syntax
    # applies ``background_color`` *after* the per-token bgs, which
    # means the per-line ``+``/``-`` bgs from the Pygments style are
    # clobbered when we do this; we accept that trade-off because the
    # whole-panel tint is the more important signal. The line-number
    # gutter, trailing whitespace, and the empty space below the
    # content all pick up the panel bg.
    if file.status in ("A", "?"):
        syntax_bg = GITHUB_DARK_ADDED_PANEL_BG
    elif file.status == "D":
        syntax_bg = GITHUB_DARK_REMOVED_PANEL_BG
    else:
        syntax_bg = None
    try:
        syntax: Syntax = Syntax(
            body_text or " ",
            lexer,
            theme=theme,
            line_numbers=True,
            word_wrap=False,
            indent_guides=False,
            code_width=width,
            start_line=start_line,
            background_color=syntax_bg,
        )
    except Exception:
        syntax = Syntax(
            body_text or " ",
            DiffLexer(),
            theme=theme,
            line_numbers=True,
            start_line=start_line,
            background_color=syntax_bg,
        )
    return syntax


def _render_file_header(file: ChangedFile) -> RenderableType:
    """Coloured header bar for a file diff (GitHub's file header style)."""
    bg = STATUS_PILL_BG.get(file.status, "#21262d")
    text = RichText()
    text.append_text(_status_pill_text(file.status))
    text.append("  ")
    text.append(file.display_path, style="bold white")
    if file.first_new_line is not None and file.last_new_line is not None:
        if file.status == "A":
            text.append(
                f"  L{file.first_new_line}-{file.last_new_line}",
                style="dim",
            )
        elif file.status != "D":
            text.append(
                f"  L{file.first_new_line}-{file.last_new_line}",
                style="dim",
            )
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
    return Padding(text, (0, 2), style=f"on {bg}")


def format_status_badge(status: str) -> str:
    """Return the human label for a git status code (M, A, D, R, ?)."""
    if not status:
        return "\u00b7"
    if status == "?":
        return "U"
    if status in _STATUS_LABEL:
        return _STATUS_LABEL[status][:1].upper()
    return status.upper()


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
    "STATUS_PILL_BG",
    "STATUS_PILL_FG",
    "build_diff_bundle",
    "detect_language",
    "format_status_badge",
    "parse_diff_files",
    "render_change_summary",
    "render_empty_state",
    "render_error",
    "render_file_diff",
]
