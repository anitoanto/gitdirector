"""Tests for the ``diff_renderer`` module.

These tests are pure-Python: they exercise the parsing and rendering helpers
without booting Textual so failures point straight at the logic.
"""

from __future__ import annotations

from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text

from gitdirector.commands.tui.diff_renderer import (
    ChangedFile,
    DiffBundle,
    build_diff_bundle,
    detect_language,
    format_status_badge,
    parse_diff_files,
    render_change_summary,
    render_empty_state,
    render_error,
    render_file_diff,
)


def _luminance(color: str) -> float:
    """WCAG-style relative luminance (0..1) for a colour that may or
    may not have a leading ``#`` (Pygments sometimes returns the bare
    hex)."""
    r, g, b = _rgb(color)
    r /= 255.0
    g /= 255.0
    b /= 255.0

    def linearise(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * linearise(r) + 0.7152 * linearise(g) + 0.0722 * linearise(b)


def _rgb(color: str) -> tuple[int, int, int]:
    """Return the (r, g, b) integers of a colour that may or may not
    have a leading ``#`` (Pygments sometimes returns the bare hex)."""
    s = color[1:] if color.startswith("#") else color
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


class TestParseDiffFiles:
    def test_empty_diff_returns_empty_list(self):
        assert parse_diff_files("") == []

    def test_single_modification(self):
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "index 1234..5678 100644\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,3 +1,3 @@\n"
            " def hello():\n"
            '-    return "old"\n'
            '+    return "new"\n'
        )
        files = parse_diff_files(diff)
        assert len(files) == 1
        f = files[0]
        assert f.path == "foo.py"
        assert f.status == "M"
        assert f.additions == 1
        assert f.deletions == 1
        assert f.is_binary is False
        assert f.is_rename is False
        assert f.old_path is None
        assert "diff --git" in f.diff_text

    def test_new_file_status_is_added(self):
        diff = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "index 0000000..1234567\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+line1\n"
            "+line2\n"
        )
        files = parse_diff_files(diff)
        assert len(files) == 1
        assert files[0].path == "new.py"
        assert files[0].status == "A"
        assert files[0].additions == 2
        assert files[0].deletions == 0

    def test_deleted_file_status_is_deleted(self):
        diff = (
            "diff --git a/old.py b/old.py\n"
            "deleted file mode 100644\n"
            "index 1234567..0000000\n"
            "--- a/old.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-line1\n"
            "-line2\n"
        )
        files = parse_diff_files(diff)
        assert len(files) == 1
        assert files[0].status == "D"
        assert files[0].deletions == 2

    def test_rename_detected(self):
        diff = (
            "diff --git a/old_name.py b/new_name.py\n"
            "similarity index 90%\n"
            "rename from old_name.py\n"
            "rename to new_name.py\n"
            "index 1234..5678 100644\n"
            "--- a/old_name.py\n"
            "+++ b/new_name.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-old\n"
            "+new\n"
        )
        files = parse_diff_files(diff)
        assert len(files) == 1
        assert files[0].is_rename is True
        assert files[0].status == "R"
        assert files[0].old_path == "old_name.py"
        assert files[0].path == "new_name.py"

    def test_binary_file_marked(self):
        diff = (
            "diff --git a/img.png b/img.png\n"
            "index 1234..5678 100644\n"
            "--- a/img.png\n"
            "+++ b/img.png\n"
            "Binary files a/img.png and b/img.png differ\n"
        )
        files = parse_diff_files(diff)
        assert len(files) == 1
        assert files[0].is_binary is True

    def test_multiple_files(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "index 1..2 100644\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/b.py b/b.py\n"
            "index 3..4 100644\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -1 +1 @@\n"
            "-x\n"
            "+y\n"
        )
        files = parse_diff_files(diff)
        assert [f.path for f in files] == ["a.py", "b.py"]
        assert [f.status for f in files] == ["M", "M"]

    def test_handles_malformed_input_gracefully(self):
        diff = "not a real diff\n@@ -1 +1 @@\n-bad\n+ok\n"
        assert parse_diff_files(diff) == []


