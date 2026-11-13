"""Betting backtest on the saved walk-forward sims (raw sim, no Vegas blend, model rebuilt from earlier data only).

    python -m validate.bets                     # 2023-25 + 2026 weeks 3-9, ROI by edge threshold, per market
    python -m validate.bets --week 9            # also print the game-by-game card for one 2026 week

For every game and market (spread, total, moneyline) the model's win/push/lose probabilities come from the simulated score
distribution. The side with the higher expected value at the posted price is bet 1 unit when EV >= threshold, then graded on the
real result. Prices are the nflverse closing odds (spread/total odds, moneyline). A "-110" column re-grades the same bets at flat
-110 as a sanity check on the posted odds. `shrunk` pulls the model toward the no-vig market with a weight fit on 2023-25 only,
then is graded on 2026 (out of sample).
"""
import argparse, os
import numpy as np, pandas as pd

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
THR = [None, 0.0, 0.03, 0.05, 0.10]


def dec(a):
    a = np.asarray(a, float)
    return np.where(a > 0, 1 + a / 100, 1 + 100 / np.abs(a))


def load(season, lines):
    G = pd.read_csv(f"{OUT}/wf_{season}_games.csv"); sc = np.load(f"{OUT}/wf_{season}.npz")["sc"].astype(np.int16)
    G["season"] = season
    G = G.merge(lines, left_on=["season", "week", "home", "away"], right_on=["season", "week", "home_team", "away_team"], how="left")
    return G, sc


def probs(sc, G):
    """Model (win, push) for the home / over / home-ML side of each market, from the simulated scores."""
    m = (sc[:, :, 0] - sc[:, :, 1]).astype(float); t = (sc[:, :, 0] + sc[:, :, 1]).astype(float)
    s = G.spread.values[:, None]; L = G.total.values[:, None]
    P = {"spread": ((m > s).mean(1), (m == s).mean(1)), "total": ((t > L).mean(1), (t == L).mean(1)), "ml": ((m > 0).mean(1), (m == 0).mean(1))}
    # probability of the OTHER side winning (not just 1 - win - push) so pushes are handled exactly
    Q = {"spread": ((m < s).mean(1)), "total": ((t < L).mean(1)), "ml": ((m < 0).mean(1))}
    return P, Q, m, t


def market_rows(G):
    """Per market: odds for side A (home / over / home ML) and side B, actual outcome of side A in {1 win, 0.5 push, 0 lose}."""
    r = (G.hs - G["as"]).values; tot = (G.hs + G["as"]).values
    out = {}
    out["spread"] = dict(oa=G.home_spread_odds.values, ob=G.away_spread_odds.values,
                         res=np.where(r > G.spread, 1.0, np.where(r == G.spread, 0.5, 0.0)))
    out["total"] = dict(oa=G.over_odds.values, ob=G.under_odds.values,
                        res=np.where(tot > G.total, 1.0, np.where(tot == G.total, 0.5, 0.0)))
    out["ml"] = dict(oa=G.ml_h.values, ob=G.ml_a.values, res=np.where(r > 0, 1.0, np.where(r == 0, 0.5, 0.0)))
    return out


def settle(pw, pl, odds, win):
    """EV per unit and realized profit of betting a side. pw/pl: model win/lose prob; win in {1,.5,0} for this side."""
    d = dec(odds)
    ev = pw * (d - 1) - pl
    prof = np.where(win == 1, d - 1, np.where(win == 0, -1.0, 0.0))
    return ev, prof


def evaluate(G, sc, shrink=None, flat=False):
    """Returns {market: DataFrame(game rows) with chosen side, ev, profit}."""
    P, Q, m, t = probs(sc, G); M = market_rows(G); res = {}
    for k, (pa_w, pa_p) in P.items():
        pb_w = Q[k]; oa, ob = M[k]["oa"], M[k]["ob"]
        if flat and k != "ml":      # moneylines have no standard price; only spread/total are re-priced at -110
            oa = ob = np.full(len(G), -110.0)
        pa_l = 1 - pa_w - pa_p; pb_l = 1 - pb_w - pa_p
        if shrink is not None:      # pull win probs toward the no-vig market (pushes kept as modelled)
            ma, mb = 1 / dec(M[k]["oa"]), 1 / dec(M[k]["ob"]); nv = ma / (ma + mb)
            w = shrink[k]; push = pa_p
            adj = nv * (1 - push) + w * (pa_w - nv * (1 - push))      # prob side A wins, market-anchored
            pa_w, pb_w = adj, (1 - push) - adj
            pa_l, pb_l = pb_w, pa_w
        eva, pra = settle(pa_w, pa_l, oa, M[k]["res"]); evb, prb = settle(pb_w, pb_l, ob, 1 - M[k]["res"])
        pickA = eva >= evb
        res[k] = pd.DataFrame({"season": G.season.values, "week": G.week.values, "home": G.home.values, "away": G.away.values,
                               "side": np.where(pickA, "A", "B"), "ev": np.where(pickA, eva, evb), "profit": np.where(pickA, pra, prb),
                               "pw": np.where(pickA, pa_w, pb_w), "odds": np.where(pickA, oa, ob)})
    return res


