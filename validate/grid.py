"""Run engine-knob configs on dev seasons and tabulate walk-forward metrics.
    python -m validate.grid "name={json}" "name2={json}" --seasons 2023 2024 [-n 300]
Results are appended to validate/out/grid.csv. ot_both is set per season (2025 rules only apply from 2025)."""
import argparse, json, os, subprocess, sys
import pandas as pd
from validate.metrics import metrics, OUT

ap = argparse.ArgumentParser(); ap.add_argument("cfg", nargs="+"); ap.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024]); ap.add_argument("-n", type=int, default=300)
a = ap.parse_args()
rows = []
for c in a.cfg:
    name, js = c.split("=", 1); t = json.loads(js)
    per = []
    for S in a.seasons:
        tune = dict(t, ot_both=1 if S >= 2025 else 0)
        tag = f"_g_{name}"
        subprocess.run([sys.executable, "-m", "validate.wf", "--season", str(S), "-n", str(a.n), "--tune", json.dumps(tune), "--tag", tag], check=True, capture_output=True)
        per.append(metrics(S, tag))
    avg = {k: sum(p[k] for p in per) / len(per) for k in per[0]}
    row = {"cfg": name, "seasons": "+".join(map(str, a.seasons)), **avg}
    rows.append(row); print({k: (round(v, 4) if not isinstance(v, str) else v) for k, v in row.items()}, flush=True)
    pd.DataFrame([row]).to_csv(f"{OUT}/grid.csv", mode="a", header=not os.path.exists(f"{OUT}/grid.csv"), index=False)
