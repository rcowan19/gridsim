# GridSim

Navigation: **Games** (every game of the week: cards or table, market edges, weather) ·
**Players** (rankings with floor/ceiling/boom/bust/tiers, trends, stack targets) ·
**My Team** (ESPN league: lineup advice, waivers, league matchups, sandbox lineup lab) ·
**Game Lab** (any two teams: overview, fantasy, matchup ratings and correlations, play-by-play).
Week, scoring format and simulation depth are global controls in the top bar.

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

## Game conditions
Wind and roof adjust the simulation (`engine.env_factors`; fitted to 2025-26 play-by-play and shrunk: about -0.4 points of total per mph of
wind above 5, indoors about +0.5). Temperature, weekday and kickoff slot showed no usable signal. Forecasts come from Open-Meteo
(`weather.py`, free, no key) for outdoor games; if unavailable the sim uses roof only. The matchup sidebar can override conditions manually.


## Validation suite (audit branch)

`sh validate/run_all.sh` runs the leak-free walk-forward backtest for 2023-2025 (model rebuilt each week from earlier data only), writes league-level and game-level reports to `validate/out/`, evaluates the Vegas/Elo blends, checks game-script realism and runs the engine invariant tests (`pytest -q tests`). See `AUDIT.md` for findings, `validate.grid` for knob experiments and `validate.players` for the player-level (fantasy) check.


## UI (branch `ui/redesign`)
Sidebar/rail/tab-bar shell, Home dashboard, Settings, light/dark themes, Ctrl/Cmd+K palette, URL-state routing. Design tokens and component guide: `docs/ui/design-system.md`; findings and before/after: `UI_AUDIT.md`. Checks: `python tests/ui/contrast.py`, `python tests/ui/axe_run.py`, `python -m pytest -q tests/ui` (server on :8000).
