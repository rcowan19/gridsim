"""ESPN fantasy football league reader (public leagues need no login; private ones need espn_s2 + SWID cookies)."""
import json
import urllib.error
import urllib.request

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league}"
VIEWS = ["mTeam", "mRoster", "mSettings", "mMatchup"]
PRO = {1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV",
       14: "LA", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF",
       26: "SEA", 27: "TB", 28: "WAS", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU"}
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}
SLOT = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 7: "OP", 16: "DST", 17: "K", 20: "BN", 21: "IR", 23: "FLEX"}
STAT_REC, STAT_PASS_TD, STAT_PASS_YD, STAT_RUSH_YD, STAT_REC_YD = 53, 4, 3, 24, 42


class EspnError(Exception):
    pass


def fetch(league, season=2026, s2=None, swid=None, timeout=20):
    url = BASE.format(season=season, league=league) + "?" + "&".join("view=" + v for v in VIEWS)
    req = urllib.request.Request(url, headers={"User-Agent": "GridSim/1.0", "Accept": "application/json"})
    if s2 and swid:
        req.add_header("Cookie", f"espn_s2={s2}; SWID={swid}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise EspnError("ESPN says this league is private. Open it in your browser, copy the espn_s2 and SWID cookies "
                            "(espn.com → DevTools → Application → Cookies), and paste them under 'Private league'.")
        if e.code == 404:
            raise EspnError(f"League {league} not found for season {season}. Check the league ID in the ESPN URL.")
        raise EspnError(f"ESPN returned HTTP {e.code}.")
    except urllib.error.URLError as e:
        raise EspnError(f"Could not reach ESPN: {e.reason}")


def normalize(raw):
    teams = []
    for t in raw.get("teams", []):
        name = t.get("name") or ((t.get("location", "") + " " + t.get("nickname", "")).strip()) or f"Team {t.get('id')}"
        roster = []
        for e in (t.get("roster") or {}).get("entries", []):
            p = (e.get("playerPoolEntry") or {}).get("player") or {}
            pos = POS.get(p.get("defaultPositionId"))
            if not pos:
                continue
            roster.append({"id": p.get("id"), "name": p.get("fullName", "?"), "pos": pos, "team": PRO.get(p.get("proTeamId"), "FA"),
                           "slot": SLOT.get(e.get("lineupSlotId"), str(e.get("lineupSlotId"))), "injury": p.get("injuryStatus")})
        teams.append({"id": t.get("id"), "name": name, "abbrev": t.get("abbrev"), "roster": roster,
                      "record": (t.get("record") or {}).get("overall", {})})
    st = raw.get("settings", {})
    counts = {SLOT.get(int(k), k): v for k, v in (st.get("rosterSettings", {}).get("lineupSlotCounts") or {}).items() if v}
    items = {i.get("statId"): i.get("points", 0) for i in (st.get("scoringSettings", {}).get("scoringItems") or [])}
    rec = items.get(STAT_REC, 0)
    week = (raw.get("status") or {}).get("currentMatchupPeriod") or raw.get("scoringPeriodId") or 1
    sched = []
    for m in raw.get("schedule", []):
        h, a = (m.get("home") or {}).get("teamId"), (m.get("away") or {}).get("teamId")
        if h is not None and a is not None:
            sched.append({"week": m.get("matchupPeriodId"), "home": h, "away": a})
    return {"name": st.get("name"), "season": raw.get("seasonId"), "week": week, "teams": teams, "schedule": sched,
            "slots": {k: v for k, v in counts.items() if k not in ("BN", "IR")}, "bench": counts.get("BN", 0),
            "scoring": {"rec": rec, "format": "ppr" if rec >= 0.99 else "half" if rec >= 0.49 else "std",
                        "pass_td": items.get(STAT_PASS_TD), "pass_yd": items.get(STAT_PASS_YD)}}
