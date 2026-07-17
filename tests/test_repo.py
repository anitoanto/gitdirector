import os
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gitdirector import repo as repo_mod
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


def _run_git(repo_dir: Path, *args: str) -> None:
    env = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        env.pop(name, None)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=10,
    )


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

    def test_auth_failure_retries_with_configured_github_credentials(
        self, fake_git_repo, config, mocker, monkeypatch
    ):
        monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
        monkeypatch.delenv("GITDIRECTOR_GITHUB_USERNAME", raising=False)
        monkeypatch.delenv("GITDIRECTOR_GITHUB_PAT", raising=False)
        config.github_username = "octocat"
        config.github_PAT = "ghp_secret"
        config.save()
        run_git = mocker.patch(
            "subprocess.run",
            side_effect=[
                _make_run_result(
                    128,
                    "",
                    "fatal: could not read Username for 'https://github.com': "
                    "terminal prompts disabled\n",
                ),
                _make_run_result(0, "pushed\n", ""),
            ],
        )
        repo = Repository(fake_git_repo)

        code, out, err = repo._run_git("push")

        assert code == 0
        assert out == "pushed"
        assert err == ""
        assert run_git.call_count == 2
        first_env = run_git.call_args_list[0].kwargs["env"]
        retry_env = run_git.call_args_list[1].kwargs["env"]
        retry_command = run_git.call_args_list[1].args[0]
        assert "GITDIRECTOR_GITHUB_PAT" not in first_env
        assert retry_env["GITDIRECTOR_GITHUB_USERNAME"] == "octocat"
        assert retry_env["GITDIRECTOR_GITHUB_PAT"] == "ghp_secret"
        assert retry_env["GIT_CONFIG_COUNT"] == "2"
        assert retry_env["GIT_CONFIG_KEY_0"] == "credential.helper"
        assert retry_env["GIT_CONFIG_VALUE_0"] == ""
        assert retry_env["GIT_CONFIG_KEY_1"] == "credential.helper"
        assert "gitdirector.github_credential_helper" in retry_env["GIT_CONFIG_VALUE_1"]
        assert "ghp_secret" not in " ".join(retry_command)

    def test_auth_failure_without_configured_github_credentials_does_not_retry(
        self, fake_git_repo, mocker
    ):
        mocker.patch("gitdirector.repo._github_credentials_from_config", return_value=None)
        run_git = mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(
                128,
                "",
                "fatal: could not read Username for 'https://github.com': "
                "terminal prompts disabled\n",
            ),
        )
        repo = Repository(fake_git_repo)

        code, _, err = repo._run_git("push")

        assert code == 128
        assert "authentication failed" in err
        run_git.assert_called_once()

    def test_running_git_commands_can_be_killed(self, fake_git_repo, mocker):
        started = threading.Event()
        killed = threading.Event()

        class FakeProcess:
            def __init__(self):
                self.pid = 4321
                self.returncode = None

            def poll(self):
                return self.returncode

            def kill(self):
                self.returncode = -9
                killed.set()

            def communicate(self, timeout=None):
                started.set()
                if not killed.wait(timeout or 1):
                    raise AssertionError("expected running git command to be killed")
                return "", ""

        mocker.patch("gitdirector.repo.subprocess.Popen", return_value=FakeProcess())
        killpg = mocker.patch(
            "gitdirector.repo.os.killpg",
            side_effect=lambda pid, sig: (setattr(worker_process, "returncode", -9), killed.set()),
        )

        repo = Repository(fake_git_repo)
        result: dict[str, tuple[int, str, str]] = {}
        worker_process = repo._run_git.__globals__["subprocess"].Popen.return_value

        worker = threading.Thread(
            target=lambda: result.setdefault("value", repo._run_git("fetch")),
            daemon=True,
        )
        worker.start()

        assert started.wait(5)

        Repository.kill_running_git_commands()

        worker.join(timeout=5)
        assert not worker.is_alive()
        assert result["value"] == (1, "", "git command cancelled")
        killpg.assert_called_once()


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


