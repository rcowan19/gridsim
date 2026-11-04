"""Blend simulated scores with betting lines by exponential tilting.

The simulated games are reweighted (w_g ∝ exp(a·home_pts + b·away_pts)) so the weighted mean team scores equal
    target = sim_mean + beta · (vegas_implied − sim_mean),
which moves every player stat consistently with the game script. Vegas implied scores come from the spread and total
(nflverse convention: spread_line > 0 means the home team is favored).
"""
import numpy as np

BETA = 0.9   # market blend: validated on 2023-25 walk-forward (Brier falls monotonically to 1.0 = the market itself; .9 keeps a little of the sim). Was .8 from 2026 wks 3-8 (in-sample).
ELO_BETA = 0.7   # no-line fallback: tilt toward the Elo margin (2025 held-out Brier .2352 -> .2272; 2024 .2219 -> .2094)


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


_ELO = None


def elo_ratings(season=2026, K=20, hfa=48):
    """Elo ratings after every played game of the last 4 seasons (538-style margin multiplier, 1/3 regression to the mean each year)."""
    global _ELO
    if _ELO is not None:
        return _ELO
    import math
    import pandas as pd
    gl = pd.read_csv("data/games.csv")
    gl = gl[(gl.season >= season - 3) & (gl.season <= season) & gl.home_score.notna()].sort_values(["season", "week", "gameday"])
    R, cur = {}, None
    for r in gl.itertuples():
        if r.season != cur:
            if cur is not None:
                R = {t: 1505 + (x - 1505) * (2 / 3) for t, x in R.items()}
            cur = r.season
        rh, ra = R.get(r.home_team, 1505), R.get(r.away_team, 1505)
        p = 1 / (1 + 10 ** (-(rh - ra + hfa) / 400))
        res = r.home_score - r.away_score
        w = 1.0 if res > 0 else 0.0 if res < 0 else .5
        d = K * math.log(abs(res) + 1) * (2.2 / ((rh - ra) * .001 * (1 if res > 0 else -1) + 2.2)) * (w - p)
        R[r.home_team], R[r.away_team] = rh + d, ra - d
    _ELO = R
    return R


def reset():
    global _ELO
    _ELO = None


def elo_margin(home, away, neutral=False, hfa=48):
    """Expected home margin in points from Elo (25 Elo points ~ 1 point)."""
    R = elo_ratings()
    return (R.get(home, 1505) - R.get(away, 1505) + (0 if neutral else hfa)) / 25.0
