#!/usr/bin/env bash
# Runs inside the stress container: the console's handoff to a session and back.
set -uo pipefail

OUT=/out
mkdir -p "$OUT"

echo "== $(tmux -V) on $(uname -srm)"
rsync -a --delete \
  --exclude .venv --exclude .git --exclude __pycache__ --exclude .pytest_cache \
  --exclude htmlcov --exclude .coverage --exclude .nox --exclude dist --exclude stress/out \
  /src/ "$HOME/gd/"
(cd "$HOME/gd" && uv sync -q) || exit 1

cd "$HOME/gd" && uv run python stress/handoff.py --out "$OUT/handoff.bin"
