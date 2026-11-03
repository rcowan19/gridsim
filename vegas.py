"""Blend simulated scores with betting lines by exponential tilting.

The simulated games are reweighted (w_g ∝ exp(a·home_pts + b·away_pts)) so the weighted mean team scores equal
    target = sim_mean + beta · (vegas_implied − sim_mean),
which moves every player stat consistently with the game script. Vegas implied scores come from the spread and total
(nflverse convention: spread_line > 0 means the home team is favored).
"""
import numpy as np

BETA = 0.8   # fitted on the walk-forward backtest (bt_vegas.py, weeks 3-8)


def implied(spread, total):
    if spread is None or total is None or spread != spread or total != total:
        return None
    return total / 2 + spread / 2, total / 2 - spread / 2


def tilt_weights(sc, th, ta, iters=30):
    """Weights over simulated games so that weighted mean of sc[:,0]=th and sc[:,1]=ta."""
    x = sc.astype(float)
    x = x - x.mean(0)
    t = np.array([th, ta]) - sc.mean(0)
    lam = np.zeros(2)
    for _ in range(iters):
        z = x @ lam
        w = np.exp(z - z.max()); w /= w.sum()
        m = w @ x
        g = m - t
        if np.abs(g).max() < 1e-4:
            break
        C = (x - m).T @ ((x - m) * w[:, None]) + 1e-6 * np.eye(2)
        lam -= np.linalg.solve(C, g)
        lam = np.clip(lam, -1.5, 1.5)
    z = x @ lam
    w = np.exp(z - z.max())
    return w / w.sum()


def blend_index(sc, spread, total, beta=BETA, seed=0):
    """Indices that resample the simulated games toward the blended target (identity if no line)."""
    imp = implied(spread, total)
    n = len(sc)
    if imp is None or beta <= 0:
        return np.arange(n), None
    m = sc.mean(0)
    th, ta = m[0] + beta * (imp[0] - m[0]), m[1] + beta * (imp[1] - m[1])
    w = tilt_weights(sc, th, ta)
    rng = np.random.default_rng(seed)
    # systematic resampling keeps noise low
    u = (rng.random() + np.arange(n)) / n
    idx = np.searchsorted(np.cumsum(w), u)
    return np.minimum(idx, n - 1), {"home": round(float(th), 1), "away": round(float(ta), 1), "ess": round(float(1 / (w ** 2).sum()), 0)}
