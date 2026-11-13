"""Sleeper fantasy football league reader. Sleeper's read API is public and keyless: a league id (or a username, to find it) is enough.

Output of normalize() has the same shape as espn.normalize(), so the UI treats both platforms identically.
Sleeper asks apps to call /players/nfl at most once a day; players() caches it on disk.
"""
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from espn import BASE_PPR

BASE = "https://api.sleeper.app/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
PLAYERS_CACHE = os.path.join(HERE, "data", "sleeper_players.json")
PLAYERS_TTL = 24 * 3600
OFFENSE = {"QB", "RB", "WR", "TE", "K", "DEF"}
TEAM_FIX = {"LAR": "LA", "WSH": "WAS", "JAC": "JAX", "OAK": "LV", "SD": "LAC", "STL": "LA"}   # Sleeper/legacy abbreviations -> GridSim's
SLOT = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "FLEX": "FLEX", "WRRB_FLEX": "FLEX", "REC_FLEX": "FLEX", "SUPER_FLEX": "OP",
        "K": "K", "DEF": "DST", "BN": "BN", "IR": "IR", "TAXI": "BN"}
BENCHISH = {"BN", "IR", "TAXI"}
# scoring rules the engine does not model, shown to the user as "not counted" (same idea as ESPN's `unsupported`)
RULE_NAMES = {"pass_cmp": "pass completions", "pass_inc": "pass incompletions", "pass_att": "pass attempts", "rush_att": "rush attempts",
              "bonus_rec_te": "TE reception bonus", "bonus_rec_rb": "RB reception bonus", "bonus_rec_wr": "WR reception bonus",
              "bonus_pass_yd_300": "300-yd pass bonus", "bonus_pass_yd_400": "400-yd pass bonus", "bonus_rush_yd_100": "100-yd rush bonus",
              "bonus_rush_yd_200": "200-yd rush bonus", "bonus_rec_yd_100": "100-yd rec bonus", "bonus_rec_yd_200": "200-yd rec bonus",
              "pass_2pt": "2pt pass", "rush_2pt": "2pt rush", "rec_2pt": "2pt rec", "fum_lost": "fumbles lost", "fum": "fumbles",
              "pass_int_td": "pick-six", "bonus_pass_cmp_25": "25-completion bonus", "bonus_rush_att_20": "20-carry bonus", "bonus_fd_qb": "first downs (QB)",
              "bonus_fd_rb": "first downs (RB)", "bonus_fd_wr": "first downs (WR)", "bonus_fd_te": "first downs (TE)", "rush_fd": "rush first downs", "rec_fd": "rec first downs"}
_PLAYERS = {"at": 0.0, "data": None}


class SleeperError(Exception):
    pass


def _get(path, timeout=20):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "GridSim/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SleeperError("Sleeper could not find that. Check the league ID or username.")
        if e.code == 429:
            raise SleeperError("Sleeper is rate limiting requests right now. Try again in a minute.")
        raise SleeperError(f"Sleeper returned HTTP {e.code}.")
    except urllib.error.URLError as e:
        raise SleeperError(f"Could not reach Sleeper: {e.reason}")
    except ValueError:
        raise SleeperError("Sleeper sent a response we could not read.")


def find_leagues(username, season=2026):
    """Username -> [{league_id, name, season, teams, status}] so the user can pick which league to connect."""
    u = _get("/user/" + urllib.request.quote(username.strip()))
    if not u or not u.get("user_id"):
        raise SleeperError(f"No Sleeper user named '{username}'.")
    ls = _get(f"/user/{u['user_id']}/leagues/nfl/{int(season)}") or []
    return {"user": {"id": u["user_id"], "name": u.get("display_name") or username},
            "leagues": [{"league_id": l["league_id"], "name": l.get("name"), "season": l.get("season"), "teams": l.get("total_rosters"),
                         "status": l.get("status")} for l in ls]}


