"""Account API: public config for the UI, who am I, and saved leagues (ESPN cookies are stored encrypted)."""
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import auth
import crypto
import db

router = APIRouter()
PLATFORMS = ("espn", "sleeper")


@router.get("/api/config")
def config():
    """Public: what the browser needs to show a sign-in screen."""
    return {"auth": auth.auth_on(), "supabase_url": os.environ.get("SUPABASE_URL") or None, "supabase_anon_key": os.environ.get("SUPABASE_ANON_KEY") or None,
            "platforms": list(PLATFORMS), "trial_days": int(os.environ.get("STRIPE_TRIAL_DAYS", "0") or 0),
            "google": os.environ.get("GRIDSIM_GOOGLE", "").lower() in ("1", "true", "on", "yes")}   # show "Continue with Google" only once the provider is enabled in Supabase


@router.get("/api/me")
def me(u=Depends(auth.user)):
    ent = auth.access(u) if auth.auth_on() else {"active": True, "plan": "dev", "status": "dev", "until": None}
    return {"user": {"id": u["id"], "email": u["email"], "admin": bool(u.get("admin"))}, "plan": ent, "leagues": db.list_leagues(u["id"])}


class LeagueIn(BaseModel):
    platform: str
    league: str
    season: int = 2026
    name: str | None = None
    my_team: str | None = None
    s2: str | None = None
    swid: str | None = None


@router.post("/api/me/leagues")
def add_league(b: LeagueIn, u=Depends(auth.user)):
    if b.platform not in PLATFORMS:
        raise HTTPException(400, f"Unknown platform '{b.platform}'.")
    ref = b.league.strip()
    if not ref or len(ref) > 64:
        raise HTTPException(400, "Enter a league ID.")
    db.upsert_user(u["id"], u["email"])           # auth-off dev user has no row yet
    if len(db.list_leagues(u["id"])) >= int(os.environ.get("GRIDSIM_MAX_LEAGUES", "10")):
        raise HTTPException(400, "You have reached the limit of saved leagues. Remove one first.")
    creds = crypto.seal({"s2": b.s2, "swid": b.swid}) if (b.platform == "espn" and b.s2 and b.swid) else None
    lid = db.save_league(u["id"], b.platform, ref, b.season, (b.name or "")[:200] or None, b.my_team, creds)
    return db.get_league(u["id"], lid)


class LeaguePatch(BaseModel):
    my_team: str | None = None
    name: str | None = None


@router.patch("/api/me/leagues/{lid}")
def patch_league(lid: int, b: LeaguePatch, u=Depends(auth.user)):
    vals = {k: v for k, v in b.model_dump().items() if v is not None}
    if not vals or not db.update_league(u["id"], lid, **vals):
        raise HTTPException(404, "League not found.")
    return db.get_league(u["id"], lid)


@router.delete("/api/me/leagues/{lid}")
def remove_league(lid: int, u=Depends(auth.user)):
    if not db.delete_league(u["id"], lid):
        raise HTTPException(404, "League not found.")
    return {"ok": True}


def saved_creds(u, saved):
    """Resolve a saved league id for the signed-in user -> (platform, league, season, s2, swid). 404 if it is not theirs."""
    r = db.get_league(u["id"], int(saved), with_creds=True)
    if not r:
        raise HTTPException(404, "League not found.")
    c = crypto.unseal(r.pop("creds_enc", None))
    return r["platform"], r["league"], r["season"], c.get("s2") or "", c.get("swid") or ""
