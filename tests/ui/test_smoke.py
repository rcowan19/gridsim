"""Playwright smoke tests (server must be running on :8000):  python -m pytest -q tests/ui/test_smoke.py"""
import json, re, pytest
from playwright.sync_api import sync_playwright
URL = "http://localhost:8000/"

@pytest.fixture(scope="module")
def br():
    with sync_playwright() as p:
        b = p.chromium.launch(); yield b; b.close()

def page(br, hash_="", w=1440, h=900, mode=None, league=False, block_images=False):
    ctx = br.new_context(viewport={"width": w, "height": h}); pg = ctx.new_page(); pg.errs = []
    pg.on("pageerror", lambda e: pg.errs.append(str(e)))
    if block_images: ctx.route(re.compile(r"(espncdn\.com|nfl\.com)"), lambda r: r.abort())
    pg.add_init_script("localStorage.setItem('gs_league','1781890288');" if league else "void 0;")
    pg.goto(URL + hash_); pg.wait_for_function("typeof st!=='undefined' && st.sched", timeout=20000)
    if league: pg.wait_for_function("st.lg && st.slate", timeout=120000)
    pg.wait_for_function("!document.body.innerText.includes('Simulating')", timeout=90000)
    return pg

def settle_live(pg):
    """Tests that mark games as played themselves: wait for the page's own live-score fetch to finish, then stop it, so it cannot overwrite them."""
    pg.wait_for_load_state("networkidle"); pg.evaluate("loadLive = async () => {}")

NAV = [("home", "#/home"), ("mylineup", "#/mylineup"), ("moves", "#/moves"), ("proj", "#/players/rank"), ("games", "#/games"), ("league", "#/league"), ("inj", "#/inj"), ("whatif", "#/whatif"), ("lab", "#/lab/ov"), ("settings", "#/settings")]

def test_nav_every_page(br):
    pg = page(br)
    for nid, hs in NAV:
        pg.click(f"#side [data-nav={nid}]"); pg.wait_for_timeout(250)
        assert pg.evaluate("location.hash").startswith(hs), nid
        assert pg.locator(f"#side [data-nav={nid}][aria-current=page]").count() == 1, nid
    assert not pg.errs

def test_url_state_restores(br):
    pg = page(br, "#/players/rank?week=8&pos=QB&q=mah&fmt=half")
    assert pg.evaluate("[st.week, st.pos, st.q, st.fmt]") == [8, "QB", "mah", "half"]
    assert pg.locator("#s_body tbody tr").count() >= 1

def test_back_forward(br):
    pg = page(br, "#/home"); pg.click("#side [data-nav=games]"); pg.click("#side [data-nav=settings]")
    pg.go_back(); assert "#/games" in pg.evaluate("location.hash"); pg.go_forward(); assert "#/settings" in pg.evaluate("location.hash")

def test_palette(br):
    pg = page(br); pg.keyboard.press("Control+k"); pg.keyboard.type("settings"); pg.keyboard.press("Enter"); pg.wait_for_timeout(300)
    assert pg.evaluate("st.page") == "settings"
    pg.keyboard.press("g"); pg.keyboard.press("p"); pg.wait_for_timeout(300); assert pg.evaluate("st.page") == "players"
    pg.keyboard.press("?"); assert pg.locator("[aria-label='Keyboard shortcuts']").count() == 1

def test_table_sort_filter_columns_csv(br):
    pg = page(br, "#/players/rank"); pg.check("#rkMore"); pg.wait_for_timeout(300)
    first = pg.inner_text("#rkTable tbody tr:first-child td[data-k=name]")
    pg.click("th[data-k=boom]"); assert pg.get_attribute("th[data-k=boom]", "aria-sort") == "descending"
    pg.fill("#s_q", "mahomes"); pg.wait_for_timeout(300); assert pg.locator("#rkTable tbody tr").count() >= 1 and "Mahomes" in pg.inner_text("#rkTable tbody")
    pg.fill("#s_q", "zzzzqq"); pg.wait_for_timeout(300); assert "No rows match" in pg.inner_text("#rkTable")
    pg.fill("#s_q", ""); pg.click("#s_pos [data-p=QB]"); pg.wait_for_timeout(300)
    assert all("QB" in t for t in pg.locator("#rkTable tbody td[data-k=name]").all_inner_texts()[:5])
    pg.click(".colmenu summary"); pg.click("input[data-col=sd]"); assert pg.locator("th[data-k=sd]").count() == 0
    with pg.expect_download() as d: pg.click("[data-csv]")
    assert d.value.suggested_filename.endswith(".csv"); assert not pg.errs

