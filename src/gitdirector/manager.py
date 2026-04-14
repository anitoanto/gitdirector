from pathlib import Path
from typing import List, Tuple

from .config import Config
from .repo import Repository, RepositoryInfo, RepoStatus


class RepositoryManager:
    def __init__(self):
        self.config = Config()

    def add_repository(
        self, path: Path, discover: bool = False
    ) -> Tuple[bool, str, List[Path], List[Path]]:
        if discover:
            return self._discover_and_add(path)
        else:
            return self._add_single(path)

    def _add_single(self, path: Path) -> Tuple[bool, str, List[Path], List[Path]]:
        path = path.resolve()

        if not path.exists():
            return False, f"Path does not exist: {path}", [], []

        if not path.is_dir():
            return False, f"Path is not a directory: {path}", [], []

        if not (path / ".git").is_dir():
            return False, f"Not a git repository: {path}", [], []

        if self.config.has_repository(path):
            return False, f"Repository already tracked: {path}", [], []

        try:
            self.config.add_repository(path)
            return True, f"Added repository: {path}", [path], []
        except Exception as e:
            return False, f"Error adding repository: {str(e)}", [], []

    def _discover_and_add(self, root: Path) -> Tuple[bool, str, List[Path], List[Path]]:
        root = root.resolve()

        if not root.exists():
            return False, f"Path does not exist: {root}", [], []

        if not root.is_dir():
            return False, f"Path is not a directory: {root}", [], []

        repos = []
        skipped = []

        for item in root.rglob(".git"):
            repo_path = item.parent
            if self.config.has_repository(repo_path):
                skipped.append(repo_path)
                continue
            repos.append(repo_path)

        if repos:
            self.config.add_repositories(repos)

        if not repos:
            msg = "No new repositories found" if skipped else "No git repositories found"
            return False, msg, [], skipped

        msg = (
            f"Added {len(repos)} repository"
            if len(repos) == 1
            else f"Added {len(repos)} repositories"
        )

        return True, msg, repos, skipped

    def remove_repository(self, path: Path, discover: bool = False) -> Tuple[bool, str, List[Path]]:
        if discover:
            return self._discover_and_remove(path)
        else:
            return self._remove_single(path)

    def _remove_single(self, path: Path) -> Tuple[bool, str, List[Path]]:
        path = path.resolve()

        if not self.config.has_repository(path):
            return False, f"Repository not tracked: {path}", []

        try:
            self.config.remove_repository(path)
            return True, f"Removed repository: {path}", [path]
        except Exception as e:
            return False, f"Error removing repository: {str(e)}", []

    def remove_by_name(self, name: str) -> Tuple[bool, str, List[Path]]:
        matches = [r for r in self.config.repositories if r.name == name]

        if not matches:
            return False, f"No tracked repository named: {name}", []

        if len(matches) > 1:
            paths_list = "\n".join(f"  {p}" for p in matches)
            return (
                False,
                f"Multiple repositories named '{name}' — use the full path:\n{paths_list}",
                [],
            )

        path = matches[0]
        try:
            self.config.remove_repository(path)
            return True, f"Removed repository: {path}", [path]
        except Exception as e:
            return False, f"Error removing repository: {str(e)}", []

    def _discover_and_remove(self, root: Path) -> Tuple[bool, str, List[Path]]:
        root = root.resolve()

        repos_to_remove = [r for r in self.config.repositories if r.is_relative_to(root)]

        if not repos_to_remove:
            return False, f"No tracked repositories found under: {root}", []

        try:
            self.config.remove_repositories(repos_to_remove)

            msg = (
                f"Removed {len(repos_to_remove)} repository"
                if len(repos_to_remove) == 1
                else f"Removed {len(repos_to_remove)} repositories"
            )
            return True, msg, repos_to_remove
        except Exception as e:
            return False, f"Error removing repositories: {str(e)}", []

    def get_repository_status(self, path: Path) -> RepositoryInfo:
        if path.exists() and (path / ".git").is_dir():
            try:
                repo = Repository(path)
                return repo.get_status()
            except Exception as e:
                return RepositoryInfo(path, path.name, RepoStatus.UNKNOWN, None, str(e))
        return RepositoryInfo(
            path,
            path.name,
            RepoStatus.UNKNOWN,
            None,
            "Repository path not found or invalid",
        )

    def list_repositories(self) -> List[RepositoryInfo]:
        return [self.get_repository_status(path) for path in self.config.repositories]

    def pull_all(self) -> Tuple[List[str], List[str]]:
        success = []
        failed = []

        for path in self.config.repositories:
            if not path.exists() or not (path / ".git").is_dir():
                failed.append(f"{path.name}: Path not found or invalid")
                continue

            try:
                repo = Repository(path)
                ok, msg = repo.pull()
                if ok:
                    success.append(f"{path.name}: {msg}")
                else:
                    failed.append(f"{path.name}: {msg}")
            except Exception as e:
                failed.append(f"{path.name}: {str(e)}")

        return success, failed

    def get_repository_count(self) -> int:
        return len(self.config.repositories)
