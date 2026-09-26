# Dev

This project uses [uv](https://docs.astral.sh/uv/). Every command runs through
`uv run`; never activate `.venv` by hand.

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

## CLI conventions

Every command in `src/gitdirector/commands/` follows the same rules, backed by
the helpers in `commands/__init__.py`:

- **Streams.** Results go to stdout. Errors, prompts, spinners, and the update
  notice go to stderr, so `$(gitdirector ...)` captures only results.
- **Errors.** Raise `CommandError` (exit 1, `Error: headline` plus unwrapped
  detail lines) or `click.UsageError` (exit 2). Wrap tmux calls in
  `tmux_errors()` so a failure reads as one sentence.
- **Tables.** Use `print_rows`: borderless, fitted to the terminal (a path
  column is cut from the left), never truncated when piped.
- **Progress.** Use `run_concurrently`: a stderr spinner that only draws on a
  terminal. Results come back in input order.
- **Machine output.** `--json` on commands a script would read. Keep the keys
  stable.
- **Wording.** Repo status reads like the console's (`↑1 to push · 2 changed`,
  from `status_parts`), and session statuses use its `●`/`○` marks.
- **Startup.** Every Tab press in shell completion runs the CLI, so import
  heavy modules (tmux, Textual, pathspec, urllib) inside the command.

## Screenshots

`gd-screenshot` takes one tmux call (`capture-pane -e -N` chained after a
`display-message` for size and cursor, so both come from the same moment).
`screenshot.py` parses the SGR escapes with rich into a cell grid and draws it
with Pillow, one glyph per cell so the grid holds for any font. It uses Menlo
or DejaVu Sans Mono (else `fc-match monospace`). A character that font lacks
(it would draw the missing-glyph box) comes from `fc-match :charset=<hex>` or
a short list of CJK and symbol fonts. Colours are VS Code's ANSI palette on
the configured theme's background and foreground.

## Test suite

`pytest` runs across all cores with coverage, in a **randomized order**
(`pytest-randomly`) with a per-test timeout, so state leaking between tests
shows up quickly. Replay a failure with the printed `--randomly-seed=<seed>`,
or pin the order with `-p no:randomly`.

`tests/conftest.py` drops `FORCE_COLOR` and similar variables before rich
loads, so CLI output is asserted exactly as CI sees it.

Two rules keep it stable (see `tests/_timeouts.py`):

- **Never share mutable fixture data.** Anything handed to the app may be
  mutated in place and leak into later tests. Shared sample data is exposed as
  read-only mapping proxies plus a helper that returns fresh copies.
- **Size sync timeouts for the worst machine, and always assert them.**
  They are deadlock backstops, not speed assertions. `Event.wait` returns
  `False` on timeout instead of raising, so an unchecked wait lets a test
  continue as if it had synchronized.
- **Wait for a condition, never for time.** `pilot.pause()` only drains
  the message queues: it does not wait for a thread worker, and a screen
  pushed without `await` may not even be mounted yet. Open the Review Diff
  screen with `_open_diff_screen` (awaits the mount, the load worker and
  `_loading`), poll a predicate with a deadline (`_until` in
  `test_sidebar.py`), wait for a repaint with `_wait_for_refresh`, and gate
  a fake slow tmux call on a `threading.Event` the test releases instead of
  a `sleep` that a loaded runner can outlast. Anything that flaked under
  CPU load in the past came from one of these.

