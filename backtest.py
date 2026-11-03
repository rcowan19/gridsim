"""Walk-forward backtest: for each week W, rebuild the model using only data before W,
simulate every game that week, and score against what actually happened.

    python backtest.py              # weeks 3..latest, 400 sims/game
    python backtest.py -s 4 -n 800
"""
import argparse, json, math, os, time
from multiprocessing import Pool
import numpy as np
import pandas as pd

import build_model
from engine import Engine, PIDX

E = None
def _init(model):
    global E
    E = Engine(model)

from engine import ppr_points as _ppr
import calib

def _game(args):
    home, away, n, seed = args
    H, A = E.team(home), E.team(away)
    pids = sorted(set(H.pids) | set(A.pids)); ix = {p: i for i, p in enumerate(pids)}
    from engine import PSTATS
    ps = np.zeros((n, len(pids), len(PSTATS)), dtype=np.float32); sc = np.zeros((n, 2))
    for g in range(n):
        r = E.game(H, A, seed=seed * 7919 + g)
        sc[g] = r["score"]
        for pid, v in r["ps"].items():
            if pid in ix: ps[g, ix[pid]] = v
    pp = _ppr(ps)
    return {"home": home, "away": away, "sc": sc, "pids": pids, "ppr_mean": pp.mean(0), "ppr_p10": np.percentile(pp, 10, axis=0),
            "ppr_p90": np.percentile(pp, 90, axis=0), "stat_mean": ps.mean(0),
            "teams": {pid: (home if pid in H.players else away) for pid in pids}}

def actual_player_games():
    cols = ["game_id", "week", "play_type", "pass_attempt", "rush_attempt", "complete_pass", "interception", "sack", "qb_scramble",
            "passer_player_id", "rusher_player_id", "receiver_player_id", "passing_yards", "rushing_yards", "receiving_yards",
            "pass_touchdown", "rush_touchdown", "season_type"]
    d = pd.read_parquet(build_model.CUR, columns=cols); d = d[d.season_type == "REG"]
    rows = []
    p = d[(d.pass_attempt == 1) & d.passer_player_id.notna()]
    rows.append(p.groupby(["game_id", "week", "passer_player_id"]).agg(pyd=("passing_yards", "sum"), ptd=("pass_touchdown", "sum"), int=("interception", "sum")).reset_index().rename(columns={"passer_player_id": "pid"}))
    r = d[(d.rush_attempt == 1) & d.rusher_player_id.notna()]
    rows.append(r.groupby(["game_id", "week", "rusher_player_id"]).agg(ryd=("rushing_yards", "sum"), rtd=("rush_touchdown", "sum")).reset_index().rename(columns={"rusher_player_id": "pid"}))
    t = d[(d.pass_attempt == 1) & d.receiver_player_id.notna()]
    rows.append(t.groupby(["game_id", "week", "receiver_player_id"]).agg(rec=("complete_pass", "sum"), recyd=("receiving_yards", "sum"), rectd=("pass_touchdown", "sum")).reset_index().rename(columns={"receiver_player_id": "pid"}))
    out = rows[0]
    for x in rows[1:]: out = out.merge(x, on=["game_id", "week", "pid"], how="outer")
    out = out.fillna(0)
    out["ppr"] = 0.04 * out.pyd + 4 * out.ptd - 2 * out["int"] + 0.1 * (out.ryd + out.recyd) + 6 * (out.rtd + out.rectd) + out.rec
    return out

def games_table():
    d = pd.read_parquet(build_model.CUR, columns=["game_id", "week", "home_team", "away_team", "home_score", "away_score", "season_type"])
    d = d[d.season_type == "REG"].drop_duplicates("game_id")
    return d.reset_index(drop=True)

def norm_cdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def baseline_margin(G, wk, prev_diff):
    """team point differential per game before wk (2026) blended with 2025 prior"""
    past = G[G.week < wk]; s = {}
    for t in set(G.home_team) | set(G.away_team):
        h = past[past.home_team == t]; a = past[past.away_team == t]
        m = list(h.home_score - h.away_score) + list(a.away_score - a.home_score)
        w25 = 4.0   # 2025 prior = 4 pseudo-games at last season's average diff
        s[t] = (sum(m) + w25 * prev_diff.get(t, 0)) / (len(m) + w25)
    return s

