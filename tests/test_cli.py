import runpy
import warnings
from unittest.mock import MagicMock, patch

import pytest
from click.utils import strip_ansi

from gitdirector.cli import (
    _changes_text,
    _format_size,
    _path_text,
    _status_text,
    cli,
    main,
)
from gitdirector.repo import RepositoryInfo, RepoStatus

# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


class TestFormatSize:
    def test_none(self):
        assert _format_size(None).plain == "—"

    def test_bytes(self):
        assert "B" in _format_size(500).plain

    def test_kb(self):
        assert "KB" in _format_size(2048).plain

    def test_mb(self):
        assert "MB" in _format_size(2 * 1024 * 1024).plain

    def test_gb(self):
        assert "GB" in _format_size(2 * 1024 * 1024 * 1024).plain


class TestStatusText:
    @pytest.mark.parametrize(
        "status,expected",
        [
            (RepoStatus.UP_TO_DATE, "up to date"),
            (RepoStatus.BEHIND, "behind"),
            (RepoStatus.AHEAD, "ahead"),
            (RepoStatus.DIVERGED, "diverged"),
            (RepoStatus.UNKNOWN, "unknown"),
        ],
    )
    def test_labels(self, status, expected):
        assert _status_text(status).plain == expected


class TestChangesText:
    def test_none(self):
        assert _changes_text(False, False).plain == "—"

    def test_staged(self):
        assert "staged" in _changes_text(True, False).plain

    def test_unstaged(self):
        assert "unstaged" in _changes_text(False, True).plain

    def test_both(self):
        text = _changes_text(True, True).plain
        assert "staged" in text and "unstaged" in text


