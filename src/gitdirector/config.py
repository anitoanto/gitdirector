from pathlib import Path

import yaml


class Config:
    def __init__(self):
        self.config_dir = Path.home() / ".gitdirector"
        self.config_file = self.config_dir / "config.yaml"
        self._ensure_config_dir()
        self._load()

    def _ensure_config_dir(self) -> None:
        self.config_dir.mkdir(exist_ok=True)

    DEFAULT_MAX_WORKERS = 10
    DEFAULT_THEME = "rose-pine"

    def _load(self) -> None:
        if self.config_file.exists():
            with open(self.config_file, "r") as f:
                data = yaml.safe_load(f) or {}
                self.repositories = [Path(p) for p in data.get("repositories", [])]
                self._repo_set: set[Path] = set(self.repositories)
                self.max_workers = int(data.get("max_workers", self.DEFAULT_MAX_WORKERS))
                self.theme = str(data.get("theme", self.DEFAULT_THEME))
        else:
            self.repositories = []
            self._repo_set: set[Path] = set()
            self.max_workers = self.DEFAULT_MAX_WORKERS
            self.theme = self.DEFAULT_THEME

    def save(self) -> None:
        data: dict = {"repositories": [str(p) for p in self.repositories]}
        if self.max_workers != self.DEFAULT_MAX_WORKERS:
            data["max_workers"] = self.max_workers
        if self.theme != self.DEFAULT_THEME:
            data["theme"] = self.theme
        with open(self.config_file, "w") as f:
            yaml.dump(data, f, default_flow_style=False)

    def add_repository(self, path: Path) -> bool:
        if path not in self._repo_set:
            self.repositories.append(path)
            self._repo_set.add(path)
            self.save()
            return True
        return False

    def add_repositories(self, paths: list[Path]) -> int:
        count = 0
        for path in paths:
            if path not in self._repo_set:
                self.repositories.append(path)
                self._repo_set.add(path)
                count += 1
        if count:
            self.save()
        return count

    def remove_repository(self, path: Path) -> bool:
        if path in self._repo_set:
            self.repositories.remove(path)
            self._repo_set.discard(path)
            self.save()
            return True
        return False

    def remove_repositories(self, paths: list[Path]) -> int:
        count = 0
        for path in paths:
            if path in self._repo_set:
                self.repositories.remove(path)
                self._repo_set.discard(path)
                count += 1
        if count:
            self.save()
        return count

    def has_repository(self, path: Path) -> bool:
        return path in self._repo_set

    def clear(self) -> None:
        self.repositories = []
        self._repo_set = set()
        self.save()
