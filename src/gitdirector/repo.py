import os
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

_NETWORK_ERROR_RE = re.compile(
    r"connection reset"
    r"|connection refused"
    r"|connection timed out"
    r"|network is unreachable"
    r"|name or service not known"
    r"|could not resolve"
    r"|kex_exchange_identification"
    r"|ssh_exchange_identification",
    re.IGNORECASE,
)

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

_NO_COMMITS_RE = re.compile(
    r"does not have any commits yet"
    r"|bad default revision 'HEAD'"
    r"|ambiguous argument 'HEAD'",
    re.IGNORECASE,
)


def _is_network_error(stderr: str) -> bool:
    return _NETWORK_ERROR_RE.search(stderr) is not None


def _classify_remote_error(stderr: str) -> str | None:
    if _is_network_error(stderr):
        return "network error \u2014 could not reach remote"
    if _AUTH_ERROR_RE.search(stderr):
        return "authentication failed \u2014 configure git credentials for this remote"
    return None


def _is_no_commits_error(stderr: str) -> bool:
    return _NO_COMMITS_RE.search(stderr) is not None


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

    def _run_git(self, *args: str, _strip: bool = True, _timeout: int = 30) -> tuple[int, str, str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        if "GIT_SSH_COMMAND" not in env and "GIT_SSH" not in env:
            env["GIT_SSH_COMMAND"] = "ssh -o ConnectTimeout=10"
        try:
            result = subprocess.run(
                ["git", "-C", str(self.path)] + list(args),
                capture_output=True,
                text=True,
                timeout=_timeout,
                env=env,
                stdin=subprocess.DEVNULL,
            )
            stdout = result.stdout.strip() if _strip else result.stdout
            stderr = result.stderr.strip()
            if result.returncode != 0:
                classified = _classify_remote_error(stderr)
                if classified:
                    return result.returncode, stdout, classified
            return result.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            return 1, "", "git command timed out"
        except FileNotFoundError:
            return 1, "", "git not found"

    def get_current_branch(self) -> Optional[str]:
        code, out, _ = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        return out if code == 0 and out not in {"", "HEAD"} else None

    def get_pull_target(self) -> tuple[Optional[str], Optional[str], Optional[str]]:
        code, branch, err = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        if code != 0:
            return None, None, err or "Could not determine current branch"
        if branch in {"", "HEAD"}:
            return None, None, "Cannot pull in detached HEAD"
        return "origin", branch, None

    def _read_only_output(
        self,
        *args: str,
        empty_text: str,
        failure_text: str,
        allow_no_commits: bool = False,
    ) -> tuple[bool, str]:
        code, out, err = self._run_git(*args, _strip=False)
        output = out.rstrip()
        if code == 0:
            return True, output or empty_text
        if allow_no_commits and _is_no_commits_error(err):
            return True, empty_text
        return False, err or failure_text

    def status_output(self) -> tuple[bool, str]:
        return self._read_only_output(
            "status",
            empty_text="Working tree clean.",
            failure_text="git status failed",
        )

    def timeline_output(self) -> tuple[bool, str]:
        return self._read_only_output(
            "log",
            "--max-count=1000",
            "--graph",
            "--decorate",
            "--all",
            "--color=always",
            "--date=short",
            "--pretty=format:%C(auto)%h%Creset %C(blue)%ad%Creset %C(auto)%d%Creset %s",
            empty_text="No commits yet.",
            failure_text="git log failed",
            allow_no_commits=True,
        )

    def branches_output(self) -> tuple[bool, str]:
        return self._read_only_output(
            "branch",
            "-a",
            empty_text="No branches found.",
            failure_text="git branch -a failed",
        )

    def remotes_output(self) -> tuple[bool, str]:
        return self._read_only_output(
            "remote",
            "-v",
            empty_text="No remotes configured.",
            failure_text="git remote -v failed",
        )

    @staticmethod
    def _origin_branch_ref(branch: str) -> str:
        return f"refs/remotes/origin/{branch}"

    def _fetch_origin_branch(self, branch: Optional[str]) -> tuple[int, str]:
        args = ["fetch", "origin"]
        if branch:
            args.append(branch)
        code, _, err = self._run_git(*args)
        return code, err

    def _get_origin_sync_status(self, branch: Optional[str]) -> tuple[RepoStatus, str]:
        if branch is None:
            return RepoStatus.UNKNOWN, "Detached HEAD"

        remote_ref = self._origin_branch_ref(branch)
        code, _, _ = self._run_git("show-ref", "--verify", "--quiet", remote_ref)
        if code != 0:
            return RepoStatus.UNKNOWN, f"No origin/{branch} branch"

        code, out, err = self._run_git(
            "rev-list",
            "--left-right",
            "--count",
            f"HEAD...{remote_ref}",
        )
        if code != 0:
            return RepoStatus.UNKNOWN, err or f"Could not compare HEAD with origin/{branch}"

        parts = out.split()
        try:
            ahead = int(parts[0])
            behind = int(parts[1])
        except (IndexError, ValueError):
            return RepoStatus.UNKNOWN, "Could not determine sync status"

        if ahead > 0 and behind > 0:
            return RepoStatus.DIVERGED, f"ahead {ahead}, behind {behind}"
        if ahead > 0:
            return RepoStatus.AHEAD, f"ahead {ahead}"
        if behind > 0:
            return RepoStatus.BEHIND, f"behind {behind}"
        return RepoStatus.UP_TO_DATE, ""

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

    def get_status(self, *, fetch: bool = False) -> RepositoryInfo:
        code, out, _ = self._run_git("status", "--porcelain=v2", "--branch", _strip=False)
        if code != 0:
            return RepositoryInfo(
                self.path, self.name, RepoStatus.UNKNOWN, None, "git status failed"
            )

        branch = None
        staged = False
        unstaged = False
        staged_files: list[str] = []
        unstaged_files: list[str] = []

        for line in out.splitlines():
            if line.startswith("# branch.head "):
                branch = line[14:]
                if branch == "(detached)":
                    branch = None
            elif line.startswith("1 ") or line.startswith("2 "):
                xy = line[2:4]
                x, y = xy[0], xy[1]
                if line.startswith("1 "):
                    parts = line.split(" ", 8)
                    filename = parts[8] if len(parts) > 8 else ""
                else:
                    parts = line.split(" ", 9)
                    filename = parts[9].split("\t")[0] if len(parts) > 9 else ""
                if x not in (".", "?"):
                    staged = True
                    staged_files.append(filename)
                if y not in (".", "?"):
                    unstaged = True
                    unstaged_files.append(filename)
            elif line.startswith("u "):
                parts = line.split(" ", 10)
                filename = parts[10] if len(parts) > 10 else ""
                staged = True
                unstaged = True
                staged_files.append(filename)
                unstaged_files.append(filename)

        if fetch and branch is not None:
            code, err = self._fetch_origin_branch(branch)
            if code != 0:
                status = RepoStatus.UNKNOWN
                msg = err
            else:
                status, msg = self._get_origin_sync_status(branch)
        else:
            status, msg = self._get_origin_sync_status(branch)

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

    def pull(self, *, retries: int = 1) -> tuple[bool, str]:
        remote, branch, err = self.get_pull_target()
        if err is not None or remote is None or branch is None:
            return False, err or "Could not determine pull target"

        attempts = max(1, 1 + retries)
        for attempt in range(attempts):
            code, out, err = self._run_git("pull", "--ff-only", remote, branch)
            if code == 0:
                return True, out
            if attempt < attempts - 1 and "network error" in err:
                continue
            return False, err
