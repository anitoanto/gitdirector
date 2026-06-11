# Agent Instructions

## Virtual Environment Setup

Always setup the `.venv` correctly before running any commands or tests.

### Important Notes
- The `.venv` directory is always present in the root of the project
- This project uses **uv** as the package manager
- All commands should be run with `uv run` to ensure the correct virtual environment is activated

### Setup Instructions

1. **Ensure .venv is properly initialized:**
   ```bash
   uv sync
   ```

2. **Run commands using uv:**
   ```bash
   uv run <command>
   ```

   Examples:
   ```bash
   uv run gitdirector help
   uv run pytest
   uv run black src/
   ```

3. **Never manually activate the virtual environment** - `uv` handles this automatically

### Why uv?
- `uv` ensures consistent dependency management across all environments
- It automatically uses the `.venv` in the project root
- All team members get the same dependencies and versions

## Documentation

- **[DEV.md](DEV.md)** — Developer commands: setup, run, test, format, lint
- **[README.md](README.md)** — Project overview, installation, usage, and configuration

## Code Style

- Use very minimal comments in the codebase — let the code speak for itself

## Pre-push Checklist

Before pushing any code, always run the full test suite, linting, and formatting unless the user explicitly says not to:

1. **Tests:** `uv run pytest`
2. **Lint:** `uv run ruff check src/ tests/`
3. **Format check:** `uv run ruff format --check src/ tests/`

If `ruff format` reports files to reformat, run `uv run ruff format src/ tests/` to apply the changes, re-run the tests, then commit the formatting fix alongside the rest of the changes.

## Git Actions Require User Permission

Always ask for the user's explicit permission before performing any of the following git actions: `git add`, `git commit`, `git push`, `git rebase`, `git merge`, `git reset`, `git checkout` of other branches, force-push, or any other history-altering operation. Confirm the exact set of files to be staged, the commit message, and the target branch before running the command. The only exception is read-only inspection (`git status`, `git diff`, `git log`) which may be done freely.
