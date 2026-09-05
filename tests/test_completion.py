"""Regression tests for tab completion of repo names."""

from __future__ import annotations

from pathlib import Path

import yaml

from gitdirector.commands.completion import complete_repository_names, complete_session_names


def test_complete_repository_names_offers_paths_when_basenames_collide(config_dir, monkeypatch):
    """Two repos sharing a basename complete to their paths, not the name.

    Emitting the bare name twice is noisy and confusing, but emitting it
    once is worse: a shared basename is exactly the input every command
    rejects with "use the full path". So the completion offers the paths,
    which are distinct and actually accepted.
    """

    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        yaml.dump(
            {
                "repositories": [
                    str(config_dir.parent / "work" / "same-name"),
                    str(config_dir.parent / "projects" / "same-name"),
                ]
            }
        )
    )
    monkeypatch.setattr(Path, "home", lambda: config_dir.parent)

    items = complete_repository_names(None, None, "")
    values = [item.value for item in items]
    assert len(values) == len(set(values)), f"duplicate completion entries: {values}"
    assert "same-name" not in values, (
        f"bare ambiguous name offered; every command would reject it: {values}"
    )
    assert sorted(values) == sorted(
        [
            str(config_dir.parent / "projects" / "same-name"),
            str(config_dir.parent / "work" / "same-name"),
        ]
    ), values


def test_complete_repository_names_sorts_by_name_case_insensitive(config_dir, monkeypatch):
    """Completion results must be sorted case-insensitively so a user
    typing ``my-`` can see ``My-Repo`` and ``my-app`` interleaved
    sensibly.
    """

    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        yaml.dump(
            {
                "repositories": [
                    str(config_dir.parent / "zeta"),
                    str(config_dir.parent / "Alpha"),
                    str(config_dir.parent / "beta"),
                ]
            }
        )
    )
    monkeypatch.setattr(Path, "home", lambda: config_dir.parent)

    items = complete_repository_names(None, None, "")
    values = [item.value for item in items]
    assert values == ["Alpha", "beta", "zeta"], values


def test_complete_repository_names_filters_by_incomplete_prefix(config_dir, monkeypatch):
    """The ``incomplete`` argument narrows completion results to those
    whose name starts with that prefix (case-insensitive).
    """

    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        yaml.dump(
            {
                "repositories": [
                    str(config_dir.parent / "frontend-app"),
                    str(config_dir.parent / "backend-app"),
                    str(config_dir.parent / "docs"),
                ]
            }
        )
    )
    monkeypatch.setattr(Path, "home", lambda: config_dir.parent)

    items = complete_repository_names(None, None, "fro")
    assert [item.value for item in items] == ["frontend-app"]


def test_complete_session_names_filters_live_sessions(monkeypatch):
    monkeypatch.setattr(
        "gitdirector.commands.completion.list_all_gd_sessions",
        lambda: [
            {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
            {"session_name": "gd/beta/claude/2", "repo": "beta", "purpose": "claude"},
        ],
    )
    items = complete_session_names(None, None, "gd/b")
    assert [item.value for item in items] == ["gd/beta/claude/2"]
    assert items[0].help == "claude in beta"


def test_complete_session_names_survives_tmux_failures(monkeypatch):
    def boom():
        raise RuntimeError("no tmux")

    monkeypatch.setattr("gitdirector.commands.completion.list_all_gd_sessions", boom)
    assert complete_session_names(None, None, "") == []
