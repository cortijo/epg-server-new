#!/usr/bin/env bash
set -Eeuo pipefail
PACKAGE="${1:?Uso: smoke-test.sh /caminho/epg-stream.deb}"
DEBIAN_FRONTEND=noninteractive apt-get install -y "$PACKAGE" >/dev/null
dpkg-query -W -f='package=${Package} version=${Version} status=${Status}\n' epg-stream
test -x /usr/lib/epg-stream/TVStreamEpgOnly
test -x /usr/lib/epg-stream/epg-stream-updater.py
test -x /usr/sbin/epg-stream-configure
test -f /lib/systemd/system/epg-stream.service
test -f /lib/systemd/system/epg-stream-updater.service
test -f /lib/systemd/system/epg-stream-updater.path
test "$(stat -c %a /etc/epg-stream/epg-stream.env)" = 640
install -d -o epgstream -g epgstream /tmp/epg-smoke-data
su -s /bin/bash epgstream -c '
set -Eeuo pipefail
export EPG_DATA_DIR=/tmp/epg-smoke-data EPG_HTTP_HOST=127.0.0.1 EPG_HTTP_PORT=19140
export EPG_EMITTER_BINARY=/usr/lib/epg-stream/TVStreamEpgOnly
export EPG_ADMIN_USER=testadmin EPG_ADMIN_PASSWORD=testpassword123 EPG_INSTALL_MODE=native
python3 /usr/lib/epg-stream/app.py >/tmp/epg-native.log 2>&1 & pid=$!
trap "kill $pid 2>/dev/null || true" EXIT
ready=0
for _ in $(seq 1 20); do
  if python3 -c "import http.client; c=http.client.HTTPConnection(\"127.0.0.1\",19140,timeout=1); c.request(\"GET\",\"/health\"); r=c.getresponse(); b=r.read().decode(); assert r.status in (200,503) and \"1.20.1\" in b"; then ready=1; break; fi
  sleep 1
done
test "$ready" = 1
python3 -c "import base64,http.client; c=http.client.HTTPConnection(\"127.0.0.1\",19140); h={\"Authorization\":\"Basic \"+base64.b64encode(b\"testadmin:testpassword123\").decode()}; c.request(\"GET\",\"/\",headers=h); r=c.getresponse(); b=r.read().decode(); assert r.status==200 and \"Developed by Julio Cortijo\" in b and \"openAbout()\" in b"
kill "$pid"; wait "$pid" || true; trap - EXIT
'
echo native_smoke=ok
