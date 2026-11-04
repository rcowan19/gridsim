"""Validation report from a walk-forward run:  python -m validate.report --season 2025 [--tag X]
Part A: league-level calibration (sim vs real). Part B: game-level accuracy vs baselines and the market."""
import argparse, math, os
import numpy as np, pandas as pd
from scipy import stats
from sklearn.metrics import roc_auc_score
from engine import TSTATS

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
T = {k: i for i, k in enumerate(TSTATS)}
SUMK = ["pa", "pc", "pyd", "ptd", "int", "sk", "ra", "ryd", "rtd", "fga", "fgm", "fl"]
S = {k: i for i, k in enumerate(SUMK)}
cdf = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))


def actual_team_games(season, weeks):
    cols = ["game_id", "week", "season_type", "posteam", "defteam", "play_type", "pass_attempt", "rush_attempt", "complete_pass", "interception", "sack",
            "passing_yards", "rushing_yards", "yards_gained", "fumble_lost", "pass_touchdown", "rush_touchdown", "penalty", "penalty_yards", "penalty_team",
            "field_goal_attempt", "field_goal_result", "punt_attempt", "third_down_converted", "third_down_failed", "fixed_drive", "fixed_drive_result",
            "touchdown", "qb_kneel", "qb_spike", "down", "yardline_100", "first_down"]
    d = pd.read_parquet(f"data/pbp_{season}.parquet", columns=cols)
    d = d[(d.season_type == "REG") & d.week.isin(weeks) & d.posteam.notna()]
    p = d[d.play_type.isin(["pass", "run"])]
    g = p.groupby(["game_id", "posteam"])
    out = pd.DataFrame({"plays": g.size(), "pass": g.pass_attempt.sum() - g.sack.sum(), "rush": g.rush_attempt.sum(), "yds": g.yards_gained.sum(),
                        "sacks": g.sack.sum(), "pa": g.pass_attempt.sum() - g.sack.sum(), "pc": g.complete_pass.sum(), "int": g.interception.sum(), "ra": g.rush_attempt.sum()})
    out["to"] = p.groupby(["game_id", "posteam"]).apply(lambda x: x.interception.sum() + x.fumble_lost.sum(), include_groups=False)
    pen = d[(d.penalty == 1)].groupby(["game_id", "penalty_team"]).agg(pen=("penalty", "size"), peny=("penalty_yards", "sum"))
    pen.index.names = ["game_id", "posteam"]
    out = out.join(pen).fillna(0)
    out["punts"] = d[d.punt_attempt == 1].groupby(["game_id", "posteam"]).size(); out["fga"] = d[d.field_goal_attempt == 1].groupby(["game_id", "posteam"]).size()
    out["fgm"] = d[d.field_goal_result == "made"].groupby(["game_id", "posteam"]).size()
    out["poss"] = d.groupby(["game_id", "posteam"]).fixed_drive.nunique()
    out["td"] = d[(d.touchdown == 1)].groupby(["game_id", "posteam"]).size()
    t3 = d[d.down == 3]; out["3a"] = t3.groupby(["game_id", "posteam"]).size(); out["3c"] = d[d.third_down_converted == 1].groupby(["game_id", "posteam"]).size()
    return out.fillna(0).reset_index(), d


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--season", type=int, default=2025); ap.add_argument("--tag", default="")
    a = ap.parse_args(); S_ = a.season
    G = pd.read_csv(f"{OUT}/wf_{S_}{a.tag}_games.csv"); Z = np.load(f"{OUT}/wf_{S_}{a.tag}.npz")
    sc, ts, ot, sm = Z["sc"].astype(float), Z["ts"].astype(float), Z["ot"], Z["sm"].astype(float)       # (games, n, ...)
    ng, n = sc.shape[:2]; L = []
    P = lambda s="": (L.append(s), print(s))
    P(f"# Validation report: season {S_} walk-forward, {ng} games x {n} sims, weeks {G.week.min()}-{G.week.max()}, raw sim (no Vegas blend)\n")
    # ------------------------------------------------ A: league level
    P("## A. League-level calibration (sim vs real, same matchups)\n")
    act_pts = np.r_[G.hs.values, G["as"].values]; sim_pts = np.r_[sc[:, :, 0].ravel(), sc[:, :, 1].ravel()]
    rows = [("points / team-game", act_pts.mean(), act_pts.std(), sim_pts.mean(), sim_pts.std())]
    am = (G.hs - G["as"]).values; sm_ = (sc[:, :, 0] - sc[:, :, 1])
    rows.append(("home margin", am.mean(), am.std(), sm_.mean(), sm_.std()))
    at = (G.hs + G["as"]).values; st_ = sc.sum(2)
    rows.append(("total points", at.mean(), at.std(), st_.mean(), st_.std()))
    mk = (G.spread - am)    # market error: actual margin vs closing spread
    rows.append(("margin - closing spread (market err)", np.nanmean(am - G.spread), np.nanstd(am - G.spread), np.nan, np.nan))
    A, AD = actual_team_games(S_, list(G.week.unique())); A = A[A.game_id.notna()]
    simS = {k: ts[:, :, :, T[k]] for k in TSTATS}
    ap_ = {k: A[k].values for k in A.columns if k not in ("game_id", "posteam")}
    def addrow(name, aval, sval): rows.append((name, np.mean(aval), np.std(aval), np.mean(sval), np.std(sval)))
    for k, nm in [("plays", "plays"), ("yds", "yards"), ("sacks", "sacks"), ("to", "turnovers"), ("pen", "penalties"), ("punts", "punts"), ("fga", "FG attempts"), ("td", "touchdowns"), ("poss", "possessions (drives)")]:
        addrow(nm + " / team-game", ap_[k], simS[k].ravel())
    addrow("pass attempts / team-game", ap_["pa"], sm[:, :, :, S["pa"]].ravel()); addrow("completions", ap_["pc"], sm[:, :, :, S["pc"]].ravel())
    addrow("rush attempts", ap_["ra"], sm[:, :, :, S["ra"]].ravel()); addrow("INT", ap_["int"], sm[:, :, :, S["int"]].ravel())
    P("| stat | real mean | real sd | sim mean | sim sd | mean diff |\n|---|---|---|---|---|---|")
    for r in rows: P(f"| {r[0]} | {r[1]:.2f} | {r[2]:.2f} | {r[3]:.2f} | {r[4]:.2f} | {r[3] - r[1]:+.2f} |")
    ratio = lambda x, y: x.sum() / y.sum()
    P("\n| rate | real | sim |\n|---|---|---|")
    P(f"| completion % | {ratio(A.pc, A.pa):.3f} | {sm[..., S['pc']].sum() / sm[..., S['pa']].sum():.3f} |")
    P(f"| yards / attempt | {AD.passing_yards.sum() / (AD.pass_attempt.sum() - AD.sack.sum()):.2f} | {sm[..., S['pyd']].sum() / sm[..., S['pa']].sum():.2f} |")
    P(f"| yards / carry | {AD.rushing_yards.sum() / AD.rush_attempt.sum():.2f} | {sm[..., S['ryd']].sum() / sm[..., S['ra']].sum():.2f} |")
    P(f"| sack rate (per dropback) | {ratio(A.sacks, A.pa + A.sacks):.3f} | {simS['sacks'].sum() / (sm[..., S['pa']].sum() + simS['sacks'].sum()):.3f} |")
    P(f"| INT rate (per attempt) | {ratio(A['int'], A.pa):.4f} | {sm[..., S['int']].sum() / sm[..., S['pa']].sum():.4f} |")
    P(f"| 3rd-down conversion | {ratio(A['3c'], A['3a']):.3f} | n/a (not logged) |")
    P(f"| FG make % | {ratio(A.fgm, A.fga):.3f} | {sm[..., S['fgm']].sum() / sm[..., S['fga']].sum():.3f} |")
    P(f"| punts / possession | {ratio(A.punts, A.poss):.3f} | {simS['punts'].sum() / simS['poss'].sum():.3f} |")
    P(f"| TD / possession | {ratio(A.td, A.poss):.3f} | {simS['td'].sum() / simS['poss'].sum():.3f} |")
    P(f"| turnovers / possession | {ratio(A['to'], A.poss):.3f} | {simS['to'].sum() / simS['poss'].sum():.3f} |")
    P(f"| pass rate (dropbacks / plays) | {ratio(A.pa + A.sacks, A.plays):.3f} | {(sm[..., S['pa']].sum() + simS['sacks'].sum()) / simS['plays'].sum():.3f} |")
    ks = stats.ks_2samp(act_pts, sim_pts); wd = stats.wasserstein_distance(act_pts, sim_pts)
    P(f"\n**Points per team distribution:** KS = {ks.statistic:.3f} (p = {ks.pvalue:.3g}, sim n is large so p is over-sensitive), Wasserstein = {wd:.2f} pts.")
    P("\n| score | real % | sim % |\n|---|---|---|")
    for s in [0, 3, 6, 7, 9, 10, 13, 14, 16, 17, 19, 20, 21, 23, 24, 26, 27, 28, 30, 31, 34]: P(f"| {s} | {(act_pts == s).mean() * 100:.1f} | {(sim_pts == s).mean() * 100:.1f} |")
    P(f"| 30+ | {(act_pts >= 30).mean() * 100:.1f} | {(sim_pts >= 30).mean() * 100:.1f} |")
    P(f"| <=10 | {(act_pts <= 10).mean() * 100:.1f} | {(sim_pts <= 10).mean() * 100:.1f} |")
    otr = ot.mean(); tie_s = (sc[:, :, 0] == sc[:, :, 1]).mean(); tie_a = (G.hs == G["as"]).mean()
    P(f"\n**OT:** real {G.ot.mean() * 100:.1f}% of games, ties {tie_a * 100:.1f}%; sim OT {otr * 100:.1f}%, ties {tie_s * 100:.1f}%.")
    P(f"**Home field:** real home win {(am > 0).mean() * 100:.1f}% (ties as half: {((am > 0) + 0.5 * (am == 0)).mean() * 100:.1f}%), margin {am.mean():+.2f}; sim home win {((sm_ > 0) + 0.5 * (sm_ == 0)).mean() * 100:.1f}%, margin {sm_.mean():+.2f}.")
    # ------------------------------------------------ B: game-level
    P("\n## B. Game-level predictive accuracy (walk-forward)\n")
    ph = ((sm_ > 0) + 0.5 * (sm_ == 0)).mean(1); simm = sm_.mean(1); simt = st_.mean(1)
    y = ((am > 0) + 0.5 * (am == 0)); yb = (am > 0).astype(int)
    elo = elo_probs(G, S_)
    mkt_p = np.array([cdf(s / 13.5) for s in G.spread]); ml = devig(G.ml_h, G.ml_a)
    def met(name, p, m=None):
        p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4); ok = ~np.isnan(p)
        br = ((p - y)[ok] ** 2).mean(); ll = -(y[ok] * np.log(p[ok]) + (1 - y[ok]) * np.log(1 - p[ok])).mean()
        auc = roc_auc_score(yb[ok], p[ok]) if len(set(yb[ok])) > 1 else np.nan
        mae = np.abs(m - am)[ok].mean() if m is not None else np.nan
        return f"| {name} | {br:.4f} | {ll:.4f} | {auc:.3f} | {mae:.2f} | {((p > .5) == (yb == 1))[ok].mean() * 100:.1f}% |"
    P("| model | Brier | log loss | AUC | margin MAE | pick acc |\n|---|---|---|---|---|---|")
    P(met("coin flip", np.full(ng, .5), np.zeros(ng)))
    P(met("home always (p=.55)", np.full(ng, .55), np.full(ng, 2.0)))
    P(met("Elo", elo[0], elo[1]))
    P(met("closing spread (normal 13.5)", mkt_p, G.spread.values)); P(met("closing moneyline (devigged)", ml, G.spread.values))
    P(met("**GridSim raw**", ph, simm))
    d_br = (ph - y) ** 2 - (mkt_p - y) ** 2; se = d_br.std() / math.sqrt(ng)
    P(f"\nBrier(GridSim) - Brier(market spread) = {d_br.mean():+.4f} +/- {se:.4f} (1 se). Brier(GridSim) - Brier(Elo) = {(((ph - y) ** 2) - ((elo[0] - y) ** 2)).mean():+.4f}.")
    P(f"\nTotals: sim MAE {np.abs(simt - at).mean():.2f}, bias {np.mean(simt - at):+.2f}; closing total MAE {np.nanmean(np.abs(G.total - at)):.2f}, bias {np.nanmean(G.total - at):+.2f}. corr(sim total, closing total) = {np.corrcoef(simt, G.total)[0, 1]:.3f}")
    P(f"corr(sim margin, closing spread) = {np.corrcoef(simm, G.spread)[0, 1]:.3f}; margin RMSE sim {np.sqrt(((simm - am) ** 2).mean()):.2f} vs market {np.sqrt(np.nanmean((G.spread - am) ** 2)):.2f}")
    X = np.c_[np.ones(ng), G.spread, simm - G.spread]; beta, res, *_ = np.linalg.lstsq(X, am, rcond=None)
    resid = am - X @ beta; cov = np.linalg.inv(X.T @ X) * resid.var(ddof=3)
    P(f"Edge test: actual margin = {beta[0]:+.2f} + {beta[1]:.2f}*spread + {beta[2]:+.2f}*(sim - spread);  t(sim-spread) = {beta[2] / math.sqrt(cov[2, 2]):+.2f}  (t near 0 means no information beyond the market).")
    P("\nWin-probability calibration (sim):\n\n| bin | n | predicted | actual |\n|---|---|---|---|")
    bins = pd.cut(ph, [0, .2, .35, .5, .65, .8, 1])
    for b in bins.categories:
        m = (bins == b)
        if m.sum(): P(f"| {b} | {m.sum()} | {ph[m].mean():.3f} | {y[m].mean():.3f} |")
    # variance: sim margin spread vs real
    P(f"\nVariance check: sd of sim margin within a game (avg) = {sm_.std(1).mean():.2f}; real sd(actual margin - market) = {np.nanstd(am - G.spread):.2f}; sd of sim mean margin across games = {simm.std():.2f} vs spread sd {G.spread.std():.2f}.")
    open(f"{OUT}/report_{S_}{a.tag}.md", "w").write("\n".join(L))


