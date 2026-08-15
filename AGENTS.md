# Agent Instructions

## Environment

This project uses **uv**. The `.venv` always lives in the project root.

Run `uv sync` first, then prefix every command with `uv run` — it resolves the
right environment automatically. Never activate `.venv` manually.

```bash
uv sync
uv run pytest
uv run gitdirector help
uv run nox -s clean   # wipe caches, coverage, and build artifacts
```

## Code style

Keep comments minimal — let the code speak for itself. Comment *why*, not
*what*, and only where the reasoning is not obvious from the code.

## Before pushing

Run all three unless the user says otherwise:

```bash
uv run pytest
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

If the format check reports files, run `uv run ruff format src/ tests/`, re-run
the tests, and include the formatting fix with your changes.

The test suite runs in randomized order. If a test fails, replay it with the
seed pytest printed (`--randomly-seed=<seed>`) before assuming it is unrelated
noise — see [DEV.md](DEV.md).

## Git requires explicit permission

Ask before any state-changing git operation: `add`, `commit`, `push`, `rebase`,
`merge`, `reset`, `checkout` of another branch, force-push, or anything that
alters history. Confirm the exact files, the commit message, and the target
branch first.

Read-only inspection (`status`, `diff`, `log`) needs no permission.

## Docs

- [README.md](README.md) — overview, commands, configuration
- [DEV.md](DEV.md) — dev workflow, test suite conventions, release
- [SKILL.md](SKILL.md) — driving GitDirector headlessly from a shell
