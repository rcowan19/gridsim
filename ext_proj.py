"""Weekly log of other sites' pre-game fantasy projections (Sleeper, ESPN), kept next to the model's own so a blend can be judged on evidence.

This only RECORDS them to data/ext_proj.csv (standard PPR points per player, keyed by nflverse gsis id). Nothing here changes a projection or
is shown to users. Each refresh before the week's first kickoff overwrites that week's rows, so what is kept is the last pre-game snapshot; after
kickoff the week is frozen. After a few weeks, `python scripts/ext_blend_eval.py` scores model vs Sleeper vs ESPN vs blends against actual results.

  Sleeper  public, no key: api.sleeper.app/projections/nfl/{season}/{week}  (an undocumented endpoint, so it may change without notice)
  ESPN     needs any league id: GRIDSIM_EXTPROJ_ESPN_LEAGUE (+ GRIDSIM_EXTPROJ_ESPN_S2 and _SWID if that league is private). The raw projected
           stat lines are converted to standard PPR here so every source is on the same scoring.
Both are third parties' proprietary projections: this is for measuring accuracy, not for redistribution.
"""
import json
import os
import urllib.parse
import urllib.request

import pandas as pd

import espn

HERE = os.path.dirname(os.path.abspath(__file__))
SEASON = 2026
COLS = ["season", "week", "source", "gsis_id", "ppr"]


def sleeper_projections(week, season=SEASON, timeout=30):
    """{sleeper_id: PPR points} for QB/RB/WR/TE."""
    q = [("season_type", "regular")] + [("position[]", p) for p in ("QB", "RB", "WR", "TE")]
    url = f"https://api.sleeper.app/projections/nfl/{season}/{week}?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "GridSim/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read().decode())
    out = {}
    for row in raw if isinstance(raw, list) else []:
        pts = (row.get("stats") or {}).get("pts_ppr")
        if row.get("player_id") and isinstance(pts, (int, float)):
            out[str(row["player_id"])] = float(pts)
    return out


def espn_ppr(stats):
    """Standard PPR points from ESPN's raw projected stat line {stat id: value}."""
    return sum(float(stats.get(str(i), stats.get(i, 0)) or 0) * espn.BASE_PPR[k] for i, k in espn.STAT_MAP.items())


def espn_projections(week, season=SEASON):
    """{espn_id: PPR points} from the projected stat lines (source id 1) for `week`."""
    league = os.environ.get("GRIDSIM_EXTPROJ_ESPN_LEAGUE")
    if not league:
        raise RuntimeError("GRIDSIM_EXTPROJ_ESPN_LEAGUE is not set")
    raw = espn.fetch_live(league, season, week, os.environ.get("GRIDSIM_EXTPROJ_ESPN_S2"), os.environ.get("GRIDSIM_EXTPROJ_ESPN_SWID"))
    out = {}
    for p in raw.get("players", []):
        pl = (p.get("playerPoolEntry") or p).get("player") or {}
        if espn.POS.get(pl.get("defaultPositionId")) not in ("QB", "RB", "WR", "TE"):
            continue
        for s in pl.get("stats", []):
            if s.get("scoringPeriodId") == week and s.get("statSourceId") == 1 and s.get("statSplitTypeId") == 1 and s.get("stats"):
                out[str(pl.get("id"))] = round(espn_ppr(s["stats"]), 2)
                break
    return out


SOURCES = {"sleeper": (sleeper_projections, "sleeper_id"), "espn": (espn_projections, "espn_id")}


def upcoming(data_dir):
    """(week, first kickoff as a UTC timestamp) of the first week with a game still to play, from the nflverse schedule."""
    g = pd.read_csv(os.path.join(data_dir, "games.csv"))
    g = g[(g.season == SEASON) & (g.game_type == "REG")]
    wk = g[g.result.isna()].week.min()
    if wk != wk:
        return None, None
    w = g[g.week == wk]
    ko = [pd.Timestamp(f"{d} {t}", tz="America/New_York").tz_convert("UTC") for d, t in zip(w.gameday, w.gametime.fillna("13:00"))]
    return int(wk), min(ko)


def id_map(data_dir, col):
    """{external id as text: gsis id} from the nflverse roster file (ids are stored as floats or text)."""
    r = pd.read_parquet(os.path.join(data_dir, f"roster_{SEASON}.parquet"), columns=["gsis_id", col]).dropna()
    out = {}
    for g, e in zip(r.gsis_id, r[col]):
        try:
            e = str(int(float(e)))
        except ValueError:
            e = str(e)
        out[e] = g
    return out


def log_week(data_dir=None, now=None, sources=None):
    """Save this week's projections from each source. Returns a one-line summary for the server log."""
    data_dir = data_dir or os.path.join(HERE, "data")
    now = now or pd.Timestamp.now(tz="UTC")
    week, kickoff = upcoming(data_dir)
    if week is None:
        return "ext projections: no upcoming week"
    if now >= kickoff:
        return f"ext projections: week {week} has started, snapshot is frozen"
    path = os.path.join(data_dir, "ext_proj.csv")
    old = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame(columns=COLS)
    notes, new = [], []
    for name in sources or SOURCES:
        fetch, idcol = SOURCES[name] if sources is None else (sources[name], SOURCES[name][1])
        try:
            raw = fetch(week)
            ids = id_map(data_dir, idcol)
            rows = [(SEASON, week, name, ids[k], v) for k, v in raw.items() if k in ids]
            new += rows
            notes.append(f"{name} {len(rows)}")
        except Exception as e:                                   # one source being down never blocks the other
            notes.append(f"{name} failed ({str(e)[:60]})")
    if new:
        keep = old[~((old.season == SEASON) & (old.week == week) & old.source.isin({r[2] for r in new}))]
        pd.concat([keep, pd.DataFrame(new, columns=COLS)]).to_csv(path, index=False)
    return f"ext projections week {week}: " + ", ".join(notes)
