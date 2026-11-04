# GridSim audit log (branch `audit/model-v2`)

## Executive summary

**Verdict.** GridSim is a mechanically sound play-level simulator (9/9 invariants hold, determinism, league rates within a few %), but **as a predictor it has no edge over the betting market and loses to a 60-line Elo model in 2024-25.** Its disagreement with the closing line is anti-informative in all three held-out seasons (coef on sim-minus-spread: -0.85 / -0.12 / -0.78). The shipped blend (sim tilted toward the line, beta .9) is therefore ~ the market plus a realistic score distribution and player-level detail, not extra accuracy. Do not bet on its picks.

**Top 5 flaws found (all measured, not assumed)**
1. Scoring variance ~10% too narrow (no game-level shared uncertainty): points sd 8.9 vs 10.0; thin 0-point/low tails.
2. Home-field advantage ~half of real (sim margin +0.95 vs +2.0-2.1).
3. Ratings carry little information: Elo beats the sim's ratings in 2024-25 (Brier .206 vs .223; .227 vs .233).
4. Passing efficiency too high (completion % +3.4 pts, YPA +4.6%), plays +2-3/team, yards +2-6%.
5. Overtime: ties ~3x too frequent (1.3-1.5% vs 0-0.4%), OT rate 7% vs 4-6%. Plus: no betting-line fallback for unlined games.

**Top 5 improvements adopted (each kept only if held-out metrics held or improved)**
1. Latent per-game form shocks (`sig_cp/ypc/pace`): points sd ratio .88 -> 1.01, total sd .89 -> 1.00, margin sd .90 -> 1.02.
2. HFA terms x2.1 (`hfa_comp`, `hfa_ypc`): sim home margin +.95 -> +2.0 (real +2.0/+2.1).
3. Elo-anchored fallback blend for games with no line (beta .7): dev Brier .2293 raw -> .2224 (mean of 3 seasons).
4. Vegas beta .8 -> .9: blended Brier mean .2117 -> .2106 (beta 1.0 = .2097 is pure market; .9 keeps some sim shape).
5. Passing fix (`comp_add` 0, `air_tail` 1.25) + refit player calibration: held-out player MAE 5.23 vs 5.55 baseline, corr .54, 80%-interval coverage 81.8%.
Also: 2025 OT rule knob, `GRIDSIM_TUNE`, validation harness, one-command rerun.

**Headline before / after (walk-forward, weeks 3-18, 1000 sims/game, raw sim unless stated)**

| metric | 2023 before | 2023 after | 2024 before | 2024 after | 2025 before | 2025 after |
|---|---|---|---|---|---|---|
| Brier (raw) | .2334 | .2327 | .2219 | .2226 | .2352 | .2325 |
| Log loss (raw) | - | .6574 | - | .6365 | - | .6592 |
| Margin MAE (raw; market) | - | 10.72 (10.06) | - | 10.33 (9.55) | - | 10.80 (10.08) |
| Brier, no-line fallback (Elo-tilt) | .2334 | .2318 | .2219 | .2106 | .2352 | .2249 |
| Brier, market / Elo (ref.) | .2161 / .2330 | same | .1971 / .2062 | same | .2147 / .2267 | same |
| Score-distribution Wasserstein (pts) | .87 | .48 | 1.11 | 1.06 | 1.05 | .67 |
| KS | .037 | .028 | .062 | .062 | .042 | .048 |
| sd of team points (real 10.2 / 9.9 / 10.0) | 9.0 | 10.2 | 9.0 | 10.1 | 8.9 | 10.1 |
| sim home margin (real 3.2 / 2.0 / 2.1) | 1.0 | 2.0 | .9 | 2.0 | 1.0 | 2.0 |
| Player 80% interval coverage (held out, wk>5) | - | - | - | - | - | 81.8% |

