"""Compare league-average simulated stats vs. actual 2026/2025 weighted league stats."""
import json, random, sys, time
import numpy as np
from engine import Engine, PIDX, TIDX

def calibrate(n=3000, tune=None, seed=1, verbose=True):
    m = json.load(open("model.json"))
    e = Engine(m, tune)
    la = m["league_actual"]
    rng = random.Random(seed)
    abbrs = sorted(m["teams"])
    cache = {a: e.team(a) for a in abbrs}
    S = {k: 0.0 for k in ["pts", "plays", "pass", "rush", "yds", "sacks", "to", "punts", "fga", "td", "pa", "pc", "pyd", "int", "ra", "ryd", "ptd", "rtd", "ot", "margin"]}
    for g in range(n):
        h, a = rng.sample(abbrs, 2)
        r = e.game(cache[h], cache[a], seed=rng.randrange(10**9))
        for side in (0, 1):
            t = r["ts"][side]
            for k in ["pts", "plays", "pass", "rush", "yds", "sacks", "to", "punts", "fga", "td"]:
                S[k] += t[TIDX[k]]
        for v in r["ps"].values():
            for k in ["pa", "pc", "pyd", "int", "ra", "ryd", "ptd", "rtd"]:
                S[k] += v[PIDX[k]]
        S["ot"] += r["ot"]
        S["margin"] += r["score"][0] - r["score"][1]
    ng = 2 * n
    sim = {
        "ppg": S["pts"] / ng, "plays_pg": S["plays"] / ng,
        "pass_rate": S["pass"] / S["plays"], "sack_rate": S["sacks"] / S["pass"],
        "comp_pct": S["pc"] / S["pa"], "int_rate": S["int"] / S["pa"], "ypa": S["pyd"] / S["pa"],
        "ypc": S["ryd"] / S["ra"], "pass_td_pg": S["ptd"] / ng, "rush_td_pg": S["rtd"] / ng,
        "punts_pg": S["punts"] / ng, "fga_pg": S["fga"] / ng, "to_pg": S["to"] / ng, "yards_pg": S["yds"] / ng,
    }
    act = dict(la)
    act["pass_rate"] = la["pass_rate"] * (1 - la["scramble_rate"])
    if verbose:
        print(f"{'stat':12s} {'sim':>8s} {'actual':>8s}")
        for k in sim:
            print(f"{k:12s} {sim[k]:8.3f} {act[k]:8.3f}")
        print("OT rate", S["ot"] / n, " home margin", S["margin"] / n)
    return sim, act

if __name__ == "__main__":
    t = time.time(); calibrate(int(sys.argv[1]) if len(sys.argv) > 1 else 3000); print(f"{time.time()-t:.1f}s")
