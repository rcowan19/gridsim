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

### Live scores
`/api/espn_live` returns each player's actual fantasy points for the week (ESPN's own numbers under your league's scoring, so K and D/ST
are exact) plus game status from ESPN's public scoreboard; if the scoreboard is unreachable the page estimates status from kickoff times.
Projections then update for the rest of the week: a finished game locks the player at his actual score, a game in progress counts actual
so far plus the unplayed share of the pre-game projection, and a game not started keeps the pre-game projection. Lineup totals, win
probabilities, best-lineup and rest-of-season numbers all read that one updated value, and players whose games have started are locked in
their slots (as on ESPN). The pre-game projection stays visible in its own column. No model or simulation logic changes.
Dev/testing: `GRIDSIM_ESPN_LIVE_MOCK` and `GRIDSIM_NFL_STATUS_MOCK` serve saved payloads.

## Leagues, accounts and billing (branch `product/accounts-billing`)
- **Platforms:** ESPN and Sleeper (`espn.py`, `sleeper.py`). Both normalize to one league shape, so every page works on either. Sleeper needs no keys: enter a username to list leagues, or a league ID. Dev: `GRIDSIM_SLEEPER_MOCK=path.json`.
- **Accounts:** Supabase Auth (email + Google). The server verifies the JWT (`auth.py`) and keeps users, saved leagues and subscriptions in SQLAlchemy (`db.py`): SQLite locally, Postgres in production. ESPN cookies are stored encrypted (`crypto.py`).
- **Billing:** Stripe Checkout for a monthly plan or a season pass, Customer Portal for management, and a signed webhook (`billing.py`) as the only thing that grants or removes access.
- **Local dev:** with no env vars auth is off and everything is open as a local admin user. `GRIDSIM_ENV=production` forces auth on and refuses to start if config is missing.
- **Deploy and setup:** `Dockerfile`, `fly.toml`, `.env.example` and the step-by-step checklist in `docs/LAUNCH.md`. `static/legal/` holds draft Terms and Privacy pages that need a lawyer's review.
- **Tests:** `python -m pytest -q tests --ignore=tests/ui` (backend, mocked Stripe and Supabase); the Playwright suites are `tests/ui/test_smoke.py` (server on :8000 with mocks) and `tests/ui/test_account.py` (starts its own auth-on server).

## Game conditions
Wind and roof adjust the simulation (`engine.env_factors`; fitted to 2025-26 play-by-play and shrunk: about -0.4 points of total per mph of
wind above 5, indoors about +0.5). Temperature, weekday and kickoff slot showed no usable signal. Forecasts come from Open-Meteo
(`weather.py`, free, no key) for outdoor games; if unavailable the sim uses roof only. The matchup sidebar can override conditions manually.


## Validation suite (audit branch)

`sh validate/run_all.sh` runs the leak-free walk-forward backtest for 2023-2025 (model rebuilt each week from earlier data only), writes league-level and game-level reports to `validate/out/`, evaluates the Vegas/Elo blends, checks game-script realism and runs the engine invariant tests (`pytest -q tests`). See `AUDIT.md` for findings, `validate.grid` for knob experiments and `validate.players` for the player-level (fantasy) check.


## One simple view, with detail on demand
GridSim has a single interface built for first-time users, and every page uses it: Home (this week's matchup and the one thing to do
next), My lineup (suggested swaps, Start or sit, best lineup for winning), Add / trade (add a free agent, or find a trade), Players
(Rankings, Risers and fallers, Best stacks), Games, League (matchups, power rankings, season outlook), Injuries (tell GridSim who is
out), What if (try adding or dropping players) and Game simulator (pick two teams, see win chances and players to watch). Every team has
its logo and every player a headshot. Projections are "projected points" with a likely range.

The advanced material opens in place, without switching the interface:
- **Trade suggestion, See full analysis** (or **Build my own trade**) opens the full Trade builder (both rosters, before/after, lineup changes) in a side sheet over the Add / trade page.
- **Trade ideas are ranked by what they do to your playoff and title odds.** The finder first screens trades by best-lineup points (rest of season, replacement-level free agents as the baseline), then re-scores the best candidates over simulated seasons with and without the trade, using the same random games both ways so small effects are not lost in noise. Odds follow your league's settings (playoff teams, byes, one or two weeks per round) and each team scores around its best lineup with every player's own simulated spread. Settings → Sims per game sets the seasons per idea (Fast, Standard and Deep run 1,000, 2,000 and 10,000); **Deep** also looks at more players per side and 2-for-2 deals. Asking for a second player who adds nothing to your lineup is dropped, and ideas that differ only in which bench player you add are collapsed. Only trades the other manager has a real reason to accept are listed; lopsided ones (they lose far more than they gain) are kept as long shots behind a **Show them** button, and when no realistic trade exists the page says so instead of padding the list. Odds are not shown in "This week" mode or once the regular season is over.
  - **Mid-week it still works.** Once games have started, a trade cannot move points a player already scored, and ESPN locks a player at kickoff. So for the current week each team keeps the starters whose games have begun (their banked points stay put, even if one is traded away) and fills open slots only from players who have not played; a player whose game has started counts for the weeks still to play, not this one. A player in a final game also has no spread left, which tightens that week in the odds. Finished games no longer inflate or hide trades, and the list is cleared when game states change so it is never priced on old scores.
- **Player panel, Show advanced stats** shows the full distribution, boom/bust, prop calculator and game script; **Back to summary** returns.
- **Players, More stats** adds every statistic column, the column picker and CSV export.
- **Games, Matchup details** (per game) shows market vs sim lines, the margin distribution and weather; **More options** adds sort, table view and the weather toggle.
- **My lineup, Full stats** opens every roster stat in a side sheet; **Game simulator, More detail** holds the betting-line comparison, score distributions, team stats, correlation grid and game-script splits.
- There is no separate "tools" menu or advanced mode: the old Lineup, Waivers, Trades, Sandbox and Trade analyzer pages were folded into My lineup, Add / trade and What if.

Logos load from ESPN's image server and headshots from the NFL's (with an ESPN fallback). They load in your browser, not on the server. If an
image is blocked or missing, the team abbreviation or the player's initials show instead. `server.py` only adds display metadata
(`fn`, `hs`, `eid`) to slate rows; no model or simulation code changed.

## UI (branch `ui/redesign`)
Sidebar/rail/tab-bar shell, one simple view on every page with detail on demand, Home dashboard, Settings, light/dark themes, Ctrl/Cmd+K palette, URL-state routing. Design tokens and component guide: `docs/ui/design-system.md`; findings and before/after: `UI_AUDIT.md`. Checks: `python tests/ui/contrast.py`, `python tests/ui/axe_run.py`, `python -m pytest -q tests/ui` (server on :8000; start it with `GRIDSIM_ESPN_MOCK=/tmp/espn_mock.json` from `tests/ui/make_espn_mock.py` for the league tests).
