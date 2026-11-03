"""Player-projection calibration: shrink sim means toward the field, blend with trailing average, widen ranges."""
import json, os
import numpy as np

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calib.json")
DEFAULT = {"mu": 9.0, "lam": 1.0, "A": 0.0, "k": 4.0, "s": 1.0}


def load():
    try:
        with open(PATH) as f:
            return {**DEFAULT, **json.load(f)}
    except OSError:
        return dict(DEFAULT)


def apply(sim, p10, p90, avg, n, c=None):
    """All args may be numpy arrays (avg/n may contain NaN). Returns (mean, lo, hi)."""
    c = c or load()
    sim, p10, p90 = (np.asarray(x, float) for x in (sim, p10, p90))
    avg, n = np.asarray(avg, float), np.asarray(n, float)
    s_ = c["mu"] + c["lam"] * (sim - c["mu"])
    ok = ~np.isnan(avg) & ~np.isnan(n)
    w = np.where(ok, c["A"] * np.nan_to_num(n) / (np.nan_to_num(n) + c["k"]), 0.0)
    mean = s_ + w * (np.where(ok, avg, 0.0) - s_)
    sc = c["s"] * c["lam"]
    return mean, mean + sc * (p10 - sim), mean + sc * (p90 - sim)