def players(refresh=False):
    """{sleeper_id: {name,pos,team,injury}} for QB/RB/WR/TE/K/DEF, cached on disk for a day (Sleeper's player map is ~5 MB)."""
    now = time.time()
    if not refresh and _PLAYERS["data"] is not None and now - _PLAYERS["at"] < PLAYERS_TTL:
        return _PLAYERS["data"]
    if not refresh and os.path.exists(PLAYERS_CACHE) and now - os.path.getmtime(PLAYERS_CACHE) < PLAYERS_TTL:
        _PLAYERS.update(at=os.path.getmtime(PLAYERS_CACHE), data=json.load(open(PLAYERS_CACHE)))
        return _PLAYERS["data"]
    try:
        raw = _get("/players/nfl", timeout=60)
    except SleeperError:
        if os.path.exists(PLAYERS_CACHE):                  # stale beats nothing
            _PLAYERS.update(at=now, data=json.load(open(PLAYERS_CACHE)))
            return _PLAYERS["data"]
        raise
    out = {}
    for pid, p in raw.items():
        pos = p.get("position")
        if pos not in OFFENSE:
            continue
        team = TEAM_FIX.get(p.get("team"), p.get("team")) or "FA"
        if pos == "DEF":
            out[pid] = {"name": f"{team} D/ST", "pos": "DST", "team": team, "injury": None}
        else:
            name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
            out[pid] = {"name": name, "pos": pos, "team": team, "injury": p.get("injury_status")}
    os.makedirs(os.path.dirname(PLAYERS_CACHE), exist_ok=True)
    json.dump(out, open(PLAYERS_CACHE, "w"), separators=(",", ":"))
    _PLAYERS.update(at=now, data=out)
    return out


def regular_weeks(league):
    return max(1, int((league.get("settings") or {}).get("playoff_week_start") or 15) - 1)


def fetch(league_id, season=None):
    """One bundle of raw Sleeper data for a league: league, rosters, users, NFL state and every regular-season matchup week."""
    lid = urllib.request.quote(str(league_id).strip())
    with ThreadPoolExecutor(4) as ex:
        f = {k: ex.submit(_get, p) for k, p in (("league", f"/league/{lid}"), ("rosters", f"/league/{lid}/rosters"),
                                                  ("users", f"/league/{lid}/users"), ("state", "/state/nfl"))}
        raw = {k: v.result() for k, v in f.items()}
    if not raw["league"] or raw["league"].get("sport") not in (None, "nfl"):
        raise SleeperError(f"League {league_id} was not found on Sleeper, or it is not an NFL league.")
    if season and str(raw["league"].get("season")) != str(season):
        raise SleeperError(f"League {league_id} is a {raw['league'].get('season')} league, not {season}. "
                           f"For this season pick the newest league from your username.")

    def wk(w):
        try:
            return w, _get(f"/league/{lid}/matchups/{w}") or []
        except SleeperError:
            return w, []
    with ThreadPoolExecutor(6) as ex:
        raw["matchups"] = dict(ex.map(wk, range(1, regular_weeks(raw["league"]) + 1)))
    return raw


def fetch_live(league_id, week):
    return _get(f"/league/{urllib.request.quote(str(league_id).strip())}/matchups/{int(week)}") or []


def _scoring(ss):
    coef = {"pyd": ss.get("pass_yd", 0), "ptd": ss.get("pass_td", 0), "int": ss.get("pass_int", 0), "ryd": ss.get("rush_yd", 0),
            "rtd": ss.get("rush_td", 0), "recyd": ss.get("rec_yd", 0), "rectd": ss.get("rec_td", 0), "rec": ss.get("rec", 0)}
    coef = {k: float(v or 0) for k, v in coef.items()}
    extra = [f"{RULE_NAMES[k]} ({float(v):+g})" for k, v in ss.items() if k in RULE_NAMES and v]
    n_rules = sum(1 for v in ss.values() if v)
    rec = coef["rec"]
    return {"coef": coef, "base": BASE_PPR, "custom": n_rules >= 4 and any(abs(coef[k] - BASE_PPR[k]) > 1e-9 for k in BASE_PPR),
            "unsupported": extra, "rec": rec, "format": "ppr" if rec >= 0.99 else "half" if rec >= 0.49 else "std",
            "pass_td": coef["ptd"], "pass_yd": coef["pyd"]}