def _setup_status_mocks(
    mocker,
    ahead_behind="0\t0",
    porcelain="",
    fetch_ok=True,
    branch="main",
    remote_exists=True,
):
    """Configure subprocess.run to return canned values for get_status flow."""
    calls = []

    def side_effect(cmd, **kwargs):
        args = cmd[2:]  # strip ["git", "-C"]
        git_args = args[1:]  # strip the repo path
        calls.append(git_args)

        if git_args[:2] == ["fetch", "origin"]:
            return _make_run_result(0 if fetch_ok else 1, "", "" if fetch_ok else "fetch error")
        if "status" in git_args:
            v2 = f"# branch.oid abc123\n# branch.head {branch}\n"
            if porcelain:
                for line in porcelain.splitlines():
                    if len(line) >= 2:
                        x, y = line[0], line[1]
                        filename = line[3:].strip() if len(line) > 3 else ""
                        if x == "?" and y == "?":
                            v2 += f"? {filename}\n"
                        elif x == "!":
                            v2 += f"! {filename}\n"
                        else:
                            v2_x = x if x != " " else "."
                            v2_y = y if y != " " else "."
                            v2 += f"1 {v2_x}{v2_y} N... 100644 100644 100644 abc def {filename}\n"
            return _make_run_result(0, v2, "")
        if git_args[:3] == ["show-ref", "--verify", "--quiet"]:
            return _make_run_result(0 if remote_exists else 1, "", "")
        if git_args[:3] == ["rev-list", "--left-right", "--count"]:
            if ahead_behind is None or not remote_exists:
                return _make_run_result(1, "", "missing origin branch")
            behind_val, ahead_val = ahead_behind.split("\t")
            return _make_run_result(0, f"{ahead_val}\t{behind_val}\n", "")
        if "log" in git_args:
            return _make_run_result(0, "5 minutes ago\n", "")
        if "ls-tree" in git_args:
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

    def test_skips_size_when_excluded(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, ahead_behind="0\t0")
        size_spy = mocker.patch.object(Repository, "get_tracked_size", return_value=2048)

        info = Repository(fake_git_repo).get_status(include_size=False)

        assert info.size is None
        size_spy.assert_not_called()

    def test_includes_size_when_requested(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, ahead_behind="0\t0")
        size_spy = mocker.patch.object(Repository, "get_tracked_size", return_value=2048)

        info = Repository(fake_git_repo).get_status(include_size=True)

        assert info.size == 2048
        size_spy.assert_called_once_with()

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
        assert info.message == "fetch error"

    def test_no_tracking_branch(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, ahead_behind=None, remote_exists=False)
        info = Repository(fake_git_repo).get_status()
        assert info.status == RepoStatus.UNKNOWN
        assert info.message == "No origin/main branch"

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

    def test_untracked_files_marked_unstaged(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, porcelain="?? newfile.py\n")
        info = Repository(fake_git_repo).get_status()
        assert info.staged is False
        assert info.unstaged is True
        assert info.unstaged_files == ["newfile.py"]

    def test_untracked_and_staged_change(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, porcelain="M  a.py\n?? newfile.py\n")
        info = Repository(fake_git_repo).get_status()
        assert info.staged is True
        assert info.staged_files == ["a.py"]
        assert info.unstaged is True
        assert info.unstaged_files == ["newfile.py"]

    def test_multiple_untracked_files(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, porcelain="?? a.py\n?? b.py\n")
        info = Repository(fake_git_repo).get_status()
        assert info.staged is False
        assert info.unstaged is True
        assert info.unstaged_files == ["a.py", "b.py"]

    def test_ignored_files_ignored(self, fake_git_repo, mocker):
        _setup_status_mocks(mocker, porcelain="! ignored.py\n")
        info = Repository(fake_git_repo).get_status()
        assert info.staged is False
        assert info.unstaged is False
        assert info.staged_files is None
        assert info.unstaged_files is None

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
        if git_args[:3] == ["show-ref", "--verify", "--quiet"]:
            return _make_run_result(0, "", "")
        if git_args[:3] == ["rev-list", "--left-right", "--count"]:
            return _make_run_result(0, "0\t0\n", "")
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