def test_run_lab_sim(br):
    pg = page(br, "#/lab/ov"); pg.click("#runBtn"); pg.wait_for_selector("#gl-ov .simhero", timeout=90000)
    t = pg.inner_text("#gl-ov").lower(); assert "chance to win" in t and "players to watch" in t and "simulated games" in t
    assert "P50" in pg.inner_html("#gl-ov")
    assert pg.locator("#gl-ov .simhero .tlg").count() == 2 and pg.locator("#gl-ov .rowc .hs").count() >= 4
    pg.click("#gl-ov details.ezmore summary >> nth=0"); assert pg.locator("#gl-ov table.st:visible").count() >= 1; assert not pg.errs

def test_theme_and_settings_persist(br):
    pg = page(br, "#/settings"); pg.click("#setTheme [data-v=dark]"); assert pg.evaluate("document.documentElement.dataset.theme") == "dark"
    pg.reload(); pg.wait_for_function("typeof st!=='undefined'"); assert pg.evaluate("document.documentElement.dataset.theme") == "dark"
    pg.evaluate("localStorage.removeItem('gs_theme')")

def test_phone_no_hscroll_and_tabbar(br):
    pg = page(br, "#/players/rank", 390, 844)
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    assert pg.locator("#tabbar").is_visible() and not pg.locator("#side").is_visible()
    for el in pg.locator("#tabbar button").all(): assert el.bounding_box()["height"] >= 44
    pg.click("#tabbar [data-nav=games]"); assert pg.evaluate("st.page") == "games"

def test_error_state_retry(br):
    pg = page(br, "#/games"); pg.wait_for_selector(".gcard", timeout=90000); pg.route("**/api/slate", lambda r: r.abort()); pg.keyboard.press("r"); pg.wait_for_selector("#retrySl", timeout=15000)
    assert "Could not reach" in pg.inner_text("#nflBody"); pg.unroute("**/api/slate"); pg.click("#retrySl"); pg.wait_for_selector(".gcard", timeout=90000)


# ---------- one experience: Easy pages, detail on demand, tools under "More tools" ----------
def visible_nav(pg): return pg.eval_on_selector_all("#side [data-nav]", "b=>b.filter(x=>x.offsetParent).map(x=>x.dataset.nav)")

def test_nav_is_one_flat_list(br):
    pg = page(br, "#/home")
    assert visible_nav(pg) == [n for n, _ in NAV]
    assert pg.locator(".adv-only:visible").count() == 0 and pg.locator("#modeBtn").count() == 0 and pg.locator("#toolsBtn").count() == 0
    assert not pg.errs

def test_connect_card_then_lineup_and_images(br):
    pg = page(br, "#/home", block_images=True)
    assert pg.locator("#ezBtn").count() == 1 and "Connect my league" in pg.inner_text("#tab-home")
    pg.fill("#ezId", "1781890288"); pg.click("#ezBtn"); pg.wait_for_function("st.lg && st.slate", timeout=120000); pg.wait_for_selector(".hero", timeout=20000)
    assert "chance to win" in pg.inner_text("#tab-home")
    pg.click("#side [data-nav=mylineup]"); pg.wait_for_selector("#tab-mylineup .slotl", timeout=20000)
    assert pg.locator("#tab-mylineup .slotl").count() >= 8 and pg.locator("#tab-mylineup .hs").count() >= 8
    pg.wait_for_timeout(800); assert pg.locator("#tab-mylineup .hs img").count() == 0       # blocked image servers: letters stay, no broken <img>
    assert pg.locator("#tab-mylineup .hs b").first.is_visible()
    pg.click("#tab-mylineup .rowc >> nth=0"); pg.wait_for_selector("#dpanel .ezd-nm")
    assert pg.locator("#dpanel .ezd-hero").is_visible() and pg.locator("#dpanel .kpis").count() == 0
    pg.click("#ezAdv"); pg.wait_for_selector("#dpanel .kpis")                                  # advanced stats open in place
    assert pg.evaluate("[st.page, st.mode]") == ["mylineup", "easy"]
    pg.click("#ezBack"); pg.wait_for_selector("#dpanel .ezd-hero"); assert not pg.errs

