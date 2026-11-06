import json, espn

def test_normalize_live_picks_actual_not_projected():
    pl = lambda n, pos, t, a, p: {"playerPoolEntry": {"player": {"fullName": n, "defaultPositionId": pos, "proTeamId": t, "stats": [
        {"scoringPeriodId": 9, "statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": p}, {"scoringPeriodId": 9, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": a}]}}}
    out = espn.normalize_live({"players": [pl("Zay Flowers", 3, 33, 17.44, 14.0), pl("Jaguars D/ST", 16, 30, 9, 7), {"playerPoolEntry": {"player": {"fullName": "X", "defaultPositionId": 3, "proTeamId": 1, "stats": [{"scoringPeriodId": 9, "statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": 5}]}}}]}, 9)
    assert out == [{"name": "Zay Flowers", "pos": "WR", "team": "BAL", "pts": 17.4}, {"name": "Jaguars D/ST", "pos": "DST", "team": "JAX", "pts": 9.0}]  # no actual yet -> skipped