def _setup_pull_mocks(
    mocker,
    *,
    branch_result=(0, "main\n", ""),
    pull_results=((0, "Already up to date.\n", ""),),
):
    calls = []
    remaining_pull_results = list(pull_results)

    def side_effect(cmd, **kwargs):
        args = cmd[2:]
        git_args = args[1:]
        calls.append(git_args)

        if git_args[:2] == ["rev-parse", "--abbrev-ref"]:
            return _make_run_result(*branch_result)
        if git_args and git_args[0] == "pull":
            result = remaining_pull_results.pop(0)
            return _make_run_result(*result)
        return _make_run_result(0, "", "")

    mocker.patch("subprocess.run", side_effect=side_effect)
    return calls


class TestPull:
    def test_get_pull_target(self, fake_git_repo, mocker):
        _setup_pull_mocks(mocker, pull_results=())
        repo = Repository(fake_git_repo)

        remote, branch, err = repo.get_pull_target()

        assert (remote, branch, err) == ("origin", "main", None)

    def test_get_pull_target_branch_error(self, fake_git_repo, mocker):
        _setup_pull_mocks(mocker, branch_result=(128, "", "fatal: no branch"), pull_results=())
        repo = Repository(fake_git_repo)

        remote, branch, err = repo.get_pull_target()

        assert remote is None
        assert branch is None
        assert err == "fatal: no branch"

    def test_status_output_success(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(
                0,
                "On branch main\nnothing to commit, working tree clean\n",
                "",
            ),
        )
        repo = Repository(fake_git_repo)

        ok, output = repo.status_output()

        assert ok is True
        assert "On branch main" in output

    def test_status_output_failure(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(1, "", "fatal: status failed\n"),
        )
        repo = Repository(fake_git_repo)

        ok, output = repo.status_output()

        assert ok is False
        assert output == "fatal: status failed"


class TestAddCommitPush:
    def _repo_path(self, fake_git_repo):
        return str(fake_git_repo)

    def test_add_all(self, fake_git_repo, mocker):
        run_git = mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(0, "", ""),
        )
        repo = Repository(fake_git_repo)

        ok, output = repo.add()

        assert ok is True
        assert output == ""
        argv = run_git.call_args.args[0]
        assert argv[:3] == ["git", "-C", self._repo_path(fake_git_repo)]
        assert argv[3:] == ["add", "-A"]

    def test_add_specific_paths(self, fake_git_repo, mocker):
        run_git = mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(0, "", ""),
        )
        repo = Repository(fake_git_repo)

        ok, output = repo.add(["src/foo.py", "src/bar.py"])

        assert ok is True
        argv = run_git.call_args.args[0]
        assert argv[3:] == ["add", "--", "src/foo.py", "src/bar.py"]

    def test_add_failure(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(1, "", "fatal: pathspec 'nope' did not match\n"),
        )
        repo = Repository(fake_git_repo)

        ok, output = repo.add()

        assert ok is False
        assert "pathspec" in output

    def test_commit_success(self, fake_git_repo, mocker):
        run_git = mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(0, "[main abc1234] test\n", ""),
        )
        repo = Repository(fake_git_repo)

        ok, output = repo.commit("test")

        assert ok is True
        assert "[main abc1234]" in output
        argv = run_git.call_args.args[0]
        assert argv[3:6] == ["commit", "-m", "test"]

    def test_commit_empty_message_rejected_locally(self, fake_git_repo, mocker):
        run_git = mocker.patch("subprocess.run")
        repo = Repository(fake_git_repo)

        ok, msg = repo.commit("   ")

        assert ok is False
        assert "empty" in msg.lower()
        run_git.assert_not_called()

    def test_commit_failure(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(1, "", "error: nothing to commit\n"),
        )
        repo = Repository(fake_git_repo)

        ok, output = repo.commit("msg")

        assert ok is False
        assert "nothing to commit" in output

    def test_push_plain(self, fake_git_repo, mocker):
        run_git = mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(0, "To origin\n", ""),
        )
        repo = Repository(fake_git_repo)

        ok, output = repo.push()

        assert ok is True
        assert "origin" in output
        assert run_git.call_args.args[0][3:] == ["push"]

    def test_push_set_upstream(self, fake_git_repo, mocker):
        run_git = mocker.patch(
            "subprocess.run",
            side_effect=[
                _make_run_result(0, "main\n", ""),  # get_current_branch
                _make_run_result(0, "To origin\n", ""),  # push
            ],
        )
        repo = Repository(fake_git_repo)

        ok, _ = repo.push(set_upstream=True)

        assert ok is True
        # The second ``subprocess.run`` call is the actual push.
        assert run_git.call_args_list[1].args[0][3:] == ["push", "-u", "origin", "main"]

    def test_push_set_upstream_without_branch(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(128, "", "fatal: not a git repo\n"),
        )
        repo = Repository(fake_git_repo)

        ok, msg = repo.push(set_upstream=True)

        assert ok is False
        assert "branch" in msg.lower()


