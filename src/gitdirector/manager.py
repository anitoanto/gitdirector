import os
from pathlib import Path

from pathspec import GitIgnoreSpec

from .config import Config
from .repo import Repository, RepositoryInfo, RepoStatus
from .storage import normalize_repository_path


def _load_gitignore(directory: Path) -> GitIgnoreSpec | None:
    path = directory / ".gitignore"
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open(encoding="utf-8", errors="surrogateescape") as file:
            return GitIgnoreSpec.from_lines(file)
    except (OSError, ValueError):
        return None


def _is_ignored(directory: Path, ignore_specs: dict[Path, GitIgnoreSpec]) -> bool:
    ignored = False
    for ignore_root, spec in ignore_specs.items():
        try:
            relative_path = directory.relative_to(ignore_root)
        except ValueError:
            continue
        result = spec.check_file(f"{relative_path.as_posix()}/")
        if result.include is not None:
            ignored = result.include
    return ignored


class RepositoryManager:
    def __init__(self):
        self.config = Config()

    def resolve_repository_target(
        self,
        target: str,
        *,
        names_only: bool = False,
        allow_untracked_git_path: bool = False,
        fuzzy_names: bool = False,
    ) -> tuple[Path | None, list[Path], bool]:
        """Resolve a command target while preserving its path/name policy."""
        candidate = Path(target).expanduser()
        path_like = (
            "/" in target
            or "\\" in target
            or target in (".", "..")
            or candidate.is_absolute()
            or target.startswith("~")
            or candidate.exists()
        )

        if not names_only and path_like:
            path = normalize_repository_path(candidate)
            if allow_untracked_git_path and path.is_dir() and (path / ".git").is_dir():
                return path, [], True
            if self.config.has_repository(path):
                return path, [], True
            return None, [], True

        target_lower = target.lower()
        matches = [
            repository
            for repository in self.config.repositories
            if repository.name.lower() == target_lower
        ]
        if not matches and fuzzy_names:
            matches = [
                repository
                for repository in self.config.repositories
                if target_lower in repository.name.lower()
            ]
        return (matches[0] if len(matches) == 1 else None), matches, False

    def add_repository(
        self, path: Path, discover: bool = False
    ) -> tuple[bool, str, list[Path], list[Path]]:
        if discover:
            return self._discover_and_add(path)
        else:
            return self._add_single(path)

    def _add_single(self, path: Path) -> tuple[bool, str, list[Path], list[Path]]:
        path = normalize_repository_path(path)

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

    def _discover_and_add(self, root: Path) -> tuple[bool, str, list[Path], list[Path]]:
        root = normalize_repository_path(root)

        if not root.exists():
            return False, f"Path does not exist: {root}", [], []

        if not root.is_dir():
            return False, f"Path is not a directory: {root}", [], []

        repos: list[Path] = []
        skipped: list[Path] = []
        ignore_specs: dict[Path, GitIgnoreSpec] = {}

        for current_root, dirs, _ in os.walk(root):
            current_path = Path(current_root)
            ignore_specs = {
                ignore_root: spec
                for ignore_root, spec in ignore_specs.items()
                if ignore_root == current_path or ignore_root in current_path.parents
            }
            gitignore = _load_gitignore(current_path)
            if gitignore is not None:
                ignore_specs[current_path] = gitignore
            dirs[:] = [
                directory
                for directory in dirs
                if not _is_ignored(current_path / directory, ignore_specs)
            ]
            if ".git" not in dirs:
                continue
            dirs.remove(".git")
            repo_path = normalize_repository_path(current_path)
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

    def remove_repository(self, path: Path, discover: bool = False) -> tuple[bool, str, list[Path]]:
        if discover:
            return self._discover_and_remove(path)
        else:
            return self._remove_single(path)

    def _remove_single(self, path: Path) -> tuple[bool, str, list[Path]]:
        path = normalize_repository_path(path)

        if not self.config.has_repository(path):
            return False, f"Repository not tracked: {path}", []

        try:
            self.config.remove_repository(path)
            return True, f"Removed repository: {path}", [path]
        except Exception as e:
            return False, f"Error removing repository: {str(e)}", []

    def remove_by_name(self, name: str) -> tuple[bool, str, list[Path]]:
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

    def _discover_and_remove(self, root: Path) -> tuple[bool, str, list[Path]]:
        root = normalize_repository_path(root)

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

    def get_repository_status(
        self,
        path: Path,
        *,
        fetch: bool = False,
        include_size: bool = False,
    ) -> RepositoryInfo:
        if path.exists() and (path / ".git").is_dir():
            try:
                repo = Repository(path)
                return repo.get_status(fetch=fetch, include_size=include_size)
            except Exception as e:
                return RepositoryInfo(path, path.name, RepoStatus.UNKNOWN, None, str(e))
        return RepositoryInfo(
            path,
            path.name,
            RepoStatus.UNKNOWN,
            None,
            "Repository path not found or invalid",
        )
