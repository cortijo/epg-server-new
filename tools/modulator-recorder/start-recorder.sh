#!/usr/bin/env bash
set -Eeuo pipefail

: "${RECORDER_TARGET_URL:?Defina RECORDER_TARGET_URL}"
: "${RECORDER_PASSWORD:?Defina RECORDER_PASSWORD}"

mkdir -p "$RECORDER_DIR"
chmod 0770 "$RECORDER_DIR"
password_file="$RECORDER_DIR/.vnc-passwd"
x11vnc -storepasswd "$RECORDER_PASSWORD" "$password_file" >/dev/null
chmod 0600 "$password_file"

cleanup() {
  jobs -pr | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

Xvfb "$DISPLAY" -screen 0 1600x1000x24 -nolisten tcp &
fluxbox >/tmp/fluxbox.log 2>&1 &
x11vnc -display "$DISPLAY" -rfbauth "$password_file" -forever -shared \
  -localhost -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc "$RECORDER_PORT" localhost:5900 \
  >/tmp/websockify.log 2>&1 &

for _ in $(seq 1 30); do
  if bash -c '</dev/tcp/127.0.0.1/5900' 2>/dev/null; then break; fi
  sleep 1
done

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output="$RECORDER_DIR/recording-$timestamp.spec.js"
printf 'Gravador Playwright iniciado. Saída: %s\n' "$output"
npx --yes playwright@1.55.0 codegen --ignore-https-errors --viewport-size=1500,900 \
  --output="$output" "$RECORDER_TARGET_URL"
