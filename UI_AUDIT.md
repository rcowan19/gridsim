# GridSim UI audit and redesign log (branch `ui/redesign`, based on `audit/model-v2`)

Scope: presentation, navigation, interaction only. No model/sim changes. Stack stays **vanilla JS in one static `index.html`** (190 KB, no build step); no framework swap (that would be a rewrite, which you asked me to ask about first). Screenshots: `docs/ui/before/` (1440 / 820 / 390 px, every page and subtab, from `tests/ui/shots.py`).

## Phase 0: current UI

**Map (before).** Top bar: brand + 5 nav buttons (Games, Players, My Team, Injuries, Game Lab) + a global control strip (Week, PPR/Half/Std, Depth, Re-simulate, Update data, stamp). Subtabs: Players {Rankings, Trends, Stacks, Trade analyzer}; My Team {Lineup, Waivers, Trades, What-if, League, Sandbox}; Game Lab {Overview, Fantasy, Matchup, Play-by-play}. Route state = `#page` only (no sub-tab, week, filter, player). Drawer for player detail.

**Click counts (before; clicks from cold load to the answer).**

| task | clicks | notes |
|---|---|---|
| Read a game's sim result | 0-1 | Games page auto-sims; "Full matchup analysis" opens Lab |
| Run one custom matchup | 4-6 | Game Lab, 2 selects (2-4 clicks), Simulate |
| Change sim count and read distribution | 3 | Depth select is global and re-runs the whole slate; no per-game N, no standard error shown |
| My team's projections | 2-3 | My Team, Load league (ID is prefilled, still needs a click), Lineup |
| Find a trade / waiver add-drop | 2 (+load) | buried as subtabs 2 and 3 under My Team |
| League rankings and playoff odds | 2 (+load) | subtab 5 under My Team |
| Change settings (scoring, depth, league) | 1-2 | scattered: scoring+depth in header, league ID inside My Team, no theme/units anywhere |

**Problems found (screenshots + code).**
1. Header does too much: nav and 7 controls share two rows; on phone the nav is cut off ("Game Lab" clipped) and scrolls sideways.
2. My Team is a junk drawer: Waivers, Trades, League are primary jobs but sit as 3rd-level tabs beside Sandbox/What-if.
3. No home/dashboard; first screen is a 15-card wall (~2,600 px on desktop, ~9,600 px on phone).
4. Route state is just `#page`: sub-tab, week, filters, selected player are lost on reload and unshareable.
5. No command palette, no keyboard shortcuts, no "?" help.
6. No dark theme; legacy dark tokens (`.__x`) are dead code with an unrelated teal palette.
7. Settings scattered; League connection state not visible outside My Team; no last-run timestamp/sim count beside the Run action.
8. Empty states exist but are plain text; no skeletons (only a spinner); errors are inline text, no toasts, no retry.
9. Wide player table overflows inside its card on phone with no pinned name column; touch targets (pills, seg buttons) are ~28-32 px.
10. Jargon without tooltips (VOR, boom/bust, SD, OPPS, TD%, "lean", "blended", ESS); chart marks rely on color alone in the team histograms.
11. Contrast: `--dim` (#6e6657 on #f2eee4) measured below; muted/dim text on `--surface-2` to be verified programmatically in Phase 2.
12. Inconsistent controls: native selects/checkboxes (blue default checkbox) beside custom segs and pills.

What already works and is kept: warm paper/ink palette and token scale, tabular numerals, inline 10-90 range bars in the player table, probability bars, histograms with the margin line, accessible subtab buttons, drawer, "show more" pagination.

Not testable here: My Team / Waivers / Trades / League screens need a live ESPN league; ESPN is unreachable from this sandbox, so those screens are verified with a fixture or from the empty state only (see limitations).

## Phase 1: research (sub-agent, web). Items marked (known) came from background knowledge; the agent could not open every site.

Ranked patterns to adopt:

| # | pattern | why | source |
|---|---|---|---|
| 1 | Inverted pyramid per screen: answer, then distribution, then table | professional feel, scannable | 50+1 forecast design post: blog.fiftyplusone.news/p/how-501-designed-our-2026-election |
| 2 | Left sidebar (grouped), icon rail on tablet, bottom bar (4 + More) on phone | 7-9 sections; one-tap reach | linear.app/now/how-we-redesigned-the-linear-ui; M3 navigation bar (known) |
| 3 | Cmd/Ctrl+K palette plus a visible "Search" button | speed for power users, discoverable for others | ARIA combobox pattern (known) |
| 4 | Real `<table>`, sticky header, pinned name column, `aria-sort`, presets | dense tables stay usable | ninjatables.com/sticky-headers-vs-fixed-columns |
| 5 | Wide stat tables scroll with pinned name; list-like ones become cards on phone | no clipping | 50+1 post |
| 6 | Show sim outcomes as discrete bins/quantiles with plain-language annotations | honest uncertainty | 50+1 post |
| 7 | Floor/median/ceiling range bar in every projection row | compact quantile band | known |
| 8 | Probability bar + number; phrase as "about 6 in 10 sims" | reduces misreading | arxiv.org/pdf/2011.07586 |
| 9 | Warm theme from a few variables; dark theme derived (warm near-black, not #000) | one-place restyle | linear.app post |
| 10 | Okabe-Ito categorical palette; never color alone (sign/arrow/label) | colorblind-safe | jfly.uni-koeln.de/color (known) |
| 11 | Designed empty/loading/error states with a next action; skeletons that match layout | no blank screens | carbondesignsystem.com/patterns/empty-states-pattern |
| 12 | WCAG 2.2 AA: 24 px min target (44 on mobile primary), focus not obscured by sticky headers, 4.5:1 text / 3:1 marks | accessibility | w3.org/WAI/WCAG22/Understanding/target-size-minimum.html |

Pitfalls to avoid: sticky header covering focus (use scroll-padding), >5 items in a bottom bar, palette as the only way to reach things, smooth density curves with no sample count, red/green-only meaning, tan-on-cream low contrast, tooltip-only data on touch.

## Phase 3: information architecture (decided, no ask: one clear structure)

Sidebar groups (all reachable in <=2 clicks; most in 1):

```
GridSim
 Home                         (new dashboard)
 SIMULATE   Games | Matchup lab
 PLAYERS    Projections | Injuries
 MY LEAGUE  Lineup | Waivers | Trades | League | What-if/Sandbox
 Settings                     (new: league, scoring, sims, theme, data)
Phone bottom bar: Home | Games | Players | My League | More(sheet: Lab, Injuries, Settings, search)
Context bar (all pages): week, scoring, sims, Run, data stamp.  Cmd/Ctrl+K palette.  "?" shortcuts.
```
URL: `#/<page>/<sub>?week=9&fmt=ppr&q=...` restored on load; back/forward work.

## Decisions log
- Stay vanilla (see top). Tokens already exist, so the design system is formalized in `docs/ui/design-system.md` rather than adopting Tailwind/shadcn.
- Use the existing `go(page, sub)` router; sidebar items map to (page, sub) pairs so no screen logic is rewritten.