def test_trade_ideas_use_season_odds_and_deep_searches_wider(br):
    pg = page(br, "#/moves", league=True)
    pg.click(".choice[data-mv=trade]"); pg.wait_for_selector("#mvFind"); pg.click("#mvFind")
    pg.wait_for_selector("#mvBody .tcard", timeout=180000)
    assert re.search(r"Playoffs \d+(\.\d)?% → \d+(\.\d)?% · Title \d+(\.\d)?% → \d+(\.\d)?%", pg.inner_text("#mvBody"))
    pg.click("[data-sug='0']"); pg.wait_for_selector("#sheet:not(.hide) #trEval table.st", timeout=60000)
    assert "SEASON ODDS" in pg.inner_text("#sheet #trEval")
    chk = pg.evaluate("""(() => { const c = st.trc, x = st.tr.px, O = x.odds, me = c.tms[c.L.my], tid = st.tr.partner, th = c.tms[tid], s = a => Array.from(a).reduce((p, q) => p + q, 0);
      const z = trOdds(c, x, {m2: me.ids, t2: th.ids}, tid);                      // a trade that changes nothing
      return {N: O.N, pt: O.pt, byes: O.byes, ti: s(O.base.ti), po: s(O.base.po), by: s(O.base.by), none: [z.me.ti[1] - z.me.ti[0], z.me.po[1] - z.me.po[0], z.th.ti[1] - z.th.ti[0], z.me.ew[1] - z.me.ew[0]]}; })()""")
    assert chk["N"] == 2000 and abs(chk["ti"] - 1) < 1e-9 and abs(chk["po"] - chk["pt"]) < 1e-9 and abs(chk["by"] - chk["byes"]) < 1e-9   # one champion per season
    assert chk["none"] == [0, 0, 0, 0]                                           # same random games before and after, so no change means no difference
    pg.keyboard.press("Escape")
    # Deep: 10,000 seasons, wider search (2-for-2 deals), still no padded asks or near-duplicate ideas
    pg.evaluate("st.depth = 'deep'; st.tr.px = null; st.tr.sugg = null")
    pg.click("#mvFind"); pg.wait_for_selector("#mvBody .tcard", timeout=180000)
    d = pg.evaluate("""(() => { const sg = st.tr.sugg, seen = new Set(); let dup = 0;
      sg.forEach(s => { const k = s.t.id + '|' + s.get.slice().sort().join() + '|' + Math.round(s.e.myGain * 10) + '|' + Math.round(trKey(s.o) * 100); if (seen.has(k)) dup++; seen.add(k); });
      return {N: st.tr.px.odds.N, n: sg.length, allOdds: sg.every(s => s.o && s.o.N === 10000), dup}; })()""")
    assert d["N"] == 10000 and d["n"] >= 4 and d["allOdds"] and d["dup"] == 0
    pg.evaluate("st.depth = 'std'; st.tr.px = null; st.tr.sugg = null")
    assert not pg.errs

def test_trade_values_ignore_points_already_scored_this_week(br):
    # Mid-week, a trade cannot move points a player has already scored, and a player whose game has started cannot be slotted in.
    # So the points banked by started players must not change what any trade is worth (they used to: the finder counted them as a gain).
    pg = page(br, "#/moves?week=9", league=True)
    settle_live(pg)
    r = pg.evaluate("""(async () => {
      const k = leagueCtx(), c = {D: k.D, L: k.L, tms: k.tms}, rows = [...(st.slate.rows_idle || []), ...st.slate.rows];
      const done = new Set([...new Set(st.slate.games.map(g => g.id))].sort().filter((_, i) => i % 2 === 0));       // half the games are final
      const mark = bump => { rows.forEach(r => { delete r._act; delete r._left; delete r._gs; if (done.has(r.game)) { r._gs = 'post'; r._left = 0; r._act = P0(r, 'proj') * 1.6 + 3 + bump; } }); st.tr.px = null; };
      const gains = async bump => { mark(bump); const x = await trPrep(c), me = c.tms[c.L.my], top = (ids, n) => ids.filter(i => x.row(i) && x.TP.has(x.row(i).pos)).sort((a, b) => x.vor(b) - x.vor(a)).slice(0, n), out = [];
        c.D.teams.filter(t => t.id !== c.L.my).slice(0, 4).forEach(t => top(me.ids, 3).forEach(g => top(c.tms[t.id].ids, 3).forEach(q => { const e = trEval(c, [g], [q], t.id); out.push([g + '>' + q + '@' + t.id, e.myGain, e.theirGain, x.vor(g), x.vor(q)]); })));
        return out; };
      const a = await gains(0), b = await gains(50), started = rows.filter(r => r._gs).length;
      return {started, n: a.length, drift: Math.max(...a.map((v, i) => Math.max(Math.abs(v[1] - b[i][1]), Math.abs(v[2] - b[i][2]), Math.abs(v[3] - b[i][3]), Math.abs(v[4] - b[i][4])))), moved: a.some(v => Math.abs(v[1]) > .5)};
    })()""")
    assert r["started"] > 50 and r["n"] >= 20 and r["moved"], r
    assert r["drift"] < 1e-6, r                                   # +50 banked points on every started player changes nothing about any trade
    assert "weeks still to play" in pg.evaluate("(st.live.n = 1, st.live.gs = {a: {s: 'post'}}, trLiveNote())")      # the Moves page says how started games are handled
    assert not pg.errs