def devig(h, a):
    def imp(m): return np.where(m < 0, -m / (-m + 100), 100 / (m + 100))
    ih, ia = imp(np.asarray(h, float)), imp(np.asarray(a, float)); return ih / (ih + ia)


def elo_probs(G, season, K=20, hfa=48):
    gl = pd.read_csv("data/games.csv"); gl = gl[(gl.season >= season - 3) & (gl.season <= season) & gl.home_score.notna()].sort_values(["season", "week", "gameday"])
    R = {}; cur = None; out = {}
    for r in gl.itertuples():
        if r.season != cur:
            if cur is not None:
                for t in R: R[t] = 1505 + (R[t] - 1505) * (2 / 3)
            cur = r.season
        rh, ra = R.get(r.home_team, 1505), R.get(r.away_team, 1505)
        dr = rh - ra + hfa; p = 1 / (1 + 10 ** (-dr / 400))
        out[(r.season, r.week, r.home_team, r.away_team)] = (p, dr / 25 * 1.0)
        res = r.home_score - r.away_score; w = 1.0 if res > 0 else 0.0 if res < 0 else .5
        mult = math.log(abs(res) + 1) * (2.2 / ((rh - ra) * .001 * (1 if res > 0 else -1) + 2.2))
        d = K * mult * (w - p); R[r.home_team] = rh + d; R[r.away_team] = ra - d
    pr = np.array([out[(season, r.week, r.home, r.away)] for r in G.itertuples()]); return pr[:, 0], pr[:, 1]


if __name__ == "__main__":
    main()
