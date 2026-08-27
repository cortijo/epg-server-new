#!/usr/bin/env bash
set -Eeuo pipefail

NETWORKS_FILE="${EPG_ALLOWED_NETWORKS_FILE:-/opt/redes-liberadas}"
PORTS_FILE="${EPG_ALLOWED_PORTS_FILE:-/opt/portas-liberadas}"
RULES_FILE="${EPG_FIREWALL_RULES_FILE:-/etc/epg-firewall.nft}"
TABLE="epg_firewall"

die() { printf 'ERRO: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "execute como root"
command -v nft >/dev/null || die "nftables não está instalado"
command -v python3 >/dev/null || die "python3 não está instalado"
[[ -f "$NETWORKS_FILE" ]] || die "arquivo ausente: $NETWORKS_FILE"
[[ -f "$PORTS_FILE" ]] || die "arquivo ausente: $PORTS_FILE"

mapfile -t networks < <(sed 's/#.*//;s/[[:space:]]//g;/^$/d' "$NETWORKS_FILE")
mapfile -t ports < <(sed 's/#.*//;s/[[:space:]]//g;/^$/d' "$PORTS_FILE")
(( ${#networks[@]} > 0 )) || die "cadastre ao menos uma rede"
(( ${#ports[@]} > 0 )) || die "cadastre ao menos uma porta"

ipv4=(); ipv6=()
for network in "${networks[@]}"; do
  version="$(python3 - "$network" <<'PY'
import ipaddress, sys
try:
    print(ipaddress.ip_network(sys.argv[1], strict=False).version)
except ValueError:
    raise SystemExit(1)
PY
)" || die "rede inválida: $network"
  [[ "$version" == 4 ]] && ipv4+=("$network") || ipv6+=("$network")
done

for port in "${ports[@]}"; do
  [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 )) \
    || die "porta inválida: $port"
done

if [[ -n "${SSH_CONNECTION:-}" ]]; then
  read -r ssh_client _ _ ssh_port <<<"$SSH_CONNECTION"
  [[ "$ssh_port" =~ ^[0-9]+$ ]] || die "não foi possível determinar a porta SSH"
  printf '%s\n' "${ports[@]}" | grep -Fxq "$ssh_port" \
    || die "a porta SSH atual ($ssh_port) não está autorizada"
  python3 - "$ssh_client" "${networks[@]}" <<'PY' \
    || die "o cliente SSH atual não pertence às redes autorizadas"
import ipaddress, sys
client = ipaddress.ip_address(sys.argv[1])
raise SystemExit(0 if any(client in ipaddress.ip_network(item, strict=False)
                          for item in sys.argv[2:]) else 1)
PY
fi

join_by_comma() { local IFS=,; printf '%s' "$*"; }
ports_csv="$(join_by_comma "${ports[@]}")"
tmp="$(mktemp /tmp/epg-firewall.XXXXXX.nft)"
trap 'rm -f "$tmp"' EXIT

{
  echo "destroy table inet $TABLE"
  echo "table inet $TABLE {"
  (( ${#ipv4[@]} )) && echo "  set allowed_v4 { type ipv4_addr; flags interval; elements = { $(join_by_comma "${ipv4[@]}") }; }"
  (( ${#ipv6[@]} )) && echo "  set allowed_v6 { type ipv6_addr; flags interval; elements = { $(join_by_comma "${ipv6[@]}") }; }"
  echo "  chain input {"
  echo "    type filter hook input priority -20; policy drop;"
  echo '    iifname "lo" accept'
  echo '    ct state invalid drop'
  echo '    ct state established,related accept'
  echo '    ip protocol icmp accept'
  echo '    ip6 nexthdr ipv6-icmp accept'
  echo '    udp sport 67 udp dport 68 accept'
  (( ${#ipv4[@]} )) && echo "    ip saddr @allowed_v4 tcp dport { $ports_csv } ct state new accept"
  (( ${#ipv6[@]} )) && echo "    ip6 saddr @allowed_v6 tcp dport { $ports_csv } ct state new accept"
  echo '  }'
  echo '}'
} >"$tmp"

nft -c -f "$tmp" || die "regras inválidas; firewall atual preservado"
install -o root -g root -m 0644 "$tmp" "$RULES_FILE"
nft -f "$RULES_FILE" || die "falha ao carregar as regras"
printf 'Firewall aplicado: portas {%s} restritas a %d rede(s).\n' \
  "$ports_csv" "${#networks[@]}"
nft list table inet "$TABLE"