def test_what_if_keeps_the_lineup_you_played_once_games_start(br):
    # Once games have started the lineup is what you set at kickoff. The page used to rebuild "best lineup" from what everyone scored,
    # so a bench player who outscored a starter showed up in his slot (a starter ended up "on the bench"), and added or dropped players
    # who had already played moved the totals.
    pg = page(br, "#/whatif?week=9", league=True); settle_live(pg)
    r = pg.evaluate("""(() => {
      const k = leagueCtx(), me = k.me, rows = [...(st.slate.rows_idle || []), ...st.slate.rows], start = Object.values(me.starters).filter(Boolean), sset = new Set(start);
      rows.forEach(r => { r._gs = 'post'; r._left = 0; r._act = sset.has(r.id) ? 5 : 30; });                 // every game is final and the bench scored far more than the lineup
      st.live.n = 1; st.wi.add = []; st.wi.drop = []; renderWhatIfEz();
      const B = wiEval(st.wi.ctx.base, st.wi.ctx), played = B.li.slice().sort().join() === start.slice().sort().join();
      const fa = rows.find(r => r.pos === 'WR' && !me.ids.includes(r.id)).id;
      st.wi.drop = [start[1]]; st.wi.add = [fa]; renderWhatIfEz();                                              // drop a starter, add a player who has already played
      const A = wiEval([...st.wi.ctx.base.filter(i => i !== start[1]), fa], st.wi.ctx);
      return {played, same: Object.values(A.set).join() === Object.values(B.set).join(), ev: [B.ev, A.ev], text: document.querySelector('#tab-whatif').innerText}; })()""")
    assert r["played"], "best lineup must be the lineup that was played, not the best scorers"
    assert r["same"] and abs(r["ev"][0] - r["ev"][1]) < 1e-9, r["ev"]
    assert "starting lineup would not change" in r["text"] and "Games have started" in r["text"]
    assert not pg.errs

def test_finished_week_rolls_over_to_the_next_week(br):
    # On a Tuesday ESPN says every game is final: lineups, trades and waivers are about the next week, not a hindsight lineup of what everyone scored.
    pg = page(br, "#/home", league=True); settle_live(pg)
    r = pg.evaluate("""(() => { const was = st.slate.week, nx = st.sched.weeks.find(w => w > was), fin = est => st.live.gs = Object.fromEntries(st.slate.games.map(g => [g.id, {s: 'post', left: 0, ...(est ? {est: 1} : {})}]));
      const out = {was, nx}; fin(true); out.estimated = maybeAdvanceWeek();                                  // our own kickoff-time guess is not enough
      fin(false); st.weekPinned = true; out.pinned = maybeAdvanceWeek(); st.weekPinned = false;               // nor is a week you picked yourself
      const sv = st.sched.next_week, sw = st.lg.data.week; st.sched.next_week = -1; st.lg.data.week = -1; out.notCurrent = maybeAdvanceWeek(); st.sched.next_week = sv; st.lg.data.week = sw;   // nor an old week
      out.advanced = maybeAdvanceWeek(); return out; })()""")
    assert r["estimated"] is False and r["pinned"] is False and r["notCurrent"] is False and r["advanced"] is True, r
    pg.wait_for_function("st.slate && st.slate.week === %d" % r["nx"], timeout=120000)
    assert pg.input_value("#g_week") == str(r["nx"])
    assert not pg.errs

