# Dev

This project uses [uv](https://docs.astral.sh/uv/). Every command runs through
`uv run` — never activate `.venv` by hand.

```bash
uv sync                              # set up / update .venv
uv run gitdirector                   # run the CLI
uv run pytest                        # tests
uv run ruff check src/ tests/        # lint
uv run ruff format src/ tests/       # format
uv run ruff format --check src/ tests/
uv run nox -s clean                  # delete caches, coverage, build artifacts
```

`clean` removes caches, coverage output, and build artifacts everywhere in the
project, and never touches `.git`, virtualenvs, or `node_modules`.

## Test suite

`pytest` runs across all cores with coverage, in a **randomized order**
(`pytest-randomly`) with a per-test timeout, so state leaking between tests
shows up quickly. Replay a failure with the printed `--randomly-seed=<seed>`,
or pin the order with `-p no:randomly`.

Two rules keep it stable — see `tests/_timeouts.py`:

- **Never share mutable fixture data.** Anything handed to the app may be
  mutated in place and leak into later tests. Shared sample data is exposed as
  read-only mapping proxies plus a helper that returns fresh copies.
- **Size sync timeouts for the worst machine, and always assert them.**
  They are deadlock backstops, not speed assertions. `Event.wait` returns
  `False` on timeout instead of raising, so an unchecked wait lets a test
  continue as if it had synchronized.

Tests needing tmux start their own private server (`TMUX_TMPDIR`), never the
developer's, are skipped without `tmux`, and are serialized behind a file lock.
Do the same for any manual experiment: `tmux -L <name>` or a private
`TMUX_TMPDIR`.

## Sessions and panels in tmux

**Attaching.** The console, `gitdirector cd`, and the Sessions tab attach a
tmux client straight to the session; detaching (`prefix d`) or the session
ending returns to the console. Every `gd/<repo>_<id>/<purpose>/<N>` session carries
its own look, applied by `sync_panel_tmux_config`: a heavy top border with the
session's label (`pane-border-status top`, also set on new windows through an
`after-new-window` hook), the themed status line, and `detach-on-destroy on`
so an agent exiting always returns the client to the console.

**Panels.** A panel is a tmux session (`gd/panel/<name>`) whose panes each
show one session through a *view*: a session grouped with it
(`new-session -t =<session> -s gd/view/...`). A view shares the session's
windows but has its own session options -- status line off, and `@gd_slot`,
which the session's border format shows as a slot badge. tmux draws a border
for each client with that client's session, so the same session shows its
plain header when attached directly and `N  label` inside panel slot N. The
view is deleted by tmux (`destroy-unattached`) when its pane's client goes
away, and the real session is never modified. `prefix N` selects slot N by
the panes' `@gd_slot` option: tmux numbers panes in layout-tree order, which
is not slot order for every layout.

**Resizing.** tmux keeps pane sizes roughly on a window resize but lets the
ratios drift. When a panel is laid out, `_panel_resize_commands` turns its
tmux layout tree into `resize-pane -x/-y N%` commands (every split's children
but the last, top down) installed as the window's `window-resized` hook, so
proportions are restored inside tmux the moment the window changes size.
`tests/tmux/test_panel_resize.py` checks every layout against the exact
layout at several sizes.

**Launch directory.** A tmux server keeps the working directory of the
client that forked it, and `tmux list-clients` leads from any session to the
attached client and its parent. Every tmux client therefore runs from the
home directory, and `console` and `cd` re-exec themselves there without
`PWD`/`OLDPWD` (`launch_context.py`).

## Sessions tab status

### Agent-reported status

An agent with lifecycle hooks can report its own status, which the monitor
trusts over every heuristic below. The protocol is a tmux pane option,
`@gitdirector_agent_state`, holding `running`, `waiting`, or `idle`,
optionally followed by the epoch second of the report (`running 1788714352`)
so repeated reports of the same state still show when the agent last spoke;
the hook stamps it with `tmux set-option -p -t "$TMUX_PANE" ...`, which works
whether or not GitDirector is running, and `list-panes` reads it back for
free every second. It is pane scoped because a session shown in a panel is
also reachable through the panel's grouped view session, and a session
option set through `$TMUX_PANE` lands on whichever of the two tmux picks. An agent whose hooks leave gaps also sets
`@gitdirector_agent_interrupts unreported`, which enables the reconciliation
described below.

**Claude Code.** Both Claude launch entries pass `--settings '{"hooks": ...}'`
on the command line (Claude merges it with the user's own settings, which
are never modified):