Honest reading: the raw Brier barely moved (the sim's team ratings, not its variance or HFA, limit it). The gains are in distributional realism (variance, HFA, tails), the no-line fallback and player intervals.

## Experiment log (tuned on 2023-24, confirmed on 2025; tune never touched 2025)

| change | result | decision |
|---|---|---|
| `hfa_comp/hfa_ypc` x2.1 | home margin matches real; Brier flat/slightly better | **adopted** |
| latent shocks sig_cp .05, sig_ypc .5, sig_pace .15 | variance ratios ~1.0; Brier flat | **adopted** |
| sig_pass (pass-rate shock) | no gain | reverted (0) |
| sig_pace .15 -> .08 (dev, 300 sims) | Brier .2264 vs .2266, total-sd ratio .94 vs 1.01 | **kept .15** (no held-out gain) |
| `rg` rating gain 1.5 / 2.0 | Brier .2288 -> .2282 / .2304 | kept 1.0 (noise) |
| yards-per-completion rating | no gain | reverted |
| finer Q4/down pass-rate residual states | no gain | reverted |
| position-specific shrinkage | no gain | not adopted |
| `ot_both` (2025 rules) | OT correct from 2025; ties still ~3x | adopted for 2025+ |
| `comp_add` 0, `air_tail` 1.25 | completion gap +3.4 -> ~0.2 pts (dev) | **adopted** |
| Vegas beta .8 -> .9 | blended Brier -.001 | **adopted** |
| Elo-tilt fallback beta .7 | -.007 vs raw (higher beta overfits sim noise less than it helps? .9: .2218 vs .7: .2224, ~equal) | adopted .7 (conservative) |

## Remaining weaknesses (prioritized)
1. **No edge vs market; ratings low-information.** Elo beats them. Fix would be better priors (QB-adjusted EPA, preseason/market win totals) - architectural, ask first.
2. **Plays/team +2-3 and yards +2-6%** (shared pace shock inflates plays sd to 11.5 vs 8.2 real; lowering it did not help held-out metrics). Points/yard slightly low, so scoring is only -0.4 to -1.0/team in 2024-25.
3. **OT ties 3x too frequent** (1.3-1.5% vs ~0.4%).
4. **Q4 leader/trailer pass-rate gap** (leaders pass too much) undiagnosed (`out/gamestate.md`).
5. QB over-projection (+0.8 raw), explosive plays ~6% low, penalties -0.4 to -0.9/team.
6. Walk-forward leak: roster team = end-of-season team for traded players; late scratches uncatchable.
7. Real ESPN league never tested from the sandbox (ESPN unreachable).

## Re-run everything
```
sh validate/run_all.sh        # 2023-25 walk-forward + reports + blend eval + game-state + pytest (~12 min with warm cache, 2 cores)
python -m validate.grid 'cur={}' 'x={"sig_pace":0.08}' --seasons 2023 2024 -n 300   # knob experiments
python -m validate.players validate/out/bt_players_ca0p.csv                          # player-level check
```
Outputs: `validate/out/report_<season>.md`, `gamestate.md`.

## Honest limitations
- 3 seasons x ~240 games: Brier SE ~.006, so differences under ~.01 are noise; the market gap (+.018 to +.026) is the only large effect.
- Market comparison uses closing spreads mapped through N(0,13.5), not true probabilities.
- Player validation is one season-slice (~1,250 player-games), calibration trained on weeks <=5.
- League baselines are computed from nflverse pbp, not external sources.
- The model has **no demonstrated edge**; "blend ~ market" is the accurate description.

---

Status: in progress. Baseline = `main` @ fbd10d7 + injury tab (00fd6fc). All validation runs are seeded and pinned to `data/` snapshots of 2026-11-04.

## Phase 0: architecture map

| layer | file | what it does |
|---|---|---|
| data | `fetch_data.py` | nflverse pbp 2025/2026, rosters, snap counts, injuries, depth charts, `games.csv` (lines, roof, wind) |
| model build | `build_model.py` (684 l) | recency-weighted (0.85/wk, prev season x0.35) empirical tables and pools -> `model.json`: pass-call table (down x dist x zone), field-zone completion tables, air/YAC pools, run pools (open field / short / goal line), sack/INT/fumble/penalty rates, 4th-down table, clock pools, FG table, punt/KO pools, team ratings (shrunk), player usage (target/carry share, cpoe, adot, YAC, goal-line), injury/depth-chart availability |
| engine | `engine.py` (822 l) | play-by-play state machine: kickoff -> drives -> play call (pass prob = table logit + team shift + score/time residual) -> outcome draws -> clock -> scoring; kneels, end-of-half FG, 2-pt rule, OT, penalties, safeties, return TDs |
| sim | `sim.py` | slate runner (multiprocess chunks), `team_draw` (Questionable players sit with prob 1-p_play), Vegas exponential tilting (`vegas.py`, BETA 0.8), ESPN K/DST scoring, per-player rows |
| calibration | `calibrate.py`, `tune.py`, `fit_calib.py`, `calib.py` | league-level knob tuning (coordinate descent on 2026-weighted league stats), player-projection shrink/blend (`calib.json`) |
| app | `server.py`, `static/index.html`, `espn.py` | FastAPI + single-page UI, ESPN league import |

Game flow: kickoff (TD prob `ko_td`, return pool) -> `drive()` loop: kneel check, end-of-half FG, 4th-down choice (table x `go_mult`, late-game rules), pre-snap penalty draw, dropback or rush, live-ball penalty, until possession changes or clock expires. Half 1 ends at 1800 s, OT is 10 min with `[2,2]` timeouts.

## Constants and provenance (summary; full list is in `Engine.DEFAULT_TUNE`, `build_model.py` head, `vegas.py`, `calib.json`)

| constant(s) | value | source |
|---|---|---|
| `RECENCY` 0.85, `W_PREV` 0.35, `USAGE_DECAY` 0.7, `W_PREV_USE` 0.03 | | hand-picked, partly swept in `backtest.py` on 2026 wks 3-8 |
| shrink k: sack 120, scramble 150, cpoe_def 250, int_def 400, ypc 200, pace 250, QB cpoe 150, QB int 300, receiver adot 40/cpoe 80 (x0.5)/yac 30 | | guessed; never validated out of sample |
| `comp_add` .02, `run_add` .1, `gl_add` .4, `air_add` .8, `pace_mult` .985, `go_mult` 1.1, `pen_scale` 1, `yac_scale` 1 | | `tune.py` coordinate descent against 2026 league stats (**in-sample**) |
| `hfa_comp` .006, `hfa_ypc` .06 | | hand-set; HFA is only these two terms |
| `qb_y1` 2.55, `qb_y3` 1.35, `qb_rz` 1.7 | | fitted on 2024-26 goal-line carries |
| `punt_td` .0055, `fum_td` .035, `eoh_fg` .9 | | hand-set from pbp counts |
| Vegas `BETA` 0.8 | | tuned on 2026 wks 3-8 (**in-sample**) |
| `calib.json` mu 6, lam .75, A .3, k .5, s ~1.5 | | fit on 2026 wks 3-8 backtest players (train/test split by week, then refit on all) |
| env: wind -0.4 pt/mph above 5, roof +0.5 | | pbp regression, shrunk |
| p_play by practice status .46/.67/.91 | | 2026 injury reports vs. who played |

Data freshness: nflverse files refreshed 2026-11-04 (injuries week 9 practice reports only; no game statuses until Friday). Vegas lines exist only through week 9.

## Validation harness (`validate/`, `tests/`)

```
python -m validate.wf --season 2025 -n 1000        # leak-free walk-forward (~5 min on 2 cores); also 2023, 2024
python -m validate.report --season 2025            # part A league calibration + part B game-level accuracy -> validate/out/report_2025.md
pytest -q tests                                    # engine invariants (scores legal, clock monotone, stats reconcile, determinism, side symmetry)
```
Leak controls: model rebuilt for each week W from weeks < W and the previous season only; 2026-only inputs (injury reports, depth charts, snaps) are masked for earlier seasons; rosters are the same-season/prior-season files; model-build RNG reseeded per week. Known residual leak: roster team is the end-of-season team for mid-season trades.

## Phase 1 research (details: `research_phase1.md`)
Verified from sources: FG% ~85% (50+ yd 71.7% in 2024), 2024 4th-down go rates, HFA ~1.7-2.0 pts and falling ~0.03/yr, 2025 OT rule (both teams get a possession, then sudden death; ties still possible), WP-model dependent-sample pitfalls. League baselines (EPA, pass rate, sack/INT rates, etc.) the researcher could not verify online, so **all league baselines in this audit are computed directly from the nflverse pbp in `data/`** (`validate/report.py`), not quoted.
Ranked gaps of a typical play-level sim vs. the literature: (1) no correlated game-level latent factors, (2) weak hierarchical shrinkage/ratings, (3) score-state play calling, (4) possessions/clock calibration, (5) HFA, (6) 4th-down policy, (7) 2024-25 rules, (8) score-distribution tails, (9) validation vs. market, (10) fantasy usage layer.

## Baseline findings (raw sim, walk-forward, 3 held-out-style seasons; weeks 3-18; 240-ish games each; 1000 sims/game)

Game-level (Brier lower is better; market = closing spread via N(0,13.5)):

| season | coin | Elo | market | **GridSim raw** | GridSim - market (1 se ~ .006) | edge test: coef on (sim - spread) |
|---|---|---|---|---|---|---|
| 2023 | .2500 | .2330 | .2161 | .2334 | +.0173 | -0.79 (t -1.8) |
| 2024 | .2500 | .2062 | .1971 | .2219 | +.0248 | -0.21 (t -0.6) |
| 2025 | .2490 | .2267 | .2147 | .2352 | +.0206 | -0.87 (t -2.2) |

Verdict: **the raw sim has no edge over the market and does not beat a 60-line Elo model in 2024-25.** Its disagreement with the market is anti-informative in all three seasons (negative coefficients: when the sim differs from the line the line is the better guess). Any earlier "63-65% winners / Brier .223" numbers were on 2026 weeks 3-8 (~90 games, SE ~ .02), *after* Vegas blending and with knobs tuned on those same games, so they overstated the model.

League-level (sim vs real, averaged over 2023-25 where noted):

| check | real | sim | verdict |
|---|---|---|---|
| sd of points / team-game | 10.0 / 9.9 (2025/24) | 8.9 / 9.0 | **~10% too narrow** (also totals sd 13.7 vs 12.1, margin sd 14.4 vs 13.0) |
| home margin | +2.1 / +2.0 / (2023 +3.1) | +0.96 / +0.94 / +0.9 | **HFA ~1.2-2 pts too small** (applied via two small terms only) |
| completion % (excl. sacks) | 64.2 / 65.2 | 67.6 / 67.5 | **+2.3 to +3.4 pts too high** |
| yards per attempt | 6.99 (2025) | 7.31 | +4.6% |
| yards / team-game | 328 / 341 | 347 / 348 | +2 to +6% |
| plays / team-game | 60.6 / 61.8 | 62.5 / 63.2 | +1.4 to +1.9 |
| possessions / team-game | 10.73 | 10.73 | exact |
| TD / possession, FG make %, INT rate, punts / possession | .240, .854, .0206, .330 | .237, .858, .0203, .344 | good |
| points / team-game | 23.0 / 23.1 | 22.7 / 22.1 | within ~0.4-1.0 |
| OT rate, ties | 5.4-5.8%, 0-0.4% | 7.6-7.8%, 1.4% | **too many OT games; ~20% of sim OT games tie vs ~5-10% real** |
| scoring tails | 0 pts 1.5%, <=10 pts 13.5% | 0.4%, 10.3% | thin low tail (same cause as narrow variance) |

Invariant tests (`pytest -q tests`): 9/9 pass (legal score increments, clock monotone, down/distance valid, player stats reconcile, determinism, neutral-field side symmetry, ties only after OT). No correctness bug found so far in these classes.

Additional findings:
- Reproducibility bug (minor): `build_model.RNG` is module-global, so `model.json` depends on call order; harness reseeds it each build.
- Rating gain test: scaling every team-rating deviation by 1.5 changes dev-season Brier .2288 -> .2282 (2.0: .2304). Ratings are not over-shrunk, they are low-information.
