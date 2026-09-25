#!/usr/bin/env bash
# Runs as root in the stress container: repeat a phase until tmux crashes and
# print the core's backtrace. core_pattern belongs to Docker's Linux VM.
set -uo pipefail
PHASE="${1:-chaos}"; TRIES="${2:-10}"
echo '/out/core.%e.%p' > /proc/sys/kernel/core_pattern
rm -f /out/core.*
for i in $(seq 1 "$TRIES"); do
  su gd -c "ulimit -c unlimited; STRESS_PHASES=$PHASE SKIP_PYTEST=1 STRESS_SECONDS=${STRESS_SECONDS:-60} bash /stress/run.sh" | tail -1
  core=$(ls /out/core.tmux.* 2>/dev/null | head -1)
  if [[ -n "$core" ]]; then
    echo "== crash on try $i: $core"
    gdb -q -batch -ex "bt full" -ex "info registers" /usr/local/bin/tmux "$core" 2>&1 | head -120
    break
  fi
done
echo core > /proc/sys/kernel/core_pattern
