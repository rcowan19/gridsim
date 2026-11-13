# Launch checklist: accounts, payments, hosting

GridSim runs locally with no sign-in exactly as before. Setting `GRIDSIM_ENV=production` turns on sign-in, plan checks and billing, and the server refuses to start if any setting below is missing.

```
browser ── Supabase (sign-in, Postgres) ──┐
   │                                      ├── GridSim server (FastAPI + simulator) on Fly.io, data on a disk
   └──── Stripe Checkout / Portal ────────┘        ▲
                  Stripe webhook ──────────────────┘  (the only thing that grants or removes a plan)
```

## 1. Supabase (about 15 minutes)

1. New project. Note the region; pick the one nearest your Fly region.
2. Authentication → Providers: leave Email on. For Google (optional): create an OAuth client in Google Cloud Console and paste its ID and secret into Providers → Google, then set `GRIDSIM_GOOGLE=1`. Until then the Continue with Google button is hidden.
3. Authentication → URL configuration: Site URL = your app address; add it and `http://localhost:8000` to Redirect URLs.
4. Settings → API: copy the project URL (`SUPABASE_URL`) and the anon public key (`SUPABASE_ANON_KEY`). If the page shows a "JWT secret" (legacy), copy it as `SUPABASE_JWT_SECRET`. Newer projects use signing keys; then `SUPABASE_URL` alone is enough.
5. Settings → Database: copy the connection string as `DATABASE_URL`. Tables are created on first start.

## 2. Stripe (start live-mode verification early, it can take days)

1. Work in **test mode** first.
2. Product catalog: create "GridSim monthly" (recurring, monthly) and "GridSim season pass" (one-time). Copy both price IDs into `STRIPE_PRICE_MONTHLY` and `STRIPE_PRICE_SEASON`.
3. Developers → Webhooks → add endpoint `https://YOUR-APP/api/stripe/webhook` with these events: `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `charge.refunded`. Copy the signing secret to `STRIPE_WEBHOOK_SECRET`.
4. Settings → Billing → Customer portal: turn on cancel subscription and update payment method.
5. API keys: `STRIPE_SECRET_KEY` (`sk_test_...`, later `sk_live_...`).
6. Optional: `STRIPE_TRIAL_DAYS` for a free trial on monthly; Stripe Tax then `STRIPE_AUTOMATIC_TAX=1`.
7. A season pass runs to March 1 after the season it was bought for (set `SEASON_PASS_UNTIL=YYYY-MM-DD` to override).

## 3. Fly.io

See the comments at the top of `fly.toml`. Generate the key for `GRIDSIM_SECRET_KEY` with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and store a copy somewhere safe. If it is lost, users have to re-enter their ESPN cookies.
First boot downloads the data and builds the model (several minutes); the health check waits for it. Data refreshes every `GRIDSIM_REFRESH_HOURS`.
`GRIDSIM_ADMIN_EMAILS` lists who can press "Update data".

## 4. End-to-end test (test mode) before real money

Shortcut for a local run: `bash scripts/paid_local.sh` creates the virtual environment, installs packages, installs the Stripe CLI, asks for your email, the Supabase database password and the Stripe test secret key (hidden), writes a git-ignored `.env`, forwards webhooks and starts the app. The list below is what to try once it is running.

1. Create an account, confirm the email, sign in. You should land on the plan screen.
2. Buy the season pass with card `4242 4242 4242 4242`, any future date, any CVC. You return to the app and it unlocks within a few seconds (webhook).
3. In Stripe, refund it. Access should end.
4. Repeat with monthly, then cancel in "Manage billing". Access continues to the period end.
5. Connect an ESPN league (private, with cookies) and a Sleeper league (username). Reload, sign out and in; both should still be there. Open one from Settings → Leagues.
6. Sign in as a second account: it must not see the first account's leagues.

## 5. Before charging anyone

- **Legal pages**: `static/legal/terms.html` and `privacy.html` are drafts with `[brackets]`. Have a lawyer review them. Decide the refund policy and what you say about taxes.
- **Business basics**: Stripe requires a legal entity or sole-proprietor details and a bank account; you are under 21 in many states for some account types, so check what Stripe asks of you. Sales tax on digital services varies by state; Stripe Tax can calculate it.
- **Logos and headshots**: the UI loads team logos and player photos from ESPN and NFL servers. That is fine for personal use; for a paid product get permission or switch to licensed assets.
- **ESPN**: the league API is unofficial and can change or block you without notice. Sleeper's API is public and documented. Yahoo is not built yet.
- **Betting**: not part of this build. Selling betting picks adds advertising and gambling rules that vary by state.

## Known limits

- Rate limiting is per server process (fine for one machine; use Redis if you scale out).
- Tables are created automatically, but there are no migrations yet (add Alembic before changing columns).
- No in-app account deletion yet; the privacy page tells people to email you.
- The Sleeper adapter was written from Sleeper's documented responses and tested with fixtures. Try it with a real username before launch.
- Stripe and Supabase calls were tested with mocks; the first real sign-in and test payment is the true end-to-end check.