class TestTimelineOutput:
    def test_success(self, fake_git_repo, mocker):
        run_git = mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(
                0,
                "* abc1234 2026-04-20  (HEAD -> main) Add timeline view\n",
                "",
            ),
        )
        repo = Repository(fake_git_repo)

        ok, output = repo.timeline_output()

        assert ok is True
        assert "* abc1234" in output
        assert "Add timeline view" in output
        assert "--max-count=1000" in run_git.call_args.args[0]

    def test_no_commits_returns_empty_message(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(
                128,
                "",
                "fatal: your current branch 'main' does not have any commits yet\n",
            ),
        )
        repo = Repository(fake_git_repo)

        ok, output = repo.timeline_output()

        assert ok is True
        assert output == "No commits yet."


class TestBranchesOutput:
    def test_success(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(0, "* main\n  remotes/origin/main\n", ""),
        )
        repo = Repository(fake_git_repo)

        ok, output = repo.branches_output()

        assert ok is True
        assert "* main" in output
        assert "remotes/origin/main" in output

    def test_empty_branch_list_returns_fallback(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(0, "", ""),
        )
        repo = Repository(fake_git_repo)

        ok, output = repo.branches_output()

        assert ok is True
        assert output == "No branches found."


class TestRemotesOutput:
    def test_success(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(
                0,
                "origin\thttps://example.com/repo.git (fetch)\n"
                "origin\thttps://example.com/repo.git (push)\n",
                "",
            ),
        )
        repo = Repository(fake_git_repo)

        ok, output = repo.remotes_output()

        assert ok is True
        assert "origin" in output
        assert "(fetch)" in output

    def test_empty_remote_list_returns_fallback(self, fake_git_repo, mocker):
        mocker.patch(
            "subprocess.run",
            return_value=_make_run_result(0, "", ""),
        )
        repo = Repository(fake_git_repo)

        ok, output = repo.remotes_output()

        assert ok is True
        assert output == "No remotes configured."

    def test_pull_success(self, fake_git_repo, mocker):
        calls = _setup_pull_mocks(mocker)
        repo = Repository(fake_git_repo)
        ok, msg = repo.pull()
        assert ok is True
        assert "Already up to date" in msg
        assert calls[-1] == ["pull", "--ff-only", "origin", "main"]

    def test_failure(self, fake_git_repo, mocker):
        _setup_pull_mocks(
            mocker,
            pull_results=((1, "", "fatal: Not possible to fast-forward\n"),),
        )
        repo = Repository(fake_git_repo)
        ok, msg = repo.pull()
        assert ok is False
        assert "fast-forward" in msg

    def test_retry_on_network_error(self, fake_git_repo, mocker):
        calls = _setup_pull_mocks(
            mocker,
            pull_results=(
                (1, "", "network error \u2014 could not reach remote"),
                (0, "Updated.\n", ""),
            ),
        )
        repo = Repository(fake_git_repo)
        ok, msg = repo.pull()
        assert ok is True
        assert sum(1 for call in calls if call and call[0] == "pull") == 2

    def test_no_retry_on_non_network_error(self, fake_git_repo, mocker):
        _setup_pull_mocks(
            mocker,
            pull_results=((1, "", "fatal: some error"),),
        )
        repo = Repository(fake_git_repo)
        ok, msg = repo.pull()
        assert ok is False

    def test_retry_exhausted(self, fake_git_repo, mocker):
        _setup_pull_mocks(
            mocker,
            pull_results=(
                (1, "", "network error \u2014 could not reach remote"),
                (1, "", "network error \u2014 could not reach remote"),
                (1, "", "network error \u2014 could not reach remote"),
            ),
        )
        repo = Repository(fake_git_repo)
        ok, msg = repo.pull(retries=2)
        assert ok is False
        assert "network error" in msg

    def test_negative_retries_still_attempt_once(self, fake_git_repo, mocker):
        _setup_pull_mocks(
            mocker,
            pull_results=((1, "", "fatal: some error"),),
        )
        repo = Repository(fake_git_repo)

        ok, msg = repo.pull(retries=-1)

        assert ok is False
        assert "fatal" in msg

    def test_detached_head(self, fake_git_repo, mocker):
        _setup_pull_mocks(mocker, branch_result=(0, "HEAD\n", ""), pull_results=())

        repo = Repository(fake_git_repo)
        ok, msg = repo.pull()

        assert ok is False
        assert msg == "Cannot pull in detached HEAD"


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


