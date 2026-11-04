"""Leak-free walk-forward season backtest (raw sim, no Vegas blend).

    python -m validate.wf --season 2025 -n 300 [--weeks 3 18]

For each week W the model is rebuilt from data strictly before W (current season weeks < W plus the previous
season as the prior), every scheduled game of week W is simulated n times, and per-game aggregates are saved to
validate/out/wf_<season>.npz  (scores, team stats, player-sum stats) for validate.report.
2026-only inputs (injury reports, depth charts, snap counts) are masked for earlier seasons so nothing leaks.
"""
import argparse, json, os, random, time
from multiprocessing import Pool
import numpy as np
import pandas as pd

import build_model
from engine import Engine, PSTATS, TSTATS, PIDX, env_factors

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
E = None
_orig_read = pd.read_parquet


def mask_future(season):
    """Block files that only exist for 2026 (or that would leak 2026 state) when backtesting an earlier season."""
    def rp(path, *a, **k):
        p = str(path)
        if season < 2026 and any(s in p for s in ("injuries_2026", "depth_charts_2026", "snap_counts_2026")):
            raise FileNotFoundError(p)
        if season < 2026 and "roster_2025" in p:
            p = p.replace("roster_2025", f"roster_{season - 1}")   # prior-season roster
        elif season < 2026 and "roster_2026" in p:
            p = p.replace("roster_2026", f"roster_{season}")       # same-season roster (end-of-season team: minor leak for traded players)
        return _orig_read(p, *a, **k)
    pd.read_parquet = rp


TUNE = None


def _init(model, tune=None):
    global E
    E = Engine(model, tune)


SUM = ["pa", "pc", "pyd", "ptd", "int", "sk", "ra", "ryd", "rtd", "fga", "fgm", "fl"]


def _game(args):
    home, away, n, seed, roof = args
    env = env_factors(roof=roof)
    H, A = E.team(home), E.team(away)
    av = random.Random(seed * 7 + 11)
    sc = np.zeros((n, 2), np.int16); ts = np.zeros((n, 2, len(TSTATS)), np.int16); ot = np.zeros(n, bool)
    sm = np.zeros((n, 2, len(SUM)), np.int16)
    six = [PIDX[k] for k in SUM]
    for g in range(n):
        h_, a_ = E.team_draw(home, {}, av), E.team_draw(away, {}, av)
        r = E.game(h_, a_, seed=seed * 7919 + g, env=env)
        sc[g] = r["score"]; ot[g] = r["ot"]; ts[g] = r["ts"]
        for pid, v in r["ps"].items():
            side = 0 if pid in h_.players else 1 if pid in a_.players else None
            if side is not None:
                for j, ix in enumerate(six):
                    sm[g, side, j] += v[ix]
    return sc, ts, ot, sm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2025); ap.add_argument("-n", type=int, default=300)
    ap.add_argument("--weeks", type=int, nargs=2, default=[3, 18]); ap.add_argument("--tag", default=""); ap.add_argument("--tune", default="{}", help="JSON of Engine tune overrides")
    a = ap.parse_args()
    S = a.season; tune = json.loads(a.tune) or None
    build_model.CUR, build_model.PREV = f"data/pbp_{S}.parquet", f"data/pbp_{S - 1}.parquet"
    mask_future(S)
    gl = pd.read_csv("data/games.csv"); gl = gl[(gl.season == S) & (gl.game_type == "REG")]
    os.makedirs(OUT, exist_ok=True)
    rows, SC, TS, OT, SM = [], [], [], [], []
    t0 = time.time()
    for wk in range(a.weeks[0], a.weeks[1] + 1):
        W = gl[gl.week == wk]
        if W.empty:
            continue
        MV = os.environ.get("MODELV", ""); mp = f"{OUT}/models{MV}/{S}_{wk}.json"
        if os.path.exists(mp):
            M = json.load(open(mp))                          # cached walk-forward model (the engine knobs do not change it)
        else:
            build_model.RNG = np.random.default_rng(7)      # deterministic model build regardless of call order
            M = build_model.main(cutoff=wk, write=False, quiet=True)
            os.makedirs(f"{OUT}/models{MV}", exist_ok=True); json.dump(M, open(mp, "w"), separators=(",", ":"))
        jobs = [(r.home_team, r.away_team, a.n, wk * 100 + i, r.roof) for i, r in enumerate(W.itertuples())]
        with Pool(os.cpu_count() or 1, initializer=_init, initargs=(M, tune)) as pool:
            res = pool.map(_game, jobs)
        for r, (sc, ts, ot, sm) in zip(W.itertuples(), res):
            rows.append({"week": wk, "home": r.home_team, "away": r.away_team, "hs": r.home_score, "as": r.away_score, "spread": r.spread_line,
                         "total": r.total_line, "ml_h": r.home_moneyline, "ml_a": r.away_moneyline, "roof": r.roof, "wind": r.wind, "ot": r.overtime})
            SC.append(sc); TS.append(ts); OT.append(ot); SM.append(sm)
        print(f"{S} week {wk} ({len(W)} games)  {time.time() - t0:.0f}s", flush=True)
    pd.DataFrame(rows).to_csv(f"{OUT}/wf_{S}{a.tag}_games.csv", index=False)
    np.savez_compressed(f"{OUT}/wf_{S}{a.tag}.npz", sc=np.stack(SC), ts=np.stack(TS), ot=np.stack(OT), sm=np.stack(SM))
    print("saved", f"{OUT}/wf_{S}{a.tag}.npz")


if __name__ == "__main__":
    main()
