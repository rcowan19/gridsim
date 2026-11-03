"""2026 schedule + betting lines from nflverse games.csv."""
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PATHS = [os.path.join(HERE, "data", "games.csv"), os.path.join(HERE, "schedule_2026.csv")]   # downloaded copy first, bundled fallback


def load(season=2026):
    d = None
    for p in PATHS:
        try:
            d = pd.read_csv(p)
            if len(d[d.season == season]):
                break
        except OSError:
            continue
    if d is None:
        return []
    d = d[(d.season == season) & (d.game_type == "REG")]
    out = []
    for r in d.itertuples():
        nz = lambda x: None if x != x else float(x)
        out.append({"week": int(r.week), "id": r.game_id, "away": r.away_team, "home": r.home_team, "day": r.gameday, "time": r.gametime,
                    "spread": nz(r.spread_line), "total": nz(r.total_line), "neutral": r.location == "Neutral",
                    "played": r.home_score == r.home_score, "hs": nz(r.home_score), "as": nz(r.away_score)})
    return out
