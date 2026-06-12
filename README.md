# GitDirector

A terminal based control plane for developers working across multiple repositories. Launch multiple AI coding agents, multiple tmux sessions and track changes across all your repos in one place.

## Why GitDirector?

If you work across more than a handful of repositories, the overhead adds up fast. Jumping between terminals to check states, pull changes, and babysit agents is friction you don't need.

GitDirector gives you a single cockpit for all of it. See every repo's status, Drop into any of them. Run AI agents in parallel, each isolated in its own tmux session, while you monitor everything from the dashboard. Less tab-switching, more shipping.

## Installation

```bash
pip install gitdirector
```

Requires Python 3.10+.

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
| `gitdirector autoclean`                      | Remove broken repository links from tracking           |
| `gitdirector info PATH\|NAME [--full]`       | Show file statistics for a repository                  |
| `gitdirector gd-tmux PATH\|NAME "command"`   | Create a gd tmux session and run a command in it       |
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
- `i` to show repository info (file count, lines, tokens, max depth, top file types)
- `r` to refresh all statuses
- Press `enter` on any repository to open an action menu:
    - **New tmux session** — create and attach a session for the repository
    - **Review Diff** — open a two-pane viewer for uncommitted changes (left: file list with GitHub-style status pills and +/- counts; right: per-file unified diff with GitHub-dark syntax highlighting, line numbers, and green/red backgrounds for added/removed lines). Navigate files with `j`/`k`, `n`/`p`, `g`/`G`, or `]`/`[`. Switch the focused panel with `tab`. `r` refreshes, `esc` closes.
    - **Attach existing session** — switch to any already-running tmux session
    - **Launch AI agent** — open OpenCode, Claude Code, GitHub Copilot, Codex, or Pi in a new tmux session
    - **Remove session** — kill a running tmux session

### unlink

```bash
gitdirector unlink /path/to/repo         # unlink by full path
gitdirector unlink my-repo               # unlink by repository name
gitdirector unlink /path/to/folder --discover  # unlink all repos under a path
```

If multiple tracked repositories share the same name, `gitdirector` will refuse and list the conflicting paths so you can use the full path instead.

### info

```bash
gitdirector info /path/to/repo        # by path
gitdirector info my-repo               # by name
gitdirector info my-repo --full        # show all file extensions
```

Shows file statistics for a repository:

- Total file count, line count, token count, and max directory depth
- Top 10 file types by count with line and token breakdowns (use `--full` to show all)
- Files without extensions grouped as `(no ext)`
- Remaining types grouped as `others`
- Binary files show `-` for lines and tokens
- All operations respect `.gitignore` at every level

Also available in the TUI console by pressing `i` on a selected repository.

### list

Displays a live table of all tracked repositories with:

- Sync state: `up to date`, `ahead`, `behind`, `diverged`, or `unknown`
- Current branch
- Staged/unstaged changes
- Last commit (relative time)
- Tracked file size
- Path

Checks run concurrently (default: 10 workers, configurable from 1 to 32).

### status

Shows repositories with uncommitted changes (staged and/or unstaged files). Prints a summary of total, clean, and changed repo counts.

### pull

Pulls all tracked repositories concurrently using fast-forward only on each repository's current branch (`git pull --ff-only origin <current-branch>`). Reports success or failure per repository.

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

### gd-tmux

```bash
gitdirector gd-tmux my-repo "npm test"
gitdirector gd-tmux /path/to/my-repo "make build"
gitdirector gd-tmux my-repo 'echo "hello world"'
```

Creates a new gd tmux session rooted at the given repository, runs the supplied command inside it, and attaches the session. The session is created using the same code path the TUI's **Launch AI agent** menu uses: it is a real `gd/{repo}/{command}/{N}` session, visible in the Sessions tab of `gitdirector console`, and it self-destructs when the command exits.

- `PATH|NAME` — accept either an absolute path to a tracked repository or its basename, with the same disambiguation rules as `gitdirector unlink`.
- `"command"` — the shell command to run inside the new session. Pass it quoted so the shell hands the whole string to gitdirector; quotes and arguments are preserved verbatim.

The command is the same as opening the repo in the console, picking **Launch AI agent** from the action menu, and entering the command — but you can do it from any shell without entering the TUI first. The new session shows up in the Sessions tab immediately.

