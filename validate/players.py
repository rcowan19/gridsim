"""Player-level validation of a backtest_players CSV (PPR, projected >= 3, played):  python -m validate.players validate/out/bt_players_X.csv ...
Raw sim bias/MAE by position, then calibration fitted on weeks <= 5 and scored on weeks > 5 (MAE, corr, 80% coverage)."""
import itertools, sys
import numpy as np, pandas as pd
import calib

R = pd.read_parquet("data/roster_2026.parquet", columns=["gsis_id", "position"]).drop_duplicates("gsis_id")


def run(path):
    p = pd.read_csv(path).merge(R, left_on="pid", right_on="gsis_id", how="left")
    p = p[p.pred.notna() & p.played & (p.pred >= 3) & p.position.isin(["QB", "RB", "WR", "TE"])]
    out = {"n": len(p), "raw_MAE": np.abs(p.pred - p.act).mean(), "raw_corr": np.corrcoef(p.pred, p.act)[0, 1]}
    for q in ("QB", "RB", "WR", "TE"):
        g = p[p.position == q]; out[f"bias_{q}"] = (g.pred - g.act).mean()
    tr, te = p[p.week <= 5], p[p.week > 5]
    best = None
    for mu, lam, A, k in itertools.product([6, 9], np.arange(.4, 1.01, .05), np.arange(0, 1.01, .1), [.5, 1, 2, 4, 8]):
        c = {"mu": mu, "lam": float(lam), "A": float(A), "k": float(k), "s": 1.0}
        m = calib.apply(tr.pred, tr.p10, tr.p90, tr.base, tr.n, c)[0]; mae = np.abs(m - tr.act).mean()
        if best is None or mae < best[0]: best = (mae, c)
    c = best[1]
    for s in np.arange(.8, 3.01, .05):
        c["s"] = float(s); _, lo, hi = calib.apply(tr.pred, tr.p10, tr.p90, tr.base, tr.n, c)
        if ((tr.act >= lo) & (tr.act <= hi)).mean() >= .8: break
    m, lo, hi = calib.apply(te.pred, te.p10, te.p90, te.base, te.n, c)
    out.update({"test_MAE": np.abs(m - te.act).mean(), "test_corr": np.corrcoef(m, te.act)[0, 1], "test_cov": ((te.act >= lo) & (te.act <= hi)).mean(), "base_MAE": np.abs(te.base - te.act)[te.base.notna()].mean()})
    return out


if __name__ == "__main__":
    for f in sys.argv[1:]:
        print(f.split("/")[-1].ljust(24), {k: round(float(v), 3) for k, v in run(f).items()})
