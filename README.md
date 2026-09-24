<h1 align="center">GitDirector</h1>

<p align="center">
  A terminal control plane for many git repositories and the AI agents working in them.
</p>

<p align="center">
  <a href="https://pypi.org/project/gitdirector/"><img alt="PyPI" src="https://img.shields.io/pypi/v/gitdirector?color=6e5bd6"></a>
  <a href="https://github.com/anitoanto/gitdirector/actions/workflows/main.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/anitoanto/gitdirector/main.yml?branch=main&label=CI"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/anitoanto/gitdirector"></a>
</p>

<p align="center">
  See every repo's state in one dashboard. Run coding agents and long-lived
  commands in parallel tmux sessions, watch which ones need you, and drive the
  whole thing from scripts.
</p>

![GitDirector console: repositories, sessions, panels, and the repo action menu](https://raw.githubusercontent.com/anitoanto/gitdirector/main/docs/screenshots/console-overview.png)

<sub>Screenshots use made-up repositories and sessions.</sub>

## Highlights

- **One dashboard for every repo.** Sync state, branch, uncommitted changes,
  and last commit for each tracked repository, grouped by parent directory.
- **Agents and servers in tmux sessions.** Launch Claude Code, OpenCode,
  GitHub Copilot, Codex, or Pi in a repo with one keypress, or run a dev
  server there. Each lives in its own named tmux session.
- **Live session status.** Every session shows `running`, `waiting` (blocked
  on you), or `idle`, so you can leave an agent alone until it needs an answer.
- **Session sidebar.** An open session sits beside a list of every other
  session and its status; click one to switch.
- **Panels.** Reusable tmux layouts that show several sessions side by side.
- **Git without leaving the console.** Status, log, branches, remotes, pull,
  push, and a two-pane diff viewer that can stage, commit, and push.
- **Headless CLI.** Start a session, read its scrollback, and send it input
  from a script or from another agent.

## Install

```bash
pip install gitdirector        # or: pipx install gitdirector / uv tool install gitdirector
```

Runs on macOS and Linux with Python 3.10–3.14 and git; sessions and panels
need [tmux](https://github.com/tmux/tmux) ≥ 3.2a. `gitdirector doctor` checks
all of it.

## Quick start

```bash
gitdirector link ~/work --discover   # track every repo under a directory
gitdirector console                  # open the dashboard
```

> If GitDirector is useful to you, please star the repo — it needs stars to
> qualify for Homebrew inclusion.

## Console

![GitDirector features: git menu, diff review, repo info, and panel creation](https://raw.githubusercontent.com/anitoanto/gitdirector/main/docs/screenshots/console-features.png)

The console has three tabs, switched with `1`, `2`, and `3`.

| Key | Action |
| --- | --- |
| `j` / `k`, arrows | Move between rows (`h` / `l` scroll wide tables sideways) |
| `enter` | Act on the row: action menu (repositories), attach (sessions), open (panels) |
| `/` | Filter the table |
| `s` | Sort (Repositories and Panels tabs) |
| `r` | Refresh |
| `g` | Git menu for the highlighted repo |
| `i` | Repo info: files, lines, tokens, and depth per extension |
| `space` / `shift+space` | Collapse or expand one group / every group |
| `d` | Edit a session's description (Sessions tab) |
| `n` | Create a panel (Panels tab) |
| `q` | Quit |

**Repositories** shows sync state (`up to date`, `ahead`, `behind`,
`diverged`), branch, changes, and the last commit; repos sharing a parent
directory collapse into a group. `enter` opens the action menu: start a shell,
open the repo or group in VS Code, launch an AI agent, attach to or remove a
session. Claude Code's permission mode is picked on its row with Tab or `←`/`→`:
`default` (your own settings), `auto` (preselected), or `bypass`
(`--dangerously-skip-permissions`). `g` opens the git menu: status,
timeline, branches, remotes, pull, push, and **Review Diff** — a two-pane view
of uncommitted changes with real line numbers, where `g` stages everything and
commits (optionally pushing).

**Sessions** lists every gitdirector tmux session with its status, purpose,
repo, and tmux session name, with its description on the line below (wrapped,
never cut off). A repo's sessions are always kept together, and alternate repos
sit on a subtle band. Statuses are `running` (the agent or program is working),
`waiting` (it needs you: a permission prompt, a question, a bell), or `idle`
(nothing is happening). Claude Code and OpenCode report their own status
through hooks passed inline on the command GitDirector launches (your own
settings are never touched); everything else is classified from its pane (see
[DEV.md](DEV.md)).

**Panels** are reusable tmux layouts showing several sessions side by side,
each under its own header with its slot number. `prefix 1`–`9` jumps to a
slot, and proportions hold as the window resizes.

Every session carries its own themed header and status line, so it looks the
same attached directly, in a panel, or from a plain `tmux attach`. Sessions
never learn which directory gitdirector was started from.

### Session sidebar

Opening a session (from the console, the Sessions tab, or `gitdirector cd`)
shows it beside a sidebar listing every session, grouped by repo, with its
live status. Click a session, or select it and press Enter, to show it on the
right; the session you leave keeps running.

| Key | Action |
| --- | --- |
| `prefix Tab` | From the session, focus the sidebar (reopening it if it was closed) |
| `prefix b` | Collapse the sidebar to a rail of status dots, or expand it |
| `prefix d` | Detach and go back to the console |
| `↑`/`↓`, `j`/`k`, Enter | In the sidebar: move, open the session |
| Tab, `→`, `l`, Esc | In the sidebar: focus the session |

The `«` next to the sidebar's title goes back to the console, like `prefix d`, and `◧`
collapses or expands it. The keys are listed on the status line beside the clock
when the window is wide enough.

The usual tmux ways of moving between panes (`prefix ←`/`→`, `prefix o`, a
click) work too. `prefix Tab` and `prefix b` only mean this inside the sidebar
view; everywhere else they keep whatever they were bound to.

When the shown session ends, the sidebar says so and takes focus so you can
pick another; when no sessions are left you are back in the console. Set
`sidebar: false` in the config to attach to sessions directly instead.

## Commands

| Command | Description |
| --- | --- |
| `console` | Interactive dashboard |
| `link PATH [--discover]` | Track a repo, or every repo under a directory |
| `unlink PATH\|NAME [--discover]` | Stop tracking |
| `list` | All tracked repos with their sync status |
| `status` | Only repos with uncommitted changes |
| `pull [--yes]` | Fast-forward pull every tracked repo, concurrently |
| `cd PATH\|NAME` | Open or switch to a tmux session for a repo |
| `info PATH\|NAME [--full]` | File, line, and token statistics |
| `doctor` | Check tmux, config, shell completion, and agent CLIs |
| `autoclean [--yes]` | Drop links whose paths no longer exist |
| `reset [--yes]` | Kill every session and panel, wipe `~/.gitdirector` |
| `gd-tmux PATH\|NAME "cmd" [-d TEXT]` | Run a command in a new background session |
| `gd-capture SESSION [--lines N\|--full]` | Print a live session's last lines |
| `gd-send SESSION [TEXT] [--enter\|--key KEY]` | Send input to a live session |
| `completion {bash\|zsh\|fish}` | Print the shell completion script |
| `help` | Overview of all commands |

Repo arguments take a path or the directory name; when two tracked repos
share a name, pass the path. Worktrees and submodules work like any checkout.
`gitdirector COMMAND -h` shows a command's options. Errors and notices go to
stderr, so command output is safe to capture.

## Background sessions

```bash
SESSION=$(gitdirector gd-tmux my-repo "npm run dev")   # start; prints the session name
gitdirector gd-capture "$SESSION" --lines 50            # read its last lines
gitdirector gd-send "$SESSION" --key C-c                # send it input
```

The session ends when the command exits.

**AI coding agents:** the rules for driving GitDirector headlessly live in
[`SKILL.md`](SKILL.md). Point your agent at it before it runs these commands.

## Configuration

`~/.gitdirector/config.yaml`:

```yaml
repositories:
  - /path/to/repo1
max_workers: 10   # optional, 1-32, default 10
theme: rose-pine  # optional
sidebar: true     # optional; false attaches to sessions without the sidebar
```

Themes: `textual-dark`, `textual-light`, `ansi-dark`, `ansi-light`, `nord`,
`gruvbox`, `dracula`, `tokyo-night`, `monokai`, `flexoki`, `solarized-light`,
`solarized-dark`, `atom-one-dark`, `atom-one-light`, `rose-pine`,
`rose-pine-moon`, `rose-pine-dawn`, `catppuccin-latte`, `catppuccin-frappe`,
`catppuccin-macchiato`, `catppuccin-mocha`.

### GitHub credentials

GitDirector runs plain `git`, so if pull and push work in your terminal they
work here. SSH is preferred:

```ssh-config
Host github.com
  Hostname ssh.github.com
  Port 443
  User git
  AddKeysToAgent yes
  IdentityFile ~/.ssh/github
```

Git runs without a terminal, so ssh can't ask to confirm an unknown host;
GitDirector uses `StrictHostKeyChecking=accept-new`, which records a new host
in `~/.ssh/known_hosts` as answering `yes` would and still refuses a key that
changes. A refused key shows as `(offline)` in the Sync column. Set
`GIT_SSH_COMMAND` to take over ssh entirely.

If SSH isn't possible, HTTPS GitHub remotes can fall back to a personal access
token in `~/.gitdirector/secrets.yaml`:

```yaml
github_username: your-username
github_PAT: github_pat_...
```

It is only used to retry a command that failed authentication, through a
temporary credential helper — never on the command line or in the TUI. The
file is plaintext, so scope the token narrowly.

## Shell completion

Add the line for your shell to its rc file to complete subcommands, options,
repo names, and session names:

```bash
eval "$(gitdirector completion bash)"
eval "$(gitdirector completion zsh)"
gitdirector completion fish | source
```

## Contributing

See [DEV.md](DEV.md) for development, tests, internals, and releases, and
[AGENTS.md](AGENTS.md) when pointing a coding agent at this repo.

## License

[MIT](LICENSE)