class TestParseCopyStatus:
    """Bug regression: a git diff emits 'copy from ... copy to ...' for
    a copy entry. The parser should set status to 'C' (copied), with
    the right old_path and new_path, not leave it as 'M'.
    """

    def test_copy_status_from_header(self):
        diff_text = (
            "diff --git a/original.txt b/similar.txt\n"
            "similarity index 66%\n"
            "copy from original.txt\n"
            "copy to similar.txt\n"
            "index 2756ab3..8bd2bc5 100644\n"
            "--- a/original.txt\n"
            "+++ b/similar.txt\n"
            "@@ -1 +1,2 @@\n"
            " abc def ghi jkl\n"
            "+// copy\n"
        )

        files = parse_diff_files(diff_text)

        assert len(files) == 1
        f = files[0]
        assert f.status == "C", f"expected status 'C' (copied), got {f.status!r}"
        assert f.path == "similar.txt"
        assert f.old_path == "original.txt"

    def test_rename_still_renders_as_R(self):
        """Sanity check: a real rename still renders as status 'R' and
        the diff renderer doesn't regress on the existing rename path
        while introducing copy support.
        """

        diff_text = (
            "diff --git a/old.txt b/new.txt\n"
            "similarity index 100%\n"
            "rename from old.txt\n"
            "rename to new.txt\n"
        )

        files = parse_diff_files(diff_text)

        assert len(files) == 1
        f = files[0]
        assert f.status == "R", f"expected status 'R' for rename, got {f.status!r}"
        assert f.path == "new.txt"
        assert f.old_path == "old.txt"

    def test_multiple_copies_from_same_source(self):
        """When git emits several copies from the same source (one per
        target), each file must come out with status 'C' and its own
        old_path. Regressing here would roll both copies up onto the
        single-old_path of the first one.
        """

        diff_text = (
            "diff --git a/original.txt b/back1.txt\n"
            "similarity index 100%\n"
            "copy from original.txt\n"
            "copy to back1.txt\n"
            "diff --git a/original.txt b/back2.txt\n"
            "similarity index 100%\n"
            "copy from original.txt\n"
            "copy to back2.txt\n"
            "diff --git a/original.txt b/target.txt\n"
            "similarity index 100%\n"
            "rename from original.txt\n"
            "rename to target.txt\n"
        )

        files = parse_diff_files(diff_text)

        by_path = {f.path: f for f in files}
        assert set(by_path) == {"back1.txt", "back2.txt", "target.txt"}

        assert by_path["back1.txt"].status == "C"
        assert by_path["back1.txt"].old_path == "original.txt"

        assert by_path["back2.txt"].status == "C"
        assert by_path["back2.txt"].old_path == "original.txt"

        assert by_path["target.txt"].status == "R"
        assert by_path["target.txt"].old_path == "original.txt"

    def test_copy_with_content_change_preserves_counts(self):
        """When a copy also adds/removes content the count fields must
        reflect the additions/deletions from the body lines.
        """

        diff_text = (
            "diff --git a/original.txt b/clone.txt\n"
            "similarity index 72%\n"
            "copy from original.txt\n"
            "copy to clone.txt\n"
            "index 2756ab3..12573ff 100644\n"
            "--- a/original.txt\n"
            "+++ b/clone.txt\n"
            "@@ -1 +1,2 @@\n"
            " abc def ghi jkl\n"
            "+//mod\n"
        )

        files = parse_diff_files(diff_text)
        assert len(files) == 1
        f = files[0]
        assert f.status == "C"
        assert f.old_path == "original.txt"
        assert f.path == "clone.txt"
        assert f.additions == 1, f"expected 1 addition, got {f.additions}"
        assert f.deletions == 0, f"expected 0 deletions, got {f.deletions}"


class TestParseQuotedFilenames:
    """Bug regression: git quotes filenames that contain characters
    outside its safe set, e.g. ``"a/file_with\\ttab.txt" "b/file_...
    "``. The parser must unquote them so they appear in the file list.
    """

    def test_quoted_paths_with_tab_escape(self):
        diff_text = (
            'diff --git "a/file with\\ttab.txt" "b/file with\\ttab.txt"\n'
            "index 113a406..e019be0 100644\n"
            '--- "a/file with\\ttab.txt"\n'
            '+++ "b/file with\\ttab.txt"\n'
            "@@ -1 +1 @@\n"
            "-first\n"
            "+second\n"
        )

        files = parse_diff_files(diff_text)

        assert len(files) == 1
        assert files[0].path == "file with\ttab.txt"

    def test_quoted_paths_with_spaces(self):
        diff_text = (
            'diff --git "a/my file.txt" "b/my file.txt"\n'
            "index 113a406..e019be0 100644\n"
            '--- "a/my file.txt"\n'
            '+++ "b/my file.txt"\n'
            "@@ -1 +1 @@\n"
            "-first\n"
            "+second\n"
        )

        files = parse_diff_files(diff_text)

        assert len(files) == 1
        assert files[0].path == "my file.txt"


