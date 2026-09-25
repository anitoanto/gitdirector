import json
import runpy
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.utils import strip_ansi

from gitdirector.cli import cli, main
from gitdirector.commands import display_path, fit_left, format_size, status_text
from gitdirector.commands.pull import summarize_pull
from gitdirector.repo import RepositoryInfo, RepoStatus

# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


class TestFormatSize:
    @pytest.mark.parametrize(
        "size,expected",
        [(None, "-"), (500, "500 B"), (2048, "2.0 KB"), (2 << 20, "2.0 MB"), (2 << 30, "2.0 GB")],
    )
    def test_units(self, size, expected):
        assert format_size(size).plain == expected


def _info(status=RepoStatus.UP_TO_DATE, **kwargs):
    return RepositoryInfo(Path("/r/repo"), "repo", status, "main", **kwargs)


class TestStatusText:
    """The CLI speaks the console's status language."""

    @pytest.mark.parametrize(
        "info,expected",
        [
            (_info(), "clean"),
            (_info(RepoStatus.AHEAD, ahead=2), "↑2 to push"),
            (_info(RepoStatus.BEHIND, behind=3), "↓3 to pull"),
            (_info(RepoStatus.DIVERGED, ahead=1, behind=1), "↑1 to push · ↓1 to pull"),
            (
                _info(staged=True, staged_files=["a"], unstaged=True, unstaged_files=["b", "c"]),
                "1 staged · 2 changed",
            ),
            (_info(RepoStatus.UNKNOWN, message="No origin/main branch"), "no remote branch"),
            (_info(RepoStatus.UNKNOWN), "sync unknown"),
            (_info(RepoStatus.BEHIND, behind=1, sync_stale=True), "↓1 to pull · offline"),
        ],
    )
    def test_labels(self, info, expected):
        assert status_text(info).plain == expected


class TestFitLeft:
    def test_short_text_is_unchanged(self):
        assert fit_left("/a/b", 10) == "/a/b"

    def test_keeps_the_tail(self):
        assert fit_left("/very/long/leading/dirs/myrepo", 12) == "…dirs/myrepo"

    @pytest.mark.parametrize("width", [0, 1, 2])
    def test_tiny_widths_do_not_crash(self, width):
        assert len(fit_left("/Users/example/myrepo", width)) <= max(width, 0)