Tests needing tmux start their own private server (`TMUX_TMPDIR`), never the
developer's, are skipped without `tmux`, and are serialized behind a file lock.
On top of that, `tests/conftest.py` makes the whole run private: it removes
`$TMUX` and `$TMUX_PANE` (tmux prefers `$TMUX` over `TMUX_TMPDIR`, so a run
started inside tmux would otherwise reach the developer's server) and points
`TMUX_TMPDIR` at a fresh directory. At the end it kills those servers by
explicit socket and hangs up any orphan left running in the run's temp tree:
a pane shell that outlives its server keeps a pty, and enough of them run the
machine out of ptys, after which every tmux spawn fails.

Do the same for any manual experiment: `tmux -S /tmp/<name>.sock` (or
`env -u TMUX` with a private `TMUX_TMPDIR`). Never a bare `tmux kill-server`
from a shell inside tmux: it kills the server that shell runs in.

## Stress test

`stress/` runs gitdirector against a real tmux 3.7c on Linux, in Docker, as a
non-root user with zsh login shells (like a Mac):

```bash
cd stress && docker compose build && docker compose run --rm stress
```

It runs the test suite first, then one phase per process (`STRESS_PHASES`,
`STRESS_SECONDS` each):

- `normal`: concurrent sessions, descriptions, agent launches, panels, decks
  with the real sidebar and attached clients, and the monitor.
- `ptystarve`: the same with `/dev/pts` capped just above what is in use, so
  pane spawns fail the way they do on a Mac out of ptys.
- `nproc`: the same with the tmux server under a tight process limit.
- `chaos`: the same while pane programs and clients are killed, panes
  respawned and windows resized underneath.

A phase passes only if the tmux server never dies, no pane is left broken
(`#{pane_pid}` -1), every resource failure reaches the user explained, and
killing the server leaves no process and no pty behind. Reports land in
`stress/out/<run>-<phase>/`.

**Diff viewer benchmark.** `stress/bench_diff.py` builds a real repository
with a massive uncommitted diff (800 files, about +30000/-12000 lines by
default; `--files`, `--adds`, `--dels`) and times the Review Diff screen
headlessly: the load, each keypress through the file list, paging, the jump
to the last file, and scrolling the diff. It runs on its own or in the
container (`BENCH_FILES` and friends override the sizes):

```bash
uv run python stress/bench_diff.py --files 800
cd stress && docker compose run --rm stress bash /stress/bench.sh   # -> out/bench-diff.json
```

The file list is one `OptionList` that draws only the rows on screen; the
earlier widget-per-file list took minutes to mount and reflowed on every
keypress at that size. Each file's rendered diff is cached per width.

## Sessions and panels in tmux

**Attaching.** The console, `gitdirector cd`, and the Sessions tab open a
repository session in a *deck* beside the session sidebar (below), or attach
a tmux client straight to it with `sidebar: false`; detaching (`prefix d`)
returns to the console. Every `gd/<repo>_<id>/<purpose>/<N>` session carries
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

**tmux 3.7c crashes worked around.** Found by the stress test (below); each
took every session down:

- A window of a session group closing on its own (its program exits or is
  killed) can segfault the server: `server_kill_window` destroys the group
  while still walking the session list. Every work session's window therefore
  has `remain-on-exit on` and a `pane-died` hook that removes the session and
  its views with `kill-session -g` (`guard_session_window`), and removing a
  work session always takes its views (`kill_tmux_session`). Never
  `kill-pane`/`kill-window` a window that views share.
- `new-session` attaching a client whose terminal has just gone exits the
  server (`fatal: tcgetattr failed`). Views are created detached, then
  attached with `attach-session`, which only fails (`view_attach_command`).
- Re-respawning a pane whose respawn failed to fork segfaults the server:
  every respawn goes through `respawn_pane`, which never retries.

**Resizing.** tmux keeps pane sizes roughly on a window resize but lets the
ratios drift. When a panel is laid out, `_panel_resize_commands` turns its
tmux layout tree into `resize-pane -x/-y N%` commands (every split's children
but the last, top down) installed as the window's `window-resized` hook, so
proportions are restored inside tmux the moment the window changes size.
`tests/tmux/test_panel_resize.py` checks every layout against the exact
layout at several sizes.

**Decks (the session sidebar).** `attach_tmux_session` hands every
`gd/<repo>/<purpose>/<N>` session to `integrations/tmux/deck.py`, which builds
a `gd/deck/<pid>-<hex>` session for that one client. Its window has two
panes: the sidebar (`python -m gitdirector.commands.tui.sidebar <deck>`, a
Textual app) and the main pane, a nested tmux client (`tmux -S <socket>`) on
a view of the shown session, exactly like a panel slot. Showing another
session creates a new view and runs `switch-client -c <main pane tty>`; the
old view loses its client and tmux destroys it. The deck's session options
record the panes and the shown session (`@gd_deck_main`, `@gd_deck_sidebar`,
`@gd_deck_target`). Its status line has no badge or label: the deck's keys sit
on the left, drawn with the live `#{prefix}` (in full from 90 columns, a shorter
form from 64, none below), and the clock on the right.
On tmux 3.6+ the divider between sidebar and session is drawn as spaces on the
terminal background, so it disappears; older tmux keeps a heavy line.

The sidebar owns the deck. Every 0.3 s it reads the deck in one tmux call
(`read_deck_state`) and repairs it: a shown session gone from
`list-sessions` gets a "session ended" message in the main pane and focus
moves to the sidebar (checked by name, because a killed session's window
lives on in the view until the view goes); a main pane whose client exited
(its command falls back to `sleep`) gets a message too; a closed main pane is
recreated; and when no repository session is left the deck is closed, which
returns the client to the console. Statuses come from its own `TmuxMonitor`,
the console's being paused while it is attached. A sample carries the deck's
focus and shown session as of when it was read, so one begun before a click,
while the mouse button is held (tmux makes the sidebar the active pane on any
press, until the sidebar hands focus back), or while a session is still being
shown or focused is applied without them: otherwise the highlight lit up
under the pressed button and the shown marker flashed back for a frame. A
release the sidebar never sees (the button let go over another pane) stops
counting as held after a second. A click or Enter goes straight from
`SessionList.action_select` to `open_session`, not through an
`OptionSelected` message, so the highlight, the dimmed look and the marker
land in the same frame.

`prefix Tab` (unbound in tmux by default) and `prefix b` are rebound as
`if-shell -F '#{m:gd/deck/*,...}' <deck command> <original>`, so outside a
deck they do whatever they did before; the original is kept in
`@gd_prefix_original_<key>` so wrapping is
idempotent, and a gitdirector binding is never saved as the original.
`prefix b` is one binding for decks and panels: `display-panes` in a panel,
the sidebar toggle in a deck. `prefix Tab` toggles
focus, or splits a new sidebar in from `@gd_deck_respawn_sidebar` when it
was closed; `prefix b` sends `b` to the sidebar, which flips the global
`@gd_sidebar_collapsed` and resizes itself. The window's `window-resized`
hook applies the width format (32 columns, a third of narrow windows, 5 when
collapsed), and the sidebar renders as a rail of status dots under 14.

The mouse wheel in copy mode (`WheelUpPane`/`WheelDownPane` in `copy-mode` and
`copy-mode-vi`) is wrapped the same way, on `#{m:gd/*,#{session_name}}`: one
line per notch in GitDirector sessions instead of tmux's five, the original
kept in `@gd_original_<table>_<key>`. list-keys prints `\;` between commands,
which a command string reads as an argument, so a kept original is turned
back into ` ; ` before it is rebound. The console and the sidebar scroll one
row per notch too (`scroll_sensitivity_y`).

Opening is one frame change, from the console to the finished deck. The
console builds the deck before it suspends (`prepare_attach`), at the
client's exact size less the status line, so attaching resizes nothing; the
sidebar pane comes first so the main pane's client attaches once, at its
final width. tmux paints the sidebar pane in Textual's `$surface` before the
app starts, and the app keeps its widgets hidden until the monitor's first
sample is in, then shows them all at once.

Coming back is one frame change too. The attach client runs with
`TERM=<term>-gdscreen`, a copy of the terminal's entry compiled once into
`~/.gitdirector/cache/terminfo` (`attach_client_env`) without `smcup`/`rmcup`, so
tmux never switches the terminal back to the shell's screen. Its `rmkx`, which
tmux sends only on the way out and just before it clears the screen and prints
`[detached ...]`, also starts synchronized output (`?2026h`): the deck's last
frame stays up while the console writes its captured frame over it
(`_attach_while_suspended`), and the hold ends after the first refresh
(`_release_held_frame`). To check, record a pty's output across a detach:
there must be no `?1049l` and nothing drawn between the `?2026h` and the frame.

A deck gets `destroy-unattached` only once its client is on it, so it dies
when the client detaches; `reap_stale_decks` removes any a crash left
unattached. From inside tmux the client switches in, and closing the deck
switches it back to `@gd_deck_return`: `detach-on-destroy previous` means
the previous session alphabetically, not the one the client came from.

**Respawning panes.** Every `respawn-pane` goes through `core.respawn_pane`.
When a respawn cannot fork (no free pty, process limit), tmux before 3.8
leaves the pane with `#{pane_pid}` `-1` and no input context, and the next
`respawn-pane` on it segfaults the server, taking every session with it
(`input_free < spawn_pane < cmd_respawn_pane_exec` in
`~/Library/Logs/DiagnosticReports/tmux-*.ips`). So a failed respawn is never
retried: its pane is killed, and a pane already at pid `-1` is killed instead
of respawned. A killed deck main pane is recreated by the sidebar; a panel
rebuild fails and keeps the old panel. Splits may retry a fork failure, since
tmux discards a pane it could not spawn. A panel rebuild also kills any
`gd/build/*-<pid>` or `*_orphaned-<pid>-*` session whose process is gone.

**Launch directory.** A tmux server keeps the working directory of the
client that forked it, and `tmux list-clients` leads from any session to the
attached client and its parent. Every tmux client therefore runs from the
home directory, and `console`, `cd`, and `panel` re-exec themselves there
without `PWD`/`OLDPWD` (`launch_context.py`).

## Sessions tab status

### Agent-reported status

Four statuses, one meaning each: `running` (the agent is working), `waiting`
(it needs a human to act), `pending` (the agent is at its prompt while
subagents it started still work), `idle` (it is doing nothing). An agent with lifecycle hooks reports its own, and the monitor
trusts it instead of watching the pane (no captures, no heuristics). The
protocol is a tmux pane option, `@gitdirector_agent_state`, holding
`running`, `waiting`, `pending`, or `idle`, optionally followed by the
epoch of the report (`running 1788714352.120`) and, on a `waiting`, by
`approval` when a tool waits for permission (see below). OpenCode reports
`pending` itself; for Claude Code the monitor derives it (below).
It is pane scoped because a session shown in a panel is also reachable
through the panel's grouped view session, and a session option set through
`$TMUX_PANE` lands on whichever of the two tmux picks. `list-panes` reads it
back for free every second.

Both integrations are inline: they ride on the command GitDirector launches,
never touch the user's configuration, and write nothing to disk. Everything
they keep is in pane options, which go away with the pane.

**Claude Code.** The launch command carries `--settings '{"hooks": ...}'`
(Claude merges it with the user's settings for that process only). Every
event in `CLAUDE_STATUS_EVENTS` runs one shipped script,
`src/gitdirector/integrations/claude_status.py`, as `python -I -S` (about
20 ms, one tmux call per event, prints nothing, always exits 0). Main-thread
events:

| Hook | Status |
| --- | --- |
| `SessionStart`, `Stop`, `StopFailure`, `PostCompact` (manual) | `idle` |
| `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `PermissionDenied` (auto mode denied; Claude carries on), `ElicitationResult`, `PreCompact`, `PostCompact` (auto) | `running` |
| `PermissionRequest` for a tool | `waiting <epoch> approval` |
| `PermissionRequest` for `AskUserQuestion` or `ExitPlanMode` (a question, not an approval), `Elicitation` | `waiting` |
| `SessionEnd` | options cleared |

Events from subagents carry an `agent_id` and an `agent_type`, and keep
firing after the main turn has ended (a background subagent's tool calls),
so they never touch the main state. They keep two pane options instead,
each changed inside tmux (`if-shell -F`, `set-option -a`, `set-option -F`
with `#{s/…//:…}`) so concurrent hooks cannot race:

- `@gitdirector_agent_waiter` = `<agent id> <epoch>` (plus `approval`, as
  above) on a subagent's `PermissionRequest` or `Elicitation`; that
  subagent's next tool result or its `SubagentStop` clears it, and so does a
  new prompt.
- `@gitdirector_agent_helpers` = `` <id> <id>`` of the subagents still
  working. No hook marks a subagent's start (`SubagentStart` never fires in
  2.1.282), so a subagent is listed on its first `PreToolUse`, which Claude
  waits for and so lands before its `SubagentStop`, and removed on the
  `PostToolUse` of its `SubagentHandback` (its last tool call) or on its
  `SubagentStop`, whichever gets through first. Claude Code's own helper
  sends a lone `SubagentStop` with an empty `agent_type` and is never
  listed. `SessionStart` and `SessionEnd` clear the list; a new prompt does
  not (background subagents keep going).

`Notification` is not used: most of its types (`agent_completed`,
`elicitation_response`, `auth_success`, ...) are not about the user being
needed, and `PermissionRequest` already reports the ones that are.

Two transitions fire no hook; the monitor fills them from Claude's own
records (`resolve_agent_status` in `monitor.py`):

- **Escape**, during a turn or at a prompt (and "No" at a prompt): Claude
  writes a `[Request interrupted by user...]` user entry to the transcript at
  once. The hooks store the transcript path in `@gitdirector_agent_transcript`;
  while the report is `running` or `waiting`, the monitor reads the file's
  last 64 KB whenever its size or mtime changes, and an interrupt newer than
  the report means `idle`.
- **Approving a prompt**: nothing fires until the tool finishes. The approved
  tool runs as a new child process of Claude (the Bash tool starts a shell),
  so a `waiting … approval` whose agent started a direct child process that
  has lived at least 2 s since the report is `running` (`caffeinate`, which
  Claude keeps running while it works, is ignored). Only `approval` waits
  are checked, and only Claude's own children count: a background task
  keeps spawning processes of its own (grandchildren), which used to flip an
  open question to `running`. Answering a question fires `PostToolUse` at
  once, so it needs no inference.

The monitor then turns an `idle` into `pending` while
`@gitdirector_agent_helpers` lists a subagent that is still working. The
list is kept by best-effort hooks (one seen live never took effect and left
a finished subagent listed), so the monitor never trusts it alone: a
subagent's transcript is `<session>/subagents/agent-<id>.jsonl` beside the
session's own (`_has_live_helper`), and a listed subagent counts as gone
when that file is missing, when its last `assistant` entry carries
`stop_reason: end_turn` (a subagent runs one turn, so that is its final
message; the tail is re-read only when the file changes), or when it has
been quiet for 2 minutes (a killed subagent never sends its
`SubagentStop`; a working one writes at every tool call). `running` and
`waiting` always win over `pending`. Background shells are not counted: a
dev server never finishes. A finished background task or subagent comes
back as a queued message, which fires `UserPromptSubmit` then `Stop`, so it
needs no special case. Verified live against Claude Code 2.1.282, in
default and auto mode, with the hand-back arriving both at the prompt and
mid-turn.

**OpenCode.** The launch entry sets `OPENCODE_CONFIG_CONTENT` to a config
that adds one plugin, `src/gitdirector/integrations/opencode_status.js`
(shipped in the wheel). OpenCode merges that JSON with the user's own
config in memory. The plugin subscribes to OpenCode's event bus and reports
the most urgent state across every session the process holds:

| Event | Effect |
| --- | --- |
| `permission.asked`, `question.asked` | the request is pending |
| `permission.replied`, `question.replied`, `question.rejected` | the request is no longer pending |
| `session.created`, `session.updated` with `info.parentID` | the session is a subagent (a child session) |
| `session.status` `busy` or `retry` | the session has a turn in progress |
| `session.status` `idle`, `session.idle`, `session.error`, `session.deleted` | the session is no longer busy, and its pending requests are dropped |

After every event the reported state is `waiting` if any request is pending,
else `running` if a top-level session is busy, else `pending` if only
subagents are busy, else `idle`; the option is only rewritten when that
state changes. A subagent normally blocks its parent (`running`); with
background subagents (`OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS`, or
detaching a running one) the parent returns to its prompt while the child
works, which is `pending`. Verified live against OpenCode 1.18.32;
`tests/test_opencode_status.py` replays the recorded events through the
plugin with Node. OpenCode reports an interrupted turn as
idle itself. `opencode --pure` disables external plugins and therefore this
reporting.

A report whose pane is back at a shell prompt (the agent exited without
saying so) is ignored. Agents started from the console or with `--agent` (`cd`, `gd-tmux`)
carry the hooks; one started as a plain command (`gd-tmux repo claude`, or by
hand) has none and is classified like any other program.

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
at prompt    the tty's foreground process group is led by an interactive
             shell (a shell name followed only by options: no -c string,
             no script), i.e. no job the user started holds the terminal
changed      visible pane content differs from the last capture, ignoring
             a one-cell flip that restores the previous frame (a program
             drawing its own blinking cursor) and the redraw within 1.5 s
             of the pane being resized (a client attaching at another size)
cpu          the process tree burned >= 0.5 s of CPU within the last 3 s
             (a lone housekeeping burst from an idle agent does not count)
bell         tmux's window bell flag is set (tmux only raises it while no
             client is attached to that session, so the monitor never
             attaches one); the monitor that sees it clears it with
             `kill-session -C` (clears alerts, kills nothing) and leaves
             `@gd_bell_at` for every other monitor, since a session shown
             only through a deck or panel view never has it cleared by
             tmux; the status clears on a real content change >= 1 s later
             or on attach