class TestParseDiffGitPathsHelper:
    """Direct tests for the line-parsing helper used by the diff renderer."""

    def test_unquoted_paths(self):
        from gitdirector.commands.tui.diff_renderer import _parse_diff_git_paths

        old, new = _parse_diff_git_paths("diff --git a/foo.py b/bar.py")
        assert old == "foo.py"
        assert new == "bar.py"

    def test_unquoted_same_path(self):
        from gitdirector.commands.tui.diff_renderer import _parse_diff_git_paths

        old, new = _parse_diff_git_paths("diff --git a/foo.py b/foo.py")
        assert old == "foo.py"
        assert new == "foo.py"

    def test_quoted_paths_with_spaces(self):
        from gitdirector.commands.tui.diff_renderer import _parse_diff_git_paths

        old, new = _parse_diff_git_paths('diff --git "a/my file.txt" "b/my file.txt"')
        assert old == "my file.txt"
        assert new == "my file.txt"

    def test_quoted_paths_with_tab_escape(self):
        from gitdirector.commands.tui.diff_renderer import _parse_diff_git_paths

        old, new = _parse_diff_git_paths(
            'diff --git "a/file_with\\ttab.txt" "b/file_with\\ttab.txt"'
        )
        assert old == "file_with\ttab.txt"
        assert new == "file_with\ttab.txt"

    def test_unrelated_line_returns_none_pair(self):
        from gitdirector.commands.tui.diff_renderer import _parse_diff_git_paths

        old, new = _parse_diff_git_paths("--- a/foo.py b/bar.py")
        assert old is None
        assert new is None

    def test_missing_b_prefix_returns_none(self):
        from gitdirector.commands.tui.diff_renderer import _parse_diff_git_paths

        old, new = _parse_diff_git_paths("diff --git a/foo.py")
        assert old is None
        assert new is None

    def test_quoted_path_unclosed_quote_returns_none(self):
        """An unclosed quote is malformed — we'd rather drop the file
        than guess path boundaries.
        """
        from gitdirector.commands.tui.diff_renderer import _parse_diff_git_paths

        old, new = _parse_diff_git_paths('diff --git "a/foo b/bar')
        assert old is None
        assert new is None


class TestUnescapeGitPath:
    """Reverse of git's pathname quoting (``\\\\``, ``\\t``, ``\\"``, ``\\n``)."""

    def test_plain_text_unchanged(self):
        from gitdirector.commands.tui.diff_renderer import _unescape_git_path

        assert _unescape_git_path("plain") == "plain"

    def test_tab_escape_decoded(self):
        from gitdirector.commands.tui.diff_renderer import _unescape_git_path

        assert _unescape_git_path("a\\tb") == "a\tb"

    def test_quote_escape_decoded(self):
        from gitdirector.commands.tui.diff_renderer import _unescape_git_path

        assert _unescape_git_path('a\\"b') == 'a"b'

    def test_backslash_escape_decoded(self):
        from gitdirector.commands.tui.diff_renderer import _unescape_git_path

        # ``\\\\`` should yield a single literal backslash.
        assert _unescape_git_path("a\\\\b") == "a\\b"

    def test_newline_escape_decoded(self):
        from gitdirector.commands.tui.diff_renderer import _unescape_git_path

        # ``\\n`` is git's newline escape. Real Unix paths can't
        # contain a literal newline, so we leave the escape decoded.
        assert _unescape_git_path("a\\nb") == "a\nb"

    def test_octal_escaped_utf8_decoded(self):
        from gitdirector.commands.tui.diff_renderer import _unescape_git_path

        assert _unescape_git_path("caf\\303\\251.txt") == "café.txt"


class TestParseOctalEscapedFilenames:
    def test_quoted_utf8_path_is_decoded(self):
        diff_text = (
            'diff --git "a/caf\\303\\251.txt" "b/caf\\303\\251.txt"\n'
            "index 113a406..e019be0 100644\n"
            "@@ -1 +1 @@\n"
            "-first\n"
            "+second\n"
        )

        files = parse_diff_files(diff_text)

        assert len(files) == 1
        assert files[0].path == "café.txt"


