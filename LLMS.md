---
name: gitdirector
description: Drive GitDirector from a shell. Run dev servers, watchers, REPLs, and other AI coding agents in background tmux sessions; read their output, send them input, and watch whether they are running, waiting, pending, or idle. Use when the user asks you to use GitDirector, `gitdirector`, or `gd`.
---

# GitDirector for AI agents

GitDirector keeps long-lived programs in named, detached tmux sessions. They
keep running after your shell call returns, and you can read from them, type
into them, and check their status later. A normal shell call can't do that,
because it blocks until the command exits.

Use it only when the user asks for GitDirector (`gitdirector`, `gd`) **and**
the program is long-lived: a dev server, a watcher, a REPL, or another AI
agent. Run one-off commands in your own shell: a session closes when its
program exits, and its output goes with it.

For git itself, use `git` directly.

## Commands

Every command below is non-interactive. Results go to stdout and errors to
stderr. Exit code is 0 on success, 1 on failure, and 2 for bad arguments.

```bash
# Find repositories
gitdirector list --json [--no-fetch]      # tracked repos: name, path, branch, sync, changes
gitdirector info PATH|NAME --json [--full] # files, lines, and tokens per extension

# Sessions
gitdirector gd-tmux PATH|NAME "command" -d "Agent: what it is for"   # prints SESSION
gitdirector gd-tmux PATH|NAME --agent claude -d "Agent: task"          # start an agent
gitdirector sessions --json               # every live session and its status
gitdirector gd-capture SESSION [-n 200 | --full]   # recent output, as text
gitdirector gd-screenshot SESSION /abs/path/shot.png  # the screen as an image (rarely needed)
gitdirector gd-send SESSION "text" [--enter]       # paste text (newlines stay newlines); --enter submits it
gitdirector gd-send SESSION --key C-c              # keys: C-c C-d C-z C-l Enter Escape Tab Up Down
gitdirector gd-kill SESSION               # end the session and everything in it

gitdirector doctor                        # is tmux installed? which agent CLIs are there?
```

Do not run `console`, `cd`, or `panel`: they take over the terminal.

## Rules

- **Always pass `-d/--description`**, as `AgentName: what it is doing`. It is
  how the user tells your sessions apart in the console.
- **Use the absolute repo path** from `list --json`. Two tracked repos can
  share a name.
- **Quote the command as one string.** It runs through `sh -lc`, so
  `'echo "hi"'` is the safe form.
- **Take the session name from stdout.** Names look like
  `gd/my-repo_bavte/shell/3`, and the trailing number is not predictable.
- **Stop programs with `gd-send SESSION --key C-c`.** Use `gd-kill` only if
  that fails, or when the user asks.
- **Redirect output you need to keep**, because it dies with the session:
  `"make watch 2>&1 | tee /tmp/watch.log"`.

## Screenshots

`gd-capture` is almost always enough: read the text first. Use
`gd-screenshot` only when that text gives you too little to understand what
is going on, for example a full-screen program where layout, colour, or
highlighting carries the meaning (which option is selected, which pane is
active, what is red).

```bash
gitdirector gd-screenshot "$S" /tmp/api-session.png   # prints the saved path
```

- **You must give the path.** There is no default. It must end in `.png`,
  its directory must exist, and an existing file is overwritten.
- The image shows exactly what the session's screen shows right now: its
  size, colours, and cursor. Scrollback is not included.
- Delete the file when you are done with it.

## Session status

`sessions --json` returns one object per live session:

```json
{"session": "gd/api_bavte/claude-auto/1", "repo": "api", "purpose": "claude-auto",
 "status": "waiting", "description": "Claude: fix the auth tests"}
```

| status | meaning |
| --- | --- |
| `running` | the program is working |
| `waiting` | it needs a person: a permission prompt, a question, or a bell |
| `pending` | the agent (Claude Code or OpenCode) is at its prompt, but subagents it started are still working; not done yet |
| `idle` | nothing is happening; an agent is at its prompt, or a shell is at `$` |

Agents started with `--agent claude` or `--agent opencode` report their own
status through hooks, so theirs is exact. For every other program, the status
is inferred from the pane (CPU, output changes, the bell). Sessions started
from the console or with `--agent` show up here the same way.

## Running another AI agent

`--agent` is one of `claude`, `opencode`, `codex`, `copilot`, or `pi`. Claude
Code also takes `--mode`:

- `auto` (the default) is Claude's auto permission mode.
- `default` uses the user's own settings.
- `bypass` passes `--dangerously-skip-permissions`. Use it only if the user
  asks.

```bash
S=$(gitdirector gd-tmux /abs/path/api --agent claude -d "Claude: fix auth tests")

# Wait for its prompt: poll `sessions --json` until this session is idle.
gitdirector gd-send "$S" "Fix the failing tests in tests/auth, then run them" --enter

# Poll until it is idle (done) or waiting (needs an answer), then read it.
# pending means its background subagents are still working: keep polling.
gitdirector sessions --json
gitdirector gd-capture "$S" -n 80

# Answer a question, or approve a prompt, the same way.
gitdirector gd-send "$S" "yes, go ahead" --enter
```

Poll every few seconds. When the status is `waiting`, read the screen with
`gd-capture` before you answer. Never approve something the user wouldn't.

## Dev server example

```bash
S=$(gitdirector gd-tmux /abs/path/web "npm run dev" -d "Vite: dev server on :5173")
gitdirector gd-capture "$S" -n 50        # check that it started and find its URL
gitdirector gd-send "$S" --key C-c       # stop it
```
