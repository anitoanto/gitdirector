import os
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

_AUTH_ERROR_RE = re.compile(
    r"could not read username"
    r"|authentication failed"
    r"|permission denied"
    r"|terminal prompts disabled"
    r"|could not read from remote repository"
    r"|unable to access"
    r"|returned error: 40[13]"
    r"|invalid credentials"
    r"|logon failed",
    re.IGNORECASE,
)


def _is_auth_error(stderr: str) -> bool:
    return _AUTH_ERROR_RE.search(stderr) is not None


class RepoStatus(Enum):
    UP_TO_DATE = "up-to-date"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    UNKNOWN = "unknown"


@dataclass
class RepositoryInfo:
    path: Path
    name: str
    status: RepoStatus
    branch: Optional[str] = None
    message: str = ""
    staged: bool = False
    unstaged: bool = False
    staged_files: Optional[list[str]] = None
    unstaged_files: Optional[list[str]] = None
    last_updated: Optional[str] = None
    last_commit_timestamp: Optional[int] = None
    size: Optional[int] = None

    def __repr__(self) -> str:
        return f"{self.name:<30} {self.status.value:<12} {self.branch or 'N/A':<15}"


class Repository:
    def __init__(self, path: Path):
        if not self._is_git_repo(path):
            raise ValueError(f"Not a git repository: {path}")
        self.path = path
        self.name = path.name

    @staticmethod
    def _is_git_repo(path: Path) -> bool:
        return (path / ".git").is_dir()

    def _run_git(self, *args: str, _strip: bool = True) -> tuple[int, str, str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = ""
        env["SSH_ASKPASS"] = ""
        try:
            result = subprocess.run(
                ["git", "-C", str(self.path)] + list(args),
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                stdin=subprocess.DEVNULL,
            )
            stdout = result.stdout.strip() if _strip else result.stdout
            stderr = result.stderr.strip()
            if result.returncode != 0 and _is_auth_error(stderr):
                return (
                    result.returncode,
                    stdout,
                    "authentication failed \u2014 configure git credentials for this remote",
                )
            return result.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            return 1, "", "git command timed out"
        except FileNotFoundError:
            return 1, "", "git not found"

    def get_current_branch(self) -> Optional[str]:
        code, out, _ = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        return out if code == 0 else None

    def get_last_commit_info(self) -> tuple[Optional[str], Optional[int]]:
        code, out, _ = self._run_git("log", "-1", "--format=%cd%n%ct", "--date=relative")
        if code != 0 or not out:
            return None, None
        lines = out.split("\n", 1)
        date = lines[0] if lines[0] else None
        ts: Optional[int] = None
        if len(lines) > 1 and lines[1]:
            try:
                ts = int(lines[1])
            except ValueError:
                pass
        return date, ts

    def get_tracked_size(self) -> Optional[int]:
        """Return total byte size of all tracked files (respects .gitignore)."""
        code, out, _ = self._run_git("ls-tree", "-r", "-l", "--full-tree", "HEAD", _strip=False)
        if code != 0 or not out:
            return None
        total = 0
        for line in out.split("\n"):
            if not line:
                continue
            parts = line.split(None, 4)
            if len(parts) >= 4:
                try:
                    total += int(parts[3])
                except ValueError:
                    pass
        return total

    def get_status(self) -> RepositoryInfo:
        branch = self.get_current_branch()

        code, out, err = self._run_git("fetch")
        if code != 0:
            return RepositoryInfo(self.path, self.name, RepoStatus.UNKNOWN, branch, err)

        code, ahead_behind, _ = self._run_git("rev-list", "--left-right", "--count", "@{u}...HEAD")

        if code != 0:
            return RepositoryInfo(
                self.path, self.name, RepoStatus.UNKNOWN, branch, "No tracking branch"
            )

        try:
            behind, ahead = map(int, ahead_behind.split())
            if ahead > 0 and behind > 0:
                status = RepoStatus.DIVERGED
                msg = f"ahead {ahead}, behind {behind}"
            elif ahead > 0:
                status = RepoStatus.AHEAD
                msg = f"ahead {ahead}"
            elif behind > 0:
                status = RepoStatus.BEHIND
                msg = f"behind {behind}"
            else:
                status = RepoStatus.UP_TO_DATE
                msg = ""
        except ValueError:
            status = RepoStatus.UNKNOWN
            msg = "Could not parse git status"

        code, porcelain, _ = self._run_git("status", "--porcelain", _strip=False)
        staged = False
        unstaged = False
        staged_files: list[str] = []
        unstaged_files: list[str] = []
        if code == 0 and porcelain:
            for line in porcelain.splitlines():
                if len(line) >= 2:
                    x, y = line[0], line[1]
                    filename = line[3:].strip()
                    if x not in (" ", "?"):
                        staged = True
                        staged_files.append(filename)
                    if y not in (" ", "?"):
                        unstaged = True
                        unstaged_files.append(filename)

        last_updated, last_commit_ts = self.get_last_commit_info()
        size = self.get_tracked_size()

        return RepositoryInfo(
            self.path,
            self.name,
            status,
            branch,
            msg,
            staged,
            unstaged,
            staged_files or None,
            unstaged_files or None,
            last_updated,
            last_commit_ts,
            size,
        )

    def pull(self) -> tuple[bool, str]:
        code, out, err = self._run_git("pull", "--ff-only")
        if code == 0:
            return True, out
        return False, err
