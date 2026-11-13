"""GridSim web server:  python server.py  ->  http://localhost:8000"""
import json
import os
import random
from contextlib import asynccontextmanager
from multiprocessing import Pool

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import accounts
import auth
import billing
import db
import sim
from engine import PSTATS, TSTATS, env_factors

HERE = os.path.dirname(os.path.abspath(__file__))
TEAMS = {
    "ARI": ("Arizona Cardinals", "#97233F"), "ATL": ("Atlanta Falcons", "#A71930"), "BAL": ("Baltimore Ravens", "#3B2A8C"),
    "BUF": ("Buffalo Bills", "#00338D"), "CAR": ("Carolina Panthers", "#0085CA"), "CHI": ("Chicago Bears", "#C83803"),
    "CIN": ("Cincinnati Bengals", "#E04E10"), "CLE": ("Cleveland Browns", "#C2410C"), "DAL": ("Dallas Cowboys", "#003594"),
    "DEN": ("Denver Broncos", "#D9480F"), "DET": ("Detroit Lions", "#0076B6"), "GB": ("Green Bay Packers", "#2A5A45"),
    "HOU": ("Houston Texans", "#A71930"), "IND": ("Indianapolis Colts", "#1E4C9A"), "JAX": ("Jacksonville Jaguars", "#00747F"),
    "KC": ("Kansas City Chiefs", "#C8102E"), "LA": ("Los Angeles Rams", "#1F4DB0"), "LAC": ("Los Angeles Chargers", "#0072B8"),
    "LV": ("Las Vegas Raiders", "#4B5156"), "MIA": ("Miami Dolphins", "#00858E"), "MIN": ("Minnesota Vikings", "#4F2683"),
    "NE": ("New England Patriots", "#1B3A6B"), "NO": ("New Orleans Saints", "#8C7335"), "NYG": ("New York Giants", "#0B2265"),
    "NYJ": ("New York Jets", "#125740"), "PHI": ("Philadelphia Eagles", "#00636C"), "PIT": ("Pittsburgh Steelers", "#A87900"),
    "SEA": ("Seattle Seahawks", "#3F7D1A"), "SF": ("San Francisco 49ers", "#AA0000"), "TB": ("Tampa Bay Buccaneers", "#B80A0A"),
    "TEN": ("Tennessee Titans", "#2C6FB5"), "WAS": ("Washington Commanders", "#6B1D2A"),
}
MODEL = sim.load_model()
POOL = None


@asynccontextmanager
async def lifespan(app):
    global POOL
    auth.check_production_config()      # a paid app refuses to start half-configured
    db.engine()
    POOL = Pool(os.cpu_count() or 1, initializer=sim._init)
    hrs = float(os.environ.get("GRIDSIM_REFRESH_HOURS", "0") or 0)
    if hrs > 0:                                     # keep injuries, depth charts and results fresh without anyone pressing a button
        import threading
        threading.Thread(target=_refresh_loop, args=(hrs,), daemon=True).start()
    yield
    POOL.terminate()


app = FastAPI(lifespan=lifespan)
app.include_router(accounts.router)
app.include_router(billing.router)


def _stamp():
    import datetime
    p = os.path.join(HERE, "data", "injuries_2026.parquet")
    return datetime.datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="minutes") if os.path.exists(p) else None


@app.get("/api/data_status")
def data_status():
    return {"updated": _stamp()}


def do_refresh():
    """Pull the latest nflverse data (injury reports, depth charts, results), rebuild the model, swap in fresh workers.
    The old workers finish the sims already running instead of being killed."""
    global MODEL, POOL
    import subprocess, sys, threading
    with REFRESH_LOCK:
        for script in ("fetch_data.py", "build_model.py"):
            p = subprocess.run([sys.executable, script], cwd=HERE, capture_output=True, text=True, timeout=1800)
            if p.returncode:
                raise RuntimeError(f"{script} failed: {(p.stderr or p.stdout)[-300:]}")
        MODEL = sim.load_model()
        import vegas; vegas.reset()
        sim._E = None
        old, POOL = POOL, Pool(os.cpu_count() or 1, initializer=sim._init)
        old.close()
        threading.Thread(target=old.join, daemon=True).start()
    return {"updated": _stamp()}


REFRESH_LOCK = __import__("threading").Lock()


@app.post("/api/refresh", dependencies=[Depends(auth.admin)])
def refresh():
    try:
        return do_refresh()
    except RuntimeError as e:
        raise HTTPException(500, str(e))


@app.get("/healthz")
def healthz():
    return {"ok": True, "model": bool(MODEL), "pool": POOL is not None}