def test_injured_players_are_simulated_from_their_return_week(br):
    # Jeremiyah Love (on IR, four-game minimum) showed "+0 vs repl." for the rest of the season: an Out player was out in every later week.
    pg = page(br, "#/moves?week=9", league=True); reqs = []
    pg.on("request", lambda r: reqs.append(r.post_data_json) if r.method == "POST" and r.url.endswith("/api/slate") else None)
    r = pg.evaluate("""(async () => {
      const w0 = st.slate.week; st.inj = {C: false}; st.wi.ros = null;
      st.returns = {week: w0, map: {A: {ret: w0 + 2, kind: 'ir'}, B: {ret: null, kind: 'ir'}, C: {ret: w0 + 1, kind: 'week'}}};
      await ensureRos(); st.inj = {};
      return {w0, tagA: retTag({id: 'A', status: 'Out'}), tagB: retTag({id: 'B', status: 'Out'}), tagHealthy: retTag({id: 'A', status: 'Active'})}; })()""")
    w0 = r["w0"]; by = {q["week"]: q["ov"] for q in reqs}
    assert by and min(by) == w0 + 1 and "A" not in by[w0 + 1] and by[w0 + 2]["A"] is True and by[w0 + 5]["A"] is True      # in the lineup pool from week w0 + 2
    assert all("B" not in o for o in by.values())                                                                      # no return this season: stays out
    assert all(o["C"] is False for o in by.values())                                                                   # your own Injuries edit wins
    assert "back wk %d" % (w0 + 2) in r["tagA"] and "out for the year" in r["tagB"] and r["tagHealthy"] == ""
    assert not pg.errs

def test_lopsided_trades_are_long_shots_and_hidden_by_default(br):
    pg = page(br, "#/moves", league=True)
    pg.click(".choice[data-mv=trade]"); pg.wait_for_selector("#mvFind"); pg.click("#mvFind")
    pg.wait_for_selector("#mvBody .tcard, #mvBody .note", timeout=180000)
    # the case from the bug report: a low-value player for the other team's two best players
    pg.click("[data-sug='0']"); pg.wait_for_selector("#sheet:not(.hide) #trEval table.st", timeout=60000)
    res = pg.evaluate("""(() => { const c = st.trc, x = st.tr.px, me = c.tms[c.L.my], tid = st.tr.partner, th = c.tms[tid];
      const tp = ids => ids.filter(i => x.row(i) && x.TP.has(x.row(i).pos)).sort((a, b) => x.vor(b) - x.vor(a));
      const give = [tp(me.ids).slice(-1)[0]], get = tp(th.ids).slice(0, 2), e = trEval(c, give, get, tid), s = {e, tier: trTier(e), o: trOdds(c, x, e, tid)};
      const real = st.tr.sugg.filter(q => q.good), fake = {...s, good: trGood(s)};
      const keep = st.tr.sugg; st.tr.sugg = [...keep, fake]; st.tr.showLong = false; const hidden = trShown().length; st.tr.showLong = true; const shown = trShown().length; st.tr.sugg = keep; st.tr.showLong = false;
      return {tier: s.tier, good: fake.good, theirGain: e.theirGain, bal: e.bal, verdict: trVerdict(e, s.o)[0], hidden, shown, nReal: real.length}; })()""")
    assert res["tier"] == 2 and res["good"] is False and res["theirGain"] < -0.5      # the other manager would be worse off: not recommended
    assert res["shown"] == res["hidden"] + 1                                          # kept, but only shown on request
    pg.keyboard.press("Escape")
    txt = pg.inner_text("#mvBody"); assert "They give up more" not in txt and "Long shot" not in txt   # nothing red in the default list
    assert not pg.errs

def test_season_outlook_uses_each_players_own_spread(br):
    pg = page(br, "#/league", league=True)
    r = pg.evaluate("""(() => { const pw = labPool(), ids = st.slate.rows.slice(0, 80).map(r => r.id), [m, s] = lineupStats(ids, pw, false);
      const lu = Object.values(autoSet(ids, pw, false)).filter(i => pw[i]).map(i => pw[i]);
      return {s, want: 1.15 * Math.sqrt(lu.reduce((a, p) => a + (p.sd * (liveOn(p) ? p._left : 1)) ** 2, 0)), noP10: Object.values(pw).every(p => p.p10 === undefined), n: lu.length}; })()""")
    assert r["n"] >= 6 and r["noP10"] and abs(r["s"] - r["want"]) < 1e-9        # rows have sd, not p10/p90, so the old formula gave every player a flat 5 (a player already in a final game has no spread left)
    f = pg.evaluate("""(() => { const pw = labPool(), ids = st.slate.rows.slice(0, 80).map(r => r.id), s0 = lineupStats(ids, pw, false)[1], p = pw[Object.values(autoSet(ids, pw, false)).find(i => pw[i])];
      const sv = [p._act, p._left, p._gs]; p._act = 10; p._left = 0; p._gs = 'post'; const s1 = lineupStats(ids, pw, false)[1]; [p._act, p._left, p._gs] = sv; return {s0, s1}; })()""")
    assert f["s1"] < f["s0"]                                                   # a finished game stops adding to the lineup's spread
    assert not pg.errs

