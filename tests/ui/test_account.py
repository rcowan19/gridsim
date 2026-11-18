"""Sign-in, plan and saved-league flow in a real browser against a second server (port 8001) with auth ON.
Supabase is replaced by tests/ui/fake_supabase.js; Stripe's signed webhook is posted by the test itself. No network needed.
    python -m pytest -q tests/ui/test_account.py
"""
import hashlib, hmac, json, os, subprocess, sys, time, urllib.request
import jwt, pytest
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT, URL = 8001, "http://localhost:8001/"
SECRET, WH = "ui-test-jwt-secret-ui-test-jwt-secret-12345", "whsec_ui"
FAKE = open(os.path.join(ROOT, "tests/ui/fake_supabase.js")).read()
AXE = os.path.join(ROOT, "tests/ui/axe.min.js")


def token(sub="ui-user-1", email="ui@example.com"):
    return jwt.encode({"sub": sub, "email": email, "aud": "authenticated", "exp": int(time.time()) + 3600}, SECRET, algorithm="HS256")


def webhook(ev):
    body = json.dumps(ev).encode(); ts = int(time.time())
    sig = hmac.new(WH.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(URL + "api/stripe/webhook", data=body, headers={"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"})
    return urllib.request.urlopen(req).status


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    d = tmp_path_factory.mktemp("acct")
    subprocess.run([sys.executable, "-m", "tests.ui.make_sleeper_mock", str(d / "sl.json")], cwd=ROOT, check=True, capture_output=True)
    env = {**os.environ, "GRIDSIM_REFRESH_HOURS": "0", "GRIDSIM_AUTH": "on", "SUPABASE_JWT_SECRET": SECRET, "DATABASE_URL": f"sqlite:///{d}/a.db", "GRIDSIM_SECRET_KEY": "u6RUU1wN3c3LLr2c3B9rRS5iFXEOmP2S3o_DMHbQEvU=",
           "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": WH, "STRIPE_PRICE_MONTHLY": "price_m", "STRIPE_PRICE_SEASON": "price_s", "APP_URL": URL.rstrip("/"),
           "GRIDSIM_SLEEPER_MOCK": str(d / "sl.json"), "GRIDSIM_NFL_STATUS_MOCK": str(d / "none.json"), "GRIDSIM_MODEL": ""}
    env.pop("GRIDSIM_ENV", None)
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--port", str(PORT)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try: urllib.request.urlopen(URL + "healthz", timeout=2); break
        except Exception: time.sleep(1)
    else: p.kill(); pytest.fail("test server did not start")
    yield
    p.terminate()
    try: p.wait(15)
    except subprocess.TimeoutExpired: p.kill()


@pytest.fixture(scope="module")
def br():
    with sync_playwright() as p:
        b = p.chromium.launch(); yield b; b.close()


def open_page(br, w=1280, h=900):
    ctx = br.new_context(viewport={"width": w, "height": h}); pg = ctx.new_page(); pg.errs = []
    pg.on("pageerror", lambda e: pg.errs.append(str(e)))
    ctx.route("**/static/vendor/supabase.js", lambda r: r.fulfill(body=FAKE, content_type="application/javascript"))
    ctx.route(lambda u: "espncdn.com" in u or "nfl.com" in u, lambda r: r.abort())
    pg.add_init_script(f"window.__TOKEN={json.dumps(token())};")
    return pg


def axe_clean(pg):
    pg.add_script_tag(path=AXE)
    bad = pg.evaluate("axe.run(document.querySelector('#gate'), {resultTypes:['violations']}).then(r => r.violations.map(v => v.id + ': ' + v.nodes.length))")
    assert bad == [], bad


def test_api_is_closed_without_sign_in(srv):
    for path in ("api/teams", "api/schedule", "api/me", "api/league?platform=sleeper&league=L1"):
        try: urllib.request.urlopen(URL + path); assert False, path
        except urllib.error.HTTPError as e: assert e.code == 401, (path, e.code)
    assert json.load(urllib.request.urlopen(URL + "api/config"))["auth"] is True


def test_sign_in_pay_connect_and_come_back(srv, br):
    pg = open_page(br); pg.goto(URL); pg.wait_for_selector("#gForm")
    assert pg.evaluate("document.querySelector('.app').inert") is True and pg.locator("#gate").is_visible()
    axe_clean(pg)
    pg.fill("#gEmail", "ui@example.com"); pg.fill("#gPw", "wrong-password"); pg.click("#gSubmit")
    pg.wait_for_function("document.querySelector('#gErr').textContent.includes('Invalid login')")
    pg.fill("#gPw", "right-password"); pg.click("#gSubmit")
    pg.wait_for_selector("[data-plan=season]", timeout=20000)                       # signed in, no plan yet: the plan screen
    assert "ui@example.com" in pg.inner_text("#gate") and pg.locator("[data-plan]").count() == 2
    axe_clean(pg)
    asked = []
    pg.route("**/api/billing/checkout", lambda r: (asked.append(json.loads(r.request.post_data)), r.fulfill(json={"url": "/?billing=success"})))
    pg.click("[data-plan=season]"); pg.wait_for_function("location.search.includes('billing=success')", timeout=20000)
    assert asked == [{"plan": "season"}]
    # Stripe's webhook is what grants access: before it lands the page waits, after it the app opens
    assert webhook({"id": "evt_ui1", "type": "checkout.session.completed", "created": int(time.time()), "data": {"object": {
        "mode": "payment", "payment_status": "paid", "customer": "cus_ui", "client_reference_id": "ui-user-1", "metadata": {"uid": "ui-user-1", "plan": "season"}}}}) == 200
    pg.wait_for_function("st.me && st.me.plan.active && st.me.plan.plan === 'season'", timeout=30000)
    assert not pg.locator("#gate").is_visible() and pg.evaluate("document.querySelector('.app').inert") is False
    pg.wait_for_function("st.sched", timeout=20000)
    # connect a Sleeper league by id, pick my team, and have it saved to the account
    pg.click("#ezPlat [data-p=sleeper]"); pg.click("#tab-home details.adv summary"); pg.fill("#ezId", "L1"); pg.click("#ezBtn")
    pg.wait_for_selector("[data-myteam]", timeout=120000); pg.click("[data-myteam='2']"); pg.wait_for_function("st.needTeam === false")
    pg.wait_for_function("st.me.leagues.length === 1 && st.me.leagues[0].my_team === null || st.me.leagues[0].my_team === '2'", timeout=10000)
    pg.reload(); pg.wait_for_function("st.lg && st.lg.platform === 'sleeper' && st.lg.saved", timeout=120000)       # restored from the account, not the browser
    assert pg.evaluate("[st.lg.my, st.needTeam]") == [2, False]
    pg.click("#side [data-nav=settings]"); pg.wait_for_selector("#setOut")
    t = pg.inner_text("#tab-settings"); assert "ui@example.com" in t and "Season pass" in t and "Sleeper" in t and not pg.locator("#setUpd").count()
    assert not pg.errs, pg.errs
    pg.click("#setOut"); pg.wait_for_selector("#gForm", timeout=20000); assert pg.evaluate("localStorage.getItem('fake_session')") is None


def test_other_accounts_see_nothing_of_it(srv, br):
    pg = open_page(br); pg.add_init_script(f"window.__TOKEN={json.dumps(token('ui-user-2', 'two@example.com'))};"); pg.goto(URL); pg.wait_for_selector("#gForm")
    pg.fill("#gEmail", "two@example.com"); pg.fill("#gPw", "right-password"); pg.click("#gSubmit"); pg.wait_for_selector("[data-plan=season]", timeout=20000)
    assert pg.evaluate("fetch('/api/me').then(r => r.json()).then(d => [d.plan.active, d.leagues.length])") == [False, 0]
    assert pg.evaluate("fetch('/api/teams').then(r => r.status)") == 402


def test_phone_layout_of_sign_in_and_plans(srv, br):
    pg = open_page(br, 390, 780); pg.add_init_script(f"window.__TOKEN={json.dumps(token('ui-user-3', 'three@example.com'))};"); pg.goto(URL); pg.wait_for_selector("#gForm")
    assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    pg.fill("#gEmail", "three@example.com"); pg.fill("#gPw", "right-password"); pg.click("#gSubmit"); pg.wait_for_selector("[data-plan]", timeout=20000)
    assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1") and pg.locator("[data-plan]").count() == 2