# ---------------------------------------------------------------------------
# get_status() with a remote fetch failure — classification contract
# ---------------------------------------------------------------------------
#
# When the user runs `list` (which sets fetch=True) the repo's status goes
# through `_get_origin_sync_status` after `_fetch_origin_branch`. If the
# fetch fails, the message surfaced to the UI must be the *classified*
# string (so the TUI can colour it sensibly and the CLI can show a useful
# summary). Without classification, raw `fatal: ...` from git leaks into
# the message and breaks downstream parsing/colouring.
# ---------------------------------------------------------------------------


class TestGetStatusFetchErrorClassification:
    def test_fetch_auth_error_yields_classified_message(self, tmp_path, monkeypatch):
        from gitdirector import repo as repo_mod

        # Build a real local repo so the non-fetch code path is exercised.
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _run_git(repo_dir, "init")
        _run_git(repo_dir, "config", "user.email", "t@t")
        _run_git(repo_dir, "config", "user.name", "t")
        (repo_dir / "README.md").write_text("hi")
        _run_git(repo_dir, "add", "-A")
        _run_git(repo_dir, "commit", "-m", "init")

        # Simulate real subprocess output by patching the lower-level call
        # so the production normalization/classification path runs. We patch
        # `subprocess.Popen` (the constructor) so the existing
        # `_run_git`/`_normalize_git_result` pipeline classifies the error.
        def fake_popen(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # The real _run_git uses text=True, so communicate() returns str.
            if "status" in cmd:
                rc, out, err = 0, "# branch.head main\n", ""
            elif "fetch" in cmd:
                rc, out, err = (
                    1,
                    "",
                    "fatal: Authentication failed for 'https://example.com/r.git'",
                )
            elif "show-ref" in cmd:
                rc, out, err = 1, "", "unknown ref"
            elif "rev-list" in cmd:
                rc, out, err = 0, "0\t0", ""
            elif "log" in cmd:
                rc, out, err = 0, "2 hours ago\n1234", ""
            elif "ls-tree" in cmd:
                rc, out, err = 0, "", ""
            else:
                raise AssertionError(f"Unexpected git command: {cmd}")

            proc = MagicMock()
            # text=True means communicate() returns strings, not bytes.
            proc.communicate.return_value = (out, err)
            proc.returncode = rc
            return proc

        monkeypatch.setattr(repo_mod.subprocess, "Popen", fake_popen)

        info = Repository(repo_dir).get_status(fetch=True)
        assert info.status == repo_mod.RepoStatus.UNKNOWN
        msg = info.message.lower()
        # The classified message must be present, NOT the raw git stderr.
        assert "authentication" in msg or "network" in msg
        assert "fatal:" not in info.message


# ---------------------------------------------------------------------------
# get_diff_against_head / read_file_text — diff view support
# ---------------------------------------------------------------------------


class TestGetDiffAgainstHead:
    def test_success_returns_diff_and_untracked(self, fake_git_repo, mocker):
        diff_text = "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        responses = {
            ("diff", "HEAD", "--no-color"): (0, diff_text, ""),
            ("ls-files", "--others", "--exclude-standard", "-z"): (
                0,
                "untracked.py\x00",
                "",
            ),
        }

        def fake_run_git(self, *args, **_kwargs):
            key = tuple(args)
            if key in responses:
                rc, out, err = responses[key]
                return rc, out, err
            return 1, "", f"unexpected call: {args}"

        mocker.patch.object(repo_mod.Repository, "_run_git", fake_run_git)
        repo = Repository(fake_git_repo)
        ok, text, untracked = repo.get_diff_against_head()
        assert ok is True
        assert "+new" in text
        assert untracked == ["untracked.py"]

    def test_failure_returns_error_and_empty_untracked(self, fake_git_repo, mocker):
        calls = []

        def fake_run_git(self, *args, **_kwargs):
            calls.append(args)
            return 128, "", "fatal: bad revision"

        mocker.patch.object(repo_mod.Repository, "_run_git", fake_run_git)
        repo = Repository(fake_git_repo)
        ok, text, untracked = repo.get_diff_against_head()
        assert ok is False
        assert "bad revision" in text
        assert untracked == []
        assert calls == [("diff", "HEAD", "--no-color")]

    def test_no_commits_retries_against_empty_tree(self, fake_git_repo, mocker):
        empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        calls = []
        responses = {
            ("diff", "HEAD", "--no-color"): (
                128,
                "",
                "fatal: ambiguous argument 'HEAD': unknown revision or path not in the working tree.",
            ),
            ("diff", empty_tree, "--no-color"): (0, "diff --git a/a.py b/a.py\n+new\n", ""),
            ("ls-files", "--others", "--exclude-standard", "-z"): (0, "untracked.py\x00", ""),
        }

        def fake_run_git(self, *args, **_kwargs):
            calls.append(args)
            return responses.get(tuple(args), (1, "", "unexpected"))

        mocker.patch.object(repo_mod.Repository, "_run_git", fake_run_git)
        ok, text, untracked = Repository(fake_git_repo).get_diff_against_head()

        assert ok is True
        assert "+new" in text
        assert untracked == ["untracked.py"]
        assert calls == [
            ("diff", "HEAD", "--no-color"),
            ("diff", empty_tree, "--no-color"),
            ("ls-files", "--others", "--exclude-standard", "-z"),
        ]

    def test_unborn_head_includes_staged_and_modified_files(self, tmp_path):
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _run_git(repo_dir, "init")

        staged = repo_dir / "staged.txt"
        staged.write_text("staged only\n")
        _run_git(repo_dir, "add", "staged.txt")

        modified = repo_dir / "modified.txt"
        modified.write_text("staged version\n")
        _run_git(repo_dir, "add", "modified.txt")
        modified.write_text("working tree version\n")

        (repo_dir / "untracked.txt").write_text("untracked\n")

        ok, text, untracked = Repository(repo_dir).get_diff_against_head()

        assert ok is True
        assert "diff --git a/staged.txt b/staged.txt" in text
        assert "+staged only" in text
        assert "diff --git a/modified.txt b/modified.txt" in text
        assert "+working tree version" in text
        assert "untracked.txt" not in text
        assert untracked == ["untracked.txt"]

    def test_truncates_diff_above_max_bytes(self, fake_git_repo, mocker):
        huge = "x" * (3 * 1024 * 1024)
        responses = {
            ("diff", "HEAD", "--no-color"): (0, huge, ""),
            ("ls-files", "--others", "--exclude-standard", "-z"): (0, "", ""),
        }

        def fake_run_git(self, *args, **_kwargs):
            return responses.get(tuple(args), (1, "", "unexpected"))

        mocker.patch.object(repo_mod.Repository, "_run_git", fake_run_git)
        repo = Repository(fake_git_repo)
        ok, text, _ = repo.get_diff_against_head(max_bytes=1024)
        assert ok is True
        assert len(text) < len(huge)
        assert "gd-truncated" in text

    def test_empty_diff_returns_empty_string(self, fake_git_repo, mocker):
        responses = {
            ("diff", "HEAD", "--no-color"): (0, "", ""),
            ("ls-files", "--others", "--exclude-standard", "-z"): (0, "", ""),
        }

        def fake_run_git(self, *args, **_kwargs):
            return responses.get(tuple(args), (1, "", "unexpected"))

        mocker.patch.object(repo_mod.Repository, "_run_git", fake_run_git)
        repo = Repository(fake_git_repo)
        ok, text, _ = repo.get_diff_against_head()
        assert ok is True
        assert text == ""


