"""Build model.json: league tables, empirical pools, team ratings, player usage.

Data: nflverse play-by-play. Current season weighted by recency, previous season
used as a lower-weight prior. Everything is shrunk toward league average.
"""
import json
import math
import numpy as np
import pandas as pd

RNG = np.random.default_rng(7)
CUR, PREV = "data/pbp_2026.parquet", "data/pbp_2025.parquet"
W_PREV_USE = 0.03      # per-play weight of last season when estimating *usage shares* (carries/targets)
USAGE_DECAY = 0.7      # per-week decay of usage shares inside the current season
W_PREV = 0.35          # weight of every previous-season play
RECENCY = 0.85         # per-week decay inside the current season

COLS = """game_id play_id season week posteam defteam home_team away_team down ydstogo yardline_100 qtr
game_seconds_remaining half_seconds_remaining play_type yards_gained air_yards yards_after_catch complete_pass
interception fumble_lost sack touchdown qb_dropback qb_scramble qb_kneel qb_spike rush_attempt pass_attempt
passer_player_id passer_player_name rusher_player_id rusher_player_name receiver_player_id receiver_player_name
kicker_player_id kicker_player_name kick_distance field_goal_result extra_point_result two_point_conv_result
two_point_attempt punt_attempt kickoff_attempt punt_blocked punter_player_name return_yards return_touchdown
touchback penalty penalty_team penalty_yards first_down_penalty no_huddle score_differential wp cpoe
home_score away_score season_type passing_yards rushing_yards receiving_yards pass_touchdown rush_touchdown""".split()


def player_games(df):
    """Per-(game, player) PPR fantasy points from raw pbp rows."""
    key = ["game_id", "week", "pid"]
    parts = []
    p = df[(df.pass_attempt == 1) & df.passer_player_id.notna()]
    parts.append(p.groupby(["game_id", "week", "passer_player_id"]).agg(pyd=("passing_yards", "sum"), ptd=("pass_touchdown", "sum"), ints=("interception", "sum"), pa=("pass_attempt", "sum")).reset_index().rename(columns={"passer_player_id": "pid"}))
    r = df[(df.rush_attempt == 1) & df.rusher_player_id.notna()]
    parts.append(r.groupby(["game_id", "week", "rusher_player_id"]).agg(ryd=("rushing_yards", "sum"), rtd=("rush_touchdown", "sum"), ra=("rush_attempt", "sum")).reset_index().rename(columns={"rusher_player_id": "pid"}))
    t = df[(df.pass_attempt == 1) & df.receiver_player_id.notna()]
    parts.append(t.groupby(["game_id", "week", "receiver_player_id"]).agg(rec=("complete_pass", "sum"), recyd=("receiving_yards", "sum"), rectd=("pass_touchdown", "sum"), tgt=("pass_attempt", "sum")).reset_index().rename(columns={"receiver_player_id": "pid"}))
    out = parts[0]
    for x in parts[1:]:
        out = out.merge(x, on=key, how="outer")
    out = out.fillna(0)
    out["ppr"] = (0.04 * out.pyd + 4 * out.ptd - 2 * out.ints + 0.1 * (out.ryd + out.recyd)
                  + 6 * (out.rtd + out.rectd) + out.rec)
    return out


def snap_pcts(cutoff=None):
    """{gsis_id: {week: offense snap pct}} from nflverse snap counts (pfr id -> gsis id via rosters)."""
    try:
        s = pd.read_parquet("data/snap_counts_2026.parquet")
    except FileNotFoundError:
        return {}
    m = {}
    for f in ("data/roster_2025.parquet", "data/roster_2026.parquet"):
        r = pd.read_parquet(f)
        m.update({a: b for a, b in zip(r.pfr_id, r.gsis_id) if isinstance(a, str) and isinstance(b, str)})
    if cutoff is not None:
        s = s[s.week < cutoff]
    out = {}
    for pid, wk, pct in zip(s.pfr_player_id, s.week, s.offense_pct):
        g = m.get(pid)
        if g and pct == pct:
            out.setdefault(g, {})[int(wk)] = float(pct)
    return out


def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def shrink(sx, sw, prior, k):
    return (sx + k * prior) / (sw + k)


def wmean(x, w):
    w = np.asarray(w, float)
    return float((np.asarray(x, float) * w).sum() / max(w.sum(), 1e-9))


def wpool(vals, w, n):
    vals = np.asarray(vals, float)
    w = np.asarray(w, float)
    if len(vals) == 0:
        return []
    idx = RNG.choice(len(vals), size=min(n, max(len(vals) * 3, 50)), p=w / w.sum())
    return [int(round(v)) for v in vals[idx]]


