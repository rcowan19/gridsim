"""Compact metrics for one walk-forward run (used by grid searches):  python -m validate.metrics 2025 [tag]"""
import math, sys, os
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score
from engine import TSTATS
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
cdf = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))


def metrics(season, tag=""):
    G = pd.read_csv(f"{OUT}/wf_{season}{tag}_games.csv"); Z = np.load(f"{OUT}/wf_{season}{tag}.npz")
    sc, ts, ot = Z["sc"].astype(float), Z["ts"].astype(float), Z["ot"]
    am = (G.hs - G["as"]).values; sm = sc[:, :, 0] - sc[:, :, 1]
    ph = ((sm > 0) + 0.5 * (sm == 0)).mean(1); y = (am > 0) + 0.5 * (am == 0)
    sp = np.r_[G.hs.values, G["as"].values]; ss = np.r_[sc[:, :, 0].ravel(), sc[:, :, 1].ravel()]
    tot = sc.sum(2); at = (G.hs + G["as"]).values
    T = {k: i for i, k in enumerate(TSTATS)}
    pc = np.clip(ph, 1e-4, 1 - 1e-4)
    return {"brier": ((ph - y) ** 2).mean(), "logloss": -(y * np.log(pc) + (1 - y) * np.log(1 - pc)).mean(), "auc": roc_auc_score((am > 0).astype(int), ph),
            "mae_margin": np.abs(sm.mean(1) - am).mean(), "mae_total": np.abs(tot.mean(1) - at).mean(), "sd_pts": ss.std() / sp.std(), "sd_tot": tot.std() / at.std(),
            "sd_marg": sm.std() / am.std(), "hfa": sm.mean() - am.mean(), "pts": ss.mean() - sp.mean(), "ot": ot.mean() - G.ot.mean(), "tie": (sm == 0).mean() - (am == 0).mean(),
            "plays": ts[:, :, :, T["plays"]].mean() - 0, "yds": ts[:, :, :, T["yds"]].mean()}


if __name__ == "__main__":
    print({k: round(float(v), 4) for k, v in metrics(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "").items()})
