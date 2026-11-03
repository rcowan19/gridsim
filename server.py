"""GridSim web server:  python server.py  ->  http://localhost:8000"""
import os
import random
from contextlib import asynccontextmanager
from multiprocessing import Pool

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import sim
from engine import PSTATS, TSTATS

HERE = os.path.dirname(os.path.abspath(__file__))
TEAMS = {
    "ARI": ("Arizona Cardinals", "#97233F"), "ATL": ("Atlanta Falcons", "#A71930"), "BAL": ("Baltimore Ravens", "#7B5FBF"),
    "BUF": ("Buffalo Bills", "#2F6FD0"), "CAR": ("Carolina Panthers", "#0085CA"), "CHI": ("Chicago Bears", "#E8793A"),
    "CIN": ("Cincinnati Bengals", "#FB4F14"), "CLE": ("Cleveland Browns", "#FF5B1F"), "DAL": ("Dallas Cowboys", "#5A87C8"),
    "DEN": ("Denver Broncos", "#FB4F14"), "DET": ("Detroit Lions", "#0076B6"), "GB": ("Green Bay Packers", "#2E8B57"),
    "HOU": ("Houston Texans", "#C8374A"), "IND": ("Indianapolis Colts", "#3A7BD5"), "JAX": ("Jacksonville Jaguars", "#00A3B0"),
    "KC": ("Kansas City Chiefs", "#E31837"), "LA": ("Los Angeles Rams", "#3D6FD1"), "LAC": ("Los Angeles Chargers", "#2FA7E0"),
    "LV": ("Las Vegas Raiders", "#A5ACAF"), "MIA": ("Miami Dolphins", "#00A6A6"), "MIN": ("Minnesota Vikings", "#7E57C2"),
    "NE": ("New England Patriots", "#3C6AB5"), "NO": ("New Orleans Saints", "#D3BC8D"), "NYG": ("New York Giants", "#2E6DD6"),
    "NYJ": ("New York Jets", "#2E8B6E"), "PHI": ("Philadelphia Eagles", "#1E8F7A"), "PIT": ("Pittsburgh Steelers", "#F2B705"),
    "SEA": ("Seattle Seahawks", "#69BE28"), "SF": ("San Francisco 49ers", "#D1303A"), "TB": ("Tampa Bay Buccaneers", "#D50A0A"),
    "TEN": ("Tennessee Titans", "#4B92DB"), "WAS": ("Washington Commanders", "#B03A4F"),
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
        out[abbr] = {"name": nm, "color": col, "players": players, "kicker": t["k"]["name"], "punter": t["punter"]}
    return {"teams": out, "meta": MODEL["meta"], "league": MODEL["league_actual"]}


@app.post("/api/simulate")
def simulate(r: Req):
    check(r)
    n = max(50, min(r.n, 20000))
    seed = r.seed if r.seed is not None else random.randrange(10**6)
    res = sim.simulate(r.home, r.away, n, seed, r.overrides, r.neutral, pool=POOL)
    return sim.summarize(res, r.home, r.away, r.overrides)


@app.post("/api/game")
def one_game(r: Req):
    check(r)
    e = sim.get_engine()
    H, A = e.team(r.home, r.overrides), e.team(r.away, r.overrides)
    seed = r.seed if r.seed is not None else random.randrange(10**9)
    g = e.game(H, A, seed=seed, neutral=r.neutral, log=True)
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
