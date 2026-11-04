#!/bin/sh
# One command: full validation suite (~20 min on 2 cores, mostly the three walk-forward seasons; cached models make re-runs ~6 min)
#   sh validate/run_all.sh [N_SIMS_PER_GAME, default 1000]
set -e
cd "$(dirname "$0")/.."
N=${1:-1000}
for S in 2023 2024 2025; do
  # ot_both=1 is the 2025+ overtime rule; earlier seasons were played under the old rule
  if [ "$S" -ge 2025 ]; then T='{"ot_both":1}'; else T='{"ot_both":0}'; fi
  python -m validate.wf --season $S -n $N --tune "$T"
  python -m validate.report --season $S > /dev/null
done
python -m validate.blend_eval
python -m validate.gamestate > /dev/null
python -m pytest -q tests
echo "reports: validate/out/report_20*.md  validate/out/gamestate.md"
