"""When an injured player is expected back, so rest-of-season values count the weeks he can actually play.

The weekly projections treat a player listed Out as out for every later week. But a game-day status covers one game, and injured reserve
has a fixed minimum, so each case gets a return week:

  * on injured reserve: the week after his team's 4th game since he went on IR (the NFL minimum). If he became eligible within the last
    two weeks and is still out, two more weeks from the week shown; if longer ago than that, he is assumed done for the season (a
    player who could be activated and is not is rarely close).
  * any other Out or Doubtful: back next week.

These are estimates from what the data holds (roster status by week, game schedule); there is no medical news in it, so a long-term injury
can still come back later than this. The app shows the week it used next to the player so it can be judged.
"""
IR_GAMES = 4


def injury_returns(out, res_weeks, team_weeks, week, last, roster_latest):
    """out: {player id: team abbr} of players currently Out or Doubtful.
    res_weeks: {player id: set of weeks the roster file lists him on reserve/injured}.
    team_weeks: {team abbr: sorted weeks that team plays}. week: week shown. last: last week of the season.
    roster_latest: newest week in the roster file.
    Returns {player id: {"ret": first week he can play or None if not this season, "kind": "ir" | "week", "since": week put on IR or None, "note": text}}."""
    res = {}
    for pid, team in out.items():
        weeks = res_weeks.get(pid) or set()
        if roster_latest in weeks:                                   # on injured reserve in the newest roster
            since = roster_latest
            while since - 1 in weeks:
                since -= 1
            games = [w for w in team_weeks.get(team, []) if w >= since]
            earliest = games[IR_GAMES - 1] + 1 if len(games) >= IR_GAMES else last + 1
            if earliest > week:
                ret, note = earliest, f"On injured reserve since week {since}; the earliest he can return is week {earliest}"
            elif week - earliest <= 2:
                ret, note = week + 2, f"On injured reserve since week {since}; eligible since week {earliest} and still out, so two more weeks"
            else:
                ret, note = None, f"On injured reserve since week {since}; eligible since week {earliest} and still out, so assumed out for the season"
            res[pid] = {"ret": ret if ret is not None and ret <= last else None, "kind": "ir", "since": since, "note": note}
        else:
            ret = week + 1
            res[pid] = {"ret": ret if ret <= last else None, "kind": "week", "since": None, "note": f"Out in week {week}; back the week after"}
    return res
