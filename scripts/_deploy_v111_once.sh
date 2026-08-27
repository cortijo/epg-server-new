#!/usr/bin/env bash
set -Eeuo pipefail

LICENSE_ADMIN_PASSWORD="${1:?informe a senha administrativa do servidor de licencas}"
EPG_ADMIN_PASSWORD="${2:?informe a senha administrativa do EPG}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OLD_EPG="epg-stream-pre-v1.11.0-${STAMP}"
OLD_LICENSE="epg-license-server-pre-v1.1.0-${STAMP}"
APP_BACKUP="/srv/epg-stream-backup-pre-v1.11.0-${STAMP}"
LICENSE_BACKUP="epg-license-data-backup-pre-v1.1.0-${STAMP}"
CLIENT_BACKUP="epg-license-client-backup-pre-v1.11.0-${STAMP}"
ROTATE_FILE="$(mktemp /tmp/epg-license-rotate.XXXXXX)"
LIST_FILE="$(mktemp /tmp/epg-license-list.XXXXXX)"
VIEW_FILE="$(mktemp /tmp/epg-license-view.XXXXXX)"
SUCCESS=0

cleanup() {
  rm -f "$ROTATE_FILE" "$LIST_FILE" "$VIEW_FILE"
}

rollback() {
  local exit_code=$?
  cleanup
  if [ "$SUCCESS" -eq 0 ]; then
    echo "Falha no deploy; restaurando containers anteriores" >&2
    docker rm -f epg-stream >/dev/null 2>&1 || true
    if docker container inspect "$OLD_EPG" >/dev/null 2>&1; then
      docker rename "$OLD_EPG" epg-stream >/dev/null 2>&1 || true
      docker start epg-stream >/dev/null 2>&1 || true
    fi
    docker rm -f epg-license-server >/dev/null 2>&1 || true
    if docker container inspect "$OLD_LICENSE" >/dev/null 2>&1; then
      docker rename "$OLD_LICENSE" epg-license-server >/dev/null 2>&1 || true
      docker start epg-license-server >/dev/null 2>&1 || true
    fi
  fi
  exit "$exit_code"
}
trap rollback ERR INT TERM

pre_emitters="$(docker top epg-stream | grep -c TVStreamEpgOnly || true)"
test "$pre_emitters" -gt 0

docker tag epgserver:v1.11.0-20260826-candidate epgserver:v1.11.0-20260826
docker tag epg-license-server:v1.1.0-20260826-candidate epg-license-server:v1.1.0-20260826

docker volume create "$LICENSE_BACKUP" >/dev/null
docker volume create "$CLIENT_BACKUP" >/dev/null
docker volume create epg-license-master-secret >/dev/null

docker run --rm --user 0 --entrypoint sh \
  -v /srv/epg-stream:/from:ro -v "$APP_BACKUP":/to \
  epgserver:v1.11.0-20260826 \
  -c 'cp -a /from/. /to/'
docker run --rm --user 0 --entrypoint sh \
  -v epg-license-data:/from:ro -v "$LICENSE_BACKUP":/to \
  epg-license-server:v1.1.0-20260826 \
  -c 'cp -a /from/. /to/'
docker run --rm --user 0 --entrypoint sh \
  -v epg-license-client-secret:/from:ro -v "$CLIENT_BACKUP":/to \
  epgserver:v1.11.0-20260826 \
  -c 'cp -a /from/. /to/'

docker run --rm --user 0 --entrypoint sh \
  -v epg-license-master-secret:/secret \
  epg-license-server:v1.1.0-20260826 \
  -c 'if [ ! -s /secret/master.key ]; then dd if=/dev/urandom bs=48 count=1 2>/dev/null | base64 > /secret/master.key; fi; chown 10002:10002 /secret/master.key; chmod 600 /secret/master.key'
docker run --rm --user 0 --entrypoint sh \
  -v epg-license-client-secret:/client \
  epgserver:v1.11.0-20260826 \
  -c 'chown 10001:10001 /client /client/license.key; chmod 700 /client; chmod 600 /client/license.key'

docker rename epg-license-server "$OLD_LICENSE"
docker stop "$OLD_LICENSE" >/dev/null
docker run -d --name epg-license-server --network host --restart unless-stopped \
  --user 10002:10002 \
  -e LICENSE_DATA_DIR=/data \
  -e LICENSE_HTTP_HOST=0.0.0.0 \
  -e LICENSE_HTTP_PORT=9200 \
  -e LICENSE_ADMIN_USER=licenseadmin \
  -e "LICENSE_ADMIN_PASSWORD=$LICENSE_ADMIN_PASSWORD" \
  -e LICENSE_MASTER_KEY_FILE=/run/secrets/master.key \
  -v epg-license-data:/data \
  -v epg-license-master-secret:/run/secrets:ro \
  epg-license-server:v1.1.0-20260826 >/dev/null