def _refresh_loop(hours):
    import time
    while True:
        time.sleep(hours * 3600)
        try:
            do_refresh()
            print("data refreshed", _stamp(), flush=True)
        except Exception as e:                      # a failed refresh keeps serving the last good data
            print("scheduled refresh failed:", e, flush=True)


class Req(BaseModel):
    home: str
    away: str
    n: int = 2000
    seed: int | None = None
    neutral: bool = False
    overrides: dict[str, bool] = {}
    cond_mode: str = "auto"          # auto (schedule roof + forecast) | manual | none
    roof: str | None = None          # manual: dome | outdoors
    wind: float | None = None        # manual mph
    temp: float | None = None        # manual F
    vegas: bool = True               # blend toward the betting line (scheduled games, no lineup overrides)


def check(r):
    for t in (r.home, r.away):
        if t not in MODEL["teams"]:
            raise HTTPException(400, f"unknown team {t}")
    if r.home == r.away:
        raise HTTPException(400, "pick two different teams")


@app.get("/api/teams", dependencies=[Depends(auth.plan)])
def teams():
    out = {}
    for abbr, t in MODEL["teams"].items():
        nm, col = TEAMS.get(abbr, (abbr, "#888888"))
        players = [dict(id=q["id"], name=q["name"], pos=q["pos"], active=q["active"], grp="QB", status=q.get("status")) for q in t["qbs"]]
        players += [dict(id=q["id"], name=q["name"], pos=q["pos"], active=q["active"], grp="skill", status=q.get("status"),
                         tgt=round(q["tgt"], 3), car=round(q["car"], 3)) for q in t["skill"]]
        out[abbr] = {"name": nm, "color": col, "players": players, "kicker": t["k"]["name"], "punter": t["punter"], "rates": t["rates"]}
    keys = next(iter(MODEL["teams"].values()))["rates"].keys()
    lg = {k: sum(t["rates"][k] for t in MODEL["teams"].values()) / len(MODEL["teams"]) for k in keys}
    return {"teams": out, "meta": MODEL["meta"], "league": MODEL["league_actual"], "league_rates": lg}


@app.get("/api/schedule", dependencies=[Depends(auth.plan)])
def schedule_():
    weeks = sorted({g["week"] for g in sim.SCHEDULE})
    nxt = next((w for w in weeks if any(g["week"] == w and not g["played"] for g in sim.SCHEDULE)), weeks[-1] if weeks else None)
    return {"games": sim.SCHEDULE, "weeks": weeks, "next_week": nxt}


@app.get("/api/espn", dependencies=[Depends(auth.limited("league"))])
def espn_league(league: str, season: int = 2026, s2: str = "", swid: str = ""):
    import espn
    try:
        mock = os.environ.get("GRIDSIM_ESPN_MOCK")        # dev/testing: serve a saved ESPN payload instead of calling ESPN
        raw = json.load(open(mock)) if mock else espn.fetch(league, season, s2 or None, swid or None)
        return espn.normalize(raw)
    except espn.EspnError as e:
        raise HTTPException(502, str(e))


@app.get("/api/espn_live", dependencies=[Depends(auth.limited("league"))])
def espn_live(league: str, week: int, season: int = 2026, s2: str = "", swid: str = ""):
    """Actual (or in-progress) fantasy points for a week, straight from ESPN under the league's own scoring."""
    import espn, time
    try:
        mock = os.environ.get("GRIDSIM_ESPN_LIVE_MOCK")      # dev/testing: serve a saved payload
        raw = json.load(open(mock)) if mock else espn.fetch_live(league, season, week, s2 or None, swid or None)
        players = espn.normalize_live(raw, week)
    except espn.EspnError as e:
        raise HTTPException(502, str(e))
    # game status is best-effort: the page falls back to kickoff times when the scoreboard is unreachable
    games, games_err = [], None
    try:
        gm = os.environ.get("GRIDSIM_NFL_STATUS_MOCK")
        games = espn.normalize_scoreboard(json.load(open(gm)) if gm else espn.fetch_scoreboard(season, week))
    except (espn.EspnError, OSError, ValueError) as e:
        games_err = str(e)
    return {"week": week, "fetched": time.strftime("%Y-%m-%dT%H:%M:%S"), "players": players, "games": games, "games_err": games_err}


# ---- multi-platform league access: one URL family for ESPN and Sleeper (the UI uses these; /api/espn* stay for older callers) ----
def _games_status(season, week):
    import espn
    try:
        gm = os.environ.get("GRIDSIM_NFL_STATUS_MOCK")
        return espn.normalize_scoreboard(json.load(open(gm)) if gm else espn.fetch_scoreboard(season, week)), None
    except (espn.EspnError, OSError, ValueError) as e:
        return [], str(e)


