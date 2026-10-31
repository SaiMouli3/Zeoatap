#!/usr/bin/env bash
# Runs the soak matrix used for RESULTS.md. ~60 s measured per run.
set -euo pipefail
cd "$(dirname "$0")"
DUR=${DUR:-60}
mkdir -p results
for det in ${DETECTORS:-none mock}; do
  for n in 4 8 16; do
    uv run python soak.py --cameras $n --resolution 640x360 --duration $DUR --detector $det --out results/${det}-640x360-n$n.json > /dev/null
  done
  for n in 4 8; do
    uv run python soak.py --cameras $n --resolution 1280x720 --duration $DUR --detector $det --out results/${det}-1280x720-n$n.json > /dev/null
  done
done
# Fault injection: kill a protected camera's publisher 20 s in, unplugged for 8 s.
uv run python soak.py --cameras 8 --resolution 640x360 --duration $DUR --detector mock --fault-at 20 --fault-down-s 8 \
  --out results/fault-mock-640x360-n8.json > /dev/null
uv run python summarize.py results/*.json