class TestDisplayPath:
    def test_home_is_shortened(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert display_path(tmp_path / "src" / "app") == "~/src/app"
        assert display_path(tmp_path) == "~"

    def test_other_paths_are_unchanged(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        assert display_path(tmp_path / "homework") == str(tmp_path / "homework")


class TestSummarizePull:
    def test_up_to_date(self):
        assert summarize_pull(True, "Already up to date.") == "up to date"

    def test_fast_forward(self):
        output = (
            "Updating a1b2c3d..e4f5a6b\nFast-forward\n x | 2 +-\n 1 file changed, 1 insertion(+)"
        )
        assert summarize_pull(True, output) == "a1b2c3d..e4f5a6b · 1 file changed, 1 insertion(+)"

    def test_failure_shows_first_line(self):
        assert summarize_pull(False, "fatal: Not possible to fast-forward\nhint") == (
            "Not possible to fast-forward"
        )


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def _mock_manager(**overrides):
    mgr = MagicMock()
    mgr.config.repositories = []
    mgr.config.max_workers = 2
    mgr.resolve_repository_target = MagicMock(return_value=(None, [], False))
    for key, val in overrides.items():
        setattr(mgr, key, MagicMock(return_value=val))
    return mgr


class TestLinkCommand:
    def test_link_success(self, runner, tmp_path):
        repo = tmp_path / "my-repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        mgr = _mock_manager(add_repository=(True, f"Added repository: {repo}", [repo], []))
        with patch("gitdirector.commands.link.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["link", str(repo)])
        assert result.exit_code == 0
        assert "my-repo" in result.output

    def test_link_failure(self, runner, tmp_path):
        mgr = _mock_manager(add_repository=(False, "Not a git repository: /x", [], []))
        with patch("gitdirector.commands.link.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["link", str(tmp_path)])
        assert result.exit_code == 1

    def test_link_discover(self, runner, tmp_path):
        mgr = _mock_manager(
            add_repository=(True, "Added 2 repositories", [tmp_path / "a", tmp_path / "b"], [])
        )
        with patch("gitdirector.commands.link.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["link", str(tmp_path), "--discover"])
        assert result.exit_code == 0
        assert "Tracking 2 new repositories" in result.output

    def test_link_discover_with_skipped(self, runner, tmp_path):
        """--discover with skipped repos prints skipped messages."""
        skipped = [tmp_path / "already-tracked"]
        mgr = _mock_manager(
            add_repository=(True, "Added 1 repository", [tmp_path / "new"], skipped)
        )
        with patch("gitdirector.commands.link.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["link", str(tmp_path), "--discover"])
        assert result.exit_code == 0
        assert "1 already tracked" in result.output

    def test_link_discover_none_found(self, runner, tmp_path):
        """--discover finds no repositories: should print message and succeed."""
        mgr = _mock_manager(add_repository=(True, "No git repositories found", [], []))
        with patch("gitdirector.commands.link.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["link", str(tmp_path), "--discover"])
        assert result.exit_code == 0
        assert "No git repositories found" in result.output


class TestUnlinkCommand:
    def test_unlink_success(self, runner, tmp_path):
        mgr = _mock_manager(remove_repository=(True, "Removed repository: /r", [tmp_path]))
        mgr.resolve_repository_target.return_value = (tmp_path, [], True)
        with patch("gitdirector.commands.unlink.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["unlink", str(tmp_path)])
        assert result.exit_code == 0

    def test_unlink_failure(self, runner, tmp_path):
        mgr = _mock_manager(remove_repository=(False, "Repository not tracked: /x", []))
        with patch("gitdirector.commands.unlink.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["unlink", str(tmp_path)])
        assert result.exit_code == 1

    def test_unlink_by_name_success(self, runner, tmp_path):
        """A resolved plain name removes its tracked repository path."""
        mgr = _mock_manager(
            remove_repository=(True, f"Removed repository: {tmp_path}", [tmp_path]),
        )
        mgr.resolve_repository_target.return_value = (tmp_path, [], False)
        with patch("gitdirector.commands.unlink.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["unlink", "my-repo"])
        assert result.exit_code == 0

    def test_unlink_by_name_not_found(self, runner):
        """Returns exit code 1 when name is not tracked."""
        mgr = _mock_manager(
            remove_repository=(False, "Repository not tracked", []),
        )
        with patch("gitdirector.commands.unlink.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["unlink", "my-repo"])
        assert result.exit_code == 1
        assert "my-repo" in result.output

    def test_unlink_by_name_ambiguous(self, runner):
        """Returns exit code 1 when multiple repos share the same name."""
        mgr = _mock_manager(
            remove_repository=(False, "Repository not tracked", []),
        )
        mgr.resolve_repository_target.return_value = (
            None,
            [Path("/a/my-repo"), Path("/b/my-repo")],
            False,
        )
        with patch("gitdirector.commands.unlink.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["unlink", "my-repo"])
        assert result.exit_code == 1
        assert "use the full path" in result.output
        mgr.remove_repository.assert_not_called()

    def test_unlink_by_path_does_not_fall_back_to_name_lookup(self, runner, tmp_path):
        """A path that is not tracked must fail instead of being retried as a name."""
        mgr = _mock_manager(
            remove_repository=(False, "Repository not tracked: /some/path/repo", []),
        )
        mgr.resolve_repository_target.return_value = (None, [], True)
        with patch("gitdirector.commands.unlink.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["unlink", str(tmp_path / "repo")])
        assert result.exit_code == 1
        assert "No tracked repository at path" in result.output
        mgr.remove_repository.assert_not_called()

    @pytest.mark.parametrize("dot_target", [".", ".."])
    def test_unlink_dot_is_treated_as_a_path(self, runner, dot_target):
        """. and .. should be treated as paths, not names, and must not fall back."""
        mgr = _mock_manager(
            remove_repository=(False, f"Repository not tracked: {dot_target}", []),
        )
        mgr.resolve_repository_target.return_value = (None, [], True)
        with patch("gitdirector.commands.unlink.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["unlink", dot_target])
        assert result.exit_code == 1
        mgr.remove_repository.assert_not_called()


class TestListCommand:
    def test_empty(self, runner):
        mgr = _mock_manager()
        mgr.config.repositories = []
        with patch("gitdirector.commands.listt.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        assert "No repositories tracked" in result.output

    def test_with_repos(self, runner, fake_git_repo):
        info = RepositoryInfo(
            fake_git_repo,
            fake_git_repo.name,
            RepoStatus.UP_TO_DATE,
            "main",
            last_updated="1 hour ago",
            size=1024,
        )
        mgr = _mock_manager(get_repository_status=info)
        mgr.config.repositories = [fake_git_repo]
        with patch("gitdirector.commands.listt.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        assert fake_git_repo.name in result.output

    def test_with_multiple_repos(self, runner, tmp_path):
        """List with >1 repo triggers Live multi-update UI."""
        repo1 = tmp_path / "repo1"
        repo2 = tmp_path / "repo2"
        repo1.mkdir()
        repo2.mkdir()
        info1 = RepositoryInfo(repo1, "repo1", RepoStatus.UP_TO_DATE, "main")
        info2 = RepositoryInfo(repo2, "repo2", RepoStatus.UP_TO_DATE, "dev")
        mgr = _mock_manager()
        mgr.get_repository_status = lambda path, fetch=False, include_size=False: (
            info1 if path == repo1 else info2
        )
        mgr.config.repositories = [repo1, repo2]
        with patch("gitdirector.commands.listt.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        assert "repo1" in result.output
        assert "repo2" in result.output
        # Check spinner/table summary for plural
        assert "2 repositories" in result.output or "2 repos" in result.output

    def test_json(self, runner, tmp_path):
        info = RepositoryInfo(
            tmp_path / "repo",
            "repo",
            RepoStatus.DIVERGED,
            "main",
            ahead=1,
            behind=2,
            size=10,
            last_updated="1 day ago",
        )
        mgr = _mock_manager(get_repository_status=info)
        mgr.config.repositories = [info.path]
        with patch("gitdirector.commands.listt.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["list", "--json"])
        assert result.exit_code == 0
        [repo] = json.loads(result.stdout)
        assert repo["sync"] == "diverged"
        assert (repo["ahead"], repo["behind"], repo["size"]) == (1, 2, 10)
        assert repo["path"] == str(info.path)

    def test_no_fetch_skips_the_network(self, runner, tmp_path):
        mgr = _mock_manager()
        mgr.config.repositories = [tmp_path / "repo"]
        with patch("gitdirector.commands.listt.RepositoryManager", return_value=mgr):
            runner.invoke(cli, ["list", "--no-fetch"])
        mgr.get_repository_status.assert_called_once_with(
            tmp_path / "repo", fetch=False, include_size=True
        )

    def test_piped_table_is_never_truncated(self, runner, tmp_path):
        path = tmp_path / ("very-long-directory-name-" * 8) / "repo"
        info = RepositoryInfo(path, "repo", RepoStatus.UP_TO_DATE, "main")
        mgr = _mock_manager(get_repository_status=info)
        mgr.config.repositories = [path]
        with patch("gitdirector.commands.listt.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["list"])
        assert str(path) in result.stdout
        assert all(line == line.rstrip() for line in result.stdout.splitlines())


class TestStatusCommand:
    def test_empty(self, runner):
        mgr = _mock_manager()
        mgr.config.repositories = []
        with patch("gitdirector.commands.listt.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "No repositories tracked" in result.output

    def test_all_clean(self, runner, fake_git_repo):
        info = RepositoryInfo(
            fake_git_repo,
            fake_git_repo.name,
            RepoStatus.UP_TO_DATE,
            "main",
        )
        mgr = _mock_manager(get_repository_status=info)
        mgr.config.repositories = [fake_git_repo]
        with patch("gitdirector.commands.listt.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "clean" in result.output.lower()

    def test_dirty(self, runner, fake_git_repo):
        info = RepositoryInfo(
            fake_git_repo,
            fake_git_repo.name,
            RepoStatus.UP_TO_DATE,
            "main",
            staged=True,
            staged_files=["a.py"],
        )
        mgr = _mock_manager(get_repository_status=info)
        mgr.config.repositories = [fake_git_repo]
        with patch("gitdirector.commands.listt.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "staged   a.py" in result.output
        assert "1 with changes" in result.output

    def test_json_lists_only_dirty_repositories(self, runner, tmp_path):
        clean = RepositoryInfo(tmp_path / "a", "a", RepoStatus.UP_TO_DATE, "main")
        dirty = RepositoryInfo(
            tmp_path / "b", "b", RepoStatus.UP_TO_DATE, "dev", unstaged=True, unstaged_files=["x"]
        )
        mgr = _mock_manager()
        mgr.config.repositories = [clean.path, dirty.path]
        mgr.get_repository_status = lambda path, **_: clean if path == clean.path else dirty
        with patch("gitdirector.commands.listt.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert [repo["name"] for repo in data] == ["b"]
        assert data[0]["unstaged"] == ["x"] and data[0]["branch"] == "dev"

    def test_dirty_with_unstaged(self, runner, fake_git_repo):
        info = RepositoryInfo(
            fake_git_repo,
            fake_git_repo.name,
            RepoStatus.UP_TO_DATE,
            "main",
            unstaged=True,
            unstaged_files=["b.py"],
        )
        mgr = _mock_manager(get_repository_status=info)
        mgr.config.repositories = [fake_git_repo]
        with patch("gitdirector.commands.listt.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "changed" in result.output.lower()

    def test_multiple_repos_updates_progress_display(self, runner, tmp_path):
        repo1 = tmp_path / "alpha"
        repo2 = tmp_path / "beta"
        repo1.mkdir()
        repo2.mkdir()
        info1 = RepositoryInfo(repo1, repo1.name, RepoStatus.UP_TO_DATE, "main")
        info2 = RepositoryInfo(
            repo2,
            repo2.name,
            RepoStatus.UP_TO_DATE,
            "develop",
            staged=True,
            staged_files=["tracked.py"],
        )
        mgr = _mock_manager()
        mgr.config.repositories = [repo1, repo2]
        mgr.get_repository_status = lambda path, **_: info1 if path == repo1 else info2

        with patch("gitdirector.commands.listt.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["status"])

        assert result.exit_code == 0
        assert "2 repositories" in strip_ansi(result.output)


class TestPullCommand:
    def test_empty(self, runner):
        mgr = _mock_manager()
        mgr.config.repositories = []
        with patch("gitdirector.commands.pull.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["pull"])
        assert result.exit_code == 0
        assert "No repositories tracked" in result.output

    def test_all_success(self, runner, fake_git_repo):
        mgr = _mock_manager()
        mgr.config.repositories = [fake_git_repo]
        mgr.config.max_workers = 2

        with patch("gitdirector.commands.pull.RepositoryManager", return_value=mgr):
            with patch(
                "gitdirector.commands.pull.pull_repository",
                return_value=(fake_git_repo.name, True, "Already up to date."),
            ):
                result = runner.invoke(cli, ["pull", "-y"])
        assert result.exit_code == 0
        assert "1 repository" in result.output

    def test_failure_exits_1(self, runner, fake_git_repo):
        mgr = _mock_manager()
        mgr.config.repositories = [fake_git_repo]
        mgr.config.max_workers = 2

        with patch("gitdirector.commands.pull.RepositoryManager", return_value=mgr):
            with patch(
                "gitdirector.commands.pull.pull_repository",
                return_value=(fake_git_repo.name, False, "Cannot fast-forward"),
            ):
                result = runner.invoke(cli, ["pull", "-y"])
        assert result.exit_code == 1
        assert "failed" in result.output.lower()

    def test_abort_confirmation(self, runner, fake_git_repo):
        mgr = _mock_manager()
        mgr.config.repositories = [fake_git_repo]
        mgr.config.max_workers = 2

        with patch("gitdirector.commands.pull.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["pull"], input="n\n")
        assert result.exit_code == 0
        assert "Pull 1 repository?" in result.output
        assert "REPOSITORY" not in result.output

    def test_confirmed_multiple_repos_updates_progress(self, runner, tmp_path):
        repo1 = tmp_path / "alpha"
        repo2 = tmp_path / "beta"
        repo1.mkdir()
        repo2.mkdir()
        mgr = _mock_manager()
        mgr.config.repositories = [repo1, repo2]
        mgr.config.max_workers = 2

        with patch("gitdirector.commands.pull.RepositoryManager", return_value=mgr):
            with patch(
                "gitdirector.commands.pull.pull_repository",
                side_effect=lambda path: (path.name, True, "Already up to date."),
            ):
                result = runner.invoke(cli, ["pull"], input="y\n")

        assert result.exit_code == 0
        assert "2 repositories" in result.output


class TestConsoleCommand:
    @patch("gitdirector.commands.tui.app._run_console")
    def test_console_command_invokes_tui_runner(self, mock_run_console, runner):
        result = runner.invoke(cli, ["console"])

        assert result.exit_code == 0
        mock_run_console.assert_called_once_with()

    def test_console_command_leaves_the_update_notice_to_the_dashboard(self, runner):
        with patch(
            "gitdirector.version_check.get_update_notice",
            return_value="Update available: v1.5.0 (current v1.4.2)",
        ):
            with patch("gitdirector.commands.tui.app._run_console"):
                result = runner.invoke(cli, ["console"])

        assert result.exit_code == 0
        assert "Update available" not in result.output


class TestMainEntry:
    def test_main_exception_handler(self, runner):
        with patch("gitdirector.cli.cli", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit):
                main()

    def test_cli_module_runs_main_from_dunder_main(self, monkeypatch):
        import sys

        import click

        calls = []

        def fake_main(self, *args, **kwargs):
            calls.append((self.name, args, kwargs))
            return None

        monkeypatch.setattr(click.core.Command, "main", fake_main)
        monkeypatch.setattr(sys, "argv", ["gitdirector"])

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=(
                    r"'gitdirector\.cli' found in sys\.modules after import of "
                    r"package 'gitdirector'"
                ),
                category=RuntimeWarning,
            )
            runpy.run_module("gitdirector.cli", run_name="__main__")

        assert len(calls) == 1


class TestHelpCommand:
    def test_help(self, runner):
        result = runner.invoke(cli, ["help"])
        assert result.exit_code == 0
        assert "GITDIRECTOR" in result.output
        # Every registered command appears with the first line of its docstring.
        for name, command in cli.commands.items():
            assert name in result.output
            assert command.get_short_help_str(limit=90).split()[0] in result.output
        assert "gitdirector COMMAND --help" in result.output

    def test_dash_h_is_an_alias_for_help_everywhere(self, runner):
        assert "GITDIRECTOR" in runner.invoke(cli, ["-h"]).output
        result = runner.invoke(cli, ["link", "-h"], prog_name="gitdirector")
        assert result.exit_code == 0
        assert "Usage: gitdirector link [OPTIONS] PATH" in result.output
        assert "-h, --help" in result.output

    def test_version_flag(self, runner):
        from gitdirector.commands import get_version

        for flag in ("--version", "-V"):
            result = runner.invoke(cli, [flag])
            assert result.exit_code == 0
            assert result.output.strip() == f"gitdirector {get_version()}"

    def test_update_notice_is_deferred_and_goes_to_stderr(self, runner):
        mgr = _mock_manager()
        with patch(
            "gitdirector.version_check.get_update_notice",
            return_value="Update available: v9.9.9 (current v1.0.0)",
        ):
            with patch("gitdirector.commands.listt.RepositoryManager", return_value=mgr):
                result = runner.invoke(cli, ["list"])

        assert result.exit_code == 0
        assert "Update available: v9.9.9" in result.stderr
        assert "Update available" not in result.stdout
        # Printed after the command's own output, not before it.
        assert result.output.index("No repositories tracked") < result.output.index(
            "Update available"
        )

    def test_errors_go_to_stderr(self, runner, tmp_path):
        mgr = _mock_manager(add_repository=(False, "Not a git repository: /x", [], []))
        with patch("gitdirector.commands.link.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["link", str(tmp_path)])
        assert result.exit_code == 1
        assert "Error: Not a git repository: /x" in strip_ansi(result.stderr)
        assert "Not a git repository" not in result.stdout

    def test_no_args_shows_help(self, runner):
        result = runner.invoke(cli, [])
        assert result.exit_code == 0
        assert "GITDIRECTOR" in result.output


class TestCompletionCommand:
    def test_completion_zsh_outputs_setup_script(self, runner):
        result = runner.invoke(cli, ["completion", "zsh"])

        assert result.exit_code == 0
        assert "#compdef gitdirector" in result.output
        assert "_GITDIRECTOR_COMPLETE=zsh_complete" in result.output

    def test_completion_zsh_includes_compinit_bootstrap(self, runner):
        """The eval'd script must load compinit so `compdef` is defined.

        Without this bootstrap, fresh zsh shells that haven't loaded
        compinit yet report ``command not found: compdef`` when the
        user runs ``eval "$(gitdirector completion zsh)"``.
        """
        result = runner.invoke(cli, ["completion", "zsh"])

        assert result.exit_code == 0
        assert "autoload -U +X compinit" in result.output
        assert "typeset -f compdef" in result.output
        assert result.output.index("#compdef gitdirector") < result.output.index(
            "autoload -U +X compinit"
        )

    def test_completion_zsh_bootstrap_appears_before_function(self, runner):
        result = runner.invoke(cli, ["completion", "zsh"])

        assert result.exit_code == 0
        bootstrap_idx = result.output.index("autoload -U +X compinit")
        function_idx = result.output.index("_gitdirector_completion() {")
        assert bootstrap_idx < function_idx

    def test_completion_zsh_eval_works_without_prior_compinit(self, runner, tmp_path):
        """End-to-end: eval the zsh output in a shell that never loaded compinit.

        Spawns ``zsh -f`` (no rc files) and evaluates the output.
        Without the bootstrap this raises ``compdef: command not found``.
        """
        import shutil
        import subprocess

        if not shutil.which("zsh"):
            pytest.skip("zsh is not installed on this system")

        result = runner.invoke(cli, ["completion", "zsh"])
        assert result.exit_code == 0

        script = tmp_path / "completion.zsh"
        script.write_text(result.output)
        proc = subprocess.run(
            ["zsh", "-f", "-c", f'source "{script}" && echo OK'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 0, (
            f"zsh eval failed.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        assert "OK" in proc.stdout
        assert "command not found" not in proc.stderr

    def test_completion_bash_output_is_unchanged(self, runner):
        """The bash bootstrap should NOT be injected — bash is not patched."""
        result = runner.invoke(cli, ["completion", "bash"])

        assert result.exit_code == 0
        assert "autoload -U +X compinit" not in result.output

    def test_completion_unknown_shell_fails(self, runner):
        result = runner.invoke(cli, ["completion", "powershell"])

        assert result.exit_code == 2
        assert "Invalid value" in result.output

    def test_completion_output_does_not_include_update_notice(self, runner):
        with patch(
            "gitdirector.version_check.get_update_notice",
            return_value="Update available: v1.5.0 (current v1.4.2)",
        ):
            result = runner.invoke(cli, ["completion", "fish"])

        assert result.exit_code == 0
        assert "Update available" not in result.output
        assert "_GITDIRECTOR_COMPLETE=fish_complete" in result.output

    def test_shell_completion_lists_subcommands(self, runner):
        result = runner.invoke(
            cli,
            [],
            env={
                "_GITDIRECTOR_COMPLETE": "bash_complete",
                "COMP_WORDS": "gitdirector ",
                "COMP_CWORD": "1",
            },
            prog_name="gitdirector",
        )

        assert result.exit_code == 0
        assert "plain,link" in result.output
        assert "plain,completion" in result.output
        assert "plain,gd-tmux" in result.output

    def test_shell_completion_lists_linked_repo_names(self, runner, tmp_path):
        manager = _mock_manager()
        manager.config.repositories = [
            tmp_path / "api-service",
            tmp_path / "app-web",
            tmp_path / "docs",
        ]

        with patch("gitdirector.commands.completion.Config", return_value=manager.config):
            result = runner.invoke(
                cli,
                [],
                env={
                    "_GITDIRECTOR_COMPLETE": "bash_complete",
                    "COMP_WORDS": "gitdirector cd a",
                    "COMP_CWORD": "2",
                },
                prog_name="gitdirector",
            )

        assert result.exit_code == 0
        assert "plain,api-service" in result.output
        assert "plain,app-web" in result.output
        assert "plain,docs" not in result.output

    def test_shell_completion_lists_repo_names_for_gd_tmux_target(self, runner, tmp_path):
        manager = _mock_manager()
        manager.config.repositories = [tmp_path / "backend", tmp_path / "frontend"]

        with patch("gitdirector.commands.completion.Config", return_value=manager.config):
            result = runner.invoke(
                cli,
                [],
                env={
                    "_GITDIRECTOR_COMPLETE": "bash_complete",
                    "COMP_WORDS": "gitdirector gd-tmux b",
                    "COMP_CWORD": "2",
                },
                prog_name="gitdirector",
            )

        assert result.exit_code == 0
        assert "plain,backend" in result.output
        assert "plain,frontend" not in result.output


class TestCdCommand:
    def test_cd_not_found(self, runner):
        mgr = _mock_manager()
        mgr.config.repositories = []
        with patch("gitdirector.commands.cd.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["cd", "missing-repo"])
        assert result.exit_code == 1
        assert "missing-repo" in result.output

    def test_cd_multiple_matches(self, runner, tmp_path):
        path_a = tmp_path / "projects" / "my-repo"
        path_b = tmp_path / "work" / "my-repo"
        mgr = _mock_manager()
        mgr.config.repositories = [path_a, path_b]
        mgr.resolve_repository_target = MagicMock(return_value=(None, [path_a, path_b], False))
        with patch("gitdirector.commands.cd.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["cd", "my-repo"])
        assert result.exit_code == 1
        assert "my-repo" in result.output
        assert "use the full path" in result.output
        # The advice has to be actionable: both paths are shown, and the
        # command accepts a path (see test_cd_accepts_a_path). Compared
        # with whitespace stripped because rich wraps long paths.
        flattened = "".join(result.output.split())
        assert "".join(str(path_a).split()) in flattened
        assert "".join(str(path_b).split()) in flattened

    def _fake_tmux(self):
        import sys

        fake = MagicMock()
        fake.create_tmux_session.return_value = "gd/my-repo_abcde/shell/1"
        return patch.dict(sys.modules, {"gitdirector.integrations.tmux": fake}), fake

    def test_cd_success(self, runner, tmp_path):
        repo = tmp_path / "my-repo"
        mgr = _mock_manager()
        mgr.resolve_repository_target = MagicMock(return_value=(repo, [], False))
        modules, fake = self._fake_tmux()
        with patch("gitdirector.commands.cd.RepositoryManager", return_value=mgr), modules:
            result = runner.invoke(cli, ["cd", "my-repo"])
        assert result.exit_code == 0, result.output
        fake.create_tmux_session.assert_called_once_with(
            "my-repo", repo, purpose="shell", description=None, shell=True
        )
        fake.launch_command_in_tmux_session.assert_not_called()
        fake.attach_tmux_session.assert_called_once_with(
            "gd/my-repo_abcde/shell/1", skip_config_sync=True
        )

    def test_cd_accepts_a_path(self, runner, tmp_path):
        """A path is the documented way out of an ambiguous name."""
        repo = tmp_path / "work" / "my-repo"
        mgr = _mock_manager()
        mgr.resolve_repository_target = MagicMock(return_value=(repo, [], True))
        modules, fake = self._fake_tmux()
        with patch("gitdirector.commands.cd.RepositoryManager", return_value=mgr), modules:
            result = runner.invoke(cli, ["cd", str(repo)])
        assert result.exit_code == 0, result.output
        mgr.resolve_repository_target.assert_called_once_with(str(repo))
        fake.attach_tmux_session.assert_called_once()

    def test_cd_agent_launches_it_with_status_hooks(self, runner, tmp_path):
        from gitdirector.agents import AGENTS_BY_KEY

        repo = tmp_path / "my-repo"
        mgr = _mock_manager()
        mgr.resolve_repository_target = MagicMock(return_value=(repo, [], False))
        modules, fake = self._fake_tmux()
        with patch("gitdirector.commands.cd.RepositoryManager", return_value=mgr), modules:
            result = runner.invoke(cli, ["cd", "my-repo", "--agent", "claude", "-m", "bypass"])
        assert result.exit_code == 0, result.output
        claude = AGENTS_BY_KEY["claude"]
        fake.create_tmux_session.assert_called_once_with(
            "my-repo", repo, purpose="claude-bypass", description=None, shell=False
        )
        fake.launch_command_in_tmux_session.assert_called_once_with(
            "gd/my-repo_abcde/shell/1", claude.launch_command_for("bypass")
        )

    def test_cd_kills_the_session_when_attaching_fails(self, runner, tmp_path):
        repo = tmp_path / "my-repo"
        mgr = _mock_manager()
        mgr.resolve_repository_target = MagicMock(return_value=(repo, [], False))
        modules, fake = self._fake_tmux()
        fake.attach_tmux_session.side_effect = KeyboardInterrupt
        with patch("gitdirector.commands.cd.RepositoryManager", return_value=mgr), modules:
            result = runner.invoke(cli, ["cd", "my-repo"])
        assert result.exit_code != 0
        fake.kill_tmux_session.assert_called_once_with("gd/my-repo_abcde/shell/1")

    def test_cd_session_name_rejoins_it(self, runner):
        name = "gd/my-repo_abcde/claude-auto/2"
        modules, fake = self._fake_tmux()
        with (
            modules,
            patch("gitdirector.integrations.tmux.core._session_exists", return_value=True),
        ):
            result = runner.invoke(cli, ["cd", name])
        assert result.exit_code == 0, result.output
        fake.create_tmux_session.assert_not_called()
        fake.attach_tmux_session.assert_called_once_with(name)

    def test_cd_dead_session_is_an_error(self, runner):
        with patch("gitdirector.integrations.tmux.core._session_exists", return_value=False):
            result = runner.invoke(cli, ["cd", "gd/my-repo_abcde/shell/9"])
        assert result.exit_code == 1
        assert "is not running" in result.output

    def test_cd_mode_needs_an_agent(self, runner):
        result = runner.invoke(cli, ["cd", "my-repo", "--mode", "auto"])
        assert result.exit_code == 2
        assert "--mode needs --agent" in result.output

    def test_cd_mode_must_suit_the_agent(self, runner):
        result = runner.invoke(cli, ["cd", "my-repo", "--agent", "codex", "--mode", "auto"])
        assert result.exit_code == 2
        assert "not supported by Codex" in result.output

    def test_cd_untracked_path_reports_path_not_name(self, runner, tmp_path):
        repo = tmp_path / "work" / "my-repo"
        mgr = _mock_manager()
        mgr.resolve_repository_target = MagicMock(return_value=(None, [], True))
        with patch("gitdirector.commands.cd.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["cd", str(repo)])
        assert result.exit_code == 1
        assert "No tracked repository at path" in result.output


class TestHelpGroup:
    def test_help_flag_uses_custom_format(self, runner):
        """--help triggers _HelpGroup.format_help which calls show_help()."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "GITDIRECTOR" in result.output

    def test_help_flag_prints_update_notice(self, runner):
        with patch(
            "gitdirector.version_check.get_update_notice",
            return_value="Update available: v1.5.0 (current v1.4.2)",
        ):
            result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert result.output.count("Update available: v1.5.0 (current v1.4.2)") == 1


class TestMain:
    def test_main_catches_exception(self):
        with patch("gitdirector.cli.cli", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_main_success(self):
        with patch("gitdirector.cli.cli") as mock_cli:
            main()
            mock_cli.assert_called_once()
