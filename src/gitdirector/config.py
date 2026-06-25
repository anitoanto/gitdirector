from pathlib import Path

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
        try:
            max_workers = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Invalid max_workers: expected an integer "
                f"between {cls.MIN_MAX_WORKERS} and {cls.MAX_MAX_WORKERS}"
            ) from exc
        if not cls.MIN_MAX_WORKERS <= max_workers <= cls.MAX_MAX_WORKERS:
            raise ValueError(
                "Invalid max_workers: expected a value "
                f"between {cls.MIN_MAX_WORKERS} and {cls.MAX_MAX_WORKERS}"
            )
        return max_workers

    @staticmethod
    def _optional_str(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

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

    def _load_data(self, data: dict[str, object]) -> None:
        repositories = self._normalize_paths(list(data.get("repositories", [])))
        self.repositories = repositories
        self._repo_set = set(repositories)
        self.max_workers = self._validate_max_workers(
            data.get("max_workers", self.DEFAULT_MAX_WORKERS)
        )
        self.theme = str(data.get("theme", self.DEFAULT_THEME))
        self.github_username = self._optional_str(data.get("github_username"))
        self.github_PAT = self._optional_str(data.get("github_PAT"))
        self._snapshot_repositories = tuple(self.repositories)
        self._snapshot_max_workers = self.max_workers
        self._snapshot_theme = self.theme
        self._snapshot_github_username = self.github_username
        self._snapshot_github_PAT = self.github_PAT

    def _read_data_unlocked(self) -> dict[str, object]:
        return load_yaml_mapping(self.config_file, description="GitDirector config")

    def _settings_from_latest(
        self, latest: dict[str, object]
    ) -> tuple[int, str, str | None, str | None]:
        latest_max_workers = self._validate_max_workers(
            latest.get("max_workers", self.DEFAULT_MAX_WORKERS)
        )
        latest_theme = str(latest.get("theme", self.DEFAULT_THEME))
        latest_github_username = self._optional_str(latest.get("github_username"))
        latest_github_PAT = self._optional_str(latest.get("github_PAT"))
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
        data: dict[str, object] = {"repositories": [str(path) for path in repositories]}
        if max_workers != self.DEFAULT_MAX_WORKERS:
            data["max_workers"] = max_workers
        if theme != self.DEFAULT_THEME:
            data["theme"] = theme
        if github_username is not None:
            data["github_username"] = github_username
        if github_PAT is not None:
            data["github_PAT"] = github_PAT
        write_yaml_atomic(self.config_file, data)
        self._load_data(data)

    def _load(self) -> None:
        self._load_data(self._read_data_unlocked())

    def save(self) -> None:
        repositories = list(self.repositories)
        with advisory_file_lock(self.lock_file):
            latest = self._read_data_unlocked()
            if tuple(repositories) == self._snapshot_repositories:
                repositories = self._normalize_paths(list(latest.get("repositories", [])))
            max_workers, theme, github_username, github_PAT = self._settings_from_latest(latest)
            self._write_data_unlocked(
                repositories,
                max_workers=self._validate_max_workers(max_workers),
                theme=theme,
                github_username=github_username,
                github_PAT=github_PAT,
            )

    def add_repository(self, path: Path) -> bool:
        normalized_path = normalize_repository_path(path)
        with advisory_file_lock(self.lock_file):
            latest = self._read_data_unlocked()
            repositories = self._normalize_paths(list(latest.get("repositories", [])))
            if normalized_path in set(repositories):
                self._load_data(latest)
                return False
            repositories.append(normalized_path)
            max_workers, theme, github_username, github_PAT = self._settings_from_latest(latest)
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
        with advisory_file_lock(self.lock_file):
            latest = self._read_data_unlocked()
            repositories = self._normalize_paths(list(latest.get("repositories", [])))
            repo_set = set(repositories)
            count = 0
            for path in normalized_paths:
                if path in repo_set:
                    continue
                repositories.append(path)
                repo_set.add(path)
                count += 1
            if count:
                max_workers, theme, github_username, github_PAT = self._settings_from_latest(latest)
                self._write_data_unlocked(
                    repositories,
                    max_workers=max_workers,
                    theme=theme,
                    github_username=github_username,
                    github_PAT=github_PAT,
                )
            else:
                self._load_data(latest)
            return count

    def remove_repository(self, path: Path) -> bool:
        normalized_path = normalize_repository_path(path)
        with advisory_file_lock(self.lock_file):
            latest = self._read_data_unlocked()
            repositories = self._normalize_paths(list(latest.get("repositories", [])))
            if normalized_path not in set(repositories):
                self._load_data(latest)
                return False
            repositories = [repo_path for repo_path in repositories if repo_path != normalized_path]
            max_workers, theme, github_username, github_PAT = self._settings_from_latest(latest)
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
        with advisory_file_lock(self.lock_file):
            latest = self._read_data_unlocked()
            repositories = self._normalize_paths(list(latest.get("repositories", [])))
            remaining = [path for path in repositories if path not in normalized_targets]
            count = len(repositories) - len(remaining)
            if count:
                max_workers, theme, github_username, github_PAT = self._settings_from_latest(latest)
                self._write_data_unlocked(
                    remaining,
                    max_workers=max_workers,
                    theme=theme,
                    github_username=github_username,
                    github_PAT=github_PAT,
                )
            else:
                self._load_data(latest)
            return count

    def has_repository(self, path: Path) -> bool:
        return normalize_repository_path(path) in self._repo_set

    def clear(self) -> None:
        with advisory_file_lock(self.lock_file):
            latest = self._read_data_unlocked()
            max_workers, theme, github_username, github_PAT = self._settings_from_latest(latest)
            self._write_data_unlocked(
                [],
                max_workers=max_workers,
                theme=theme,
                github_username=github_username,
                github_PAT=github_PAT,
            )
