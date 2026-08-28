#!/usr/bin/env bash
set -Eeuo pipefail

ADMIN_PASSWORD="${1:?informe a senha administrativa do EPG}"
PROFILE="${2:?informe 181 ou 187}"
SOURCE_DIR="${3:-/tmp/epgserver-v113}"
IMAGE="epgserver:v1.13.0-20260828"
CANDIDATE="${IMAGE}-candidate"
STAMP="$(date +%Y%m%d-%H%M%S)"
OLD="epg-stream-pre-v1.13.0-${STAMP}"
SUCCESS=0

case "$PROFILE" in
  181)
    LICENSE_URL="http://127.0.0.1:9200"
    INSTALLATION_ID="portonet-epg-181233106046"
    CHECK_SECONDS=60
    LICENSE_MOUNT="epg-license-client-secret:/license"
    ;;
  187)
    LICENSE_URL="http://181.233.106.46:9200"
    INSTALLATION_ID="epgbr-187019016059"
    CHECK_SECONDS=43200
    LICENSE_MOUNT="/srv/epg-license-client:/license"
    ;;
  *) echo "perfil inválido: $PROFILE" >&2; exit 2 ;;
esac

rollback() {
  local code=$?
  if [ "$SUCCESS" -eq 0 ] && docker container inspect "$OLD" >/dev/null 2>&1; then
    docker rm -f epg-stream >/dev/null 2>&1 || true
    docker rename "$OLD" epg-stream >/dev/null 2>&1 || true
    docker start epg-stream >/dev/null 2>&1 || true
  fi
  exit "$code"
}
trap rollback ERR INT TERM

test -f "$SOURCE_DIR/epg-product/Dockerfile"
pre_emitters="$(docker top epg-stream | grep -c TVStreamEpgOnly || true)"
test "$pre_emitters" -gt 0
docker build -f "$SOURCE_DIR/epg-product/Dockerfile" -t "$CANDIDATE" "$SOURCE_DIR"
docker run --rm -e PYTHONPYCACHEPREFIX=/tmp/pycache --entrypoint python3 \
  "$CANDIDATE" -m py_compile /app/app.py /app/verify_isdbtb_ts.py
docker tag "$CANDIDATE" "$IMAGE"

docker rename epg-stream "$OLD"
docker update --restart=no "$OLD" >/dev/null
docker stop "$OLD" >/dev/null
docker run -d --name epg-stream --network host --restart unless-stopped \
  --user 10001:10001 \
  -e EPG_DATA_DIR=/data -e EPG_HTTP_HOST=0.0.0.0 -e EPG_HTTP_PORT=9100 \
  -e EPG_EMITTER_BINARY=/app/TVStreamEpgOnly \
  -e EPG_LICENSE_SERVER_URL="$LICENSE_URL" \
  -e EPG_LICENSE_KEY_FILE=/license/license.key \
  -e EPG_LICENSE_INSTALLATION_ID="$INSTALLATION_ID" \
  -e EPG_LICENSE_CHECK_SECONDS="$CHECK_SECONDS" \
  -v /srv/epg-stream:/data -v "$LICENSE_MOUNT" \
  "$IMAGE" >/dev/null

for _ in $(seq 1 40); do
  curl -fsS http://127.0.0.1:9100/health >/tmp/epg-v113-health.json && break
  sleep 1
done
python3 - <<'PY'
import json
d=json.load(open('/tmp/epg-v113-health.json'))
assert d['version']=='1.13.0', d['version']
PY
sleep 5
post_emitters="$(docker top epg-stream | grep -c TVStreamEpgOnly || true)"
test "$post_emitters" = "$pre_emitters"
if [ "$PROFILE" = 181 ]; then
  curl -fsS -u "epgadmin:$ADMIN_PASSWORD" http://127.0.0.1:9100/api/state >/tmp/epg-v113-state.json
  carrier_id="$(python3 - <<'PY'
import json
d=json.load(open('/tmp/epg-v113-state.json'))
assert d['license']['valid'], d['license']
print(next(c['id'] for c in d['carriers'] if c.get('active')))
PY
)"
  curl -fsS -u "epgadmin:$ADMIN_PASSWORD" -H 'Content-Type: application/json' \
    --data "{\"id\":\"$carrier_id\",\"seconds\":8}" \
    http://127.0.0.1:9100/api/carriers/audit >/tmp/epg-v113-audit.json
else
  read -r carrier_id tsid onid service_ids pmt_pids < <(docker exec epg-stream python3 -c '
import json
d=json.load(open("/data/epg-product.json"))
c=next(x for x in d["carriers"] if x.get("auto_start"))
print(c["id"], c["transport_stream_id"], c["original_network_id"],
      ",".join(str(s["service_id"]) for s in c["services"]),
      ",".join(str(c["pmt_pid"]+i) for i,_ in enumerate(c["services"])))')
  docker exec epg-stream python3 -c \
    'from pathlib import Path; import sys; Path("/data/diagnostics",sys.argv[1]+".request").write_text("8")' \
    "$carrier_id"
  sleep 11
  audit_args=(--tsid "$tsid" --onid "$onid" --epg-only)
  IFS=, read -ra services <<<"$service_ids"
  IFS=, read -ra pmts <<<"$pmt_pids"
  for sid in "${services[@]}"; do audit_args+=(--service-id "$sid"); done
  for pid in "${pmts[@]}"; do audit_args+=(--pmt-pid "$pid"); done
  docker exec epg-stream python3 /app/verify_isdbtb_ts.py \
    "/data/diagnostics/$carrier_id.ts" "${audit_args[@]}" >/tmp/epg-v113-audit.json
fi
python3 - <<'PY'
import json
d=json.load(open('/tmp/epg-v113-audit.json'))
assert d['ok'], d.get('errors')
assert d['packet_count'] > 0
assert d['crc_errors'] == 0
assert d['repeated_synopsis_prefixes'] == 0
assert d['pid_packets'].get('0x0012', 0) > 0
assert d['pid_packets'].get('0x0014', 0) > 0
assert d['eit_present_following_events']
print('audit=ok packets=%s events=%s' % (d['packet_count'], len(d['eit_present_following_events'])))
PY
test "$(docker inspect -f '{{.RestartCount}}' epg-stream)" = 0
! docker logs --since 3m epg-stream 2>&1 | grep -Eiq 'traceback|exception|fatal'

SUCCESS=1
trap - ERR INT TERM
printf 'deploy=ok profile=%s image=%s emitters=%s rollback=%s\n' "$PROFILE" "$IMAGE" "$post_emitters" "$OLD"