def test_trade_suggestion_opens_builder_in_place(br):
    pg = page(br, "#/moves", league=True)
    assert pg.locator(".choice").count() == 2 and pg.locator("#mvBody .pick .card, #mvBody .card.empty").count() >= 1
    pg.click(".choice[data-mv=trade]"); pg.wait_for_selector("#mvFind"); pg.click("#mvFind")
    pg.wait_for_selector("#mvBody .tcard", timeout=180000); assert "you give" in pg.inner_text("#mvBody").lower()
    pg.click("[data-sug='0']"); pg.wait_for_selector("#sheet:not(.hide) #trEval h3", timeout=60000)
    assert pg.evaluate("[st.page, st.mode]") == ["moves", "easy"] and pg.locator("#sheet .tgrid").count() == 1 and "Evaluation" in pg.inner_text("#sheet #trEval")
    # photos and team logos inside the trade builder: roster tiles, both sides of the deal, and the lineup table
    assert pg.locator("#sheet .ptile .hs").count() >= 4 and pg.locator("#sheet .ptile .tlg").count() >= 4
    assert pg.locator("#sheet .tdeal .tp .hs").count() >= 2 and pg.locator("#sheet .tdeal .tp .tlg").count() >= 2
    pg.keyboard.press("Escape"); assert pg.locator("#sheet.hide").count() == 1 and pg.locator("#tm-trades").count() <= 1
    pg.click("#mvOwn"); pg.wait_for_selector("#sheet:not(.hide) .tgrid"); pg.keyboard.press("Escape"); assert not pg.errs

def test_players_and_games_details_on_demand(br):
    pg = page(br, "#/players/rank", league=True)
    heads = lambda: pg.eval_on_selector_all("#rkTable thead th", "t=>t.map(x=>x.innerText.trim().toLowerCase())")
    assert not any(h in heads() for h in ("boom%", "bust%", "sd", "median")) and pg.locator("#pg-players .plseg button:visible").count() == 3
    pg.check("#rkMore"); pg.wait_for_timeout(400); assert "boom%" in heads() and pg.locator(".colmenu:visible").count() == 1
    pg.uncheck("#rkMore"); pg.wait_for_timeout(400); assert "boom%" not in heads()
    pg.click("#side [data-nav=games]"); pg.wait_for_selector(".gcard.ez"); assert pg.locator(".gcard.ez table.gl:visible").count() == 0
    pg.click(".gcard.ez .ezmore summary >> nth=0"); assert pg.locator(".gcard.ez table.gl:visible").count() == 1
    assert pg.evaluate("document.documentElement.scrollWidth") <= 1440 and not pg.errs

def test_every_page_uses_the_easy_layout(br):
    pg = page(br, "#/lab/ov")
    for hs in ["#/lab/ov", "#/players/stack", "#/players/trend", "#/games", "#/settings"]:
        pg.evaluate(f"location.hash='{hs}'"); pg.wait_for_timeout(500)
        assert pg.evaluate("st.mode") == "easy" and pg.locator(".adv-only:visible").count() == 0, hs
    assert not pg.errs

# ---------- the pages rebuilt in the Easy style ----------
def test_league_page(br):
    pg = page(br, "#/league", league=True); pg.wait_for_selector("#tab-league .mu", timeout=30000)
    assert pg.locator("#tab-league .mu.mine").count() == 1 and pg.locator("#tab-league .tav").count() >= 4 and "Power rankings" in pg.inner_text("#tab-league")
    before = pg.inner_text("#tab-league .mu.mine"); ids = pg.eval_on_selector_all("#lgMy option", "o=>o.map(x=>x.value)")
    pg.select_option("#lgMy", [i for i in ids if i != pg.input_value("#lgMy")][0]); pg.wait_for_timeout(500)
    assert pg.inner_text("#tab-league .mu.mine") != before and pg.evaluate("document.documentElement.scrollWidth") <= 1440 and not pg.errs

def test_injuries_page_apply_and_clear(br):
    pg = page(br, "#/inj", league=True); pg.wait_for_selector("#injQ", timeout=30000)
    assert pg.locator("#injApply[disabled]").count() == 1
    pg.fill("#injQ", "KC"); pg.wait_for_selector("#tab-inj .card [data-inj]"); pg.fill("#injQ", "")
    pg.click("#tab-inj [data-inj][data-v=out] >> nth=0"); assert pg.locator("#injApply:not([disabled])").count() == 1
    pg.click("#injApply"); pg.wait_for_function("Object.keys(st.inj).length === 1", timeout=60000); pg.wait_for_function("st.slate && st.slate.edited && st.slate.edited.length", timeout=180000)
    pg.wait_for_selector("#injImpact .card", timeout=180000)
    pg.click("#injClear"); pg.click("#injApply"); pg.wait_for_function("Object.keys(st.inj).length === 0", timeout=60000); assert not pg.errs