class TestDetectLanguage:
    def test_python(self):
        assert detect_language("foo.py") == "python"

    def test_javascript(self):
        assert detect_language("foo.js") == "javascript"

    def test_typescript(self):
        assert detect_language("foo.ts") == "typescript"

    def test_rust(self):
        assert detect_language("foo.rs") == "rust"

    def test_dockerfile(self):
        assert detect_language("Dockerfile") == "dockerfile"

    def test_makefile(self):
        assert detect_language("Makefile") == "makefile"

    def test_unknown_returns_none(self):
        assert detect_language("foo.zzzunknown") is None

    def test_empty_returns_none(self):
        assert detect_language("") is None

    def test_path_with_directory(self):
        assert detect_language("src/foo/bar.py") == "python"


class TestFormatStatusBadge:
    def test_known_statuses(self):
        assert format_status_badge("M") == "M"
        assert format_status_badge("A") == "A"
        assert format_status_badge("D") == "D"
        assert format_status_badge("R") == "R"
        assert format_status_badge("?") == "U"

    def test_empty_returns_middle_dot(self):
        assert format_status_badge("") == "\u00b7"

    def test_unknown_status_returns_itself(self):
        assert format_status_badge("Z") == "Z"


class TestRenderChangeSummary:
    def test_includes_path_and_stats(self):
        f = ChangedFile(path="src/foo.py", status="M", additions=3, deletions=1)
        text = render_change_summary(f)
        assert isinstance(text, Text)
        assert "foo.py" in text.plain
        assert "+3" in text.plain
        assert "-1" in text.plain
        assert "M" in text.plain

    def test_untracked_badge(self):
        f = ChangedFile(path="new.py", status="?", additions=10)
        text = render_change_summary(f)
        # The pill shows "U" for untracked, and there's an "untracked" word
        # in the new header design.
        plain = text.plain
        assert "U" in plain
        assert "new.py" in plain

    def test_added_file_shows_new_chip(self):
        f = ChangedFile(path="added.py", status="A", additions=10, deletions=0)
        text = render_change_summary(f)
        plain = text.plain
        assert "new" in plain.lower()
        assert "added.py" in plain
        assert "+10" in plain
        assert "A" in plain

    def test_renamed_file_shows_marker(self):
        f = ChangedFile(
            path="new_name.py",
            status="R",
            is_rename=True,
            old_path="old_name.py",
            additions=1,
            deletions=1,
        )
        text = render_change_summary(f)
        plain = text.plain
        assert "old_name.py" in plain
        assert "new_name.py" in plain
        assert "renamed" in plain

    def test_binary_file_skips_stats(self):
        f = ChangedFile(path="img.png", status="M", is_binary=True, additions=0, deletions=0)
        text = render_change_summary(f)
        plain = text.plain
        assert "[binary]" in plain

    def test_long_path_truncated_with_ellipsis(self):
        long_path = "a/very/very/long/path/to/some/file.py"
        f = ChangedFile(path=long_path, status="M", additions=1)
        text = render_change_summary(f, path_width=20)
        plain = text.plain
        assert "\u2026" in plain
        assert "very/long" not in plain or plain.endswith("file.py")


class TestRenderFileHeader:
    def test_added_file_header_includes_new_label(self):
        from gitdirector.commands.tui.diff_renderer import _render_file_header

        f = ChangedFile(path="new.py", status="A", additions=10)
        from rich.padding import Padding

        header = _render_file_header(f)
        assert isinstance(header, Padding)
        plain = header.renderable.plain
        assert "new.py" in plain
        assert "new file" in plain
        assert "+10" in plain

    def test_deleted_file_header_includes_deleted_label(self):
        from gitdirector.commands.tui.diff_renderer import _render_file_header

        f = ChangedFile(path="removed.py", status="D", deletions=5)
        from rich.padding import Padding

        header = _render_file_header(f)
        assert isinstance(header, Padding)
        plain = header.renderable.plain
        assert "removed.py" in plain
        assert "deleted" in plain

    def test_renamed_file_header_includes_renamed_label(self):
        from gitdirector.commands.tui.diff_renderer import _render_file_header

        f = ChangedFile(
            path="new.py",
            status="R",
            is_rename=True,
            old_path="old.py",
            additions=1,
            deletions=1,
        )
        from rich.padding import Padding

        header = _render_file_header(f)
        assert isinstance(header, Padding)
        plain = header.renderable.plain
        assert "old.py" in plain
        assert "new.py" in plain
        assert "renamed" in plain


