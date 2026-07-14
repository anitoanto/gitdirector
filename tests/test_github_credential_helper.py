import os
import subprocess
import sys


def test_helper_returns_github_credentials():
    env = os.environ.copy()
    env["GITDIRECTOR_GITHUB_USERNAME"] = "octocat"
    env["GITDIRECTOR_GITHUB_PAT"] = "ghp_secret"

    result = subprocess.run(
        [sys.executable, "-m", "gitdirector.github_credential_helper", "get"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout == "username=octocat\npassword=ghp_secret\n"


def test_helper_ignores_non_github_hosts():
    env = os.environ.copy()
    env["GITDIRECTOR_GITHUB_USERNAME"] = "octocat"
    env["GITDIRECTOR_GITHUB_PAT"] = "ghp_secret"

    result = subprocess.run(
        [sys.executable, "-m", "gitdirector.github_credential_helper", "get"],
        input="protocol=https\nhost=example.com\n\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout == ""
