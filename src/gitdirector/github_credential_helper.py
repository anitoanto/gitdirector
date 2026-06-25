import os
import sys


def _read_credential_request() -> dict[str, str]:
    request: dict[str, str] = {}
    for line in sys.stdin:
        line = line.rstrip("\n")
        if not line:
            break
        key, separator, value = line.partition("=")
        if separator:
            request[key] = value
    return request


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "get":
        return 0

    request = _read_credential_request()
    if request.get("protocol") != "https" or request.get("host", "").lower() != "github.com":
        return 0

    username = os.environ.get("GITDIRECTOR_GITHUB_USERNAME", "").strip()
    token = os.environ.get("GITDIRECTOR_GITHUB_PAT", "").strip()
    if not username or not token:
        return 0

    print(f"username={username}")
    print(f"password={token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
