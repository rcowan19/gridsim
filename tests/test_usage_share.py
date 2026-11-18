import build_model as bm

ALL = {"tgt": 10.0, "rz_tgt": 2.0, "car": 10.0, "gl": 1.0}
PREV = {"tgt": 4.0, "rz_tgt": 1.0, "car": 4.0, "gl": 0.5}
WEEKS = {w: {"tgt": 1.0, "rz_tgt": 0.2, "car": 1.0, "gl": 0.1} for w in range(1, 7)}
KNOWN = {("ARI", w) for w in range(1, 7)}


def tot(snaps, known=KNOWN, played=(), team="ARI"):
    return bm.available_totals(ALL, PREV, WEEKS, snaps, known, played, team)


def test_a_player_who_missed_games_is_divided_by_the_games_he_played():
    t = tot({1: "ARI", 2: "ARI", 3: "ARI"})                           # played weeks 1-3 of 6
    assert t["car"] == 4.0 + 3 and t["tgt"] == 4.0 + 3 and abs(t["gl"] - 0.8) < 1e-9


def test_no_snap_data_keeps_every_game():
    assert tot(None) is ALL and tot({}) is ALL


def test_a_week_the_snap_file_does_not_cover_counts_for_everyone():
    # snap counts are published after the play-by-play, so the newest week can be missing: do not divide him by a week he may have played
    t = tot({1: "ARI", 2: "ARI"}, known={("ARI", w) for w in range(1, 6)})
    assert t["car"] == 4.0 + 2 + 1


def test_a_week_with_his_own_plays_counts_even_if_the_snap_row_is_missing():
    assert tot({1: "ARI"}, played={4})["car"] == 4.0 + 2


def test_weeks_with_another_team_do_not_count_for_this_one():
    t = tot({1: "NYJ", 2: "NYJ", 3: "ARI"})                           # traded after week 2
    assert t["car"] == 4.0 + 1
