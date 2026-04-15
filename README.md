# GitDirector

A terminal based control plane for developers working across multiple repositories. Launch multiple AI coding agents, multiple tmux sessions and track changes across all your repos in one place.

## Why GitDirector?

If you work across more than a handful of repositories, the overhead adds up fast. Jumping between terminals to check states, pull changes, and babysit agents is friction you don't need.

GitDirector gives you a single cockpit for all of it. See every repo's status, Drop into any of them. Run AI agents in parallel, each isolated in its own tmux session, while you monitor everything from the dashboard. Less tab-switching, more shipping.

## Installation

```bash
pip install gitdirector
```

## Support

If you find GitDirector useful, please star this repository on GitHub, we need more stars to qualify for inclusion in Homebrew. Your support helps a lot, thank you!

## Usage

| Command                                      | Description                                            |
| -------------------------------------------- | ------------------------------------------------------ |
| `gitdirector console`                        | Open the interactive TUI dashboard                     |
| `gitdirector link PATH [--discover]`         | Link a repository or discover all under a path         |
| `gitdirector unlink PATH\|NAME [--discover]` | Unlink a repository by path, name, or all under a path |
| `gitdirector list`                           | List all tracked repositories with live status         |
| `gitdirector status`                         | Show repositories with staged/unstaged files           |
| `gitdirector pull`                           | Pull latest changes for all tracked repositories       |
| `gitdirector cd NAME`                        | Open or switch to a tmux session for a repository      |
| `gitdirector autoclean links\|sessions`      | Clean broken links or stale tmux sessions              |
| `gitdirector help`                           | Show help                                              |

### link

```bash
gitdirector link /path/to/repo
gitdirector link /path/to/folder --discover   # recursively find and link all repos
```

### console

```bash
gitdirector console
```

Opens a full interactive TUI dashboard.

Features:

- Live table with sync state, branch, changes, last commit, and active tmux sessions
- `j`/`k` or arrow keys to navigate
- `/` to filter repositories by name or path
- `s` to cycle sort by any column
- `r` to refresh all statuses
- Press `enter` on any repository to open an action menu:
    - **New tmux session** — create and attach a session for the repository
    - **Attach existing session** — switch to any already-running tmux session
    - **Launch AI agent** — open OpenCode, Claude Code, GitHub Copilot, or Codex in a new tmux session
    - **Remove session** — kill a running tmux session

### unlink

```bash
gitdirector unlink /path/to/repo         # unlink by full path
gitdirector unlink my-repo               # unlink by repository name
gitdirector unlink /path/to/folder --discover  # unlink all repos under a path
```

If multiple tracked repositories share the same name, `gitdirector` will refuse and list the conflicting paths so you can use the full path instead.

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

### cd

```bash
gitdirector cd my-repo
```

Opens a [tmux](https://github.com/tmux/tmux) session rooted at the repository directory, or switches to it if a session for that repo already exists.

- **Inside tmux** — switches the current client to the target session.
- **Outside tmux** — replaces the current process with `tmux attach-session`, handing the terminal over to tmux.

> **Requires tmux to be installed on your system.**
>
> macOS: `brew install tmux`  
> Debian/Ubuntu: `sudo apt install tmux`  
> Arch: `sudo pacman -S tmux`

## Configuration

Config is stored at `~/.gitdirector/config.yaml`.

```yaml
repositories:
    - /path/to/repo1
    - /path/to/repo2
max_workers: 10 # optional, default 10
```

## Requirements

- Python 3.9+
- Git
- [tmux](https://github.com/tmux/tmux) ≥ 3.2a (for `gitdirector cd`)

## License

MIT
