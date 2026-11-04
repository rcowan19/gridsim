"""Monte Carlo runner + summaries + CLI."""
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

from engine import Engine, PSTATS, TSTATS, PIDX, TIDX, ppr_points, env_factors
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
    home, away, n, seed, ov, neutral = args[:6]
    env = env_factors(**args[6]) if len(args) > 6 and args[6] else None
    e = get_engine()
    H, A = e.team(home, ov), e.team(away, ov)
    pids = sorted(set(H.pids) | set(A.pids) | set(e.extra_pids(home)) | set(e.extra_pids(away)))
    ix = {p: i for i, p in enumerate(pids)}
    ps = np.zeros((n, len(pids), len(PSTATS)), dtype=np.int16)
    ts = np.zeros((n, 2, len(TSTATS)), dtype=np.int16)
    sc = np.zeros((n, 2), dtype=np.int16)
    ot = np.zeros(n, dtype=bool)
    import random as _r
    av = _r.Random(seed * 7 + 11)
    unc = bool(e.uncertain(home, ov) or e.uncertain(away, ov))
    for g in range(n):
        h_, a_ = (e.team_draw(home, ov, av), e.team_draw(away, ov, av)) if unc else (H, A)
        r = e.game(h_, a_, seed=seed * 100003 + g, neutral=neutral, env=env)
        sc[g] = r["score"]
        ot[g] = r["ot"]
        ts[g] = r["ts"]
        for pid, v in r["ps"].items():
            if pid in ix:
                ps[g, ix[pid]] = v
    return pids, ps, ts, sc, ot


