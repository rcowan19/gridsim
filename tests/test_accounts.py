import sqlalchemy as sa

import db, espn
from tests.conftest import hdr, pay


def test_saved_league_flow_and_cookie_encryption(env, monkeypatch):
    pay()
    r = env.post("/api/me/leagues", json={"platform": "espn", "league": "1781890288", "name": "Dico", "s2": "SECRET_S2_COOKIE", "swid": "{SWID-123}"}, headers=hdr())
    assert r.status_code == 200 and r.json()["has_creds"] is True and "s2" not in r.json()
    lid = r.json()["id"]
    with db.engine().connect() as c:                                  # the cookies are not in the database in the clear
        raw = c.execute(sa.text("select creds_enc from leagues")).scalar()
    assert raw and "SECRET_S2_COOKIE" not in raw and "SWID-123" not in raw
    seen = {}
    monkeypatch.setattr(espn, "fetch", lambda league, season, s2, swid, **k: seen.update(league=league, s2=s2, swid=swid) or {"teams": [], "settings": {}, "seasonId": 2026})
    assert env.get("/api/league", params={"saved": lid}, headers=hdr()).status_code == 200
    assert seen == {"league": "1781890288", "s2": "SECRET_S2_COOKIE", "swid": "{SWID-123}"}      # server used the stored cookies
    me = env.get("/api/me", headers=hdr()).json(); assert me["leagues"][0]["name"] == "Dico" and "creds_enc" not in str(me)


def test_saved_leagues_are_private_to_their_owner(env):
    pay("user-1"); pay("user-2")
    lid = env.post("/api/me/leagues", json={"platform": "sleeper", "league": "L1", "name": "mine"}, headers=hdr(sub="user-1")).json()["id"]
    other = hdr(sub="user-2", email="c@d.com")
    assert env.get("/api/league", params={"saved": lid}, headers=other).status_code == 404
    assert env.delete(f"/api/me/leagues/{lid}", headers=other).status_code == 404
    assert env.patch(f"/api/me/leagues/{lid}", json={"my_team": "2"}, headers=other).status_code == 404
    assert env.get("/api/me", headers=other).json()["leagues"] == []
    assert env.patch(f"/api/me/leagues/{lid}", json={"my_team": "2"}, headers=hdr(sub="user-1")).json()["my_team"] == "2"
    assert env.get("/api/league", params={"saved": lid}, headers=hdr(sub="user-1")).json()["platform"] == "sleeper"


def test_upsert_validation_and_limit(env, monkeypatch):
    h = hdr()
    assert env.post("/api/me/leagues", json={"platform": "yahoo", "league": "1"}, headers=h).status_code == 400
    assert env.post("/api/me/leagues", json={"platform": "espn", "league": "  "}, headers=h).status_code == 400
    a = env.post("/api/me/leagues", json={"platform": "espn", "league": "5", "name": "x"}, headers=h).json()
    b = env.post("/api/me/leagues", json={"platform": "espn", "league": "5", "my_team": "3"}, headers=h).json()      # same league: updated, not duplicated
    assert a["id"] == b["id"] and b["my_team"] == "3" and b["name"] == "x" and len(env.get("/api/me", headers=h).json()["leagues"]) == 1
    monkeypatch.setenv("GRIDSIM_MAX_LEAGUES", "1")
    assert env.post("/api/me/leagues", json={"platform": "espn", "league": "6"}, headers=h).status_code == 400
