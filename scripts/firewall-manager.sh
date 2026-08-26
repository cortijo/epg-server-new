#!/usr/bin/env bash
set -Eeuo pipefail
shopt -s extglob

PROGRAM_NAME="epg-firewall"
TABLE_FAMILY="inet"
TABLE_NAME="epg_managed"
CONFIG_FILE="${EPG_FIREWALL_CONFIG:-/etc/epg-firewall.conf}"
RULES_FILE="${EPG_FIREWALL_RULES:-/etc/epg-firewall.nft}"
INSTALL_PATH="${EPG_FIREWALL_INSTALL_PATH:-/usr/local/sbin/epg-firewall}"
UNIT_FILE="${EPG_FIREWALL_UNIT_FILE:-/etc/systemd/system/epg-firewall.service}"
PYTHON_BIN="${EPG_FIREWALL_PYTHON:-python3}"
DRY_RUN=0
FORCE=0

NETWORKS=()
TCP_PORTS=()
UDP_PORTS=()

die() {
  printf 'ERRO: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '%s\n' "$*"
}

usage() {
  cat <<'EOF'
Uso: epg-firewall [opções] COMANDO [argumentos]

Opções:
  --config ARQUIVO   usa outro arquivo de configuração
  --rules ARQUIVO    usa outro arquivo para as regras renderizadas
  --dry-run          valida e mostra o que seria feito, sem alterar o host
  --force            permite apply somente quando executado por console local
  -h, --help         mostra esta ajuda

Comandos:
  init                         cria uma configuração inicial segura
  list                         mostra redes e portas cadastradas
  network add CIDR             adiciona rede IPv4 ou IPv6
  network remove CIDR          remove rede IPv4 ou IPv6
  port add tcp|udp PORTA       adiciona porta ou intervalo (ex.: 9100, 5000-5010)
  port remove tcp|udp PORTA    remove porta ou intervalo
  check                        valida configuração e proteção da sessão SSH
  render                       imprime a tabela nftables gerada
  apply                        valida e aplica a tabela administrada
  status                       mostra a tabela efetivamente carregada
  disable                      remove somente a tabela administrada
  install                      instala script e unit systemd; não aplica regras

O utilitário nunca limpa o conjunto global de regras, não altera FORWARD/NAT e
não remove tabelas do Docker. Aplique inicialmente com uma sessão de console do
provedor disponível para rollback.
EOF
}

trim() {
  local value="$1"
  value="${value##+([[:space:]])}"
  value="${value%%+([[:space:]])}"
  printf '%s' "$value"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Comando obrigatório não encontrado: $1"
}

require_root() {
  [[ ${EUID:-$(id -u)} -eq 0 ]] || die "Execute este comando como root (sudo)."
}

canonical_network() {
  local value="$1"
  "$PYTHON_BIN" - "$value" <<'PY'
import ipaddress
import sys

try:
    print(ipaddress.ip_network(sys.argv[1], strict=False))
except ValueError as error:
    print(f"CIDR inválido: {error}", file=sys.stderr)
    raise SystemExit(1)
PY
}

canonical_port() {
  local value="$1" first last
  if [[ "$value" =~ ^([0-9]{1,5})$ ]]; then
    first="${BASH_REMATCH[1]}"
    (( first >= 1 && first <= 65535 )) || return 1
    printf '%d' "$first"
    return
  fi
  if [[ "$value" =~ ^([0-9]{1,5})-([0-9]{1,5})$ ]]; then
    first="${BASH_REMATCH[1]}"
    last="${BASH_REMATCH[2]}"
    (( first >= 1 && last <= 65535 && first < last )) || return 1
    printf '%d-%d' "$first" "$last"
    return
  fi
  return 1
}