class TestPathText:
    def test_short_path(self):
        text = _path_text("/a/b")
        assert "/a/b" in text.plain

    def test_long_path_truncated(self):
        long = "/very/long/path/" + "x" * 200
        text = _path_text(long)
        assert text.plain.startswith("\u2026")


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def _mock_manager(**overrides):
    mgr = MagicMock()
    mgr.config.repositories = []
    mgr.config.max_workers = 2
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
        assert "Added 2" in result.output

    def test_link_discover_with_skipped(self, runner, tmp_path):
        """--discover with skipped repos prints skipped messages."""
        skipped = [tmp_path / "already-tracked"]
        mgr = _mock_manager(
            add_repository=(True, "Added 1 repository", [tmp_path / "new"], skipped)
        )
        with patch("gitdirector.commands.link.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["link", str(tmp_path), "--discover"])
        assert result.exit_code == 0
        assert "skipped" in result.output.lower()

    def test_link_discover_none_found(self, runner, tmp_path):
        """--discover finds no repositories: should print message and succeed."""
        mgr = _mock_manager(add_repository=(True, "No repositories found", [], []))
        with patch("gitdirector.commands.link.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["link", str(tmp_path), "--discover"])
        assert result.exit_code == 0
        assert (
            "no repositories" in result.output.lower() or "nothing to do" in result.output.lower()
        )


class TestUnlinkCommand:
    def test_unlink_success(self, runner, tmp_path):
        mgr = _mock_manager(remove_repository=(True, "Removed repository: /r", [tmp_path]))
        with patch("gitdirector.commands.unlink.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["unlink", str(tmp_path)])
        assert result.exit_code == 0

    def test_unlink_failure(self, runner, tmp_path):
        mgr = _mock_manager(remove_repository=(False, "Repository not tracked: /x", []))
        with patch("gitdirector.commands.unlink.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["unlink", str(tmp_path)])
        assert result.exit_code == 1

    def test_unlink_by_name_success(self, runner, tmp_path):
        """Plain name falls through to remove_by_name when path lookup fails."""
        mgr = _mock_manager(
            remove_repository=(False, "Repository not tracked: /x", []),
            remove_by_name=(True, f"Removed repository: {tmp_path}", [tmp_path]),
        )
        with patch("gitdirector.commands.unlink.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["unlink", "my-repo"])
        assert result.exit_code == 0

    def test_unlink_by_name_not_found(self, runner):
        """Returns exit code 1 when name is not tracked."""
        mgr = _mock_manager(
            remove_repository=(False, "Repository not tracked", []),
            remove_by_name=(False, "No tracked repository named: my-repo", []),
        )
        with patch("gitdirector.commands.unlink.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["unlink", "my-repo"])
        assert result.exit_code == 1
        assert "my-repo" in result.output

    def test_unlink_by_name_ambiguous(self, runner):
        """Returns exit code 1 when multiple repos share the same name."""
        mgr = _mock_manager(
            remove_repository=(False, "Repository not tracked", []),
            remove_by_name=(False, "Multiple repositories named 'my-repo'", []),
        )
        with patch("gitdirector.commands.unlink.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["unlink", "my-repo"])
        assert result.exit_code == 1
        assert "multiple" in result.output.lower()

    def test_unlink_by_path_does_not_call_remove_by_name(self, runner, tmp_path):
        """Full paths that fail should NOT fall back to remove_by_name."""
        mgr = _mock_manager(
            remove_repository=(False, "Repository not tracked: /some/path/repo", []),
        )
        mgr.remove_by_name = MagicMock()
        with patch("gitdirector.commands.unlink.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["unlink", str(tmp_path / "repo")])
        assert result.exit_code == 1
        mgr.remove_by_name.assert_not_called()

    @pytest.mark.parametrize("dot_target", [".", ".."])
    def test_unlink_dot_does_not_call_remove_by_name(self, runner, dot_target):
        """. and .. should be treated as paths, not names, and must not fall back."""
        mgr = _mock_manager(
            remove_repository=(False, f"Repository not tracked: {dot_target}", []),
        )
        mgr.remove_by_name = MagicMock()
        with patch("gitdirector.commands.unlink.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["unlink", dot_target])
        assert result.exit_code == 1
        mgr.remove_by_name.assert_not_called()


class TestListCommand:
    def test_empty(self, runner):
        mgr = _mock_manager()
        mgr.config.repositories = []
        with patch("gitdirector.commands.listt.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        assert "No repositories linked" in result.output

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


class TestStatusCommand:
    def test_empty(self, runner):
        mgr = _mock_manager()
        mgr.config.repositories = []
        with patch("gitdirector.commands.status.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "No repositories linked" in result.output

    def test_all_clean(self, runner, fake_git_repo):
        info = RepositoryInfo(
            fake_git_repo,
            fake_git_repo.name,
            RepoStatus.UP_TO_DATE,
            "main",
        )
        mgr = _mock_manager(get_repository_status=info)
        mgr.config.repositories = [fake_git_repo]
        with patch("gitdirector.commands.status.RepositoryManager", return_value=mgr):
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
        with patch("gitdirector.commands.status.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "changed" in result.output.lower()

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
        with patch("gitdirector.commands.status.RepositoryManager", return_value=mgr):
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
        mgr.get_repository_status = lambda path: info1 if path == repo1 else info2

        with patch("gitdirector.commands.status.RepositoryManager", return_value=mgr):
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
        assert "No repositories linked" in result.output

    def test_all_success(self, runner, fake_git_repo):
        mgr = _mock_manager()
        mgr.config.repositories = [fake_git_repo]
        mgr.config.max_workers = 2

        with patch("gitdirector.commands.pull.RepositoryManager", return_value=mgr):
            with patch(
                "gitdirector.commands.pull._pull_one",
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
                "gitdirector.commands.pull._pull_one",
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
        assert "Aborted" in result.output

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
                "gitdirector.commands.pull._pull_one",
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

    def test_console_command_prints_update_notice(self, runner):
        with patch(
            "gitdirector.version_check.get_update_notice",
            return_value="Update available: v1.5.0 (current v1.4.2)",
        ):
            with patch("gitdirector.commands.tui.app._run_console"):
                result = runner.invoke(cli, ["console"])

        assert result.exit_code == 0
        assert "Update available: v1.5.0 (current v1.4.2)" in result.output


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
        assert "pull [--yes]" in result.output
        assert "gd-send SESSION [TEXT] [--enter | --key C-c]" in result.output

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
        with patch("gitdirector.commands.cd.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["cd", "my-repo"])
        assert result.exit_code == 1
        assert "my-repo" in result.output

    def test_cd_success(self, runner, tmp_path):
        import sys

        repo = tmp_path / "my-repo"
        mgr = _mock_manager()
        mgr.config.repositories = [repo]
        mock_tmux = MagicMock()
        fake_tmux_module = MagicMock()
        fake_tmux_module.open_in_tmux = mock_tmux
        with patch("gitdirector.commands.cd.RepositoryManager", return_value=mgr):
            with patch.dict(sys.modules, {"gitdirector.integrations.tmux": fake_tmux_module}):
                result = runner.invoke(cli, ["cd", "my-repo"])
        mock_tmux.assert_called_once_with("my-repo", repo)
        assert result.exit_code == 0

    def test_cd_tmux_integration_unavailable(self, runner, tmp_path):
        import sys

        repo = tmp_path / "my-repo"
        mgr = _mock_manager()
        mgr.config.repositories = [repo]
        # Setting the module entry to None causes ImportError on 'from ... import'
        with patch("gitdirector.commands.cd.RepositoryManager", return_value=mgr):
            with patch.dict(sys.modules, {"gitdirector.integrations.tmux": None}):
                result = runner.invoke(cli, ["cd", "my-repo"])
        assert result.exit_code == 1
        assert "tmux integration" in result.output


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
