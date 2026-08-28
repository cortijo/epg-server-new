#!/usr/bin/env bash
set -Eeuo pipefail

PRODUCT_VERSION="1.13.1"
DEFAULT_PORT="9100"
DEFAULT_DATA_DIR="/srv/epg-stream"
DEFAULT_CONTAINER="epg-stream"
DEFAULT_IMAGE="epgserver:v${PRODUCT_VERSION}-$(date +%Y%m%d)"
DEFAULT_TIMEZONE="America/Sao_Paulo"
DEFAULT_LICENSE_SERVER_URL="http://127.0.0.1:9200"
DEFAULT_LICENSE_KEY_FILE="/srv/epg-license-client/license.key"
DEFAULT_INSTALLATION_ID="$(hostname 2>/dev/null || printf 'epg-server')-epg"
CONTAINER_UID="10001"
HEALTH_ATTEMPTS="30"
HEALTH_INTERVAL="2"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
TEMP_ENV=""
SUDO=()

cleanup() {
  if [[ -n "${TEMP_ENV}" && -f "${TEMP_ENV}" ]]; then
    rm -f -- "${TEMP_ENV}"
  fi
}
trap cleanup EXIT

info() { printf '\n[INFO] %s\n' "$*"; }
warn() { printf '\n[ATENÇÃO] %s\n' "$*" >&2; }
die() { printf '\n[ERRO] %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Instalador interativo do EPG Server

Uso:
  sudo ./scripts/install.sh
  ./scripts/install.sh --help

O instalador solicita porta HTTP, diretório de dados, nome do container, tag da
imagem, fuso horário, servidor, arquivo da chave e identificador da licença. Em uma instalação nova, também solicita o primeiro
administrador. Ele não altera o firewall do servidor.
EOF
}

confirm() {
  local prompt="$1" answer
  read -r -p "${prompt} [s/N]: " answer
  [[ "${answer,,}" == "s" || "${answer,,}" == "sim" ]]
}

ask_default() {
  local variable="$1" prompt="$2" default="$3" value
  read -r -p "${prompt} [${default}]: " value
  printf -v "${variable}" '%s' "${value:-$default}"
}

prepare_privileges() {
  if [[ "${EUID}" -eq 0 ]]; then
    SUDO=()
    return
  fi
  command -v sudo >/dev/null 2>&1 || die "Execute como root ou instale o sudo."
  info "Validando permissão administrativa..."
  sudo -v || die "Não foi possível obter permissão administrativa."
  SUDO=(sudo)
}

root_run() { "${SUDO[@]}" "$@"; }
docker_run() { "${SUDO[@]}" docker "$@"; }

install_docker_if_needed() {
  if command -v docker >/dev/null 2>&1; then
    docker_run info >/dev/null 2>&1 || die "Docker existe, mas o daemon não está acessível."
    return
  fi

  warn "Docker não foi encontrado."
  confirm "Instalar Docker pelo gerenciador de pacotes" || die "Instale o Docker e execute novamente."
  [[ -r /etc/os-release ]] || die "Distribuição não identificada; instale o Docker manualmente."
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    debian|ubuntu)
      root_run apt-get update
      root_run env DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io
      root_run systemctl enable --now docker
      ;;
    *) die "Instalação automática disponível somente para Debian/Ubuntu. Instale o Docker manualmente." ;;
  esac
  docker_run info >/dev/null 2>&1 || die "Docker foi instalado, mas não iniciou corretamente."
}

