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