class TestListUntrackedFiles:
    def test_parses_null_separated_paths(self, fake_git_repo, mocker):
        def fake_run_git(self, *args, **_kwargs):
            if args[:1] == ("ls-files",):
                return 0, "a.py\x00b.py\x00c.py\x00", ""
            return 1, "", "unexpected"

        mocker.patch.object(repo_mod.Repository, "_run_git", fake_run_git)
        repo = Repository(fake_git_repo)
        assert repo._list_untracked_files() == ["a.py", "b.py", "c.py"]

    def test_returns_empty_on_empty_output(self, fake_git_repo, mocker):
        def fake_run_git(self, *args, **_kwargs):
            return 0, "", ""

        mocker.patch.object(repo_mod.Repository, "_run_git", fake_run_git)
        repo = Repository(fake_git_repo)
        assert repo._list_untracked_files() == []


class TestReadFileText:
    def test_reads_text_file(self, fake_git_repo):
        (fake_git_repo / "hello.txt").write_text("hello world\n")
        repo = Repository(fake_git_repo)
        text = repo.read_file_text("hello.txt")
        assert text == "hello world\n"

    def test_returns_none_for_missing_file(self, fake_git_repo):
        repo = Repository(fake_git_repo)
        assert repo.read_file_text("does_not_exist.txt") is None

    def test_returns_none_for_binary_file(self, fake_git_repo):
        (fake_git_repo / "blob.bin").write_bytes(b"\x00\x01\x02\x03")
        repo = Repository(fake_git_repo)
        assert repo.read_file_text("blob.bin") is None

    def test_truncates_large_file(self, fake_git_repo):
        big = "x" * 1024
        (fake_git_repo / "big.txt").write_text(big)
        repo = Repository(fake_git_repo)
        text = repo.read_file_text("big.txt", max_bytes=100)
        assert text is not None
        assert "gd-truncated" in text
        assert len(text) < 200

    def test_handles_unicode(self, fake_git_repo):
        (fake_git_repo / "u.txt").write_text("héllo wörld 🚀\n", encoding="utf-8")
        repo = Repository(fake_git_repo)
        assert repo.read_file_text("u.txt") == "héllo wörld 🚀\n"
