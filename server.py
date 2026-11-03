"""GridSim web server:  python server.py  ->  http://localhost:8000"""
import json
import os
import random
from contextlib import asynccontextmanager
from multiprocessing import Pool

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
    POOL = Pool(os.cpu_count() or 1, initializer=sim._init)
    yield
    POOL.terminate()


app = FastAPI(lifespan=lifespan)


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


@app.get("/api/teams")
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


@app.get("/api/schedule")
def schedule_():
    weeks = sorted({g["week"] for g in sim.SCHEDULE})
    nxt = next((w for w in weeks if any(g["week"] == w and not g["played"] for g in sim.SCHEDULE)), weeks[-1] if weeks else None)
    return {"games": sim.SCHEDULE, "weeks": weeks, "next_week": nxt}


@app.get("/api/espn")
def espn_league(league: str, season: int = 2026, s2: str = "", swid: str = ""):
    import espn
    try:
        mock = os.environ.get("GRIDSIM_ESPN_MOCK")        # dev/testing: serve a saved ESPN payload instead of calling ESPN
        raw = json.load(open(mock)) if mock else espn.fetch(league, season, s2 or None, swid or None)
        return espn.normalize(raw)
    except espn.EspnError as e:
        raise HTTPException(502, str(e))


class SlateReq(BaseModel):
    week: int
    n: int = 500
    weather: bool = True


SLATE_CACHE = {}


@app.post("/api/slate")
def slate(r: SlateReq):
    import time
    n = max(100, min(r.n, 10000))
    key = (r.week, n, r.weather)
    if key not in SLATE_CACHE:
        t0 = time.time()
        import weather
        from concurrent.futures import ThreadPoolExecutor
        games = [g for g in sim.SCHEDULE if g["week"] == r.week and not g["played"]]
        wx = {}
        if r.weather and games:
            with ThreadPoolExecutor(8) as ex:
                wx = {g["id"]: w for g, w in zip(games, ex.map(weather.forecast, games))}
        res = sim.simulate_slate(r.week, n, pool=POOL, hist={k: v[-8:] for k, v in MODEL.get("hist", {}).items()}, weather=wx)
        res["secs"] = round(time.time() - t0, 1)
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


@app.post("/api/simulate")
def simulate(r: Req):
    check(r)
    n = max(50, min(r.n, 20000))
    seed = r.seed if r.seed is not None else random.randrange(10**6)
    cond, src = resolve_conditions(r)
    res = sim.simulate(r.home, r.away, n, seed, r.overrides, r.neutral, pool=POOL, cond=cond)
    binfo, note = None, "off"
    g = sim.vegas_for(r.home, r.away)
    if r.vegas and g and not r.overrides and not r.neutral:
        res, binfo = sim.apply_blend(res, g.get("spread"), g.get("total"), seed=seed)
        note = "on" if binfo else "no line"
    elif r.vegas and r.overrides:
        note = "off: lineup changed"
    out = sim.summarize(res, r.home, r.away, r.overrides, hist=MODEL.get("hist", {}))
    out["blend"] = binfo; out["blend_note"] = note
    out["cond"] = {**(cond or {}), "src": src}
    return out


@app.post("/api/game")
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
