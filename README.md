# GitDirector

A Python CLI tool for managing and synchronizing multiple git repositories.

## Installation

```bash
pip install gitdirector
```

## Usage

```
gitdirector add PATH [--discover]     Add a repository or discover all under a path
gitdirector remove PATH [--discover]  Remove a repository or all under a path
gitdirector list                      List all tracked repositories with live status
gitdirector status                    Show dirty repositories with staged/unstaged files
gitdirector pull                      Pull latest changes for all tracked repositories
gitdirector help                      Show help
```

### add

```bash
gitdirector add /path/to/repo
gitdirector add /path/to/folder --discover   # recursively find and add all repos
```

### remove

```bash
gitdirector remove /path/to/repo
gitdirector remove /path/to/folder --discover
```

### list

Displays a live table of all tracked repositories with:

- Sync state: `up to date`, `ahead`, `behind`, `diverged`, or `unknown`
- Current branch
- Staged/unstaged changes
- Last commit (relative time)
- Tracked file size
- Path

Checks run concurrently (default: 10 workers).

### status

Shows repositories with uncommitted changes (staged and/or unstaged files). Prints a summary of total, clean, and changed repo counts.

### pull

Pulls all tracked repositories concurrently using fast-forward only (`git pull --ff-only`). Reports success or failure per repository.

## Configuration

Config is stored at `~/.gitdirector/config.yaml`.

```yaml
repositories:
  - /path/to/repo1
  - /path/to/repo2
max_workers: 10   # optional, default 10
```

## Requirements

- Python 3.9+
- Git

## License

MIT
