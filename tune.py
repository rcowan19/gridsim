"""Auto-tune Engine knobs so league-average sim stats match actual (coordinate descent, common random numbers).
    python tune.py            # prints best knobs; paste into Engine.DEFAULT_TUNE (or pass --write)
"""
import json, re, sys
from calibrate import calibrate
from engine import Engine

W = {"ppg": 3, "yards_pg": 2, "ypa": 1, "ypc": 1, "comp_pct": 1, "pass_td_pg": 1.5, "rush_td_pg": 1.5,
     "punts_pg": 2, "fga_pg": 1, "to_pg": .5, "plays_pg": 1, "int_rate": .3}
# knob: (lo, hi, step)
KNOBS = {"comp_add": (-.03, .02, .005), "run_add": (-.4, .4, .1), "pace_mult": (.94, 1.06, .015), "go_mult": (1.0, 1.6, .1),
         "pen_scale": (.6, 1.3, .1), "gl_add": (0, 1.2, .2), "yac_scale": (.92, 1.1, .03), "air_add": (-.2, 1.0, .2)}

def loss(t, n):
    sim, act = calibrate(n, t, verbose=False)
    return sum(w * ((sim[k] - act[k]) / act[k]) ** 2 for k, w in W.items()), sim, act

def main(n=2500, passes=2):
    cur = {k: Engine.DEFAULT_TUNE[k] for k in KNOBS}
    best, sim, act = loss(cur, n)
    print(f"start loss {best:.5f}  ppg {sim['ppg']:.2f}", flush=True)
    for p in range(passes):
        for k, (lo, hi, st) in KNOBS.items():
            for d in (+1, -1):
                while True:
                    v = round(cur[k] + d * st, 4)
                    if not lo <= v <= hi: break
                    t = dict(cur, **{k: v}); l, s, _ = loss(t, n)
                    if l < best - 1e-6:
                        best, cur, sim = l, t, s; print(f"  {k}={v}  loss {best:.5f}  ppg {s['ppg']:.2f} yds {s['yards_pg']:.0f}", flush=True)
                    else: break
    print("\nBEST", json.dumps(cur))
    print({k: (round(sim[k], 3), round(act[k], 3)) for k in W})
    return cur

if __name__ == "__main__":
    cur = main()
    if "--write" in sys.argv:
        s = open("engine.py").read()
        for k, v in cur.items():
            s = re.sub(rf'("{k}": )[-0-9.]+', rf'\g<1>{v}', s, count=1)
        open("engine.py", "w").write(s); print("written to engine.py")
