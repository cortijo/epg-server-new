#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import shlex
import socket
import ssl
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import paramiko

VERSION = "1.0.1"
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.RLock()
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)$")
USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(?::[A-Za-z0-9][A-Za-z0-9_.-]{0,127})$")
INSTALL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,79}$")


class InstallError(RuntimeError):
    pass


def require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value or "troque-" in value.lower():
        raise SystemExit(f"{name} deve ser configurado")
    return value


ADMIN_USER = require_env("INSTALLER_ADMIN_USER")
ADMIN_PASSWORD = require_env("INSTALLER_ADMIN_PASSWORD")


def valid_host(value: str) -> str:
    value = value.strip()
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        if not HOST_RE.fullmatch(value) or ".." in value:
            raise InstallError("Host SSH inválido")
        return value


def validate_request(data: dict[str, Any], install: bool = False) -> dict[str, Any]:
    result = {
        "host": valid_host(str(data.get("host", ""))),
        "port": int(data.get("port", 22)),
        "username": str(data.get("username", "")).strip(),
        "password": str(data.get("password", "")),
        "sudo_password": str(data.get("sudo_password", "")),
        "fingerprint": str(data.get("fingerprint", "")).strip(),
    }
    if not 1 <= result["port"] <= 65535 or not USER_RE.fullmatch(result["username"]):
        raise InstallError("Porta ou usuário SSH inválido")
    if not result["password"]:
        raise InstallError("Informe a senha SSH")
    if not install:
        return result
    result.update({
        "repository": str(data.get("repository", "https://github.com/cortijo/epgserver2.git")).strip(),
        "ref": str(data.get("ref", "")).strip(),
        "image": str(data.get("image", "epgserver:v1.22.0")).strip(),
        "container": str(data.get("container", "epg-stream")).strip(),
        "data_dir": str(data.get("data_dir", "/srv/epg-stream")).strip(),
        "http_port": int(data.get("http_port", 9100)),
        "license_server": str(data.get("license_server", "")).strip(),
        "license_key": str(data.get("license_key", "")).strip(),
        "license_interval": int(data.get("license_interval", 43200)),
        "installation_id": str(data.get("installation_id", "")).strip(),
        "admin_user": str(data.get("admin_user", "epgadmin")).strip(),
        "admin_password": str(data.get("admin_password", "")),
    })
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", result["repository"]):
        raise InstallError("O repositório deve ser uma URL HTTPS do GitHub")
    if not REF_RE.fullmatch(result["ref"]) or not IMAGE_RE.fullmatch(result["image"]):
        raise InstallError("Ref ou tag da imagem inválida")
    if result["ref"].lower() in {"main", "master", "head"} or result["image"].lower().endswith(":latest"):
        raise InstallError("Use uma tag ou commit Git imutável e uma imagem versionada; main/latest não são aceitos")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,63}", result["container"]):
        raise InstallError("Nome do container inválido")
    if not re.fullmatch(r"/srv/[A-Za-z0-9._/-]{2,100}", result["data_dir"]) or ".." in result["data_dir"]:
        raise InstallError("Diretório de dados deve estar abaixo de /srv")
    if not 1 <= result["http_port"] <= 65535 or not 60 <= result["license_interval"] <= 604800:
        raise InstallError("Porta HTTP ou intervalo de licença inválido")
    if not re.fullmatch(r"https?://[A-Za-z0-9.:-]+(?:/[A-Za-z0-9._~/?#=&%-]*)?", result["license_server"]):
        raise InstallError("URL do servidor de licenças inválida")
    if not result["license_key"].startswith("EPG-") or len(result["license_key"]) > 512:
        raise InstallError("Chave de licença inválida")
    if not INSTALL_ID_RE.fullmatch(result["installation_id"]):
        raise InstallError("ID de instalação inválido")
    if not USER_RE.fullmatch(result["admin_user"]) or len(result["admin_password"]) < 10:
        raise InstallError("Administrador inicial ou senha inválida (mínimo 10 caracteres)")
    if not result["fingerprint"].startswith("SHA256:"):
        raise InstallError("Confirme primeiro a fingerprint SSH")
    return result


