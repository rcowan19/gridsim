"""axe-core over every page, both modes, both themes, desktop and phone.  python tests/ui/axe_run.py
Run the server with GRIDSIM_ESPN_MOCK so the league pages have data."""
import sys
from playwright.sync_api import sync_playwright
AX = open("tests/ui/axe.min.js").read(); tot = 0
PAGES = {
    "easy": ["home", "mylineup", "moves", "players/rank", "players/trend", "players/stack", "games", "league", "inj", "whatif", "settings", "lab/ov"],
    "sim": ["lab/ov", "lab/fan", "lab/mu", "lab/pbp"],   # game simulator after a result and a watched game, every detail section open
    "sheet": ["moves"],   # moves page with the trade builder side sheet open and a trade scored
}
modes = sys.argv[1:] or ["easy", "sim", "sheet"]
with sync_playwright() as p:
    b = p.chromium.launch()
    for mode in modes:
        for theme in ("light", "dark"):
            for w in (1440, 390):
                pg = b.new_page(viewport={"width": w, "height": 900})
                pg.add_init_script(f"localStorage.setItem('gs_league','1781890288');")
                pg.goto("http://localhost:8000/"); pg.wait_for_function("typeof st!=='undefined' && st.sched", timeout=20000)
                pg.wait_for_function("st.lg && st.slate", timeout=120000)
                pg.evaluate(f"setTheme('{theme}')")
                for h in PAGES[mode]:
                    pg.evaluate(f"location.hash='#/{h}'"); pg.wait_for_timeout(900); pg.wait_for_function("!document.body.innerText.includes('Simulating')", timeout=90000); pg.wait_for_timeout(300)
                    if mode == "sim":
                        if h == "lab/ov": pg.click("#runBtn"); pg.wait_for_function("st.last", timeout=180000)
                        if h == "lab/pbp": pg.click("#gameBtn"); pg.wait_for_selector("#gameBody:not(.hide) .sb", timeout=60000)
                        pg.evaluate("document.querySelectorAll('#pg-lab details').forEach(d => d.open = true)"); pg.wait_for_timeout(400)
                    if mode == "sheet":
                        pg.evaluate("st.mv.tab='trade'; renderMoves()"); pg.click("#mvOwn"); pg.wait_for_selector("#tm-trades .ptile", timeout=60000)
                        pg.click("#tm-trades [data-partner]"); pg.click("#tm-trades [data-give]"); pg.click("#tm-trades [data-get]")
                        pg.wait_for_selector("#trEval .tdeal", timeout=240000); pg.click("#trAny"); pg.wait_for_selector("#trSugg .sug", timeout=240000); pg.wait_for_timeout(400)
                    pg.evaluate(AX); r = pg.evaluate("axe.run(document,{runOnly:['wcag2a','wcag2aa','wcag21aa','wcag22aa']})")
                    for v in r["violations"]:
                        tot += len(v["nodes"]); print(mode, theme, w, h, v["id"], v["impact"], len(v["nodes"]), v["nodes"][0]["target"][:1], v["nodes"][0].get("html", "")[:90].replace("\n", " "))
                pg.close()
    b.close()
print("TOTAL violating nodes:", tot)
