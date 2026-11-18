import pytest


def test_refresh_is_on_by_default_and_can_be_turned_off(monkeypatch):
    import server
    monkeypatch.delenv("GRIDSIM_REFRESH_HOURS", raising=False)
    assert server.refresh_hours() == 6                          # the data used to go stale unless someone set this or pressed Update data
    monkeypatch.setenv("GRIDSIM_REFRESH_HOURS", "0"); assert server.refresh_hours() == 0
    monkeypatch.setenv("GRIDSIM_REFRESH_HOURS", "2.5"); assert server.refresh_hours() == 2.5


def test_data_older_than_the_interval_is_refreshed_straight_away(monkeypatch):
    import server
    for age, due in ((1, False), (5.9, False), (6, True), (80, True), (None, True)):
        monkeypatch.setattr(server, "data_age_hours", lambda a=age: a)
        assert server.refresh_due(6) is due, age


def test_a_failed_download_keeps_the_last_good_data(monkeypatch):
    import subprocess, server
    ran = []

    def fake(cmd, **kw):
        ran.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, stdout="" if cmd[-1] == "fetch_data.py" else "built", stderr="FAIL pbp/play_by_play_2026 offline")
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(RuntimeError, match="no data files could be downloaded"):
        server.do_refresh()
    assert ran == ["fetch_data.py"]                              # the model is not rebuilt from the data it already had
