import json
from fastapi.testclient import TestClient
from tests.fixtures import sleeper as fx


def client(monkeypatch, tmp_path):
    p = tmp_path / "sl.json"
    p.write_text(json.dumps({"raw": {**fx.raw(), "matchups": {str(k): v for k, v in fx.raw()["matchups"].items()}}, "players": fx.PLAYERS, "live": fx.live(),
                             "find": {"user": {"id": "u1", "name": "roan"}, "leagues": [{"league_id": "L1", "name": "A", "season": "2026", "teams": 4, "status": "in_season"}]}}))
    monkeypatch.setenv("GRIDSIM_SLEEPER_MOCK", str(p)); monkeypatch.setenv("GRIDSIM_NFL_STATUS_MOCK", str(tmp_path / "none.json"))
    import server
    return TestClient(server.app)


def test_sleeper_league_and_live(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    r = c.get("/api/league", params={"platform": "sleeper", "league": "L1"}); assert r.status_code == 200
    d = r.json(); assert d["platform"] == "sleeper" and len(d["teams"]) == 4 and d["scoring"]["format"] == "half"
    l = c.get("/api/league_live", params={"platform": "sleeper", "league": "L1", "week": 3}).json()
    assert len(l["players"]) == 5 and "games" in l
    f = c.get("/api/sleeper/find", params={"user": "roan"}).json(); assert f["leagues"][0]["league_id"] == "L1"


def test_bad_platform_and_blank(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    assert c.get("/api/league", params={"platform": "yahoo", "league": "1"}).status_code == 400
    assert c.get("/api/league", params={"platform": "espn", "league": " "}).status_code == 400
