from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import click
from rich.table import Table
from rich.text import Text

from .. import version_check
from ..agents import agent_tools
from ..config import Config
from ..storage import load_yaml_mapping
from . import ATTENTION, DANGER, MUTED, SUCCESS, console, count_noun, display_path, emit

OK, WARN, FAIL = "ok", "warn", "fail"

# Oldest tmux sessions and panels are tested against.
# kill-session -g, which the session crash guard relies on, arrived in 3.7.
_MIN_TMUX = (3, 7, "")


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    summary: str
    details: tuple[str, ...] = ()
    fix: str | None = None
    #: Only a failed critical check makes ``doctor`` exit non-zero.
    critical: bool = False


def _which(*names: str) -> str | None:
    for name in names:
        resolved = shutil.which(name)
        if resolved is not None:
            return resolved
    return None


def _current_shell_name() -> str | None:
    shell = os.environ.get("SHELL", "").strip()
    if not shell:
        return None
    name = Path(shell).name.lower()
    return name if name in {"bash", "zsh", "fish"} else None


def _zdotdir(home: Path) -> Path:
    return Path(os.environ.get("ZDOTDIR") or home).expanduser()


def _completion_installed(shell_name: str, home: Path) -> tuple[bool, str]:
    if shell_name == "zsh":
        paths = [
            home / ".zsh/completions/_gitdirector",
            home / ".zfunc/_gitdirector",
        ]
        rc_files = [_zdotdir(home) / ".zshrc"]
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
        return False, "current shell is not bash, zsh, or fish"

    for path in paths:
        if path.exists():
            return True, f"installed in {display_path(path)}"

    markers = ("gitdirector completion", "_GITDIRECTOR_COMPLETE", "_gitdirector")
    for rc_file in rc_files:
        try:
            content = rc_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(marker in content for marker in markers):
            return True, f"set up in {display_path(rc_file)}"

    return False, f"not set up for {shell_name}"


def _config_writable(config: Config) -> tuple[bool, str]:
    try:
        config.config_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            dir=str(config.config_dir), prefix="doctor-", suffix=".tmp"
        )
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
        return False, tuple(f"Corrupted: {entry}" for entry in corrupted_files)
    return True, ()


