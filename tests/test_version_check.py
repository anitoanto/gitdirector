from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from gitdirector import version_check
from gitdirector.cli import cli


class TestFormatUpdateNotice:
    def test_none_when_up_to_date(self):
        status = version_check.UpdateStatus(current_version="1.4.2", latest_version="1.4.2")
        assert version_check.format_update_notice(status) is None

    def test_formats_newer_version(self):
        status = version_check.UpdateStatus(current_version="1.4.2", latest_version="1.5.0")
        assert (
            version_check.format_update_notice(status)
            == "Update available: v1.5.0 (current v1.4.2)"
        )


class TestGetUpdateStatus:
    def test_fetches_and_caches_latest_version(self, monkeypatch):
        calls = 0

        def fake_fetch() -> str:
            nonlocal calls
            calls += 1
            return "1.5.0"

        monkeypatch.setattr(version_check, "get_installed_version", lambda: "1.4.2")
        monkeypatch.setattr(version_check, "_fetch_latest_version", fake_fetch)

        first = version_check.get_update_status()
        second = version_check.get_update_status()

        assert first is not None
        assert second is not None
        assert first.latest_version == "1.5.0"
        assert second.latest_version == "1.5.0"
        assert calls == 1

    def test_uses_stale_cache_when_refresh_fails(self, monkeypatch):
        stale_checked_at = datetime.now(timezone.utc) - timedelta(days=1)
        version_check._write_cache(stale_checked_at, "1.5.0")

        monkeypatch.setattr(version_check, "get_installed_version", lambda: "1.4.2")
        monkeypatch.setattr(
            version_check,
            "_fetch_latest_version",
            lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        monkeypatch.setattr(version_check, "_utcnow", lambda: datetime.now(timezone.utc))

        status = version_check.get_update_status()

        assert status is not None
        assert status.latest_version == "1.5.0"
        assert status.update_available is True


class TestMissingPackageMetadata:
    """An uninstalled source checkout must not take the CLI down.

    ``get_installed_version`` feeds the header printed by every command, so an
    unguarded ``PackageNotFoundError`` there made the whole CLI unusable rather
    than just hiding the version.
    """

    def _uninstalled(self):
        from importlib.metadata import PackageNotFoundError
        from unittest.mock import patch

        version_check.get_installed_version.cache_clear()
        return patch("importlib.metadata.version", side_effect=PackageNotFoundError("gitdirector"))

    def teardown_method(self):
        # Process-global; it would otherwise carry the placeholder version
        # into whichever test the runner schedules next.
        version_check.get_installed_version.cache_clear()

    def test_version_falls_back_to_placeholder(self):
        with self._uninstalled():
            assert version_check.get_installed_version() == version_check.UNKNOWN_VERSION

    def test_cli_version_helper_does_not_raise(self):
        from gitdirector import commands

        with self._uninstalled():
            assert commands.get_version() == version_check.UNKNOWN_VERSION

    def test_update_status_reports_unknown_instead_of_a_fake_update(self):
        """Comparing against a placeholder would read as permanently outdated."""
        with self._uninstalled():
            assert version_check.get_update_status() is None
            assert version_check.get_cached_update_status() is None
            assert version_check.get_update_notice() is None


class TestBrokenCache:
    def _corrupt_cache(self):
        cache_path, _ = version_check._cache_paths()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("- a\n")

    def test_corrupt_cache_reads_as_empty(self):
        self._corrupt_cache()
        assert version_check._read_cache() == (None, None)

    def test_write_errors_are_swallowed(self, monkeypatch):
        def fail(*_args, **_kwargs):
            raise PermissionError("read-only home")

        monkeypatch.setattr(version_check, "write_yaml_atomic", fail)
        version_check._write_cache(datetime.now(timezone.utc), "1.5.0")

    @pytest.mark.parametrize("args", [[], ["--help"]])
    def test_help_survives_a_corrupt_cache(self, monkeypatch, args):
        monkeypatch.setattr(version_check, "get_installed_version", lambda: "1.4.2")
        self._corrupt_cache()
        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 0, result.output
        assert "GITDIRECTOR" in result.output