def table(df, label, thr=THR):
    rows = []
    for th in thr:
        d = df if th is None else df[df.ev >= th]
        n = len(d)
        if n == 0:
            rows.append((label, "all" if th is None else f"EV>={th:.0%}", 0, "", "", "", "")); continue
        se = d.profit.std(ddof=1) / np.sqrt(n) if n > 1 else float("nan")
        w, l = int((d.profit > 0).sum()), int((d.profit < 0).sum())
        rows.append((label, "all games" if th is None else f"EV>={th:.0%}", n, f"{w}-{l}-{n - w - l}", f"{d.profit.sum():+.1f}", f"{d.profit.mean():+.1%}", f"+/-{1.96 * se:.1%}"))
    return rows


def show(rows):
    print(f"{'':10}{'rule':11}{'bets':>5}  {'W-L-P':>9}{'units':>8}{'ROI':>8}{'95% CI':>9}")
    for r in rows:
        print(f"{r[0]:10}{r[1]:11}{r[2]:>5}  {r[3]:>9}{r[4]:>8}{r[5]:>8}{r[6]:>9}")


def fit_shrink(G, sc):
    """Weight w in [0,1] on the model vs the no-vig market that maximizes log-likelihood (pushes excluded). w=0: model adds nothing."""
    P, Q, m, t = probs(sc, G); M = market_rows(G); W = {}
    for k, (pa_w, pa_p) in P.items():
        ma, mb = 1 / dec(M[k]["oa"]), 1 / dec(M[k]["ob"]); nv = ma / (ma + mb)
        y = M[k]["res"]; keep = (y != 0.5) & np.isfinite(nv)
        base = nv * (1 - pa_p); grid = np.linspace(0, 1, 101); ll = []
        for w in grid:
            p = np.clip((base + w * (pa_w - base)) / np.clip(1 - pa_p, .05, 1), 1e-3, 1 - 1e-3)
            ll.append(np.mean(np.where(y[keep] == 1, np.log(p[keep]), np.log(1 - p[keep]))))
        W[k] = float(grid[int(np.argmax(ll))])
    return W


def accuracy(G, sc, name):
    """Raw sim vs the closing market against the real result: margin/total error and log loss of the win probabilities."""
    P, Q, m, t = probs(sc, G); M = market_rows(G)
    am = (G.hs - G["as"]).values; at = (G.hs + G["as"]).values
    print(f"{name}: margin MAE sim {np.abs(m.mean(1) - am).mean():.2f} vs line {np.abs(G.spread.values - am).mean():.2f} | total MAE sim {np.abs(t.mean(1) - at).mean():.2f} vs line {np.abs(G.total.values - at).mean():.2f}")
    for k in ("spread", "total", "ml"):
        ma, mb = 1 / dec(M[k]["oa"]), 1 / dec(M[k]["ob"]); nv = ma / (ma + mb); pw, pp = P[k]
        y = M[k]["res"]; keep = y != 0.5; ps = np.clip(pw / np.clip(1 - pp, .05, 1), 1e-3, 1 - 1e-3)
        ll = lambda p: -np.mean(np.where(y[keep] == 1, np.log(p[keep]), np.log(1 - p[keep])))
        print(f"   {k:7} log loss  sim {ll(ps):.4f}  market {ll(nv):.4f}  50/50 mix {ll((ps + nv) / 2):.4f}   (n={keep.sum()})")