def _tool_version(executable: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, "-V"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _parse_tmux_version(text: str) -> tuple[int, int, str] | None:
    match = re.search(r"(\d+)\.(\d+)([a-z]?)", text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), match.group(3)


def _gitdirector_check() -> DoctorCheck:
    status = version_check.get_update_status()
    if status is None:
        return DoctorCheck("GitDirector", OK, "version check unavailable")
    if status.update_available and status.latest_version is not None:
        return DoctorCheck(
            "GitDirector",
            WARN,
            f"{status.current_version}, {status.latest_version} is available",
            fix="pip install -U gitdirector (or your installer's upgrade command)",
        )
    return DoctorCheck("GitDirector", OK, f"{status.current_version}, up to date")


def _git_check() -> DoctorCheck:
    git = _which("git")
    if git is None:
        return DoctorCheck("git", FAIL, "not installed", fix="Install git.", critical=True)
    return DoctorCheck("git", OK, git, critical=True)


def _tmux_check() -> DoctorCheck:
    tmux = _which("tmux")
    if tmux is None:
        return DoctorCheck(
            "tmux",
            FAIL,
            "not installed; sessions, panels, and the gd-* commands need it",
            fix="Install tmux 3.7 or newer.",
            critical=True,
        )
    version_text = _tool_version(tmux)
    version = _parse_tmux_version(version_text or "")
    if version is not None and version < _MIN_TMUX:
        return DoctorCheck(
            "tmux",
            FAIL,
            f"{version_text} is too old ({tmux})",
            fix="Install tmux 3.7 or newer.",
            critical=True,
        )
    label = version_text.removeprefix("tmux ") if version_text else "installed"
    return DoctorCheck("tmux", OK, f"{label} ({tmux})", critical=True)


def _config_check() -> DoctorCheck:
    try:
        config = Config()
    except (OSError, RuntimeError, ValueError) as exc:
        return DoctorCheck(
            "Config",
            FAIL,
            "could not be loaded",
            (str(exc),),
            "Repair or remove the file named above.",
            critical=True,
        )
    location = display_path(config.config_dir)
    writable, detail = _config_writable(config)
    if not writable:
        return DoctorCheck(
            "Config",
            FAIL,
            f"{location} is not writable",
            (detail,),
            f"Fix the permissions of {location}.",
            critical=True,
        )
    valid, details = _validate_gitdirector_state(config)
    if not valid:
        return DoctorCheck(
            "Config",
            FAIL,
            f"corrupted files in {location}",
            details,
            "Repair or remove the corrupted files.",
            critical=True,
        )
    return DoctorCheck(
        "Config",
        OK,
        f"{location}, {count_noun(len(config.repositories), 'repository', 'repositories')}",
        critical=True,
    )


_COMPLETION_SETUP = {
    "bash": ('eval "$(gitdirector completion bash)"', "~/.bashrc"),
    "zsh": ('eval "$(gitdirector completion zsh)"', "~/.zshrc"),
    "fish": ("gitdirector completion fish | source", "~/.config/fish/config.fish"),
}


def _completion_check() -> DoctorCheck:
    shell_name = _current_shell_name()
    if shell_name is None:
        return DoctorCheck("Completion", WARN, "$SHELL is not bash, zsh, or fish")
    installed, detail = _completion_installed(shell_name, Path.home())
    if installed:
        return DoctorCheck("Completion", OK, f"{shell_name}, {detail}")
    line, rc_file = _COMPLETION_SETUP[shell_name]
    if shell_name == "zsh" and os.environ.get("ZDOTDIR"):
        rc_file = display_path(_zdotdir(Path.home()) / ".zshrc")
    return DoctorCheck(
        "Completion", WARN, f"not set up for {shell_name}", fix=f"Add to {rc_file}: {line}"
    )


def _agents_check() -> DoctorCheck:
    found = [(label, _which(*names)) for label, names in agent_tools()]
    installed = sum(path is not None for _, path in found)
    details = tuple(f"{label}: {path or 'not installed'}" for label, path in found)
    if not installed:
        return DoctorCheck(
            "Agent CLIs", WARN, "none installed", details, "Install the agents you use."
        )
    return DoctorCheck("Agent CLIs", OK, f"{installed} of {len(found)} installed", details)


def run_doctor_checks() -> list[DoctorCheck]:
    return [
        _gitdirector_check(),
        _git_check(),
        _tmux_check(),
        _config_check(),
        _completion_check(),
        _agents_check(),
    ]


_MARKS = {OK: ("✓", SUCCESS), WARN: ("!", ATTENTION), FAIL: ("✗", DANGER)}


def _detail_text(detail: str) -> Text:
    label, sep, value = detail.partition(": ")
    if sep and value == "not installed":
        return Text.assemble((f"{label}: ", MUTED), (value, ATTENTION))
    return Text(detail, style=MUTED)


def _print_checks(checks: list[DoctorCheck]) -> None:
    table = Table.grid(padding=(0, 2, 0, 0))
    table.add_column(no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(overflow="fold")
    for check in checks:
        mark, style = _MARKS[check.status]
        table.add_row(Text(mark, style=style), Text(check.name, style="bold"), check.summary)
        for detail in check.details:
            table.add_row("", "", _detail_text(detail))
        if check.fix:
            table.add_row("", "", Text.assemble(("fix: ", style), check.fix))
    emit(table)


def register(cli: click.Group):
    @cli.command()
    def doctor():
        """Check git, tmux, config, completion, and agents

        Exits 1 when a critical check (git, tmux, config) fails.
        """
        checks = run_doctor_checks()
        _print_checks(checks)

        warnings = sum(check.status == WARN for check in checks)
        failures = sum(check.status == FAIL for check in checks)
        console.print()
        if not warnings and not failures:
            console.print(Text("No issues found.", style=SUCCESS))
            return
        parts = []
        if failures:
            parts.append(Text(count_noun(failures, "check") + " failed", style=DANGER))
        if warnings:
            parts.append(Text(count_noun(warnings, "warning"), style=ATTENTION))
        console.print(Text(" · ", style=MUTED).join(parts))
        if any(check.status == FAIL and check.critical for check in checks):
            raise SystemExit(1)