def load(cutoff=None):
    """cutoff: only use current-season weeks < cutoff (for walk-forward backtests)."""
    cur = pd.read_parquet(CUR, columns=COLS)
    cur = cur[cur.season_type == "REG"].copy()
    if cutoff is not None:
        cur = cur[cur.week < cutoff].copy()
    maxwk = int(cur.week.max())
    cur["w"] = RECENCY ** (maxwk - cur.week)
    cur["wu"] = USAGE_DECAY ** (maxwk - cur.week)
    prev = pd.read_parquet(PREV, columns=COLS)
    prev = prev[prev.season_type == "REG"].copy()
    prev["w"] = W_PREV
    prev["wu"] = W_PREV_USE
    cur["cur"] = 1
    prev["cur"] = 0
    d = pd.concat([prev, cur], ignore_index=True)
    d = d.sort_values(["season", "game_id", "play_id"]).reset_index(drop=True)
    g = d.groupby("game_id")
    for c in ["game_seconds_remaining", "qtr", "posteam", "yardline_100", "down"]:
        d["next_" + c] = g[c].shift(-1)
    return d, maxwk


def positions():
    pos, team, status = {}, {}, {}
    for f in ["data/roster_2025.parquet", "data/roster_2026.parquet"]:
        r = pd.read_parquet(f)
        for row in r.itertuples():
            if isinstance(row.gsis_id, str):
                pos[row.gsis_id] = row.position
                team[row.gsis_id] = row.team
                status[row.gsis_id] = row.status
    return pos, team, status


def zone(y):          # yardline_100 -> pass-table zone
    return 0 if y > 80 else 1 if y > 50 else 2 if y > 20 else 3 if y > 5 else 4


def dbucket(x):
    return 0 if x <= 1 else 1 if x <= 3 else 2 if x <= 6 else 3 if x <= 10 else 4


def run_zone(y):
    return 0 if y > 20 else 1 if y > 10 else 2 if y > 5 else 3


def air_bucket(a):
    return 0 if a < 0 else 1 if a < 5 else 2 if a < 10 else 3 if a < 15 else 4 if a < 20 else 5


