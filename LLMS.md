# GitDirector — instructions for AI coding agents

If you are an AI coding agent (Claude Code, OpenCode, GitHub Copilot, Codex, Pi, or any other tool that drives a shell on behalf of a user) and the user asks you to use GitDirector, `gitdirector`, or `gd` for shell sessions, read this file end-to-end before launching those sessions. It is the contract for integrating with the user's GitDirector tmux workflow.

Only use `gitdirector gd-tmux` to launch external shell sessions when the user explicitly mentions using GitDirector, `gitdirector`, or `gd` for that work. Even then, use it only for long-lived processes such as web servers, dev servers, build watchers, file watchers, REPLs, or similar commands that need to keep running. Do not use `gd-tmux` for one-off commands: the session self-destructs when the command exits, so output from a completed one-off session is not retrievable unless the command wrote it somewhere else.

## Launching with `gd-tmux`

- Use `gd-tmux` for long-lived external shell sessions only after the user has asked for GitDirector/GD usage. Do not use it for one-off commands.
- `gd-tmux` creates a detached background tmux session, launches the command, prints the session name, and returns. It does not attach or switch the terminal.
- Use the absolute repository path when possible. By-name lookup matches the directory name verbatim and can be ambiguous when two tracked repos share a basename.
- Quote the command as a single string. It is handed to `sh -lc` inside the new session, so `'echo "hi"'` is the safest pattern for embedded double quotes.
- Always pass `--description`. The tmux session name is intentionally generic (`shell`), so the description is how the user knows what the session is doing in the Sessions tab. Use `AgentName: brief sentence describing what the session is doing`.
- Session names are standardized as `gd/<repo>/shell/<N>`. Do not expect the command or agent name to appear in the tmux session name. `N` is one higher than the highest currently running matching shell session number.
- The session self-destructs when the command exits, whether it succeeds or fails.

Examples:

```bash
gitdirector gd-tmux /path/to/repo opencode --description "OpenCode: refactor auth middleware"
gitdirector gd-tmux /path/to/repo "npm run dev" --description "Vite: frontend dev server on :5173"
gitdirector gd-tmux /path/to/repo "make watch" --description "Watcher: rebuild Go binary on save"
```

## Capturing The Session Name

`gd-tmux` prints only the full session name to stdout, so command substitution can capture it directly:

```bash
SESSION=$(gitdirector gd-tmux /path/to/repo opencode --description "OpenCode: refactor auth")
gitdirector gd-capture "$SESSION" --lines 200
```

## Reading Output With `gd-capture`

- Use `gitdirector gd-capture <session-name>` while the session is still running. Example: `gitdirector gd-capture gd/myrepo/shell/1 --lines 200`.
- Use `--lines N` for the last N lines. Use `--full` for the entire available scrollback.
- `gd-capture` only works for live sessions. Once the command exits, the tmux session and scrollback are gone.
- If output must survive after the command exits, redirect it inside the command itself.

```bash
gitdirector gd-tmux /path/to/repo "make test 2>&1 | tee /tmp/run.log" --description "Tests: make test"
```

To stop a running process, kill the matching session from `gitdirector console` in the Sessions tab, or run `tmux kill-session -t =<session-name>`.

If a command is short-lived and will not block your shell, plain shell is fine. GitDirector is for long-lived processes the user needs to monitor or stop later.
