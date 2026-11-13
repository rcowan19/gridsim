# Betting backtest (raw sim vs posted lines)

Run: `python -m validate.wf --season 2026 -n 1000 --weeks 3 9 --tune '{"ot_both":1}'` (needs the week-9 results in `data/games.csv`, i.e. Update data), then `python -m validate.bets --week 9`.
Every game is simulated 1,000 times by a model rebuilt from earlier data only, with no Vegas blend. For each market the side with the higher expected value at the posted price is bet 1 unit when EV clears a threshold, then graded on the real result.

## Result: no edge

| sample | games | margin error, sim vs line | spread, EV>=3% | total, EV>=3% | moneyline, EV>=3% |
|---|---|---|---|---|---|
| 2023-25 weeks 3-18 | 720 | 10.62 vs 9.90 | -9.4% (+/-8.1) | -7.3% (+/-8.4) | -11.9% (+/-11.9) |
| 2026 weeks 3-9 | 104 | 9.40 vs 8.85 | -9.7% (+/-21.4) | -24.0% (+/-21.6) | -10.5% (+/-30.6) |
| 2026 week 9 | 15 | 11.61 vs 8.80 | -52.1% (+/-49.0) | -3.6% (+/-63.0) | -83.1% (+/-33.2) |

ROI per unit staked at the posted odds, 95% interval in brackets.

- The raw sim is less accurate than the line on margin, total and win probability (log loss) in every sample. Anchoring the sim to the no-vig market, the best weight on the sim is 0.0 for spread, total and moneyline (fit on 2023-25), so after anchoring it never finds a +EV price.
- Regressing actual margin on the line and the sim margin gives the sim a coefficient of -0.5 (t = -2.4) in 2023-25 and 0.1 (t = 0.2) in 2026: no information beyond the line.
- Week 9 was a chalk week: favorites went 14-1 straight up and 11-4 against the spread. The sim leans toward underdogs, so it lost. Betting every favorite that week would have made money; over 2023-25 the same rule is -0.5% on spreads.
- One week (15 games) cannot show an edge either way; the +/-49% interval is wider than any plausible edge.