def _sleeper_mock():
    m = os.environ.get("GRIDSIM_SLEEPER_MOCK")             # dev/testing: {"find": ..., "raw": ..., "players": ..., "live": [...]}
    return json.load(open(m)) if m else None


@app.get("/api/league", dependencies=[Depends(auth.limited("league"))])
def league(platform: str = "espn", league: str = "", season: int = 2026, s2: str = "", swid: str = "", me: str = "", saved: int | None = None,
           u=Depends(auth.user)):
    import espn, sleeper
    if saved is not None:           # a saved league: use the stored (encrypted) credentials, the browser never needs the cookies again
        platform, league, season, s2, swid = accounts.saved_creds(u, saved)
    if not league.strip():
        raise HTTPException(400, "Enter a league ID.")
    try:
        if platform == "espn":
            mock = os.environ.get("GRIDSIM_ESPN_MOCK")
            out = espn.normalize(json.load(open(mock)) if mock else espn.fetch(league, season, s2 or None, swid or None))
        elif platform == "sleeper":
            mk = _sleeper_mock()
            out = sleeper.normalize(mk["raw"], mk["players"]) if mk else sleeper.normalize(sleeper.fetch(league, season))
        else:
            raise HTTPException(400, f"Unknown platform '{platform}'.")
    except (espn.EspnError, sleeper.SleeperError) as e:
        raise HTTPException(502, str(e))
    out["platform"] = platform
    # which team is the user's, when the platform tells us (ESPN: their SWID owns it; Sleeper: their user id owns it)
    who = (swid or "").strip().lower() if platform == "espn" else (me or "").strip().lower()
    out["my_team_guess"] = next((t["id"] for t in out["teams"] if who and who in [str(o).lower() for o in t.get("owners", [])]), None)
    return out


@app.get("/api/league_live", dependencies=[Depends(auth.limited("league"))])
def league_live(platform: str = "espn", league: str = "", week: int = 1, season: int = 2026, s2: str = "", swid: str = "", saved: int | None = None,
                u=Depends(auth.user)):
    """Actual (or in-progress) points for a week under the league's own scoring, plus which NFL games have started."""
    import espn, sleeper, time
    if saved is not None:
        platform, league, season, s2, swid = accounts.saved_creds(u, saved)
    try:
        if platform == "espn":
            mock = os.environ.get("GRIDSIM_ESPN_LIVE_MOCK")
            players = espn.normalize_live(json.load(open(mock)) if mock else espn.fetch_live(league, season, week, s2 or None, swid or None), week)
        elif platform == "sleeper":
            mk = _sleeper_mock()
            players = sleeper.normalize_live(mk["live"], week, mk["players"]) if mk else sleeper.normalize_live(sleeper.fetch_live(league, week), week)
        else:
            raise HTTPException(400, f"Unknown platform '{platform}'.")
    except (espn.EspnError, sleeper.SleeperError) as e:
        raise HTTPException(502, str(e))
    games, games_err = _games_status(season, week)
    return {"week": week, "fetched": time.strftime("%Y-%m-%dT%H:%M:%S"), "players": players, "games": games, "games_err": games_err}


@app.get("/api/sleeper/find", dependencies=[Depends(auth.limited("league"))])
def sleeper_find(user: str, season: int = 2026):
    """Sleeper username -> that user's leagues for the season, so they can pick one instead of hunting for an ID."""
    import sleeper
    mk = _sleeper_mock()
    if mk:
        return mk["find"]
    try:
        return sleeper.find_leagues(user, season)
    except sleeper.SleeperError as e:
        raise HTTPException(502, str(e))


class SlateReq(BaseModel):
    week: int
    n: int = 500
    weather: bool = True
    ov: dict[str, bool] = {}          # manual injury edits


SLATE_CACHE = {}
_FACES = None


def _attach_faces(res):
    """Display-only metadata for the UI: full name, headshot URL and ESPN id from the nflverse roster files. Does not touch any projection."""
    global _FACES
    if _FACES is None:
        _FACES = {}
        try:
            import pandas as pd
            for yr in (2025, 2026):                       # newer file wins
                f = os.path.join(os.path.dirname(__file__), "data", f"roster_{yr}.parquet")
                if os.path.exists(f):
                    d = pd.read_parquet(f, columns=["gsis_id", "full_name", "espn_id", "headshot_url"]).dropna(subset=["gsis_id"])
                    for g, n, e, h in d.itertuples(index=False):
                        cur = _FACES.get(g, {})
                        _FACES[g] = {"fn": n or cur.get("fn"), "eid": (str(int(e)) if e == e and e is not None else cur.get("eid")), "hs": h if isinstance(h, str) and h else cur.get("hs")}
        except Exception as e:                            # images are optional; never break the slate over them
            print("faces unavailable:", e)
    for row in list(res.get("rows", [])) + list(res.get("rows_idle", [])):
        f = _FACES.get(row.get("id"))
        if f:
            row.update({k: v for k, v in f.items() if v})


