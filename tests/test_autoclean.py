"""Tests for the autoclean command."""

from unittest.mock import MagicMock, patch

from gitdirector import paths
from gitdirector.cli import cli


class TestAutocleanLinks:
    def test_no_broken_links(self, runner, tmp_path):
        """When all links are valid, prints success message."""
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)

        config = MagicMock()
        config.repositories = [repo]

        with patch("gitdirector.commands.autoclean.Config", return_value=config):
            result = runner.invoke(cli, ["autoclean"])
        assert result.exit_code == 0
        assert "All tracked repositories exist" in result.output

    def test_broken_links_confirmed(self, runner, tmp_path):
        """When broken links exist and user confirms, they are removed."""
        existing = tmp_path / "existing"
        (existing / ".git").mkdir(parents=True)
        broken1 = tmp_path / "gone1"
        broken2 = tmp_path / "not-a-repo-anymore"
        broken2.mkdir()

        config = MagicMock()
        config.repositories = [existing, broken1, broken2]

        with patch("gitdirector.commands.autoclean.Config", return_value=config):
            result = runner.invoke(cli, ["autoclean"], input="y\n")
        assert result.exit_code == 0
        assert "2" in result.output
        assert "Removed" in result.output
        config.remove_repositories.assert_called_once_with([broken1, broken2])

    def test_broken_links_cancelled(self, runner, tmp_path):
        """When user declines, no links are removed."""
        broken = tmp_path / "gone"

        config = MagicMock()
        config.repositories = [broken]

        with patch("gitdirector.commands.autoclean.Config", return_value=config):
            result = runner.invoke(cli, ["autoclean"], input="n\n")
        assert result.exit_code == 0
        assert "Removed" not in result.output
        config.remove_repository.assert_not_called()

    def test_broken_links_displays_paths(self, runner, tmp_path):
        """Broken link paths are printed so the user can review them."""
        from gitdirector.commands.autoclean import console

        broken = tmp_path / "vanished"

        config = MagicMock()
        config.repositories = [broken]

        original_width = console.width
        console.width = 20
        try:
            with patch("gitdirector.commands.autoclean.Config", return_value=config):
                result = runner.invoke(cli, ["autoclean"], input="y\n")
        finally:
            console.width = original_width

        assert "vanished" in result.output


class TestAutocleanCache:
    def _seed_cache(self):
        paths.temp_dir().mkdir(parents=True)
        (paths.cache_dir() / "repos.yaml").write_text("")

    def test_cache_is_cleared_with_nothing_broken(self, runner):
        self._seed_cache()
        config = MagicMock()
        config.repositories = []

        with patch("gitdirector.commands.autoclean.Config", return_value=config):
            result = runner.invoke(cli, ["autoclean"])
        assert result.exit_code == 0
        assert "Cleared" in result.output
        assert not (paths.cache_dir() / "repos.yaml").exists()
        assert paths.temp_dir().is_dir()

    def test_cache_is_cleared_even_when_links_are_kept(self, runner, tmp_path):
        self._seed_cache()
        config = MagicMock()
        config.repositories = [tmp_path / "gone"]

        with patch("gitdirector.commands.autoclean.Config", return_value=config):
            result = runner.invoke(cli, ["autoclean"], input="n\n")
        assert result.exit_code == 0
        assert not (paths.cache_dir() / "repos.yaml").exists()
        config.remove_repositories.assert_not_called()

    def test_a_cache_that_cannot_be_cleared_fails_the_command(self, runner):
        config = MagicMock()
        config.repositories = []

        with (
            patch("gitdirector.commands.autoclean.Config", return_value=config),
            patch.object(paths, "clear_cache", side_effect=PermissionError("denied")),
        ):
            result = runner.invoke(cli, ["autoclean"])
        assert result.exit_code != 0
        assert "Failed to clear" in result.output
        assert "denied" in result.output
