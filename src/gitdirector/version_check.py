"""PyPI release checking with a short local cache."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.request import urlopen

from .storage import advisory_file_lock, load_yaml_mapping, write_yaml_atomic

try:
    from packaging.version import InvalidVersion, Version
except ImportError:  # pragma: no cover - optional dependency
    InvalidVersion = ValueError
    Version = None

_PACKAGE_NAME = "gitdirector"
_PYPI_JSON_URL = f"https://pypi.org/pypi/{_PACKAGE_NAME}/json"
_VERSION_CACHE_TTL = timedelta(hours=6)
_VERSION_CHECK_TIMEOUT_SECS = 1.0
_VERSION_RE = re.compile(r"^v?(?P<release>\d+(?:\.\d+)*)(?P<suffix>.*)$", re.IGNORECASE)
_SUFFIX_RANK = {
    "dev": 0,
    "a": 1,
    "alpha": 1,
    "b": 2,
    "beta": 2,
    "rc": 3,
    "c": 3,
    "": 4,
    "post": 5,
    "rev": 5,
    "r": 5,
}


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
    with urlopen(_PYPI_JSON_URL, timeout=_VERSION_CHECK_TIMEOUT_SECS) as response:
        payload = json.load(response)
    latest_version = payload.get("info", {}).get("version")
    if not isinstance(latest_version, str) or not latest_version.strip():
        return None
    return latest_version.strip()


def _fallback_version_key(version: str) -> tuple[tuple[int, ...], int, tuple[int, ...]]:
    normalized = version.strip().lower()
    match = _VERSION_RE.match(normalized)
    if match is None:
        return (0,), 0, ()

    release = tuple(int(part) for part in match.group("release").split("."))
    suffix = match.group("suffix").strip(".-+_").lower()
    if not suffix:
        return release, _SUFFIX_RANK[""], ()

    tokens = re.findall(r"[a-z]+|\d+", suffix)
    label = next((token for token in tokens if token.isalpha()), "")
    numbers = tuple(int(token) for token in tokens if token.isdigit())
    return release, _SUFFIX_RANK.get(label, 0), numbers


def _is_version_newer(latest_version: str, current_version: str) -> bool:
    if Version is not None:
        try:
            return Version(latest_version) > Version(current_version)
        except InvalidVersion:
            pass
    return _fallback_version_key(latest_version) > _fallback_version_key(current_version)


@lru_cache(maxsize=1)
def get_installed_version() -> str:
    from importlib.metadata import version

    return version(_PACKAGE_NAME)


def get_cached_update_status() -> UpdateStatus | None:
    _, latest_version = _read_cache()
    return UpdateStatus(get_installed_version(), latest_version)


def get_update_status() -> UpdateStatus | None:
    current_version = get_installed_version()
    now = _utcnow()
    checked_at, cached_latest_version = _read_cache()

    if checked_at is not None and now - checked_at <= _VERSION_CACHE_TTL:
        return UpdateStatus(current_version, cached_latest_version)

    latest_version = cached_latest_version
    try:
        latest_version = _fetch_latest_version()
    except Exception:
        pass

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
