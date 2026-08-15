# GitDirector

A terminal control plane for working across many git repositories. See every
repo's state in one dashboard, and run AI coding agents and long-lived commands
in parallel tmux sessions you can monitor from the same place.

```bash
pip install gitdirector
gitdirector console
```

Requires Python 3.10+, git, and [tmux](https://github.com/tmux/tmux) ≥ 3.2a for
anything session-related.

> If GitDirector is useful to you, please star the repo — it needs stars to
> qualify for Homebrew inclusion.

## Commands

| Command | Description |
| --- | --- |
| `console` | Interactive TUI dashboard |
| `link PATH [--discover]` | Track a repo, or every repo under a path |
| `unlink PATH\|NAME [--discover]` | Stop tracking |
| `list` | All tracked repos with live status |
| `status` | Only repos with uncommitted changes |
| `pull [--yes]` | Fast-forward pull every tracked repo, concurrently |
| `cd NAME` | Open or switch to a tmux session for a repo |
| `info PATH\|NAME [--full]` | File, line, and token statistics |
| `doctor` | Check tmux, clipboard, config, completion, agent CLIs |
| `autoclean` | Drop links whose paths no longer exist |
| `reset [--yes]` | Kill all sessions, wipe `~/.gitdirector`, start fresh |
| `gd-tmux PATH\|NAME "cmd" [-d TEXT]` | Run a command in a new background session |
| `gd-capture SESSION [--lines N\|--full]` | Print a live session's scrollback |
| `gd-send SESSION [TEXT] [--enter\|--key C-c]` | Send input to a live session |
| `completion SHELL` | Completion setup for bash, zsh, or fish |

Repo arguments accept an absolute path or the directory basename. If two tracked
repos share a basename, GitDirector refuses and lists both so you can
disambiguate with the full path.

## Console

The TUI has three tabs: `[1]` Repositories, `[2]` Sessions, `[3]` Panels.

Navigate with `j`/`k` or arrows, `/` to filter, `s` to sort, `r` to refresh,
`enter` to act on the highlighted row.

**Repositories** shows sync state, branch, changes, last commit, size, and
active sessions. Repos sharing a parent directory collapse into a group row
(`space` to expand). Press `i` for repo info, or `g` for git operations —
status, timeline, branches, remotes, pull, push, and **Review Diff**, a two-pane
viewer for uncommitted changes with syntax-highlighted per-file diffs. `enter`
opens an action menu: new session, attach existing, launch an AI agent
(OpenCode, Claude Code, Copilot, Codex, Pi), or remove a session.

**Sessions** lists every `gd/*` session with status, purpose, repo, and a
free-form description (`d` to edit).

**Panels** manages reusable tmux layouts (`n` to create). Drag with the mouse to
select and copy text, or `y` to copy the visible pane. Uses `pbcopy`, `wl-copy`,
`xclip`, `xsel`, or `clip.exe`, falling back to OSC 52.

## Background sessions

`gd-tmux` runs a command in a detached `gd/<repo>/shell/<N>` session and prints
the session name, so scripts can capture it:

```bash
SESSION=$(gitdirector gd-tmux /path/to/repo "npm run dev" -d "Vite: dev server")
gitdirector gd-capture "$SESSION" --lines 100
gitdirector gd-send "$SESSION" --key C-c
```

The session self-destructs when the command exits, taking its scrollback with
it. To keep output, redirect inside the command:
`"make test 2>&1 | tee /tmp/run.log"`.

**AI coding agents:** the rules for driving GitDirector headlessly live in
[`SKILL.md`](./SKILL.md). Read it before running these commands.

## Configuration

`~/.gitdirector/config.yaml`:

```yaml
repositories:
    - /path/to/repo1
max_workers: 10   # optional, 1-32, default 10
theme: rose-pine  # optional
```

Themes: `textual-dark`, `textual-light`, `ansi-dark`, `ansi-light`, `nord`, `gruvbox`,
`dracula`, `tokyo-night`, `monokai`, `flexoki`, `solarized-light`,
`solarized-dark`, `atom-one-dark`, `atom-one-light`, `rose-pine`,
`rose-pine-moon`, `rose-pine-dawn`, `catppuccin-latte`, `catppuccin-frappe`,
`catppuccin-macchiato`, `catppuccin-mocha`.

### GitHub PAT fallback

Credentials live separately in `~/.gitdirector/secrets.yaml`:

```yaml
github_username: your-username
github_PAT: github_pat_...
```

Git commands run with your normal credentials first. Only if one fails with an
auth error, and both values are set, does GitDirector retry via a temporary
credential helper for HTTPS GitHub remotes. SSH remotes are untouched. The PAT
never appears on the command line or in TUI output, but it is stored in
plaintext — scope it narrowly and protect the file.

## Shell completion

Completion covers subcommands, options, and tracked repo names.

```bash
eval "$(gitdirector completion bash)"
eval "$(gitdirector completion zsh)"
gitdirector completion fish | source
```

For zsh, writing the script into your `$fpath` avoids a subprocess on every TAB:

```bash
gitdirector completion zsh > "${fpath[1]}/_gitdirector"
```

## License

MIT
