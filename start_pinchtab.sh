#!/bin/bash
# start_pinchtab.sh -- configure and start pinchtab for LAN access
#
# Run this on the pinchtab PC (not the robot). It binds pinchtab to 0.0.0.0
# so the Reachy Mini can reach it over the LAN, sets an auth token, and
# starts a headed Chrome instance.
#
# Usage:
#   ./start_pinchtab.sh
#   PINCHTAB_TOKEN=mysecret ./start_pinchtab.sh
#   ALLOWED_DOMAINS="*" ./start_pinchtab.sh

set -euo pipefail

TOKEN="${PINCHTAB_TOKEN:-$(openssl rand -hex 16)}"
DISPLAY="${DISPLAY:-:0}"
export DISPLAY

echo "=== configuring pinchtab for LAN access ==="
echo "  bind: 0.0.0.0 (all interfaces)"
echo "  token: $TOKEN"
echo "  display: $DISPLAY"
echo ""

pinchtab config set server.bind 0.0.0.0
pinchtab config set server.token "$TOKEN"

# Allowed domains: default to everything since this is a LAN setup.
# Override with ALLOWED_DOMAINS="reachy-mini.local,example.com" to restrict.
ALLOWED_DOMAINS="${ALLOWED_DOMAINS:-*}"
pinchtab config set security.allowedDomains "$ALLOWED_DOMAINS"

echo "=== starting pinchtab server ==="
# Lower rate limit since we're exposed on the LAN.
PINCHTAB_RATE_LIMIT_MAX=300 pinchtab server &
SERVER_PID=$!
sleep 2

echo ""
echo "=== starting headed Chrome instance ==="
pinchtab instance start --mode headed
echo ""

echo "=== pinchtab is running ==="
echo "  server PID: $SERVER_PID"
echo "  API: http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'this-pc'):9867"
echo "  token: $TOKEN"
echo ""
echo "  Set PINCHTAB_URL and PINCHTAB_TOKEN in the robot's .env:"
echo "    PINCHTAB_URL=http://<this-pc-ip>:9867"
echo "    PINCHTAB_TOKEN=$TOKEN"
echo ""
echo "  Press Ctrl+C to stop."
wait $SERVER_PID