def simulate(home, away, n=2000, seed=1, overrides=None, neutral=False, pool=None, workers=None, cond=None):
    ov = overrides or {}
    workers = workers or os.cpu_count() or 1
    k = max(1, min(workers * 2, n // 50 or 1))
    sizes = [n // k + (1 if i < n % k else 0) for i in range(k)]
    jobs = [(home, away, s, seed * 1000 + i, ov, neutral, cond) for i, s in enumerate(sizes) if s]
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


def apply_blend(res, spread, total, beta=None, seed=0, home=None, away=None, neutral=False):
    """Reweight simulated games toward the betting line (see vegas.py). Returns (res, info).
    With no line but home/away given, falls back to tilting the margin toward Elo (total stays the sim's)."""
    import vegas
    b = vegas.BETA if beta is None else beta
    raw = res["sc"].mean(0)
    src = "line"
    if vegas.implied(spread, total) is None and home and away:
        spread, total, b, src = vegas.elo_margin(home, away, neutral), float(raw.sum()), vegas.ELO_BETA, "elo"
    idx, info = vegas.blend_index(res["sc"], spread, total, beta=b, seed=seed)
    if info is None:
        return res, None
    info["src"] = src
    out = dict(res, ps=res["ps"][idx], ts=res["ts"][idx], sc=res["sc"][idx], ot=res["ot"][idx])
    info.update(beta=b, sim_home=round(float(raw[0]), 1), sim_away=round(float(raw[1]), 1))
    return out, info


def _q(a):
    a = np.asarray(a, float)
    return {"mean": round(float(a.mean()), 2), "p10": round(float(np.percentile(a, 10)), 1),
            "p50": round(float(np.percentile(a, 50)), 1), "p90": round(float(np.percentile(a, 90)), 1)}


def _hist(a, lo, hi, step):
    edges = np.arange(lo, hi + step, step)
    h, _ = np.histogram(np.clip(a, lo, hi - 1e-9), bins=edges)
    return {"lo": lo, "step": step, "counts": [int(x) for x in h]}


BOOM = {"QB": 25, "RB": 20, "WR": 20, "TE": 15, "FB": 20}
BUST = {"QB": 12, "RB": 7, "WR": 7, "TE": 4, "FB": 7}
SAMPLE_STATS = ["pa", "pc", "pyd", "ptd", "int", "ra", "ryd", "rtd", "tgt", "rec", "recyd", "rectd", "fgm", "fga"]


# ESPN default D/ST scoring
def _dst_points(sacks, forced, allowed, tds=0, saf=0, yds=None):
    pa = np.select([allowed == 0, allowed <= 6, allowed <= 13, allowed <= 17, allowed <= 27, allowed <= 34, allowed <= 45],
                   [5, 4, 3, 1, 0, -1, -3], default=-5)
    ya = 0 if yds is None else np.select([yds < 100, yds < 200, yds < 300, yds < 350, yds < 400, yds < 450, yds < 500, yds < 550],
                                          [5, 3, 2, 0, -1, -3, -5, -6], default=-7)
    return sacks + 2 * forced + 6 * tds + 2 * saf + pa + ya


def _k_points(fgm, fga, xpm, xpa, fgb):
    """ESPN default kicker scoring: FG 3/4/5 by distance, missed FG -1, XP 1, missed XP -1."""
    return 3 * fgm + fgb - (fga - fgm) + xpm - (xpa - xpm)


def game_rows(res, home, away, ov, hist=None, ns=400, with_stats=False, c=None):
    """Rich per-player rows (calibrated fantasy projections + distributions) for one simulated game."""
    e = get_engine()
    c = c or calib.load()
    H, A = e.team(home, ov), e.team(away, ov)
    sc, ts, ps, pids = res["sc"].astype(int), res["ts"], res["ps"], res["pids"]
    n = len(sc)
    scale = c["s"] * c["lam"]
    rows, arrs = [], {}
    for side, cfg, opp in ((0, H, A), (1, A, H)):
        margin = sc[:, side] - sc[:, 1 - side]
        tm = e.m["teams"][cfg.abbr]
        extra = {q["id"]: q for q in tm["qbs"] if q["id"] in e.extra_pids(cfg.abbr, ov)}
        for pid in list(cfg.pids) + [p for p in extra if p not in cfg.players and p in pids]:
            q = cfg.players.get(pid) or extra[pid]
            pos = q.get("pos", "?")
            a = ps[:, pids.index(pid), :].astype(float)
            if a.sum() == 0:
                continue
            if pos == "K":
                pp = _k_points(a[:, PIDX["fgm"]], a[:, PIDX["fga"]], a[:, PIDX["xpm"]], a[:, PIDX["xpa"]], a[:, PIDX["fgb"]])
                cal, mean, lo, hi = pp, pp.mean(), np.percentile(pp, 10), np.percentile(pp, 90)
                sim_mean = mean
            else:
                pp = ppr_points(a)
                mean, lo, hi = (float(x) for x in calib.apply(pp.mean(), np.percentile(pp, 10), np.percentile(pp, 90),
                                                           q.get("ppr_avg", np.nan), q.get("ppr_n", np.nan), c))
                cal = mean + scale * (pp - pp.mean())
                sim_mean = pp.mean()
            r = {"id": pid, "name": q["name"], "pos": pos, "team": cfg.abbr, "opp": opp.abbr, "home": side == 0,
                 "proj": round(float(mean), 2), "lo": round(float(lo), 1), "hi": round(float(hi), 1),
                 "med": round(float(np.median(cal)), 1), "sd": round(float(cal.std()), 2), "sim": round(float(sim_mean), 2),
                 "avg": q.get("ppr_avg"), "n": q.get("ppr_n"), "status": q.get("status"), "p_play": q.get("p_play"), "inj_note": q.get("note"),
                 "boom": round(float((cal >= BOOM.get(pos, 99)).mean()), 3) if pos in BOOM else None,
                 "bust": round(float((cal < BUST.get(pos, -99)).mean()), 3) if pos in BUST else None,
                 "cs": round(q.get("car", 0), 3), "ts": round(q.get("tgt", 0), 3),
                 "st": {k: round(float(a[:, PIDX[k]].mean()), 2) for k in PSTATS if a[:, PIDX[k]].any()},
                 "td1": round(float(((a[:, PIDX["rtd"]] + a[:, PIDX["rectd"]]) >= 1).mean()), 3),
                 "td2": round(float(((a[:, PIDX["rtd"]] + a[:, PIDX["rectd"]]) >= 2).mean()), 3),
                 "opps": round(float((a[:, PIDX["pa"]] if pos == "QB" else a[:, PIDX["ra"]] + a[:, PIDX["tgt"]]).mean()), 1),
                 "hist": _hist(cal, -4, 60, 2),
                 "split": {"win": round(float(cal[margin > 0].mean()), 1) if (margin > 0).any() else None,
                           "loss": round(float(cal[margin < 0].mean()), 1) if (margin < 0).any() else None,
                           "lead10": round(float(cal[margin >= 10].mean()), 1) if (margin >= 10).sum() > 20 else None,
                           "trail10": round(float(cal[margin <= -10].mean()), 1) if (margin <= -10).sum() > 20 else None},
                 "samples": [round(float(x), 1) for x in cal[:ns]]}
            if hist and pid in hist:
                r["trend"] = hist[pid]
            if with_stats:
                r["stat_samples"] = {k: [int(x) for x in a[:ns, PIDX[k]]] for k in SAMPLE_STATS if a[:, PIDX[k]].any()}
            rows.append(r)
            arrs[pid] = cal
        # team defense / special teams scoring
        d = _dst_points(ts[:, 1 - side, TIDX["sacks"]].astype(float), ts[:, 1 - side, TIDX["to"]].astype(float), sc[:, 1 - side],
                        ts[:, side, TIDX["dtd"]].astype(float), ts[:, side, TIDX["saf"]].astype(float), ts[:, 1 - side, TIDX["yds"]].astype(float))
        rows.append({"id": f"DST_{cfg.abbr}", "name": f"{cfg.abbr} D/ST", "pos": "DST", "team": cfg.abbr, "opp": opp.abbr, "home": side == 0,
                     "proj": round(float(d.mean()), 2), "lo": round(float(np.percentile(d, 10)), 1), "hi": round(float(np.percentile(d, 90)), 1),
                     "med": round(float(np.median(d)), 1), "sd": round(float(d.std()), 2), "sim": round(float(d.mean()), 2), "avg": None, "n": None,
                     "status": None, "boom": round(float((d >= 12).mean()), 3), "bust": round(float((d < 2).mean()), 3), "cs": 0, "ts": 0,
                     "st": {"sacks": round(float(ts[:, 1 - side, TIDX["sacks"]].mean()), 2), "tkw": round(float(ts[:, 1 - side, TIDX["to"]].mean()), 2),
                            "pa": round(float(sc[:, 1 - side].mean()), 1)}, "td1": 0, "td2": 0, "opps": 0, "hist": _hist(d, -4, 60, 2),
                     "split": {}, "samples": [round(float(x), 1) for x in d[:ns]]})
    return rows, arrs


def vegas_for(home, away, week=None):
    for g in sorted(SCHEDULE, key=lambda g: (g["played"], g["week"])):
        if g["home"] == home and g["away"] == away and (week is None or g["week"] == week):
            return g
    return None


def summarize(res, home, away, overrides=None, hist=None):
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
                    "home": _hist(sc[:, 0], 0, 60, 1), "away": _hist(sc[:, 1], 0, 60, 1)},
           "cover": {"home_by_3": round(float((margin >= 3).mean()), 3), "home_by_7": round(float((margin >= 7).mean()), 3),
                     "away_by_3": round(float((margin <= -3).mean()), 3), "away_by_7": round(float((margin <= -7).mean()), 3),
                     "over_45": round(float((total > 45).mean()), 3), "over_50": round(float((total > 50).mean()), 3),
                     "one_score": round(float((np.abs(margin) <= 8).mean()), 3), "blowout": round(float((np.abs(margin) >= 17).mean()), 3)}}
    out["teamstats"] = {name: {k: round(float(ts[:, side, TIDX[k]].mean()), 2) for k in TSTATS} for side, name in ((0, home), (1, away))}
    out["teamstats_sd"] = {name: {k: round(float(ts[:, side, TIDX[k]].std()), 2) for k in TSTATS} for side, name in ((0, home), (1, away))}
    out["players"] = {}
    for cfg in (H, A):
        rows = []
        for pid in cfg.pids:
            q = cfg.players[pid]
            a = ps[:, pids.index(pid), :]
            if a.sum() == 0:
                continue
            row = {"id": pid, "name": q["name"], "pos": q.get("pos", "?"), "team": cfg.abbr}
            for k in PSTATS:
                if a[:, PIDX[k]].any():
                    row[k] = _q(a[:, PIDX[k]])
            rows.append(row)
        out["players"][cfg.abbr] = rows
    rows, arrs = game_rows(res, home, away, ov, hist, ns=500, with_stats=True)
    out["rows"] = rows
    # correlations among the top fantasy players of both teams
    top = [r["id"] for t in (home, away) for r in sorted([r for r in rows if r["team"] == t and r["pos"] in ("QB", "RB", "WR", "TE")],
                                                         key=lambda r: -r["proj"])[:7]]
    M = np.corrcoef(np.array([arrs[p] for p in top])) if len(top) > 1 else np.zeros((1, 1))
    nm = {r["id"]: r for r in rows}
    out["corr"] = {"ids": top, "names": [nm[p]["name"] for p in top], "teams": [nm[p]["team"] for p in top], "pos": [nm[p]["pos"] for p in top],
                   "m": [[round(float(x), 2) if x == x else 0 for x in row] for row in M]}
    v = vegas_for(home, away)
    if v and v["spread"] is not None:
        out["vegas"] = {"week": v["week"], "day": v["day"], "spread": v["spread"], "total": v["total"],
                        "home_total": round((v["total"] + v["spread"]) / 2, 1), "away_total": round((v["total"] - v["spread"]) / 2, 1)}
    return out


def simulate_slate(week, n=500, pool=None, hist=None, ns=300, weather=None, ov=None):
    games = [g for g in SCHEDULE if g["week"] == week]
    ov = dict(ov or {})          # manual injury edits: {player id: False (out) | True (plays)}
    hit = set()                  # teams with an edit: the betting line no longer describes them, so no blend
    for abbr, t in get_engine().m["teams"].items():
        if any(q["id"] in ov for q in t["qbs"] + t["skill"]):
            hit.add(abbr)
    weather = weather or {}
    conds = [{"roof": g.get("roof"), "wind": (weather.get(g["id"]) or {}).get("wind"), "temp": (weather.get(g["id"]) or {}).get("temp")} for g in games]
    # split every game into chunks so large simulation counts spread evenly across all CPU cores
    CH = 2500
    jobs, owner = [], []
    for i, g in enumerate(games):
        left, c = n, 0
        while left > 0:
            sz = min(CH, left)
            jobs.append((g["home"], g["away"], sz, 7000 + i * 97 + c, ov, g["neutral"], conds[i])); owner.append(i)
            left -= sz; c += 1
    out = pool.map(run_chunk, jobs) if pool is not None else [run_chunk(j) for j in jobs]
    parts = []
    for i in range(len(games)):
        mine = [o for o, k in zip(out, owner) if k == i]
        parts.append((mine[0][0], np.concatenate([x[1] for x in mine]), np.concatenate([x[2] for x in mine]), np.concatenate([x[3] for x in mine]), np.concatenate([x[4] for x in mine])))
    rows, meta = [], []
    c = calib.load()
    for g, p in zip(games, parts):
        res = {"pids": p[0], "ps": p[1], "ts": p[2], "sc": p[3], "ot": p[4]}
        edited = g["home"] in hit or g["away"] in hit
        res, binfo = apply_blend(res, None if edited else g.get("spread"), None if edited else g.get("total"), seed=g["week"], **({} if edited or g["played"] else {"home": g["home"], "away": g["away"], "neutral": g["neutral"]}))
        gr, _ = game_rows(res, g["home"], g["away"], ov, hist, ns=ns, with_stats=False, c=c)
        sc = res["sc"].astype(int)
        m = {"id": g["id"], "home": g["home"], "away": g["away"], "day": g["day"], "time": g["time"], "neutral": g["neutral"],
             "sim_home_win": round(float((sc[:, 0] > sc[:, 1]).mean()), 3), "sim_margin": round(float((sc[:, 0] - sc[:, 1]).mean()), 1),
             "sim_total": round(float(sc.sum(1).mean()), 1), "sim_home": round(float(sc[:, 0].mean()), 1), "sim_away": round(float(sc[:, 1].mean()), 1),
             "spread": g["spread"], "total": g["total"], "played": g["played"], "roof": g.get("roof"), "cond": conds[len(meta)],
             "ot": round(float(res["ot"].mean()), 3), "blend": binfo, "away_sc": _q(sc[:, 1]), "home_sc": _q(sc[:, 0]),
             "hist_margin": _hist(sc[:, 0] - sc[:, 1], -40, 40, 4), "hist_total": _hist(sc.sum(1), 10, 90, 4),
             "p_home_by7": round(float(((sc[:, 0] - sc[:, 1]) >= 7).mean()), 3), "p_away_by7": round(float(((sc[:, 1] - sc[:, 0]) >= 7).mean()), 3)}
        for r in gr:
            r["game"] = g["id"]
            r["game_total"] = m["sim_total"]
            r["team_total"] = m["sim_home"] if r["home"] else m["sim_away"]
        rows += gr
        meta.append(m)
    return {"week": week, "n": n, "games": meta, "rows": rows, "rows_idle": idle_rows(week, games, rows, hist, ov), "edited": sorted(hit)}


def idle_rows(week, games, rows, hist=None, ov=None):
    ov = ov or {}
    """Zero-point entries for rostered players who will not play this week (bye, out, inactive, backup),
    so they can still be found, kept on rosters and moved around in the app."""
    e = get_engine()
    have = {r["id"] for r in rows}
    opp = {}
    for g in games:
        opp[g["home"]] = (g["away"], True, g["id"]); opp[g["away"]] = (g["home"], False, g["id"])
    out = []
    for abbr, t in e.m["teams"].items():
        o = opp.get(abbr)
        cands = [dict(q, pos=q.get("pos", "QB")) for q in t["qbs"]] + list(t["skill"])
        if t.get("k"):
            cands.append(dict(t["k"], pos="K"))
        cands.append({"id": f"DST_{abbr}", "name": f"{abbr} D/ST", "pos": "DST"})
        for q in cands:
            if q["id"] in have:
                continue
            if o is None:
                why = "BYE"
            elif ov.get(q["id"]) is False:
                why = "OUT"
            elif q.get("status") in ("Out", "Doubtful"):
                why = q["status"].upper()
            else:
                why = "INACTIVE"
            r = {"id": q["id"], "name": q["name"], "pos": q["pos"], "team": abbr, "opp": o[0] if o else None, "home": bool(o and o[1]),
                 "proj": 0.0, "lo": 0.0, "hi": 0.0, "med": 0.0, "sd": 0.0, "sim": 0.0, "avg": q.get("ppr_avg"), "n": q.get("ppr_n"),
                 "status": "Out" if why in ("OUT", "DOUBTFUL") else q.get("status"), "p_play": None, "inj_note": "Out (your edit)" if ov.get(q["id"]) is False else q.get("note"),
                 "boom": 0.0 if q["pos"] in BOOM else None, "bust": 1.0 if q["pos"] in BUST else None,
                 "cs": round(q.get("car", 0), 3), "ts": round(q.get("tgt", 0), 3), "st": {}, "td1": 0.0, "td2": 0.0, "opps": 0.0,
                 "hist": _hist(np.zeros(10), -4, 60, 2), "split": {}, "samples": [], "game": None, "game_total": None, "team_total": None,
                 "idle": why}
            if hist and q["id"] in hist:
                r["trend"] = hist[q["id"]]
            out.append(r)
            have.add(q["id"])
    return out


def _load_schedule():
    try:
        import schedule
        return schedule.load()
    except Exception:
        return []


SCHEDULE = _load_schedule()


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
    for r_ in sorted(s["rows"], key=lambda r: -r["proj"])[:12]:
        print(f"  {r_['name']:16s} {r_['pos']:3s} proj {r_['proj']:5.1f}  range {r_['lo']}-{r_['hi']}  boom {r_['boom']}")