def baselines(G, name):
    """Dumb rules at the posted prices: what you lose (or win) with no model at all."""
    M = market_rows(G); out = []
    for k in ("spread", "total", "ml"):
        oa, ob, y = M[k]["oa"], M[k]["ob"], M[k]["res"]
        if k == "total": sides = {"over": (oa, y), "under": (ob, 1 - y)}
        else:
            favA = (G.spread.values > 0) if k == "spread" else (dec(oa) < dec(ob))
            fo = np.where(favA, oa, ob); fy = np.where(favA, y, 1 - y); do = np.where(favA, ob, oa); dy = 1 - fy
            sides = {"favorite": (fo, fy), "underdog": (do, dy)}
        for sd, (o, w) in sides.items():
            d = dec(o); prof = np.where(w == 1, d - 1, np.where(w == 0, -1.0, 0.0)); n = len(prof)
            out.append((f"{k} {sd}", n, f"{int((w == 1).sum())}-{int((w == 0).sum())}-{int((w == 0.5).sum())}", f"{prof.sum():+.1f}", f"{prof.mean():+.1%}"))
    print(f"-- no-model rules, {name} (every game, posted odds)")
    for r in out: print(f"   {r[0]:18}{r[1]:>5}  {r[2]:>9}{r[3]:>8}{r[4]:>8}")
    print()


def card(G, sc, shrink, season, week):
    P, Q, m, t = probs(sc, G); M = market_rows(G); ev = evaluate(G, sc)
    r = (G.hs - G["as"]).values
    print(f"\n{season} week {week}: model (raw sim) vs closing line, and the best side at EV>=3% per market")
    print(f"{'game':11}{'final':>8}  {'line':>6} {'model':>6}  {'tot':>5} {'model':>6}  {'homeML':>7} {'model':>6}   bets (profit)")
    for i, g in G.reset_index(drop=True).iterrows():
        if g.week != week: continue
        bets = []
        for k, nm in (("spread", "ATS"), ("total", "O/U"), ("ml", "ML")):
            e = ev[k].iloc[i]
            if e.ev >= 0.03:
                side = {"spread": {"A": g.home, "B": g.away}, "total": {"A": "over", "B": "under"}, "ml": {"A": g.home, "B": g.away}}[k][e.side]
                bets.append(f"{nm} {side} {e.profit:+.2f}")
        print(f"{g.away + '@' + g.home:11}{int(g['as']):>3}-{int(g.hs):<3}{'':2}{g.spread:>+6.1f} {m[i].mean():>+6.1f}  {g.total:>5.1f} {t[i].mean():>6.1f}  {g.ml_h:>+7.0f} {(m[i] > 0).mean():>6.0%}   {', '.join(bets)}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--week", type=int, default=0); ap.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024, 2025, 2026])
    a = ap.parse_args()
    lines = pd.read_csv("data/games.csv"); lines = lines[lines.game_type == "REG"][["season", "week", "home_team", "away_team", "home_spread_odds", "away_spread_odds", "over_odds", "under_odds"]]
    D = {s: load(s, lines) for s in a.seasons if os.path.exists(f"{OUT}/wf_{s}.npz")}
    hist = [s for s in D if s < 2026]
    Gh = pd.concat([D[s][0] for s in hist], ignore_index=True); Sh = np.concatenate([D[s][1] for s in hist])
    W = fit_shrink(Gh, Sh)
    print(f"model weight vs no-vig market (0 = model adds nothing, 1 = trust the raw sim), fit on {hist}: {W}\n")
    sets = [("2023-25", Gh, Sh)]
    if 2026 in D:
        G26, S26 = D[2026]; sets.append((f"2026 wk{G26.week.min()}-{G26.week.max()}", G26, S26))
        w9 = G26.week.values == 9
        sets.append(("2026 wk9", G26[w9].reset_index(drop=True), S26[w9]))
    for name, G, sc in sets:
        print(f"===== {name}: {len(G)} games =====")
        accuracy(G, sc, name); print(); baselines(G, name)
        for mode, kw in (("raw sim at posted odds", {}), ("raw sim at flat -110", {"flat": True}), ("shrunk toward market (weights from 2023-25)", {"shrink": W})):
            ev = evaluate(G, sc, **kw); rows = []
            print(f"-- {mode}")
            for k in ("spread", "total", "ml"):
                rows += table(ev[k], k, THR[1:] if "shrink" in kw else THR)
            show(rows)
            if "shrink" in kw: print("   (0 bets = after anchoring to the market the model never finds a +EV price)")
            print()
    if a.week and 2026 in D:
        G26, S26 = D[2026]; card(G26, S26, W, 2026, a.week)


if __name__ == "__main__":
    main()
