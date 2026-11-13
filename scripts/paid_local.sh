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

# Try a real login with the given postgresql:// URL, so a wrong password is caught right away.
check_db() {
  DBURL="$1" python - <<'PY'
import os, sys, psycopg
try:
    psycopg.connect(os.environ["DBURL"], connect_timeout=10).close()
except Exception as e:
    print("   The database said:", str(e).strip().splitlines()[0][:200]); sys.exit(1)
PY
}
DB_PAGE="https://supabase.com/dashboard/project/jcseqibdihlstabkgmew/database/settings"

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
# Uses one you already have; otherwise downloads Stripe's own release (no Homebrew or Xcode tools needed).
STRIPE_CLI_VERSION=1.55.11
if command -v stripe >/dev/null 2>&1; then
  STRIPE=$(command -v stripe)
else
  STRIPE=".tools/stripe-$STRIPE_CLI_VERSION/stripe"
  if [ ! -x "$STRIPE" ]; then
    case "$(uname -s)-$(uname -m)" in
      Darwin-arm64)  ARCH=mac-os_arm64;  SUM=0b78f1bc5efb7c7b00cf5e5cddd673d674a030b68dd7a6f025ff180f67154502 ;;
      Darwin-x86_64) ARCH=mac-os_x86_64; SUM=741fb41da8bb2b08c15434eb64dd58c9636dd431223c8fa652a19812a4fd5866 ;;
      *) die "Install the Stripe CLI from https://docs.stripe.com/stripe-cli and run this again." ;;
    esac
    DIR=".tools/stripe-$STRIPE_CLI_VERSION"; mkdir -p "$DIR"
    curl -fsSL -o "$DIR/stripe.tgz" "https://github.com/stripe/stripe-cli/releases/download/v$STRIPE_CLI_VERSION/stripe_${STRIPE_CLI_VERSION}_${ARCH}.tar.gz" \
      || { rm -rf "$DIR"; die "Could not download the Stripe CLI (network?)."; }
    [ "$(shasum -a 256 "$DIR/stripe.tgz" | cut -d' ' -f1)" = "$SUM" ] \
      || { rm -rf "$DIR"; die "The downloaded Stripe CLI did not match its expected checksum, so it was not used."; }
    tar -xzf "$DIR/stripe.tgz" -C "$DIR" stripe && rm -f "$DIR/stripe.tgz"
    chmod +x "$STRIPE"; xattr -d com.apple.quarantine "$STRIPE" 2>/dev/null || true
  fi
fi
echo "Using Stripe CLI at $STRIPE"

if [ "${REDO:-0}" = 1 ]; then rm -f .env; fi
if [ -s .env ] && grep -q '^STRIPE_WEBHOOK_SECRET=.\+' .env; then
  say "4/5  Settings: keeping your existing .env  (REDO=1 starts over)"
  check_db "$(sed -n "s/^DATABASE_URL='\(.*\)'$/\1/p" .env)" \
    || die "The saved database password was rejected. Reset it at $DB_PAGE (Database password), then run:  REDO=1 bash scripts/paid_local.sh"
  grep -q '^GRIDSIM_GOOGLE=' .env || echo "GRIDSIM_GOOGLE='1'" >> .env     # Google sign-in is enabled in Supabase
else
  say "4/5  Settings"
  read -rp "Your email (you become the admin who can press Update data): " ADMIN_EMAIL
  read -rsp "Stripe TEST secret key, starts with sk_test_ (hidden): " SK; echo
  [ -n "$ADMIN_EMAIL" ] || die "Email is empty."
  case "$SK" in sk_test_*) ;; *) die "That is not a Stripe test-mode secret key (it must start with sk_test_). Live keys are never used here." ;; esac

  DB_OK=0
  for attempt in 1 2 3; do
    read -rsp "Supabase database password (hidden; just press Enter if you need to reset it first): " DBPW; echo
    if [ -z "$DBPW" ]; then echo "   Reset it at: $DB_PAGE  ->  Database password -> Reset -> Generate a password -> copy it, then paste it here."; continue; fi
    ENC_PW=$(DBPW="$DBPW" python3 -c 'import os, urllib.parse as u; print(u.quote(os.environ["DBPW"], safe=""))')
    DBURL="postgresql://$DB_USER:$ENC_PW@$DB_HOST:5432/postgres"
    if check_db "$DBURL"; then DB_OK=1; break; fi
    echo "   That password was rejected. Reset it at: $DB_PAGE"
  done
  [ "$DB_OK" = 1 ] || die "No working database password after 3 tries. Reset it at $DB_PAGE and run again."
  FERNET=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
  WHSEC=$("$STRIPE" listen --api-key "$SK" --print-secret 2>/dev/null | tail -n1 | tr -d '[:space:]') || true
  case "$WHSEC" in whsec_*) ;; *) die "The Stripe CLI did not return a webhook secret. Check that the test secret key is right." ;; esac

  umask 077
  cat > .env <<EOF
GRIDSIM_ENV='production'
APP_URL='http://localhost:8000'
SUPABASE_URL='$SUPABASE_URL'
SUPABASE_ANON_KEY='$SUPABASE_ANON_KEY'
DATABASE_URL='$DBURL'
GRIDSIM_SECRET_KEY='$FERNET'
STRIPE_SECRET_KEY='$SK'
STRIPE_WEBHOOK_SECRET='$WHSEC'
STRIPE_PRICE_MONTHLY='$PRICE_MONTHLY'
STRIPE_PRICE_SEASON='$PRICE_SEASON'
STRIPE_TRIAL_DAYS='0'
GRIDSIM_ADMIN_EMAILS='$ADMIN_EMAIL'
GRIDSIM_REFRESH_HOURS='0'
GRIDSIM_GOOGLE='1'
EOF
  echo "Wrote .env (only you can read it; git ignores it)."
fi

if [ "$NO_RUN" = 1 ]; then say "5/5  Setup done. Start it with: bash scripts/paid_local.sh"; exit 0; fi

say "5/5  Starting GridSim at http://localhost:8000   (Ctrl+C stops it)"
set -a; . ./.env; set +a
"$STRIPE" listen --api-key "$STRIPE_SECRET_KEY" --forward-to localhost:8000/api/stripe/webhook >/tmp/gridsim-stripe-listen.log 2>&1 &
LISTEN_PID=$!
trap 'kill "$LISTEN_PID" 2>/dev/null || true' EXIT
echo "Stripe webhooks are being forwarded (log: /tmp/gridsim-stripe-listen.log)."
python server.py
