"""PyPI release checking with a short local cache."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from .storage import advisory_file_lock, load_yaml_mapping, write_yaml_atomic

_PACKAGE_NAME = "gitdirector"
# Shown when package metadata is absent, e.g. an uninstalled source checkout.
UNKNOWN_VERSION = "unknown"
_PYPI_JSON_URL = f"https://pypi.org/pypi/{_PACKAGE_NAME}/json"
_VERSION_CACHE_TTL = timedelta(hours=6)
_VERSION_CHECK_TIMEOUT_SECS = 1.0


@dataclass(frozen=True)
class UpdateStatus:
    current_version: str
    latest_version: str | None

    @property
    def update_available(self) -> bool:
        if not self.latest_version:
            return False
        return _is_version_newer(self.latest_version, self.current_version)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cache_paths() -> tuple[Path, Path]:
    cache_dir = Path.home() / ".gitdirector"
    return cache_dir / "version_check.yaml", cache_dir / "version_check.lock"


def _parse_checked_at(raw_value: object) -> datetime | None:
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw_value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_cache() -> tuple[datetime | None, str | None]:
    cache_path, lock_path = _cache_paths()
    with advisory_file_lock(lock_path):
        data = load_yaml_mapping(cache_path, description="GitDirector version cache")
    checked_at = _parse_checked_at(data.get("checked_at"))
    latest_version = data.get("latest_version")
    if not isinstance(latest_version, str) or not latest_version.strip():
        latest_version = None
    return checked_at, latest_version


def _write_cache(checked_at: datetime, latest_version: str | None) -> None:
    cache_path, lock_path = _cache_paths()
    data: dict[str, object] = {"checked_at": checked_at.isoformat()}
    if latest_version:
        data["latest_version"] = latest_version
    with advisory_file_lock(lock_path):
        write_yaml_atomic(cache_path, data)


def _fetch_latest_version() -> str | None:
    from urllib.request import urlopen

    with urlopen(_PYPI_JSON_URL, timeout=_VERSION_CHECK_TIMEOUT_SECS) as response:
        payload = json.load(response)
    latest_version = payload.get("info", {}).get("version")
    if not isinstance(latest_version, str) or not latest_version.strip():
        return None
    return latest_version.strip()


def _release(version: str) -> tuple[int, ...] | None:
    """``1.8.8`` as ``(1, 8, 8)``; ``None`` for anything that is not a plain release."""
    parts = version.strip().split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _is_version_newer(latest_version: str, current_version: str) -> bool:
    # PyPI reports the latest plain release; anything else is never "newer".
    latest, current = _release(latest_version), _release(current_version)
    return latest is not None and current is not None and latest > current


@lru_cache(maxsize=1)
def get_installed_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(_PACKAGE_NAME)
    except PackageNotFoundError:
        # Running from a source tree that was never installed. This value is
        # only ever displayed, and it feeds the CLI header on every command,
        # so degrade to a placeholder rather than taking the whole CLI down.
        return UNKNOWN_VERSION


def get_cached_update_status() -> UpdateStatus | None:
    current_version = get_installed_version()
    if current_version == UNKNOWN_VERSION:
        return None
    _, latest_version = _read_cache()
    return UpdateStatus(current_version, latest_version)


def get_update_status() -> UpdateStatus | None:
    current_version = get_installed_version()
    # Without a real installed version every comparison would read as
    # "out of date", so report unknown instead of nagging about a fake update.
    if current_version == UNKNOWN_VERSION:
        return None
    now = _utcnow()
    checked_at, cached_latest_version = _read_cache()

    if checked_at is not None and now - checked_at <= _VERSION_CACHE_TTL:
        return UpdateStatus(current_version, cached_latest_version)

    # Keep the cached value on *any* fetch failure. _fetch_latest_version
    # signals a malformed payload by returning None rather than raising,
    # so assigning its result directly would discard the cached version
    # and then persist that loss for a full TTL.
    latest_version = cached_latest_version
    try:
        fetched_version = _fetch_latest_version()
    except Exception:
        fetched_version = None
    if fetched_version is not None:
        latest_version = fetched_version

    _write_cache(now, latest_version)
    return UpdateStatus(current_version, latest_version)


def format_update_notice(status: UpdateStatus | None) -> str | None:
    if status is None or not status.update_available or not status.latest_version:
        return None
    return f"Update available: v{status.latest_version} (current v{status.current_version})"


def get_cached_update_notice() -> str | None:
    return format_update_notice(get_cached_update_status())


def get_update_notice() -> str | None:
    return format_update_notice(get_update_status())
