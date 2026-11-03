"""Play-by-play NFL game engine (Monte Carlo).

State machine: down / distance / field position / clock / score / timeouts.
Every random draw comes from empirical pools or tables in model.json.
Side 0 = home, side 1 = away. Y is "yards to the opponent's goal line" (yardline_100).
"""
import math
import random
from bisect import bisect_left
from collections import defaultdict
from itertools import accumulate

PSTATS = ["pa", "pc", "pyd", "ptd", "int", "sk", "ra", "ryd", "rtd", "tgt", "rec", "recyd", "rectd",
          "fl", "fga", "fgm", "xpa", "xpm", "fgb"]     # fgb: ESPN distance bonus (+1 for 40-49, +2 for 50+)
PIDX = {k: i for i, k in enumerate(PSTATS)}
TSTATS = ["pts", "plays", "pass", "rush", "yds", "pyd", "ryd", "sacks", "to", "pen", "fd", "punts", "fga", "td", "poss", "dtd", "saf"]   # dtd: defensive / return TDs, saf: safeties scored
TIDX = {k: i for i, k in enumerate(TSTATS)}


def ppr_points(a):
    """PPR fantasy points from a (..., len(PSTATS)) stat array."""
    g = lambda k: a[..., PIDX[k]]
    return (0.04 * g("pyd") + 4 * g("ptd") - 2 * g("int") + 0.1 * (g("ryd") + g("recyd"))
            + 6 * (g("rtd") + g("rectd")) + g("rec"))


def zone(y):
    return 0 if y > 80 else 1 if y > 50 else 2 if y > 20 else 3 if y > 5 else 4


def dbucket(x):
    return 0 if x <= 1 else 1 if x <= 3 else 2 if x <= 6 else 3 if x <= 10 else 4


def run_zone(y):
    return 0 if y > 20 else 1 if y > 10 else 2 if y > 5 else 3


def air_bucket(a):
    return 0 if a < 0 else 1 if a < 5 else 2 if a < 10 else 3 if a < 15 else 4 if a < 20 else 5


def _db6(x):
    return 0 if x <= 2 else 1 if x <= 4 else 2 if x <= 7 else 3 if x <= 10 else 4 if x <= 15 else 5


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def spot_txt(y, off, de):
    y = int(y)
    return f"{off} {100 - y}" if y > 50 else (f"{de} {y}" if y < 50 else "50")


