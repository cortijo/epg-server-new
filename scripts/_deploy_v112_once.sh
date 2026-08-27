#!/usr/bin/env bash
set -Eeuo pipefail

EPG_ADMIN_PASSWORD="${1:?informe a senha administrativa do EPG}"
SOURCE_DIR="${2:-/tmp/epgserver-v112}"
IMAGE="epgserver:v1.12.0-20260827"
CANDIDATE_IMAGE="epgserver:v1.12.0-20260827-candidate"
STAMP="$(date +%Y%m%d-%H%M%S)"
OLD_EPG="epg-stream-pre-v1.12.0-${STAMP}"
APP_BACKUP="/srv/epg-stream-backup-pre-v1.12.0-${STAMP}"
CANDIDATE_DATA="/tmp/epg-v112-candidate-${STAMP}"
CANDIDATE_KEY="epg-license-client-v112-candidate"
SUCCESS=0

rollback() {
  local exit_code=$?
  if [ "$SUCCESS" -eq 0 ] && docker container inspect "$OLD_EPG" >/dev/null 2>&1; then
    echo "Falha no deploy; restaurando o container EPG anterior" >&2
    docker rm -f epg-stream >/dev/null 2>&1 || true
    docker rename "$OLD_EPG" epg-stream >/dev/null 2>&1 || true
    docker start epg-stream >/dev/null 2>&1 || true
  fi
  exit "$exit_code"
}
trap rollback ERR INT TERM

test -f "$SOURCE_DIR/epg-product/Dockerfile"
pre_emitters="$(docker top epg-stream | grep -c TVStreamEpgOnly || true)"
test "$pre_emitters" -gt 0

docker build -f "$SOURCE_DIR/epg-product/Dockerfile" -t "$CANDIDATE_IMAGE" "$SOURCE_DIR"

docker run --rm --user 0 --entrypoint sh \
  -v /srv/epg-stream:/from:ro -v "$CANDIDATE_DATA:/to" \
  "$CANDIDATE_IMAGE" -c 'cp -a /from/. /to/'
docker run --rm --user 0 --entrypoint python3 \
  -v "$CANDIDATE_DATA:/data" "$CANDIDATE_IMAGE" -c \
  'import json; p="/data/epg-product.json"; d=json.load(open(p)); [c.__setitem__("auto_start",False) for c in d.get("carriers",[])]; json.dump(d,open(p,"w"),ensure_ascii=False,separators=(",",":"));'
docker run --rm --user 0 --entrypoint sh -v "$CANDIDATE_DATA:/data" \
  "$CANDIDATE_IMAGE" -c 'chown -R 10001:10001 /data'

docker volume rm "$CANDIDATE_KEY" >/dev/null 2>&1 || true
docker volume create "$CANDIDATE_KEY" >/dev/null
docker run --rm --user 0 --entrypoint sh \
  -v epg-license-client-secret:/from:ro -v "$CANDIDATE_KEY:/to" \
  "$CANDIDATE_IMAGE" -c 'cp -a /from/. /to/; chown -R 10001:10001 /to; chmod 700 /to; chmod 600 /to/license.key'

docker rm -f epg-v112-candidate epg-v112-invalid >/dev/null 2>&1 || true
docker run -d --name epg-v112-candidate --network host --restart=no \
  --user 10001:10001 \
  -e EPG_DATA_DIR=/data -e EPG_HTTP_HOST=127.0.0.1 -e EPG_HTTP_PORT=19112 \
  -e EPG_EMITTER_BINARY=/app/TVStreamEpgOnly \
  -e EPG_LICENSE_SERVER_URL=http://127.0.0.1:9200 \
  -e EPG_LICENSE_KEY_FILE=/license/license.key \
  -e EPG_LICENSE_INSTALLATION_ID=portonet-epg-181233106046 \
  -e EPG_LICENSE_CHECK_SECONDS=10 \
  -v "$CANDIDATE_DATA:/data" -v "$CANDIDATE_KEY:/license" \
  "$CANDIDATE_IMAGE" >/dev/null

for _ in $(seq 1 20); do
  curl -fsS -u "epgadmin:$EPG_ADMIN_PASSWORD" http://127.0.0.1:19112/api/license >/dev/null && break
  sleep 1
done
curl -fsS -u "epgadmin:$EPG_ADMIN_PASSWORD" http://127.0.0.1:19112/api/license |
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["valid"]'
test "$(docker top epg-v112-candidate | grep -c TVStreamEpgOnly || true)" = 0
curl -fsS -u "epgadmin:$EPG_ADMIN_PASSWORD" -H 'Content-Type: application/json' \
  --data '{}' http://127.0.0.1:19112/api/carriers/restart-all |
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["result"]=="ok" and d["restarted"]==0'