def test_what_if_add_and_drop(br):
    pg = page(br, "#/whatif", league=True); pg.wait_for_selector("#wiQ", timeout=30000)
    assert "Pick a player to add or drop" in pg.inner_text("#tab-whatif") and pg.locator("#wiClear[disabled]").count() == 1
    name = pg.evaluate("(() => { const mine = leagueCtx().me.ids; return Object.values(labPool()).filter(p => !mine.includes(p.id)).sort((a, b) => b.proj - a.proj)[0].name; })()")
    pg.fill("#wiQ", name.split(" ")[-1]); pg.wait_for_selector("#wiRes [data-add]"); pg.click("#wiRes [data-add] >> nth=0")
    pg.wait_for_selector("#tab-whatif [data-rmadd]"); assert pg.locator("#wiClear:not([disabled])").count() == 1 and pg.locator("#tab-whatif .wts .wt").count() >= 3
    pg.click("#tab-whatif [data-drop] >> nth=0"); assert pg.locator("#tab-whatif [data-drop].on").count() == 1
    pg.click("#wiClear"); assert "Pick a player to add or drop" in pg.inner_text("#tab-whatif") and not pg.errs

def test_players_views(br):
    pg = page(br, "#/players/rank", league=True)
    pg.click(".plseg[data-sg=players] [data-k=trend]"); pg.wait_for_selector("#pl-trend [data-pid]")
    assert pg.evaluate("document.querySelector('#tab-slate').dataset.view") == "trend" and pg.locator(".adv-only:visible").count() == 0
    pg.click("#pl-trend [data-pid] >> nth=0"); pg.wait_for_selector("#dpanel .ezd-hero"); pg.keyboard.press("Escape")
    pg.click(".plseg[data-sg=players] [data-k=stack]"); pg.wait_for_selector("#pl-stack .stkc"); assert pg.locator("#pl-stack .stkc .hs").count() >= 2
    assert pg.evaluate("document.documentElement.scrollWidth") <= 1440 and not pg.errs

def test_my_lineup_start_sit_and_full_stats(br):
    pg = page(br, "#/mylineup", league=True); pg.wait_for_selector("#ssA", timeout=30000)
    ids = pg.eval_on_selector_all("#ssA option", "o=>o.map(x=>x.value).filter(Boolean)"); assert len(ids) >= 4
    pg.select_option("#ssA", ids[0]); pg.select_option("#ssB", ids[3]); pg.wait_for_selector("#ssOut .wvd")
    assert pg.inner_text("#ssOut .wvd").startswith("Start ") and pg.locator("#ssOut .ssp .hs").count() == 2
    pg.click("#mlWp"); pg.wait_for_selector("#mlWpOut .wvd", timeout=30000)
    pg.click("#mlFull"); pg.wait_for_selector("#sheet:not(.hide) #lgRoster table"); assert pg.locator("#lgRoster tbody tr").count() >= 8
    pg.keyboard.press("Escape"); assert pg.locator("#sheet.hide").count() == 1 and not pg.errs

def test_settings_connect_and_choose_team(br):
    pg = page(br, "#/settings"); assert pg.locator("#setAdd").inner_text() == "Connect a league"
    pg.click("#setAdd"); pg.wait_for_selector("#ezId"); pg.fill("#ezId", "1781890288"); pg.click("#ezBtn"); pg.wait_for_function("st.lg && st.lg.data", timeout=120000)
    pg.click("#side [data-nav=settings]"); pg.wait_for_selector("#setMy", timeout=20000)
    assert pg.locator("#setMy option").count() >= 10 and pg.locator("#setAdd").inner_text() == "Add a league"
    pg.select_option("#setMy", index=2); pg.wait_for_timeout(300); assert pg.evaluate("st.lg.my") == int(pg.input_value("#setMy")) and not pg.errs

def test_sleeper_connect_by_username_guesses_my_team(br):
    pg = page(br, "#/home"); pg.click("#ezPlat [data-p=sleeper]"); pg.wait_for_selector("#ezUser")
    pg.fill("#ezUser", "roan"); pg.click("#ezFind"); pg.wait_for_selector("[data-sl=L1]")
    pg.click("[data-sl=L1]"); pg.wait_for_function("st.lg && st.lg.platform === 'sleeper'", timeout=120000)
    assert pg.evaluate("[st.lg.my, st.needTeam, st.lg.data.teams.length]") == [1, False, 4]        # roster 1 is owned by the username we searched
    pg.wait_for_function("st.slate", timeout=120000); pg.click("#side [data-nav=settings]"); assert "Sleeper" in pg.inner_text("#tab-settings") and not pg.errs

