"""Play-level realism check: game-script pass rates, 4th-down go rates, 3rd-down conversion, explosives; live model vs real pbp.
    python -m validate.gamestate [--season 2025] [-n 2500]"""
import argparse, json, random, re
import numpy as np, pandas as pd
from engine import Engine

ap = argparse.ArgumentParser(); ap.add_argument("--season", type=int, default=2025); ap.add_argument("-n", type=int, default=2500); a = ap.parse_args()
M = json.load(open("model.json")); E = Engine(M); T = sorted(M["teams"]); rng = random.Random(11); C = {t: E.team(t) for t in T}
rows = []
for i in range(a.n):
    h, v = rng.sample(T, 2); r = E.game(C[h], C[v], seed=i, log=True); prev = [0, 0]
    for ev in r["log"]:
        if ev["kind"] in ("pass", "run", "sack", "punt", "fg") and ev["down"]:
            q = re.match(r"Q(\d)", ev["clock"]); q = int(q.group(1)) if q else 5
            o = ev["o"]; sd = (prev[0] - prev[1]) * (1 if o == 0 else -1)           # score before the play
            Y = None
            m = re.match(r"(\w+) (\d+)", ev["spot"])
            rows.append({"q": q, "down": ev["down"], "dist": ev["dist"], "sd": sd, "kind": ev["kind"], "yards": ev["yards"], "text": ev["text"], "spot": ev["spot"], "off": ev["off"]})
        prev = [ev["hs"], ev["as"]]
S = pd.DataFrame(rows)
S["db"] = S.kind.isin(["pass", "sack"]) | S.text.str.contains("scrambles")
S["ydl"] = [(100 - int(re.search(r"(\d+)$", sp).group(1))) if sp.split()[0] == o else int(re.search(r"(\d+)$", sp).group(1)) if sp != "50" else 50 for sp, o in zip(S.spot, S.off)]
P = pd.read_parquet(f"data/pbp_{a.season}.parquet", columns=["season_type", "play_type", "down", "ydstogo", "qb_dropback", "rush_attempt", "qb_kneel", "qb_spike", "score_differential", "qtr", "yardline_100", "yards_gained", "pass_attempt", "sack", "first_down", "third_down_converted", "fourth_down_converted", "fourth_down_failed", "wp"])
P = P[(P.season_type == "REG") & P.down.notna() & (P.qb_kneel != 1) & (P.qb_spike != 1) & P.play_type.isin(["pass", "run", "punt", "field_goal"])]
P["db"] = (P.qb_dropback == 1)
bins = [-100, -15, -9, -1, 0, 8, 14, 100]; lab = ["<=-15", "-14..-9", "-8..-1", "tied", "1..8", "9..14", ">=15"]
def pr(df, sd, q):
    d = df[df.down.isin([1, 2, 3]) & df.play_type.isin(["pass", "run"])]; d = d.assign(b=pd.cut(d[sd], [-100, -15, -9, -1, 0, 8, 14, 100], labels=lab))
    return d[q(d)].groupby("b", observed=True).db.mean()
out = []
w = lambda x: lambda d: d.qtr.isin(x)
Sp = S.assign(score_differential=S.sd, qtr=S.q, play_type="pass")
for nm, qs in (("Q1-3", [1, 2, 3]), ("Q4", [4])):
    r, s = pr(P, "score_differential", w(qs)), pr(Sp[Sp.kind.isin(["pass", "run", "sack"]) & (Sp.down < 4)].assign(db=lambda d: d.db), "score_differential", w(qs))
    out.append(f"\n**Dropback rate on 1st-3rd down by score margin, {nm}**\n\n| margin | real | sim |\n|---|---|---|\n" + "\n".join(f"| {k} | {r.get(k, np.nan):.3f} | {s.get(k, np.nan):.3f} |" for k in lab))
# 4th down
R4 = P[P.down == 4].assign(go=lambda d: d.play_type.isin(["pass", "run"]), fg=lambda d: d.play_type == "field_goal")
S4 = S[S.down == 4].assign(go=lambda d: d.kind.isin(["pass", "run", "sack"]), fg=lambda d: d.kind == "fg")
R4 = R4[(R4.qtr <= 3) | (R4.wp.between(.2, .8))]; S4 = S4[S4.q <= 3]; R4 = R4[R4.qtr <= 3]
db = [0, 1, 3, 6, 100]; dl = ["1", "2-3", "4-6", "7+"]
for nm, df, dcol, ycol in (("real", R4, "ydstogo", "yardline_100"), ("sim", S4, "dist", "ydl")):
    df["b"] = pd.cut(df[dcol], db, labels=dl); df["z"] = pd.cut(df[ycol], [0, 40, 60, 101], labels=["opp 0-40", "mid 40-60", "own half"])
g = lambda df: df.groupby(["z", "b"], observed=True).go.mean().unstack()
out.append("\n**4th-down go rate, Q1-3 (rows: field zone, cols: yards to go)**\n\nreal:\n\n" + g(R4).round(3).to_markdown() + "\n\nsim:\n\n" + g(S4).round(3).to_markdown())
# 3rd down conversion
S3 = S[(S.down == 3) & S.kind.isin(["pass", "run", "sack"])].copy(); S3["conv"] = S3.yards >= S3.dist; S3["b"] = pd.cut(S3.dist, [0, 2, 4, 7, 10, 100], labels=["1-2", "3-4", "5-7", "8-10", "11+"])
R3 = P[(P.down == 3) & P.play_type.isin(["pass", "run"])].copy(); R3["conv"] = R3.yards_gained >= R3.ydstogo; R3["b"] = pd.cut(R3.ydstogo, [0, 2, 4, 7, 10, 100], labels=["1-2", "3-4", "5-7", "8-10", "11+"])
t = pd.DataFrame({"real": R3.groupby("b", observed=True).conv.mean(), "sim": S3.groupby("b", observed=True).conv.mean()}); t.loc["all"] = [R3.conv.mean(), S3.conv.mean()]
out.append("\n**3rd-down conversion by distance (yardage-based; excludes penalty first downs)**\n\n" + t.round(3).to_markdown())
# explosives and per-down yards
d = P[P.play_type.isin(["pass", "run"])]
dbs, drs = d[d.db & (d.sack != 1)], d[~d.db & (d.rush_attempt == 1)]
sp, sr = S[S.kind == "pass"], S[(S.kind == "run") & ~S.text.str.contains("scrambles")]
out.append(f"\n**Explosives:** pass 20+ yds per completion-or-attempt: real {(dbs.yards_gained >= 20).mean():.3f}, sim {(sp.yards >= 20).mean():.3f} | run 10+ per carry: real {(drs.yards_gained >= 10).mean():.3f}, sim {(sr.yards >= 10).mean():.3f}")
for dn in (1, 2, 3):
    out.append(f"\n- down {dn}: pass rate real {d[d.down == dn].db.mean():.3f} / sim {S[(S.down == dn) & S.kind.isin(['pass', 'run', 'sack'])].db.mean():.3f}")
txt = f"# Game-script realism: live model ({a.n} sims) vs {a.season} regular season\n" + "\n".join(out)
open("validate/out/gamestate.md", "w").write(txt); print(txt)
