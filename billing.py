"""Stripe billing: monthly subscription + one-time season pass. Hosted Checkout and Customer Portal; entitlement comes only from signed webhooks."""
import datetime as dt
import json
import os
import time

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import auth
import db

router = APIRouter()
PLANS = ("monthly", "season")
_price_cache = {"at": 0, "data": None}


def S():
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY") or None
    if not stripe.api_key:
        raise HTTPException(503, "Billing is not configured.")
    return stripe


def app_url():
    return os.environ.get("APP_URL", "http://localhost:8000").rstrip("/")


def season_end(today=None):
    """A season pass runs to March 1 after the NFL season it was bought for (bought in Mar-Aug it covers the coming season)."""
    o = os.environ.get("SEASON_PASS_UNTIL")
    if o:
        return dt.datetime.fromisoformat(o)
    t = today or db.now()
    return dt.datetime(t.year + 1 if t.month >= 3 else t.year, 3, 1)


@router.get("/api/billing/prices")
def prices():
    """Public: what the plans cost, read from Stripe (so the page can never disagree with the checkout)."""
    if _price_cache["data"] is not None and time.time() - _price_cache["at"] < 3600:
        return _price_cache["data"]
    out = {}
    try:
        s = S()
        for plan, env in (("monthly", "STRIPE_PRICE_MONTHLY"), ("season", "STRIPE_PRICE_SEASON")):
            p = s.Price.retrieve(os.environ[env])
            amt = p["unit_amount"] / 100
            out[plan] = {"amount": p["unit_amount"], "currency": p["currency"], "label": (f"${amt:g}" if p["currency"] == "usd" else f"{amt:g} {p['currency'].upper()}")
                         if amt == int(amt) else (f"${amt:.2f}" if p["currency"] == "usd" else f"{amt:.2f} {p['currency'].upper()}")}
    except Exception:           # Stripe unreachable or not configured: the page shows the buttons without a price
        return {}
    _price_cache.update(at=time.time(), data=out)
    return out


class CheckoutIn(BaseModel):
    plan: str


@router.post("/api/billing/checkout")
def checkout(b: CheckoutIn, u=Depends(auth.user)):
    if not auth.auth_on():
        raise HTTPException(400, "Billing is off in local mode.")
    if b.plan not in PLANS:
        raise HTTPException(400, "Pick a plan.")
    ent = db.entitlement(u["id"])
    if ent["active"] and ent["plan"] == b.plan:
        raise HTTPException(409, "You already have this plan.")
    s = S()
    db.upsert_user(u["id"], u["email"])
    row = db.get_sub(u["id"]); cust = row.stripe_customer_id if row else None
    if not cust:
        cust = s.Customer.create(email=u["email"] or None, metadata={"uid": u["id"]})["id"]
        db.set_sub(u["id"], stripe_customer_id=cust)
    args = dict(customer=cust, client_reference_id=u["id"], allow_promotion_codes=True,
                success_url=app_url() + "/?billing=success", cancel_url=app_url() + "/?billing=cancel")
    if os.environ.get("STRIPE_AUTOMATIC_TAX") == "1":
        args.update(automatic_tax={"enabled": True}, customer_update={"address": "auto"})
    if b.plan == "monthly":
        sub = {"metadata": {"uid": u["id"]}}
        trial = int(os.environ.get("STRIPE_TRIAL_DAYS", "0") or 0)
        if trial > 0 and not (row and row.stripe_subscription_id):      # one trial per customer
            sub["trial_period_days"] = trial
        args.update(mode="subscription", line_items=[{"price": os.environ["STRIPE_PRICE_MONTHLY"], "quantity": 1}], subscription_data=sub)
    else:
        args.update(mode="payment", line_items=[{"price": os.environ["STRIPE_PRICE_SEASON"], "quantity": 1}],
                    metadata={"uid": u["id"], "plan": "season"}, payment_intent_data={"metadata": {"uid": u["id"], "kind": "season"}})
    return {"url": s.checkout.Session.create(**args)["url"]}


@router.post("/api/billing/portal")
def portal(u=Depends(auth.user)):
    row = db.get_sub(u["id"])
    if not row or not row.stripe_customer_id:
        raise HTTPException(400, "No billing account yet.")
    return {"url": S().billing_portal.Session.create(customer=row.stripe_customer_id, return_url=app_url() + "/")["url"]}


# ---- webhook: the only place a plan is granted or revoked ----
def _ts(x):
    return dt.datetime.fromtimestamp(x, dt.timezone.utc).replace(tzinfo=None) if x else None


def _uid(obj, cust):
    return (obj.get("metadata") or {}).get("uid") or obj.get("client_reference_id") or (db.uid_for_customer(cust) if cust else None)


def handle(ev):
    t, o, created = ev["type"], ev["data"]["object"], ev.get("created") or 0
    if t in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        uid = _uid(o, o.get("customer"))
        if not uid:
            return
        db.upsert_user(uid, (o.get("customer_details") or {}).get("email") or "")
        if o.get("customer"):
            db.set_sub(uid, stripe_customer_id=o["customer"])
        if o.get("mode") == "payment" and o.get("payment_status") == "paid" and (o.get("metadata") or {}).get("plan") == "season":
            row = db.get_sub(uid); cur = row.season_pass_until if row else None
            db.set_sub(uid, season_pass_until=max(filter(None, [cur, season_end()])))
    elif t in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        uid = _uid(o, o.get("customer"))
        if not uid:
            return
        row = db.get_sub(uid)
        if row and row.last_event_ts and created < row.last_event_ts:      # an older event delivered late must not undo a newer state
            return
        item = ((o.get("items") or {}).get("data") or [{}])[0]
        end = _ts(o.get("current_period_end") or item.get("current_period_end"))
        db.upsert_user(uid, "")
        db.set_sub(uid, stripe_customer_id=o.get("customer"), stripe_subscription_id=o.get("id"),
                   status="canceled" if t.endswith("deleted") else o.get("status"), current_period_end=end, last_event_ts=created)
    elif t == "charge.refunded":
        if o.get("refunded") and (o.get("metadata") or {}).get("kind") == "season":
            uid = _uid(o, o.get("customer"))
            if uid:
                db.set_sub(uid, season_pass_until=None)


@router.post("/api/stripe/webhook")
async def webhook(request: Request):
    payload = await request.body()
    try:
        S().Webhook.construct_event(payload, request.headers.get("stripe-signature", ""), os.environ.get("STRIPE_WEBHOOK_SECRET", ""))
        ev = json.loads(payload)          # verified above; plain dicts are easier to work with than Stripe's objects
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(400, "Bad signature.")
    if db.event_seen(ev["id"]):
        return {"ok": True, "duplicate": True}
    handle(ev)
    db.mark_event(ev["id"])
    return {"ok": True}