class TestGithubDarkStyle:
    def test_style_is_pygments_style(self):
        from pygments.style import Style

        from gitdirector.commands.tui.diff_renderer import GithubDarkStyle

        assert issubclass(GithubDarkStyle, Style)

    def test_style_has_required_token_mappings(self):
        from pygments.token import Generic

        from gitdirector.commands.tui.diff_renderer import GithubDarkStyle

        styles = GithubDarkStyle.styles
        assert Generic.Heading in styles
        assert Generic.Subheading in styles
        assert Generic.Inserted in styles
        assert Generic.Deleted in styles

    def test_markdown_emphasis_tokens_have_legible_colors(self):
        # Pygments' default style maps Generic.Strong/Emph/EmphStrong
        # to bare ``bold``/``italic`` with no colour. On a dark bg that
        # falls back to the terminal's default fg (usually black) and
        # makes **bold** and *italic* text invisible. Our style must
        # pin every emphasis token to a bright tone.
        from pygments.token import Generic

        from gitdirector.commands.tui.diff_renderer import GithubDarkStyle

        for token in (Generic.Strong, Generic.Emph, Generic.EmphStrong):
            style = GithubDarkStyle.style_for_token(token)
            color = style.get("color")
            assert color, f"{token} has no colour (would render as black)"
            # The colour has to be bright enough to read on #0d1117.
            # Per-component brightness check is more meaningful than
            # the WCAG formula here (which under-weights blues).
            r, g, b = _rgb(color)
            assert max(r, g, b) > 180, f"{token} colour {color} too dark to read on dark bg"

    def test_heading_and_subheading_are_legible(self):
        # The default style maps these to dark navy / dark purple,
        # which also disappear on a dark bg.
        from pygments.token import Generic

        from gitdirector.commands.tui.diff_renderer import GithubDarkStyle

        for token in (Generic.Heading, Generic.Subheading):
            style = GithubDarkStyle.style_for_token(token)
            color = style.get("color")
            assert color
            r, g, b = _rgb(color)
            assert max(r, g, b) > 150, f"{token} colour {color} too dark to read on dark bg"

    def test_shell_output_and_prompt_tokens_are_legible(self):
        # Generic.Output defaults to #717171 (dark grey) and
        # Generic.Prompt defaults to #000080 (navy) in Pygments' default
        # style — both invisible on the dark bg. Our style must
        # override them.
        from pygments.token import Generic

        from gitdirector.commands.tui.diff_renderer import GithubDarkStyle

        for token in (Generic.Output, Generic.Prompt):
            style = GithubDarkStyle.style_for_token(token)
            color = style.get("color")
            assert color, f"{token} has no colour"
            r, g, b = _rgb(color)
            assert max(r, g, b) > 100, f"{token} colour {color} too dark to read on dark bg"


class TestStatusPillColors:
    def test_all_known_statuses_have_a_pill(self):
        from gitdirector.commands.tui.diff_renderer import STATUS_PILL_BG, STATUS_PILL_FG

        for status in ("A", "M", "D", "R", "?", "C", "T", "U"):
            assert status in STATUS_PILL_BG
            assert status in STATUS_PILL_FG


