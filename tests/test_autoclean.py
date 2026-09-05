"""Tests for the autoclean command."""

from unittest.mock import MagicMock, patch

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
        assert "All links are valid" in result.output

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
        assert "Cancelled" in result.output
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
