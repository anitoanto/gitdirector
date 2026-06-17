# GitDirector

A terminal based control plane for developers working across multiple repositories. Launch multiple AI coding agents, multiple tmux sessions and track changes across all your repos in one place.

## Why GitDirector?

If you work across more than a handful of repositories, the overhead adds up fast. Jumping between terminals to check states, pull changes, and babysit agents is friction you don't need.

GitDirector gives you a single cockpit for all of it. See every repo's status, drop into any of them. Run AI agents in parallel, each isolated in its own tmux session, while you monitor everything from the dashboard. Less tab-switching, more shipping.

## Installation

```bash
pip install gitdirector
```

Requires Python 3.10+.

## Support

If you find GitDirector useful, please star this repository on GitHub, we need more stars to qualify for inclusion in Homebrew. Your support helps a lot, thank you!

## Usage

| Command                                                    | Description                                            |
| ---------------------------------------------------------- | ------------------------------------------------------ |
| `gitdirector console`                                      | Open the interactive TUI dashboard                     |
| `gitdirector link PATH [--discover]`                       | Link a repository or discover all under a path         |
| `gitdirector unlink PATH\|NAME [--discover]`               | Unlink a repository by path, name, or all under a path |
| `gitdirector list`                                         | List all tracked repositories with live status         |
| `gitdirector status`                                       | Show repositories with staged/unstaged files           |
| `gitdirector pull [--yes]`                                 | Pull latest changes for all tracked repositories       |
| `gitdirector cd NAME`                                      | Open or switch to a tmux session for a repository      |
| `gitdirector autoclean`                                    | Remove broken repository links from tracking           |
| `gitdirector info PATH\|NAME [--full]`                     | Show file statistics for a repository                  |
| `gitdirector gd-tmux PATH\|NAME "command" [--description TEXT]` | Create a gd tmux session and run a command in it       |
| `gitdirector gd-capture SESSION [--lines N] [--full]`      | Print the scrollback of a live gd tmux session         |
| `gitdirector help`                                         | Show help                                              |

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
- Tabs for `[1] Repositories`, `[2] Sessions`, `[3] Panels`, and `[4] Groups`
- `j`/`k` or arrow keys to navigate
- `/` to filter the active tab
- `s` to sort the active table
- `i` to show repository info (file count, lines, tokens, max depth, top file types)
- `g` on the Repositories tab to open Git operations: status, timeline, branches, remotes, and pull
- `r` to refresh all statuses
- Press `enter` on any repository to open an action menu:
    - **New tmux session** — create and attach a session for the repository
    - **Review Diff** — open a two-pane viewer for uncommitted changes (left: file list with GitHub-style status pills and +/- counts; right: per-file unified diff with GitHub-dark syntax highlighting, line numbers, and green/red backgrounds for added/removed lines). Navigate files with `j`/`k`, `n`/`p`, `g`/`G`, or `]`/`[`. Switch the focused panel with `tab`. `r` refreshes, `esc` closes.
    - **Attach existing session** — switch to any already-running tmux session
    - **Launch AI agent** — open OpenCode, Claude Code, GitHub Copilot, Codex, or Pi in a new tmux session
    - **Remove session** — kill a running tmux session
- **Sessions tab** (press `2`) lists every active `gd/*` tmux session with its status, purpose, repository, **description**, and full session name. The description column is free-form text stored on the session (default `"-"`), with a width that scales to your terminal and wraps long text. Highlight a row and press `d` to edit its description.
- **Panels tab** (press `3`) manages reusable tmux panel layouts. Press `n` to create a panel, or `enter` on a panel to open, reconfigure, rename, or delete it.
- **Groups tab** (press `4`) automatically groups linked repositories that share the same parent directory. Press `enter` on a group to start or attach group-scoped sessions.

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

```bash
gitdirector pull          # prompts before pulling
gitdirector pull --yes    # skip confirmation
gitdirector pull -y       # short form
```

Pulls all tracked repositories concurrently using fast-forward only on each repository's current branch (`git pull --ff-only origin <current-branch>`). Reports success or failure per repository.

By default, `pull` asks for confirmation after listing the repositories it will update. Use `--yes` / `-y` for non-interactive runs.

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
gitdirector gd-tmux my-repo "opencode" --description "OpenCode: refactor auth middleware"
```

Creates a new gd tmux session rooted at the given repository, runs the supplied command inside it, and returns immediately without attaching. The session is a real `gd/{repo}/shell/{N}` session, visible in the Sessions tab of `gitdirector console`, and it self-destructs when the command exits. `N` is allocated as one higher than the highest currently running matching shell session number.

- `PATH|NAME` — accept either an absolute path to a tracked repository or its basename, with the same disambiguation rules as `gitdirector unlink`.
- `"command"` — the shell command to run inside the new session. Pass it quoted so the shell hands the whole string to gitdirector; quotes and arguments are preserved verbatim.
- `--description` / `-d` — optional. Free-text description stored on the session and shown in the Sessions tab. Accepts both `--description "text"` and `--description=text` forms. When omitted, the column displays `"-"`.

The full session name (e.g. `gd/myrepo/shell/1`) is printed to stdout right after the background session is created, so scripts and agents can capture it for later use with `gd-capture`:

```bash
SESSION=$(gitdirector gd-tmux /path/to/repo opencode --description "OpenCode: refactor auth")
gitdirector gd-capture "$SESSION" --lines 100
```

Use this from any shell to start a background command without entering the TUI first. The new session shows up in the Sessions tab immediately. Inside the TUI, highlight any session row and press `d` to edit its description.

### autoclean

```bash
gitdirector autoclean
```

Scans the tracked repositories in `~/.gitdirector/config.yaml` for paths that no longer exist on disk and removes them from the config. Lists any broken links for review and asks for confirmation before deleting them.

### gd-capture

```bash
gitdirector gd-capture gd/myrepo/shell/1                # last 200 lines
gitdirector gd-capture gd/myrepo/shell/1 --lines 50    # last 50 lines
gitdirector gd-capture gd/myrepo/shell/1 --full        # entire scrollback
```

Prints the current scrollback of a live `gd/*` tmux session to stdout (plain text, pipeable into `grep`/`less`/etc.). The argument is the full session name as shown in the TUI Sessions tab. It must match the `gd/<repo>/<purpose>/<N>` shape; sessions created by `gd-tmux` use `gd/<repo>/shell/<N>`.

**Only running sessions can be captured.** `gd-tmux` sessions self-destruct when their command exits (success or failure), so for any *finished* session you have to have redirected the command's own output to a file:

```bash
gitdirector gd-tmux myrepo "make test 2>&1 | tee /tmp/run.log"
```

The session is gone the moment the command exits; the scrollback lives on only as the bytes the process actually wrote.

## For AI coding agents

If you are an AI coding agent (Claude Code, OpenCode, GitHub Copilot, Codex, Pi, or any other tool that drives a shell on the user's behalf), the rules for integrating with the user's GitDirector workflow — including `gd-tmux` background sessions, `gd-capture`, and the mandatory `--description` flag — live in [`LLMS.md`](./LLMS.md). Read that file end-to-end before running any commands.

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
- [tmux](https://github.com/tmux/tmux) ≥ 3.2a (for `gitdirector cd`, `gitdirector console` sessions, `gitdirector gd-tmux`, and `gitdirector gd-capture`)

## License

MIT
