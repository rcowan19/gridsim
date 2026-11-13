import time
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

import auth, db
from tests.conftest import hdr, pay, token, SECRET

LG = {"platform": "sleeper", "league": "L1"}


def test_auth_off_is_open(monkeypatch, tmp_path):
    db.reset("sqlite:///" + str(tmp_path / "o.db")); monkeypatch.setenv("GRIDSIM_AUTH", "off")
    import server
    from fastapi.testclient import TestClient
    c = TestClient(server.app)
    assert c.get("/api/me").json()["plan"]["active"] is True and c.get("/api/config").json()["auth"] is False
    monkeypatch.setenv("GRIDSIM_SLEEPER_MOCK", "/nonexistent"); db.reset()


def test_requires_token(env):
    assert env.get("/api/me").status_code == 401
    assert env.get("/api/me", headers={"Authorization": "Bearer junk"}).status_code == 401
    assert env.get("/api/me", headers=hdr()).status_code == 200


@pytest.mark.parametrize("kw", [dict(secret="x" * 40), dict(exp=-60), dict(aud="anon"), dict(alg="HS512")])
def test_bad_tokens_rejected(env, kw):
    assert env.get("/api/me", headers=hdr(**kw)).status_code == 401


def test_alg_none_rejected(env):
    t = pyjwt.encode({"sub": "u", "aud": "authenticated", "exp": int(time.time()) + 99}, None, algorithm="none")
    assert env.get("/api/me", headers={"Authorization": "Bearer " + t}).status_code == 401


def test_asymmetric_token_via_jwks(env, monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    class FakeClient:
        def __init__(self, *a, **k): pass
        def get_signing_key_from_jwt(self, t): return type("K", (), {"key": key.public_key()})()
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co"); monkeypatch.setattr(auth.jwt, "PyJWKClient", FakeClient); auth._jwks.clear()
    good = pyjwt.encode({"sub": "u9", "email": "q@z.com", "aud": "authenticated", "iss": "https://abc.supabase.co/auth/v1", "exp": int(time.time()) + 99}, key, algorithm="ES256")
    wrong_iss = pyjwt.encode({"sub": "u9", "aud": "authenticated", "iss": "https://evil.example/auth/v1", "exp": int(time.time()) + 99}, key, algorithm="ES256")
    assert env.get("/api/me", headers={"Authorization": "Bearer " + good}).json()["user"]["email"] == "q@z.com"
    assert env.get("/api/me", headers={"Authorization": "Bearer " + wrong_iss}).status_code == 401


def test_plan_gate(env):
    assert env.get("/api/league", params=LG).status_code == 401
    assert env.get("/api/league", params=LG, headers=hdr()).status_code == 402          # signed in, not paid
    assert env.post("/api/slate", json={"week": 9}, headers=hdr()).status_code == 402
    assert env.get("/api/teams", headers=hdr()).status_code == 402
    pay()
    assert env.get("/api/league", params=LG, headers=hdr()).status_code == 200
    assert env.get("/api/teams", headers=hdr()).status_code == 200


def test_expired_and_past_due_access():
    import datetime as dt
    uid = "u-exp"; db.reset("sqlite://"); db.upsert_user(uid, "a@b.com")
    db.set_sub(uid, status="canceled", current_period_end=db.now() + dt.timedelta(days=5)); assert not db.entitlement(uid)["active"]
    db.set_sub(uid, status="past_due", current_period_end=db.now() - dt.timedelta(days=1)); assert db.entitlement(uid)["active"]        # inside the 3-day grace
    db.set_sub(uid, status="past_due", current_period_end=db.now() - dt.timedelta(days=4)); assert not db.entitlement(uid)["active"]
    db.set_sub(uid, status="canceled", season_pass_until=db.now() + dt.timedelta(days=90)); e = db.entitlement(uid); assert e["active"] and e["plan"] == "season"
    db.set_sub(uid, season_pass_until=db.now() - dt.timedelta(days=1)); assert not db.entitlement(uid)["active"]
    db.reset()


def test_admin_only_refresh(env, monkeypatch):
    assert env.post("/api/refresh", headers=hdr()).status_code == 403
    assert env.post("/api/refresh").status_code == 401


def test_rate_limit(env, monkeypatch):
    pay(); monkeypatch.setenv("GRIDSIM_RATE_PER_MIN", "2"); auth._hits.clear()
    codes = [env.get("/api/league", params=LG, headers=hdr()).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_production_config_must_be_complete(monkeypatch):
    monkeypatch.setenv("GRIDSIM_ENV", "production")
    for k in auth.REQUIRED_IN_PROD:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError) as e:
        auth.check_production_config()
    assert "STRIPE_WEBHOOK_SECRET" in str(e.value) and "SUPABASE_URL" in str(e.value)
    for k in auth.REQUIRED_IN_PROD:
        monkeypatch.setenv(k, "x")
    auth.check_production_config()
    monkeypatch.setenv("GRIDSIM_AUTH", "off")
    with pytest.raises(RuntimeError):
        auth.check_production_config()
    assert auth.auth_on() is False and True        # explicit off is reported, never silent


def test_free_access_for_admins_and_listed_emails(env, monkeypatch):
    LGq = {"platform": "sleeper", "league": "L1", "season": 2026}
    stranger = hdr(sub="s1", email="stranger@x.com")
    assert env.get("/api/teams", headers=stranger).status_code == 402                       # nobody is free by default
    monkeypatch.setenv("GRIDSIM_ADMIN_EMAILS", "Owner@Example.com"); monkeypatch.setenv("GRIDSIM_FREE_EMAILS", "friend@x.com, tester@x.com")
    for sub, mail in (("o1", "owner@example.com"), ("f1", "friend@x.com"), ("f2", "tester@x.com")):
        h = hdr(sub=sub, email=mail)
        assert env.get("/api/teams", headers=h).status_code == 200, mail                    # case-insensitive, no Stripe plan needed
        plan = env.get("/api/me", headers=h).json()["plan"]
        assert plan["active"] is True and plan["plan"] == "free"                            # the app shows "Free access", not the paywall
    assert env.get("/api/teams", headers=stranger).status_code == 402                       # still closed to everyone else
    assert env.get("/api/teams", headers=hdr(sub="s2", email="friend@x.com.evil.com")).status_code == 402   # exact match only


def test_paid_plan_wins_over_free_label(env, monkeypatch):
    monkeypatch.setenv("GRIDSIM_ADMIN_EMAILS", "a@b.com"); pay()
    plan = env.get("/api/me", headers=hdr()).json()["plan"]
    assert plan["plan"] == "monthly" and plan["customer"] is True                           # real plan shown; the billing portal still works
