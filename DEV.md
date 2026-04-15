# Dev

## Setup

```bash
uv sync
```

## Run

```bash
uv run gitdirector
```

## Tmux Session Status

Pseudocode for how the Sessions tab determines status:

```text
every 3 seconds:
	statuses = tmux list-panes for all gd/* sessions
	for each session:
		command = resolve effective foreground command from pane pid + process tree
		bell = monitor saw a tmux %bell event for this session
		last_change = last time the visible pane content changed

		if bell:
			state = waiting
		elif pane is dead:
			state = idle
		elif command is a plain shell (zsh/bash/sh/etc):
			state = idle
		elif session purpose is an agent (opencode/claude/copilot/codex)
				 and command matches that agent
				 and now - last_change >= 10 seconds:
			state = idle
		else:
			state = running
```

Notes:

- waiting takes priority over every other state
- agent idle uses visible pane-content changes, not raw tmux output events
- agent sessions prefer the actual agent process over helper children like node
- background status refresh updates status cells in place and does not reorder rows

## Repo Info Tokens

`gitdirector info` computes token counts with `tiktoken` using the `cl100k_base` encoding.

This is the same tokenizer family used by OpenAI embedding models such as `text-embedding-3-small`, `text-embedding-3-large`, and the older `text-embedding-ada-002`.

Special-token-like strings in source files are counted as normal text so token counting does not fail on content like `<|endoftext|>`.

## Tests

```bash
uv run pytest
```

## Format

```bash
uv run black src/ tests/
```

## Lint

```bash
uv run ruff check src/ tests/
```

## Release

1. Bump `version` in `pyproject.toml`
2. Run `uv sync`
3. Merge with main, tag the release with v<version>, GitHub Actions will build and publish to PyPI.