def clock_txt(t, period):
    if period == 3:
        s = max(int(t), 0)
        return f"OT {s // 60}:{s % 60:02d}"
    q = min(int((3600 - t) // 900) + 1, 4)
    s = max(int(t - (4 - q) * 900), 0)
    return f"Q{q} {s // 60}:{s % 60:02d}"


def env_factors(roof=None, wind=None, temp=None):
    """Game-conditions adjustments from 2025-26 play-by-play, shrunk because the evidence is modest (wind: about -0.4 pts of total per mph above 5, ~1.8 sigma after controlling for team quality;
    roof: ~+0.5 pts, not significant; temperature, weekday and kickoff slot showed no usable signal).
    Returns multipliers/offsets applied by the engine: ypa (completion-yards multiplier), comp (completion prob add), pass (dropback logit)."""
    indoor = roof in ("dome", "closed")
    if indoor:
        return {"ypa": 1.004, "comp": 0.002, "pass": 0.0}
    w = 0.0 if wind is None else max(0.0, min(float(wind), 22.0) - 5.0)
    return {"ypa": 1.0 - 0.0045 * min(w, 10.0), "comp": -0.0017 * min(w, 11.0), "pass": -0.011 * min(w, 15.0)}


def stoch_round(v, rng):
    f = math.floor(v)
    return int(f + (1 if rng.random() < v - f else 0))


class Engine:
    # knobs that calibrate.py can tune
    DEFAULT_TUNE = {"hfa_comp": 0.006, "hfa_ypc": 0.06, "pass_scale": 1.0, "ypc_scale": 1.0,
                    "comp_add": 0.02, "pen_scale": 1.0, "yac_scale": 1.0, "air_scale": 1.0, "air_add": 0.8, "air_mult": 1.0, "air_tail": 1.0, "yds_mult": 1.0, "pace_mult": 0.985, "run_add": 0.1, "gl_add": 0.4, "pen_live": 1.0, "go_mult": 1.1, "mid_air": 1.0, "punt_td": 0.0055, "fum_td": 0.035, "eoh_fg": 0.9}

    def __init__(self, model, tune=None):
        self.m = m = model
        self.pt, self.ptd = m["pass_table"], m["pass_default"]
        self.lcache = {}
        self.resid, self.edges = m["pass_resid"], m["sd_edges"]
        self.fourth, self.comp = m["fourth"], m["comp_by_air"]
        self.yacp = {int(k): v for k, v in m["yac_pool"].items()}
        self.air = m["air_pool"]
        self.runp = {int(k): v for k, v in m["run_pool"].items()}
        self.runshort = m["run_pool_short"]
        self.rungoal = {int(k): v for k, v in m["run_goal"].items()}
        self.airrz = m["air_rz"]
        self.ashift = m["air_shift"]
        self.cresid = m["comp_resid"]
        self.yfac = m["yac_fac"]
        self.clk = m["clock"]
        la = m["league_actual"]
        self.lsack, self.lscr, self.lint = la["sack_rate"], la["scramble_rate"], la["int_rate"]
        self.fgx = [r[0] for r in m["fg_table"]]
        self.fgy = [r[1] for r in m["fg_table"]]
        self.punt = {int(k): v for k, v in m["punt_pool"].items()}
        self.tune = dict(self.DEFAULT_TUNE, **(tune or {}))
        self.teams = {}
        self.tc_cache = {}
        self.trace = None

    def team(self, abbr, overrides=None):
        return TeamCfg(self.m, abbr, overrides or {})

    def uncertain(self, abbr, ov=None):
        """Active players with a chance of sitting out (Questionable), as [(id, p_play)]."""
        ov, t = ov or {}, self.m["teams"][abbr]
        return [(q["id"], q["p_play"]) for q in t["qbs"] + t["skill"]
                if q.get("active") and q.get("p_play") is not None and q["id"] not in ov]

    def extra_pids(self, abbr, ov=None):
        """Backups who can enter when an uncertain QB sits."""
        t, ids = self.m["teams"][abbr], set(p for p, _ in self.uncertain(abbr, ov))
        qb = [q for q in t["qbs"] if q.get("active")]
        if qb and qb[0]["id"] in ids:
            return [q["id"] for q in t["qbs"] if not q.get("active")][:1]
        return []

    def team_draw(self, abbr, ov, rng):
        """Team config for one simulated game: each Questionable player plays with his p_play."""
        ov = ov or {}
        sit = tuple(sorted(pid for pid, p in self.uncertain(abbr, ov) if rng.random() >= p))
        key = (abbr, sit, tuple(sorted(ov.items())))
        tc = self.tc_cache.get(key)
        if tc is None:
            tc = self.tc_cache[key] = TeamCfg(self.m, abbr, dict(ov, **{p: False for p in sit}))
        return tc

    def game(self, home, away, seed=None, neutral=False, log=False, env=None):
        return Game(self, home, away, random.Random(seed), neutral, log, env).run()


class TeamCfg:
    def __init__(self, m, abbr, ov):
        t = m["teams"][abbr]
        la = m["league_actual"]
        self.abbr, self.r = abbr, t["rates"]
        on = lambda q: ov.get(q["id"], q["active"])
        qbs = [q for q in t["qbs"] if on(q)] or [q for q in t["qbs"] if ov.get(q["id"]) is not False][:1] or t["qbs"][:1]
        self.qb = qbs[0] if qbs else {"id": "QB_" + abbr, "name": "QB." + abbr, "cpoe": 0, "int": la["int_rate"],
                                       "scr": la["scramble_rate"], "pos": "QB"}
        sk = [q for q in t["skill"] if on(q)]
        self.recv = [q for q in sk if q["tgt"] > 0.004] or [
            {"id": "WR_" + abbr, "name": "WR." + abbr, "pos": "WR", "bpos": "WR", "tgt": 1, "rz_tgt": 1,
             "adot": 0, "cpoe": 0, "yac": 1, "car": 0, "gl": 0, "ypc": 0}]
        self.rw = self._weights(self.recv, "tgt", t["mix"])
        self.rzw = self._weights(self.recv, "rz_tgt", t["mix_rz"])
        car = [q for q in sk if q["car"] > 0.01]
        if self.qb.get("car"):
            car = car + [self.qb]
        if not car:
            car = [{"id": "RB_" + abbr, "name": "RB." + abbr, "pos": "RB", "car": 1, "gl": 1, "ypc": 0}]
        self.car = car
        self.cw = list(accumulate(q["car"] for q in car))
        self.gw = list(accumulate(max(q.get("gl", 0), 1e-4) for q in car))
        self.k = t["k"]
        self.punter = t["punter"]
        self.players = {q["id"]: q for q in self.recv + car + [self.qb, self.k]}
        self.pids = sorted(self.players)


def _weights(recv, key, mix):
    """Cumulative target weights: position-group mix is kept; players share their group's volume."""
    gs = defaultdict(float)
    for q in recv:
        gs[q.get("bpos", "WR")] += q[key]
    w = []
    for q in recv:
        g = q.get("bpos", "WR")
        w.append(mix.get(g, 0.3) * q[key] / gs[g] if gs[g] > 0 else 0.0)
    if sum(w) <= 0:
        w = [1.0] * len(recv)
    return list(accumulate(w))


TeamCfg._weights = staticmethod(_weights)


def pick(items, cum, rng):
    return items[min(bisect_left(cum, rng.random() * cum[-1]), len(items) - 1)]


class Game:
    def __init__(self, eng, home, away, rng, neutral, log, env=None):
        self.e, self.rng, self.cfg = eng, rng, [home, away]
        self.env = env or {}
        self.names = [home.abbr, away.abbr]
        self.neutral, self.logon, self.logs = neutral, log, []
        self.pts, self.to = [0, 0], [3, 3]
        self.time, self.period = 3600, 1
        self.off, self.Y, self.down, self.dist = 0, 75, 1, 10
        self.drive_no = 0
        self.ps = defaultdict(lambda: [0] * len(PSTATS))
        self.ts = [[0] * len(TSTATS), [0] * len(TSTATS)]
        self.pending_kick = None
        self.dscore = None
        self.drive_off = 0
        self.pre = (1, 10, 75, 0)
        self.mu = [self._matchup(i) for i in (0, 1)]

    # ------------------------------------------------------------ setup
    def _matchup(self, i):
        O, D, e = self.cfg[i], self.cfg[1 - i], self.e
        hf = 0.0 if self.neutral else (1 if i == 0 else -1)
        return {
            "p_sack": clamp(e.lsack + (O.r["sack_off"] - e.lsack) + (D.r["sack_def"] - e.lsack), 0.015, 0.16),
            "p_scr": clamp(O.qb["scr"] + (O.r["scr_off"] - e.lscr) * 0.3, 0.005, 0.15),
            "p_int": clamp(e.lint + (O.qb["int"] - e.lint) + (D.r["int_def"] - e.lint), 0.006, 0.06),
            "cp": O.qb["cpoe"] + D.r["cpoe_def"] + hf * e.tune["hfa_comp"] + e.tune["comp_add"] + self.env.get("comp", 0.0),
            "ypc": e.tune["ypc_scale"] * D.r["ypc_def"] * 0.8 + hf * e.tune["hfa_ypc"],
            "pace": O.r["pace"] * e.tune["pace_mult"], "pshift": O.r["pass_shift"],
        }

    def _bin(self, sd):
        ed = self.e.edges
        for i in range(len(ed) - 1):
            if ed[i] <= sd < ed[i + 1]:
                return i
        return len(ed) - 2

    # ------------------------------------------------------------ logging
    def say(self, kind, text, yards=0, x0=None, x1=None, scoring=False, o=None):
        if not self.logon:
            return
        down, dist, Y, po = self.pre
        o = po if o is None else o
        self.logs.append({
            "n": len(self.logs) + 1, "drive": self.drive_no, "clock": clock_txt(self.time, self.period),
            "off": self.names[o], "o": o, "down": down, "dist": dist,
            "spot": spot_txt(Y, self.names[po], self.names[1 - po]), "kind": kind, "text": text,
            "yards": yards, "hs": self.pts[0], "as": self.pts[1], "x0": x0, "x1": x1, "scoring": scoring})

    def xp(self, y, o):               # field x (0 = home end zone, 100 = away end zone)
        return y if o == 0 else 100 - y

    # ------------------------------------------------------------ clock
    def clock_over(self):
        return self.time <= (1800 if self.period == 1 else 0)

    def elapse(self, kind, o, fixed=None, to_ok=False):
        e = self.e
        if fixed is None:
            nh = self.hurry(o)
            s = self.rng.choice(e.clk[f"{1 if nh else 0}_{kind}"])
            if not nh:
                s = int(round(s * self.mu[o]["pace"]))
            s = max(s, 1)
        else:
            s = fixed
        if (fixed is None or to_ok) and self.period in (1, 2):
            lt = self.time - (1800 if self.period == 1 else 0)
            if lt <= 125:
                diff = self.pts[0] - self.pts[1]
                tr = 0 if diff < 0 else 1 if diff > 0 else None
                if tr is not None and self.to[tr] > 0 and s > 12:
                    self.to[tr] -= 1
                    s = self.rng.randint(4, 8)
        floor = 1800 if self.period == 1 else 0
        self.time = max(self.time - s, floor)

    def hurry(self, o):
        sd = self.pts[o] - self.pts[1 - o]
        if self.period == 1 and self.time <= 1920 and sd <= 0:
            return True
        return self.period == 2 and self.time <= 300 and sd < 0

    # ------------------------------------------------------------ scoring / possession
    def add_pts(self, side, n):
        self.pts[side] += n
        self.ts[side][TIDX["pts"]] += n

    def give_ball(self, side, y):
        y = max(1, min(99, int(y)))
        self.off, self.Y, self.down, self.dist = side, y, 1, min(10, y)
        self.drive_no += 1
        self.ts[side][TIDX["poss"]] += 1

    def kickoff(self, kicker):
        e, rng, rec = self.e, self.rng, 1 - kicker
        self.pre = (0, 0, 65, kicker)
        if rng.random() < e.m["ko_td"]:
            self.add_pts(rec, 6)
            self.ts[rec][TIDX["td"]] += 1
            self.ts[rec][TIDX["dtd"]] += 1
            self.say("kickoff", f"{self.names[kicker]} kicks off; returned for a TOUCHDOWN", scoring=True, o=kicker)
            self.extra_point(rec)
            if self.period == 3 or self.clock_over():
                self.pending_kick = None
                self.dscore = "DEF" if self.period == 3 else None
                return
            return self.kickoff(rec)
        y = rng.choice(e.m["ko_pool"])
        self.say("kickoff", f"{self.names[kicker]} kicks off; {self.names[rec]} start at "
                            f"{spot_txt(y, self.names[rec], self.names[kicker])}", o=kicker)
        self.give_ball(rec, y)
        self.drive_off = rec

    def extra_point(self, side):
        e, rng = self.e, self.rng
        if self.period == 3:
            return
        diff = self.pts[side] - self.pts[1 - side]
        if self.period == 2 and self.time == 0 and abs(diff) > 2:
            return
        if self.period == 2 and self.time <= 900 and diff in (-2, -5):
            if rng.random() < e.m["two_make"]:
                self.add_pts(side, 2)
                self.say("2pt", f"{self.names[side]} two-point conversion GOOD", scoring=True, o=side)
            else:
                self.say("2pt", f"{self.names[side]} two-point conversion fails", o=side)
            return
        k = self.cfg[side].k
        self.ps[k["id"]][PIDX["xpa"]] += 1
        if rng.random() < clamp(e.m["xp_make"] + k["fg_over"] * 0.2, 0.8, 0.995):
            self.add_pts(side, 1)
            self.ps[k["id"]][PIDX["xpm"]] += 1
            self.say("xp", f"{k['name']} extra point GOOD", scoring=True, o=side)
        else:
            self.say("xp", f"{k['name']} extra point NO GOOD", o=side)

    def touchdown(self, side, text, x0, x1, defensive=False):
        if defensive:
            self.ts[side][TIDX["dtd"]] += 1
        self.add_pts(side, 6)
        self.ts[side][TIDX["td"]] += 1
        self.say("td", text + " TOUCHDOWN", scoring=True, x0=x0, x1=x1, o=side)
        self.extra_point(side)
        self.pending_kick = side
        self.dscore = "TD" if side == self.drive_off else "DEF"

    # ------------------------------------------------------------ play calling
    def pass_prob(self, o):
        e = self.e
        down, dist, Y = self.down, self.dist, self.Y
        b = e.pt.get(f"{down}_{dbucket(dist)}_{zone(Y)}")
        if b is None:
            b = e.ptd.get(f"{down}_{dbucket(dist)}", 0.58)
        lg = e.lcache.get(b)
        if lg is None:
            lg = e.lcache[b] = math.log(b / (1 - b))
        sd = self.pts[o] - self.pts[1 - o]
        if self.period == 3:
            ts = 0
        elif (self.period == 2 and self.time <= 120) or (self.period == 1 and self.time <= 1920):
            ts = 2
        else:
            ts = 1 if (self.period == 2 and self.time <= 900) else 0
        z = lg + self.mu[o]["pshift"] + e.resid.get(f"{self._bin(sd)}_{ts}", 0.0) + math.log(e.tune["pass_scale"]) + self.env.get("pass", 0.0)
        return 1 / (1 + math.exp(-z))

    def fourth_choice(self):
        e, rng = self.e, self.rng
        Y, d, o = self.Y, self.dist, self.off
        sd = self.pts[o] - self.pts[1 - o]
        lt = self.time - (1800 if self.period == 1 else 0)
        fg_ok = Y + 17 <= 64
        need = -sd
        if self.period == 3 and fg_ok and Y + 17 <= 58:
            return "fg"
        if lt <= 8 and self.period in (1, 2):
            return "fg" if Y + 17 <= 62 else "go"
        if self.period == 2 and self.time <= 360 and sd < 0:
            if need <= 3 and Y + 17 <= 58:
                return "fg"
            return "go" if (d <= 12 or Y <= 60) else "punt"
        zy = 0 if Y <= 25 else 1 if Y <= 36 else 2 if Y <= 43 else 3 if Y <= 54 else 4 if Y <= 69 else 5
        db = 0 if d <= 1 else 1 if d <= 2 else 2 if d <= 4 else 3 if d <= 7 else 4
        row = e.fourth.get(f"{zy}_{db}")
        if row is None:
            return "fg" if Y <= 36 else "punt"
        g0 = min(row[0] * e.tune["go_mult"], 0.98); rest = max(row[1] + row[2], 1e-9); k = (1 - g0) / rest
        r = rng.random()
        ch = "go" if r < g0 else "fg" if r < g0 + row[1] * k else "punt"
        if ch == "fg" and not fg_ok:
            ch = "punt"
        if ch == "punt" and Y <= 25 and fg_ok:
            ch = "fg"
        return ch

    # ------------------------------------------------------------ main loop
    def run(self):
        rng = self.rng
        recv_first = rng.randint(0, 1)
        self.kickoff(1 - recv_first)
        for _ in range(60):
            self.run_regulation_period(recv_first)
            if self.period == 2 and self.clock_over():
                break
        if self.pts[0] == self.pts[1]:
            self.overtime()
        self.pre = (0, 0, 50, self.off)
        self.say("final", "FINAL" + (" (OT)" if self.period == 3 else ""))
        return self.result()

    def run_regulation_period(self, recv_first):
        """Run drives until the current half ends."""
        guard = 0
        while guard < 60:
            guard += 1
            self.pending_kick, self.dscore = None, None
            self.drive()
            if self.clock_over():
                if self.period == 1:
                    self.pre = (0, 0, 50, self.off)
                    self.say("half", "END OF FIRST HALF")
                    self.period, self.time, self.to = 2, 1800, [3, 3]
                    self.kickoff(recv_first)       # opening receiver now kicks
                    continue
                return
            if self.pending_kick is not None:
                self.kickoff(self.pending_kick)

    def overtime(self):
        rng = self.rng
        self.pre = (0, 0, 50, self.off)
        self.say("ot", "TIED AFTER REGULATION - OVERTIME")
        self.period, self.time, self.to = 3, 600, [2, 2]
        recv = rng.randint(0, 1)
        self.kickoff(1 - recv)
        poss = 0
        while self.time > 0 and poss < 20:
            self.pending_kick, self.dscore = None, None
            self.drive()
            poss += 1
            if self.dscore == "DEF" or (poss == 1 and self.dscore == "TD") or (poss >= 2 and self.pts[0] != self.pts[1]):
                break
            if self.clock_over():
                break
            if self.pending_kick is not None:
                self.kickoff(self.pending_kick)

    def drive(self):
        rng, e = self.rng, self.e
        d0 = self.drive_no
        plays = 0
        while not self.clock_over() and plays < 40:
            plays += 1
            o = self.off
            O, D = self.cfg[o], self.cfg[1 - o]
            self.pre = (self.down, self.dist, self.Y, o)
            sd = self.pts[o] - self.pts[1 - o]
            lt = self.time - (1800 if self.period == 1 else 0)
            # kneel to close out the game
            if self.period == 2 and sd > 0:
                dto = self.to[1 - o]
                need = 40 * max(4 - self.down, 1) - 35 * min(dto, max(4 - self.down, 0))
                if lt <= need:
                    self.kneel(O)
                    if self.drive_no != d0:
                        return
                    continue
            if self.down < 4 and lt <= 15 and self.Y + 17 <= 58 and (self.period == 1 or (self.period == 2 and -3 <= sd <= 0)) \
                    and rng.random() < e.tune["eoh_fg"]:
                return self.do_fg(O)             # kick on any down when the half is about to end
            if self.down == 4:
                ch = self.fourth_choice()
                if ch == "punt":
                    return self.do_punt(O)
                if ch == "fg":
                    return self.do_fg(O)
            pr = rng.random()
            po, pdf = e.m["pen_off_rate"] * e.tune["pen_scale"], e.m["pen_def_rate"] * e.tune["pen_scale"]
            if pr < po + pdf:
                self.penalty(o, pr < po)
                continue
            if rng.random() < self.pass_prob(o):
                self.dropback(o, O, D)
            else:
                self.rush(o, O, D)
            if self.drive_no != d0 or self.pending_kick is not None:
                return
            self.live_penalty(o)

    # ------------------------------------------------------------ plays
    def first_down_or_not(self, o, yds):
        """Apply a non-scoring gain. Returns True if possession flipped on downs."""
        Y2 = self.Y - yds
        if yds >= self.dist:
            self.down, self.dist = 1, min(10, Y2)
            self.ts[o][TIDX["fd"]] += 1
        else:
            self.down += 1
            self.dist -= yds
        self.Y = Y2
        if self.down > 4:
            self.give_ball(1 - o, 100 - Y2)
            return True
        return False

    def team_yards(self, o, yds, passing):
        t = self.ts[o]
        t[TIDX["yds"]] += yds
        t[TIDX["pyd" if passing else "ryd"]] += yds

    def lose_ball(self, o, Y2):
        self.ts[o][TIDX["to"]] += 1
        if self.rng.random() < self.e.tune["fum_td"]:
            self.drive_off = 1 - o
            self.say("td", "Fumble recovered", 0, self.xp(Y2, o), None, o=1 - o)
            self.touchdown(1 - o, "Fumble returned for", self.xp(Y2, o), self.xp(100, o), defensive=True)
            self.dscore = "DEF"
            return
        self.give_ball(1 - o, 100 - Y2)

    def rush(self, o, O, D):
        e, rng, mu = self.e, self.rng, self.mu[o]
        Y = self.Y
        c = pick(O.car, O.gw if Y <= 3 else O.cw, rng)
        if Y <= 10:
            base = rng.choice(e.rungoal[Y])
        else:
            base = rng.choice(e.runshort if (self.dist <= 2 and Y > 10) else e.runp[run_zone(Y)])
        zs = 1.0 if Y > 20 else 0.6
        v = base + zs * (0.6 * c.get("ypc", 0) + 0.4 * O.r["ypc_off"] + mu["ypc"] + e.tune["run_add"]) + (e.tune["gl_add"] if Y <= 10 else 0.0)
        yds = int(clamp(stoch_round(v, rng), -9, 99))
        self.ts[o][TIDX["plays"]] += 1
        self.ts[o][TIDX["rush"]] += 1
        st = self.ps[c["id"]]
        x0 = self.xp(Y, o)
        if yds >= Y:
            yds = Y
            st[PIDX["ra"]] += 1
            st[PIDX["ryd"]] += yds
            st[PIDX["rtd"]] += 1
            self.team_yards(o, yds, False)
            self.elapse("run", o)
            return self.touchdown(o, f"{c['name']} rush for {yds} yards", x0, self.xp(0, o))
        if Y - yds >= 100:
            return self.safety(o, f"{c['name']} tackled in the end zone", c["id"])
        st[PIDX["ra"]] += 1
        st[PIDX["ryd"]] += yds
        self.team_yards(o, yds, False)
        self.elapse("run", o)
        if rng.random() < e.m["fum_rush"]:
            st[PIDX["fl"]] += 1
            self.say("run", f"{c['name']} rush for {yds} yards, FUMBLES - recovered by {self.names[1 - o]}",
                     yds, x0, self.xp(Y - yds, o))
            return self.lose_ball(o, Y - yds)
        self.say("run", f"{c['name']} rush for {yds} yard{'s' if abs(yds) != 1 else ''}", yds, x0, self.xp(Y - yds, o))
        self.first_down_or_not(o, yds)

    def kneel(self, O):
        o, Y = self.off, self.Y
        self.ts[o][TIDX["plays"]] += 1
        self.elapse("run", o, fixed=40, to_ok=True)
        self.say("kneel", f"{O.qb['name']} kneels", -1, self.xp(Y, o), self.xp(min(Y + 1, 99), o))
        self.Y = min(Y + 1, 99)
        self.down += 1
        self.dist += 1
        if self.down > 4:
            self.give_ball(1 - o, 100 - self.Y)

    def dropback(self, o, O, D):
        e, rng, mu = self.e, self.rng, self.mu[o]
        Y, qb = self.Y, O.qb
        qs = self.ps[qb["id"]]
        x0 = self.xp(Y, o)
        self.ts[o][TIDX["plays"]] += 1
        self.ts[o][TIDX["pass"]] += 1
        r = rng.random()
        if r < mu["p_sack"]:
            loss = rng.choice(e.m["sack_pool"])
            self.ts[o][TIDX["sacks"]] += 1
            qs[PIDX["sk"]] += 1
            self.team_yards(o, loss, True)
            self.elapse("sack", o)
            if Y - loss >= 100:
                return self.safety(o, f"{qb['name']} sacked in the end zone", qb["id"])
            if rng.random() < e.m["fum_sack"]:
                qs[PIDX["fl"]] += 1
                self.say("sack", f"{qb['name']} sacked for {-loss} yard loss, FUMBLES - lost", loss, x0, self.xp(Y - loss, o))
                return self.lose_ball(o, Y - loss)
            self.say("sack", f"{qb['name']} sacked for {-loss} yard loss", loss, x0, self.xp(Y - loss, o))
            self.first_down_or_not(o, loss)
            return
        if r < mu["p_sack"] + mu["p_scr"]:
            yds = int(clamp(rng.choice(e.m["scramble_pool"]), -9, 99))
            qs[PIDX["ra"]] += 1
            qs[PIDX["ryd"]] += yds
            self.ts[o][TIDX["pass"]] -= 1
            self.ts[o][TIDX["rush"]] += 1
            self.team_yards(o, min(yds, Y), False)
            self.elapse("run", o)
            if yds >= Y:
                qs[PIDX["rtd"]] += 1
                qs[PIDX["ryd"]] -= yds - Y
                return self.touchdown(o, f"{qb['name']} scrambles for {Y} yards", x0, self.xp(0, o))
            self.say("run", f"{qb['name']} scrambles for {yds} yards", yds, x0, self.xp(Y - yds, o))
            self.first_down_or_not(o, yds)
            return
        # ---- throw
        tgt = pick(O.recv, O.rzw if Y <= 20 else O.rw, rng)
        pool = e.air[tgt.get("bpos", "WR")]
        cell = f"{0 if self.down == 1 else 1 if self.down == 2 else 2}_{_db6(self.dist)}"
        dsh = e.ashift.get(cell, 0.0)
        air = None
        if Y <= 20 or (Y <= e.airrz[-1]["hi"] and rng.random() < e.tune["mid_air"]):
            for b in e.airrz:
                if Y <= b["hi"]:
                    air = rng.choice(b["pool"])
                    break
            if air is not None and air > Y + 10:
                air = Y + 10                  # air yards can land in the end zone
        for _ in range(0 if air is not None else 6):
            a = rng.choice(pool) + tgt["adot"] * 0.6 * e.tune["air_scale"] + e.tune["air_add"] + dsh
            if a > 0:
                a *= e.tune["air_mult"]
            if a > 15:
                a = 15 + (a - 15) * e.tune["air_tail"]
            a = int(round(a))
            if a <= Y + 10:                  # end-zone targets are allowed
                air = a
                break
        if air is None:
            air = rng.randint(0, max(Y, 1))
        qs[PIDX["pa"]] += 1
        ts_ = self.ps[tgt["id"]]
        if rng.random() < e.m["throwaway"] and rng.random() >= mu["p_int"] * 3:
            self.say("pass", f"{qb['name']} pass incomplete (thrown away)", 0, x0, x0)
            self.elapse("inc", o)
            self.first_down_or_not(o, 0)
            return
        ts_[PIDX["tgt"]] += 1
        if rng.random() < mu["p_int"]:
            qs[PIDX["int"]] += 1
            self.ts[o][TIDX["to"]] += 1
            self.elapse("inc", o)
            s = Y - max(air, 0)
            ret = rng.choice(e.m["int_return_pool"])
            if s <= 0:
                self.say("int", f"{qb['name']} pass INTERCEPTED in the end zone (intended for {tgt['name']}) - touchback",
                         0, x0, None)
                self.give_ball(1 - o, 80)
                return
            ydef = 100 - s - ret
            if ydef <= 0:
                self.drive_off = 1 - o
                self.say("int", f"{qb['name']} pass INTERCEPTED (intended for {tgt['name']}), returned {ret} yards", 0, x0, None)
                self.touchdown(1 - o, "Interception returned for", self.xp(s, o), self.xp(100, o), defensive=True)
                self.dscore = "DEF"
                return
            self.say("int", f"{qb['name']} pass INTERCEPTED (intended for {tgt['name']}), returned {ret} yards", 0, x0, self.xp(100 - ydef, 1 - o))
            self.give_ball(1 - o, ydef)
            return
        ab = air_bucket(air)
        pc = e.comp.get(f"{3 if Y <= 5 else 1 if Y <= 10 else 2 if Y <= 20 else 0}_{ab}", e.comp.get(f"{1 if Y <= 10 else 0}_{ab}", 0.6)) + mu["cp"] + tgt["cpoe"] + e.cresid.get(cell, 0.0)
        pc = clamp(pc / (1 - mu["p_int"]), 0.05, 0.96)
        cmp_ = rng.random() < pc
        if e.trace is not None:
            e.trace.append([air, ab, cmp_, tgt.get("bpos"), Y, pc, None, None, self.down, self.dist, mu["cp"], tgt["cpoe"], e.cresid.get(cell, 0.0), e.comp.get(f"{3 if Y <= 5 else 1 if Y <= 10 else 2 if Y <= 20 else 0}_{ab}", e.comp.get(f"{1 if Y <= 10 else 0}_{ab}", 0.6))])
        if not cmp_:
            self.say("pass", f"{qb['name']} pass incomplete intended for {tgt['name']}", 0, x0, x0)
            self.elapse("inc", o)
            self.first_down_or_not(o, 0)
            return
        yac = stoch_round(rng.choice(e.yacp[ab]) * tgt["yac"] * e.tune["yac_scale"] * e.yfac.get(cell, 1.0), rng)
        yds = air + yac
        if yds > 0:
            yds = stoch_round(yds * (e.tune["yds_mult"] * self.env.get("ypa", 1.0) if Y > 30 else self.env.get("ypa", 1.0)), rng)
        yds = int(clamp(yds, -9, 99))
        if e.trace is not None:
            e.trace[-1][6], e.trace[-1][7] = yds - air, yds
        qs[PIDX["pc"]] += 1
        ts_[PIDX["rec"]] += 1
        self.ts[o][TIDX["pass"]] += 0
        self.elapse("comp", o)
        if yds >= Y:
            yds = Y
            for s_, k in ((qs, "pyd"), (ts_, "recyd")):
                s_[PIDX[k]] += yds
            qs[PIDX["ptd"]] += 1
            ts_[PIDX["rectd"]] += 1
            self.team_yards(o, yds, True)
            return self.touchdown(o, f"{qb['name']} pass complete to {tgt['name']} for {yds} yards", x0, self.xp(0, o))
        qs[PIDX["pyd"]] += yds
        ts_[PIDX["recyd"]] += yds
        self.team_yards(o, yds, True)
        if rng.random() < e.m["fum_pass"]:
            ts_[PIDX["fl"]] += 1
            self.say("pass", f"{qb['name']} pass complete to {tgt['name']} for {yds} yards, FUMBLES - lost", yds, x0, self.xp(Y - yds, o))
            return self.lose_ball(o, Y - yds)
        self.say("pass", f"{qb['name']} pass complete to {tgt['name']} for {yds} yards", yds, x0, self.xp(Y - yds, o))
        self.first_down_or_not(o, yds)

    def safety(self, o, text, pid=None):
        self.add_pts(1 - o, 2)
        self.ts[1 - o][TIDX["saf"]] += 1
        self.say("safety", text + " - SAFETY", scoring=True, o=o)
        self.dscore = "DEF"
        # conceding team free-kicks; model as a punt from its own 20
        pool = self.e.punt.get(4) or self.e.punt[max(self.e.punt)]
        self.give_ball(1 - o, self.rng.choice(pool))
        self.drive_off = 1 - o
        self.pending_kick = None
        self.time = max(self.time - 0, 0)

    def live_penalty(self, o):
        """Flag enforced on top of a play that stood (no down replayed)."""
        e, rng, m = self.e, self.rng, self.e.m
        if "pen_live_def" not in m or self.down > 4:
            return
        r, sc = rng.random(), e.tune["pen_live"]
        if r < m["pen_live_def"] * sc:
            yds = min(rng.choice(m["pen_live_def_pool"]), self.Y // 2)
            if yds <= 0:
                return
            self.ts[1 - o][TIDX["pen"]] += 1
            self.say("penalty", f"{self.names[1 - o]} penalty after the play: defensive, {yds} yards", yds, self.xp(self.Y, o), self.xp(self.Y - yds, o))
            self.Y -= yds
            if rng.random() < m["pen_live_auto1"] or yds >= self.dist:
                self.down, self.dist = 1, min(10, self.Y)
                self.ts[o][TIDX["fd"]] += 1
            else:
                self.dist -= yds
        elif r < (m["pen_live_def"] + m["pen_live_off"]) * sc:
            yds = min(rng.choice(m["pen_live_off_pool"]), (100 - self.Y) // 2)
            if yds <= 0:
                return
            self.ts[o][TIDX["pen"]] += 1
            self.say("penalty", f"{self.names[o]} penalty on the play: offensive, {yds} yards", -yds, self.xp(self.Y, o), self.xp(self.Y + yds, o))
            self.Y += yds
            self.dist += yds

    def penalty(self, o, offense):
        e, rng = self.e, self.rng
        self.ts[o if offense else 1 - o][TIDX["pen"]] += 1
        Y = self.Y
        self.elapse("inc", o, fixed=rng.randint(5, 9))
        if offense:
            yds = min(rng.choice(e.m["pen_off_pool"]) or 5, (100 - Y) // 2)
            self.say("penalty", f"{self.names[o]} penalty: offensive, {yds} yards", -yds, self.xp(Y, o), self.xp(Y + yds, o))
            self.Y += yds
            self.dist += yds
        else:
            yds = min(rng.choice(e.m["pen_def_pool"]) or 5, Y // 2)
            self.say("penalty", f"{self.names[1 - o]} penalty: defensive, {yds} yards", yds, self.xp(Y, o), self.xp(Y - yds, o))
            self.Y -= yds
            if rng.random() < e.m["pen_def_auto1"] or yds >= self.dist:
                self.down, self.dist = 1, min(10, self.Y)
                self.ts[o][TIDX["fd"]] += 1
            else:
                self.dist -= yds

    def do_punt(self, O):
        e, rng, o, Y = self.e, self.rng, self.off, self.Y
        bk = 0 if Y < 40 else 1 if Y < 50 else 2 if Y < 60 else 3 if Y < 75 else 4
        pool = e.punt.get(bk) or e.punt[min(e.punt, key=lambda k: abs(k - bk))]
        rs = rng.choice(pool)
        self.ts[o][TIDX["punts"]] += 1
        self.elapse("run", o, fixed=rng.randint(6, 13))
        if rng.random() < e.tune["punt_td"]:
            self.drive_off = o
            self.say("punt", f"{O.punter} punt; returned for a touchdown", 0, self.xp(Y, o), None)
            self.touchdown(1 - o, "Punt returned for", self.xp(Y, o), self.xp(100, o), defensive=True)
            self.dscore = "DEF"
            return
        self.say("punt", f"{O.punter} punt; {self.names[1 - o]} take over at "
                         f"{spot_txt(rs, self.names[1 - o], self.names[o])}", 0, self.xp(Y, o), None)
        self.give_ball(1 - o, rs)

    def do_fg(self, O):
        e, rng, o, Y = self.e, self.rng, self.off, self.Y
        dist = Y + 17
        if dist <= e.fgx[-1]:
            p = _interp(dist, e.fgx, e.fgy)
        else:
            p = max(0.05, e.fgy[-1] - 0.035 * (dist - e.fgx[-1]))
        k = O.k
        p = clamp(p + k["fg_over"] * 0.8, 0.03, 0.995)
        st = self.ps[k["id"]]
        st[PIDX["fga"]] += 1
        self.ts[o][TIDX["fga"]] += 1
        self.elapse("run", o, fixed=5)
        if rng.random() < p:
            st[PIDX["fgm"]] += 1
            st[PIDX["fgb"]] += 2 if dist >= 50 else 1 if dist >= 40 else 0
            self.add_pts(o, 3)
            self.say("fg", f"{k['name']} {dist}-yard field goal GOOD", scoring=True, x0=self.xp(Y, o))
            self.pending_kick = o
            self.dscore = "FG"
        else:
            self.say("fg", f"{k['name']} {dist}-yard field goal NO GOOD", x0=self.xp(Y, o))
            self.give_ball(1 - o, int(min(80, 93 - Y)))

    # ------------------------------------------------------------ output
    def result(self):
        return {"home": self.names[0], "away": self.names[1], "score": list(self.pts), "ot": self.period == 3,
                "ps": {k: list(v) for k, v in self.ps.items()}, "ts": self.ts, "log": self.logs}


def _interp(x, xs, ys):
    if x <= xs[0]:
        return ys[0]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            t = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + t * (ys[i] - ys[i - 1])
    return ys[-1]
