# GridSim

Tabs: **Matchup** (win prob, model-vs-Vegas edges, matchup ratings, fantasy projections, correlations, game-script splits, prop calculator) ·
**Fantasy rankings** (whole-week slate sim: floor/ceiling/boom/bust, VOR, tiers, stack targets) ·
**Lineup lab** (your lineup vs opponent with correlated win probability, optimizer, start/sit) · **Game viewer**.

Play-by-play NFL Monte Carlo simulator with a web UI.

    pip install -r requirements.txt
    python fetch_data.py       # download nflverse data (re-run weekly to refresh)
    python build_model.py      # data -> model.json (team ratings, player usage, empirical pools)
    python server.py           # http://localhost:8000
    python sim.py KC BUF -n 5000   # CLI
    python calibrate.py 3000       # league-average sim vs actual

Engine (`engine.py`): down/distance/field-position/clock/timeouts state machine; empirical pools for
yards, air yards, YAC, clock, punts, kickoffs; 4th-down, kneel, 2-pt and OT logic; per-player usage.
Calibration knobs: `Engine.DEFAULT_TUNE`. Known gap: league scoring ~21.1 ppg vs 23.0 actual
(rush TDs and long passes run light); no onside kicks, fumble/punt-return TDs, or injuries-in-game.

## Evaluation & tuning
    python backtest.py -s 3 -n 300   # walk-forward backtest -> backtest_games.csv / backtest_players.csv
    python tune.py                   # fit engine knobs to league averages
    python fit_calib.py 5            # fit player-projection calibration (train wk<=5, test after) -> calib.json

## ESPN league (My league tab)
Enter your league ID (public leagues need nothing else; private leagues need the `espn_s2` and `SWID` cookies). The server reads
rosters/settings/schedule from ESPN (`espn.py`), matches players to projections, and shows lineup advice, league matchup win
probabilities, power rankings and a waiver wire. Dev/testing: `GRIDSIM_ESPN_MOCK=path.json python server.py` serves a saved ESPN payload.
