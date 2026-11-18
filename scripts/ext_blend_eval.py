"""Score the model against Sleeper and ESPN projections, and blends of them, on weeks where all were recorded.

    python backtest.py -s 4 -n 400        # writes backtest_players.csv (the model's walk-forward projections and actual PPR)
    python scripts/ext_blend_eval.py      # needs data/ext_proj.csv, which the server fills each week (see ext_proj.py)

Prints mean absolute error and correlation for each source, then the blend weight on the outside projections that minimises error. Use it
only once several weeks are in: a handful of rows says nothing. The model's rows come from a walk-forward rebuild, the outside ones are the
last snapshot before kickoff, so neither has seen the result."""
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ext = pd.read_csv(os.path.join(HERE, "data", "ext_proj.csv"))
    bt = pd.read_csv(os.path.join(HERE, "backtest_players.csv")).dropna(subset=["pred"])
    wide = ext.pivot_table(index=["week", "gsis_id"], columns="source", values="ppr").reset_index().rename(columns={"gsis_id": "pid"})
    d = bt.merge(wide, on=["week", "pid"], how="inner")
    srcs = [c for c in ("sleeper", "espn") if c in d]
    d = d.dropna(subset=srcs)
    d = d[d.pred >= 3]
    if len(d) < 200:
        print(f"only {len(d)} player-weeks recorded in common ({d.week.nunique()} weeks); wait for more before trusting any number")
    if not len(d):
        return
    d["ext"] = d[srcs].mean(axis=1)
    print(f"{len(d)} player-weeks over weeks {sorted(d.week.unique())}, model projection >= 3")
    for name, col in [("GridSim", "pred")] + [(s.title(), s) for s in srcs] + [("outside mean", "ext")]:
        print(f"  {name:13s} MAE {np.abs(d[col] - d.act).mean():.2f}  corr {np.corrcoef(d[col], d.act)[0, 1]:.3f}  bias {np.mean(d[col] - d.act):+.2f}")
    grid = np.linspace(0, 1, 11)
    mae = [np.abs((1 - w) * d.pred + w * d.ext - d.act).mean() for w in grid]
    best = int(np.argmin(mae))
    print("weight on outside projections -> MAE:  " + "  ".join(f"{w:.1f}:{m:.2f}" for w, m in zip(grid, mae)))
    print(f"best weight {grid[best]:.1f} (MAE {mae[best]:.2f} vs model alone {mae[0]:.2f}, outside alone {mae[-1]:.2f})")


if __name__ == "__main__":
    main()
