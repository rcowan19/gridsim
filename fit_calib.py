"""Fit player-projection calibration on backtest_players.csv (train weeks -> test weeks, then refit on all)."""
import itertools, json, sys
import numpy as np, pandas as pd
import calib

p = pd.read_csv("backtest_players.csv")
p = p[p.pred.notna() & p.played & (p.pred >= 3)].copy()

def metrics(d, c):
    m, lo, hi = calib.apply(d.pred, d.p10, d.p90, d.base, d.n, c)
    return np.abs(m - d.act).mean(), np.corrcoef(m, d.act)[0, 1], ((d.act >= lo) & (d.act <= hi)).mean()

def fit(d):
    best = None
    for mu, lam, A, k in itertools.product([6, 9, 12], np.arange(.4, 1.01, .05), np.arange(0, 1.01, .1), [.5, 1, 2, 4, 8]):
        c = {"mu": mu, "lam": float(lam), "A": float(A), "k": float(k), "s": 1.0}
        mae = metrics(d, c)[0]
        if best is None or mae < best[0]: best = (mae, c)
    c = best[1]
    for s in np.arange(.8, 3.01, .05):          # widen ranges to 80% coverage
        c["s"] = float(s)
        if metrics(d, c)[2] >= .80: break
    return c

split = int(sys.argv[1]) if len(sys.argv) > 1 else 5
tr, te = p[p.week <= split], p[p.week > split]
c = fit(tr)
print(f"train weeks <= {split} (n={len(tr)}), test weeks > {split} (n={len(te)})\nfitted: {c}")
raw = {"mu": 9, "lam": 1, "A": 0, "k": 4, "s": 1}
base_mae = np.abs(te.base - te.act)[te.base.notna()].mean()
for name, cc in [("raw sim", raw), ("calibrated", c)]:
    mae, r, cov = metrics(te, cc)
    print(f"TEST {name:11s} MAE {mae:.2f}  corr {r:.3f}  80% coverage {cov:.1%}")
print(f"TEST trailing-avg baseline MAE {base_mae:.2f}  corr {np.corrcoef(te.base[te.base.notna()], te.act[te.base.notna()])[0,1]:.3f}")
final = fit(p)
json.dump(final, open("calib.json", "w"), indent=1)
print("final (all weeks) written to calib.json:", final)
