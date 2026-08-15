from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import click

from .. import version_check
from ..config import Config
from ..storage import load_yaml_mapping
from . import console

_CLIPBOARD_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("pbcopy",),
    ("wl-copy",),
    ("xclip", "-selection", "clipboard"),
    ("xsel", "--clipboard", "--input"),
    ("clip.exe",),
)

_AGENT_TOOLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("OpenCode", ("opencode",)),
    ("Claude Code", ("claude", "claude-code")),
    ("GitHub Copilot", ("copilot", "github-copilot-cli")),
    ("Codex", ("codex",)),
    ("Pi", ("pi",)),
)


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    classification: str
    summary: str
    details: tuple[str, ...] = ()
    fix: str | None = None


def _which(*names: str) -> str | None:
    for name in names:
        resolved = shutil.which(name)
        if resolved is not None:
            return resolved
    return None


def _clipboard_tools_available() -> list[str]:
    available: list[str] = []
    for command in _CLIPBOARD_COMMANDS:
        resolved = _which(command[0])
        if resolved is None:
            continue
        available.append(resolved)
    return available


def _current_shell_name() -> str | None:
    shell = os.environ.get("SHELL", "").strip()
    if not shell:
        return None
    name = Path(shell).name.lower()
    return name if name in {"bash", "zsh", "fish"} else None


def _completion_installed(shell_name: str, home: Path) -> tuple[bool, str, tuple[str, ...]]:
    if shell_name == "zsh":
        paths = [
            home / ".zsh/completions/_gitdirector",
            home / ".zfunc/_gitdirector",
        ]
        rc_files = [home / ".zshrc"]
    elif shell_name == "bash":
        paths = [
            home / ".local/share/bash-completion/completions/gitdirector",
            home / ".bash_completion.d/gitdirector",
        ]
        rc_files = [home / ".bashrc", home / ".bash_profile", home / ".profile"]
    elif shell_name == "fish":
        paths = [home / ".config/fish/completions/gitdirector.fish"]
        rc_files = [home / ".config/fish/config.fish"]
    else:
        return False, "current shell is not bash, zsh, or fish", ()

    expected_paths = tuple(str(path) for path in paths)

    for path in paths:
        if path.exists():
            return True, f"detected {path}", expected_paths

    markers = ("gitdirector completion", "_GITDIRECTOR_COMPLETE", "_gitdirector")
    for rc_file in rc_files:
        try:
            content = rc_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(marker in content for marker in markers):
            return True, f"detected setup in {rc_file}", expected_paths

    return False, f"not detected for {shell_name}", expected_paths


def _config_writable(config: Config) -> tuple[bool, str]:
    config.config_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=str(config.config_dir), prefix="doctor-", suffix=".tmp")
    try:
        os.close(fd)
        Path(temp_path).unlink(missing_ok=True)
    except OSError as exc:
        return False, f"{config.config_dir}: {exc}"
    return True, str(config.config_dir)


def _validate_panels_file(_path: Path) -> None:
    from .tui.panels import PanelStore

    PanelStore()


def _validate_version_check_cache(path: Path) -> None:
    from ..version_check import _parse_checked_at

    data = load_yaml_mapping(path, description="GitDirector version cache")
    checked_at = data.get("checked_at")
    if checked_at is not None:
        if not isinstance(checked_at, str) or _parse_checked_at(checked_at) is None:
            raise ValueError(
                "Invalid GitDirector version cache: 'checked_at' must be an ISO-8601 string"
            )

    latest_version = data.get("latest_version")
    if latest_version is not None and (
        not isinstance(latest_version, str) or not latest_version.strip()
    ):
        raise ValueError(
            "Invalid GitDirector version cache: 'latest_version' must be a non-empty string"
        )


def _validate_repos_cache(path: Path) -> None:
    data = load_yaml_mapping(path, description="repository cache")
    updated_at = data.get("updated_at")
    if updated_at is not None and (
        isinstance(updated_at, bool) or not isinstance(updated_at, (int, float))
    ):
        raise ValueError("Invalid repository cache: 'updated_at' must be a number")

    repositories = data.get("repositories")
    if repositories is not None and not isinstance(repositories, list):
        raise ValueError("Invalid repository cache: 'repositories' must be a list")

    config_token = data.get("config_token")
    if config_token is not None and not isinstance(config_token, dict):
        raise ValueError("Invalid repository cache: 'config_token' must be a mapping")


