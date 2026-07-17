from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .storage import (
    advisory_file_lock,
    load_yaml_mapping,
    normalize_repository_path,
    write_yaml_atomic,
)


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
        self._snapshot_repositories: tuple[Path, ...] = ()
        self._snapshot_max_workers = self.DEFAULT_MAX_WORKERS
        self._snapshot_theme = self.DEFAULT_THEME
        self._snapshot_github_username: str | None = None
        self._snapshot_github_PAT: str | None = None
        self._ensure_config_dir()
        self._load()

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
    def _validate_loaded_data(
        cls, main_data: dict[str, object], secrets_data: dict[str, object]
    ) -> None:
        cls._validate_repositories(main_data.get("repositories", []))
        cls._validate_max_workers(main_data.get("max_workers", cls.DEFAULT_MAX_WORKERS))
        cls._validate_theme(main_data.get("theme", cls.DEFAULT_THEME))
        cls._optional_str(secrets_data.get("github_username"), "github_username")
        cls._optional_str(secrets_data.get("github_PAT"), "github_PAT")

    @staticmethod
    def _normalize_paths(paths: list[object]) -> list[Path]:
        normalized: list[Path] = []
        seen: set[Path] = set()
        for raw_path in paths:
            path = normalize_repository_path(Path(str(raw_path)))
            if path in seen:
                continue
            seen.add(path)
            normalized.append(path)
        return normalized

    def _load_data(
        self,
        main_data: dict[str, object],
        secrets_data: dict[str, object] | None = None,
    ) -> None:
        secrets = secrets_data if secrets_data is not None else {}
        self._validate_loaded_data(main_data, secrets)
        repositories = self._normalize_paths(main_data.get("repositories", []))
        self.repositories = repositories
        self._repo_set = set(repositories)
        self.max_workers = self._validate_max_workers(
            main_data.get("max_workers", self.DEFAULT_MAX_WORKERS)
        )
        self.theme = self._validate_theme(main_data.get("theme", self.DEFAULT_THEME))
        self.github_username = self._optional_str(secrets.get("github_username"), "github_username")
        self.github_PAT = self._optional_str(secrets.get("github_PAT"), "github_PAT")
        self._snapshot_repositories = tuple(self.repositories)
        self._snapshot_max_workers = self.max_workers
        self._snapshot_theme = self.theme
        self._snapshot_github_username = self.github_username
        self._snapshot_github_PAT = self.github_PAT

    @staticmethod
    def _read_data_unlocked(path: Path, description: str) -> dict[str, object]:
        return load_yaml_mapping(path, description=description)

    def _read_main_unlocked(self) -> dict[str, object]:
        return self._read_data_unlocked(self.config_file, "GitDirector config")

    def _read_secrets_unlocked(self) -> dict[str, object]:
        return self._read_data_unlocked(self.secrets_file, "GitDirector secrets")

    @contextmanager
    def _locked_latest(self) -> Iterator[tuple[dict[str, object], dict[str, object]]]:
        with advisory_file_lock(self.lock_file):
            latest_main = self._read_main_unlocked()
            latest_secrets = self._read_secrets_unlocked()
            self._validate_loaded_data(latest_main, latest_secrets)
            yield latest_main, latest_secrets

    def _settings_from_latest(
        self,
        latest_main: dict[str, object],
        latest_secrets: dict[str, object],
    ) -> tuple[int, str, str | None, str | None]:
        self._validate_loaded_data(latest_main, latest_secrets)
        latest_max_workers = self._validate_max_workers(
            latest_main.get("max_workers", self.DEFAULT_MAX_WORKERS)
        )
        latest_theme = self._validate_theme(latest_main.get("theme", self.DEFAULT_THEME))
        latest_github_username = self._optional_str(
            latest_secrets.get("github_username"), "github_username"
        )
        latest_github_PAT = self._optional_str(latest_secrets.get("github_PAT"), "github_PAT")
        max_workers = (
            latest_max_workers
            if self.max_workers == self._snapshot_max_workers
            else self.max_workers
        )
        theme = latest_theme if self.theme == self._snapshot_theme else self.theme
        github_username = (
            latest_github_username
            if self.github_username == self._snapshot_github_username
            else self.github_username
        )
        github_PAT = (
            latest_github_PAT if self.github_PAT == self._snapshot_github_PAT else self.github_PAT
        )
        return max_workers, theme, github_username, github_PAT

    def _write_data_unlocked(
        self,
        repositories: list[Path],
        *,
        max_workers: int,
        theme: str,
        github_username: str | None,
        github_PAT: str | None,
    ) -> None:
        main_data: dict[str, object] = {"repositories": [str(path) for path in repositories]}
        if max_workers != self.DEFAULT_MAX_WORKERS:
            main_data["max_workers"] = max_workers
        if theme != self.DEFAULT_THEME:
            main_data["theme"] = theme
        write_yaml_atomic(self.config_file, main_data)
        secrets_data: dict[str, object] = {}
        if github_username is not None:
            secrets_data["github_username"] = github_username
        if github_PAT is not None:
            secrets_data["github_PAT"] = github_PAT
        if secrets_data:
            write_yaml_atomic(self.secrets_file, secrets_data)
        elif self.secrets_file.exists():
            self.secrets_file.unlink()
        self._load_data(main_data, secrets_data)

    def _load(self) -> None:
        self._load_data(self._read_main_unlocked(), self._read_secrets_unlocked())

    def save(self) -> None:
        repositories = list(self.repositories)
        with self._locked_latest() as (latest_main, latest_secrets):
            if tuple(repositories) == self._snapshot_repositories:
                repositories = self._normalize_paths(list(latest_main.get("repositories", [])))
            max_workers, theme, github_username, github_PAT = self._settings_from_latest(
                latest_main, latest_secrets
            )
            self._write_data_unlocked(
                repositories,
                max_workers=self._validate_max_workers(max_workers),
                theme=theme,
                github_username=github_username,
                github_PAT=github_PAT,
            )

    def add_repository(self, path: Path) -> bool:
        normalized_path = normalize_repository_path(path)
        with self._locked_latest() as (latest_main, latest_secrets):
            repositories = self._normalize_paths(list(latest_main.get("repositories", [])))
            if normalized_path in set(repositories):
                self._load_data(latest_main, latest_secrets)
                return False
            repositories.append(normalized_path)
            max_workers, theme, github_username, github_PAT = self._settings_from_latest(
                latest_main, latest_secrets
            )
            self._write_data_unlocked(
                repositories,
                max_workers=max_workers,
                theme=theme,
                github_username=github_username,
                github_PAT=github_PAT,
            )
            return True

    def add_repositories(self, paths: list[Path]) -> int:
        normalized_paths = self._normalize_paths(paths)
        with self._locked_latest() as (latest_main, latest_secrets):
            repositories = self._normalize_paths(list(latest_main.get("repositories", [])))
            repo_set = set(repositories)
            count = 0
            for path in normalized_paths:
                if path in repo_set:
                    continue
                repositories.append(path)
                repo_set.add(path)
                count += 1
            if count:
                max_workers, theme, github_username, github_PAT = self._settings_from_latest(
                    latest_main, latest_secrets
                )
                self._write_data_unlocked(
                    repositories,
                    max_workers=max_workers,
                    theme=theme,
                    github_username=github_username,
                    github_PAT=github_PAT,
                )
            else:
                self._load_data(latest_main, latest_secrets)
            return count

    def remove_repository(self, path: Path) -> bool:
        normalized_path = normalize_repository_path(path)
        with self._locked_latest() as (latest_main, latest_secrets):
            repositories = self._normalize_paths(list(latest_main.get("repositories", [])))
            if normalized_path not in set(repositories):
                self._load_data(latest_main, latest_secrets)
                return False
            repositories = [repo_path for repo_path in repositories if repo_path != normalized_path]
            max_workers, theme, github_username, github_PAT = self._settings_from_latest(
                latest_main, latest_secrets
            )
            self._write_data_unlocked(
                repositories,
                max_workers=max_workers,
                theme=theme,
                github_username=github_username,
                github_PAT=github_PAT,
            )
            return True

    def remove_repositories(self, paths: list[Path]) -> int:
        normalized_targets = set(self._normalize_paths(paths))
        with self._locked_latest() as (latest_main, latest_secrets):
            repositories = self._normalize_paths(list(latest_main.get("repositories", [])))
            remaining = [path for path in repositories if path not in normalized_targets]
            count = len(repositories) - len(remaining)
            if count:
                max_workers, theme, github_username, github_PAT = self._settings_from_latest(
                    latest_main, latest_secrets
                )
                self._write_data_unlocked(
                    remaining,
                    max_workers=max_workers,
                    theme=theme,
                    github_username=github_username,
                    github_PAT=github_PAT,
                )
            else:
                self._load_data(latest_main, latest_secrets)
            return count

    def has_repository(self, path: Path) -> bool:
        return normalize_repository_path(path) in self._repo_set

    def clear(self) -> None:
        with self._locked_latest() as (latest_main, latest_secrets):
            max_workers, theme, github_username, github_PAT = self._settings_from_latest(
                latest_main, latest_secrets
            )
            self._write_data_unlocked(
                [],
                max_workers=max_workers,
                theme=theme,
                github_username=github_username,
                github_PAT=github_PAT,
            )