def normalize(raw, pdb=None):
    pdb = pdb if pdb is not None else players()
    lg, st = raw["league"], raw["league"].get("settings") or {}
    users = {u["user_id"]: u for u in raw.get("users") or []}
    positions = lg.get("roster_positions") or []
    starter_slots = [SLOT.get(p, p) for p in positions if p not in BENCHISH]
    counts = {}
    for p in positions:
        s = SLOT.get(p, p)
        counts[s] = counts.get(s, 0) + 1
    teams = []
    for r in raw.get("rosters") or []:
        u = users.get(r.get("owner_id")) or {}
        name = (u.get("metadata") or {}).get("team_name") or u.get("display_name") or f"Team {r.get('roster_id')}"
        slot_of = {}
        for pid, slot in zip(r.get("starters") or [], starter_slots):
            if pid and pid != "0":
                slot_of[pid] = slot
        for pid in r.get("reserve") or []:
            slot_of[pid] = "IR"
        roster = []
        for pid in r.get("players") or []:
            p = pdb.get(pid)
            if not p:
                continue
            roster.append({"id": pid, "name": p["name"], "pos": p["pos"], "team": p["team"], "slot": slot_of.get(pid, "BN"),
                           "injury": (p.get("injury") or "ACTIVE").upper()})
        s = r.get("settings") or {}
        teams.append({"id": r.get("roster_id"), "name": name, "abbrev": "".join(c for c in name if c.isalnum())[:4].upper(), "roster": roster, "owners": [r["owner_id"]] if r.get("owner_id") else [],
                      "record": {"wins": s.get("wins", 0), "losses": s.get("losses", 0), "ties": s.get("ties", 0),
                                 "pointsFor": round((s.get("fpts") or 0) + (s.get("fpts_decimal") or 0) / 100, 2)}})
    sched = []
    for w, ms in sorted((raw.get("matchups") or {}).items()):
        by = {}
        for m in ms:
            if m.get("matchup_id") is not None:
                by.setdefault(m["matchup_id"], []).append(m.get("roster_id"))
        sched += [{"week": int(w), "home": g[0], "away": g[1]} for g in by.values() if len(g) == 2]
    state = raw.get("state") or {}
    week = int(state.get("week") or 0) if str(state.get("season")) == str(lg.get("season")) else int(st.get("leg") or 1)
    week = max(1, week or int(st.get("leg") or 1))
    rw = regular_weeks(lg)
    return {"name": lg.get("name"), "season": int(lg.get("season") or 0) or None, "week": week, "teams": teams, "schedule": sched,
            "slots": {k: v for k, v in counts.items() if k not in ("BN", "IR") and k in ("QB", "RB", "WR", "TE", "FLEX", "OP", "K", "DST")},
            "bench": counts.get("BN", 0) + counts.get("TAXI", 0),
            "settings": {"regular_weeks": rw, "playoff_teams": st.get("playoff_teams"),
                         "playoff_len": 2 if st.get("playoff_round_type") == 2 else 1,       # Sleeper does not document this field well; 1 is the common case
                         "faab": st.get("waiver_type") == 2, "faab_budget": st.get("waiver_budget"), "waiver_days": st.get("waiver_clear_days"),
                         "waivers": [{"id": r.get("roster_id"), "rank": (r.get("settings") or {}).get("waiver_position"),
                                      "faab_spent": (r.get("settings") or {}).get("waiver_budget_used")} for r in raw.get("rosters") or []]},
            "scoring": _scoring(lg.get("scoring_settings") or {})}


def normalize_live(matchups, week, pdb=None):
    """-> [{name,pos,team,pts}]: Sleeper's own points for every rostered player under the league's scoring. Free agents are not included."""
    pdb = pdb if pdb is not None else players()
    out = []
    for m in matchups:
        for pid, pts in (m.get("players_points") or {}).items():
            p = pdb.get(pid)
            if p and pts is not None:
                out.append({"name": p["name"], "pos": p["pos"], "team": p["team"], "pts": round(float(pts), 1)})
    return out
