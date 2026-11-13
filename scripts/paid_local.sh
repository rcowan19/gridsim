#!/usr/bin/env bash
# Run the paid version of GridSim (sign-in + billing) on this computer, in Stripe TEST mode.
#   bash scripts/paid_local.sh             set everything up (first run), then start the app
#   bash scripts/paid_local.sh --no-run    set up only
#   REDO=1 bash scripts/paid_local.sh      throw away .env and answer the questions again
# First run asks three things (what you type for the two secrets is hidden):
#   your email, the Supabase database password, the Stripe test secret key.
set -euo pipefail
cd "$(dirname "$0")/.."

NO_RUN=0; [ "${1:-}" = "--no-run" ] && NO_RUN=1
say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# Public project identifiers (safe to keep here; the secrets are asked for below).
SUPABASE_URL="${SUPABASE_URL:-https://jcseqibdihlstabkgmew.supabase.co}"
SUPABASE_ANON_KEY="${SUPABASE_ANON_KEY:-sb_publishable_9y7OeaaNcauerk4ysCzG6A_83dts2T3}"
DB_HOST="${DB_HOST:-aws-0-us-west-2.pooler.supabase.com}"
DB_USER="${DB_USER:-postgres.jcseqibdihlstabkgmew}"
PRICE_MONTHLY="${STRIPE_PRICE_MONTHLY:-price_1UbTPqAHiDRJJhot2ZLil4pa}"
PRICE_SEASON="${STRIPE_PRICE_SEASON:-price_1UbTQEAHiDRJJhot83nBnKsF}"

say "1/5  Python"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10 or newer is needed.  Install it with: brew install python"

say "2/5  Virtual environment and packages (a minute or two the first time)"
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

say "3/5  Stripe CLI (forwards Stripe webhooks to this computer)"
if ! command -v stripe >/dev/null 2>&1; then
  command -v brew >/dev/null 2>&1 || die "Homebrew is missing. Install it from https://brew.sh and run this again."
  brew install stripe/stripe-cli/stripe
fi

if [ "${REDO:-0}" = 1 ]; then rm -f .env; fi
if [ -s .env ] && grep -q '^STRIPE_WEBHOOK_SECRET=.\+' .env; then
  say "4/5  Settings: keeping your existing .env  (REDO=1 starts over)"
else
  say "4/5  Settings"
  read -rp "Your email (you become the admin who can press Update data): " ADMIN_EMAIL
  read -rsp "Supabase database password (chosen when you created the project; hidden): " DBPW; echo
  read -rsp "Stripe TEST secret key, starts with sk_test_ (hidden): " SK; echo
  [ -n "$ADMIN_EMAIL" ] || die "Email is empty."
  [ -n "$DBPW" ] || die "Database password is empty."
  case "$SK" in sk_test_*) ;; *) die "That is not a Stripe test-mode secret key (it must start with sk_test_). Live keys are never used here." ;; esac

  ENC_PW=$(DBPW="$DBPW" python3 -c 'import os, urllib.parse as u; print(u.quote(os.environ["DBPW"], safe=""))')
  FERNET=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
  WHSEC=$(stripe listen --api-key "$SK" --print-secret 2>/dev/null | tail -n1 | tr -d '[:space:]') || true
  case "$WHSEC" in whsec_*) ;; *) die "The Stripe CLI did not return a webhook secret. Check that the test secret key is right." ;; esac

  umask 077
  cat > .env <<EOF
GRIDSIM_ENV='production'
APP_URL='http://localhost:8000'
SUPABASE_URL='$SUPABASE_URL'
SUPABASE_ANON_KEY='$SUPABASE_ANON_KEY'
DATABASE_URL='postgresql://$DB_USER:$ENC_PW@$DB_HOST:5432/postgres'
GRIDSIM_SECRET_KEY='$FERNET'
STRIPE_SECRET_KEY='$SK'
STRIPE_WEBHOOK_SECRET='$WHSEC'
STRIPE_PRICE_MONTHLY='$PRICE_MONTHLY'
STRIPE_PRICE_SEASON='$PRICE_SEASON'
STRIPE_TRIAL_DAYS='0'
GRIDSIM_ADMIN_EMAILS='$ADMIN_EMAIL'
GRIDSIM_REFRESH_HOURS='0'
EOF
  echo "Wrote .env (only you can read it; git ignores it)."
fi

if [ "$NO_RUN" = 1 ]; then say "5/5  Setup done. Start it with: bash scripts/paid_local.sh"; exit 0; fi

say "5/5  Starting GridSim at http://localhost:8000   (Ctrl+C stops it)"
set -a; . ./.env; set +a
stripe listen --api-key "$STRIPE_SECRET_KEY" --forward-to localhost:8000/api/stripe/webhook >/tmp/gridsim-stripe-listen.log 2>&1 &
LISTEN_PID=$!
trap 'kill "$LISTEN_PID" 2>/dev/null || true' EXIT
echo "Stripe webhooks are being forwarded (log: /tmp/gridsim-stripe-listen.log)."
python server.py
