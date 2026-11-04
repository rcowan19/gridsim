"""axe-core over every page, both themes.  python tests/ui/axe_run.py"""
import json
from playwright.sync_api import sync_playwright
AX = open("tests/ui/axe.min.js").read(); tot = 0
with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("light", "dark"):
        for w in (1440, 390):
            pg = b.new_page(viewport={"width": w, "height": 900}); pg.goto("http://localhost:8000/"); pg.wait_for_function("typeof st!=='undefined' && st.sched", timeout=20000)
            pg.evaluate(f"setTheme('{theme}')")
            for h in ["home", "games", "players/rank", "inj", "lab/ov", "team/lineup", "settings"]:
                pg.evaluate(f"location.hash='#/{h}'"); pg.wait_for_timeout(700); pg.wait_for_function("!document.body.innerText.includes('Simulating')", timeout=90000); pg.wait_for_timeout(300)
                pg.evaluate(AX); r = pg.evaluate("axe.run(document,{runOnly:['wcag2a','wcag2aa','wcag21aa','wcag22aa']})")
                for v in r["violations"]:
                    tot += len(v["nodes"]); print(theme, w, h, v["id"], v["impact"], len(v["nodes"]), v["nodes"][0]["target"][:1], v["nodes"][0].get("html", "")[:90].replace("\n", " "))
            pg.close()
    b.close()
print("TOTAL violating nodes:", tot)
