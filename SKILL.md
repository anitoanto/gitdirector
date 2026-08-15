---
name: gitdirector
description: Drive GitDirector headlessly from a shell — run long-lived commands in background tmux sessions, read their output, and send them input. Use when the user asks you to use GitDirector, `gitdirector`, or `gd` to start dev servers, watchers, REPLs, or AI agents.
---

# GitDirector for coding agents

GitDirector runs commands in named, detached tmux sessions you can read from and
write to later. That is the whole value: a normal shell call blocks until the
command exits, so it cannot host a dev server or a watcher. A GitDirector
session keeps running after your call returns.

## When to use it

Use it **only** when the user has asked for GitDirector / `gd`, **and** the
command is long-lived — dev server, build/file watcher, REPL, or an interactive
AI agent.

Do not use it for one-off commands. The session self-destructs the moment the
command exits, taking its scrollback with it, so `make test` run this way is
output you can never read. Run one-off commands in your normal shell.

## The four commands

```bash
# Start. Prints the session name to stdout and returns immediately.
gitdirector gd-tmux PATH|NAME "command" --description "Agent: what this is doing"

# Read. Only works while the session is alive.
gitdirector gd-capture SESSION [--lines N] [--full]     # default: last 200 lines

# Write.
gitdirector gd-send SESSION "text" [--enter]            # paste; --enter runs it
gitdirector gd-send SESSION --key C-c                   # stop foreground process
```

Read-only inspection is also safe headlessly: `gitdirector list`, `status`,
`info PATH|NAME [--full]`, `doctor`.

## Rules

- **Always pass `--description`.** The session name is generic (`shell`), so the
  description is the only way the user can tell what it is doing in the Sessions
  tab. Format: `AgentName: brief description`.
- **Prefer the absolute repo path.** Name lookup matches the directory basename
  and is ambiguous when two tracked repos share one.
- **Quote the command as one string.** It is passed to `sh -lc`, so
  `'echo "hi"'` is the safe pattern for embedded double quotes.
- **Capture the session name** from stdout rather than guessing it. Names are
  `gd/<repo>/shell/<N>`, where `N` is one above the highest running shell
  session for that repo — it is not stable or predictable.
- **Stop processes with `--key C-c`.** Only kill the session if Ctrl-C fails or
  the user asks.

## Typical flow

```bash
SESSION=$(gitdirector gd-tmux /path/to/repo "npm run dev" \
  --description "Vite: frontend dev server on :5173")

# ... later, while it is still running ...
gitdirector gd-capture "$SESSION" --lines 100
gitdirector gd-send "$SESSION" --key C-c
```

## Output that must outlive the session

Scrollback dies with the session, so redirect inside the command itself:

```bash
gitdirector gd-tmux /path/to/repo "make watch 2>&1 | tee /tmp/watch.log" \
  --description "Watcher: rebuild on save"
```

## More examples

```bash
gitdirector gd-tmux /path/to/repo opencode --description "OpenCode: refactor auth middleware"
gitdirector gd-send gd/myrepo/opencode/1 "continue and run the tests" --enter
```