def _validate_gitdirector_state(config: Config) -> tuple[bool, tuple[str, ...]]:
    config_dir = config.config_dir
    validated_files = 0
    corrupted_files: list[str] = []
    checked_paths: set[Path] = set()

    for path in (config.config_file, config.secrets_file):
        if path.exists():
            checked_paths.add(path)
            validated_files += 1

    validators = {
        config_dir / "panels.yaml": _validate_panels_file,
        config_dir / "version_check.yaml": _validate_version_check_cache,
        config_dir / "cache" / "repos.yaml": _validate_repos_cache,
    }

    for path, validator in validators.items():
        if not path.exists():
            continue
        checked_paths.add(path)
        validated_files += 1
        try:
            validator(path)
        except (OSError, RuntimeError, ValueError) as exc:
            corrupted_files.append(f"{path.relative_to(config_dir)}: {exc}")

    for path in sorted(config_dir.rglob("*.yaml")):
        if path in checked_paths:
            continue
        validated_files += 1
        try:
            load_yaml_mapping(
                path, description=f"GitDirector state file {path.relative_to(config_dir)}"
            )
        except ValueError as exc:
            corrupted_files.append(f"{path.relative_to(config_dir)}: {exc}")

    if corrupted_files:
        return False, tuple(
            ["~/.gitdirector folder corrupted"]
            + [f"Corrupted: {entry}" for entry in corrupted_files]
        )

    return True, ("~/.gitdirector folder valid",)


def _gitdirector_version_check() -> DoctorCheck:
    status = version_check.get_update_status()
    if status is None:
        return DoctorCheck(
            "GitDirector",
            "ok",
            "optional",
            "",
            ("Version check unavailable",),
        )

    if status.update_available and status.latest_version is not None:
        return DoctorCheck(
            f"GitDirector [{status.current_version}]",
            "warn",
            "optional",
            "",
            (f"GitDirector {status.latest_version} is available",),
            "Update GitDirector.",
        )

    return DoctorCheck(
        f"GitDirector [{status.current_version}]",
        "ok",
        "optional",
        "",
        ("Up to date",),
    )


def run_doctor_checks() -> list[DoctorCheck]:
    checks: list[DoctorCheck] = [_gitdirector_version_check()]

    tmux_path = _which("tmux")
    if tmux_path is None:
        checks.append(
            DoctorCheck(
                "Tmux",
                "fail",
                "critical",
                "tmux is not installed",
                (
                    "Needed for `gitdirector console`, `gitdirector cd`, `gitdirector gd-tmux`, `gitdirector gd-capture`, and `gitdirector gd-send`.",
                    "Without tmux, the multi-session and agent-session workflow is unavailable.",
                ),
                "Install tmux.",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "Tmux",
                "ok",
                "critical",
                "tmux is installed",
                (f"Resolved executable: {tmux_path}",),
            )
        )

    clipboard_tools = _clipboard_tools_available()
    if clipboard_tools:
        checks.append(
            DoctorCheck(
                "Clipboard Integration",
                "ok",
                "optional",
                "supported clipboard integration is available",
                (f"Detected tools: {', '.join(clipboard_tools)}",),
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "Clipboard Integration",
                "warn",
                "optional",
                "no supported clipboard tool was found",
                ("Looked for: pbcopy, wl-copy, xclip, xsel, clip.exe.",),
                "Install a clipboard tool.",
            )
        )

    try:
        config = Config()
    except (OSError, RuntimeError, ValueError) as exc:
        checks.append(
            DoctorCheck(
                "Config",
                "fail",
                "critical",
                "GitDirector config could not be loaded",
                (str(exc),),
                "Repair `~/.gitdirector/`.",
            )
        )
    else:
        writable, detail = _config_writable(config)
        if writable:
            state_valid, state_details = _validate_gitdirector_state(config)
            checks.append(
                DoctorCheck(
                    "Config",
                    "ok" if state_valid else "fail",
                    "critical",
                    "GitDirector state files are valid"
                    if state_valid
                    else "corrupted GitDirector state files were found",
                    state_details,
                    None if state_valid else "Repair or remove corrupted files.",
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    "Config",
                    "fail",
                    "critical",
                    "GitDirector config directory is not writable",
                    (detail,),
                    "Fix permissions for `~/.gitdirector/`.",
                )
            )

    shell_name = _current_shell_name()
    if shell_name is None:
        checks.append(
            DoctorCheck(
                "Shell Completion",
                "warn",
                "optional",
                "shell completion could not be checked",
                ("`SHELL` is unset or not one of: bash, zsh, fish.",),
                "Run `gitdirector completion <shell>`.",
            )
        )
    else:
        installed, detail, expected_paths = _completion_installed(shell_name, Path.home())
        if installed:
            checks.append(
                DoctorCheck(
                    "Shell Completion",
                    "ok",
                    "optional",
                    f"shell completion is installed for {shell_name}",
                    (detail,),
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    "Shell Completion",
                    "warn",
                    "optional",
                    f"shell completion was not detected for {shell_name}",
                    (
                        f"Looked for setup in shell rc files and completion paths: {', '.join(expected_paths)}",
                    ),
                    f"Run `gitdirector completion {shell_name}`.",
                )
            )

    available_agents: list[tuple[str, str]] = []
    missing_agents: list[str] = []
    for label, names in _AGENT_TOOLS:
        resolved = _which(*names)
        if resolved is None:
            missing_agents.append(label)
        else:
            available_agents.append((label, resolved))
    if available_agents:
        details = tuple(f"{label}: {resolved}" for label, resolved in available_agents)
        if missing_agents:
            details += tuple(f"{label}: not installed" for label in missing_agents)
        checks.append(
            DoctorCheck(
                "Agent Tools",
                "warn" if missing_agents else "ok",
                "optional",
                "one or more agent CLIs are installed",
                details,
                None if not missing_agents else "Install desired agent CLIs.",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "Agent Tools",
                "warn",
                "optional",
                "no supported agent CLI was found",
                tuple(f"{label}: not installed" for label, _names in _AGENT_TOOLS),
                "Install desired agent CLIs.",
            )
        )

    return checks


