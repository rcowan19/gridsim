"""axe-core over every page, both modes, both themes, desktop and phone.  python tests/ui/axe_run.py
Run the server with GRIDSIM_ESPN_MOCK so the league pages have data."""
import sys
from playwright.sync_api import sync_playwright
AX = open("tests/ui/axe.min.js").read(); tot = 0
PAGES = {
    "easy": ["home", "mylineup", "moves", "players/rank", "games", "settings"],
    "adv": ["home", "games", "players/rank", "players/stack", "inj", "lab/ov", "team/lineup", "team/trades", "settings"],
}
modes = sys.argv[1:] or ["easy", "adv"]
with sync_playwright() as p:
    b = p.chromium.launch()
    for mode in modes:
        for theme in ("light", "dark"):
            for w in (1440, 390):
                pg = b.new_page(viewport={"width": w, "height": 900})
                pg.add_init_script(f"localStorage.setItem('gs_mode','{mode}');localStorage.setItem('gs_league','1781890288');")
                pg.goto("http://localhost:8000/"); pg.wait_for_function("typeof st!=='undefined' && st.sched", timeout=20000)
                pg.wait_for_function("st.lg && st.slate", timeout=120000)
                pg.evaluate(f"setTheme('{theme}')")
                for h in PAGES[mode]:
                    pg.evaluate(f"location.hash='#/{h}'"); pg.wait_for_timeout(900); pg.wait_for_function("!document.body.innerText.includes('Simulating')", timeout=90000); pg.wait_for_timeout(300)
                    pg.evaluate(AX); r = pg.evaluate("axe.run(document,{runOnly:['wcag2a','wcag2aa','wcag21aa','wcag22aa']})")
                    for v in r["violations"]:
                        tot += len(v["nodes"]); print(mode, theme, w, h, v["id"], v["impact"], len(v["nodes"]), v["nodes"][0]["target"][:1], v["nodes"][0].get("html", "")[:90].replace("\n", " "))
                pg.close()
    b.close()
print("TOTAL violating nodes:", tot)