validate_inputs() {
  [[ "${HTTP_PORT}" =~ ^[0-9]+$ ]] || die "A porta deve ser numérica."
  (( HTTP_PORT >= 1 && HTTP_PORT <= 65535 )) || die "A porta deve estar entre 1 e 65535."
  [[ "${DATA_DIR}" == /* && "${DATA_DIR}" != "/" ]] || die "Use um diretório absoluto diferente de /."
  [[ "${CONTAINER_NAME}" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || die "Nome de container inválido."
  [[ "${IMAGE_TAG}" =~ ^[a-zA-Z0-9][a-zA-Z0-9._/-]*:[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || die "Tag de imagem inválida."
  [[ "${IMAGE_TAG##*:}" != "latest" ]] || die "A tag latest não é permitida; use uma versão imutável."
  [[ "${TIMEZONE}" =~ ^[a-zA-Z0-9_+/-]+$ ]] || die "Fuso horário inválido."
  if [[ -n "${PUBLIC_BASE_URL}" && ! "${PUBLIC_BASE_URL}" =~ ^https?://[^/]+$ ]]; then
    die "A URL pública deve usar http(s) e não pode conter caminho."
  fi
  [[ "${LICENSE_SERVER_URL}" =~ ^https?://[^/]+$ ]] || die "A URL do servidor de licenças deve usar http(s) e não pode conter caminho."
  [[ "${LICENSE_KEY_FILE}" == /* && -f "${LICENSE_KEY_FILE}" ]] || die "O arquivo da chave de licença não existe ou não é absoluto."
  LICENSE_KEY_DIR="$(dirname -- "${LICENSE_KEY_FILE}")"
  LICENSE_KEY_NAME="$(basename -- "${LICENSE_KEY_FILE}")"
  [[ "${LICENSE_KEY_NAME}" =~ ^[a-zA-Z0-9._-]+$ ]] || die "Nome do arquivo da chave inválido."
  [[ "${INSTALLATION_ID}" =~ ^[a-zA-Z0-9._:-]{8,128}$ ]] || die "Identificador de instalação inválido."
}

container_exists() {
  docker_run container inspect "$1" >/dev/null 2>&1
}

image_exists() {
  docker_run image inspect "$1" >/dev/null 2>&1
}

port_is_busy() {
  if command -v ss >/dev/null 2>&1; then
    ss -H -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)${HTTP_PORT}$"
  else
    return 1
  fi
}

data_has_users() {
  [[ -s "${DATA_DIR}/epg-product.json" ]] && root_run grep -q '"password_hash"' "${DATA_DIR}/epg-product.json"
}

ask_bootstrap_credentials() {
  local password confirmation
  while true; do
    read -r -p "Usuário do primeiro administrador: " ADMIN_USER
    [[ "${ADMIN_USER}" =~ ^[a-zA-Z0-9_.-]{3,64}$ ]] && break
    warn "Use de 3 a 64 caracteres: letras, números, ponto, hífen ou sublinhado."
  done
  while true; do
    read -r -s -p "Senha inicial (mínimo de 10 caracteres): " password
    printf '\n'
    read -r -s -p "Confirme a senha: " confirmation
    printf '\n'
    if [[ ${#password} -lt 10 ]]; then
      warn "A senha precisa ter pelo menos 10 caracteres."
    elif [[ "${password}" != "${confirmation}" ]]; then
      warn "As senhas não coincidem."
    else
      ADMIN_PASSWORD="${password}"
      break
    fi
  done
}

create_bootstrap_env() {
  local old_umask
  old_umask="$(umask)"
  umask 077
  TEMP_ENV="$(mktemp "${TMPDIR:-/tmp}/epgserver-bootstrap.XXXXXX")"
  umask "${old_umask}"
  {
    printf 'EPG_ADMIN_USER=%s\n' "${ADMIN_USER}"
    printf 'EPG_ADMIN_PASSWORD=%s\n' "${ADMIN_PASSWORD}"
  } >"${TEMP_ENV}"
  chmod 0600 "${TEMP_ENV}"
  unset ADMIN_PASSWORD
}

run_application() {
  local env_file="${1:-}"
  local args=(
    run -d
    --name "${CONTAINER_NAME}"
    --network host
    --restart unless-stopped
    --user "${CONTAINER_UID}:${CONTAINER_UID}"
    --read-only
    --tmpfs /tmp:rw,noexec,nosuid,size=64m,mode=1777
    --security-opt no-new-privileges:true
    --cap-drop ALL
    --env "EPG_HTTP_PORT=${HTTP_PORT}"
    --env "TZ=${TIMEZONE}"
    --env "EPG_LICENSE_SERVER_URL=${LICENSE_SERVER_URL}"
    --env "EPG_LICENSE_KEY_FILE=/license/${LICENSE_KEY_NAME}"
    --env "EPG_LICENSE_INSTALLATION_ID=${INSTALLATION_ID}"
    --volume "${DATA_DIR}:/data"
    --volume "${LICENSE_KEY_DIR}:/license"
  )
  if [[ -n "${PUBLIC_BASE_URL}" ]]; then
    args+=(--env "EPG_PUBLIC_BASE_URL=${PUBLIC_BASE_URL}")
  fi
  if [[ -n "${env_file}" ]]; then
    args+=(--env-file "${env_file}")
  fi
  args+=("${IMAGE_TAG}")
  docker_run "${args[@]}" >/dev/null
}

wait_for_health() {
  local attempt
  for ((attempt=1; attempt<=HEALTH_ATTEMPTS; attempt++)); do
    if docker_run exec "${CONTAINER_NAME}" python3 -c \
      "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:${HTTP_PORT}/health', timeout=2); raise SystemExit(0 if r.status == 200 else 1)" \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep "${HEALTH_INTERVAL}"
  done
  return 1
}

remove_new_container() {
  if container_exists "${CONTAINER_NAME}"; then
    docker_run rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  fi
}

restore_previous_container() {
  local previous="$1"
  warn "A nova versão não ficou saudável. Restaurando o container anterior..."
  remove_new_container
  if container_exists "${previous}"; then
    docker_run rename "${previous}" "${CONTAINER_NAME}"
    docker_run update --restart=unless-stopped "${CONTAINER_NAME}" >/dev/null
    docker_run start "${CONTAINER_NAME}" >/dev/null
    warn "Container anterior restaurado. O backup dos dados foi preservado."
  else
    warn "Container anterior não encontrado; restauração automática não foi possível."
  fi
}

main() {
  if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
  fi
  [[ $# -eq 0 ]] || die "Opção desconhecida. Use --help."
  [[ -f "${REPO_ROOT}/epg-product/Dockerfile" ]] || die "Execute o script dentro do repositório completo."

  printf '\n=== Instalador do EPG Server v%s ===\n' "${PRODUCT_VERSION}"
  prepare_privileges
  install_docker_if_needed

  ask_default HTTP_PORT "Porta HTTP do painel" "${DEFAULT_PORT}"
  ask_default DATA_DIR "Diretório persistente" "${DEFAULT_DATA_DIR}"
  ask_default CONTAINER_NAME "Nome do container" "${DEFAULT_CONTAINER}"
  ask_default IMAGE_TAG "Nome e tag imutável da imagem" "${DEFAULT_IMAGE}"
  ask_default TIMEZONE "Fuso horário" "${DEFAULT_TIMEZONE}"
  ask_default PUBLIC_BASE_URL "URL pública base (opcional, ex.: http://IP:PORTA)" ""
  ask_default LICENSE_SERVER_URL "URL do servidor de licenças" "${DEFAULT_LICENSE_SERVER_URL}"
  ask_default LICENSE_KEY_FILE "Arquivo da chave de licença" "${DEFAULT_LICENSE_KEY_FILE}"
  ask_default INSTALLATION_ID "Identificador desta instalação" "${DEFAULT_INSTALLATION_ID}"
  PUBLIC_BASE_URL="${PUBLIC_BASE_URL%/}"
  validate_inputs
  root_run chown "${CONTAINER_UID}:${CONTAINER_UID}" "${LICENSE_KEY_DIR}" "${LICENSE_KEY_FILE}"
  root_run chmod 0750 "${LICENSE_KEY_DIR}"
  root_run chmod 0600 "${LICENSE_KEY_FILE}"

  local updating=false previous_container="" backup_dir="" stamp
  if container_exists "${CONTAINER_NAME}"; then
    updating=true
    info "Container existente detectado: ${CONTAINER_NAME}"
    confirm "Construir e atualizar preservando rollback" || die "Operação cancelada."
  elif port_is_busy; then
    die "A porta ${HTTP_PORT} já está ocupada por outro processo."
  fi
  if image_exists "${IMAGE_TAG}"; then
    die "A imagem ${IMAGE_TAG} já existe. Informe uma nova tag imutável para não sobrescrever versões."
  fi

  root_run install -d -o "${CONTAINER_UID}" -g "${CONTAINER_UID}" -m 0750 "${DATA_DIR}"
  if ! data_has_users; then
    info "Volume sem usuários. Cadastre o primeiro administrador."
    ask_bootstrap_credentials
    create_bootstrap_env
  fi

  info "Compilando ${IMAGE_TAG}. O serviço atual continuará ativo durante o build..."
  docker_run build -f "${REPO_ROOT}/epg-product/Dockerfile" -t "${IMAGE_TAG}" "${REPO_ROOT}"

  if [[ "${updating}" == true ]]; then
    stamp="$(date +%Y%m%d-%H%M%S)"
    previous_container="${CONTAINER_NAME}-pre-${stamp}"
    backup_dir="${DATA_DIR}-backup-pre-${stamp}"
    while container_exists "${previous_container}" || [[ -e "${backup_dir}" ]]; do
      sleep 1
      stamp="$(date +%Y%m%d-%H%M%S)"
      previous_container="${CONTAINER_NAME}-pre-${stamp}"
      backup_dir="${DATA_DIR}-backup-pre-${stamp}"
    done
    info "Criando backup em ${backup_dir}..."
    root_run cp -a -- "${DATA_DIR}" "${backup_dir}"
    docker_run stop "${CONTAINER_NAME}" >/dev/null
    docker_run rename "${CONTAINER_NAME}" "${previous_container}"
    docker_run update --restart=no "${previous_container}" >/dev/null
  fi

  info "Iniciando a aplicação..."
  if ! run_application "${TEMP_ENV}"; then
    [[ "${updating}" == true ]] && restore_previous_container "${previous_container}"
    [[ "${updating}" == false ]] && remove_new_container
    die "Não foi possível criar o novo container."
  fi
  if ! wait_for_health; then
    [[ "${updating}" == true ]] && restore_previous_container "${previous_container}"
    [[ "${updating}" == false ]] && remove_new_container
    die "A aplicação não respondeu ao health check. Consulte: docker logs ${CONTAINER_NAME}"
  fi

  if [[ -n "${TEMP_ENV}" ]]; then
    info "Administrador criado. Removendo credenciais do ambiente definitivo..."
    docker_run rm -f "${CONTAINER_NAME}" >/dev/null
    cleanup
    TEMP_ENV=""
    if ! run_application "" || ! wait_for_health; then
      [[ "${updating}" == true ]] && restore_previous_container "${previous_container}"
      [[ "${updating}" == false ]] && remove_new_container
      die "A reinicialização segura, sem credenciais no ambiente, falhou."
    fi
  fi

  info "EPG Server instalado e saudável."
  printf '  Container: %s\n  Imagem:    %s\n  Dados:     %s\n' "${CONTAINER_NAME}" "${IMAGE_TAG}" "${DATA_DIR}"
  printf '  Painel:    http://IP_DO_SERVIDOR:%s/\n' "${HTTP_PORT}"
  printf '  Firewall:  autorize TCP %s somente para as redes administrativas necessárias.\n' "${HTTP_PORT}"
  if [[ "${updating}" == true ]]; then
    printf '  Rollback:  container %s\n  Backup:    %s\n' "${previous_container}" "${backup_dir}"
  fi
}

main "$@"
