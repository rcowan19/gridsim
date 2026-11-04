"""Correctness / property tests for the play-by-play engine.  Run: pytest -q tests"""
import json, math, re, random
import numpy as np, pytest
from engine import Engine, PIDX, TIDX

M = json.load(open("model.json"))
E = Engine(M)
TEAMS = sorted(M["teams"])
PAIRS = [(TEAMS[i], TEAMS[(i * 7 + 3) % len(TEAMS)]) for i in range(len(TEAMS)) if TEAMS[i] != TEAMS[(i * 7 + 3) % len(TEAMS)]]


def game(h, a, seed, **k):
    return E.game(E.team(h), E.team(a), seed=seed, **k)


def secs(clock):
    m = re.match(r"(Q\d|OT) (\d+):(\d+)", clock)
    q, mm, ss = m.group(1), int(m.group(2)), int(m.group(3))
    return (q, mm * 60 + ss)


def test_determinism():
    a, b = game("KC", "BUF", 123), game("KC", "BUF", 123)
    assert a["score"] == b["score"] and a["ps"] == b["ps"] and a["ts"] == b["ts"]


def test_scores_legal_and_monotone():
    for i, (h, a) in enumerate(PAIRS[:25]):
        r = game(h, a, 1000 + i, log=True)
        hs = as_ = 0
        for ev in r["log"]:
            dh, da = ev["hs"] - hs, ev["as"] - as_
            assert dh >= 0 and da >= 0 and not (dh and da), ev
            assert dh in (0, 1, 2, 3, 6) and da in (0, 1, 2, 3, 6), ev           # XP, 2pt/safety, FG, TD (each is its own log row)
            hs, as_ = ev["hs"], ev["as"]
        assert [hs, as_] == r["score"]


def test_down_distance_field_position_valid():
    bad = []
    for i, (h, a) in enumerate(PAIRS[:25]):
        for ev in game(h, a, 2000 + i, log=True)["log"]:
            if ev["kind"] in ("pass", "run", "sack", "kneel", "int", "fg", "punt") and ev["down"]:
                if not (1 <= ev["down"] <= 4 and ev["dist"] >= 1):
                    bad.append(ev)
    assert not bad, bad[:3]


def test_clock_never_increases_within_period():
    for i, (h, a) in enumerate(PAIRS[:25]):
        last = {}
        for ev in game(h, a, 3000 + i, log=True)["log"]:
            if ev["kind"] in ("final", "half", "ot"):
                continue
            q, s = secs(ev["clock"])
            if q in last:
                assert s <= last[q] + 0, (ev, last[q])
            last[q] = s
            assert s >= 0


def test_player_stats_reconcile_with_team_stats():
    for i, (h, a) in enumerate(PAIRS[:30]):
        r = game(h, a, 4000 + i)
        s = lambda k: sum(v[PIDX[k]] for v in r["ps"].values())
        assert s("pc") == s("rec") and s("pyd") == s("recyd") and s("ptd") == s("rectd"), (h, a)
        assert s("tgt") <= s("pa")
        assert s("fgm") <= s("fga") and s("xpm") <= s("xpa")
        pts = 6 * (s("rtd") + s("rectd")) + 3 * s("fgm") + s("xpm")
        dtd = sum(t[TIDX["dtd"]] for t in r["ts"]); saf = sum(t[TIDX["saf"]] for t in r["ts"])
        two = r["score"][0] + r["score"][1] - pts - 6 * dtd - 2 * saf
        assert 0 <= two <= 16 and two % 2 == 0 or two in (0, 2, 4, 6, 8, 10, 12, 14, 16), (h, a, two)   # remainder is 2-point conversions (+ blown rounding)


def test_possession_and_ties():
    ties = ots = 0
    for i, (h, a) in enumerate(PAIRS[:40]):
        r = game(h, a, 5000 + i)
        if r["score"][0] == r["score"][1]:
            ties += 1
            assert r["ot"], "a tie can only occur after OT"
        ots += r["ot"]
    assert ties <= ots


def test_home_away_symmetry_neutral():
    """On a neutral field the engine must not favour a side: team X scores the same as home or away."""
    n, d = 400, []
    for s in range(n):
        d.append(game("DET", "NE", s, neutral=True)["score"][0] - game("NE", "DET", s + 10**6, neutral=True)["score"][1])   # DET as home vs DET as away
    d = np.array(d); z = d.mean() / (d.std() / math.sqrt(n))
    assert abs(z) < 3.5, f"side bias z={z:.2f}"


def test_no_negative_or_absurd_stats():
    for i, (h, a) in enumerate(PAIRS[:30]):
        r = game(h, a, 6000 + i)
        for v in r["ps"].values():
            assert min(v[PIDX[k]] for k in ("pa", "pc", "tgt", "rec", "ra", "fga", "fgm")) >= 0
        assert all(t[TIDX["plays"]] >= 20 for t in r["ts"])
        assert 0 <= r["score"][0] <= 90 and 0 <= r["score"][1] <= 90


def test_edge_state_helpers():
    from engine import zone, dbucket, run_zone
    assert zone(1) == 4 and zone(99) == 0 and dbucket(1) == 0 and dbucket(20) == 4 and run_zone(1) == 3
