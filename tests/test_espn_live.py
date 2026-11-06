import json, espn

def test_normalize_live_picks_actual_not_projected():
    pl = lambda n, pos, t, a, p: {"playerPoolEntry": {"player": {"fullName": n, "defaultPositionId": pos, "proTeamId": t, "stats": [
        {"scoringPeriodId": 9, "statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": p}, {"scoringPeriodId": 9, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": a}]}}}
    out = espn.normalize_live({"players": [pl("Zay Flowers", 3, 33, 17.44, 14.0), pl("Jaguars D/ST", 16, 30, 9, 7), {"playerPoolEntry": {"player": {"fullName": "X", "defaultPositionId": 3, "proTeamId": 1, "stats": [{"scoringPeriodId": 9, "statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": 5}]}}}]}, 9)
    assert out == [{"name": "Zay Flowers", "pos": "WR", "team": "BAL", "pts": 17.4}, {"name": "Jaguars D/ST", "pos": "DST", "team": "JAX", "pts": 9.0}]  # no actual yet -> skipped


def test_normalize_scoreboard_states_and_abbreviations():
    ev = lambda h, a, state, per, clk: {"competitions": [{"status": {"period": per, "displayClock": clk, "type": {"state": state, "completed": state == "post", "shortDetail": ""}},
                                                          "competitors": [{"homeAway": "home", "team": {"abbreviation": h}}, {"homeAway": "away", "team": {"abbreviation": a}}]}]}
    out = espn.normalize_scoreboard({"events": [ev("WSH", "LAR", "in", 3, "5:00"), ev("BAL", "JAX", "post", 4, "0:00"), ev("NO", "CLE", "pre", 0, "15:00"), {"competitions": [{}]}]})
    assert [(g["home"], g["away"], g["state"]) for g in out] == [("WAS", "LA", "in"), ("BAL", "JAX", "post"), ("NO", "CLE", "pre")]
    assert out[0]["period"] == 3 and out[0]["clock"] == "5:00"
