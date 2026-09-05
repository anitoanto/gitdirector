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
- **Panels.** Reusable tmux layouts that show several sessions side by side.
- **Git without leaving the console.** Status, log, branches, remotes, pull,
  push, and a two-pane diff viewer that can stage, commit, and push.
- **Headless CLI.** Start a session, read its scrollback, and send it input
  from a script or from another agent.

## Install

```bash
pip install gitdirector        # or: pipx install gitdirector / uv tool install gitdirector
```

Requires Python 3.10 or newer (the test suite runs on every release from
3.10 through 3.14) and git. Anything session-related needs
[tmux](https://github.com/tmux/tmux) ≥ 3.2a. `gitdirector doctor` checks all
of it.

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
| `s` | Sort |
| `r` | Refresh |
| `g` | Git menu for the highlighted repo |
| `i` | Repo info: files, lines, tokens, and depth per extension |
| `space` / `shift+space` | Collapse or expand one group / every group |
| `d` | Edit a session's description (Sessions tab) |
| `n` | Create a panel (Panels tab) |
| `q` | Quit |

**Repositories** shows sync state (`up to date`, `ahead`, `behind`,
`diverged`), branch, staged and unstaged changes, and the last commit. Repos
that share a parent directory collapse into a group row. `enter` opens the
action menu: start a shell session, attach to an existing one, launch an AI
agent, or remove a session. `g` opens the git menu — status, timeline,
branches, remotes, pull, push, and **Review Diff**: a two-pane viewer of
uncommitted changes with syntax-highlighted per-file diffs, where `g` stages
everything and commits (optionally pushing) after you write a message.

**Sessions** lists every `gd/*` tmux session with its status, purpose, repo,
and a free-form description. `running` means the session is working,
`waiting` means it is blocked on you (a permission question, a prompt for
input), and `idle` means nothing is happening (a shell prompt, or a finished
agent turn). Claude Code and OpenCode sessions launched from the console
report their status through the agents' own lifecycle hooks; every other
session is classified from what its pane is doing (see [DEV.md](DEV.md)).

**Panels** manages reusable tmux layouts. Opening a panel attaches to a tmux
session that shows every assigned session side by side; `prefix 1`–`9` jumps
to a pane and `prefix b` shows the pane numbers.

Leaving a session (detach with `prefix d`, or exit its program) returns you
to the console where you left it.

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
| `gd-capture SESSION [--lines N\|--full]` | Print a live session's scrollback |
| `gd-send SESSION [TEXT] [--enter\|--key KEY]` | Send input to a live session |
| `completion {bash\|zsh\|fish}` | Print the shell completion script |
| `help` | Overview of all commands |

Repo arguments accept an absolute path or the directory basename. If two
tracked repos share a basename, GitDirector refuses and lists both so you can
pass the full path. Worktrees and submodules (where `.git` is a file rather
than a directory) are accepted like any other checkout.

Run `gitdirector COMMAND --help` (or `-h`) for a command's options. Errors
and the update notice go to stderr, so command output is safe to capture in
scripts.

## Background sessions

`gd-tmux` runs a command in a detached `gd/<repo>/shell/<N>` session and
prints the session name, so scripts can capture it:

```bash
SESSION=$(gitdirector gd-tmux /path/to/repo "npm run dev" -d "Vite: dev server")
gitdirector gd-capture "$SESSION" --lines 100
gitdirector gd-send "$SESSION" --key C-c
```

`--key` accepts `C-c`, `C-d`, `C-z`, `C-l`, `Enter`, `Escape`, `Tab`, `Up`,
and `Down`. The session self-destructs when the command exits, taking its
scrollback with it; to keep output, redirect inside the command:
`"make test 2>&1 | tee /tmp/run.log"`.

**AI coding agents:** the rules for driving GitDirector headlessly live in
[`SKILL.md`](SKILL.md). Point your agent at it before it runs these commands.

## Configuration

`~/.gitdirector/config.yaml`:

```yaml
repositories:
  - /path/to/repo1
max_workers: 10   # optional, 1-32, default 10
theme: rose-pine  # optional
```

Themes: `textual-dark`, `textual-light`, `ansi-dark`, `ansi-light`, `nord`,
`gruvbox`, `dracula`, `tokyo-night`, `monokai`, `flexoki`, `solarized-light`,
`solarized-dark`, `atom-one-dark`, `atom-one-light`, `rose-pine`,
`rose-pine-moon`, `rose-pine-dawn`, `catppuccin-latte`, `catppuccin-frappe`,
`catppuccin-macchiato`, `catppuccin-mocha`.

### GitHub credentials

GitDirector runs plain `git` for pull, push, and fetch, so it uses whatever
credentials git already has. The recommended setup is SSH remotes with a
key loaded in your agent — nothing to store in GitDirector, and pull and
push just work from the console and from background sessions.

`~/.ssh/config`:

```ssh-config
Host github.com
  Hostname ssh.github.com
  Port 443
  User git
  AddKeysToAgent yes
  IdentityFile ~/.ssh/github
```

`Hostname ssh.github.com` with `Port 443` tunnels SSH over the HTTPS port,
which gets through networks that block port 22; drop those two lines if you
do not need that. `AddKeysToAgent yes` loads the key into `ssh-agent` on
first use so the passphrase is asked once per login. Then clone (or switch
remotes) with the SSH URL:

```bash
git remote set-url origin git@github.com:owner/repo.git
ssh -T git@github.com   # should greet you by username
```

#### HTTPS token fallback

If SSH is not an option, GitDirector can retry with a personal access token
on HTTPS GitHub remotes. Credentials live separately in
`~/.gitdirector/secrets.yaml`:

```yaml
github_username: your-username
github_PAT: github_pat_...
```

Git commands still run with your normal credentials first. Only if one
fails with an auth error, and both values are set, does GitDirector retry
via a temporary credential helper. SSH remotes are never touched. The PAT
never appears on the command line or in TUI output, but it is stored in
plaintext — scope it narrowly and protect the file.

## Shell completion

Completion covers subcommands, options, and tracked repo names.

```bash
eval "$(gitdirector completion bash)"
eval "$(gitdirector completion zsh)"
gitdirector completion fish | source
```

For zsh, writing the script into your `$fpath` avoids a subprocess on every
TAB:

```bash
gitdirector completion zsh > "${fpath[1]}/_gitdirector"
```

## Contributing

See [DEV.md](DEV.md) for the development workflow, test suite conventions,
and the release process, and [AGENTS.md](AGENTS.md) if you are pointing a
coding agent at this repo.

## License

[MIT](LICENSE)