class TestPerLineTint:
    """The diff viewer must give the reader a clear visual signal for
    the file's status:

    * For new / untracked files, the entire right-side panel is
      green-tinted (line-number gutter, content, trailing whitespace,
      and empty space below the content all read as "added").
    * For deleted files, the entire panel is red-tinted.
    * For modified files, the per-line ``+`` / ``-`` bgs are the
      visible green / red signals against the base dark panel.
    """

    def _style_for(self, token):
        from gitdirector.commands.tui.diff_renderer import GithubDarkStyle

        return GithubDarkStyle.style_for_token(token)

    def _syntax_for(self, file: ChangedFile):
        from gitdirector.commands.tui.diff_renderer import _render_file_body

        return _render_file_body(
            ["+first line", "-second line"],
            file=file,
            width=80,
            theme="monokai",
        )

    def test_inserted_line_has_green_tinted_bgcolor(self):
        from pygments.token import Generic

        style = self._style_for(Generic.Inserted)
        bg = style.get("bgcolor")
        assert bg, "Generic.Inserted must have a bgcolor"
        r, g, b = _rgb(bg)
        # The bg must lean green: the green channel is the largest.
        assert g > r and g > b, f"expected green-tinted bg, got {bg}"
        # And it must be visibly tinted (not the base dark bg) so the
        # reader can see the row is "added" at a glance.
        assert g > 30, f"expected a visible green tint, got {bg}"

    def test_deleted_line_has_red_tinted_bgcolor(self):
        from pygments.token import Generic

        style = self._style_for(Generic.Deleted)
        bg = style.get("bgcolor")
        assert bg, "Generic.Deleted must have a bgcolor"
        r, g, b = _rgb(bg)
        assert r > g and r > b, f"expected red-tinted bg, got {bg}"
        assert r > 30, f"expected a visible red tint, got {bg}"

    def test_new_file_paints_whole_panel_green(self):
        # The line-number gutter, trailing whitespace, and empty
        # space below the content must all read as green.
        from gitdirector.commands.tui.diff_renderer import (
            GITHUB_DARK_ADDED_PANEL_BG,
        )

        syntax = self._syntax_for(ChangedFile(path="new.py", status="A", additions=2))
        bg = getattr(syntax, "background_color", None)
        assert bg == GITHUB_DARK_ADDED_PANEL_BG
        r, g, b = _rgb(bg)
        assert g > r and g > b, f"expected green-tinted panel bg, got {bg}"

    def test_untracked_file_paints_whole_panel_green(self):
        from gitdirector.commands.tui.diff_renderer import (
            GITHUB_DARK_ADDED_PANEL_BG,
        )

        syntax = self._syntax_for(ChangedFile(path="new.py", status="?", additions=2))
        assert getattr(syntax, "background_color", None) == GITHUB_DARK_ADDED_PANEL_BG

    def test_deleted_file_paints_whole_panel_red(self):
        from gitdirector.commands.tui.diff_renderer import (
            GITHUB_DARK_REMOVED_PANEL_BG,
        )

        syntax = self._syntax_for(ChangedFile(path="old.py", status="D", deletions=2))
        bg = getattr(syntax, "background_color", None)
        assert bg == GITHUB_DARK_REMOVED_PANEL_BG
        r, g, b = _rgb(bg)
        assert r > g and r > b, f"expected red-tinted panel bg, got {bg}"

    def test_modified_file_keeps_base_dark_panel(self):
        # Modified files: no whole-panel tint; the per-line
        # ``+`` / ``-`` bgs from the Pygments style are the
        # only colour signal.
        from gitdirector.commands.tui.diff_renderer import GITHUB_DARK_BG

        syntax = self._syntax_for(ChangedFile(path="foo.py", status="M", additions=1, deletions=1))
        # Rich defaults the Syntax bg to the theme's bg when no
        # ``background_color`` is passed; either way it must be the
        # base dark colour, not a green/red panel tint.
        bg = getattr(syntax, "background_color", None) or GITHUB_DARK_BG
        assert bg == GITHUB_DARK_BG

    def test_added_palette_constants(self):
        from gitdirector.commands.tui.diff_renderer import (
            GITHUB_DARK_ADDED_BG,
            GITHUB_DARK_REMOVED_BG,
        )

        # The per-line added bg is a slightly more visible green
        # (so the tint is readable) and the removed bg is the
        # matching slightly-more-visible red. They still keep the
        # base dark bg under them.
        for color, expected_channel in (
            (GITHUB_DARK_ADDED_BG, "g"),
            (GITHUB_DARK_REMOVED_BG, "r"),
        ):
            r, g, b = _rgb(color)
            if expected_channel == "g":
                assert g > r and g > b, f"{color} should lean green"
            else:
                assert r > g and r > b, f"{color} should lean red"


class TestDiffDelegatingLexer:
    def test_preserves_inserted_token(self):
        from pygments.token import Generic

        from gitdirector.commands.tui.diff_renderer import _DiffDelegatingLexer

        lexer = _DiffDelegatingLexer(file_lexer_name="python")
        sample = "+    return 42\n"
        tokens = list(lexer.get_tokens(sample))
        types = [t for t, _ in tokens]
        assert Generic.Inserted in types
        # The full line is one Generic.Inserted token, including the leading '+'
        inserted_text = next(v for t, v in tokens if t is Generic.Inserted)
        assert inserted_text.startswith("+")

    def test_preserves_deleted_token(self):
        from pygments.token import Generic

        from gitdirector.commands.tui.diff_renderer import _DiffDelegatingLexer

        lexer = _DiffDelegatingLexer(file_lexer_name="python")
        sample = "-    return 42\n"
        tokens = list(lexer.get_tokens(sample))
        types = [t for t, _ in tokens]
        assert Generic.Deleted in types
        deleted_text = next(v for t, v in tokens if t is Generic.Deleted)
        assert deleted_text.startswith("-")

    def test_falls_back_to_diff_when_no_file_lexer(self):
        from pygments.token import Generic

        from gitdirector.commands.tui.diff_renderer import _DiffDelegatingLexer

        lexer = _DiffDelegatingLexer()
        sample = "+    x = 1\n"
        tokens = list(lexer.get_tokens(sample))
        assert any(t is Generic.Inserted for t, _ in tokens)

    def test_ignores_unknown_file_lexer(self):
        from gitdirector.commands.tui.diff_renderer import _DiffDelegatingLexer

        # Should not raise even if lexer name is unknown
        lexer = _DiffDelegatingLexer(file_lexer_name="this-is-not-a-real-lexer")
        sample = "+    x = 1\n"
        list(lexer.get_tokens(sample))

    def test_long_path_truncated(self):
        f = ChangedFile(path="a/very/very/long/path/to/some/file.py", status="M", additions=1)
        text = render_change_summary(f, path_width=15)
        plain = text.plain
        assert "very/long/path" not in plain or "..." in plain or len(plain) < 60


