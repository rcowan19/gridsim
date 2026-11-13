import json, time
import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

import db
from tests.fixtures import sleeper as fx

SECRET = "test-jwt-secret-test-jwt-secret-1234"


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A clean database + Supabase-style auth switched on, plus a Sleeper mock so league endpoints work offline."""
    db.reset("sqlite:///" + str(tmp_path / "t.db"))
    for k in ("GRIDSIM_ENV", "STRIPE_SECRET_KEY", "GRIDSIM_ADMIN_EMAILS", "GRIDSIM_FREE_EMAILS", "GRIDSIM_GOOGLE", "GRIDSIM_RATE_PER_MIN", "SUPABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GRIDSIM_AUTH", "on"); monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("GRIDSIM_SECRET_KEY", "u6RUU1wN3c3LLr2c3B9rRS5iFXEOmP2S3o_DMHbQEvU=")
    m = tmp_path / "sl.json"
    m.write_text(json.dumps({"raw": {**fx.raw(), "matchups": {str(k): v for k, v in fx.raw()["matchups"].items()}}, "players": fx.PLAYERS, "live": fx.live(),
                             "find": {"user": {"id": "u1", "name": "roan"}, "leagues": []}}))
    monkeypatch.setenv("GRIDSIM_SLEEPER_MOCK", str(m)); monkeypatch.setenv("GRIDSIM_NFL_STATUS_MOCK", str(tmp_path / "none.json"))
    import server
    yield TestClient(server.app)
    db.reset()


def token(sub="user-1", email="a@b.com", secret=SECRET, exp=3600, aud="authenticated", alg="HS256", **extra):
    return pyjwt.encode({"sub": sub, "email": email, "aud": aud, "exp": int(time.time()) + exp, **extra}, secret, algorithm=alg)


def hdr(**kw):
    return {"Authorization": "Bearer " + token(**kw)}


def pay(uid="user-1", days=30):
    import datetime as dt
    db.upsert_user(uid, "a@b.com")
    db.set_sub(uid, status="active", current_period_end=db.now() + dt.timedelta(days=days), stripe_customer_id="cus_" + uid)