def _status_label(status: str) -> str:
    return {
        "ok": "[green][✓][/green]",
        "warn": "[yellow][!][/yellow]",
        "fail": "[red][x][/red]",
    }[status]


def _print_check(check: DoctorCheck) -> None:
    # A few checks carry their state entirely in the name (the version
    # check renders as "GitDirector [1.2.3]"), so the summary is optional.
    summary = check.summary.strip()
    heading = f"{_status_label(check.status)} [white]{check.name}[/white]"
    if summary:
        heading = f"{heading} [dim]— {summary}[/dim]"
    console.print(heading)
    for detail in check.details:
        if detail.endswith(": not installed"):
            console.print(f"  [yellow][!][/yellow] [dim]{detail}[/dim]")
        elif check.name == "Agent Tools":
            console.print(f"  [green][✓][/green] [dim]{detail}[/dim]")
        else:
            console.print(f"  [green]•[/green] [dim]{detail}[/dim]")
    if check.fix is not None:
        console.print(f"  [yellow]•[/yellow] [bold]Fix:[/bold] {check.fix}")


def register(cli: click.Group):
    @cli.command()
    def doctor():
        """Check GitDirector environment setup and common integrations."""
        checks = run_doctor_checks()
        for check in checks:
            _print_check(check)

        warn_count = sum(check.status == "warn" for check in checks)
        failures = [check for check in checks if check.status == "fail"]
        critical_fail_count = sum(check.classification == "critical" for check in failures)
        optional_fail_count = len(failures) - critical_fail_count

        def _failure_phrase(count: int, label: str) -> str:
            noun = "check failed" if count == 1 else "checks failed"
            return f"{count} {label} {noun}"

        lines: list[str] = []
        if warn_count:
            noun = "check needs" if warn_count == 1 else "checks need"
            lines.append(f" [yellow][!][/yellow] {warn_count} optional {noun} attention")
        # Reported separately: only a critical failure sets the exit code,
        # so calling an optional failure "critical" would misrepresent it.
        if critical_fail_count:
            lines.append(f" [red][x][/red] {_failure_phrase(critical_fail_count, 'critical')}")
        if optional_fail_count:
            lines.append(f" [red][x][/red] {_failure_phrase(optional_fail_count, 'optional')}")

        console.print()
        if not lines:
            console.print(" [green][✓][/green] No issues found!\n")
        else:
            for line in lines:
                console.print(line)
            console.print()
        if critical_fail_count:
            raise SystemExit(1)