if pane is dead:                                        idle
elif bell:                                              waiting
elif at prompt:                                         idle
elif changed < 4 s ago or cpu < 4 s ago:                running
else:                                                   idle
```

- `waiting` needs a sign that a human is needed, and the bell is the only one
  every terminal program can give; a program that is merely quiet (an agent
  at its prompt, an editor, a dev server) is `idle`.
- A shell at its prompt is idle whatever happens on screen: drawing the
  prompt, typing, redraws, and the output of a command that already
  finished are not work. With job control every command the user runs gets
  a process group of its own and the terminal; what runs in the shell's own
  group (profile scripts, prompt helpers such as oh-my-posh or a completion
  script, command substitutions) is the shell preparing its prompt, and a
  background job (gitstatusd, `cmd &`) never holds the terminal. A shell
  script or an agent started through `sh -c` is not interactive, so it is
  watched like any other program.
- The first sample seeds the "last change" time from tmux's own
  `window_activity` stamp, so a long-quiet program classifies correctly
  immediately instead of after a settling period. It never makes a prompt
  running: a new session's stamp is just the moment its prompt appeared.

## Token counting

`gitdirector info` counts tokens with `tiktoken` using `cl100k_base`, the same
encoding as OpenAI's `text-embedding-3-*` and `text-embedding-ada-002`.
Special-token-like strings such as `<|endoftext|>` are counted as ordinary text
so counting never fails on source content.

## Release

1. Bump `version` in `pyproject.toml`, then `uv sync`.
2. Run lint, format check, and tests.
3. Merge to `main`.

CI (`.github/workflows/main.yml`) runs the shared checks from `checks.yml`
(`ruff format --check`, `ruff check`, and the test suite on Python 3.10
through 3.14), then compares the version against PyPI and, if that version
is not yet released, builds and publishes it, then creates the `v<version>`
tag and a GitHub release with auto-generated notes and the built sdist/wheel
attached. Pull requests run only the checks.

Never create the tag yourself. It is made by the release job at the exact
commit that was published, and only after PyPI succeeds, so a failed build
cannot leave a tag behind. Re-running a workflow whose tag already exists is
a no-op rather than an error.