@app.post("/api/slate", dependencies=[Depends(auth.limited("sim"))])
def slate(r: SlateReq):
    import time
    n = max(100, min(r.n, 10000))
    ov = {k: bool(b) for k, b in r.ov.items()}
    key = (r.week, n, r.weather, tuple(sorted(ov.items())))
    if key not in SLATE_CACHE:
        t0 = time.time()
        import weather
        from concurrent.futures import ThreadPoolExecutor
        games = [g for g in sim.SCHEDULE if g["week"] == r.week and not g["played"]]
        wx = {}
        if r.weather and games:
            with ThreadPoolExecutor(8) as ex:
                wx = {g["id"]: w for g, w in zip(games, ex.map(weather.forecast, games))}
        res = sim.simulate_slate(r.week, n, pool=POOL, hist={k: v[-8:] for k, v in MODEL.get("hist", {}).items()}, weather=wx, ov=ov)
        res["secs"] = round(time.time() - t0, 1)
        _attach_faces(res)
        SLATE_CACHE[key] = res
    return SLATE_CACHE[key]


def resolve_conditions(r):
    """Returns (cond dict for the engine or None, human-readable source)."""
    import weather
    if r.cond_mode == "none":
        return None, "off"
    if r.cond_mode == "manual":
        return {"roof": r.roof, "wind": r.wind, "temp": r.temp}, "manual"
    g = sim.vegas_for(r.home, r.away)
    if not g:
        return None, "no scheduled game"
    wx = weather.forecast(g) or {}
    return {"roof": g.get("roof"), "wind": wx.get("wind"), "temp": wx.get("temp")}, ("forecast" if wx else ("roof only" if g.get("roof") else "none"))


@app.post("/api/simulate", dependencies=[Depends(auth.limited("sim"))])
def simulate(r: Req):
    check(r)
    n = max(50, min(r.n, 20000))
    seed = r.seed if r.seed is not None else random.randrange(10**6)
    cond, src = resolve_conditions(r)
    res = sim.simulate(r.home, r.away, n, seed, r.overrides, r.neutral, pool=POOL, cond=cond)
    binfo, note = None, "off"
    g = sim.vegas_for(r.home, r.away)
    # the line describes the scheduled game, so blend only when the site setting matches the schedule (a neutral-site game stays neutral)
    if r.vegas and g and not r.overrides and bool(r.neutral) == bool(g.get("neutral")):
        res, binfo = sim.apply_blend(res, g.get("spread"), g.get("total"), seed=seed, home=r.home, away=r.away, neutral=r.neutral)
        note = "on" if binfo else "no line"
    elif r.vegas and r.overrides:
        note = "off: lineup changed"
    out = sim.summarize(res, r.home, r.away, r.overrides, hist=MODEL.get("hist", {}))
    out["blend"] = binfo; out["blend_note"] = note
    out["cond"] = {**(cond or {}), "src": src}
    return out


@app.post("/api/game", dependencies=[Depends(auth.limited("sim"))])
def one_game(r: Req):
    check(r)
    e = sim.get_engine()
    H, A = e.team(r.home, r.overrides), e.team(r.away, r.overrides)
    seed = r.seed if r.seed is not None else random.randrange(10**9)
    cond, _ = resolve_conditions(r)
    g = e.game(H, A, seed=seed, neutral=r.neutral, log=True, env=env_factors(**cond) if cond else None)
    names = {}
    for cfg in (H, A):
        for pid, q in cfg.players.items():
            names[pid] = {"name": q["name"], "pos": q.get("pos", "?"), "team": cfg.abbr}
    box = []
    for pid, v in g["ps"].items():
        if pid in names:
            box.append({**names[pid], "id": pid, **{k: v[i] for i, k in enumerate(PSTATS) if v[i]}})
    return {"seed": seed, "home": g["home"], "away": g["away"], "score": g["score"], "ot": g["ot"], "log": g["log"],
            "box": box, "team": {g["home"]: dict(zip(TSTATS, g["ts"][0])), g["away"]: dict(zip(TSTATS, g["ts"][1]))}}


app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8000)))
