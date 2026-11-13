import json, os
import espn, sleeper
from tests.fixtures import sleeper as fx


def norm():
    return sleeper.normalize(fx.raw(), fx.PLAYERS)


def test_same_shape_as_espn():
    e = espn.normalize(json.load(open("/tmp/espn_mock.json"))) if os.path.exists("/tmp/espn_mock.json") else None
    n = norm()
    keys = {"name", "season", "week", "teams", "schedule", "slots", "bench", "settings", "scoring"}
    assert keys <= set(n) and (e is None or set(e) == set(n))
    assert {"id", "name", "abbrev", "roster", "record"} <= set(n["teams"][0])
    assert {"id", "name", "pos", "team", "slot", "injury"} <= set(n["teams"][0]["roster"][0])
    assert set(n["settings"]) >= {"regular_weeks", "playoff_teams", "playoff_len", "faab", "faab_budget", "waiver_days", "waivers"}
    assert set(n["scoring"]) >= {"coef", "base", "custom", "unsupported", "rec", "format", "pass_td", "pass_yd"}


def test_slots_and_roster():
    n = norm()
    assert n["slots"] == {"QB": 1, "RB": 1, "WR": 2, "TE": 1, "FLEX": 1, "OP": 1, "K": 1, "DST": 1} and n["bench"] == 2
    t = {x["id"]: x for x in n["teams"]}
    by = {p["name"]: p for p in t[1]["roster"]}
    assert by["Lamar Jackson"]["slot"] == "QB" and by["Alvin Kamara"]["slot"] == "RB" and by["Bench Guy"]["slot"] == "FLEX"
    assert by["DET D/ST"]["slot"] == "DST" and by["Justin Tucker"]["slot"] == "K"
    assert by["Hurt Back"]["slot"] == "IR" and by["Justin Jefferson"]["injury"] == "QUESTIONABLE" and by["Alvin Kamara"]["injury"] == "ACTIVE"
    assert t[1]["name"] == "Roan's Rowdy Team" and t[2]["name"] == "sam" and t[4]["name"] == "Team 4"
    assert t[1]["record"] == {"wins": 3, "losses": 0, "ties": 0, "pointsFor": 412.34}


def test_schedule_week_and_settings():
    n = norm()
    assert n["week"] == 3 and n["settings"]["regular_weeks"] == 3
    assert len(n["schedule"]) == 6 and {"week": 1, "home": 1, "away": 2} in n["schedule"] and {"week": 3, "home": 2, "away": 3} in n["schedule"]
    s = n["settings"]
    assert s["faab"] is True and s["faab_budget"] == 100 and s["waiver_days"] == 2 and s["playoff_teams"] == 2
    assert s["waivers"][0] == {"id": 1, "rank": 1, "faab_spent": 10}


def test_scoring_half_ppr_custom():
    sc = norm()["scoring"]
    assert sc["format"] == "half" and sc["rec"] == 0.5 and sc["custom"] is True        # half PPR differs from the engine's base PPR
    assert sc["coef"]["pyd"] == 0.04 and sc["coef"]["int"] == -2 and sc["coef"]["rtd"] == 6
    assert any("TE reception bonus" in x for x in sc["unsupported"]) and any("fumbles lost" in x for x in sc["unsupported"])
    std = fx.raw(); std["league"]["scoring_settings"] = {"pass_yd": 0.04, "pass_td": 4, "pass_int": -2, "rush_yd": 0.1, "rush_td": 6, "rec": 1, "rec_yd": 0.1, "rec_td": 6}
    s2 = sleeper.normalize(std, fx.PLAYERS)["scoring"]
    assert s2["format"] == "ppr" and s2["custom"] is False and s2["unsupported"] == []


def test_live_points():
    out = sleeper.normalize_live(fx.live(), 3, fx.PLAYERS)
    by = {o["name"]: o for o in out}
    assert by["Alvin Kamara"]["pts"] == 18.4 and by["DET D/ST"]["pts"] == 7.0 and by["Lamar Jackson"]["pts"] == 31.3
    assert len(out) == 5 and all(set(o) == {"name", "pos", "team", "pts"} for o in out)        # unknown id "000" is dropped


def test_season_mismatch_and_errors(monkeypatch):
    def fake(path, timeout=20):
        if path.startswith("/league/L1/matchups"): return []
        return {"/league/L1": fx.raw()["league"], "/league/L1/rosters": [], "/league/L1/users": [], "/state/nfl": {}}[path]
    monkeypatch.setattr(sleeper, "_get", fake)
    assert sleeper.fetch("L1", 2026)["league"]["name"] == "Sleeper Test League"
    try: sleeper.fetch("L1", 2025); assert False
    except sleeper.SleeperError as e: assert "2026 league" in str(e)


def test_find_leagues(monkeypatch):
    calls = {"/user/roan": {"user_id": "u1", "display_name": "roan"},
             "/user/u1/leagues/nfl/2026": [{"league_id": "L1", "name": "A", "season": "2026", "total_rosters": 12, "status": "in_season"}]}
    monkeypatch.setattr(sleeper, "_get", lambda p, timeout=20: calls[p])
    r = sleeper.find_leagues("roan", 2026)
    assert r["user"]["name"] == "roan" and r["leagues"] == [{"league_id": "L1", "name": "A", "season": "2026", "teams": 12, "status": "in_season"}]
    monkeypatch.setattr(sleeper, "_get", lambda p, timeout=20: None)
    try: sleeper.find_leagues("nobody"); assert False
    except sleeper.SleeperError as e: assert "No Sleeper user" in str(e)
