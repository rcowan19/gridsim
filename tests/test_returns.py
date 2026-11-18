from returns import injury_returns

TW = {"ARI": list(range(1, 19)), "BUF": [w for w in range(1, 19) if w != 7]}                 # ARI plays every week, BUF has a bye in 7


def test_ir_player_counts_from_the_week_after_four_games():
    # put on IR in week 10: misses weeks 10 to 13, so he can play in week 14 (the case that showed Jeremiyah Love as worth nothing all season)
    r = injury_returns({"love": "ARI"}, {"love": {10}}, TW, week=10, last=18, roster_latest=10)["love"]
    assert (r["ret"], r["kind"], r["since"]) == (14, "ir", 10) and "week 10" in r["note"] and "week 14" in r["note"]


def test_ir_clock_skips_a_bye_and_follows_the_streak():
    r = injury_returns({"p": "BUF"}, {"p": {5, 6, 7, 8}}, TW, week=9, last=18, roster_latest=8)["p"]       # on IR since 5: games 5, 6, 8, 9, so back in week 10
    assert r["since"] == 5 and r["ret"] == 10
    r = injury_returns({"p": "ARI"}, {"p": {3, 8}}, TW, week=9, last=18, roster_latest=8)["p"]            # an earlier stint does not count: this one began in week 8
    assert r["since"] == 8 and r["ret"] == 12


def test_just_past_the_minimum_means_two_more_weeks_but_long_past_means_done():
    r = injury_returns({"p": "ARI"}, {"p": set(range(6, 11))}, TW, week=11, last=18, roster_latest=10)["p"]   # eligible since week 10, still out in 11
    assert r["ret"] == 13 and "two more weeks" in r["note"]
    r = injury_returns({"p": "ARI"}, {"p": set(range(1, 11))}, TW, week=11, last=18, roster_latest=10)["p"]   # on IR since week 1: eligible since week 5
    assert r["ret"] is None and "out for the season" in r["note"]


def test_game_day_out_is_back_next_week_and_the_season_end_cuts_it_off():
    r = injury_returns({"a": "ARI", "b": "ARI", "c": "ARI"}, {"b": {8}}, TW, week=11, last=18, roster_latest=10)
    assert r["a"] == {"ret": 12, "kind": "week", "since": None, "note": "Out in week 11; back the week after"} and r["b"]["kind"] == "week"   # IR long ago, not in the newest roster
    late = injury_returns({"a": "ARI", "b": "ARI"}, {"b": {16}}, TW, week=18, last=18, roster_latest=16)
    assert late["a"]["ret"] is None                                         # nothing left of the season
    assert injury_returns({"b": "ARI"}, {"b": {16}}, TW, week=16, last=18, roster_latest=16)["b"]["ret"] is None   # 4 games from week 16 is past week 18


def test_endpoint_lists_everyone_who_is_out_with_a_return_week(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("GRIDSIM_NFL_STATUS_MOCK", str(tmp_path / "none.json"))
    import server
    d = TestClient(server.app).get("/api/returns", params={"week": 10}).json()
    assert d["week"] == 10 and d["returns"], "the model has players who are Out"
    assert all(set(v) == {"ret", "kind", "since", "note"} and v["kind"] in ("ir", "week") and (v["ret"] is None or v["ret"] > 10) for v in d["returns"].values())


def test_ir_start_comes_from_his_last_game_when_the_roster_file_only_shows_the_newest_week():
    # hurt in week 8, on IR since then, but the roster file lists him on reserve only in week 11: his four games are 9, 10, 11, 12
    r = injury_returns({"love": "ARI"}, {"love": {11}}, TW, week=11, last=18, roster_latest=11, last_played={"love": 8})["love"]
    assert (r["since"], r["ret"]) == (9, 13)
    # no last-game information: falls back to the roster file
    r = injury_returns({"love": "ARI"}, {"love": {11}}, TW, week=11, last=18, roster_latest=11)["love"]
    assert r["since"] == 11
    # a last game after the roster's IR start (activated, played, hurt again) does not move the start earlier
    r = injury_returns({"p": "ARI"}, {"p": {10, 11}}, TW, week=11, last=18, roster_latest=11, last_played={"p": 12})["p"]
    assert r["since"] == 10
