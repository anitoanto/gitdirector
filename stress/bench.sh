#!/usr/bin/env bash
# Runs inside the stress container: the diff viewer benchmark on Linux.
set -uo pipefail

OUT=/out
mkdir -p "$OUT"

echo "== $(tmux -V) on $(uname -srm), $(nproc) cpus"
rsync -a --delete \
  --exclude .venv --exclude .git --exclude __pycache__ --exclude .pytest_cache \
  --exclude htmlcov --exclude .coverage --exclude .nox --exclude dist --exclude stress/out \
  /src/ "$HOME/gd/"
(cd "$HOME/gd" && uv sync -q) || exit 1

echo "== diff viewer: ${BENCH_FILES:-800} files, +${BENCH_ADDS:-30000} -${BENCH_DELS:-12000}"
cd "$HOME/gd" && uv run python /stress/bench_diff.py \
  --files "${BENCH_FILES:-800}" --adds "${BENCH_ADDS:-30000}" --dels "${BENCH_DELS:-12000}" \
  --keys "${BENCH_KEYS:-20}" --out "$OUT/bench-diff.json"
