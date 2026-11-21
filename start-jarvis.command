#!/bin/bash
# Double-click on a Mac: starts JARVIS (live prices, paper book), the moomoo bridge and the dashboard.
#
# First run: makes your own password, phone-code secret and bridge token, keeps them in .jarvis-secrets
# (only you can read it), and shows the phone-code secret once so you can add it to Google Authenticator.
# The bridge trades moomoo's PAPER account. For real money, open .jarvis-secrets, set REAL_MONEY=yes and
# your MOOMOO_TRADE_PASSWORD, and start again.
cd "$(dirname "$0")" || exit 1

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "JARVIS needs Python 3.10 or newer. Install it from https://www.python.org/downloads/ and double-click again."
  read -r -p "Press Enter to close." _; exit 1
fi
# Its own Python environment, so installing moomoo never touches the Mac's system Python.
[ -x .venv/bin/python ] || python3 -m venv .venv
PY=.venv/bin/python

SECRETS=.jarvis-secrets
if [ ! -f "$SECRETS" ]; then
  umask 077
  TOTP=$($PY -m trading.webauth new-totp | sed -n 's/^DASHBOARD_TOTP_SECRET=//p')
  cat > "$SECRETS" <<EOF
DASHBOARD_PASSWORD=$($PY -c 'import secrets; print(secrets.token_urlsafe(12))')
SESSION_SECRET=$($PY -c 'import secrets; print(secrets.token_hex(32))')
DASHBOARD_TOTP_SECRET=$TOTP
BRIDGE_TOKEN=$($PY -c 'import secrets; print(secrets.token_urlsafe(32))')
LIVE_MAX_ORDER_USD=200
LIVE_MAX_DAY_USD=500
LIVE_SYMBOLS=AAPL,MSFT,NVDA
LIVE_MAX_POSITIONS=2
LIVE_MAX_ORDERS_DAY=3
REAL_MONEY=no
MOOMOO_TRADE_PASSWORD=
EOF
  echo "================ FIRST-TIME SETUP ================"
  echo "Your dashboard password:  $(sed -n 's/^DASHBOARD_PASSWORD=//p' $SECRETS)"
  echo "Phone-code secret (type it into Google Authenticator or Authy, 'enter a setup key'):"
  echo "    $TOTP"
  echo "Both are saved in $(pwd)/$SECRETS"
  echo "=================================================="
  read -r -p "Press Enter once the code is in your authenticator app." _
fi
set -a; . "./$SECRETS"; set +a

$PY -c 'import moomoo' 2>/dev/null || $PY -m pip install --quiet moomoo-api || echo "Could not install moomoo-api: the bridge won't start, JARVIS still will."

mkdir -p events
DASHBOARD_HOST=127.0.0.1 DASHBOARD_AUTH=force EVENTS_DIR=./events MARKET=live PORT=8080 \
  $PY -m trading.cloud > events/app.log 2>&1 &
APP=$!
trap 'kill $APP $BRIDGE 2>/dev/null' EXIT

echo "Starting JARVIS..."
for _ in $(seq 1 60); do
  curl -s -o /dev/null http://127.0.0.1:8080/health && break
  sleep 2
done
open http://127.0.0.1:8080/ 2>/dev/null
echo "JARVIS is running: http://127.0.0.1:8080  (password and phone code from first-time setup)"

if [ "$REAL_MONEY" = "yes" ]; then
  [ -n "$MOOMOO_TRADE_PASSWORD" ] || { echo "Set MOOMOO_TRADE_PASSWORD in $SECRETS for real money."; exit 1; }
  export BRIDGE_REAL_MONEY=I-ACCEPT-THE-RISK
  echo "Bridge: REAL MONEY, max \$$LIVE_MAX_ORDER_USD per order, \$$LIVE_MAX_DAY_USD a day."
else
  echo "Bridge: moomoo PAPER account."
fi
echo "Make sure moomoo OpenD is open and logged in. Keep this window open; close it to stop everything."
BRIDGE_URL=http://127.0.0.1:8080 $PY -m trading.bridge &
BRIDGE=$!
wait $BRIDGE
echo "The bridge stopped (is OpenD open and logged in?). JARVIS keeps running; close this window to stop it."
wait $APP