class TestRenderFileDiff:
    def test_returns_group_with_syntax(self):
        f = ChangedFile(
            path="foo.py",
            status="M",
            additions=1,
            deletions=1,
            diff_text=("diff --git a/foo.py b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"),
        )
        r = render_file_diff(f, theme="monokai")
        assert isinstance(r, Group)
        # First piece is the header text, second is the Syntax
        syntax_pieces = [p for p in r.renderables if isinstance(p, Syntax)]
        assert len(syntax_pieces) == 1

    def test_no_newline_marker_is_not_rendered(self):
        f = ChangedFile(
            path="settings.json",
            status="A",
            additions=3,
            diff_text=(
                "diff --git a/settings.json b/settings.json\n"
                "--- /dev/null\n"
                "+++ b/settings.json\n"
                "@@ -0,0 +1,3 @@\n"
                "+{\n"
                '+  "port": 5501\n'
                "+}\n"
                "\\ No newline at end of file\n"
            ),
        )

        rendered = render_file_diff(f)
        syntax = next(piece for piece in rendered.renderables if isinstance(piece, Syntax))

        assert "No newline at end of file" not in syntax.code
        assert syntax.code.splitlines() == ["+{", '+  "port": 5501', "+}"]

    def test_binary_file_shows_message_no_syntax(self):
        f = ChangedFile(path="app.exe", status="M", is_binary=True, diff_text="")
        r = render_file_diff(f)
        syntax_pieces = [p for p in r.renderables if isinstance(p, Syntax)]
        assert syntax_pieces == []

    def test_image_file_renders_header_only_no_diff(self):
        f = ChangedFile(path="logo.png", status="A", is_image=True, additions=0, deletions=0)
        r = render_file_diff(f)
        syntax_pieces = [p for p in r.renderables if isinstance(p, Syntax)]
        assert syntax_pieces == []
        # Header is still rendered so the user sees which file is selected.
        assert len(r.renderables) == 1

    def test_image_extension_detection_marks_untracked(self):
        diff = ""
        bundle = build_diff_bundle(diff, ["hero.jpg", "script.py"], lambda _p: "x = 1\n")
        by_path = {f.path: f for f in bundle.files}
        assert by_path["hero.jpg"].is_image is True
        assert by_path["script.py"].is_image is False

    def test_image_extension_detection_from_diff(self):
        diff = (
            "diff --git a/banner.png b/banner.png\n"
            "index 1234..5678 100644\n"
            "--- a/banner.png\n"
            "+++ b/banner.png\n"
            "Binary files a/banner.png and b/banner.png differ\n"
        )
        files = parse_diff_files(diff)
        assert files[0].is_image is True
        assert files[0].is_binary is True

    def test_renders_very_large_diff_without_truncation(self):
        line_count = 5000
        huge_diff = (
            "diff --git a/big.py b/big.py\n"
            "--- a/big.py\n"
            "+++ b/big.py\n"
            f"@@ -1,{line_count} +1,{line_count} @@\n"
            + "".join(f"+line {i}\n" for i in range(line_count))
        )
        f = ChangedFile(
            path="big.py",
            status="M",
            additions=line_count,
            diff_text=huge_diff,
        )
        r = render_file_diff(f)
        syntax_pieces = [p for p in r.renderables if isinstance(p, Syntax)]
        assert len(syntax_pieces) == 1
        # No truncation marker should be injected and the full body
        # should be passed through to the Syntax renderer.
        syntax = syntax_pieces[0]
        assert "[gd-truncated]" not in syntax.code
        rendered_lines = len(syntax.code.splitlines())
        assert rendered_lines == line_count


