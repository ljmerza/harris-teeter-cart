#!/bin/bash
set -e

# Headful Camoufox (less detectable than headless) needs an X display.
# Started directly, as in lowedepot: xvfb-run's readiness wait can hang here.
export DISPLAY=:99
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1366x900x24 -nolisten tcp &
XVFB_PID=$!
for _ in $(seq 1 30); do
    [ -e /tmp/.X11-unix/X99 ] && break
    sleep 0.3
done
trap 'kill $XVFB_PID $APP_PID 2>/dev/null; wait; exit 0' SIGTERM SIGINT

python -m ht_cart &
APP_PID=$!
wait $APP_PID