docker run -d --name epg-v112-invalid --network host --restart=no \
  --user 10001:10001 \
  -e EPG_DATA_DIR=/data -e EPG_HTTP_HOST=127.0.0.1 -e EPG_HTTP_PORT=19113 \
  -e EPG_EMITTER_BINARY=/app/TVStreamEpgOnly \
  -e EPG_LICENSE_SERVER_URL=http://127.0.0.1:9200 \
  -e EPG_LICENSE_KEY_FILE=/license/missing.key \
  -e EPG_LICENSE_INSTALLATION_ID=portonet-epg-v112-invalid \
  -e EPG_LICENSE_CHECK_SECONDS=10 \
  -v "$CANDIDATE_DATA:/data" "$CANDIDATE_IMAGE" >/dev/null
sleep 2
test "$(curl -sS -o /tmp/v112-state.json -w '%{http_code}' -u "epgadmin:$EPG_ADMIN_PASSWORD" http://127.0.0.1:19113/api/state)" = 200
test "$(curl -sS -o /tmp/v112-sources.json -w '%{http_code}' -u "epgadmin:$EPG_ADMIN_PASSWORD" http://127.0.0.1:19113/api/sources)" = 402
test "$(curl -sS -o /tmp/v112-restart.json -w '%{http_code}' -u "epgadmin:$EPG_ADMIN_PASSWORD" -H 'Content-Type: application/json' --data '{}' http://127.0.0.1:19113/api/carriers/restart-all)" = 402
curl -fsS -u "epgadmin:$EPG_ADMIN_PASSWORD" http://127.0.0.1:19113/ |
  grep -q 'Licença inválida, entre em contato com o suporte'
test "$(docker top epg-v112-invalid | grep -c TVStreamEpgOnly || true)" = 0
docker rm -f epg-v112-invalid >/dev/null

docker tag "$CANDIDATE_IMAGE" "$IMAGE"
docker run --rm --user 0 --entrypoint sh \
  -v /srv/epg-stream:/from:ro -v "$APP_BACKUP:/to" \
  "$IMAGE" -c 'cp -a /from/. /to/'
docker rename epg-stream "$OLD_EPG"
docker update --restart=no "$OLD_EPG" >/dev/null
docker stop "$OLD_EPG" >/dev/null
docker run -d --name epg-stream --network host --restart unless-stopped \
  --user 10001:10001 \
  -e EPG_DATA_DIR=/data -e EPG_HTTP_HOST=0.0.0.0 -e EPG_HTTP_PORT=9100 \
  -e EPG_EMITTER_BINARY=/app/TVStreamEpgOnly \
  -e EPG_LICENSE_SERVER_URL=http://127.0.0.1:9200 \
  -e EPG_LICENSE_KEY_FILE=/license/license.key \
  -e EPG_LICENSE_INSTALLATION_ID=portonet-epg-181233106046 \
  -e EPG_LICENSE_CHECK_SECONDS=60 \
  -v /srv/epg-stream:/data -v epg-license-client-secret:/license \
  "$IMAGE" >/dev/null

for _ in $(seq 1 30); do
  curl -fsS -u "epgadmin:$EPG_ADMIN_PASSWORD" http://127.0.0.1:9100/api/license >/dev/null && break
  sleep 1
done
sleep 4
post_emitters="$(docker top epg-stream | grep -c TVStreamEpgOnly || true)"
test "$post_emitters" = "$pre_emitters"
curl -fsS -u "epgadmin:$EPG_ADMIN_PASSWORD" http://127.0.0.1:9100/api/license |
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["valid"] and d["channel_count"]<=d["max_channels"]; print("license_valid=yes channels=%s/%s"%(d["channel_count"],d["max_channels"]))'
curl -fsS -u "epgadmin:$EPG_ADMIN_PASSWORD" http://127.0.0.1:9100/ |
  grep -q 'Reiniciar todos os fluxos'
test "$(docker inspect -f '{{.RestartCount}}' epg-stream)" = 0
! docker logs --since 3m epg-stream 2>&1 | grep -Eiq 'traceback|exception|fatal'

SUCCESS=1
trap - ERR INT TERM
printf 'deploy=ok image=%s emitters=%s rollback=%s backup=%s candidate=%s\n' \
  "$IMAGE" "$post_emitters" "$OLD_EPG" "$APP_BACKUP" epg-v112-candidate
