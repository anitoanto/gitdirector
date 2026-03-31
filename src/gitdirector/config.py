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

    def _load(self) -> None:
        if self.config_file.exists():
            with open(self.config_file, "r") as f:
                data = yaml.safe_load(f) or {}
                self.repositories = [Path(p) for p in data.get("repositories", [])]
        else:
            self.repositories = []

    def save(self) -> None:
        data = {"repositories": [str(p) for p in self.repositories]}
        with open(self.config_file, "w") as f:
            yaml.dump(data, f, default_flow_style=False)

    def add_repository(self, path: Path) -> bool:
        if path not in self.repositories:
            self.repositories.append(path)
            self.save()
            return True
        return False

    def remove_repository(self, path: Path) -> bool:
        if path in self.repositories:
            self.repositories.remove(path)
            self.save()
            return True
        return False

    def has_repository(self, path: Path) -> bool:
        return path in self.repositories

    def clear(self) -> None:
        self.repositories = []
        self.save()
