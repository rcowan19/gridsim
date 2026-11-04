"""Playwright smoke tests (server must be running on :8000):  python -m pytest -q tests/ui/test_smoke.py"""
import json, pytest
from playwright.sync_api import sync_playwright
URL = "http://localhost:8000/"

@pytest.fixture(scope="module")
def br():
    with sync_playwright() as p:
        b = p.chromium.launch(); yield b; b.close()

def page(br, hash_="", w=1440, h=900):
    pg = br.new_page(viewport={"width": w, "height": h}); pg.errs = []
    pg.on("pageerror", lambda e: pg.errs.append(str(e)))
    pg.goto(URL + hash_); pg.wait_for_function("typeof st!=='undefined' && st.sched", timeout=20000)
    pg.wait_for_function("!document.body.innerText.includes('Simulating')", timeout=90000)
    return pg

def test_nav_every_page(br):
    pg = page(br)
    for nid, hs in [("games", "#/games"), ("proj", "#/players/rank"), ("lineup", "#/team/lineup"), ("waivers", "#/team/waivers"), ("trades", "#/team/trades"), ("league", "#/team/league"), ("inj", "#/inj"), ("lab", "#/lab/ov"), ("settings", "#/settings"), ("home", "#/home")]:
        pg.click(f"#side [data-nav={nid}]"); pg.wait_for_timeout(250)
        assert pg.evaluate("location.hash").startswith(hs), nid
        assert pg.locator(f"#side [data-nav={nid}][aria-current=page]").count() == 1, nid
    assert not pg.errs

def test_url_state_restores(br):
    pg = page(br, "#/players/rank?week=8&pos=QB&q=mah&fmt=half")
    assert pg.evaluate("[st.week, st.pos, st.q, st.fmt]") == [8, "QB", "mah", "half"]
    assert pg.locator("#s_body tbody tr").count() >= 1
    pg.go_back() if False else None

def test_back_forward(br):
    pg = page(br, "#/home"); pg.click("#side [data-nav=games]"); pg.click("#side [data-nav=settings]")
    pg.go_back(); assert "#/games" in pg.evaluate("location.hash"); pg.go_forward(); assert "#/settings" in pg.evaluate("location.hash")

def test_palette(br):
    pg = page(br); pg.keyboard.press("Control+k"); pg.keyboard.type("settings"); pg.keyboard.press("Enter"); pg.wait_for_timeout(300)
    assert pg.evaluate("st.page") == "settings"
    pg.keyboard.press("g"); pg.keyboard.press("p"); pg.wait_for_timeout(300); assert pg.evaluate("st.page") == "players"
    pg.keyboard.press("?"); assert pg.locator("[aria-label='Keyboard shortcuts']").count() == 1

def test_table_sort_filter_columns_csv(br):
    pg = page(br, "#/players/rank")
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
    pg = page(br, "#/lab/ov"); pg.click("#runBtn"); pg.wait_for_function("document.querySelector('#gl-ov').innerText.includes('win probability')", timeout=90000)
    assert "standard error" in pg.inner_text("#gl-ov"); assert "P50" in pg.inner_html("#gl-ov"); assert not pg.errs

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
    pg = page(br, "#/games"); pg.route("**/api/slate", lambda r: r.abort()); pg.click("#g_refresh"); pg.wait_for_selector("#retrySl", timeout=15000)
    assert "Could not reach" in pg.inner_text("#nflBody"); pg.unroute("**/api/slate"); pg.click("#retrySl"); pg.wait_for_selector(".gcard", timeout=90000)
