"""Sign-in (Supabase JWTs), plan checks and rate limits as FastAPI dependencies.

Local dev runs with auth OFF (everything open, one built-in user) so the app works exactly as before.
Production (GRIDSIM_ENV=production) always has auth ON and refuses to start with missing configuration.
"""
import collections
import os
import threading
import time

import jwt
from fastapi import Depends, HTTPException, Request

import db

DEV_USER = {"id": "local-dev", "email": "dev@localhost", "admin": True}
REQUIRED_IN_PROD = ["SUPABASE_URL", "SUPABASE_ANON_KEY", "GRIDSIM_SECRET_KEY", "DATABASE_URL", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
                    "STRIPE_PRICE_MONTHLY", "STRIPE_PRICE_SEASON", "APP_URL"]


def production():
    return os.environ.get("GRIDSIM_ENV") == "production"


def auth_on():
    v = os.environ.get("GRIDSIM_AUTH")
    return production() if v is None else v.lower() in ("on", "1", "true")


def check_production_config():
    """Called at startup. A paid app must never come up half configured (open endpoints, no webhook secret, no database)."""
    if not production():
        return
    miss = [k for k in REQUIRED_IN_PROD if not os.environ.get(k)]
    if os.environ.get("GRIDSIM_AUTH", "on").lower() in ("off", "0", "false"):
        miss.append("GRIDSIM_AUTH (must not be off in production)")
    if not (os.environ.get("SUPABASE_JWT_SECRET") or os.environ.get("SUPABASE_URL")):
        miss.append("SUPABASE_JWT_SECRET or SUPABASE_URL")
    if miss:
        raise RuntimeError("Missing production configuration: " + ", ".join(miss))


_jwks = {}


def _verify(token):
    try:
        hdr = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise HTTPException(401, "Sign in to continue.")
    alg = hdr.get("alg")
    if alg == "HS256":                                   # Supabase projects with a shared JWT secret
        key = os.environ.get("SUPABASE_JWT_SECRET")
        if not key:
            raise HTTPException(401, "Sign in to continue.")
    elif alg in ("ES256", "RS256"):                      # projects with asymmetric signing keys: check against the project's published keys
        base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        if not base:
            raise HTTPException(401, "Sign in to continue.")
        url = base + "/auth/v1/.well-known/jwks.json"
        cl = _jwks.get(url) or _jwks.setdefault(url, jwt.PyJWKClient(url, cache_keys=True, lifespan=3600))
        try:
            key = cl.get_signing_key_from_jwt(token).key
        except jwt.PyJWTError:
            raise HTTPException(401, "Sign in to continue.")
    else:
        raise HTTPException(401, "Sign in to continue.")
    iss = (os.environ.get("SUPABASE_URL", "").rstrip("/") + "/auth/v1") if os.environ.get("SUPABASE_URL") else None
    try:
        return jwt.decode(token, key, algorithms=[alg], audience="authenticated", issuer=iss, options={"require": ["exp", "sub"]}, leeway=10)
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Your session expired. Sign in again.")
    except jwt.PyJWTError:
        raise HTTPException(401, "Sign in to continue.")


def admin_emails():
    return {e.strip().lower() for e in os.environ.get("GRIDSIM_ADMIN_EMAILS", "").split(",") if e.strip()}


def free_emails():
    """Emails that get the app without paying: the admins plus GRIDSIM_FREE_EMAILS (testers, friends, press)."""
    extra = {e.strip().lower() for e in os.environ.get("GRIDSIM_FREE_EMAILS", "").split(",") if e.strip()}
    return admin_emails() | extra


def access(u):
    """The user's plan as the app sees it: a real Stripe plan if they have one, otherwise free access for listed emails."""
    ent = db.entitlement(u["id"])
    if not ent["active"] and (u.get("email") or "").lower() in free_emails():
        ent = {**ent, "active": True, "plan": "free", "status": "free", "until": None}
    return ent


def user(request: Request):
    """The signed-in user ({id, email, admin}). With auth off: the built-in local user."""
    if not auth_on():
        return DEV_USER
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in to continue.")
    c = _verify(h[7:].strip())
    email = (c.get("email") or "").lower()
    db.upsert_user(c["sub"], email)
    return {"id": c["sub"], "email": email, "admin": email in admin_emails()}


def plan(u=Depends(user)):
    """Signed in AND paid (monthly or season pass). With auth off: always allowed."""
    if not auth_on():
        return u
    if not access(u)["active"]:
        raise HTTPException(402, "This needs an active plan.")
    return u


def admin(u=Depends(user)):
    if auth_on() and not u.get("admin"):
        raise HTTPException(403, "Admins only.")
    return u


# ---- a small per-user rate limit for the expensive endpoints (in memory: per server process) ----
_hits = collections.defaultdict(collections.deque)
_lock = threading.Lock()


def limited(bucket, per_min=None):
    def dep(u=Depends(plan)):
        if not auth_on():
            return u
        cap = per_min or int(os.environ.get("GRIDSIM_RATE_PER_MIN", "60"))
        t = time.time()
        with _lock:
            q = _hits[(u["id"], bucket)]
            while q and q[0] < t - 60:
                q.popleft()
            if len(q) >= cap:
                raise HTTPException(429, "Slow down a little and try again in a few seconds.", headers={"Retry-After": "10"})
            q.append(t)
        return u
    return dep
