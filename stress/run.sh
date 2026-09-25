#!/usr/bin/env bash
# Runs inside the stress container: the test suite on Linux, then each phase.
set -uo pipefail

OUT=/out
SECONDS_PER_PHASE="${STRESS_SECONDS:-60}"
PHASES="${STRESS_PHASES:-normal ptystarve nproc chaos}"
mkdir -p "$OUT"

echo "== $(tmux -V) on $(uname -srm), user $(id -un), shell $SHELL"
rsync -a --delete \
  --exclude .venv --exclude .git --exclude __pycache__ --exclude .pytest_cache \
  --exclude htmlcov --exclude .coverage --exclude .nox --exclude dist --exclude stress/out \
  /src/ "$HOME/gd/"
(cd "$HOME/gd" && uv sync -q) || exit 1

if [[ "${SKIP_PYTEST:-0}" != 1 ]]; then
  echo "== pytest on Linux with $(tmux -V)"
  (cd "$HOME/gd" && uv run pytest -q -p no:randomly --no-cov 2>&1 | tail -5) | tee "$OUT/pytest.txt"
fi

status=0
run=0
for phase in $PHASES; do
  run=$((run + 1))
  dir="$OUT/$run-$phase"
  mkdir -p "$dir"
  echo "== stress $phase for ${SECONDS_PER_PHASE}s (run $run)"
  (cd "$HOME/gd" && uv run python /stress/stress.py \
     --phase "$phase" --seconds "$SECONDS_PER_PHASE" --out "$dir") 2>&1 \
    | tee "$dir/$phase.log" | tail -3
  grep -q '"verdict": "PASS"' "$dir/$phase.json" 2>/dev/null || status=1
done
exit $status
