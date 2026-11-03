"""Walk-forward backtest of the Vegas blend weight (beta). Simulates each game once, scores several betas.
    python bt_vegas.py -s 3 -n 600
"""
import argparse, math, os, time
from multiprocessing import Pool
import numpy as np, pandas as pd
import build_model, backtest, vegas, calib
from engine import Engine, PSTATS, ppr_points

E = None
def _init(m):
    global E
    E = Engine(m)

def _game(args):
    import random
    home, away, n, seed = args
    H, A = E.team(home), E.team(away)
    pids = sorted(set(H.pids) | set(A.pids) | set(E.extra_pids(home)) | set(E.extra_pids(away))); ix = {p: i for i, p in enumerate(pids)}
    ps = np.zeros((n, len(pids), len(PSTATS)), dtype=np.float32); sc = np.zeros((n, 2))
    av = random.Random(seed * 7 + 11)
    for g in range(n):
        r = E.game(E.team_draw(home, {}, av), E.team_draw(away, {}, av), seed=seed * 7919 + g)
        sc[g] = r["score"]
        for pid, v in r["ps"].items():
            if pid in ix: ps[g, ix[pid]] = v
    return pids, ppr_points(ps).astype(np.float32), sc

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("-s", type=int, default=3); ap.add_argument("-n", type=int, default=600); a = ap.parse_args()
    G = backtest.games_table(); AP = backtest.actual_player_games()
    gl = pd.read_csv("data/games.csv"); gl = gl[gl.season == 2026][["week", "home_team", "away_team", "spread_line", "total_line"]]
    G = G.merge(gl, on=["week", "home_team", "away_team"], how="left")
    betas = [0, .2, .4, .6, .8, 1.0]
    games, players = [], []
    for wk in range(a.s, int(G.week.max()) + 1):
        M = build_model.main(cutoff=wk, write=False, quiet=True); W = G[G.week == wk]
        with Pool(os.cpu_count() or 1, initializer=_init, initargs=(M,)) as P:
            res = P.map(_game, [(r.home_team, r.away_team, a.n, wk * 100 + i) for i, r in enumerate(W.itertuples())])
        for r, (pids, pp, sc) in zip(W.itertuples(), res):
            act = AP[AP.game_id == r.game_id].set_index("pid").ppr
            am = r.home_score - r.away_score
            for b in betas:
                idx, _ = vegas.blend_index(sc, r.spread_line, r.total_line, beta=b, seed=wk)
                s = sc[idx]; m = s[:, 0] - s[:, 1]
                games.append({"beta": b, "week": wk, "pm": m.mean(), "ph": (m > 0).mean() + .5 * (m == 0).mean(), "pt": s.sum(1).mean(),
                              "am": am, "atot": r.home_score + r.away_score, "vm": r.spread_line, "vt": r.total_line})
                mu, p10, p90 = pp[idx].mean(0), np.percentile(pp[idx], 10, 0), np.percentile(pp[idx], 90, 0)
                for i, pid in enumerate(pids):
                    players.append({"beta": b, "week": wk, "pid": pid, "pred": float(mu[i]), "p10": float(p10[i]), "p90": float(p90[i]),
                                    "act": float(act.get(pid, 0.0)), "played": pid in act.index})
        print(f"week {wk} done", flush=True)
    g, p = pd.DataFrame(games), pd.DataFrame(players)
    g.to_csv("bt_vegas_games.csv", index=False); p.to_csv("bt_vegas_players.csv", index=False)
    hw = lambda d: (d.am > 0) + .5 * (d.am == 0)
    print("\nbeta  margin_MAE  Brier  pick%  total_MAE  total_bias | player_MAE  corr   (proj>=3 at beta=0, played)")
    keep = p[(p.beta == 0) & (p.pred >= 3) & p.played][["week", "pid"]]
    for b in betas:
        d = g[g.beta == b]; q = p[p.beta == b].merge(keep, on=["week", "pid"])
        print(f"{b:4.1f}  {np.abs(d.pm - d.am).mean():9.2f}  {((d.ph - hw(d)) ** 2).mean():.3f}  {((d.pm > 0) == (d.am > 0)).mean():5.1%}  {np.abs(d.pt - d.atot).mean():8.2f}  {np.mean(d.pt - d.atot):+8.2f}  |  {np.abs(q.pred - q.act).mean():8.3f}  {np.corrcoef(q.pred, q.act)[0, 1]:.3f}")
    d = g[g.beta == 0]
    print(f"Vegas alone: margin MAE {np.abs(d.vm - d.am).mean():.2f}  total MAE {np.abs(d.vt - d.atot).mean():.2f}  pick {((d.vm > 0) == (d.am > 0)).mean():.1%}")

if __name__ == "__main__":
    main()