def host_fingerprint(host: str, port: int, timeout: int = 10) -> str:
    sock = socket.create_connection((host, port), timeout=timeout)
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        return f"SHA256:{digest}"
    finally:
        transport.close()
        sock.close()


def connect(config: dict[str, Any]) -> paramiko.SSHClient:
    observed = host_fingerprint(config["host"], config["port"])
    if config.get("fingerprint") and not hmac.compare_digest(observed, config["fingerprint"]):
        raise InstallError(f"Fingerprint SSH divergente; recebida {observed}")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(config["host"], port=config["port"], username=config["username"],
                   password=config["password"], look_for_keys=False, allow_agent=False,
                   timeout=12, auth_timeout=12, banner_timeout=12)
    return client


def run(client: paramiko.SSHClient, command: str, timeout: int = 60,
        stdin_data: str = "") -> tuple[int, str]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if stdin_data:
        stdin.write(stdin_data)
        stdin.flush()
        stdin.channel.shutdown_write()
    output = (stdout.read() + stderr.read()).decode("utf-8", "replace")
    return stdout.channel.recv_exit_status(), output[-12000:]


def parse_os_release(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    distro = values.get("ID", "unknown").lower()
    family = "apt" if distro in {"ubuntu", "debian", "linuxmint"} else "dnf" if distro in {"rhel", "rocky", "almalinux", "fedora", "centos"} else "unsupported"
    return {"id": distro, "name": values.get("PRETTY_NAME", distro), "version": values.get("VERSION_ID", ""), "family": family}


def probe(config: dict[str, Any]) -> dict[str, Any]:
    fingerprint = host_fingerprint(config["host"], config["port"])
    config = dict(config, fingerprint=fingerprint)
    client = connect(config)
    try:
        code, output = run(client, "cat /etc/os-release; printf '\\n__ARCH__='; uname -m; printf '\\n__DOCKER__='; command -v docker || true", 20)
        if code:
            raise InstallError("Não foi possível identificar o sistema remoto")
        os_text, tail = output.split("\n__ARCH__=", 1)
        arch, docker = tail.split("\n__DOCKER__=", 1)
        detected = parse_os_release(os_text)
        detected.update({"architecture": arch.strip(), "docker_installed": bool(docker.strip()), "fingerprint": fingerprint})
        return detected
    finally:
        client.close()


def q(value: Any) -> str:
    return shlex.quote(str(value))


def install_script(c: dict[str, Any]) -> str:
    license_b64 = base64.b64encode(c["license_key"].encode()).decode()
    if c.get("os_family", "apt") == "apt":
        install_packages = "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io git ca-certificates"
    else:
        docker_family = "fedora" if c.get("os_id") == "fedora" else "centos"
        install_packages = (
            "dnf install -y dnf-plugins-core git ca-certificates && "
            f"dnf config-manager --add-repo https://download.docker.com/linux/{docker_family}/docker-ce.repo && "
            "dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin"
        )
    return f'''set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
{install_packages}
systemctl enable --now docker
work=/opt/omniepg-installer/source
mkdir -p /opt/omniepg-installer
rm -rf "$work.new"
git clone --depth 1 --branch {q(c['ref'])} {q(c['repository'])} "$work.new"
docker build -t {q(c['image'])} -f "$work.new/epg-product/Dockerfile" "$work.new"
rm -rf "$work"
mv "$work.new" "$work"
install -d -o 10001 -g 10001 -m 0750 {q(c['data_dir'])}
install -d -m 0700 /srv/epg-license-client
printf %s {q(license_b64)} | base64 -d > /srv/epg-license-client/license.key
chmod 0600 /srv/epg-license-client/license.key
name={q(c['container'])}
rollback="${{name}}-rollback-$(date +%Y%m%d-%H%M%S)"
if docker container inspect "$name" >/dev/null 2>&1; then
  docker stop "$name"
  docker rename "$name" "$rollback"
  docker update --restart=no "$rollback"
fi
common="--restart unless-stopped --network host -v {q(c['data_dir'])}:/data -v /srv/epg-license-client:/license:ro"
docker run -d --name "$name" $common \
  -e EPG_HTTP_HOST=0.0.0.0 -e EPG_HTTP_PORT={c['http_port']} \
  -e EPG_DATA_DIR=/data -e EPG_EMITTER_BINARY=/app/TVStreamEpgOnly \
  -e EPG_LICENSE_KEY_FILE=/license/license.key \
  -e EPG_LICENSE_CHECK_SECONDS={c['license_interval']} \
  -e EPG_LICENSE_SERVER_URL={q(c['license_server'])} \
  -e EPG_LICENSE_INSTALLATION_ID={q(c['installation_id'])} \
  -e EPG_INSTALL_MODE=docker -e EPG_ADMIN_USERNAME={q(c['admin_user'])} \
  -e EPG_ADMIN_PASSWORD={q(c['admin_password'])} {q(c['image'])}
healthy=0
for i in $(seq 1 30); do
  if docker exec "$name" python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:{c['http_port']}/health',timeout=2)" >/dev/null 2>&1; then healthy=1; break; fi
  sleep 2
done
if [ "$healthy" != 1 ]; then
  docker logs --tail 40 "$name" || true
  docker rm -f "$name" || true
  if docker container inspect "$rollback" >/dev/null 2>&1; then docker rename "$rollback" "$name"; docker update --restart=unless-stopped "$name"; docker start "$name"; fi
  exit 42
fi
# Remove as credenciais de bootstrap do docker inspect após sua persistência.
docker rm -f "$name"
docker run -d --name "$name" $common \
  -e EPG_HTTP_HOST=0.0.0.0 -e EPG_HTTP_PORT={c['http_port']} \
  -e EPG_DATA_DIR=/data -e EPG_EMITTER_BINARY=/app/TVStreamEpgOnly \
  -e EPG_LICENSE_KEY_FILE=/license/license.key \
  -e EPG_LICENSE_CHECK_SECONDS={c['license_interval']} \
  -e EPG_LICENSE_SERVER_URL={q(c['license_server'])} \
  -e EPG_LICENSE_INSTALLATION_ID={q(c['installation_id'])} \
  -e EPG_INSTALL_MODE=docker {q(c['image'])}
sleep 3
docker container inspect "$name" --format 'image={{{{.Config.Image}}}} status={{{{.State.Status}}}} network={{{{.HostConfig.NetworkMode}}}}'
'''


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[SEGREDO REDIGIDO]")
    return text


def job_log(job_id: str, message: str) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job["log"].append({"at": int(time.time()), "message": message[-4000:]})
        job["updated_at"] = int(time.time())


def install_worker(job_id: str, config: dict[str, Any]) -> None:
    secrets = [config["password"], config["sudo_password"], config["license_key"], config["admin_password"]]
    client = None
    try:
        job_log(job_id, "Conectando por SSH e validando a fingerprint…")
        client = connect(config)
        job_log(job_id, "Detectando o sistema operacional…")
        code, os_output = run(client, "cat /etc/os-release", 15)
        detected = parse_os_release(os_output)
        if code or detected["family"] == "unsupported":
            raise InstallError(f"Sistema não suportado: {detected['name']}")
        job_log(job_id, f"Detectado: {detected['name']} ({detected['family']})")
        config["os_family"] = detected["family"]
        config["os_id"] = detected["id"]
        script = install_script(config)
        command = "sudo -S -p '' bash"
        job_log(job_id, "Instalando dependências, construindo a imagem e efetuando o cutover seguro…")
        code, output = run(client, command, 1800, config["sudo_password"] + "\n" + script)
        clean = redact(output, secrets)
        if code:
            raise InstallError(f"Instalação falhou (código {code}).\n{clean}")
        job_log(job_id, clean or "Instalação concluída")
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "completed"
            JOBS[job_id]["result"] = {"host": config["host"], "port": config["http_port"], "image": config["image"]}
    except Exception as error:
        job_log(job_id, redact(str(error), secrets))
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "failed"
    finally:
        if client:
            client.close()
        for key in ("password", "sudo_password", "license_key", "admin_password"):
            config[key] = ""


INDEX = r'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Instalador OMNIEPG</title><style>
:root{--navy:#082b69;--blue:#1264d8;--bg:#f2f5f9;--line:#d9e2ec;--red:#c52b39;--green:#15945f}*{box-sizing:border-box}body{margin:0;background:var(--bg);font:14px Inter,Segoe UI,Arial;color:#17283b}header{padding:22px 30px;background:linear-gradient(120deg,#061d48,var(--navy));color:white}header h1{margin:0 0 5px}.wrap{max-width:1100px;margin:24px auto;padding:0 18px}.card{background:white;border:1px solid var(--line);border-radius:14px;padding:22px;margin-bottom:16px;box-shadow:0 8px 24px #1232}h2{margin-top:0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:13px}label{font-size:12px;font-weight:750;display:flex;flex-direction:column;gap:6px}.wide{grid-column:1/-1}input{padding:11px;border:1px solid #bdcbd8;border-radius:8px}button{padding:11px 15px;border:0;border-radius:8px;font-weight:800;cursor:pointer}button.primary{background:var(--blue);color:#fff}button:disabled{opacity:.5}.actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}.notice{padding:12px;border-left:4px solid #e0a426;background:#fff9e8}.ok{color:var(--green)}.error{color:var(--red)}pre{background:#07162d;color:#d8edff;padding:16px;border-radius:9px;white-space:pre-wrap;max-height:340px;overflow:auto}.fingerprint{font-family:Consolas,monospace;word-break:break-all}@media(max-width:720px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}}</style></head><body><header><h1>Instalador OMNIEPG</h1><div>Provisionamento remoto seguro por SSH · v1.0.0</div></header><main class="wrap"><div class="notice"><b>Segurança:</b> publique este painel somente por HTTPS ou em uma rede administrativa. Senhas e chaves são mantidas apenas durante a instalação.</div><section class="card"><h2>1. Servidor e acesso SSH</h2><div class="grid"><label>Host ou IP<input id="host"></label><label>Porta SSH<input id="port" type="number" value="22"></label><label>Usuário SSH<input id="username"></label><label>Senha SSH<input id="password" type="password"></label><label>Senha sudo<input id="sudoPassword" type="password"></label><label>Fingerprint confirmada<input id="fingerprint" readonly class="fingerprint"></label></div><div id="probeResult"></div><div class="actions"><button class="primary" onclick="probe()">Identificar servidor</button></div></section><section class="card"><h2>2. OMNIEPG</h2><div class="grid"><label class="wide">Repositório público<input id="repository" value="https://github.com/cortijo/epgserver2.git"></label><label>Branch/tag Git<input id="ref" value="main"></label><label>Tag Docker imutável<input id="image" value="epgserver:v1.22.0"></label><label>Container<input id="container" value="epg-stream"></label><label>Diretório persistente<input id="dataDir" value="/srv/epg-stream"></label><label>Porta web<input id="httpPort" type="number" value="9100"></label><label class="wide">Servidor de licenças<input id="licenseServer" placeholder="http://SERVIDOR:9200"></label><label class="wide">Chave da licença<input id="licenseKey" type="password" placeholder="EPG-..."></label><label>Consulta da licença (segundos)<input id="licenseInterval" type="number" value="43200"></label><label>ID único da instalação<input id="installationId" placeholder="cliente-host-001"></label><label>Administrador inicial<input id="adminUser" value="epgadmin"></label><label>Senha inicial<input id="adminPassword" type="password"></label></div><div class="actions"><button id="installButton" class="primary" disabled onclick="install()">Instalar OMNIEPG</button></div></section><section id="jobCard" class="card" hidden><h2>Execução</h2><div id="jobStatus"></div><pre id="jobLog"></pre></section></main><script>
const el=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));let jobId='',timer=0;
document.querySelector('header>div').textContent='Provisionamento remoto seguro por SSH · v1.0.1';
el('ref').value='';el('ref').placeholder='Tag ou commit imutável, por exemplo epg-v1.22.0';
function ssh(){return{host:el('host').value,port:+el('port').value,username:el('username').value,password:el('password').value,sudo_password:el('sudoPassword').value,fingerprint:el('fingerprint').value}}
async function call(url,options={}){const r=await fetch(url,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}}),j=await r.json();if(!r.ok)throw Error(j.error||`HTTP ${r.status}`);return j}
async function probe(){el('installButton').disabled=true;el('probeResult').innerHTML='Consultando…';try{const r=await call('/api/probe',{method:'POST',body:JSON.stringify(ssh())});el('fingerprint').value=r.fingerprint;el('probeResult').innerHTML=`<p class="ok"><b>${esc(r.name)}</b> · ${esc(r.architecture)} · Docker ${r.docker_installed?'instalado':'será instalado'}</p><p>Confirme se a fingerprint SSH é a esperada: <span class="fingerprint">${esc(r.fingerprint)}</span></p>`;el('installButton').disabled=false}catch(e){el('probeResult').innerHTML=`<p class="error">${esc(e.message)}</p>`}}
function payload(){return{...ssh(),repository:el('repository').value,ref:el('ref').value,image:el('image').value,container:el('container').value,data_dir:el('dataDir').value,http_port:+el('httpPort').value,license_server:el('licenseServer').value,license_key:el('licenseKey').value,license_interval:+el('licenseInterval').value,installation_id:el('installationId').value,admin_user:el('adminUser').value,admin_password:el('adminPassword').value}}
async function install(){if(!confirm('A instalação poderá instalar Docker e substituir o container configurado, preservando-o para rollback. Continuar?'))return;el('installButton').disabled=true;try{const r=await call('/api/install',{method:'POST',body:JSON.stringify(payload())});jobId=r.job_id;el('password').value=el('sudoPassword').value=el('licenseKey').value=el('adminPassword').value='';el('jobCard').hidden=false;poll()}catch(e){alert(e.message);el('installButton').disabled=false}}
async function poll(){try{const j=await call(`/api/jobs/${jobId}`);el('jobStatus').innerHTML=`Estado: <b>${esc(j.status)}</b>`;el('jobLog').textContent=j.log.map(x=>new Date(x.at*1000).toLocaleTimeString()+' '+x.message).join('\n');if(j.status==='running')timer=setTimeout(poll,1500);else el('installButton').disabled=false}catch(e){el('jobStatus').innerHTML=`<span class="error">${esc(e.message)}</span>`}}
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    server_version = "OMNIEPGInstaller/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.client_address[0]} - {fmt % args}", flush=True)

    def authenticated(self) -> bool:
        expected = "Basic " + base64.b64encode(f"{ADMIN_USER}:{ADMIN_PASSWORD}".encode()).decode()
        return hmac.compare_digest(self.headers.get("Authorization", ""), expected)

    def auth(self) -> bool:
        if self.authenticated():
            return True
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Instalador OMNIEPG"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 32768:
            raise InstallError("Requisição muito grande")
        return json.loads(self.rfile.read(length) or b"{}")

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json({"status": "ok", "product": "OMNIEPG Installer", "version": VERSION})
            return
        if not self.auth():
            return
        if self.path == "/":
            body = INDEX.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})", self.path)
        if match:
            with JOBS_LOCK:
                job = JOBS.get(match.group(1))
                self.send_json(job if job else {"error": "Job não encontrado"}, 200 if job else 404)
            return
        self.send_json({"error": "Não encontrado"}, 404)

    def do_POST(self) -> None:
        if not self.auth():
            return
        try:
            if self.path == "/api/probe":
                self.send_json(probe(validate_request(self.json_body())))
                return
            if self.path == "/api/install":
                config = validate_request(self.json_body(), install=True)
                job_id = uuid.uuid4().hex
                with JOBS_LOCK:
                    JOBS[job_id] = {"id": job_id, "status": "running", "created_at": int(time.time()), "updated_at": int(time.time()), "log": [], "result": None}
                threading.Thread(target=install_worker, args=(job_id, config), daemon=True).start()
                self.send_json({"job_id": job_id}, 202)
                return
            self.send_json({"error": "Não encontrado"}, 404)
        except (InstallError, ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)
        except Exception as error:
            self.send_json({"error": f"Falha interna: {error}"}, 500)


def main() -> None:
    host = os.environ.get("INSTALLER_HTTP_HOST", "127.0.0.1")
    port = int(os.environ.get("INSTALLER_HTTP_PORT", "9300"))
    server = ThreadingHTTPServer((host, port), Handler)
    cert = os.environ.get("INSTALLER_TLS_CERT", "")
    key = os.environ.get("INSTALLER_TLS_KEY", "")
    if cert and key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"OMNIEPG Installer {VERSION} em {host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
