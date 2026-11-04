# GridSim design system

Single source of truth: the `:root` and `:root[data-theme="dark"]` token blocks at the top of `static/index.html`. Everything restyles from there. Check contrast after any token change: `python tests/ui/contrast.py` (exits non-zero on failure).

## Tokens
| group | tokens | notes |
|---|---|---|
| surfaces | `--bg --surface --surface-2 --line --line-soft --line-strong` | warm paper (light) / warm near-black (dark). `--line-strong` is the 3:1 control border |
| text | `--text --muted --dim` | all >= 4.5:1 on `--bg`, `--surface` (light 4.9-16, dark 6.1-14.7) |
| accent | `--accent --accent-ink` | leather brown; the only primary action color |
| semantic | `--good --bad --warn` + `-bg` tints | turf / brick / gold. Never the only signal: pair with sign, arrow or word |
| position | `--pos-qb --pos-rb --pos-wr --pos-te --pos-k --pos-dst` | text colors, >= 4.5:1 in both themes; JS reads them via `POSCOL` |
| chart | JS `C` (swapped per theme from `CPAL`), team colors via `readable()` | team colors are pushed to >= 4.5:1 on the current surface; two near-identical team colors fall back to neutral (`pairCols`) |
| spacing | `--s1..--s6` = 4, 8, 12, 16, 24, 32 px | |
| radii | `--r-sm` 6, `--r-lg` 10, `--pill` | |
| type | UI: system sans 14/1.5; headings: Palatino/Georgia serif; numerals: `font-variant-numeric: tabular-nums` in tables and tiles; code/kbd: mono | |
| layout | `--side-w` 232, `--rail-w` 64, `--ctx-h` 56 | breakpoints: 1100 (rail), 760 (bottom tab bar) |

Categorical chart colors come from team colors (data-driven) and position hues; never use more than 2 team colors in one chart.

## Components (class -> use)
`.btn` (+`.primary` `.ghost` `.sm`), `.seg` (segmented control), `.pill`/`.chips` (filters), `.subtabs` (in-page tabs), `.card` (+`.empty`, `.empty.err`), `.tile` (StatTile: `.tl` label, `.tv` value, `.ts` sub), `.rowlink` (list row that navigates), `.pbar` (progress bar), `.skel` (skeleton), `.toast` via `toast(msg,{err,action})`, command palette via `openPalette()`, tooltip via `data-tip="..."` (hover and keyboard focus), `renderTable(host,id,cols,rows,{tools,limit,step,onRow})` (sortable, `aria-sort`, sticky header and pinned name column when `tools`, column chooser, CSV), `hist()` + `qMarks()` (histogram with P10/P50/P90), `rangeBar()` (floor/median/ceiling bar), `.wbar` (probability bar).

## Number formatting (use the helpers, never `toFixed` inline)
`fmt(x, d=1)` fantasy points and scores 1 dp; `pct(x, d=0)` probabilities as whole % (win % 1 dp only in the matchup header); `sgn(x)` signed deltas with a true minus; spreads as `TEAM -3.5`; ranges as `lo to hi` with the sentence "middle 80% of simulated outcomes"; missing = en dash. No value shows more than 1 decimal.

## Uncertainty rules
Never a bare point estimate where a distribution exists: show range or probability, label what it means (80% interval, "X in 100 sims"), show sim count and the standard error where a probability is the headline.

## Accessibility rules
Visible `:focus-visible` ring; `scroll-padding-top` for the sticky bar; targets >= 44 px on phone (24 px minimum elsewhere); every control labelled; `aria-current="page"` on nav; `aria-sort` on sortable headers; toasts in a polite live region; `prefers-reduced-motion` disables animation; run `python tests/ui/axe_run.py` (must report 0).

## How to add a screen
1. Add `<section id="pg-NAME" class="page hide"><div id="tab-NAME"></div></section>` and append `'NAME'` to `PAGES`.
2. Add an entry to `NAV` (and `TITLE`) so it appears in the sidebar, palette and shortcuts; give sub-tabs a `data-sg` group if needed.
3. In `go()`, call `renderNAME()` (use `ensureSlate` if it needs simulation data). Render skeleton first, then content; handle empty and error states with `.card.empty` (always include a next action).
4. Reflect any filter in `syncURL()` / `applyRoute()` so the view is shareable.
5. Run `contrast.py`, `axe_run.py`, `pytest tests/ui`, then take light/dark screenshots at 1440/820/390 (`tests/ui/shots.py`).

## How to add a component
Style with tokens only (no hex literals); give it a hover, focus-visible and disabled state; check it in dark; keep touch height >= 44 px under 760 px.
