import datetime as dt, hashlib, hmac, json, time
import pytest, stripe

import billing, db
from tests.conftest import hdr

WH = "whsec_test"


@pytest.fixture
def bill(env, monkeypatch):
    for k, v in dict(STRIPE_SECRET_KEY="sk_test_x", STRIPE_WEBHOOK_SECRET=WH, STRIPE_PRICE_MONTHLY="price_m", STRIPE_PRICE_SEASON="price_s", APP_URL="https://app.example").items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SEASON_PASS_UNTIL", "2027-03-01")
    made = {"customers": [], "sessions": []}
    monkeypatch.setattr(stripe.Customer, "create", staticmethod(lambda **k: made["customers"].append(k) or {"id": "cus_1"}))
    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(lambda **k: made["sessions"].append(k) or {"url": "https://checkout.stripe.test/s"}))
    monkeypatch.setattr(stripe.billing_portal.Session, "create", staticmethod(lambda **k: made.setdefault("portal", k) and {"url": "https://portal.test"}))
    env.made = made
    return env


def send(c, ev, secret=WH, ts=None):
    body = json.dumps(ev).encode(); ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return c.post("/api/stripe/webhook", content=body, headers={"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"})


def event(eid, typ, obj, created=None):
    return {"id": eid, "type": typ, "created": created or int(time.time()), "data": {"object": obj}}


def test_checkout_monthly(bill, monkeypatch):
    monkeypatch.setenv("STRIPE_TRIAL_DAYS", "7")
    r = bill.post("/api/billing/checkout", json={"plan": "monthly"}, headers=hdr()); assert r.status_code == 200 and r.json()["url"].startswith("https://checkout")
    s = bill.made["sessions"][0]
    assert s["mode"] == "subscription" and s["customer"] == "cus_1" and s["line_items"][0]["price"] == "price_m" and s["client_reference_id"] == "user-1"
    assert s["subscription_data"] == {"metadata": {"uid": "user-1"}, "trial_period_days": 7} and s["success_url"] == "https://app.example/?billing=success"
    bill.post("/api/billing/checkout", json={"plan": "season"}, headers=hdr())
    assert len(bill.made["customers"]) == 1                                  # the Stripe customer is reused
    s2 = bill.made["sessions"][1]; assert s2["mode"] == "payment" and s2["line_items"][0]["price"] == "price_s" and s2["metadata"]["plan"] == "season"


def test_checkout_guards(bill):
    assert bill.post("/api/billing/checkout", json={"plan": "monthly"}).status_code == 401
    assert bill.post("/api/billing/checkout", json={"plan": "lifetime"}, headers=hdr()).status_code == 400
    send(bill, event("e0", "customer.subscription.created", {"id": "sub_1", "customer": "cus_1", "status": "active", "metadata": {"uid": "user-1"}, "current_period_end": int(time.time()) + 999999}))
    assert bill.post("/api/billing/checkout", json={"plan": "monthly"}, headers=hdr()).status_code == 409
    assert bill.post("/api/billing/portal", headers=hdr()).json()["url"] == "https://portal.test"
    assert bill.post("/api/billing/portal", headers=hdr(sub="nobody")).status_code == 400


def test_webhook_rejects_bad_signature(bill):
    ev = event("e1", "checkout.session.completed", {})
    assert send(bill, ev, secret="whsec_wrong").status_code == 400
    assert bill.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "nope"}).status_code == 400
    assert send(bill, ev, ts=int(time.time()) - 3600).status_code == 400          # replayed old delivery


def test_season_pass_purchase_and_refund(bill):
    obj = {"mode": "payment", "payment_status": "paid", "customer": "cus_9", "client_reference_id": "user-1", "metadata": {"uid": "user-1", "plan": "season"}}
    assert send(bill, event("e2", "checkout.session.completed", obj)).status_code == 200
    e = db.entitlement("user-1"); assert e["active"] and e["plan"] == "season" and e["until"].startswith("2027-03-01")
    assert send(bill, event("e2", "checkout.session.completed", obj)).json().get("duplicate") is True            # same event twice
    unpaid = dict(obj, payment_status="unpaid", client_reference_id="user-7", metadata={"uid": "user-7", "plan": "season"})
    send(bill, event("e3", "checkout.session.completed", unpaid)); assert not db.entitlement("user-7")["active"]
    send(bill, event("e4", "charge.refunded", {"refunded": True, "customer": "cus_9", "metadata": {"uid": "user-1", "kind": "season"}}))
    assert not db.entitlement("user-1")["active"]


def test_subscription_lifecycle_and_out_of_order(bill):
    end = int(time.time()) + 30 * 86400; base = {"id": "sub_1", "customer": "cus_2", "metadata": {"uid": "user-2"}, "current_period_end": end}
    send(bill, event("s1", "customer.subscription.created", {**base, "status": "active"}, created=1000))
    assert db.entitlement("user-2")["active"] and db.entitlement("user-2")["plan"] == "monthly"
    send(bill, event("s2", "customer.subscription.updated", {**base, "status": "past_due"}, created=2000))
    assert db.get_sub("user-2").status == "past_due"
    send(bill, event("s3", "customer.subscription.updated", {**base, "status": "active"}, created=1500))         # arrives late: ignored
    assert db.get_sub("user-2").status == "past_due"
    send(bill, event("s4", "customer.subscription.deleted", {**base, "status": "canceled"}, created=3000))
    assert db.get_sub("user-2").status == "canceled" and not db.entitlement("user-2")["active"]
    # a later update that carries no uid metadata still finds the user by Stripe customer id
    send(bill, event("s5", "customer.subscription.updated", {"id": "sub_2", "customer": "cus_2", "status": "active", "current_period_end": end}, created=4000))
    assert db.entitlement("user-2")["active"]


def test_prices_fallback_and_labels(bill, monkeypatch):
    billing._price_cache.update(at=0, data=None)
    monkeypatch.setattr(stripe.Price, "retrieve", staticmethod(lambda pid: {"unit_amount": 499 if pid == "price_m" else 2500, "currency": "usd"}))
    assert bill.get("/api/billing/prices").json() == {"monthly": {"amount": 499, "currency": "usd", "label": "$4.99"}, "season": {"amount": 2500, "currency": "usd", "label": "$25"}}
    billing._price_cache.update(at=0, data=None)
    monkeypatch.setattr(stripe.Price, "retrieve", staticmethod(lambda pid: (_ for _ in ()).throw(RuntimeError("down"))))
    assert bill.get("/api/billing/prices").json() == {}
    billing._price_cache.update(at=0, data=None)
