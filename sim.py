"""Monte Carlo runner + summaries + CLI."""
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

from engine import Engine, PSTATS, TSTATS, PIDX, TIDX, ppr_points
import calib

_E = None
_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model.json")


def load_model(path=_MODEL_PATH):
    with open(path) as f:
        return json.load(f)


def _init(tune=None):
    global _E
    _E = Engine(load_model(), tune)


def get_engine(tune=None):
    global _E
    if _E is None:
        _init(tune)
    return _E


def run_chunk(args):
    home, away, n, seed, ov, neutral = args
    e = get_engine()
    H, A = e.team(home, ov), e.team(away, ov)
    pids = sorted(set(H.pids) | set(A.pids))
    ix = {p: i for i, p in enumerate(pids)}
    ps = np.zeros((n, len(pids), len(PSTATS)), dtype=np.int16)
    ts = np.zeros((n, 2, len(TSTATS)), dtype=np.int16)
    sc = np.zeros((n, 2), dtype=np.int16)
    ot = np.zeros(n, dtype=bool)
    for g in range(n):
        r = e.game(H, A, seed=seed * 100003 + g, neutral=neutral)
        sc[g] = r["score"]
        ot[g] = r["ot"]
        ts[g] = r["ts"]
        for pid, v in r["ps"].items():
            if pid in ix:
                ps[g, ix[pid]] = v
    return pids, ps, ts, sc, ot


def simulate(home, away, n=2000, seed=1, overrides=None, neutral=False, pool=None, workers=None):
    ov = overrides or {}
    workers = workers or os.cpu_count() or 1
    k = max(1, min(workers * 2, n // 50 or 1))
    sizes = [n // k + (1 if i < n % k else 0) for i in range(k)]
    jobs = [(home, away, s, seed * 1000 + i, ov, neutral) for i, s in enumerate(sizes) if s]
    if pool is not None:
        parts = pool.map(run_chunk, jobs)
    elif workers > 1:
        with Pool(workers, initializer=_init) as p:
            parts = p.map(run_chunk, jobs)
    else:
        parts = [run_chunk(j) for j in jobs]
    pids = parts[0][0]
    return {"pids": pids, "ps": np.concatenate([p[1] for p in parts]), "ts": np.concatenate([p[2] for p in parts]),
            "sc": np.concatenate([p[3] for p in parts]), "ot": np.concatenate([p[4] for p in parts])}


def _q(a):
    a = np.asarray(a, float)
    return {"mean": round(float(a.mean()), 2), "p10": round(float(np.percentile(a, 10)), 1),
            "p50": round(float(np.percentile(a, 50)), 1), "p90": round(float(np.percentile(a, 90)), 1)}


def summarize(res, home, away, overrides=None):
    e = get_engine()
    ov = overrides or {}
    H, A = e.team(home, ov), e.team(away, ov)
    sc, ts, ps, pids = res["sc"].astype(int), res["ts"], res["ps"], res["pids"]
    n = len(sc)
    hw = float((sc[:, 0] > sc[:, 1]).mean())
    aw = float((sc[:, 1] > sc[:, 0]).mean())
    margin = sc[:, 0] - sc[:, 1]
    total = sc.sum(1)
    out = {"n": n, "home": home, "away": away,
           "win": {"home": round(hw, 4), "away": round(aw, 4), "tie": round(1 - hw - aw, 4)},
           "ot_rate": round(float(res["ot"].mean()), 4),
           "score": {"home": _q(sc[:, 0]), "away": _q(sc[:, 1])},
           "margin": {"mean": round(float(margin.mean()), 2), "median": float(np.median(margin))},
           "total": {"mean": round(float(total.mean()), 2), "median": float(np.median(total))},
           "hist": {"margin": _hist(margin, -50, 50, 2), "total": _hist(total, 0, 100, 2),
                    "home": _hist(sc[:, 0], 0, 60, 1), "away": _hist(sc[:, 1], 0, 60, 1)}}
    out["teamstats"] = {}
    for side, name in ((0, home), (1, away)):
        out["teamstats"][name] = {k: round(float(ts[:, side, TIDX[k]].mean()), 2) for k in TSTATS}
    cover = {}
    out["players"] = {}
    for side, cfg in ((0, H), (1, A)):
        rows = []
        for pid in cfg.pids:
            q = cfg.players[pid]
            i = pids.index(pid)
            a = ps[:, i, :]
            if a.sum() == 0:
                continue
            row = {"id": pid, "name": q["name"], "pos": q.get("pos", "?"), "team": cfg.abbr}
            if row["pos"] != "K":
                pp = ppr_points(a.astype(float))
                m, lo, hi = calib.apply(pp.mean(), np.percentile(pp, 10), np.percentile(pp, 90),
                                        q.get("ppr_avg", np.nan), q.get("ppr_n", np.nan))
                row["ppr"] = {"proj": round(float(m), 1), "lo": round(float(lo), 1), "hi": round(float(hi), 1),
                              "sim": round(float(pp.mean()), 1), "avg": q.get("ppr_avg"), "n": q.get("ppr_n")}
            for k in PSTATS:
                if a[:, PIDX[k]].any():
                    row[k] = _q(a[:, PIDX[k]])
            rows.append(row)
        out["players"][cfg.abbr] = rows
    return out


def _hist(a, lo, hi, step):
    edges = np.arange(lo, hi + step, step)
    h, _ = np.histogram(np.clip(a, lo, hi - 1e-9), bins=edges)
    return {"lo": lo, "step": step, "counts": [int(x) for x in h]}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("home"); ap.add_argument("away")
    ap.add_argument("-n", type=int, default=2000)
    ap.add_argument("--neutral", action="store_true")
    a = ap.parse_args()
    t = time.time()
    r = simulate(a.home, a.away, a.n, neutral=a.neutral)
    s = summarize(r, a.home, a.away)
    print(f"{a.n} games in {time.time() - t:.1f}s")
    print(f"{a.home} {s['score']['home']['mean']}  {a.away} {s['score']['away']['mean']}  "
          f"win {s['win']['home']:.1%}/{s['win']['away']:.1%}  total {s['total']['mean']}")
    for t_, rows in s["players"].items():
        print(t_)
        for p in rows[:8]:
            print("  ", p["name"], {k: v["mean"] for k, v in p.items() if isinstance(v, dict)})
