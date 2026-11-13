"""A small Sleeper league (4 teams, 3 weeks) shaped like api.sleeper.app responses, plus a trimmed /players/nfl map."""
PLAYERS = {
    "4035": {"name": "Alvin Kamara", "pos": "RB", "team": "NO", "injury": None},
    "4881": {"name": "Lamar Jackson", "pos": "QB", "team": "BAL", "injury": None},
    "6794": {"name": "Justin Jefferson", "pos": "WR", "team": "MIN", "injury": "Questionable"},
    "7564": {"name": "Ja'Marr Chase", "pos": "WR", "team": "CIN", "injury": None},
    "4034": {"name": "Travis Kelce", "pos": "TE", "team": "KC", "injury": None},
    "2749": {"name": "Justin Tucker", "pos": "K", "team": "BAL", "injury": None},
    "DET": {"name": "DET D/ST", "pos": "DST", "team": "DET", "injury": None},
    "9221": {"name": "Bench Guy", "pos": "RB", "team": "LA", "injury": None},
    "1111": {"name": "Hurt Back", "pos": "RB", "team": "SF", "injury": "Out"},
}
ROSTER_POS = ["QB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "K", "DEF", "BN", "BN", "IR"]
SCORING = {"pass_yd": 0.04, "pass_td": 4, "pass_int": -2, "rush_yd": 0.1, "rush_td": 6, "rec": 0.5, "rec_yd": 0.1, "rec_td": 6, "bonus_rec_te": 0.5, "fum_lost": -2}


def raw():
    ros = lambda rid, owner, starters, players, reserve=None, w=3, l=0: {
        "roster_id": rid, "owner_id": owner, "starters": starters, "players": players, "reserve": reserve or [],
        "settings": {"wins": w, "losses": l, "ties": 0, "fpts": 412, "fpts_decimal": 34, "waiver_position": rid, "waiver_budget_used": 10 * rid}}
    rosters = [
        ros(1, "u1", ["4881", "4035", "6794", "7564", "4034", "9221", "0", "2749", "DET"], ["4881", "4035", "6794", "7564", "4034", "9221", "2749", "DET", "1111"], ["1111"]),
        ros(2, "u2", [], []), ros(3, "u3", [], [], w=1, l=2), ros(4, None, [], [], w=0, l=3)]
    users = [{"user_id": "u1", "display_name": "roan", "metadata": {"team_name": "Roan's Rowdy Team"}}, {"user_id": "u2", "display_name": "sam"},
             {"user_id": "u3", "display_name": "kim", "metadata": {}}]
    mu = lambda pairs: [{"roster_id": r, "matchup_id": m, "points": 0.0, "players_points": {}} for r, m in pairs]
    return {"league": {"league_id": "L1", "name": "Sleeper Test League", "season": "2026", "sport": "nfl", "status": "in_season", "total_rosters": 4,
                       "roster_positions": ROSTER_POS, "scoring_settings": SCORING,
                       "settings": {"playoff_week_start": 4, "playoff_teams": 2, "waiver_type": 2, "waiver_budget": 100, "waiver_clear_days": 2, "leg": 3}},
            "rosters": rosters, "users": users, "state": {"season": "2026", "week": 3},
            "matchups": {1: mu([(1, 1), (2, 1), (3, 2), (4, 2)]), 2: mu([(1, 1), (3, 1), (2, 2), (4, 2)]), 3: mu([(1, 1), (4, 1), (2, 2), (3, 2)])}}


def live():
    return [{"roster_id": 1, "matchup_id": 1, "players_points": {"4035": 18.4, "6794": 22.0, "DET": 7.0, "2749": 0.0, "000": 5.0}},
            {"roster_id": 2, "matchup_id": 1, "players_points": {"4881": 31.26}}]
