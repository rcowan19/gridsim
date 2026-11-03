"""Walk-forward backtest of kicker and D/ST projections (ESPN default scoring).   python bt_kdst.py -s 3 -n 600"""
import argparse, os, random
from multiprocessing import Pool
import numpy as np, pandas as pd
import build_model, backtest, vegas, sim
from engine import Engine, PIDX, TIDX

E = None
def _init(m):
    global E
    E = Engine(m)

def _game(args):
    home, away, n, seed, line = args
    av = random.Random(seed * 7 + 11)
    H, A = E.team(home), E.team(away)
    ks = [H.k["id"], A.k["id"]]
    ts = np.zeros((n, 2, len(TIDX))); sc = np.zeros((n, 2)); kp = np.zeros((n, 2))
    for g in range(n):
        r = E.game(E.team_draw(home, {}, av), E.team_draw(away, {}, av), seed=seed * 7919 + g)
        sc[g] = r["score"]; ts[g] = r["ts"]
        for s, kid in enumerate(ks):
            v = np.array(r["ps"].get(kid, [0] * len(PIDX)), float)
            kp[g, s] = sim._k_points(v[PIDX["fgm"]], v[PIDX["fga"]], v[PIDX["xpm"]], v[PIDX["xpa"]], v[PIDX["fgb"]])
    out = {}
    for b in (0, vegas.BETA):
        idx, _ = vegas.blend_index(sc, line[0], line[1], beta=b, seed=seed)
        t, s_, k = ts[idx], sc[idx], kp[idx]
        d = [sim._dst_points(t[:, 1 - s, TIDX["sacks"]], t[:, 1 - s, TIDX["to"]], s_[:, 1 - s], t[:, s, TIDX["dtd"]], t[:, s, TIDX["saf"]], t[:, 1 - s, TIDX["yds"]]) for s in (0, 1)]
        out[b] = {"k": k.mean(0), "dst": [x.mean() for x in d]}
    return out

def actual():
    c = ["game_id", "week", "season_type", "posteam", "defteam", "play_type", "sack", "interception", "fumble_lost", "touchdown", "td_team",
         "safety", "yards_gained", "field_goal_result", "kick_distance", "extra_point_result", "home_team", "away_team", "total_home_score", "total_away_score"]
    d = pd.read_parquet(build_model.CUR, columns=c); d = d[d.season_type == "REG"]
    rows = []
    for gid, g in d.groupby("game_id"):
        h, a = g.home_team.iloc[0], g.away_team.iloc[0]; fs = {h: g.total_home_score.max(), a: g.total_away_score.max()}
        for T, O in ((h, a), (a, h)):
            dfn = g[g.defteam == T]; off = g[g.posteam == O]
            ret_td = ((g.play_type == "kickoff") & (g.td_team == T) & (g.posteam == T)).sum()
            dtd = ((g.touchdown == 1) & (g.td_team == T) & (g.defteam == T)).sum() + ret_td
            saf = ((g.safety == 1) & (g.defteam == T)).sum()
            yds = off[off.play_type.isin(["pass", "run"])].yards_gained.sum()
            pa = fs[O]
            dst = float(sim._dst_points(np.array(dfn.sack.sum(), float), np.array((dfn.interception.sum() + dfn.fumble_lost.sum()), float),
                                        np.array(pa, float), dtd, saf, np.array(yds, float)))
            kk = g[(g.posteam == T)]
            fg = kk[kk.play_type == "field_goal"]; xp = kk[kk.extra_point_result.notna()]
            made = fg.field_goal_result == "made"
            k = float(3 * made.sum() + ((fg.kick_distance >= 40) & made).sum() + ((fg.kick_distance >= 50) & made).sum() - (~made).sum()
                      + (xp.extra_point_result == "good").sum() - (xp.extra_point_result != "good").sum())
            rows.append({"game_id": gid, "week": int(g.week.iloc[0]), "team": T, "dst": dst, "k": k})
    return pd.DataFrame(rows)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("-s", type=int, default=3); ap.add_argument("-n", type=int, default=600); a = ap.parse_args()
    G = backtest.games_table(); gl = pd.read_csv("data/games.csv"); gl = gl[gl.season == 2026][["week", "home_team", "away_team", "spread_line", "total_line"]]
    G = G.merge(gl, on=["week", "home_team", "away_team"], how="left"); A = actual().set_index(["game_id", "team"])
    rows = []
    for wk in range(a.s, int(G.week.max()) + 1):
        M = build_model.main(cutoff=wk, write=False, quiet=True); W = G[G.week == wk]
        hist = A[A.week < wk].reset_index().groupby("team")[["dst", "k"]].mean()
        with Pool(os.cpu_count() or 1, initializer=_init, initargs=(M,)) as P:
            res = P.map(_game, [(r.home_team, r.away_team, a.n, wk * 100 + i, (r.spread_line, r.total_line)) for i, r in enumerate(W.itertuples())])
        for r, o in zip(W.itertuples(), res):
            for s, T in enumerate((r.home_team, r.away_team)):
                act = A.loc[(r.game_id, T)]
                rows.append({"week": wk, "team": T, "act_dst": act.dst, "act_k": act.k, "sim_dst": o[0]["dst"][s], "bl_dst": o[vegas.BETA]["dst"][s],
                             "sim_k": o[0]["k"][s], "bl_k": o[vegas.BETA]["k"][s], "avg_dst": hist.dst.get(T, np.nan), "avg_k": hist.k.get(T, np.nan)})
        print("week", wk, "done", flush=True)
    p = pd.DataFrame(rows); p.to_csv("bt_kdst.csv", index=False)
    for x in ("dst", "k"):
        a_ = p["act_" + x]
        print(f"{x.upper():4s} actual mean {a_.mean():5.2f} sd {a_.std():5.2f} | sim mean {p['sim_' + x].mean():5.2f} MAE {np.abs(p['sim_' + x] - a_).mean():.2f} corr {np.corrcoef(p['sim_' + x], a_)[0, 1]:.3f}"
              f" | blended MAE {np.abs(p['bl_' + x] - a_).mean():.2f} corr {np.corrcoef(p['bl_' + x], a_)[0, 1]:.3f} | season avg MAE {np.abs(p['avg_' + x] - a_).mean():.2f} corr {p[['avg_' + x]].corrwith(a_).iloc[0]:.3f}"
              f" | always-mean MAE {np.abs(a_.mean() - a_).mean():.2f}")

if __name__ == "__main__":
    main()
