from datetime import datetime, timedelta, timezone

from gitdirector import version_check


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
        # Both are process-global and would otherwise carry the placeholder
        # version into whichever test the runner schedules next.
        from gitdirector import commands

        version_check.get_installed_version.cache_clear()
        commands.__version__ = None

    def test_version_falls_back_to_placeholder(self):
        with self._uninstalled():
            assert version_check.get_installed_version() == version_check.UNKNOWN_VERSION

    def test_cli_version_helper_does_not_raise(self):
        from gitdirector import commands

        with self._uninstalled():
            commands.__version__ = None
            assert commands.get_version() == version_check.UNKNOWN_VERSION
        commands.__version__ = None

    def test_update_status_reports_unknown_instead_of_a_fake_update(self):
        """Comparing against a placeholder would read as permanently outdated."""
        with self._uninstalled():
            assert version_check.get_update_status() is None
            assert version_check.get_cached_update_status() is None
            assert version_check.get_update_notice() is None
