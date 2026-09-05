from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from pathlib import Path

from .storage import (
    advisory_file_lock,
    load_yaml_mapping,
    normalize_repository_path,
    write_yaml_atomic,
)

logger = logging.getLogger(__name__)

_MAIN_KEYS = frozenset({"repositories", "max_workers", "theme"})
_SECRET_KEYS = frozenset({"github_username", "github_PAT"})


@dataclass(frozen=True)
class _Settings:
    """Every configurable value except the repository list."""

    max_workers: int
    theme: str
    github_username: str | None
    github_PAT: str | None


class Config:
    DEFAULT_MAX_WORKERS = 10
    MIN_MAX_WORKERS = 1
    MAX_MAX_WORKERS = 32
    DEFAULT_THEME = "rose-pine"

    def __init__(self):
        self.config_dir = Path.home() / ".gitdirector"
        self.config_file = self.config_dir / "config.yaml"
        self.secrets_file = self.config_dir / "secrets.yaml"
        self.lock_file = self.config_dir / "config.lock"
        self.repositories: list[Path] = []
        self._repo_set: set[Path] = set()
        self.max_workers = self.DEFAULT_MAX_WORKERS
        self.theme = self.DEFAULT_THEME
        self.github_username: str | None = None
        self.github_PAT: str | None = None
        # What was last read from or written to disk. A field that still
        # matches its snapshot has not been edited in memory, so a save keeps
        # whatever another process wrote to disk in the meantime.
        self._snapshot_repositories: tuple[Path, ...] = ()
        self._snapshot_settings = self._current_settings()
        self._config_state_snapshot: dict[str, list[bool | int | None]] = self._cache_token()
        self._ensure_config_dir()
        self._load()

    @staticmethod
    def _path_state(path: Path) -> list[bool | int | None]:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return [False, None, None]
        return [True, stat.st_mtime_ns, stat.st_size]

    def _cache_token(self) -> dict[str, list[bool | int | None]]:
        return {
            "config": self._path_state(self.config_file),
            "secrets": self._path_state(self.secrets_file),
        }

    def repository_cache_token(self) -> dict[str, list[bool | int | None]]:
        return {key: value.copy() for key, value in self._cache_token().items()}

    def _refresh_config_state_snapshot(self) -> None:
        self._config_state_snapshot = self._cache_token()

    def _invalidate_repository_cache(self) -> None:
        cache_file = self.config_dir / "cache" / "repos.yaml"
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            logger.debug("Failed to remove repository cache", exc_info=True)

    def _ensure_config_dir(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _validate_max_workers(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                "Invalid max_workers: expected an integer "
                f"between {cls.MIN_MAX_WORKERS} and {cls.MAX_MAX_WORKERS}"
            )
        if not cls.MIN_MAX_WORKERS <= value <= cls.MAX_MAX_WORKERS:
            raise ValueError(
                "Invalid max_workers: expected a value "
                f"between {cls.MIN_MAX_WORKERS} and {cls.MAX_MAX_WORKERS}"
            )
        return value

    @staticmethod
    def _optional_str(value: object, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Invalid {field}: expected a string or null")
        return value.strip() or None

    @staticmethod
    def _validate_repositories(value: object) -> list[str]:
        if not isinstance(value, list) or any(
            not isinstance(path, str) or not path.strip() for path in value
        ):
            raise ValueError("Invalid repositories: expected a list of nonempty strings")
        return value

    @staticmethod
    def _validate_theme(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Invalid theme: expected a nonempty string")
        return value

    @classmethod
    def _parse_settings(
        cls, main_data: dict[str, object], secrets_data: dict[str, object]
    ) -> _Settings:
        return _Settings(
            max_workers=cls._validate_max_workers(
                main_data.get("max_workers", cls.DEFAULT_MAX_WORKERS)
            ),
            theme=cls._validate_theme(main_data.get("theme", cls.DEFAULT_THEME)),
            github_username=cls._optional_str(
                secrets_data.get("github_username"), "github_username"
            ),
            github_PAT=cls._optional_str(secrets_data.get("github_PAT"), "github_PAT"),
        )

    @classmethod
    def _validate_loaded_data(
        cls, main_data: dict[str, object], secrets_data: dict[str, object]
    ) -> None:
        cls._validate_repositories(main_data.get("repositories", []))
        cls._parse_settings(main_data, secrets_data)

    @staticmethod
    def _normalize_paths(paths: Iterable[object]) -> list[Path]:
        normalized: list[Path] = []
        seen: set[Path] = set()
        for raw_path in paths:
            path = normalize_repository_path(Path(str(raw_path)))
            if path in seen:
                continue
            seen.add(path)
            normalized.append(path)
        return normalized

    def _current_settings(self) -> _Settings:
        return _Settings(
            max_workers=self.max_workers,
            theme=self.theme,
            github_username=self.github_username,
            github_PAT=self.github_PAT,
        )

    def _apply(self, repositories: list[Path], settings: _Settings) -> None:
        self.repositories = repositories
        self._repo_set = set(repositories)
        self.max_workers = settings.max_workers
        self.theme = settings.theme
        self.github_username = settings.github_username
        self.github_PAT = settings.github_PAT
        self._snapshot_repositories = tuple(repositories)
        self._snapshot_settings = settings

    def _load_data(
        self,
        main_data: dict[str, object],
        secrets_data: dict[str, object] | None = None,
    ) -> None:
        secrets = secrets_data if secrets_data is not None else {}
        self._validate_loaded_data(main_data, secrets)
        repositories = self._normalize_paths(main_data.get("repositories", []))
        self._apply(repositories, self._parse_settings(main_data, secrets))

    def _read_main_unlocked(self) -> dict[str, object]:
        return load_yaml_mapping(self.config_file, description="GitDirector config")

    def _read_secrets_unlocked(self) -> dict[str, object]:
        return load_yaml_mapping(self.secrets_file, description="GitDirector secrets")

    @contextmanager
    def _locked_latest(self) -> Iterator[tuple[dict[str, object], dict[str, object]]]:
        with advisory_file_lock(self.lock_file):
            latest_main = self._read_main_unlocked()
            latest_secrets = self._read_secrets_unlocked()
            self._validate_loaded_data(latest_main, latest_secrets)
            yield latest_main, latest_secrets

    def _merged_settings(
        self,
        latest_main: dict[str, object],
        latest_secrets: dict[str, object],
    ) -> _Settings:
        """Combine in-memory edits with what is on disk right now.

        A field edited since the last load wins; an untouched field takes the
        on-disk value so a concurrent writer's change is not overwritten.
        """
        latest = self._parse_settings(latest_main, latest_secrets)
        current = self._current_settings()
        merged = {}
        for field in fields(_Settings):
            edited = getattr(current, field.name) != getattr(self._snapshot_settings, field.name)
            merged[field.name] = getattr(current if edited else latest, field.name)
        return _Settings(**merged)

    def _write_data_unlocked(
        self,
        repositories: list[Path],
        settings: _Settings,
        *,
        latest_main: dict[str, object],
        latest_secrets: dict[str, object],
    ) -> None:
        # Keys this version does not know about are carried over untouched,
        # so a newer or hand-edited file is not silently trimmed.
        main_data: dict[str, object] = {"repositories": [str(path) for path in repositories]}
        if settings.max_workers != self.DEFAULT_MAX_WORKERS:
            main_data["max_workers"] = settings.max_workers
        if settings.theme != self.DEFAULT_THEME:
            main_data["theme"] = settings.theme
        main_data.update({k: v for k, v in latest_main.items() if k not in _MAIN_KEYS})
        write_yaml_atomic(self.config_file, main_data)

        secrets_data: dict[str, object] = {}
        if settings.github_username is not None:
            secrets_data["github_username"] = settings.github_username
        if settings.github_PAT is not None:
            secrets_data["github_PAT"] = settings.github_PAT
        secrets_data.update({k: v for k, v in latest_secrets.items() if k not in _SECRET_KEYS})
        if secrets_data:
            write_yaml_atomic(self.secrets_file, secrets_data)
        else:
            self.secrets_file.unlink(missing_ok=True)

        self._load_data(main_data, secrets_data)
        self._refresh_config_state_snapshot()
        self._invalidate_repository_cache()

    def _load(self) -> None:
        self._load_data(self._read_main_unlocked(), self._read_secrets_unlocked())
        self._refresh_config_state_snapshot()

    def reload_if_changed(self) -> bool:
        if self._cache_token() == self._config_state_snapshot:
            return False
        self._load()
        self._invalidate_repository_cache()
        return True

    def save(self) -> None:
        repositories = list(self.repositories)
        with self._locked_latest() as (latest_main, latest_secrets):
            if tuple(repositories) == self._snapshot_repositories:
                repositories = self._normalize_paths(latest_main.get("repositories", []))
            self._write_data_unlocked(
                repositories,
                self._merged_settings(latest_main, latest_secrets),
                latest_main=latest_main,
                latest_secrets=latest_secrets,
            )

    def _update_repositories(self, update: Callable[[list[Path]], int]) -> int:
        """Apply *update* to the on-disk repository list under the lock.

        *update* mutates the list in place and returns how many entries it
        changed. Nothing is written when it changed none, but the in-memory
        state is still refreshed from disk.
        """
        with self._locked_latest() as (latest_main, latest_secrets):
            repositories = self._normalize_paths(latest_main.get("repositories", []))
            changed = update(repositories)
            if changed:
                self._write_data_unlocked(
                    repositories,
                    self._merged_settings(latest_main, latest_secrets),
                    latest_main=latest_main,
                    latest_secrets=latest_secrets,
                )
            else:
                self._load_data(latest_main, latest_secrets)
            return changed

    @staticmethod
    def _append_missing(repositories: list[Path], candidates: list[Path]) -> int:
        present = set(repositories)
        added = 0
        for path in candidates:
            if path in present:
                continue
            repositories.append(path)
            present.add(path)
            added += 1
        return added

    @staticmethod
    def _drop_present(repositories: list[Path], targets: set[Path]) -> int:
        remaining = [path for path in repositories if path not in targets]
        removed = len(repositories) - len(remaining)
        repositories[:] = remaining
        return removed

    def add_repository(self, path: Path) -> bool:
        target = normalize_repository_path(path)
        return bool(self._update_repositories(lambda repos: self._append_missing(repos, [target])))

    def add_repositories(self, paths: list[Path]) -> int:
        targets = self._normalize_paths(paths)
        return self._update_repositories(lambda repos: self._append_missing(repos, targets))

    def remove_repository(self, path: Path) -> bool:
        target = normalize_repository_path(path)
        return bool(self._update_repositories(lambda repos: self._drop_present(repos, {target})))

    def remove_repositories(self, paths: list[Path]) -> int:
        targets = set(self._normalize_paths(paths))
        return self._update_repositories(lambda repos: self._drop_present(repos, targets))

    def has_repository(self, path: Path) -> bool:
        return normalize_repository_path(path) in self._repo_set

    def clear(self) -> None:
        """Drop every tracked repository and write the config file even if empty."""
        with self._locked_latest() as (latest_main, latest_secrets):
            self._write_data_unlocked(
                [],
                self._merged_settings(latest_main, latest_secrets),
                latest_main=latest_main,
                latest_secrets=latest_secrets,
            )
