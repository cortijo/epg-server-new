#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="${EPG_PACKAGE_VERSION:-1.17.1}"
REVISION="${EPG_PACKAGE_REVISION:-1}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
ARCH="${EPG_PACKAGE_ARCH:-$(dpkg --print-architecture)}"
BUILD_DIR="$(mktemp -d)"
STAGE="$BUILD_DIR/epg-stream"
OUTPUT_DIR="${EPG_PACKAGE_OUTPUT_DIR:-$ROOT/dist}"
trap 'rm -rf -- "$BUILD_DIR"' EXIT

for command in g++ dpkg-deb dpkg; do
  command -v "$command" >/dev/null 2>&1 || { echo "Dependência de build ausente: $command" >&2; exit 1; }
done
case "$ARCH" in amd64|arm64) ;; *) echo "Arquitetura não suportada: $ARCH" >&2; exit 1 ;; esac

mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/lib/epg-stream" "$STAGE/usr/sbin" \
  "$STAGE/lib/systemd/system" "$STAGE/etc/epg-stream" "$OUTPUT_DIR"

g++ -std=c++17 -O2 -DNDEBUG -pthread -I/usr/include/jsoncpp -I"$ROOT/src" \
  "$ROOT/src/EpgOnlyMain.cpp" "$ROOT/src/EpgInjector.cpp" \
  "$ROOT/src/ConfigManager.cpp" "$ROOT/src/utils.cpp" \
  -lboost_system -lboost_thread -lcurl -ljsoncpp \
  -o "$STAGE/usr/lib/epg-stream/TVStreamEpgOnly"

install -m 0755 "$ROOT/epg-product/app.py" "$STAGE/usr/lib/epg-stream/app.py"
install -m 0644 "$ROOT/epg-product/license_client.py" "$STAGE/usr/lib/epg-stream/license_client.py"
install -m 0755 "$ROOT/scripts/verify_isdbtb_ts.py" "$STAGE/usr/lib/epg-stream/verify_isdbtb_ts.py"
install -m 0755 "$SCRIPT_DIR/epg-stream-updater.py" "$STAGE/usr/lib/epg-stream/epg-stream-updater.py"
install -m 0755 "$SCRIPT_DIR/epg-stream-configure" "$STAGE/usr/sbin/epg-stream-configure"
install -m 0644 "$SCRIPT_DIR/epg-stream.service" "$STAGE/lib/systemd/system/epg-stream.service"
install -m 0644 "$SCRIPT_DIR/epg-stream-updater.service" "$STAGE/lib/systemd/system/epg-stream-updater.service"
install -m 0644 "$SCRIPT_DIR/epg-stream-updater.path" "$STAGE/lib/systemd/system/epg-stream-updater.path"
install -m 0640 "$SCRIPT_DIR/epg-stream.env" "$STAGE/etc/epg-stream/epg-stream.env"
install -m 0755 "$SCRIPT_DIR/postinst" "$STAGE/DEBIAN/postinst"
install -m 0755 "$SCRIPT_DIR/prerm" "$STAGE/DEBIAN/prerm"
install -m 0755 "$SCRIPT_DIR/postrm" "$STAGE/DEBIAN/postrm"

cat >"$STAGE/DEBIAN/control" <<EOF
Package: epg-stream
Version: ${VERSION}-${REVISION}
Section: net
Priority: optional
Architecture: ${ARCH}
Maintainer: Julio Cortijo
Depends: adduser, python3, python3-pil, ca-certificates, libboost-system1.83.0, libboost-thread1.83.0, libcurl4t64, libjsoncpp25
Description: Servidor EPG multicast ISDB-TB
 Converte fontes XMLTV em um transporte MPEG-TS auxiliar com EIT, TDT/TOT e
 sinalização ISDB-TB, administrado por painel HTTP e serviço systemd.
EOF
printf '%s\n' '/etc/epg-stream/epg-stream.env' >"$STAGE/DEBIAN/conffiles"

find "$STAGE" -type d -exec chmod 0755 {} +
chmod 0750 "$STAGE/etc/epg-stream"
chmod 0640 "$STAGE/etc/epg-stream/epg-stream.env"
strip --strip-unneeded "$STAGE/usr/lib/epg-stream/TVStreamEpgOnly"

PACKAGE="$OUTPUT_DIR/epg-stream_${VERSION}-${REVISION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$PACKAGE"
dpkg-deb --info "$PACKAGE"
echo "Pacote gerado: $PACKAGE"