def main(cutoff=None, write=True, quiet=False, usage_decay=None, w_prev_use=None):
    global USAGE_DECAY, W_PREV_USE
    if usage_decay is not None: USAGE_DECAY = usage_decay
    if w_prev_use is not None: W_PREV_USE = w_prev_use
    d, maxwk = load(cutoff)
    pos, rteam, rstatus = positions()
    M = {"meta": {"max_week": maxwk}}

    sc = d[d.play_type.isin(["pass", "run"]) & d.down.notna() & d.posteam.notna()].copy()
    sc["db"] = (sc.qb_dropback == 1).astype(int)
    games = d.drop_duplicates("game_id")

    # ---------------- league actuals (for calibration) ----------------
    gw = games.w
    la = {}
    la["ppg"] = wmean((games.home_score + games.away_score) / 2, gw)
    nteamgames = 2 * len(games)
    dbk = sc[sc.db == 1]
    att = dbk[(dbk.sack == 0) & (dbk.qb_scramble == 0)]
    des = sc[(sc.db == 0) & (sc.rush_attempt == 1)]
    la["plays_pg"] = float(sc.w.sum() / (nteamgames / 2) / (d.w.sum() / sc.w.sum()) * 0 + len(sc) / nteamgames)
    la["pass_rate"] = wmean(sc.db, sc.w)
    la["sack_rate"] = wmean(dbk.sack, dbk.w)
    la["scramble_rate"] = wmean(dbk.qb_scramble, dbk.w)
    la["comp_pct"] = wmean(att.complete_pass, att.w)
    la["int_rate"] = wmean(att.interception, att.w)
    la["ypa"] = wmean(att.yards_gained, att.w)
    la["ypc"] = wmean(des.yards_gained, des.w)
    la["pass_td_pg"] = float(att.touchdown.sum() / nteamgames)
    la["rush_td_pg"] = float(sc[(sc.rush_attempt == 1)].touchdown.sum() / nteamgames)
    la["punts_pg"] = float(d.punt_attempt.sum() / nteamgames)
    la["fga_pg"] = float((d.field_goal_result.notna()).sum() / nteamgames)
    la["to_pg"] = float(((sc.interception == 1) | (sc.fumble_lost == 1)).sum() / nteamgames)
    la["yards_pg"] = float(sc.yards_gained.sum() / nteamgames)
    lgc = wmean(att.cpoe.fillna(0) / 100, att.w)
    M["league_actual"] = la
    lg = {"sack": la["sack_rate"], "scr": la["scramble_rate"], "int": la["int_rate"], "comp": la["comp_pct"],
          "ypc": la["ypc"]}

    # ---------------- pass-call table ----------------
    sc["z"] = sc.yardline_100.map(zone)
    sc["b"] = sc.ydstogo.map(dbucket)
    base = {}
    agg = sc.groupby(["down", "b"]).apply(lambda g: pd.Series({"n": g.w.sum(), "p": wmean(g.db, g.w)}), include_groups=False)
    cells = sc.groupby(["down", "b", "z"]).apply(lambda g: pd.Series({"n": g.w.sum(), "p": wmean(g.db, g.w)}), include_groups=False)
    for (dn, b, z), r in cells.iterrows():
        pr = agg.loc[(dn, b)].p
        base[f"{int(dn)}_{int(b)}_{int(z)}"] = shrink(r.p * r.n, r.n, pr, 40)
    M["pass_table"] = base
    M["pass_default"] = {f"{int(dn)}_{int(b)}": float(r.p) for (dn, b), r in agg.iterrows()}

    # game situation residuals (score diff x time state) in logit space
    def tstate(r):
        return 2 if (r.qtr == 4 and r.game_seconds_remaining <= 120) or (r.qtr == 2 and r.half_seconds_remaining <= 120) else \
               1 if r.qtr == 4 else 0
    sc["ts"] = [2 if (q == 4 and s <= 120) or (q == 2 and h <= 120) else 1 if q == 4 else 0
                for q, s, h in zip(sc.qtr, sc.game_seconds_remaining, sc.half_seconds_remaining)]
    edges = [-100, -17, -9, -4, 0, 1, 4, 9, 17, 100]
    sc["sdb"] = pd.cut(sc.score_differential.fillna(0), edges, right=False, labels=False)
    exp = np.array([base.get(f"{int(a)}_{int(b)}_{int(z)}", 0.55) for a, b, z in zip(sc.down, sc.b, sc.z)])
    sc["exp"] = exp
    resid = {}
    for (sd, ts), g in sc.groupby(["sdb", "ts"]):
        n = g.w.sum()
        r = logit(wmean(g.db, g.w)) - logit(wmean(g.exp, g.w))
        resid[f"{int(sd)}_{int(ts)}"] = float(r * n / (n + 30))
    M["pass_resid"] = resid
    M["sd_edges"] = edges

    # ---------------- 4th down choices ----------------
    f4 = d[(d.down == 4) & d.posteam.notna() & d.wp.between(0.12, 0.88)
           & ((d.play_type.isin(["run", "pass"])) | (d.punt_attempt == 1) | (d.field_goal_result.notna()))].copy()
    f4["ch"] = np.where(f4.punt_attempt == 1, "punt", np.where(f4.field_goal_result.notna(), "fg", "go"))
    f4["zy"] = pd.cut(f4.yardline_100, [-1, 25, 36, 43, 54, 69, 101], labels=False)
    f4["db4"] = pd.cut(f4.ydstogo, [0, 1, 2, 4, 7, 100], labels=False)
    f4 = f4[(f4.half_seconds_remaining > 150)]
    t4 = {}
    for (z, b), g in f4.groupby(["zy", "db4"]):
        n = g.w.sum()
        pr = [wmean(g.ch == c, g.w) for c in ("go", "fg", "punt")]
        t4[f"{int(z)}_{int(b)}"] = [float(x) for x in pr] + [float(n)]
    M["fourth"] = t4

    # ---------------- passes ----------------
    pa = att.copy()
    pa["ab"] = pa.air_yards.fillna(5).map(air_bucket)
    pa["rz"] = (pa.yardline_100 <= 10).astype(int)
    pa_t = pa[pa.receiver_player_id.notna()]
    M["throwaway"] = float(1 - pa_t.w.sum() / pa.w.sum())
    comp = {}
    for (rz, ab), g in pa_t.groupby(["rz", "ab"]):
        n = g.w.sum()
        comp[f"{int(rz)}_{int(ab)}"] = float(wmean(g.complete_pass, g.w))
    M["comp_by_air"] = comp

    yac = {}
    cp = pa[pa.complete_pass == 1]
    for ab, g in cp.groupby("ab"):
        yac[str(int(ab))] = wpool(g.yards_after_catch.fillna(0), g.w, 2500)
    M["yac_pool"] = yac
    M["yac_mean"] = float(wmean(cp.yards_after_catch.fillna(0), cp.w))

    pa["pos"] = pa.receiver_player_id.map(pos).fillna("WR")
    M["air_pool"] = {}
    M["adot_pos"] = {}
    for p in ["WR", "TE", "RB"]:
        g = pa[(pa.pos.isin(["FB", "RB"]) if p == "RB" else pa.pos == p) & pa.air_yards.notna()]
        M["air_pool"][p] = wpool(g.air_yards, g.w, 4000)
        M["adot_pos"][p] = float(wmean(g.air_yards, g.w))
    M["yac_pos"] = {}
    for p in ["WR", "TE", "RB"]:
        g = cp[(cp.receiver_player_id.map(pos).fillna("WR").isin(["FB", "RB"]) if p == "RB" else cp.receiver_player_id.map(pos).fillna("WR") == p)]
        M["yac_pos"][p] = float(wmean(g.yards_after_catch.fillna(0), g.w))

    rzb = [(1, 2), (3, 5), (6, 10), (11, 15), (16, 20)]
    M["air_rz"] = []
    for lo, hi in rzb:
        g = pa_t[pa_t.yardline_100.between(lo, hi) & pa_t.air_yards.notna()]
        M["air_rz"].append({"lo": lo, "hi": hi, "pool": wpool(g.air_yards, g.w, 1500)})

    # situational depth of target: shift in air yards by (down group, distance bucket)
    ta = pa_t[pa_t.air_yards.notna()].copy()
    ta["dg"] = np.where(ta.down == 1, 0, np.where(ta.down == 2, 1, 2))
    ta["db6"] = pd.cut(ta.ydstogo, [0, 2, 4, 7, 10, 15, 100], labels=False)
    amean = wmean(ta.air_yards, ta.w)
    ash = {}
    for (dg, b), g in ta.groupby(["dg", "db6"]):
        n = g.w.sum()
        ash[f"{int(dg)}_{int(b)}"] = float((wmean(g.air_yards, g.w) - amean) * n / (n + 40))
    M["air_shift"] = ash
    # completion and YAC residuals by situation (given air-yards bucket)
    ta["ab"] = ta.air_yards.map(air_bucket)
    ta["rz"] = (ta.yardline_100 <= 10).astype(int)
    ta["ec"] = [comp[f"{r}_{b}"] for r, b in zip(ta.rz, ta.ab)]
    ymean = {int(k): float(np.mean(v)) for k, v in yac.items()}
    cres, yfac = {}, {}
    for (dg, b), g in ta.groupby(["dg", "db6"]):
        n = g.w.sum()
        cres[f"{int(dg)}_{int(b)}"] = float((wmean(g.complete_pass, g.w) - wmean(g.ec, g.w)) * n / (n + 60))
        c = g[g.complete_pass == 1]
        ey = np.array([ymean[int(x)] for x in c.ab])
        nc = c.w.sum()
        ratio = (c.yards_after_catch.fillna(0) * c.w).sum() / max((ey * c.w).sum(), 1e-9)
        yfac[f"{int(dg)}_{int(b)}"] = float(1 + (ratio - 1) * nc / (nc + 60))
    M["comp_resid"], M["yac_fac"] = cres, yfac

    # fumbles lost
    M["fum_pass"] = float(wmean(cp.fumble_lost.fillna(0), cp.w))
    M["fum_rush"] = float(wmean(des.fumble_lost.fillna(0), des.w))
    sk = dbk[dbk.sack == 1]
    M["fum_sack"] = float(wmean(sk.fumble_lost.fillna(0), sk.w))
    M["sack_pool"] = wpool(sk.yards_gained.clip(-20, 0), sk.w, 1500)
    scr = dbk[dbk.qb_scramble == 1]
    M["scramble_pool"] = wpool(scr.yards_gained, scr.w, 1500)
    M["int_return_pool"] = wpool(d[d.interception == 1].return_yards.fillna(0), d[d.interception == 1].w, 800)

    # ---------------- runs ----------------
    des = des.copy()
    des["rzn"] = des.yardline_100.map(run_zone)
    M["run_pool"] = {str(int(z)): wpool(g.yards_gained, g.w, 5000) for z, g in des.groupby("rzn")}
    goal = {}
    for y in range(1, 11):
        g = des[des.yardline_100 == y]
        if len(g) < 60:
            g = des[des.yardline_100.between(y - 1, y + 1)]
        goal[str(y)] = wpool(g.yards_gained, g.w, 1500)
    M["run_goal"] = goal
    sh = des[(des.ydstogo <= 2) & (des.yardline_100 > 5)]
    M["run_pool_short"] = wpool(sh.yards_gained, sh.w, 4000)

    # ---------------- clock ----------------
    sc["el"] = sc.game_seconds_remaining - sc.next_game_seconds_remaining
    ok = sc[(sc.next_qtr == sc.qtr) & sc.el.between(0, 50) & (sc.touchdown == 0)]
    ok = ok[ok.next_posteam == ok.posteam]
    def kind(r):
        pass
    ok = ok.assign(kind=np.where(ok.sack == 1, "sack", np.where(ok.qb_scramble == 1, "run",
                    np.where(ok.db == 0, "run", np.where(ok.complete_pass == 1, "comp", "inc")))))
    clk = {}
    for nh in (0, 1):
        for k in ("run", "comp", "inc", "sack"):
            g = ok[(ok.no_huddle.fillna(0) == nh) & (ok.kind == k)]
            clk[f"{nh}_{k}"] = wpool(g.el, g.w, 1500)
    M["clock"] = clk
    M["clock_mean"] = float(wmean(ok[ok.no_huddle.fillna(0) == 0].el, ok[ok.no_huddle.fillna(0) == 0].w))

    # ---------------- penalties ----------------
    npl = d[(d.play_type == "no_play") & d.penalty_team.notna() & d.down.notna() & (d.punt_attempt != 1) & (d.kickoff_attempt != 1)]
    off_p = npl[npl.penalty_team == npl.posteam]
    def_p = npl[npl.penalty_team == npl.defteam]
    tot = sc.w.sum() + npl.w.sum()
    M["pen_off_rate"] = float(off_p.w.sum() / tot)
    M["pen_def_rate"] = float(def_p.w.sum() / tot)
    M["pen_off_pool"] = wpool(off_p.penalty_yards.fillna(5).clip(0, 15), off_p.w, 600)
    M["pen_def_pool"] = wpool(def_p.penalty_yards.fillna(5).clip(0, 30), def_p.w, 600)
    M["pen_def_auto1"] = float(wmean(def_p.first_down_penalty.fillna(0), def_p.w))

    # ---------------- special teams ----------------
    fg = d[d.field_goal_result.notna() & d.kick_distance.notna()]
    bins = [(15, 29), (30, 34), (35, 39), (40, 44), (45, 49), (50, 54), (55, 70)]
    fgt = []
    for lo, hi in bins:
        g = fg[fg.kick_distance.between(lo, hi)]
        n = g.w.sum()
        fgt.append([float(g.kick_distance.mean()), float(wmean(g.field_goal_result == "made", g.w)), float(n)])
    M["fg_table"] = fgt
    xp = d[d.extra_point_result.notna()]
    M["xp_make"] = float(wmean(xp.extra_point_result == "good", xp.w))
    tp = d[d.two_point_attempt == 1]
    M["two_make"] = float(wmean(tp.two_point_conv_result == "success", tp.w))

    pun = d[(d.punt_attempt == 1) & (d.punt_blocked.fillna(0) == 0) & d.kick_distance.notna() & d.yardline_100.notna()].copy()
    spot = pun.yardline_100 - (pun.kick_distance - pun.return_yards.fillna(0))
    pun["rs"] = np.where(pun.touchback == 1, 80, np.where(spot <= 0, 80, 100 - spot))
    pun["bk"] = pd.cut(pun.yardline_100, [0, 40, 50, 60, 75, 101], labels=False, right=False)
    M["punt_pool"] = {str(int(b)): wpool(g.rs, g.w, 1200) for b, g in pun.groupby("bk")}

    ko = d[d.kickoff_attempt == 1].copy()
    ko = ko[(ko.next_posteam == ko.posteam) & ko.next_yardline_100.notna() & (ko.next_down == 1) & (ko.return_touchdown.fillna(0) == 0)]
    M["ko_pool"] = wpool(ko.next_yardline_100, ko.w, 2500)
    allko = d[d.kickoff_attempt == 1]
    M["ko_td"] = float(wmean(allko.return_touchdown.fillna(0), allko.w))

    # ---------------- team ratings ----------------
    teams = sorted(set(d.posteam.dropna()))
    exp_all = sc.exp.values
    sc["exp_pass"] = exp_all
    T = {}
    for t in teams:
        o = sc[sc.posteam == t]
        df = sc[sc.defteam == t]
        # pass-rate-over-expected in logit units
        neutral = o[(o.score_differential.abs() <= 8) & (o.qtr <= 3)]
        dp = wmean(neutral.db, neutral.w) - wmean(neutral.exp_pass, neutral.w)
        n = neutral.w.sum()
        pass_shift = float(4 * dp * n / (n + 120))
        od, dd = o[o.db == 1], df[df.db == 1]
        oa = od[(od.sack == 0) & (od.qb_scramble == 0)]
        da = dd[(dd.sack == 0) & (dd.qb_scramble == 0)]
        orun = o[(o.db == 0) & (o.rush_attempt == 1)]
        drun = df[(df.db == 0) & (df.rush_attempt == 1)]
        r = {"pass_shift": pass_shift}
        r["sack_off"] = shrink((od.sack * od.w).sum(), od.w.sum(), lg["sack"], 120)
        r["sack_def"] = shrink((dd.sack * dd.w).sum(), dd.w.sum(), lg["sack"], 120)
        r["scr_off"] = shrink((od.qb_scramble * od.w).sum(), od.w.sum(), lg["scr"], 150)
        r["cpoe_def"] = shrink((da.cpoe.fillna(0) * da.w).sum() / 100, da.w.sum(), lgc, 250) - lgc
        r["int_def"] = shrink((da.interception * da.w).sum(), da.w.sum(), lg["int"], 400)
        r["ypc_off"] = shrink((orun.yards_gained * orun.w).sum(), orun.w.sum(), lg["ypc"], 200) - lg["ypc"]
        r["ypc_def"] = shrink((drun.yards_gained * drun.w).sum(), drun.w.sum(), lg["ypc"], 200) - lg["ypc"]
        pace = o[(o.no_huddle.fillna(0) == 0) & o.index.isin(ok.index)]
        r["pace"] = float(shrink((pace.el * pace.w).sum(), pace.w.sum(), M["clock_mean"], 250) / M["clock_mean"])
        T[t] = r
    # opponent adjustment: re-estimate each rating from plays net of the opposing unit's current rating (alternating, damped by shrinkage)
    specs = [("ypc_off", "ypc_def", lambda x: (x.db == 0) & (x.rush_attempt == 1), "yards_gained", 200, lg["ypc"], False),
             ("sack_off", "sack_def", lambda x: x.db == 1, "sack", 120, lg["sack"], True)]
    for off_k, def_k, mask, col, kk, base, absolute in specs:
        sub = sc[mask(sc)]
        for _ in range(4):
            dd_ = {t: T[t][def_k] - (base if absolute else 0) for t in teams}
            do_ = {t: T[t][off_k] - (base if absolute else 0) for t in teams}
            yo = sub[col] - sub.defteam.map(dd_).fillna(0)
            yd = sub[col] - sub.posteam.map(do_).fillna(0)
            new = {}
            for t in teams:
                mo, md = sub.posteam == t, sub.defteam == t
                new[t] = (shrink((yo[mo] * sub.w[mo]).sum(), sub.w[mo].sum(), base, kk) - (0 if absolute else base),
                          shrink((yd[md] * sub.w[md]).sum(), sub.w[md].sum(), base, kk) - (0 if absolute else base))
            for t in teams:
                T[t][off_k], T[t][def_k] = new[t]
    M["team_rates"] = T

    # ---------------- players ----------------
    cur = sc[sc.cur == 1]
    last_weeks = {t: sorted(cur[cur.posteam == t].week.unique())[-4:] for t in teams}
    P = {}

    def pl(pid, name):
        if pid not in P:
            P[pid] = {"id": pid, "name": name, "pos": pos.get(pid, "?"), "team": None}
        return P[pid]

    # receivers
    rec = pa[pa.receiver_player_id.notna()]
    for pid, g in rec.groupby("receiver_player_id"):
        p = pl(pid, g.receiver_player_name.iloc[-1])
        p["_tgt_w"] = float(g.w.sum())
        p["_tgt_u"] = float(g.wu.sum())
        p["_rz_u"] = float(g[g.yardline_100 <= 20].wu.sum())
        p["_adot_x"] = float((g.air_yards.fillna(0) * g.w).sum())
        p["_cpoe_x"] = float((g.cpoe.fillna(0) * g.w).sum() / 100)
        c = g[g.complete_pass == 1]
        p["_rec_w"] = float(c.w.sum())
        p["_yac_x"] = float((c.yards_after_catch.fillna(0) * c.w).sum())
        p["_team_pbp"] = g.sort_values(["season", "week"]).posteam.iloc[-1]
        p["_team_pbp_cur"] = int(g.cur.max())
        p["_recent"] = float(g[g.week.isin(last_weeks.get(p["_team_pbp"], [])) & (g.cur == 1)].shape[0])
    # carries
    for pid, g in des[des.rusher_player_id.notna()].groupby("rusher_player_id"):
        p = pl(pid, g.rusher_player_name.iloc[-1])
        p["_car_w"] = float(g.w.sum())
        p["_car_u"] = float(g.wu.sum())
        p["_gl_u"] = float(g[g.yardline_100 <= 5].wu.sum())
        p["_ypc_x"] = float((g.yards_gained * g.w).sum())
        tm = g.sort_values(["season", "week"]).posteam.iloc[-1]
        p.setdefault("_team_pbp", tm)
        p["_recent"] = p.get("_recent", 0) + float(g[g.week.isin(last_weeks.get(tm, [])) & (g.cur == 1)].shape[0])
    # QBs
    for pid, g in dbk[dbk.passer_player_id.notna()].groupby("passer_player_id"):
        if pos.get(pid) not in (None, "QB"):
            continue          # trick-play throws by WR/RB/TE don't make them quarterbacks
        p = pl(pid, g.passer_player_name.iloc[-1])
        a = g[(g.sack == 0) & (g.qb_scramble == 0)]
        p["_db_w"] = float(g.w.sum())
        p["_att_w"] = float(a.w.sum())
        p["_qb_cpoe_x"] = float((a.cpoe.fillna(0) * a.w).sum() / 100)
        p["_qb_int_x"] = float((a.interception * a.w).sum())
        p["_qb_scr_x"] = float((g.qb_scramble * g.w).sum())
        tm = g.sort_values(["season", "week"]).posteam.iloc[-1]
        p["_team_pbp"] = tm
        p["_recent_db"] = float(g[g.week.isin(last_weeks.get(tm, [])) & (g.cur == 1)].shape[0])
        p["pos"] = "QB"
        p["_team_pbp_cur"] = int(g.cur.max())
    # kickers
    for pid, g in fg.groupby("kicker_player_id"):
        p = pl(pid, g.kicker_player_name.iloc[-1])
        p["pos"] = "K"
        p["_fga"] = float(g.w.sum())
        p["_team_pbp"] = g.sort_values(["season", "week"]).posteam.iloc[-1]
        p["_team_pbp_cur"] = int(g.cur.max())
        exp_fg = np.interp(g.kick_distance, [r_[0] for r_ in fgt], [r_[1] for r_ in fgt])
        p["_fg_over"] = float(((g.field_goal_result == "made") * 1.0 - exp_fg).mul(g.w).sum())

    # injury report: statuses for the target week (backtest: that week's pre-game report; live: next week if published, else latest)
    inj_status, inj_note = {}, "none"
    try:
        inj = pd.read_parquet("data/injuries_2026.parquet")
        inj = inj[inj.report_status.isin(["Out", "Doubtful", "Questionable"])]
        want = cutoff if cutoff is not None else maxwk + 1
        wk_av = inj.week.max() if len(inj) else 0
        use = want if want in set(inj.week) else (None if cutoff is not None else wk_av)
        if use is not None:
            inj_status = dict(zip(inj[inj.week == use].gsis_id, inj[inj.week == use].report_status))
            inj_note = f"week {int(use)}" + ("" if use == want else " (stale: next week not published yet)")
    except FileNotFoundError:
        pass
    M["meta"]["injuries"] = inj_note
    live = cutoff is None
    pg = player_games(d[d.cur == 1])
    ppr_avg = pg.groupby("pid").ppr.mean().to_dict()
    ppr_n = pg.groupby("pid").ppr.count().to_dict()

    # assign players to current roster teams; keep only those active in the current season
    out = {t: {"qbs": [], "skill": [], "k": None} for t in teams}
    team_tot = {}
    for t in teams:
        o = pa[pa.posteam == t]
        d_ = des[des.posteam == t]
        team_tot[t] = {
            "tgt": o.wu.sum(), "rz_tgt": o[o.yardline_100 <= 20].wu.sum(),
            "car": d_.wu.sum(), "gl": d_[d_.yardline_100 <= 5].wu.sum(),
        }
    pos_adot = M["adot_pos"]
    pos_yac = M["yac_pos"]
    for pid, p in P.items():
        tm_pbp = p.get("_team_pbp")
        if tm_pbp is None:
            continue
        has_cur = (p.get("_team_pbp_cur", 1) == 1)
        roster_team = rteam.get(pid, tm_pbp) if rstatus.get(pid) in ("ACT", "RES", "INA", "DEV", "UFA") else tm_pbp
        rt = roster_team if roster_team in out else tm_pbp
        if not has_cur and "_car_w" not in p:
            continue
        if rstatus.get(pid) in (None,) and not has_cur:
            continue
        p["team"] = rt
        T0 = team_tot[tm_pbp]
        q = {"id": pid, "name": p["name"], "pos": p["pos"]}
        if pid in inj_status:
            q["status"] = inj_status[pid]
        if live and rstatus.get(pid) in ("RES", "CUT"):
            q["status"] = "Out"
        if pid in ppr_avg:
            q["ppr_avg"], q["ppr_n"] = round(ppr_avg[pid], 3), int(ppr_n[pid])
        if p["pos"] == "QB":
            if "_db_w" not in p:
                continue
            q["db"] = round(p["_db_w"], 2)
            q["recent"] = p.get("_recent_db", 0)
            q["cpoe"] = shrink(p["_qb_cpoe_x"], p["_att_w"], lgc, 150) - lgc
            q["int"] = shrink(p["_qb_int_x"], p["_att_w"], lg["int"], 300)
            q["scr"] = shrink(p["_qb_scr_x"], p["_db_w"], lg["scr"], 120)
            out[rt]["qbs"].append(q)
            if "_car_w" in p:
                q["car"] = p["_car_u"] / max(T0["car"], 1e-9)
                q["gl"] = (p["_gl_u"] + 8 * q["car"]) / (T0["gl"] + 8)
                q["ypc"] = shrink(p["_ypc_x"], p["_car_w"], lg["ypc"], 60) - lg["ypc"]
            continue
        if p["pos"] == "K":
            q["fg_over"] = p["_fg_over"] / (p["_fga"] + 25)
            q["fga"] = p["_fga"]
            q["cur"] = has_cur
            out[rt]["k"] = out[rt]["k"] if (out[rt]["k"] and out[rt]["k"]["fga"] >= q["fga"]) else q
            continue
        if p["pos"] in ("OL", "DL", "LB", "DB", "P", "LS", "?"):
            if "_tgt_w" not in p and "_car_w" not in p:
                continue
        bucket = "RB" if p["pos"] in ("RB", "FB") else p["pos"] if p["pos"] in ("WR", "TE") else "WR"
        if p["pos"] == "QB":
            continue
        q["pos"] = p["pos"] if p["pos"] in ("WR", "TE", "RB", "FB") else "WR"
        q["bpos"] = bucket
        tw = p.get("_tgt_w", 0)
        q["tgt"] = p.get("_tgt_u", 0) / max(T0["tgt"], 1e-9)
        q["rz_tgt"] = (p.get("_rz_u", 0) + 20 * q["tgt"]) / (T0["rz_tgt"] + 20)
        q["adot"] = shrink(p.get("_adot_x", 0), tw, pos_adot[bucket], 40) - pos_adot[bucket] if tw else 0.0
        q["cpoe"] = (shrink(p.get("_cpoe_x", 0), tw, lgc, 80) - lgc) * 0.5 if tw else 0.0
        rw = p.get("_rec_w", 0)
        pm = pos_yac[bucket]
        q["yac"] = shrink(p.get("_yac_x", 0), rw, pm, 30) / pm if rw else 1.0
        cw = p.get("_car_w", 0)
        q["car"] = p.get("_car_u", 0) / max(T0["car"], 1e-9)
        q["gl"] = (p.get("_gl_u", 0) + 8 * q["car"]) / (T0["gl"] + 8)
        q["ypc"] = shrink(p.get("_ypc_x", 0), cw, lg["ypc"], 80) - lg["ypc"] if cw else 0.0
        q["recent"] = p.get("_recent", 0)
        if q["tgt"] < 0.004 and q["car"] < 0.01:
            continue
        out[rt]["skill"].append(q)

    grp = lambda p: "RB" if p in ("RB", "FB") else "TE" if p == "TE" else "WR"
    pa_t = pa_t.assign(grp=pa_t.receiver_player_id.map(pos).fillna("WR").map(grp))
    lmix = pa_t.groupby("grp").w.sum() / pa_t.w.sum()
    lmix_rz = pa_t[pa_t.yardline_100 <= 20].groupby("grp").w.sum() / pa_t[pa_t.yardline_100 <= 20].w.sum()
    for t in teams:
        g = pa_t[pa_t.posteam == t]
        gz = g[g.yardline_100 <= 20]
        mix = {k: float((g[g.grp == k].w.sum() + 40 * lmix[k]) / (g.w.sum() + 40)) for k in ("WR", "TE", "RB")}
        mixz = {k: float((gz[gz.grp == k].w.sum() + 40 * lmix_rz[k]) / (gz.w.sum() + 40)) for k in ("WR", "TE", "RB")}
        out[t]["mix"], out[t]["mix_rz"] = mix, mixz
    for t in teams:
        o = out[t]
        o["qbs"].sort(key=lambda x: (-x["recent"], -x["db"]))
        o["skill"].sort(key=lambda x: -(x["tgt"] + x["car"]))
        o["skill"] = o["skill"][:18]
        o["qbs"] = o["qbs"][:3]
        ok_ = lambda q: q.get("status") not in ("Out", "Doubtful")
        first = next((q for q in o["qbs"] if ok_(q) and q["recent"] > 0), o["qbs"][0] if o["qbs"] else None)
        for q in o["qbs"]:
            q["active"] = q is first
        for q in o["skill"]:
            q["active"] = bool(q["recent"] > 0 and ok_(q))
        if o["k"] is None:
            o["k"] = {"id": f"K_{t}", "name": "K." + t, "pos": "K", "fg_over": 0.0, "fga": 0, "cur": True}
        # punter name
    pn = d[(d.punt_attempt == 1) & d.punter_player_name.notna()].sort_values(["season", "game_id"]).groupby("posteam").punter_player_name.last()
    for t in teams:
        out[t]["punter"] = pn.get(t, "P." + t)
        out[t]["rates"] = T[t]
    M["teams"] = out
    for t in teams:
        for kk in ("qbs", "skill"):
            for q in out[t][kk]:
                for key in list(q):
                    if isinstance(q[key], float):
                        q[key] = round(q[key], 5)

    snaps = snap_pcts(cutoff)
    hist = {}
    keep = {q["id"] for t in teams for k in ("qbs", "skill") for q in out[t][k]}
    for pid, g in pg.groupby("pid"):
        if pid not in keep:
            continue
        rows = []
        for r in g.sort_values("week").itertuples():
            rows.append([int(r.week), round(r.ppr, 1), int(r.ra), int(r.tgt), int(r.rec), int(r.ryd), int(r.recyd), int(r.pyd),
                         int(r.ptd + r.rtd + r.rectd), round(snaps.get(pid, {}).get(int(r.week), -1), 2)])
        hist[pid] = rows
    M["hist"] = hist
    if write:
        with open("model.json", "w") as f:
            json.dump(M, f, separators=(",", ":"))
    if quiet:
        return M
    print("teams", len(teams), "actual", {k: round(v, 3) for k, v in la.items()})
    for t in ["KC", "BUF", "DET"]:
        if t in out:
            print(t, [(q["name"], q["active"], round(q["tgt"], 3), round(q["car"], 3)) for q in out[t]["skill"][:6]],
                  [(q["name"], q["active"]) for q in out[t]["qbs"]], out[t]["k"]["name"])
    return M


if __name__ == "__main__":
    main()
