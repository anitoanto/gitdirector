import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gitdirector.repo import (
    Repository,
    RepositoryInfo,
    RepoStatus,
    _classify_remote_error,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_result(returncode=0, stdout="", stderr=""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# ---------------------------------------------------------------------------
# Repository.__init__ / _is_git_repo
# ---------------------------------------------------------------------------


class TestIsGitRepo:
    def test_valid_repo(self, fake_git_repo):
        repo = Repository(fake_git_repo)
        assert repo.path == fake_git_repo
        assert repo.name == fake_git_repo.name

    def test_not_a_repo(self, tmp_path):
        with pytest.raises(ValueError, match="Not a git repository"):
            Repository(tmp_path)


# ---------------------------------------------------------------------------
# _run_git
# ---------------------------------------------------------------------------


class TestRunGit:
    def test_success(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(0, "ok\n", ""),
        )
        repo = Repository(fake_git_repo)
        code, out, err = repo._run_git("status")
        assert code == 0
        assert out == "ok"

    def test_failure(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(128, "", "fatal: error\n"),
        )
        repo = Repository(fake_git_repo)
        code, out, err = repo._run_git("status")
        assert code == 128
        assert "fatal" in err

    def test_timeout(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10),
        )
        repo = Repository(fake_git_repo)
        code, out, err = repo._run_git("fetch")
        assert code == 1
        assert "timed out" in err

    def test_git_not_found(self, fake_git_repo, mocker):
        mocker.patch("subprocess.run", side_effect=FileNotFoundError)
        repo = Repository(fake_git_repo)
        code, out, err = repo._run_git("status")
        assert code == 1
        assert "not found" in err


# ---------------------------------------------------------------------------
# get_current_branch
# ---------------------------------------------------------------------------


class TestGetCurrentBranch:
    def test_success(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(0, "main\n", ""),
        )
        repo = Repository(fake_git_repo)
        assert repo.get_current_branch() == "main"

    def test_failure_returns_none(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(128, "", "fatal\n"),
        )
        repo = Repository(fake_git_repo)
        assert repo.get_current_branch() is None


# ---------------------------------------------------------------------------
# get_last_commit_info
# ---------------------------------------------------------------------------


class TestGetLastCommitInfo:
    def test_success(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(0, "2 hours ago\n1700000000\n", ""),
        )
        repo = Repository(fake_git_repo)
        date, ts = repo.get_last_commit_info()
        assert date == "2 hours ago"
        assert ts == 1700000000

    def test_empty_repo(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(0, "", ""),
        )
        repo = Repository(fake_git_repo)
        date, ts = repo.get_last_commit_info()
        assert date is None
        assert ts is None


# ---------------------------------------------------------------------------
# get_tracked_size
# ---------------------------------------------------------------------------


class TestGetTrackedSize:
    def test_computes_total(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(
                0,
                "100644 blob abc123       5\ta.txt\n100644 blob def456       6\tb.txt\n",
                "",
            ),
        )
        repo = Repository(fake_git_repo)
        assert repo.get_tracked_size() == 11

    def test_git_failure(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(1, "", "error"),
        )
        repo = Repository(fake_git_repo)
        assert repo.get_tracked_size() is None

    def test_empty_output(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(0, "", ""),
        )
        repo = Repository(fake_git_repo)
        assert repo.get_tracked_size() is None


# ---------------------------------------------------------------------------
# get_status  – various sync states
# ---------------------------------------------------------------------------


def _setup_status_mocks(mocker, ahead_behind="0\t0", porcelain="", fetch_ok=True, branch="main"):
    """Configure subprocess.run to return canned values for get_status flow."""
    calls = []

    def side_effect(cmd, **kwargs):
        args = cmd[2:]  # strip ["git", "-C"]
        git_args = args[1:]  # strip the repo path
        calls.append(git_args)

        if "fetch" in git_args:
            return _make_run_result(0 if fetch_ok else 1, "", "" if fetch_ok else "fetch error")
        if "status" in git_args:
            v2 = f"# branch.oid abc123\n# branch.head {branch}\n"
            if ahead_behind:
                try:
                    behind_val, ahead_val = ahead_behind.split("\t")
                    v2 += f"# branch.upstream origin/{branch}\n"
                    v2 += f"# branch.ab +{ahead_val} -{behind_val}\n"
                except ValueError:
                    pass
            if porcelain:
                for line in porcelain.splitlines():
                    if len(line) >= 2:
                        x, y = line[0], line[1]
                        filename = line[3:].strip() if len(line) > 3 else ""
                        if x == "?" and y == "?":
                            v2 += f"? {filename}\n"
                        else:
                            v2_x = x if x != " " else "."
                            v2_y = y if y != " " else "."
                            v2 += f"1 {v2_x}{v2_y} N... 100644 100644 100644 abc def {filename}\n"
            return _make_run_result(0, v2, "")
        if "log" in git_args:
            return _make_run_result(0, "5 minutes ago\n", "")
        if "ls-files" in git_args:
            return _make_run_result(0, "", "")
        return _make_run_result(0, "", "")

    mocker.patch("subprocess.run", side_effect=side_effect)
    return calls


class TestGetStatusSync:
    def test_up_to_date(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, ahead_behind="0\t0")
        info = Repository(fake_git_repo).get_status()
        assert info.status == RepoStatus.UP_TO_DATE
        assert info.branch == "main"

    def test_ahead(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, ahead_behind="0\t3")
        info = Repository(fake_git_repo).get_status()
        assert info.status == RepoStatus.AHEAD
        assert "ahead 3" in info.message

    def test_behind(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, ahead_behind="5\t0")
        info = Repository(fake_git_repo).get_status()
        assert info.status == RepoStatus.BEHIND
        assert "behind 5" in info.message

    def test_diverged(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, ahead_behind="2\t3")
        info = Repository(fake_git_repo).get_status()
        assert info.status == RepoStatus.DIVERGED
        assert "ahead" in info.message and "behind" in info.message

    def test_fetch_failure(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, fetch_ok=False)
        info = Repository(fake_git_repo).get_status(fetch=True)
        assert info.status == RepoStatus.UNKNOWN

    def test_no_tracking_branch(self, fake_git_repo, mocker):
        """rev-list fails when there's no upstream."""
        _setup_status_mocks(mocker, ahead_behind=None)
        info = Repository(fake_git_repo).get_status()
        assert info.status == RepoStatus.UNKNOWN
        assert "No tracking branch" in info.message

    def test_git_status_fails(self, fake_git_repo, mocker):
        def side_effect(cmd, **kwargs):
            git_args = cmd[3:]
            if "status" in git_args:
                return _make_run_result(1, "", "error")
            return _make_run_result(0, "", "")

        mocker.patch("subprocess.run", side_effect=side_effect)
        info = Repository(fake_git_repo).get_status()
        assert info.status == RepoStatus.UNKNOWN
        assert info.message == "git status failed"


class TestGetStatusChanges:
    def test_staged_files(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, porcelain="M  file.py\n")
        info = Repository(fake_git_repo).get_status()
        assert info.staged is True
        assert info.staged_files == ["file.py"]
        assert info.unstaged is False

    def test_unstaged_files(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, porcelain=" M file.py\n")
        info = Repository(fake_git_repo).get_status()
        assert info.unstaged is True
        assert info.unstaged_files == ["file.py"]
        assert info.staged is False

    def test_staged_and_unstaged(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, porcelain="M  a.py\n M b.py\n")
        info = Repository(fake_git_repo).get_status()
        assert info.staged is True
        assert info.unstaged is True

    def test_untracked_files_ignored(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, porcelain="?? newfile.py\n")
        info = Repository(fake_git_repo).get_status()
        assert info.staged is False
        assert info.unstaged is False

    def test_clean_working_tree(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, porcelain="")
        info = Repository(fake_git_repo).get_status()
        assert info.staged is False
        assert info.unstaged is False
        assert info.staged_files is None
        assert info.unstaged_files is None


# ---------------------------------------------------------------------------
# get_status – detached HEAD, renames, unmerged
# ---------------------------------------------------------------------------


def _setup_raw_status(mocker, status_output):
    """Mock _run_git to return raw v2 status output for get_status tests."""

    def side_effect(cmd, **kwargs):
        args = cmd[2:]
        git_args = args[1:]

        if "status" in git_args:
            return _make_run_result(0, status_output, "")
        if "log" in git_args:
            return _make_run_result(0, "5 minutes ago\n1700000000\n", "")
        if "ls-tree" in git_args:
            return _make_run_result(0, "", "")
        return _make_run_result(0, "", "")

    mocker.patch("subprocess.run", side_effect=side_effect)


class TestGetStatusDetachedHead:
    def test_detached_head(self, fake_git_repo, mocker):
        v2 = "# branch.oid abc123\n# branch.head (detached)\n# branch.ab +0 -0\n"
        _setup_raw_status(mocker, v2)
        info = Repository(fake_git_repo).get_status()
        assert info.branch is None


class TestGetStatusRenameEntry:
    def test_staged_rename(self, fake_git_repo, mocker):
        v2 = (
            "# branch.oid abc123\n"
            "# branch.head main\n"
            "# branch.ab +0 -0\n"
            "2 R. N... 100644 100644 100644 abc def R100\told.py\tnew.py\n"
        )
        _setup_raw_status(mocker, v2)
        info = Repository(fake_git_repo).get_status()
        assert info.staged is True
        assert info.staged_files is not None

    def test_unstaged_rename(self, fake_git_repo, mocker):
        v2 = (
            "# branch.oid abc123\n"
            "# branch.head main\n"
            "# branch.ab +0 -0\n"
            "2 .R N... 100644 100644 100644 abc def R100\told.py\tnew.py\n"
        )
        _setup_raw_status(mocker, v2)
        info = Repository(fake_git_repo).get_status()
        assert info.unstaged is True
        assert info.unstaged_files is not None


class TestGetStatusUnmergedEntry:
    def test_unmerged_file(self, fake_git_repo, mocker):
        v2 = (
            "# branch.oid abc123\n"
            "# branch.head main\n"
            "# branch.ab +0 -0\n"
            "u UU N... 100644 100644 100644 100644 abc def ghi conflict.py\n"
        )
        _setup_raw_status(mocker, v2)
        info = Repository(fake_git_repo).get_status()
        assert info.staged is True
        assert info.unstaged is True
        assert "conflict.py" in (info.staged_files or [])
        assert "conflict.py" in (info.unstaged_files or [])


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------


class TestPull:
    def test_success(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(0, "Already up to date.\n", ""),
        )
        repo = Repository(fake_git_repo)
        ok, msg = repo.pull()
        assert ok is True
        assert "Already up to date" in msg

    def test_failure(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(1, "", "fatal: Not possible to fast-forward\n"),
        )
        repo = Repository(fake_git_repo)
        ok, msg = repo.pull()
        assert ok is False
        assert "fast-forward" in msg

    def test_retry_on_network_error(self, fake_git_repo, mocker):
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 1:
                return _make_run_result(1, "", "network error \u2014 could not reach remote")
            return _make_run_result(0, "Updated.\n", "")

        mocker.patch("subprocess.run", side_effect=side_effect)
        repo = Repository(fake_git_repo)
        ok, msg = repo.pull()
        assert ok is True
        assert len(calls) == 2

    def test_no_retry_on_non_network_error(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(1, "", "fatal: some error"),
        )
        repo = Repository(fake_git_repo)
        ok, msg = repo.pull()
        assert ok is False

    def test_retry_exhausted(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(1, "", "network error \u2014 could not reach remote"),
        )
        repo = Repository(fake_git_repo)
        ok, msg = repo.pull(retries=2)
        assert ok is False
        assert "network error" in msg

    def test_negative_retries_still_attempt_once(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(1, "", "fatal: some error"),
        )
        repo = Repository(fake_git_repo)

        ok, msg = repo.pull(retries=-1)

        assert ok is False
        assert "fatal" in msg


# ---------------------------------------------------------------------------
# _classify_remote_error
# ---------------------------------------------------------------------------


class TestClassifyRemoteError:
    def test_network_error(self):
        assert "network error" in _classify_remote_error("connection refused")

    def test_auth_error(self):
        assert "authentication" in _classify_remote_error("authentication failed")

    def test_no_match(self):
        assert _classify_remote_error("fatal: some other error") is None


# ---------------------------------------------------------------------------
# _run_git error classification
# ---------------------------------------------------------------------------


class TestRunGitErrorClassification:
    def test_network_error_classified(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(128, "", "connection refused\n"),
        )
        repo = Repository(fake_git_repo)
        code, out, err = repo._run_git("fetch")
        assert code == 128
        assert "network error" in err

    def test_auth_error_classified(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(128, "", "authentication failed\n"),
        )
        repo = Repository(fake_git_repo)
        code, out, err = repo._run_git("fetch")
        assert code == 128
        assert "authentication" in err


# ---------------------------------------------------------------------------
# RepositoryInfo repr
# ---------------------------------------------------------------------------


class TestGetTrackedSizeValueError:
    def test_non_integer_size_field(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(
                0,
                "100644 blob abc123       NaN\ta.txt\n100644 blob def456       6\tb.txt\n",
                "",
            ),
        )
        repo = Repository(fake_git_repo)
        assert repo.get_tracked_size() == 6


class TestRepositoryInfo:
    def test_repr(self):
        info = RepositoryInfo(
            path=Path("/tmp/repo"),
            name="repo",
            status=RepoStatus.UP_TO_DATE,
            branch="main",
        )
        text = repr(info)
        assert "repo" in text
        assert "up-to-date" in text
        assert "main" in text
