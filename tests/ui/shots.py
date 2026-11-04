"""Screenshot every page/subtab at 3 widths.  python tests/ui/shots.py <outdir> [theme]"""
import sys, re, time
from playwright.sync_api import sync_playwright
out = sys.argv[1]; theme = sys.argv[2] if len(sys.argv) > 2 else ""
URL = "http://localhost:8000/"
W = {"desktop": (1440, 900), "tablet": (820, 1100), "phone": (390, 844)}
with sync_playwright() as p:
    b = p.chromium.launch()
    for wn, (w, h) in W.items():
        pg = b.new_page(viewport={"width": w, "height": h}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL); pg.wait_for_timeout(2500)
        if theme: pg.evaluate(f"document.documentElement.dataset.theme='{theme}'")
        for page in ["home", "games", "players", "team", "inj", "lab", "settings"]:
            pg.evaluate(f"go('{page}')"); pg.wait_for_timeout(1200)
            try: pg.wait_for_function("!document.body.innerText.includes('Simulating')", timeout=60000)
            except Exception: errs.append(page+": still simulating")
            pg.wait_for_timeout(600)
            subs = pg.eval_on_selector_all(f"#pg-{page} .subtabs button", "bs=>bs.map(b=>b.dataset.k)")
            pg.screenshot(path=f"{out}/{wn}_{page}.png", full_page=True)
            for k in subs[1:] if subs else []:
                try:
                    pg.click(f"#pg-{page} .subtabs button[data-k='{k}']", timeout=2000); pg.wait_for_timeout(1500)
                    pg.screenshot(path=f"{out}/{wn}_{page}_{k}.png", full_page=True)
                except Exception as e: errs.append(f"{page}/{k}: {str(e)[:60]}")
        sw = pg.evaluate("document.documentElement.scrollWidth"); print(wn, "scrollWidth", sw, "vs", w, "errors", errs)
        pg.close()
    b.close()