def prev_season_diff():
    d = pd.read_parquet(build_model.PREV, columns=["game_id", "home_team", "away_team", "home_score", "away_score", "season_type"])
    d = d[d.season_type == "REG"].drop_duplicates("game_id"); out = {}
    for t in set(d.home_team):
        h = d[d.home_team == t]; a = d[d.away_team == t]
        m = list(h.home_score - h.away_score) + list(a.away_score - a.home_score); out[t] = float(np.mean(m))
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("-s", type=int, default=3); ap.add_argument("-n", type=int, default=400); ap.add_argument("-d", type=float, default=None); ap.add_argument("-p", type=float, default=None)
    a = ap.parse_args()
    G = games_table(); AP = actual_player_games(); pdiff = prev_season_diff()
    last = int(G.week.max()); t0 = time.time()
    games, players = [], []
    for wk in range(a.s, last + 1):
        M = build_model.main(cutoff=wk, write=False, quiet=True, usage_decay=a.d, w_prev_use=a.p)
        bm = baseline_margin(G, wk, pdiff)
        W = G[G.week == wk]
        with Pool(os.cpu_count() or 1, initializer=_init, initargs=(M,)) as pool:
            res = pool.map(_game, [(r.home_team, r.away_team, a.n, int(r.week) * 100 + i) for i, r in enumerate(W.itertuples())])
        base_ppr, base_n = {}, {}
        for tm in M["teams"].values():
            for q in tm["qbs"] + tm["skill"]:
                if "ppr_avg" in q: base_ppr[q["id"]], base_n[q["id"]] = q["ppr_avg"], q["ppr_n"]
        base_ppr = pd.Series(base_ppr, dtype=float); base_n = pd.Series(base_n, dtype=float)
        for r, g in zip(W.itertuples(), res):
            m = g["sc"][:, 0] - g["sc"][:, 1]; tot = g["sc"].sum(1)
            ph = float((m > 0).mean() + 0.5 * (m == 0).mean())
            actual_m = r.home_score - r.away_score
            bmar = bm[r.home_team] - bm[r.away_team] + 1.5
            games.append({"week": wk, "home": r.home_team, "away": r.away_team, "act_margin": actual_m, "act_total": r.home_score + r.away_score,
                          "sim_p_home": ph, "sim_margin": float(m.mean()), "sim_total": float(tot.mean()),
                          "base_margin": bmar, "base_p_home": norm_cdf(bmar / 13.5)})
            act = AP[AP.game_id == r.game_id].set_index("pid")
            for i, pid in enumerate(g["pids"]):
                if pid in act.index:
                    ap_ = float(act.loc[pid, "ppr"]); played = True
                else:
                    ap_ = 0.0; played = False
                players.append({"week": wk, "pid": pid, "team": g["teams"][pid], "pred": float(g["ppr_mean"][i]), "p10": float(g["ppr_p10"][i]),
                                "p90": float(g["ppr_p90"][i]), "act": ap_, "played": played, "base": float(base_ppr.get(pid, np.nan)), "n": float(base_n.get(pid, np.nan))})
            # actual fantasy-relevant players the sim didn't project
            miss = act[(act.ppr >= 8) & ~act.index.isin(g["pids"])]
            inmodel = {q["id"]: q for tm in M["teams"].values() for q in tm["qbs"] + tm["skill"]}
            for pid, row in miss.iterrows():
                reason = "inactive_flag" if pid in inmodel else "not_in_model"
                players.append({"week": wk, "pid": pid, "team": None, "pred": np.nan, "p10": np.nan, "p90": np.nan, "act": float(row.ppr), "played": True, "base": float(base_ppr.get(pid, np.nan)), "n": float(base_n.get(pid, np.nan)), "reason": reason})
        print(f"week {wk} done ({len(W)} games)  {time.time() - t0:.0f}s", flush=True)
    gdf, pdf = pd.DataFrame(games), pd.DataFrame(players)
    gdf.to_csv("backtest_games.csv", index=False); pdf.to_csv("backtest_players.csv", index=False)
    report(gdf, pdf)

def report(g, p):
    print(f"\n=== GAMES  (n={len(g)}, weeks {g.week.min()}-{g.week.max()}) ===")
    hw = (g.act_margin > 0).astype(float) + 0.5 * (g.act_margin == 0)
    def row(name, pm, pp):
        acc = ((pm > 0) == (g.act_margin > 0)).mean()
        brier = ((pp - hw) ** 2).mean()
        print(f"{name:14s} pick acc {acc:5.1%}  Brier {brier:.3f}  margin MAE {np.abs(pm - g.act_margin).mean():5.2f}  bias {np.mean(pm - g.act_margin):+5.2f}")
    row("GridSim", g.sim_margin, g.sim_p_home)
    row("pt-diff base", g.base_margin, g.base_p_home)
    row("always home", pd.Series(1.0, index=g.index), pd.Series(0.55, index=g.index))
    print(f"total points  sim MAE {np.abs(g.sim_total - g.act_total).mean():.2f}  bias {np.mean(g.sim_total - g.act_total):+.2f}  (actual avg {g.act_total.mean():.1f}, sim avg {g.sim_total.mean():.1f})")
    print("win-prob calibration (sim):")
    g = g.assign(b=pd.cut(g.sim_p_home, [0, .35, .45, .55, .65, 1]))
    for b, x in g.groupby("b", observed=True): print(f"  {str(b):12s} n={len(x):3d}  predicted {x.sim_p_home.mean():.2f}  actual home win {(x.act_margin > 0).mean():.2f}")
    pp = p[p.pred.notna() & p.played & (p.pred >= 3)]
    print(f"\n=== PLAYERS (PPR, projected >=3 pts, n={len(pp)}) ===")
    print(f"GridSim MAE {np.abs(pp.pred - pp.act).mean():.2f}   corr {np.corrcoef(pp.pred, pp.act)[0, 1]:.3f}   bias {np.mean(pp.pred - pp.act):+.2f}")
    b = pp[pp.base.notna()]
    print(f"vs trailing-avg baseline on same {len(b)} rows:  sim MAE {np.abs(b.pred - b.act).mean():.2f} / base MAE {np.abs(b.base - b.act).mean():.2f}   sim corr {np.corrcoef(b.pred, b.act)[0, 1]:.3f} / base corr {np.corrcoef(b.base, b.act)[0, 1]:.3f}")
    print(f"80% interval coverage (should be ~80%): {((pp.act >= pp.p10) & (pp.act <= pp.p90)).mean():.1%}")
    allp = p[p.played & (p.act >= 8)]
    if "reason" in p: print("  missed scorers by reason:", p[p.reason.notna()].reason.value_counts().to_dict())
    print(f"players who scored >=8 PPR: {len(allp)}, projected by sim at all: {allp.pred.notna().mean():.1%}")
    print("by projected-points bucket:")
    for lo, hi in [(3, 6), (6, 10), (10, 15), (15, 99)]:
        x = pp[(pp.pred >= lo) & (pp.pred < hi)]
        if len(x): print(f"  {lo:2d}-{hi:2d}  n={len(x):3d}  predicted {x.pred.mean():5.1f}  actual {x.act.mean():5.1f}")

if __name__ == "__main__":
    main()
