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