### autoclean

```bash
gitdirector autoclean
```

Scans the tracked repositories in `~/.gitdirector/config.yaml` for paths that no longer exist on disk and removes them from the config. Lists any broken links for review and asks for confirmation before deleting them.

## Note for AI coding agents

If you are an AI coding agent reading this project: when the user asks you to run a long-lived process inside a tracked repository — a dev server, a frontend bundler, a backend API, a watcher, a database, a test runner with a long timeout, or anything else that does not exit on its own — **do not run it in the foreground**. A foreground process will block your shell for the rest of the session and the user will not be able to interact with you.

Use `gitdirector gd-tmux` to launch the process in a detached gd tmux session that the user can monitor and tear down themselves. The session shows up in the Sessions tab of `gitdirector console`, auto-cleans up when the process exits, and supports path-or-name lookup the same way every other gitdirector command does.

**Always prefer the absolute path over the repo name.** The by-name lookup matches the *directory name on disk* verbatim (via `Path.name`) — case-sensitive, dots and spaces included — and is ambiguous if two tracked repos share the same basename. When in doubt, look up the path with `gitdirector list` and use the absolute path. Quoting a path is fine; an absolute path is never ambiguous.

Examples:

```bash
# Frontend dev server in the user's web repo — use the absolute path
gitdirector gd-tmux /Users/me/projects/webapp "npm run dev"

# Backend API in the api repo
gitdirector gd-tmux /Users/me/projects/api "uvicorn app.main:app --reload --port 8000"

# A long-running integration test with output captured to a log
gitdirector gd-tmux /Users/me/projects/myrepo "pytest -x -v 2>&1 | tee /tmp/test.log"

# A watcher or build loop
gitdirector gd-tmux /Users/me/projects/myrepo "npm run watch"

# A repo whose directory name contains a space — quote both the path and the command
gitdirector gd-tmux "/Users/me/projects/My Repo" "npm run dev"
```

The by-name form is supported for ergonomic use by humans in interactive shells, but agents should default to the absolute path to avoid any chance of matching the wrong repo.

Rules of thumb:

- **Use the absolute path.** It is unambiguous and survives the user renaming or moving the directory. The by-name form is convenient shorthand, not a slug — it is the raw directory name, case-sensitive.
- **Repo names with spaces, dots, or punctuation are matched verbatim** when you do use the by-name form. A repo at `/Users/me/My Repo` is matched by `"My Repo"`, not by `My` or `Repo`. Quote the name to keep the shell from splitting it.
- **Quoting the command matters.** The command string is handed to `sh -lc` inside the new session, so write it as a single quoted string. `'echo "hi"'` (single-quote outer, double-quote inner) is the safest pattern for embedded double quotes.
- **Path separators in the command are fine.** Commands like `python /path/to/script.py` or `find . -name foo/bar` work; the command is embedded verbatim into the inner shell, while the session-name label is auto-sanitized.
- **The session self-destructs** when the command exits (success or failure), so you do not need to clean it up.
- **If you need to capture output**, redirect it to a file in the command itself (`> /tmp/out 2>&1`) and read the file later — the session is gone once the command exits.
- **If the user asks you to stop a running process**, kill the matching session: `gitdirector console` → Sessions tab, or directly with `tmux kill-session -t =<session-name>`.

## Configuration

Config is stored at `~/.gitdirector/config.yaml`.

```yaml
repositories:
    - /path/to/repo1
    - /path/to/repo2
max_workers: 10 # optional, valid range 1-32, default 10
theme: rose-pine # optional, default rose-pine
```

### Available Themes

`textual-dark`, `textual-light`, `nord`, `gruvbox`, `catppuccin-mocha`, `textual-ansi`, `dracula`, `tokyo-night`, `monokai`, `flexoki`, `catppuccin-latte`, `catppuccin-frappe`, `catppuccin-macchiato`, `solarized-light`, `solarized-dark`, `rose-pine`, `rose-pine-moon`, `rose-pine-dawn`, `atom-one-dark`, `atom-one-light`

## Requirements

- Python 3.10+
- Git
- [tmux](https://github.com/tmux/tmux) ≥ 3.2a (for `gitdirector cd`)

## License

MIT
