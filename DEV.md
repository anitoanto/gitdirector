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

`clean` walks the whole project — root, `src/`, `tests/`, every subfolder —
removing `__pycache__`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.nox`,
`.tox`, `htmlcov`, `build/`, `dist/`, `*.egg-info`, `.coverage*`, and stray
`.pyc`/`.DS_Store` files. It never descends into `.venv` or `.git`, so it cannot
break your environment.

## Test suite

`pytest` defaults (in `pyproject.toml`) run the suite across all cores with
coverage, in a **randomized order** with a per-test timeout.

Order is randomized on purpose. The suite runs under `-n auto`, which already
hands tests to workers in a different grouping every run, so any state leaking
between tests used to surface as unreproducible CI failures. `pytest-randomly`
prints the seed it used; replay a failure with `--randomly-seed=<seed>`, or pin
the original order with `-p no:randomly`.

Two rules keep it stable — see `tests/_timeouts.py`:

- **Never share mutable fixture data.** Anything handed to the app may be
  mutated in place and leak into later tests. Shared sample data is exposed as
  read-only mapping proxies plus a helper that returns fresh copies.
- **Size sync timeouts for the worst machine, and always assert them.**
  They are deadlock backstops, not speed assertions. `Event.wait` returns
  `False` on timeout instead of raising, so an unchecked wait lets a test
  continue as if it had synchronized.

Tests needing a real tmux server are skipped when `tmux` is absent and
serialized behind a file lock.

## Sessions tab status

How the Sessions tab derives each status, refreshed every 3 seconds:

```text
statuses = tmux list-panes for all gd/* sessions
for each session:
    command     = foreground command resolved from pane pid + process tree
    bell        = monitor saw a tmux %bell event
    last_change = last time visible pane content changed

    if bell:                                          waiting
    elif pane is dead:                                idle
    elif command is a plain shell (zsh/bash/sh/...):  idle
    elif purpose is an agent (opencode/claude/copilot/codex/pi)
         and command matches that agent
         and now - last_change >= 10s:                idle
    else:                                             running
```

- `waiting` outranks every other state.
- Agent idleness uses visible pane-content changes, not raw tmux output events.
- Agent sessions prefer the real agent process over helper children like `node`.
- Background refreshes update status cells in place and never reorder rows.

## Token counting

`gitdirector info` counts tokens with `tiktoken` using `cl100k_base` — the same
encoding as OpenAI's `text-embedding-3-*` and `text-embedding-ada-002`.
Special-token-like strings such as `<|endoftext|>` are counted as ordinary text
so counting never fails on source content.

## Release

1. Bump `version` in `pyproject.toml`, then `uv sync`.
2. Run lint, format check, and tests.
3. Merge to `main`.

CI runs the shared checks, compares the version against PyPI, and — if that
version is not yet released — publishes it, then creates the `v<version>` tag
and a GitHub release with auto-generated notes and the built sdist/wheel
attached.

Never create the tag yourself. It is made by the release job at the exact
commit that was published, and only after PyPI succeeds, so a failed build
cannot leave a tag behind. Re-running a workflow whose tag already exists is
a no-op rather than an error.
