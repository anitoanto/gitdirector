import os
import re
import shlex
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

_ORIGINAL_SUBPROCESS_RUN = subprocess.run
_RUNNING_GIT_PROCESSES: set[subprocess.Popen] = set()
_RUNNING_GIT_PROCESSES_LOCK = threading.Lock()

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
    r"|logon failed"
    r"|repository not found"
    r"|support for password authentication was removed",
    re.IGNORECASE,
)

_NO_COMMITS_RE = re.compile(
    r"does not have any commits yet" r"|bad default revision 'HEAD'" r"|ambiguous argument 'HEAD'",
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


def _is_auth_error(stderr: str) -> bool:
    return _AUTH_ERROR_RE.search(stderr) is not None


def _github_credentials_from_config() -> tuple[str, str] | None:
    from .config import Config

    try:
        config = Config()
    except Exception:
        return None
    if not config.github_username or not config.github_PAT:
        return None
    return config.github_username, config.github_PAT


def _add_env_git_config(env: dict[str, str], key: str, value: str) -> None:
    try:
        index = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        index = 0
    env[f"GIT_CONFIG_KEY_{index}"] = key
    env[f"GIT_CONFIG_VALUE_{index}"] = value
    env["GIT_CONFIG_COUNT"] = str(index + 1)


def _apply_github_auth_env(env: dict[str, str], username: str, token: str) -> None:
    helper = f"!{shlex.quote(sys.executable)} -m gitdirector.github_credential_helper"
    _add_env_git_config(env, "credential.helper", "")
    _add_env_git_config(env, "credential.helper", helper)
    env["GITDIRECTOR_GITHUB_USERNAME"] = username
    env["GITDIRECTOR_GITHUB_PAT"] = token


def _register_running_git_process(process: subprocess.Popen) -> None:
    with _RUNNING_GIT_PROCESSES_LOCK:
        _RUNNING_GIT_PROCESSES.add(process)


def _unregister_running_git_process(process: subprocess.Popen) -> None:
    with _RUNNING_GIT_PROCESSES_LOCK:
        _RUNNING_GIT_PROCESSES.discard(process)


def _kill_running_git_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        pass


def _normalize_git_result(
    returncode: int,
    stdout: str,
    stderr: str,
    *,
    strip_stdout: bool,
) -> tuple[int, str, str]:
    normalized_stdout = stdout.strip() if strip_stdout else stdout
    normalized_stderr = stderr.strip()

    if returncode < 0:
        return 1, normalized_stdout, normalized_stderr or "git command cancelled"

    if returncode != 0:
        classified = _classify_remote_error(normalized_stderr)
        if classified:
            return returncode, normalized_stdout, classified

    return returncode, normalized_stdout, normalized_stderr


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

    @classmethod
    def kill_running_git_commands(cls) -> None:
        with _RUNNING_GIT_PROCESSES_LOCK:
            processes = tuple(_RUNNING_GIT_PROCESSES)
        for process in processes:
            _kill_running_git_process(process)

    def _git_env(self, *, github_auth: tuple[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        if "GIT_SSH_COMMAND" not in env and "GIT_SSH" not in env:
            env["GIT_SSH_COMMAND"] = "ssh -o ConnectTimeout=10"
        if github_auth is not None:
            username, token = github_auth
            _apply_github_auth_env(env, username, token)
        return env

    def _run_git_once(
        self,
        *args: str,
        _strip: bool,
        _timeout: int,
        _github_auth: tuple[str, str] | None = None,
    ) -> tuple[int, str, str, str]:
        env = self._git_env(github_auth=_github_auth)
        command = ["git", "-C", str(self.path)] + list(args)
        try:
            if subprocess.run is not _ORIGINAL_SUBPROCESS_RUN:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=_timeout,
                    env=env,
                    stdin=subprocess.DEVNULL,
                )
                code, out, err = _normalize_git_result(
                    result.returncode,
                    result.stdout,
                    result.stderr,
                    strip_stdout=_strip,
                )
                return code, out, err, result.stderr.strip()

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except subprocess.TimeoutExpired:
            return 1, "", "git command timed out", "git command timed out"
        except FileNotFoundError:
            return 1, "", "git not found", "git not found"

        _register_running_git_process(process)
        try:
            stdout, stderr = process.communicate(timeout=_timeout)
        except subprocess.TimeoutExpired:
            _kill_running_git_process(process)
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            return 1, "", "git command timed out", "git command timed out"
        finally:
            _unregister_running_git_process(process)

        code, out, err = _normalize_git_result(
            process.returncode if process.returncode is not None else 1,
            stdout,
            stderr,
            strip_stdout=_strip,
        )
        return code, out, err, stderr.strip()

    def _run_git(self, *args: str, _strip: bool = True, _timeout: int = 30) -> tuple[int, str, str]:
        code, out, err, raw_err = self._run_git_once(*args, _strip=_strip, _timeout=_timeout)
        if code == 0 or not _is_auth_error(raw_err or err):
            return code, out, err

        github_auth = _github_credentials_from_config()
        if github_auth is None:
            return code, out, err

        retry_code, retry_out, retry_err, _ = self._run_git_once(
            *args,
            _strip=_strip,
            _timeout=_timeout,
            _github_auth=github_auth,
        )
        return retry_code, retry_out, retry_err

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

    def get_status(self, *, fetch: bool = False, include_size: bool = True) -> RepositoryInfo:
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
            elif line.startswith("? "):
                filename = line[2:]
                unstaged = True
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
        size = self.get_tracked_size() if include_size else None

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

    def add(self, paths: list[str] | None = None) -> tuple[bool, str]:
        """Stage files for the next commit.

        With ``paths=None`` runs ``git add -A`` to stage every change
        (tracked modifications plus untracked, minus paths excluded by
        the repo's ``.gitignore``). With an explicit ``paths`` list runs
        ``git add -- <paths>`` to stage only those relative paths.
        """
        if paths:
            code, out, err = self._run_git("add", "--", *paths)
        else:
            code, out, err = self._run_git("add", "-A")
        if code != 0:
            return False, err or out or "git add failed"
        return True, out

    def commit(self, message: str) -> tuple[bool, str]:
        """Create a commit with ``message``; returns ``(ok, output)``."""
        if not message or not message.strip():
            return False, "commit message is empty"
        code, out, err = self._run_git("commit", "-m", message)
        if code != 0:
            return False, err or out or "git commit failed"
        return True, out

    def push(self, *, set_upstream: bool = False) -> tuple[bool, str]:
        """Push the current branch to its upstream.

        With ``set_upstream=True`` runs ``git push -u origin <branch>`` so
        the local branch tracks the remote on the first push. Subsequent
        pushes (or pushes from a clone that already has the tracking
        branch) can pass ``set_upstream=False`` for a plain ``git push``.
        """
        if set_upstream:
            branch = self.get_current_branch()
            if not branch:
                return False, "Could not determine current branch"
            code, out, err = self._run_git("push", "-u", "origin", branch)
        else:
            code, out, err = self._run_git("push")
        if code != 0:
            return False, err or out or "git push failed"
        return True, out

    def get_diff_against_head(
        self, *, max_bytes: int = 2 * 1024 * 1024
    ) -> tuple[bool, str, list[str]]:
        """Return combined uncommitted diff plus list of untracked file paths.

        The diff covers both staged and unstaged changes for tracked files,
        produced by ``git diff HEAD`` so reviewers see a single coherent
        picture of uncommitted work. Untracked files are returned separately
        because git cannot produce a diff for files that have no HEAD entry.

        The diff text is capped at ``max_bytes`` to keep the TUI responsive
        on very large changesets. A truncated diff is signalled by a trailing
        marker line that the renderer can display to the user.
        """
        code, diff_text, err = self._run_git("diff", "HEAD", "--no-color", _strip=False)
        if code != 0:
            return False, err or "git diff failed", []

        untracked = self._list_untracked_files()
        if len(diff_text.encode("utf-8", errors="replace")) > max_bytes:
            truncated = diff_text.encode("utf-8", errors="replace")[:max_bytes].decode(
                "utf-8", errors="replace"
            )
            diff_text = truncated + "\n[gd-truncated] diff exceeded size cap\n"
        elif diff_text and not diff_text.endswith("\n"):
            diff_text += "\n"
        return True, diff_text, untracked

    def _list_untracked_files(self) -> list[str]:
        code, out, _ = self._run_git(
            "ls-files", "--others", "--exclude-standard", "-z", _strip=False
        )
        if code != 0 or not out:
            return []
        paths: list[str] = []
        for chunk in out.split("\x00"):
            if chunk:
                paths.append(chunk)
        return paths

    def read_file_text(self, rel_path: str, *, max_bytes: int = 256 * 1024) -> str | None:
        """Read a tracked or untracked file's text content, capped to ``max_bytes``.

        Returns ``None`` if the file is missing, binary, or unreadable.
        """
        candidate = self.path / rel_path
        if not candidate.is_file():
            return None
        try:
            size = candidate.stat().st_size
        except OSError:
            return None
        if size > max_bytes:
            try:
                with candidate.open("rb") as fh:
                    data = fh.read(max_bytes)
            except OSError:
                return None
            return data.decode("utf-8", errors="replace") + "\n[gd-truncated]\n"
        try:
            with candidate.open("rb") as fh:
                data = fh.read()
        except OSError:
            return None
        if b"\x00" in data:
            return None
        return data.decode("utf-8", errors="replace")