| Hook | State |
| --- | --- |
| `SessionStart`, `Stop` (turn finished, prompt is back), `StopFailure` (turn ended by an API error), `PermissionDenied` | `idle` |
| `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `ElicitationResult` | `running` |
| `PreToolUse` for `AskUserQuestion`, `PermissionRequest`, `Elicitation`, `Notification` (except `idle_prompt`) | `waiting` |
| `SessionEnd` | option cleared |

`SubagentStop` is deliberately unmapped: Claude Code's own helper that
generates the prompt suggestion after a turn fires it while the session is
idle.

Three transitions have no hook, and the monitor settles them from the pane
(`reconcile_agent_report` in `monitor.py`). A static screen alone proves
nothing: Claude Code stops redrawing for stretches of a turn while it waits
on the model, so a reported `running` is trusted however still the pane. An
interrupt (Escape) and an answered prompt both show a keypress after the
last report (tmux's `session_activity`, which only client input moves) with
a redraw right behind it; when that is followed by 5 s without visible
change or CPU, a `running` was interrupted and a `waiting` was dismissed,
so both become `idle`. An answered `waiting` that keeps drawing is `running`
(the approved tool is executing) until `PostToolUse` reports. Finally, a
turn that resumes on its own after a background task finished fires no
`UserPromptSubmit`, so a reported `idle` whose pane keeps producing output
for 5 s with nobody typing into it is shown as `running`; an idle Claude
animates for at most about 3 s at a time.

The hook fragments live in `src/gitdirector/agents.py`.

**OpenCode.** The launch entry sets `OPENCODE_CONFIG_CONTENT` to a config
that adds one plugin, `src/gitdirector/integrations/opencode_status.js`
(shipped in the wheel). OpenCode merges that JSON with the user's own
config. The plugin subscribes to OpenCode's event bus and reports the most
urgent state across every session the process holds:

| Event | Effect |
| --- | --- |
| `permission.asked`, `question.asked` | the request is pending |
| `permission.replied`, `question.replied`, `question.rejected` | the request is no longer pending |
| `session.status` busy | the session has a turn in progress |
| `session.status` not busy, `session.idle`, `session.error`, `session.deleted` | the session is no longer busy, and its pending requests are dropped |

After every event the reported state is `waiting` if any request is pending,
else `running` if any session is busy, else `idle`; the option is only
rewritten when that state changes.

OpenCode reports an interrupted turn as idle itself, so it does not set the
interrupts flag. `opencode --pure` disables external plugins and therefore
this reporting.

An agent started some other way (`gd-tmux repo claude`, or by hand) has no
hooks and falls back to the heuristics.

### Heuristics for everything else

Statuses are agent-agnostic: nothing is keyed on the session's purpose or on
which program is running. `TmuxMonitor` samples every `gd/*` session once a
second; the same sample also carries each session's repo label and
description, so the Sessions tab lists sessions from it (`entries()`) without
a separate `list-sessions` call and repaints only the rows whose status
changed.

Signals gathered per sample (one `tmux list-panes -a` and one `ps` call for
all sessions, plus a `capture-pane` only for panes whose tmux activity stamp
moved):

```text
command      foreground program under the pane (process tree, shallowest
             non-shell process in the tty's foreground process group)
changed      visible pane content differs from the last capture, ignoring
             a one-cell flip that restores the previous frame (a program
             drawing its own blinking cursor)
cpu          the process tree burned >= 0.5 s of CPU within the last 3 s
             (a lone housekeeping burst from an idle agent does not count)
interactive  the pane's tty is in raw mode (the program reads keystrokes);
             tmux's mouse/alternate-screen flags are the fallback
bell         tmux's window bell flag rose (tmux only raises it while no
             client is attached, so the monitor never attaches one);
             cleared by a real content change >= 1 s later or on attach

if pane is dead:                                        idle
elif bell:                                              waiting
elif command is a shell:   running if changed < 2 s ago, else idle
elif changed < 4 s ago or cpu < 4 s ago:                running
elif interactive:                                       waiting
else:                                                   idle
```

- `waiting` means the program is alive and blocked on the user: an agent at
  its prompt or a permission question, an editor, a REPL.
- `idle` means nothing is happening: a shell prompt, or a non-interactive
  program (dev server, build) that has gone quiet.
- The first sample seeds the "last change" time from tmux's own
  `window_activity` stamp, so a long-quiet session classifies correctly
  immediately instead of after a settling period.

## Token counting

`gitdirector info` counts tokens with `tiktoken` using `cl100k_base` — the same
encoding as OpenAI's `text-embedding-3-*` and `text-embedding-ada-002`.
Special-token-like strings such as `<|endoftext|>` are counted as ordinary text
so counting never fails on source content.

## Release

1. Bump `version` in `pyproject.toml`, then `uv sync`.
2. Run lint, format check, and tests.
3. Merge to `main`.

CI (`.github/workflows/main.yml`) runs the shared checks from `checks.yml`
— `ruff format --check`, `ruff check`, and the test suite on Python 3.10
through 3.14 — then compares the version against PyPI and, if that version
is not yet released, builds and publishes it, then creates the `v<version>`
tag and a GitHub release with auto-generated notes and the built sdist/wheel
attached. Pull requests run only the checks.

Never create the tag yourself. It is made by the release job at the exact
commit that was published, and only after PyPI succeeds, so a failed build
cannot leave a tag behind. Re-running a workflow whose tag already exists is
a no-op rather than an error.