def test_sleeper_by_id_asks_which_team_is_mine(br):
    pg = page(br, "#/home"); pg.click("#ezPlat [data-p=sleeper]"); pg.click("#tab-home details.adv summary"); pg.fill("#ezId", "L1"); pg.click("#ezBtn")
    pg.wait_for_selector("[data-myteam]", timeout=120000); assert pg.evaluate("st.needTeam") is True and pg.locator("[data-myteam]").count() == 4
    pg.click("[data-myteam='3']"); pg.wait_for_function("st.needTeam === false && st.lg.my === 3"); assert not pg.errs

def test_game_simulator_views_and_watch(br):
    pg = page(br, "#/lab/ov", block_images=True)
    assert pg.locator("#lpA .tlg").count() == 1 and pg.locator("#lpH .tlg").count() == 1
    a, h = pg.input_value("#away"), pg.input_value("#home"); pg.click("#swap"); assert (pg.input_value("#away"), pg.input_value("#home")) == (h, a)
    pg.click("#runBtn"); pg.wait_for_selector("#gl-ov .simhero", timeout=90000)
    pg.click("#gl-ov [data-pid] >> nth=0"); pg.wait_for_selector("#dpanel .ezd-hero"); pg.keyboard.press("Escape")
    pg.click("#labAllPl"); pg.wait_for_selector("#ptab tbody tr"); assert pg.evaluate("st.sub.lab") == "fan" and pg.locator("#ptab .hs").count() >= 6
    pg.click(".plseg[data-sg=lab] [data-k=mu]"); assert pg.locator("#gl-mu table.mb:visible").count() >= 1 and pg.locator("#gl-mu .rowc.cp").count() >= 4
    pg.click("#gameBtn"); pg.wait_for_selector("#gameBody:not(.hide) .sb", timeout=60000); assert pg.evaluate("st.sub.lab") == "pbp" and pg.locator("#gameBody .sb .tlg").count() == 2
    pg.click("#bNext"); assert pg.evaluate("st.i") == 1 and pg.evaluate("document.documentElement.scrollWidth") <= 1440 and not pg.errs

def test_rankings_show_projection_not_actual(br):
    pg = page(br, "#/players/rank?week=9", league=True)
    settle_live(pg)                                                        # let the score fetch finish first, then play one game out ourselves
    r = pg.evaluate("(() => { const x = st.slate.rows.find(r => r.pos === 'WR' && P0(r, 'proj') > 8); x._gs = 'post'; x._left = 0; x._act = P0(x, 'proj') + 7; st.live.n = 1; renderSlate(); return {n: x.name, p: fmt(P0(x, 'proj')), a: fmt(x._act)}; })()")
    pg.fill("#s_q", r["n"].lower()); pg.wait_for_timeout(400)
    assert pg.locator("#rkTable tbody tr td[data-k=proj]").first.inner_text().strip() == r["p"] != r["a"]
    assert r["a"] in pg.locator("#rkTable tbody tr td[data-k=act]").first.inner_text()      # what he scored sits in its own column
    pg.fill("#s_q", ""); pg.wait_for_timeout(300)
    ranks = pg.eval_on_selector_all("#rkTable tbody tr", "t=>t.map(x=>x.cells[0].innerText.trim()).map(Number)"); assert ranks == sorted(ranks) and not pg.errs

def test_simulator_neutral_follows_the_schedule(br):
    pg = page(br, "#/lab/ov")
    g = pg.evaluate("st.sched.games.find(g => g.neutral && !g.played && g.spread != null)"); assert g, "schedule needs an unplayed neutral-site game with a line"
    pg.select_option("#away", g["away"]); pg.select_option("#home", g["home"]); assert pg.is_checked("#neutral")      # the schedule says neutral
    pg.click("#runBtn"); pg.wait_for_selector("#gl-ov .simhero", timeout=90000); assert "Blended" in pg.inner_text("#gl-ov .simhero")   # and the line blend still applies
    other = pg.evaluate("(a => Object.keys(st.TEAMS).find(t => t !== a && !st.sched.games.some(x => x.home === t && x.away === a)))(%r)" % g["away"])
    pg.select_option("#home", other); assert not pg.is_checked("#neutral")                                         # picking another opponent resets it
    pg.click("#runBtn"); pg.wait_for_function("document.querySelector('#gl-ov').innerText.includes('no scheduled game')", timeout=90000); assert not pg.errs