class TestBuildDiffBundle:
    def test_combines_diff_and_untracked(self):
        def lookup(p):
            if p == "u1.py":
                return "alpha\nbeta\n"
            return None

        diff = "diff --git a/m1.py b/m1.py\n@@ -1 +1 @@\n-old\n+new\n"
        bundle = build_diff_bundle(diff, ["u1.py", "u2.py"], lookup)
        assert len(bundle.files) == 3
        statuses = [(f.path, f.status) for f in bundle.files]
        assert ("m1.py", "M") in statuses
        assert ("u1.py", "?") in statuses
        assert ("u2.py", "?") in statuses

    def test_untracked_synthetic_diff_is_valid(self):
        def lookup(p):
            return "x = 1\ny = 2\n"

        bundle = build_diff_bundle("", ["new.py"], lookup)
        f = bundle.files[0]
        assert f.status == "?"
        assert f.path == "new.py"
        assert "+x = 1" in f.diff_text
        assert "+y = 2" in f.diff_text

    def test_untracked_synthetic_diff_includes_hunk_header(self):
        # The synthetic diff for an untracked file MUST include a
        # ``@@`` hunk header, otherwise ``_split_diff_for_render``
        # can't separate the meta block from the body and the
        # ``+`` lines end up styled as the muted-gray "meta" caption
        # instead of the green "added" body.
        def lookup(p):
            return "x = 1\ny = 2\n"

        bundle = build_diff_bundle("", ["new.py"], lookup)
        f = bundle.files[0]
        assert "@@" in f.diff_text
        # The hunk header should reference the right line count so
        # the line-number gutter is correct.
        assert "@@ -0,0 +1,2 @@" in f.diff_text

    def test_untracked_with_zero_content_still_has_hunk_header(self):
        def lookup(p):
            return ""

        bundle = build_diff_bundle("", ["empty.py"], lookup)
        f = bundle.files[0]
        # An empty file still gets a well-formed synthetic diff so
        # the renderer doesn't fall back to the muted-gray meta
        # styling for its (non-existent) body.
        assert "@@" in f.diff_text
        assert "+" not in f.diff_text or "@@ -0,0" in f.diff_text

    def test_untracked_binary_lookup_returns_none_marks_unreadable(self):
        def lookup(p):
            return None

        bundle = build_diff_bundle("", ["weird.bin"], lookup)
        f = bundle.files[0]
        assert f.status == "?"
        assert "binary" in f.diff_text.lower() or "unreadable" in f.diff_text.lower()

    def test_raw_diff_preserved(self):
        diff = "diff --git a/a.py b/a.py\n"
        bundle = build_diff_bundle(diff, [], lambda p: None)
        assert bundle.raw == diff


class TestRenderEmptyState:
    def test_includes_repo_and_branch(self):
        text = render_empty_state("my-repo", "main")
        assert "my-repo" in text.plain
        assert "main" in text.plain
        assert "No uncommitted changes" in text.plain

    def test_handles_missing_branch(self):
        text = render_empty_state("my-repo", None)
        assert "my-repo" in text.plain
        assert "branch" not in text.plain


class TestRenderError:
    def test_includes_message(self):
        text = render_error("git not found")
        assert "git not found" in text.plain
        assert "Failed to load diff" in text.plain


class TestChangedFile:
    def test_status_label_for_known_status(self):
        assert ChangedFile(path="x", status="M").status_label == "modified"
        assert ChangedFile(path="x", status="A").status_label == "added"
        assert ChangedFile(path="x", status="D").status_label == "deleted"
        assert ChangedFile(path="x", status="R").status_label == "renamed"
        assert ChangedFile(path="x", status="?").status_label == "untracked"

    def test_status_label_for_unknown(self):
        assert ChangedFile(path="x", status="Z").status_label == "changed"

    def test_display_path_for_rename(self):
        f = ChangedFile(path="new.py", status="R", is_rename=True, old_path="old.py")
        assert f.display_path == "old.py \u2192 new.py"

    def test_display_path_for_normal(self):
        f = ChangedFile(path="foo.py", status="M")
        assert f.display_path == "foo.py"

    def test_is_untracked_property(self):
        assert ChangedFile(path="x", status="?").is_untracked is True
        assert ChangedFile(path="x", status="M").is_untracked is False


class TestDiffBundle:
    def test_default_empty(self):
        b = DiffBundle()
        assert b.files == []
        assert b.raw == ""