for _ in $(seq 1 20); do
  if curl -fsS -u "licenseadmin:$LICENSE_ADMIN_PASSWORD" http://127.0.0.1:9200/api/licenses > "$LIST_FILE"; then
    break
  fi
  sleep 1
done
test -s "$LIST_FILE"

docker rename epg-stream "$OLD_EPG"
docker stop "$OLD_EPG" >/dev/null
docker run -d --name epg-stream --network host --restart unless-stopped \
  --user 10001:10001 \
  -e EPG_DATA_DIR=/data \
  -e EPG_HTTP_HOST=0.0.0.0 \
  -e EPG_HTTP_PORT=9100 \
  -e EPG_EMITTER_BINARY=/app/TVStreamEpgOnly \
  -e EPG_LICENSE_SERVER_URL=http://127.0.0.1:9200 \
  -e EPG_LICENSE_KEY_FILE=/license/license.key \
  -e EPG_LICENSE_INSTALLATION_ID=portonet-epg-181233106046 \
  -e EPG_LICENSE_CHECK_SECONDS=60 \
  -v /srv/epg-stream:/data \
  -v epg-license-client-secret:/license \
  epgserver:v1.11.0-20260826 >/dev/null

for _ in $(seq 1 30); do
  if curl -fsS -u "epgadmin:$EPG_ADMIN_PASSWORD" http://127.0.0.1:9100/api/license >/dev/null; then
    break
  fi
  sleep 1
done

license_id="$(python3 -c 'import json,sys; items=json.load(open(sys.argv[1]))["licenses"]; matches=[x for x in items if x.get("enabled") and x.get("installation_id")=="portonet-epg-181233106046"]; assert len(matches)==1; print(matches[0]["id"])' "$LIST_FILE")"
curl -fsS -u "licenseadmin:$LICENSE_ADMIN_PASSWORD" \
  -H 'Content-Type: application/json' \
  --data "{\"id\":\"$license_id\"}" \
  http://127.0.0.1:9200/api/licenses/rotate > "$ROTATE_FILE"
key="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["key"])' "$ROTATE_FILE")"
test "${#key}" -ge 40
payload="$(python3 -c 'import json,sys; print(json.dumps({"key":sys.argv[1]}))' "$key")"
curl -fsS -u "epgadmin:$EPG_ADMIN_PASSWORD" \
  -H 'Content-Type: application/json' --data "$payload" \
  http://127.0.0.1:9100/api/license/key >/dev/null
curl -fsS -u "licenseadmin:$LICENSE_ADMIN_PASSWORD" \
  -H 'Content-Type: application/json' \
  --data "{\"id\":\"$license_id\"}" \
  http://127.0.0.1:9200/api/licenses/key > "$VIEW_FILE"
viewed="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["key"])' "$VIEW_FILE")"
test "$key" = "$viewed"

sleep 3
post_emitters="$(docker top epg-stream | grep -c TVStreamEpgOnly || true)"
test "$post_emitters" = "$pre_emitters"
curl -fsS -u "epgadmin:$EPG_ADMIN_PASSWORD" http://127.0.0.1:9100/api/license |
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["valid"] and d["channel_count"] <= d["max_channels"]; print("license_valid=yes channels=%s/%s" % (d["channel_count"], d["max_channels"]))'
docker run --rm --user 0 --entrypoint sh \
  -v epg-license-client-secret:/client:ro -v epg-license-data:/data:ro \
  epg-license-server:v1.1.0-20260826 \
  -c 'key=$(cat /client/license.key); test -n "$key"; ! grep -Fq "$key" /data/licenses.json; stat -c "key_mode=%a owner=%u:%g" /client/license.key'
test "$(docker inspect -f '{{.RestartCount}}' epg-stream)" = 0
test "$(docker inspect -f '{{.RestartCount}}' epg-license-server)" = 0

SUCCESS=1
trap - ERR INT TERM
cleanup
printf 'deploy=ok emitters=%s rollback_epg=%s rollback_license=%s app_backup=%s license_backup=%s client_backup=%s\n' \
  "$post_emitters" "$OLD_EPG" "$OLD_LICENSE" "$APP_BACKUP" "$LICENSE_BACKUP" "$CLIENT_BACKUP"
