import pandas as pd
import pytest

import ext_proj


def files(tmp_path, results=(None, None)):
    pd.DataFrame({"gsis_id": ["g1", "g2", "g3"], "espn_id": [111.0, 222.0, None], "sleeper_id": ["s1", "s2", "s3"]}).to_parquet(tmp_path / "roster_2026.parquet")
    pd.DataFrame({"season": 2026, "game_type": "REG", "week": [10, 11, 11], "gameday": ["2026-11-08", "2026-11-19", "2026-11-22"],
                  "gametime": ["13:00", "20:15", "13:00"], "result": [3.0, None, None]}).to_csv(tmp_path / "games.csv", index=False)


NOW = pd.Timestamp("2026-11-18 18:00", tz="UTC")


def test_espn_projected_stat_line_is_converted_to_standard_ppr():
    # 250 pass yd, 2 pass TD, 1 int, 20 rush yd, 5 rec, 60 rec yd, 1 rec TD
    stats = {"3": 250, "4": 2, "20": 1, "24": 20, "42": 60, "53": 5, "43": 1}
    assert ext_proj.espn_ppr(stats) == pytest.approx(0.04 * 250 + 8 - 2 + 2 + 6 + 5 + 6)


def test_logs_the_upcoming_week_before_kickoff_and_replaces_it_on_the_next_refresh(tmp_path):
    files(tmp_path)
    src = {"sleeper": lambda w: {"s1": 15.0, "s2": 7.5, "zz": 99.0}, "espn": lambda w: {"111": 14.0}}
    assert "week 11: sleeper 2, espn 1" in ext_proj.log_week(str(tmp_path), NOW, src)
    d = pd.read_csv(tmp_path / "ext_proj.csv"); assert set(d.week) == {11} and len(d) == 3
    ext_proj.log_week(str(tmp_path), NOW + pd.Timedelta(hours=2), {"sleeper": lambda w: {"s1": 16.0}, "espn": lambda w: {"111": 14.0}})
    d = pd.read_csv(tmp_path / "ext_proj.csv")
    assert len(d) == 2 and d[d.source == "sleeper"].ppr.iloc[0] == 16.0                    # newer snapshot replaced the old one, no duplicates


def test_week_is_frozen_once_the_first_game_has_started(tmp_path):
    files(tmp_path)
    late = pd.Timestamp("2026-11-20 02:00", tz="UTC")                                       # after Thursday 8:15pm ET
    assert "frozen" in ext_proj.log_week(str(tmp_path), late, {"sleeper": lambda w: {"s1": 1.0}})
    assert not (tmp_path / "ext_proj.csv").exists()


def test_one_source_failing_does_not_stop_the_other(tmp_path):
    files(tmp_path)
    def boom(w): raise OSError("offline")
    out = ext_proj.log_week(str(tmp_path), NOW, {"sleeper": boom, "espn": lambda w: {"222": 9.0}})
    assert "sleeper failed" in out and "espn 1" in out
    assert pd.read_csv(tmp_path / "ext_proj.csv").gsis_id.tolist() == ["g2"]
