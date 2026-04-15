"""Tests for gitdirector.info — repository file analysis with gitignore awareness."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from gitdirector.info import (
    FileTypeInfo,
    RepoInfoResult,
    _count_lines,
    _count_tokens,
    _get_non_ignored_files,
    gather_repo_info,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), capture_output=True, check=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _write(repo: Path, rel_path: str, content: str = "") -> Path:
    p = repo / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _commit_all(repo: Path, msg: str = "commit") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg, "--allow-empty")


# ---------------------------------------------------------------------------
# _count_lines
# ---------------------------------------------------------------------------


class TestCountLines:
    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        assert _count_lines(f) == 0

    def test_single_line_no_newline(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes(b"hello")
        assert _count_lines(f) == 1

    def test_single_line_with_newline(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes(b"hello\n")
        assert _count_lines(f) == 1

    def test_multiple_lines(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes(b"line1\nline2\nline3\n")
        assert _count_lines(f) == 3

    def test_multiple_lines_no_trailing_newline(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes(b"a\nb\nc")
        assert _count_lines(f) == 3

    def test_binary_file_returns_none(self, tmp_path):
        f = tmp_path / "bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        assert _count_lines(f) is None

    def test_binary_null_after_text(self, tmp_path):
        f = tmp_path / "mixed"
        f.write_bytes(b"text here\x00more binary")
        assert _count_lines(f) is None

    def test_nonexistent_file(self, tmp_path):
        assert _count_lines(tmp_path / "no-such-file") is None

    def test_large_text_file(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_bytes(b"line\n" * 10_000)
        assert _count_lines(f) == 10_000

    def test_binary_null_beyond_first_chunk(self, tmp_path):
        f = tmp_path / "late_null"
        f.write_bytes(b"a" * 9000 + b"\x00")
        assert _count_lines(f) is None


class TestCountTokens:
    def test_special_token_text_is_counted_as_normal_text(self):
        assert _count_tokens("before <|endoftext|> after") > 0

    def test_falls_back_when_encode_ordinary_is_unavailable(self):
        class LegacyEncoder:
            def encode(self, text: str, disallowed_special=()):
                assert text == "legacy text"
                assert disallowed_special == ()
                return [1, 2, 3]

        with patch("gitdirector.info._get_encoder", return_value=LegacyEncoder()):
            assert _count_tokens("legacy text") == 3


# ---------------------------------------------------------------------------
# _get_non_ignored_files — gitignore tests
# ---------------------------------------------------------------------------


class TestGetNonIgnoredFiles:
    def test_empty_repo(self, tmp_path):
        repo = _init_repo(tmp_path)
        _commit_all(repo, "initial")
        assert _get_non_ignored_files(repo) == []

    def test_tracked_files_returned(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "a.py", "x = 1\n")
        _write(repo, "b.txt", "hello\n")
        _commit_all(repo)
        files = sorted(_get_non_ignored_files(repo))
        assert files == ["a.py", "b.txt"]

    def test_untracked_non_ignored_included(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "tracked.py", "1\n")
        _commit_all(repo)
        _write(repo, "untracked.py", "2\n")
        files = sorted(_get_non_ignored_files(repo))
        assert "untracked.py" in files
        assert "tracked.py" in files

    def test_root_gitignore_excludes_files(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "*.log\nbuild/\n")
        _write(repo, "app.py", "x\n")
        _write(repo, "debug.log", "log data\n")
        _write(repo, "build/output.js", "compiled\n")
        _commit_all(repo)
        files = _get_non_ignored_files(repo)
        assert "app.py" in files
        assert ".gitignore" in files
        assert "debug.log" not in files
        assert "build/output.js" not in files

    def test_root_gitignore_directory_pattern(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "node_modules/\n")
        _write(repo, "index.js", "console.log('hi');\n")
        _write(repo, "node_modules/pkg/index.js", "module\n")
        _write(repo, "node_modules/pkg/package.json", "{}\n")
        _commit_all(repo)
        files = _get_non_ignored_files(repo)
        assert "index.js" in files
        for f in files:
            assert not f.startswith("node_modules/")

    def test_nested_gitignore(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "*.log\n")
        _write(repo, "src/.gitignore", "*.tmp\n")
        _write(repo, "src/app.py", "code\n")
        _write(repo, "src/cache.tmp", "temp\n")
        _write(repo, "src/error.log", "err\n")
        _write(repo, "root.log", "log\n")
        _commit_all(repo)
        files = _get_non_ignored_files(repo)
        assert "src/app.py" in files
        assert "src/.gitignore" in files
        assert "src/cache.tmp" not in files
        assert "src/error.log" not in files
        assert "root.log" not in files

    def test_deeply_nested_gitignore(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "a/b/c/.gitignore", "secret.*\n")
        _write(repo, "a/b/c/main.py", "pass\n")
        _write(repo, "a/b/c/secret.key", "key\n")
        _write(repo, "a/b/c/secret.json", "{}\n")
        _write(repo, "a/b/c/d/secret.txt", "not ignored here\n")
        _commit_all(repo)
        files = _get_non_ignored_files(repo)
        assert "a/b/c/main.py" in files
        assert "a/b/c/secret.key" not in files
        assert "a/b/c/secret.json" not in files
        assert "a/b/c/d/secret.txt" not in files

    def test_negation_pattern(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "*.log\n!important.log\n")
        _write(repo, "debug.log", "d\n")
        _write(repo, "important.log", "keep\n")
        _commit_all(repo)
        files = _get_non_ignored_files(repo)
        assert "debug.log" not in files
        assert "important.log" in files

    def test_wildcard_pattern(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "__pycache__/\n*.pyc\n")
        _write(repo, "app.py", "pass\n")
        _write(repo, "__pycache__/app.cpython-311.pyc", "\x00\x01")
        _write(repo, "lib/util.pyc", "\x00\x01")
        _commit_all(repo)
        files = _get_non_ignored_files(repo)
        assert "app.py" in files
        for f in files:
            assert "__pycache__" not in f
            assert not f.endswith(".pyc")

    def test_gitignore_with_comments_and_blanks(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "# comment\n\n*.bak\n\n# another comment\ntemp/\n")
        _write(repo, "file.txt", "ok\n")
        _write(repo, "file.bak", "backup\n")
        _write(repo, "temp/data", "tmp\n")
        _commit_all(repo)
        files = _get_non_ignored_files(repo)
        assert "file.txt" in files
        assert "file.bak" not in files
        for f in files:
            assert not f.startswith("temp/")

    def test_dot_git_never_included(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "file.txt", "hi\n")
        _commit_all(repo)
        files = _get_non_ignored_files(repo)
        for f in files:
            assert not f.startswith(".git/")
            assert f != ".git"

    def test_gitignore_star_star_pattern(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "**/dist/\n")
        _write(repo, "src/main.py", "pass\n")
        _write(repo, "dist/bundle.js", "compiled\n")
        _write(repo, "packages/a/dist/out.js", "compiled\n")
        _commit_all(repo)
        files = _get_non_ignored_files(repo)
        assert "src/main.py" in files
        for f in files:
            assert "/dist/" not in f and not f.startswith("dist/")

    def test_multiple_nested_gitignores_at_different_levels(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "*.log\n")
        _write(repo, "src/.gitignore", "*.generated.py\n")
        _write(repo, "src/lib/.gitignore", "vendor/\n")
        _write(repo, "src/app.py", "pass\n")
        _write(repo, "src/models.generated.py", "auto\n")
        _write(repo, "src/lib/util.py", "pass\n")
        _write(repo, "src/lib/vendor/dep.py", "dep\n")
        _write(repo, "root.log", "log\n")
        _commit_all(repo)
        files = _get_non_ignored_files(repo)
        assert "src/app.py" in files
        assert "src/lib/util.py" in files
        assert "src/models.generated.py" not in files
        assert "src/lib/vendor/dep.py" not in files
        assert "root.log" not in files

    def test_not_a_git_repo(self, tmp_path):
        files = _get_non_ignored_files(tmp_path)
        assert files == []

    def test_gitignore_trailing_spaces_handled(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "*.tmp   \n")
        _write(repo, "data.tmp", "temp\n")
        _write(repo, "keep.txt", "ok\n")
        _commit_all(repo)
        files = _get_non_ignored_files(repo)
        assert "keep.txt" in files
        # git itself handles trailing spaces in .gitignore - just verify it doesn't crash

    def test_gitignore_ignores_entire_subtree(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "ignored_dir/\n")
        _write(repo, "ignored_dir/a.py", "pass\n")
        _write(repo, "ignored_dir/sub/b.py", "pass\n")
        _write(repo, "ignored_dir/sub/deep/c.py", "pass\n")
        _write(repo, "keep.py", "pass\n")
        _commit_all(repo)
        files = _get_non_ignored_files(repo)
        assert "keep.py" in files
        for f in files:
            assert not f.startswith("ignored_dir/")


# ---------------------------------------------------------------------------
# gather_repo_info — integration tests
# ---------------------------------------------------------------------------


class TestGatherRepoInfo:
    def test_empty_repo(self, tmp_path):
        repo = _init_repo(tmp_path)
        _commit_all(repo, "initial")
        result = gather_repo_info(repo)
        assert result.total_files == 0
        assert result.file_types == []
        assert result.total_lines == 0
        assert result.total_tokens == 0
        assert result.max_depth == 0

    def test_single_file(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "hello.py", "print('hello')\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        assert result.total_files == 1
        assert result.total_lines == 1
        assert result.total_tokens > 0
        assert result.max_depth == 0
        assert len(result.file_types) == 1
        assert result.file_types[0].extension == ".py"
        assert result.file_types[0].count == 1
        assert result.file_types[0].line_count == 1
        assert result.file_types[0].token_count > 0

    def test_multiple_extensions(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "a.py", "line1\nline2\n")
        _write(repo, "b.py", "line1\n")
        _write(repo, "c.js", "var x = 1;\nvar y = 2;\nvar z = 3;\n")
        _write(repo, "d.md", "# Title\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        assert result.total_files == 4
        assert result.total_lines == 7
        exts = {ft.extension: ft for ft in result.file_types}
        assert exts[".py"].count == 2
        assert exts[".py"].line_count == 3
        assert exts[".js"].count == 1
        assert exts[".js"].line_count == 3
        assert exts[".md"].count == 1
        assert exts[".md"].line_count == 1

    def test_top_5_with_others(self, tmp_path):
        repo = _init_repo(tmp_path)
        for i, ext in enumerate(
            [
                ".py",
                ".js",
                ".ts",
                ".md",
                ".json",
                ".yaml",
                ".toml",
                ".css",
                ".html",
                ".xml",
                ".sql",
                ".sh",
            ]
        ):
            _write(repo, f"file{i}{ext}", "line\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        assert result.total_files == 12
        assert result.file_types[-1].extension == "others"
        assert result.file_types[-1].count == 2

    def test_full_flag_shows_all_extensions(self, tmp_path):
        repo = _init_repo(tmp_path)
        for i, ext in enumerate(
            [
                ".py",
                ".js",
                ".ts",
                ".md",
                ".json",
                ".yaml",
                ".toml",
                ".css",
                ".html",
                ".xml",
                ".sql",
                ".sh",
            ]
        ):
            _write(repo, f"file{i}{ext}", "line\n")
        _commit_all(repo)
        result = gather_repo_info(repo, full=True)
        assert result.total_files == 12
        extensions = [ft.extension for ft in result.file_types]
        assert "others" not in extensions
        assert len(result.file_types) == 12

    def test_full_flag_still_shows_no_ext_at_end(self, tmp_path):
        repo = _init_repo(tmp_path)
        for i, ext in enumerate(
            [".py", ".js", ".ts", ".md", ".json", ".yaml", ".toml", ".css", ".html", ".xml", ".sql"]
        ):
            _write(repo, f"file{i}{ext}", "line\n")
        _write(repo, "Makefile", "all:\n")
        _commit_all(repo)
        result = gather_repo_info(repo, full=True)
        assert result.file_types[-1].extension == "(no ext)"
        assert "others" not in [ft.extension for ft in result.file_types]

    def test_top_5_ordering_by_count(self, tmp_path):
        repo = _init_repo(tmp_path)
        for i in range(5):
            _write(repo, f"f{i}.py", "pass\n")
        for i in range(3):
            _write(repo, f"f{i}.js", "x\n")
        _write(repo, "readme.md", "# Hi\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        assert result.file_types[0].extension == ".py"
        assert result.file_types[0].count == 5
        assert result.file_types[1].extension == ".js"
        assert result.file_types[1].count == 3
        assert result.file_types[2].extension == ".md"
        assert result.file_types[2].count == 1

    def test_binary_extension_shows_none_lines(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "image.png", "not really a png\n")
        _write(repo, "app.py", "pass\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        exts = {ft.extension: ft for ft in result.file_types}
        assert exts[".png"].line_count is None
        assert exts[".png"].token_count is None
        assert exts[".py"].line_count == 1
        assert exts[".py"].token_count is not None

    def test_binary_content_shows_none_lines(self, tmp_path):
        repo = _init_repo(tmp_path)
        p = repo / "data.dat"
        p.write_bytes(b"\x00\x01\x02binary content")
        _write(repo, "code.py", "x = 1\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        exts = {ft.extension: ft for ft in result.file_types}
        assert exts[".dat"].line_count is None
        assert exts[".dat"].token_count is None
        assert exts[".py"].line_count == 1

    def test_binary_content_with_text_extension_shows_none_lines(self, tmp_path):
        repo = _init_repo(tmp_path)
        p = repo / "notes.txt"
        p.write_bytes(b"\x00\x01\x02binary content")
        _write(repo, "code.py", "x = 1\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        exts = {ft.extension: ft for ft in result.file_types}
        assert exts[".txt"].line_count is None
        assert exts[".txt"].token_count is None
        assert exts[".py"].line_count == 1

    def test_max_depth_flat(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "a.py", "pass\n")
        _write(repo, "b.py", "pass\n")
        _commit_all(repo)
        assert gather_repo_info(repo).max_depth == 0

    def test_max_depth_nested(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "a.py", "pass\n")
        _write(repo, "src/b.py", "pass\n")
        _write(repo, "src/lib/c.py", "pass\n")
        _write(repo, "src/lib/utils/d.py", "pass\n")
        _commit_all(repo)
        assert gather_repo_info(repo).max_depth == 3

    def test_max_depth_with_gitignore_excluded_deep_paths(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "deep/\n")
        _write(repo, "a.py", "pass\n")
        _write(repo, "deep/a/b/c/d/e.py", "pass\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        assert result.max_depth == 0
        assert result.total_files == 2  # a.py + .gitignore

    def test_gitignored_files_not_counted(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "*.log\nsecrets/\n")
        _write(repo, "app.py", "pass\n")
        _write(repo, "debug.log", "lots of log lines\n" * 100)
        _write(repo, "secrets/api_key.txt", "key=abc\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        assert result.total_files == 2  # app.py + .gitignore
        exts = {ft.extension: ft for ft in result.file_types}
        assert ".log" not in exts
        assert ".txt" not in exts
        assert result.total_lines == 3  # app.py(1) + .gitignore(2)

    def test_gitignored_files_excluded_from_line_count(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "generated/\n")
        _write(repo, "main.py", "x = 1\ny = 2\n")
        _write(repo, "generated/big.py", "pass\n" * 10_000)
        _commit_all(repo)
        result = gather_repo_info(repo)
        assert result.total_lines == 3  # main.py(2) + .gitignore(1)

    def test_no_ext_files(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "Makefile", "all:\n\techo hi\n")
        _write(repo, "Dockerfile", "FROM python:3.11\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        exts = {ft.extension: ft for ft in result.file_types}
        assert "(no ext)" in exts
        assert exts["(no ext)"].count == 2

    def test_no_ext_after_regular_before_others(self, tmp_path):
        repo = _init_repo(tmp_path)
        for i, ext in enumerate(
            [
                ".py",
                ".js",
                ".ts",
                ".md",
                ".json",
                ".yaml",
                ".toml",
                ".css",
                ".html",
                ".xml",
                ".sql",
                ".sh",
            ]
        ):
            _write(repo, f"file{i}{ext}", "line\n")
        _write(repo, "Makefile", "all:\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        extensions = [ft.extension for ft in result.file_types]
        no_ext_idx = extensions.index("(no ext)")
        others_idx = extensions.index("others")
        assert no_ext_idx < others_idx
        assert no_ext_idx == len(extensions) - 2
        assert others_idx == len(extensions) - 1

    def test_token_counts(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "hello.py", "print('hello world')\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        assert result.total_tokens > 0
        assert result.file_types[0].token_count > 0
        assert result.file_types[0].token_count == result.total_tokens

    def test_token_counts_with_special_token_text(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "prompt.txt", "alpha <|endoftext|> omega\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        assert result.total_tokens > 0
        assert result.file_types[0].extension == ".txt"
        assert result.file_types[0].token_count == result.total_tokens

    def test_binary_files_excluded_from_token_count(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "app.py", "x = 1\n")
        p = repo / "data.bin"
        p.write_bytes(b"\x00" * 1000)
        _commit_all(repo)
        result = gather_repo_info(repo)
        exts = {ft.extension: ft for ft in result.file_types}
        assert exts[".bin"].token_count is None
        assert result.total_tokens > 0

    def test_case_insensitive_extensions(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "a.PY", "pass\n")
        _write(repo, "b.py", "pass\n")
        _write(repo, "c.Py", "pass\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        assert len(result.file_types) == 1
        assert result.file_types[0].extension == ".py"
        assert result.file_types[0].count == 3

    def test_others_with_mixed_binary_and_text(self, tmp_path):
        repo = _init_repo(tmp_path)
        for i in range(5):
            _write(repo, f"f{i}.py", "pass\n")
        for i in range(3):
            _write(repo, f"f{i}.js", "x\n")
        for i in range(2):
            _write(repo, f"f{i}.ts", "x\n")
        for i in range(2):
            _write(repo, f"f{i}.md", "# hi\n")
        for i in range(2):
            _write(repo, f"f{i}.json", "{}\n")
        for i in range(2):
            _write(repo, f"f{i}.yaml", "a: 1\n")
        for i in range(2):
            _write(repo, f"f{i}.toml", "a = 1\n")
        for i in range(2):
            _write(repo, f"f{i}.css", "body {}\n")
        for i in range(2):
            _write(repo, f"f{i}.html", "<p>hi</p>\n")
        for i in range(2):
            _write(repo, f"f{i}.xml", "<x/>\n")
        # These will be in "others"
        _write(repo, "a.sql", "SELECT 1;\n")
        img = repo / "b.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        _commit_all(repo)
        result = gather_repo_info(repo)
        others = next(ft for ft in result.file_types if ft.extension == "others")
        assert others.count == 2
        assert others.line_count is not None

    def test_others_all_binary(self, tmp_path):
        repo = _init_repo(tmp_path)
        for i in range(5):
            _write(repo, f"f{i}.py", "pass\n")
        for i in range(3):
            _write(repo, f"f{i}.js", "x\n")
        for i in range(2):
            _write(repo, f"f{i}.ts", "x\n")
        for i in range(2):
            _write(repo, f"f{i}.md", "# hi\n")
        for i in range(2):
            _write(repo, f"f{i}.json", "{}\n")
        for i in range(2):
            _write(repo, f"f{i}.yaml", "a: 1\n")
        for i in range(2):
            _write(repo, f"f{i}.toml", "a = 1\n")
        for i in range(2):
            _write(repo, f"f{i}.css", "body {}\n")
        for i in range(2):
            _write(repo, f"f{i}.html", "<p>hi</p>\n")
        for i in range(2):
            _write(repo, f"f{i}.xml", "<x/>\n")
        for ext in [".png", ".jpg", ".gif"]:
            p = repo / f"img{ext}"
            p.write_bytes(b"\x00" * 10)
        _commit_all(repo)
        result = gather_repo_info(repo)
        others = next(ft for ft in result.file_types if ft.extension == "others")
        assert others.line_count is None
        assert others.token_count is None

    def test_exactly_5_extensions_no_others(self, tmp_path):
        repo = _init_repo(tmp_path)
        for ext in [".py", ".js", ".ts", ".md", ".json"]:
            _write(repo, f"file{ext}", "line\n")
        _commit_all(repo)
        result = gather_repo_info(repo)
        assert len(result.file_types) == 5
        assert all(ft.extension != "others" for ft in result.file_types)

    def test_nested_gitignore_excludes_from_stats(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "src/.gitignore", "*.generated.py\n")
        _write(repo, "src/app.py", "x = 1\ny = 2\nz = 3\n")
        _write(repo, "src/auto.generated.py", "pass\n" * 500)
        _commit_all(repo)
        result = gather_repo_info(repo)
        assert result.total_lines < 500

    def test_untracked_files_included_if_not_ignored(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, "tracked.py", "pass\n")
        _commit_all(repo)
        _write(repo, "new_file.py", "x = 1\ny = 2\n")
        result = gather_repo_info(repo)
        assert result.total_files == 2
        assert result.total_lines == 3

    def test_untracked_files_excluded_if_ignored(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "*.tmp\n")
        _write(repo, "tracked.py", "pass\n")
        _commit_all(repo)
        _write(repo, "scratch.tmp", "temporary data\n")
        result = gather_repo_info(repo)
        exts = {ft.extension: ft for ft in result.file_types}
        assert ".tmp" not in exts

    def test_subprocess_failure_returns_empty(self, tmp_path):
        with patch("gitdirector.info.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=128, stdout=b"", stderr=b""
            )
            result = gather_repo_info(tmp_path)
        assert result.total_files == 0
        assert result.file_types == []

    def test_gitignore_pattern_applied_to_subdirectory_files(self, tmp_path):
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "*.env\n")
        _write(repo, ".env", "SECRET=abc\n")
        _write(repo, "src/.env", "DB_URL=x\n")
        _write(repo, "src/lib/.env", "KEY=y\n")
        _write(repo, "src/app.py", "pass\n")
        _commit_all(repo)
        files = _get_non_ignored_files(repo)
        for f in files:
            assert not f.endswith(".env"), f"gitignored file included: {f}"

    def test_file_type_info_dataclass(self):
        ft = FileTypeInfo(extension=".py", count=10, line_count=500, token_count=2000)
        assert ft.extension == ".py"
        assert ft.count == 10
        assert ft.line_count == 500
        assert ft.token_count == 2000

    def test_repo_info_result_dataclass(self):
        r = RepoInfoResult(
            total_files=100,
            file_types=[FileTypeInfo(".py", 50, 1000, 5000)],
            total_lines=1000,
            total_tokens=5000,
            max_depth=3,
        )
        assert r.total_files == 100
        assert len(r.file_types) == 1
        assert r.total_lines == 1000
        assert r.total_tokens == 5000
        assert r.max_depth == 3


# ---------------------------------------------------------------------------
# CLI info command tests
# ---------------------------------------------------------------------------


class TestInfoCommand:
    def test_info_by_path(self, tmp_path, runner):
        from gitdirector.cli import cli

        repo = _init_repo(tmp_path)
        _write(repo, "hello.py", "print('hi')\n")
        _commit_all(repo)

        result = runner.invoke(cli, ["info", str(repo)])
        assert result.exit_code == 0
        assert "hello.py" not in result.output or "Files" in result.output
        assert "1" in result.output

    def test_info_by_name(self, tmp_path, runner, monkeypatch):
        from gitdirector.cli import cli

        repo = _init_repo(tmp_path)
        _write(repo, "hello.py", "print('hi')\n")
        _commit_all(repo)

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        config_dir = tmp_path / "home" / ".gitdirector"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text(f"repositories:\n  - {repo}\n")

        result = runner.invoke(cli, ["info", "repo"])
        assert result.exit_code == 0

    def test_info_not_found(self, tmp_path, runner, monkeypatch):
        from gitdirector.cli import cli

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        config_dir = tmp_path / "home" / ".gitdirector"
        config_dir.mkdir(parents=True)
        (config_dir / "config.yaml").write_text("repositories: []\n")

        result = runner.invoke(cli, ["info", "nonexistent"])
        assert result.exit_code != 0

    def test_info_ambiguous(self, tmp_path, runner, monkeypatch):
        from gitdirector.cli import cli

        repo1 = tmp_path / "my-app-one"
        repo1.mkdir()
        (repo1 / ".git").mkdir()
        repo2 = tmp_path / "my-app-two"
        repo2.mkdir()
        (repo2 / ".git").mkdir()

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        config_dir = tmp_path / "home" / ".gitdirector"
        config_dir.mkdir(parents=True)
        (config_dir / "config.yaml").write_text(f"repositories:\n  - {repo1}\n  - {repo2}\n")

        result = runner.invoke(cli, ["info", "my-app"])
        assert result.exit_code != 0
