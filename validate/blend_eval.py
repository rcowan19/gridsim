"""Held-out evaluation of the score-tilting blends on saved walk-forward sims.
    python -m validate.blend_eval        # Brier by season for: raw sim, market line at beta, Elo margin at beta"""
import numpy as np, pandas as pd, vegas
from validate.report import elo_probs
OUT = "validate/out"


def brier(S, beta, src):
    G = pd.read_csv(f"{OUT}/wf_{S}_games.csv"); sc = np.load(f"{OUT}/wf_{S}.npz")["sc"].astype(float)
    me = elo_probs(G, S)[1]; y = ((G.hs > G["as"]) + .5 * (G.hs == G["as"])).values; p = []
    for i in range(len(G)):
        s = sc[i]; m = s.mean(0)
        spr, tot = (me[i], m.sum()) if src == "elo" else (G.spread.iloc[i], G.total.iloc[i])
        w = vegas.tilt_weights(s, m[0] + beta * (tot / 2 + spr / 2 - m[0]), m[1] + beta * (tot / 2 - spr / 2 - m[1])); d = s[:, 0] - s[:, 1]
        p.append(w @ ((d > 0) + .5 * (d == 0)))
    return float(((np.array(p) - y) ** 2).mean())


if __name__ == "__main__":
    for src, betas in (("market", (0, .6, .8, .9, 1.0)), ("elo", (0, .3, .5, .7, .9))):
        print(src)
        for b in betas:
            r = [brier(S, b, "elo" if src == "elo" else "mkt") for S in (2023, 2024, 2025)]
            print(f"  beta {b}: " + "  ".join(f"{S} {x:.4f}" for S, x in zip((2023, 2024, 2025), r)) + f"  mean {np.mean(r):.4f}")
