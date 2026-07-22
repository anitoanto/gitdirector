"""Regression tests for tab completion of repo names."""

from __future__ import annotations

from pathlib import Path

import yaml

from gitdirector.commands.completion import complete_repository_names


def test_complete_repository_names_dedupes_when_two_repos_share_basename(config_dir, monkeypatch):
    """When two tracked repos share a basename (different paths), the
    completion list should still present each unique name only once
    per prefix — duplicate tabs are noisy and confusing.
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
    assert values.count("same-name") == 1, (
        f"expected one completion entry per unique name, got {values}"
    )


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