sort_unique_arrays() {
  if ((${#NETWORKS[@]})); then
    mapfile -t NETWORKS < <(printf '%s\n' "${NETWORKS[@]}" | LC_ALL=C sort -u)
  fi
  if ((${#TCP_PORTS[@]})); then
    mapfile -t TCP_PORTS < <(printf '%s\n' "${TCP_PORTS[@]}" | LC_ALL=C sort -u)
  fi
  if ((${#UDP_PORTS[@]})); then
    mapfile -t UDP_PORTS < <(printf '%s\n' "${UDP_PORTS[@]}" | LC_ALL=C sort -u)
  fi
}

load_config() {
  local line key value normalized line_number=0
  NETWORKS=()
  TCP_PORTS=()
  UDP_PORTS=()
  [[ -f "$CONFIG_FILE" ]] || die "Configuração inexistente: $CONFIG_FILE. Execute '$PROGRAM_NAME init'."
  require_command "$PYTHON_BIN"
  while IFS= read -r line || [[ -n "$line" ]]; do
    ((line_number += 1))
    line="$(trim "$line")"
    [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
    [[ "$line" == *=* ]] || die "Linha $line_number inválida em $CONFIG_FILE"
    key="$(trim "${line%%=*}")"
    value="$(trim "${line#*=}")"
    [[ -n "$value" ]] || die "Valor vazio na linha $line_number de $CONFIG_FILE"
    case "$key" in
      NETWORK)
        normalized="$(canonical_network "$value")" || die "Rede inválida na linha $line_number"
        NETWORKS+=("$normalized")
        ;;
      TCP_PORT)
        normalized="$(canonical_port "$value")" || die "Porta TCP inválida na linha $line_number: $value"
        TCP_PORTS+=("$normalized")
        ;;
      UDP_PORT)
        normalized="$(canonical_port "$value")" || die "Porta UDP inválida na linha $line_number: $value"
        UDP_PORTS+=("$normalized")
        ;;
      *) die "Chave desconhecida na linha $line_number: $key" ;;
    esac
  done < "$CONFIG_FILE"
  sort_unique_arrays
}

write_config() {
  local directory temporary value
  directory="$(dirname "$CONFIG_FILE")"
  mkdir -p "$directory"
  temporary="$(mktemp "$directory/.epg-firewall.conf.XXXXXX")"
  {
    printf '# Gerenciado por %s. Uma entrada por linha.\n' "$PROGRAM_NAME"
    for value in "${NETWORKS[@]}"; do printf 'NETWORK=%s\n' "$value"; done
    for value in "${TCP_PORTS[@]}"; do printf 'TCP_PORT=%s\n' "$value"; done
    for value in "${UDP_PORTS[@]}"; do printf 'UDP_PORT=%s\n' "$value"; done
  } > "$temporary"
  chmod 0600 "$temporary"
  mv -f "$temporary" "$CONFIG_FILE"
}

array_contains() {
  local needle="$1" item
  shift
  for item in "$@"; do [[ "$item" == "$needle" ]] && return 0; done
  return 1
}

port_is_allowed() {
  local target="$1" item first last
  shift
  for item in "$@"; do
    if [[ "$item" == *-* ]]; then
      first="${item%-*}"
      last="${item#*-}"
      (( target >= first && target <= last )) && return 0
    elif (( target == item )); then
      return 0
    fi
  done
  return 1
}

network_contains_ip() {
  local address="$1"
  shift
  "$PYTHON_BIN" - "$address" "$@" <<'PY'
import ipaddress
import sys

address = ipaddress.ip_address(sys.argv[1])
raise SystemExit(0 if any(address in ipaddress.ip_network(item, strict=False) for item in sys.argv[2:]) else 1)
PY
}

validate_nonempty_policy() {
  ((${#NETWORKS[@]})) || die "Cadastre ao menos uma NETWORK antes de aplicar."
  ((${#TCP_PORTS[@]} + ${#UDP_PORTS[@]})) || die "Cadastre ao menos uma porta TCP ou UDP antes de aplicar."
}

validate_ssh_session() {
  local require_console_confirmation="${1:-0}"
  local client_ip server_port _client_port _server_ip
  if [[ -z "${SSH_CONNECTION:-}" ]]; then
    if (( require_console_confirmation && FORCE == 0 )); then
      die "Sessão SSH não detectada. No console local, repita com --force após revisar a configuração."
    fi
    (( require_console_confirmation )) || info "Aviso: sessão SSH não detectada; a proteção de IP remoto não se aplica neste check."
    return 0
  fi
  read -r client_ip _client_port _server_ip server_port <<< "$SSH_CONNECTION"
  [[ -n "$client_ip" && "$server_port" =~ ^[0-9]+$ ]] || die "SSH_CONNECTION inválida; aplicação recusada."
  port_is_allowed "$server_port" "${TCP_PORTS[@]}" || \
    die "A porta SSH atual ($server_port/tcp) não está autorizada. Ajuste a configuração antes de aplicar."
  network_contains_ip "$client_ip" "${NETWORKS[@]}" || \
    die "O IP da sessão SSH atual não pertence às redes autorizadas. Ajuste a configuração antes de aplicar."
  (( FORCE == 0 )) || die "--force não é permitido dentro de uma sessão SSH; use o console local."
}

nft_elements() {
  local value separator=""
  for value in "$@"; do
    printf '%s%s' "$separator" "$value"
    separator=", "
  done
}

render_set() {
  local name="$1" type="$2"
  shift 2
  printf '    set %s {\n' "$name"
  printf '        type %s\n' "$type"
  printf '        flags interval\n'
  if (($#)); then
    printf '        elements = { '
    nft_elements "$@"
    printf ' }\n'
  fi
  printf '    }\n\n'
}

render_rules() {
  local replace_existing="${1:-0}" value
  local ipv4=() ipv6=()
  for value in "${NETWORKS[@]}"; do
    if [[ "$value" == *:* ]]; then ipv6+=("$value"); else ipv4+=("$value"); fi
  done
  if (( replace_existing )); then
    printf 'delete table %s %s\n\n' "$TABLE_FAMILY" "$TABLE_NAME"
  fi
  printf 'table %s %s {\n' "$TABLE_FAMILY" "$TABLE_NAME"
  render_set allowed_ipv4 ipv4_addr "${ipv4[@]}"
  render_set allowed_ipv6 ipv6_addr "${ipv6[@]}"
  render_set allowed_tcp_ports inet_service "${TCP_PORTS[@]}"
  render_set allowed_udp_ports inet_service "${UDP_PORTS[@]}"
  cat <<'EOF'
    chain input {
        type filter hook input priority -5; policy accept;

        iifname "lo" accept
        ct state established,related accept
        meta l4proto icmp accept
        meta l4proto ipv6-icmp accept

        ip saddr @allowed_ipv4 tcp dport @allowed_tcp_ports accept
        ip6 saddr @allowed_ipv6 tcp dport @allowed_tcp_ports accept
        ip saddr @allowed_ipv4 udp dport @allowed_udp_ports accept
        ip6 saddr @allowed_ipv6 udp dport @allowed_udp_ports accept

        meta l4proto tcp counter drop
        meta l4proto udp counter drop
    }
}
EOF
}

init_config() {
  local client_ip server_port _client_port _server_ip
  [[ ! -e "$CONFIG_FILE" ]] || die "A configuração já existe: $CONFIG_FILE"
  NETWORKS=()
  TCP_PORTS=(22)
  UDP_PORTS=()
  if [[ -n "${SSH_CONNECTION:-}" ]]; then
    read -r client_ip _client_port _server_ip server_port <<< "$SSH_CONNECTION"
    [[ "$client_ip" == *:* ]] && NETWORKS+=("$client_ip/128") || NETWORKS+=("$client_ip/32")
    TCP_PORTS=("$server_port")
  fi
  write_config
  info "Configuração criada em $CONFIG_FILE"
  if ((${#NETWORKS[@]} == 0)); then
    info "Cadastre ao menos uma rede antes de aplicar. Nenhuma regra foi carregada."
  else
    info "A rede e a porta da sessão SSH atual foram cadastradas. Revise antes de aplicar."
  fi
}

list_config() {
  load_config
  printf 'Configuração: %s\n' "$CONFIG_FILE"
  printf 'Redes autorizadas:\n'
  ((${#NETWORKS[@]})) && printf '  %s\n' "${NETWORKS[@]}" || printf '  (nenhuma)\n'
  printf 'Portas TCP:\n'
  ((${#TCP_PORTS[@]})) && printf '  %s\n' "${TCP_PORTS[@]}" || printf '  (nenhuma)\n'
  printf 'Portas UDP:\n'
  ((${#UDP_PORTS[@]})) && printf '  %s\n' "${UDP_PORTS[@]}" || printf '  (nenhuma)\n'
}

change_network() {
  local action="$1" requested="$2" normalized
  load_config
  normalized="$(canonical_network "$requested")" || die "CIDR inválido: $requested"
  case "$action" in
    add)
      array_contains "$normalized" "${NETWORKS[@]}" || NETWORKS+=("$normalized")
      ;;
    remove)
      local kept=() item
      for item in "${NETWORKS[@]}"; do [[ "$item" != "$normalized" ]] && kept+=("$item"); done
      NETWORKS=("${kept[@]}")
      ;;
    *) die "Ação de rede inválida: $action" ;;
  esac
  sort_unique_arrays
  write_config
  info "Configuração atualizada. Execute '$PROGRAM_NAME check' e '$PROGRAM_NAME apply'."
}

change_port() {
  local action="$1" protocol="${2,,}" requested="$3" normalized item
  local kept=()
  [[ "$protocol" == tcp || "$protocol" == udp ]] || die "Protocolo deve ser tcp ou udp."
  normalized="$(canonical_port "$requested")" || die "Porta ou intervalo inválido: $requested"
  load_config
  local -n ports_ref="${protocol^^}_PORTS"
  case "$action" in
    add)
      array_contains "$normalized" "${ports_ref[@]}" || ports_ref+=("$normalized")
      ;;
    remove)
      for item in "${ports_ref[@]}"; do [[ "$item" != "$normalized" ]] && kept+=("$item"); done
      ports_ref=("${kept[@]}")
      ;;
    *) die "Ação de porta inválida: $action" ;;
  esac
  sort_unique_arrays
  write_config
  info "Configuração atualizada. Execute '$PROGRAM_NAME check' e '$PROGRAM_NAME apply'."
}

check_config() {
  load_config
  validate_nonempty_policy
  validate_ssh_session 0
  info "Configuração válida: ${#NETWORKS[@]} rede(s), ${#TCP_PORTS[@]} entrada(s) TCP e ${#UDP_PORTS[@]} entrada(s) UDP."
}

apply_rules() {
  local directory temporary replace_existing=0
  load_config
  validate_nonempty_policy
  validate_ssh_session 1
  if (( DRY_RUN )); then
    info "Simulação aprovada; nenhuma regra foi alterada."
    render_rules 0
    return
  fi
  require_root
  require_command nft
  directory="$(dirname "$RULES_FILE")"
  mkdir -p "$directory"
  temporary="$(mktemp "$directory/.epg-firewall.nft.XXXXXX")"
  trap 'rm -f "${temporary:-}"' RETURN
  nft list table "$TABLE_FAMILY" "$TABLE_NAME" >/dev/null 2>&1 && replace_existing=1
  render_rules "$replace_existing" > "$temporary"
  nft -c -f "$temporary"
  nft -f "$temporary"
  install -m 0600 "$temporary" "$RULES_FILE"
  if [[ -f "$UNIT_FILE" ]] && command -v systemctl >/dev/null 2>&1; then
    systemctl enable epg-firewall.service >/dev/null
  fi
  info "Firewall atualizado com sucesso. Tabela administrada: $TABLE_FAMILY $TABLE_NAME"
}

show_status() {
  require_command nft
  nft list table "$TABLE_FAMILY" "$TABLE_NAME"
}

disable_rules() {
  require_root
  require_command nft
  if nft list table "$TABLE_FAMILY" "$TABLE_NAME" >/dev/null 2>&1; then
    if (( DRY_RUN )); then
      info "Simulação: removeria table $TABLE_FAMILY $TABLE_NAME"
    else
      nft delete table "$TABLE_FAMILY" "$TABLE_NAME"
      info "Tabela $TABLE_FAMILY $TABLE_NAME removida; outras regras foram preservadas."
    fi
  else
    info "A tabela administrada não está carregada."
  fi
  if (( DRY_RUN == 0 )) && command -v systemctl >/dev/null 2>&1; then
    systemctl disable epg-firewall.service >/dev/null 2>&1 || true
  fi
}

install_manager() {
  local source_path temporary_unit
  require_root
  require_command systemctl
  source_path="$(readlink -f "$0")"
  if [[ "$source_path" != "$(readlink -m "$INSTALL_PATH")" ]]; then
    install -m 0755 "$source_path" "$INSTALL_PATH"
  fi
  if [[ ! -e "$CONFIG_FILE" ]]; then
    init_config
  fi
  temporary_unit="$(mktemp "$(dirname "$UNIT_FILE")/.epg-firewall.service.XXXXXX")"
  cat > "$temporary_unit" <<EOF
[Unit]
Description=EPG managed nftables firewall
After=network-pre.target
Before=network.target docker.service
Wants=network-pre.target

[Service]
Type=oneshot
ExecStart=$INSTALL_PATH --config $CONFIG_FILE --rules $RULES_FILE --force apply
ExecReload=$INSTALL_PATH --config $CONFIG_FILE --rules $RULES_FILE --force apply
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
  install -m 0644 "$temporary_unit" "$UNIT_FILE"
  rm -f "$temporary_unit"
  systemctl daemon-reload
  info "Instalado em $INSTALL_PATH. A unit ainda não foi habilitada."
  info "Revise, execute '$INSTALL_PATH check' e então '$INSTALL_PATH apply'."
}

while (($#)); do
  case "$1" in
    --config) (($# >= 2)) || die "--config exige um arquivo"; CONFIG_FILE="$2"; shift 2 ;;
    --rules) (($# >= 2)) || die "--rules exige um arquivo"; RULES_FILE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    -*) die "Opção desconhecida: $1" ;;
    *) break ;;
  esac
done

command_name="${1:-}"
[[ -n "$command_name" ]] || { usage; exit 1; }
shift

case "$command_name" in
  init) (($# == 0)) || die "init não recebe argumentos"; init_config ;;
  list) (($# == 0)) || die "list não recebe argumentos"; list_config ;;
  network) (($# == 2)) || die "Uso: network add|remove CIDR"; change_network "$1" "$2" ;;
  port) (($# == 3)) || die "Uso: port add|remove tcp|udp PORTA"; change_port "$1" "$2" "$3" ;;
  check) (($# == 0)) || die "check não recebe argumentos"; check_config ;;
  render) (($# == 0)) || die "render não recebe argumentos"; load_config; validate_nonempty_policy; render_rules 0 ;;
  apply) (($# == 0)) || die "apply não recebe argumentos"; apply_rules ;;
  status) (($# == 0)) || die "status não recebe argumentos"; show_status ;;
  disable) (($# == 0)) || die "disable não recebe argumentos"; disable_rules ;;
  install) (($# == 0)) || die "install não recebe argumentos"; install_manager ;;
  *) die "Comando desconhecido: $command_name" ;;
esac
