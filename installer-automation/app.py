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

VERSION = "1.1.3"
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
    stdout.channel.set_combine_stderr(True)
    if stdin_data:
        stdin.write(stdin_data)
        stdin.flush()
        stdin.channel.shutdown_write()
    output = stdout.read().decode("utf-8", "replace")
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


def parse_marked_json(output: str, missing_message: str) -> dict[str, Any]:
    marker = "__OMNIEPG_JSON__"
    if marker not in output:
        raise InstallError(f"{missing_message}: {output[-1000:]}")
    return json.loads(output.rsplit(marker, 1)[1].splitlines()[0])


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


def validate_manage(data: dict[str, Any]) -> dict[str, Any]:
    result = validate_request(data)
    result.update({
        "container": str(data.get("container", "epg-stream")).strip(),
        "panel_user": str(data.get("panel_user", "")).strip(),
        "panel_password": str(data.get("panel_password", "")),
    })
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,63}", result["container"]):
        raise InstallError("Nome do container inválido")
    if result["panel_user"] and not USER_RE.fullmatch(result["panel_user"]):
        raise InstallError("Usuário do painel inválido")
    return result


REMOTE_INSPECT = r'''import base64,json,subprocess,sys,time,urllib.request
cfg=json.loads(sys.stdin.readline())
name=cfg["container"]
raw=subprocess.check_output(["docker","inspect",name],text=True)
obj=json.loads(raw)[0]
env=dict(x.split("=",1) for x in obj["Config"].get("Env",[]) if "=" in x)
port=int(env.get("EPG_HTTP_PORT","9100"))
started=time.monotonic()
health={"status":"offline","error":"health indisponível"}
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health",timeout=5) as r: health=json.load(r)
except Exception as e: health={"status":"offline","error":str(e)[:300]}
latency=round((time.monotonic()-started)*1000,1)
state={}; sources=[]
if cfg.get("panel_user") and cfg.get("panel_password"):
    auth=base64.b64encode((cfg["panel_user"]+":"+cfg["panel_password"]).encode()).decode()
    headers={"Authorization":"Basic "+auth}
    for path,target in (("/api/state","state"),("/api/sources","sources")):
        try:
            req=urllib.request.Request(f"http://127.0.0.1:{port}"+path,headers=headers)
            with urllib.request.urlopen(req,timeout=8) as r:
                if target=="state": state=json.load(r)
                else: sources=json.load(r).get("sources",[])
        except Exception as e:
            state.setdefault("management_error",str(e)[:300])
carriers=state.get("carriers",[])
usage={}
for c in carriers:
    for s in c.get("services",[]):
        sid=s.get("source_id") or c.get("source_id")
        usage[sid]=usage.get(sid,0)+1
safe_sources=[{"id":s.get("id"),"name":s.get("name"),"type":s.get("source_type","xmltv"),"channels":usage.get(s.get("id"),0),"last_sync":s.get("last_sync_at") or s.get("last_success_at")} for s in sources]
errors=[]
for e in state.get("epg_health",{}).get("errors",[]):
    errors.append({k:e.get(k) for k in ("carrier_id","carrier_name","service_id","service_name","source_id","source_name","message","reason") if e.get(k) is not None})
mounts=[{"type":m.get("Type"),"source":m.get("Source"),"destination":m.get("Destination")} for m in obj.get("Mounts",[])]
backups=[]
try:
    backups=sorted([x for x in os.listdir("/srv/omniepg-backups") if x.endswith(".tar.gz")],reverse=True)[:30]
except Exception: pass
print("__OMNIEPG_JSON__"+json.dumps({"container":{"name":name,"image":obj["Config"]["Image"],"status":obj["State"]["Status"],"running":obj["State"]["Running"],"started_at":obj["State"].get("StartedAt"),"restart_count":obj.get("RestartCount",0),"network":obj["HostConfig"].get("NetworkMode"),"mounts":mounts},"health":health,"latency_ms":latency,"port":port,"license_server":env.get("EPG_LICENSE_SERVER_URL",""),"license_interval":env.get("EPG_LICENSE_CHECK_SECONDS",""),"installation_id":env.get("EPG_LICENSE_INSTALLATION_ID",""),"carriers":len(carriers),"channels":sum(len(c.get("services",[])) for c in carriers),"active_emitters":sum(1 for c in carriers if c.get("active")),"sources":safe_sources,"errors":errors,"management_error":state.get("management_error",""),"backups":backups}))
'''.replace("import base64,json,subprocess,sys,time,urllib.request", "import base64,json,os,subprocess,sys,time,urllib.request")


def inspect_existing(config: dict[str, Any]) -> dict[str, Any]:
    observed = host_fingerprint(config["host"], config["port"])
    if not config.get("fingerprint") or not hmac.compare_digest(observed, config["fingerprint"]):
        raise InstallError(f"Confirme a fingerprint SSH antes de gerenciar: {observed}")
    started = time.monotonic()
    client = connect(config)
    try:
        config_b64 = base64.b64encode(json.dumps({"container": config["container"], "panel_user": config["panel_user"], "panel_password": config["panel_password"]}).encode()).decode()
        script = REMOTE_INSPECT.replace('cfg=json.loads(sys.stdin.readline())', f'cfg=json.loads(base64.b64decode("{config_b64}"))')
        code, output = run(client, "sudo -S -p '' python3 -", 30, config["sudo_password"] + "\n" + script)
        if code:
            raise InstallError(f"Falha ao inspecionar a instalação: {output[-1000:]}")
        result = parse_marked_json(output, "O servidor não devolveu um inventário válido")
        result["ssh_latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        result["fingerprint"] = observed
        return result
    finally:
        client.close()


def validate_action(data: dict[str, Any]) -> dict[str, Any]:
    result = validate_manage(data)
    action = str(data.get("action", ""))
    if action not in {"restart", "backup", "restore", "license", "deploy"}:
        raise InstallError("Ação de gestão inválida")
    result["action"] = action
    result["backup"] = str(data.get("backup", ""))
    result["license_server"] = str(data.get("license_server", "")).strip()
    result["license_key"] = str(data.get("license_key", "")).strip()
    result["repository"] = str(data.get("repository", "")).strip()
    result["ref"] = str(data.get("ref", "")).strip()
    result["image"] = str(data.get("image", "")).strip()
    if action == "restore" and not re.fullmatch(r"omniepg-backup-[0-9]{8}-[0-9]{6}\.tar\.gz", result["backup"]):
        raise InstallError("Backup inválido")
    if action == "license":
        if not re.fullmatch(r"https?://[A-Za-z0-9.:-]+(?:/[A-Za-z0-9._~/?#=&%-]*)?", result["license_server"]):
            raise InstallError("URL do servidor de licença inválida")
        if result["license_key"] and (not result["license_key"].startswith("EPG-") or len(result["license_key"]) > 512):
            raise InstallError("Chave de licença inválida")
        if not result["panel_user"] or not result["panel_password"]:
            raise InstallError("Informe as credenciais do painel OMNIEPG para alterar a licença")
    if action == "deploy":
        if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", result["repository"]):
            raise InstallError("Repositório GitHub inválido")
        if not REF_RE.fullmatch(result["ref"]) or result["ref"].lower() in {"main", "master", "head"}:
            raise InstallError("Use tag ou commit Git imutável")
        if not IMAGE_RE.fullmatch(result["image"]) or result["image"].lower().endswith(":latest"):
            raise InstallError("Use uma tag Docker imutável")
    if not result.get("fingerprint", "").startswith("SHA256:"):
        raise InstallError("Confirme a fingerprint SSH")
    return result


REMOTE_ACTION = r'''import base64,json,os,shutil,subprocess,sys,time,urllib.request
cfg=json.loads(sys.stdin.readline()); action=cfg["action"]; name=cfg["container"]
def call(args,check=True): return subprocess.run(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=check).stdout
def inspect(): return json.loads(call(["docker","inspect",name]))[0]
def recreate(image=None,env_updates=None):
    obj=inspect(); image=image or obj["Config"]["Image"]
    env=dict(x.split("=",1) for x in obj["Config"].get("Env",[]) if "=" in x)
    env.update(env_updates or {})
    keep={k:v for k,v in env.items() if k.startswith("EPG_") and k not in {"EPG_ADMIN_USERNAME","EPG_ADMIN_PASSWORD"}}
    rollback=name+"-rollback-"+time.strftime("%Y%m%d-%H%M%S")
    call(["docker","stop",name]); call(["docker","rename",name,rollback]); call(["docker","update","--restart=no",rollback])
    args=["docker","run","-d","--name",name,"--restart",obj["HostConfig"].get("RestartPolicy",{}).get("Name") or "unless-stopped"]
    network=obj["HostConfig"].get("NetworkMode") or "host"; args += ["--network",network]
    for bind in obj["HostConfig"].get("Binds") or []: args += ["-v",bind]
    for k,v in keep.items(): args += ["-e",k+"="+v]
    args.append(image)
    try:
        call(args)
        port=int(keep.get("EPG_HTTP_PORT","9100")); ok=False
        for _ in range(30):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health",timeout=2); ok=True; break
            except Exception: time.sleep(2)
        if not ok: raise RuntimeError("health não respondeu")
        return {"rollback":rollback,"image":image}
    except Exception:
        subprocess.run(["docker","rm","-f",name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        call(["docker","rename",rollback,name]); call(["docker","update","--restart=unless-stopped",name]); call(["docker","start",name]); raise
if action=="restart":
    call(["docker","restart",name]); result={"message":"Container reiniciado"}
elif action=="backup":
    os.makedirs("/srv/omniepg-backups",exist_ok=True); filename="omniepg-backup-"+time.strftime("%Y%m%d-%H%M%S")+".tar.gz"
    obj=inspect(); data=next((m["Source"] for m in obj.get("Mounts",[]) if m.get("Destination")=="/data" and m.get("Type")=="bind"),None)
    if not data: raise RuntimeError("Volume /data não é bind mount; backup automático não suportado")
    target="/srv/omniepg-backups/"+filename
    packed=subprocess.run(["tar","--warning=no-file-changed","--ignore-failed-read","-czf",target,"-C",data,"."],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    try:
        if packed.returncode not in (0,1): raise RuntimeError(packed.stdout[-1000:])
        call(["tar","-tzf",target])
    except Exception:
        try: os.unlink(target)
        except FileNotFoundError: pass
        raise
    result={"message":"Backup criado","backup":filename}
elif action=="restore":
    path="/srv/omniepg-backups/"+cfg["backup"]
    if not os.path.isfile(path): raise RuntimeError("Backup não encontrado")
    obj=inspect(); data=next((m["Source"] for m in obj.get("Mounts",[]) if m.get("Destination")=="/data" and m.get("Type")=="bind"),None)
    if not data: raise RuntimeError("Volume /data não é bind mount; restore automático não suportado")
    safety="/srv/omniepg-backups/omniepg-backup-"+time.strftime("%Y%m%d-%H%M%S")+".tar.gz"; call(["tar","-czf",safety,"-C",data,"."])
    call(["docker","stop",name])
    try:
        shutil.rmtree(data); os.makedirs(data,exist_ok=True); call(["tar","-xzf",path,"-C",data]); call(["chown","-R","10001:10001",data]); call(["docker","start",name])
    except Exception:
        shutil.rmtree(data,ignore_errors=True); os.makedirs(data,exist_ok=True); call(["tar","-xzf",safety,"-C",data]); call(["chown","-R","10001:10001",data]); call(["docker","start",name]); raise
    result={"message":"Backup restaurado","safety_backup":os.path.basename(safety)}
elif action=="license":
    result=recreate(env_updates={"EPG_LICENSE_SERVER_URL":cfg["license_server"]})
    if cfg.get("license_key"):
        body=json.dumps({"key":cfg["license_key"]}).encode(); auth=base64.b64encode((cfg["panel_user"]+":"+cfg["panel_password"]).encode()).decode()
        port=int(dict(x.split("=",1) for x in inspect()["Config"].get("Env",[]) if "=" in x).get("EPG_HTTP_PORT","9100"))
        req=urllib.request.Request(f"http://127.0.0.1:{port}/api/license/key",data=body,headers={"Authorization":"Basic "+auth,"Content-Type":"application/json"},method="POST")
        urllib.request.urlopen(req,timeout=15).read()
    result["message"]="Licença atualizada e container validado"
elif action=="deploy":
    work="/opt/omniepg-installer/manage-build-"+str(os.getpid()); shutil.rmtree(work,ignore_errors=True)
    call(["git","clone","--depth","1","--branch",cfg["ref"],cfg["repository"],work]); call(["docker","build","-t",cfg["image"],"-f",work+"/epg-product/Dockerfile",work]); shutil.rmtree(work,ignore_errors=True)
    result=recreate(image=cfg["image"]); result["message"]="Versão aplicada e validada"
print("__OMNIEPG_JSON__"+json.dumps(result))
'''


def manage_worker(job_id: str, config: dict[str, Any]) -> None:
    secrets = [config["password"], config["sudo_password"], config["panel_password"], config["license_key"]]
    client = None
    try:
        job_log(job_id, f"Executando ação {config['action']} com fingerprint validada…")
        client = connect(config)
        public = {k: v for k, v in config.items() if k not in {"password", "sudo_password", "panel_password", "license_key"}}
        public.update({"panel_user": config["panel_user"], "panel_password": config["panel_password"], "license_key": config["license_key"]})
        config_b64 = base64.b64encode(json.dumps(public).encode()).decode()
        script = REMOTE_ACTION.replace('cfg=json.loads(sys.stdin.readline())', f'cfg=json.loads(base64.b64decode("{config_b64}"))')
        stdin_data = config["sudo_password"] + "\n" + script
        code, output = run(client, "sudo -S -p '' python3 -", 1800, stdin_data)
        clean = redact(output, secrets)
        if code:
            raise InstallError(f"Ação falhou (código {code}): {clean[-3000:]}")
        result = parse_marked_json(output, "A ação não devolveu resultado estruturado")
        job_log(job_id, result.get("message", "Ação concluída"))
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "completed"; JOBS[job_id]["result"] = result
    except Exception as error:
        job_log(job_id, redact(str(error), secrets))
        with JOBS_LOCK: JOBS[job_id]["status"] = "failed"
    finally:
        if client: client.close()
        for key in ("password", "sudo_password", "panel_password", "license_key"): config[key] = ""


INDEX = r'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Instalador OMNIEPG</title><style>
:root{--navy:#082b69;--blue:#1264d8;--bg:#f2f5f9;--line:#d9e2ec;--red:#c52b39;--green:#15945f}*{box-sizing:border-box}body{margin:0;background:var(--bg);font:14px Inter,Segoe UI,Arial;color:#17283b}header{padding:22px 30px;background:linear-gradient(120deg,#061d48,var(--navy));color:white}header h1{margin:0 0 5px}.wrap{max-width:1100px;margin:24px auto;padding:0 18px}.card{background:white;border:1px solid var(--line);border-radius:14px;padding:22px;margin-bottom:16px;box-shadow:0 8px 24px #1232}h2{margin-top:0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:13px}label{font-size:12px;font-weight:750;display:flex;flex-direction:column;gap:6px}.wide{grid-column:1/-1}input{padding:11px;border:1px solid #bdcbd8;border-radius:8px}button{padding:11px 15px;border:0;border-radius:8px;font-weight:800;cursor:pointer}button.primary{background:var(--blue);color:#fff}button:disabled{opacity:.5}.actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}.notice{padding:12px;border-left:4px solid #e0a426;background:#fff9e8}.ok{color:var(--green)}.error{color:var(--red)}pre{background:#07162d;color:#d8edff;padding:16px;border-radius:9px;white-space:pre-wrap;max-height:340px;overflow:auto}.fingerprint{font-family:Consolas,monospace;word-break:break-all}@media(max-width:720px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}}</style></head><body><header><h1>Instalador OMNIEPG</h1><div>Provisionamento remoto seguro por SSH · v1.0.0</div></header><main class="wrap"><div class="notice"><b>Segurança:</b> publique este painel somente por HTTPS ou em uma rede administrativa. Senhas e chaves são mantidas apenas durante a instalação.</div><section class="card"><h2>1. Servidor e acesso SSH</h2><div class="grid"><label>Host ou IP<input id="host"></label><label>Porta SSH<input id="port" type="number" value="22"></label><label>Usuário SSH<input id="username"></label><label>Senha SSH<input id="password" type="password"></label><label>Senha sudo<input id="sudoPassword" type="password"></label><label>Fingerprint confirmada<input id="fingerprint" readonly class="fingerprint"></label></div><div id="probeResult"></div><div class="actions"><button class="primary" onclick="probe()">Identificar servidor</button></div></section><section class="card"><h2>2. OMNIEPG</h2><div class="grid"><label class="wide">Repositório público<input id="repository" value="https://github.com/cortijo/epgserver2.git"></label><label>Branch/tag Git<input id="ref" value="main"></label><label>Tag Docker imutável<input id="image" value="epgserver:v1.22.0"></label><label>Container<input id="container" value="epg-stream"></label><label>Diretório persistente<input id="dataDir" value="/srv/epg-stream"></label><label>Porta web<input id="httpPort" type="number" value="9100"></label><label class="wide">Servidor de licenças<input id="licenseServer" placeholder="http://SERVIDOR:9200"></label><label class="wide">Chave da licença<input id="licenseKey" type="password" placeholder="EPG-..."></label><label>Consulta da licença (segundos)<input id="licenseInterval" type="number" value="43200"></label><label>ID único da instalação<input id="installationId" placeholder="cliente-host-001"></label><label>Administrador inicial<input id="adminUser" value="epgadmin"></label><label>Senha inicial<input id="adminPassword" type="password"></label></div><div class="actions"><button id="installButton" class="primary" disabled onclick="install()">Instalar OMNIEPG</button></div></section><section id="jobCard" class="card" hidden><h2>Execução</h2><div id="jobStatus"></div><pre id="jobLog"></pre></section></main><script>
const el=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));let jobId='',timer=0;
document.querySelector('header>div').textContent='Provisionamento remoto seguro por SSH · v1.0.1';
el('ref').value='';el('ref').placeholder='Tag ou commit imutável, por exemplo epg-v1.22.0';
document.querySelector('header>div').textContent='Instalação e gestão remota segura · v1.1.3';
document.head.insertAdjacentHTML('beforeend','<style>.mode-switch{display:flex;gap:8px;margin:18px 0}.mode-switch button{flex:1;border:1px solid var(--line)}.mode-switch button.active{background:var(--blue);color:#fff}.manage{display:none}.manage.show{display:block}.install-hidden{display:none!important}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.metric{padding:14px;border:1px solid var(--line);border-radius:10px}.metric small{display:block;color:#657789}.metric b{display:block;font-size:19px;margin-top:5px}.table{width:100%;border-collapse:collapse}.table th,.table td{padding:9px;border-top:1px solid var(--line);text-align:left}.management-actions{display:flex;gap:8px;flex-wrap:wrap}.management-actions button{background:#e8eef5}.danger{color:var(--red)}@media(max-width:720px){.metrics{grid-template-columns:1fr 1fr}}</style>');
const main=document.querySelector('main.wrap'),notice=main.querySelector('.notice'),installCards=[...main.querySelectorAll(':scope>.card')];notice.insertAdjacentHTML('afterend','<div class="mode-switch"><button id="modeNew" class="active" onclick="setMode(\'new\')">Nova instalação</button><button id="modeManage" onclick="setMode(\'manage\')">Instalação existente</button></div><section id="manageView" class="manage"><div class="card"><h2>Servidor OMNIEPG existente</h2><div class="grid"><label>Host ou IP<input id="mHost"></label><label>Porta SSH<input id="mPort" type="number" value="22"></label><label>Usuário SSH<input id="mUsername"></label><label>Senha SSH<input id="mPassword" type="password"></label><label>Senha sudo<input id="mSudo" type="password"></label><label>Container<input id="mContainer" value="epg-stream"></label><label>Usuário do painel<input id="mPanelUser" value="epgadmin"></label><label>Senha do painel<input id="mPanelPassword" type="password"></label><label>Fingerprint<input id="mFingerprint" readonly></label></div><div class="actions"><button onclick="manageProbe()">Identificar</button><button class="primary" id="manageConnect" disabled onclick="inspectManage()">Conectar e analisar</button></div></div><div id="manageDashboard"></div></section>');
function setMode(mode){const manage=mode==='manage';el('modeNew').classList.toggle('active',!manage);el('modeManage').classList.toggle('active',manage);el('manageView').classList.toggle('show',manage);installCards.slice(0,2).forEach(card=>card.classList.toggle('install-hidden',manage))}
function mssh(){return{host:el('mHost').value,port:+el('mPort').value,username:el('mUsername').value,password:el('mPassword').value,sudo_password:el('mSudo').value,fingerprint:el('mFingerprint').value,container:el('mContainer').value,panel_user:el('mPanelUser').value,panel_password:el('mPanelPassword').value}}
async function manageProbe(){el('manageConnect').disabled=true;try{const r=await call('/api/probe',{method:'POST',body:JSON.stringify(mssh())});el('mFingerprint').value=r.fingerprint;el('manageConnect').disabled=false;alert(`${r.name} · ${r.architecture}\nFingerprint: ${r.fingerprint}`)}catch(e){alert(e.message)}}
let manageInfo=null;
async function inspectManage(){el('manageDashboard').innerHTML='<div class="card">Consultando saúde, Docker, fontes e canais…</div>';try{manageInfo=await call('/api/manage/inspect',{method:'POST',body:JSON.stringify(mssh())});renderManage()}catch(e){el('manageDashboard').innerHTML=`<div class="card error">${esc(e.message)}</div>`}}
function renderManage(){const m=manageInfo,c=m.container,h=m.health||{},valid=h.license?.valid,errors=m.errors||[],sources=m.sources||[];el('manageDashboard').innerHTML=`<div class="card"><h2>Visão geral</h2><div class="metrics"><div class="metric"><small>ESTADO</small><b class="${c.running?'ok':'error'}">${c.running?'Online':'Offline'}</b></div><div class="metric"><small>IMAGEM</small><b>${esc(c.image)}</b></div><div class="metric"><small>LATÊNCIA HEALTH</small><b>${m.latency_ms} ms</b></div><div class="metric"><small>LATÊNCIA SSH</small><b>${m.ssh_latency_ms} ms</b></div><div class="metric"><small>LICENÇA</small><b class="${valid?'ok':'error'}">${valid?'Válida':'Inválida'}</b></div><div class="metric"><small>CANAIS</small><b>${m.channels}</b></div><div class="metric"><small>PORTADORAS / ATIVAS</small><b>${m.carriers} / ${m.active_emitters}</b></div><div class="metric"><small>REINÍCIOS</small><b>${c.restart_count}</b></div></div><p>Rede: <b>${esc(c.network)}</b> · Porta: <b>${m.port}</b> · Instalação: <b>${esc(m.installation_id||'—')}</b></p><div class="management-actions"><button onclick="manageAction('restart')">Reiniciar sistema</button><button onclick="manageAction('backup')">Criar backup</button><button onclick="showRestore()">Restaurar backup</button><button onclick="showLicense()">Licença</button><button onclick="showDeploy()">Atualizar / downgrade</button><button onclick="inspectManage()">Atualizar diagnóstico</button></div></div><div id="manageOperation"></div><div class="card"><h2>Fontes XMLTV (${sources.length})</h2>${sources.length?`<table class="table"><thead><tr><th>Fonte</th><th>Tipo</th><th>Canais</th><th>Última sincronização</th></tr></thead><tbody>${sources.map(s=>`<tr><td><b>${esc(s.name)}</b></td><td>${esc(s.type)}</td><td>${s.channels}</td><td>${s.last_sync?new Date(s.last_sync*1000).toLocaleString():'—'}</td></tr>`).join('')}</tbody></table>`:'<p>Nenhuma fonte disponível. Informe as credenciais corretas do painel.</p>'}</div><div class="card"><h2>Erros nos canais (${errors.length})</h2>${errors.length?`<table class="table"><thead><tr><th>Portadora</th><th>Canal</th><th>Fonte</th><th>Erro</th></tr></thead><tbody>${errors.map(e=>`<tr><td>${esc(e.carrier_name||e.carrier_id||'—')}</td><td>${esc(e.service_name||e.service_id||'—')}</td><td>${esc(e.source_name||e.source_id||'—')}</td><td class="error">${esc(e.message||e.reason||'Erro não detalhado')}</td></tr>`).join('')}</tbody></table>`:'<p class="ok">Nenhum erro de canal informado.</p>'}</div>`}
function showRestore(){const backups=manageInfo?.backups||[];el('manageOperation').innerHTML=`<div class="card"><h2>Restaurar backup</h2><label>Backup<select id="manageBackup">${backups.map(b=>`<option>${esc(b)}</option>`).join('')}</select></label><div class="actions"><button onclick="el('manageOperation').innerHTML=''">Cancelar</button><button class="danger" onclick="manageAction('restore',{backup:el('manageBackup').value})" ${backups.length?'':'disabled'}>Restaurar</button></div></div>`}
function showLicense(){el('manageOperation').innerHTML=`<div class="card"><h2>Servidor e chave de licença</h2><div class="grid"><label class="wide">Servidor de licença<input id="manageLicenseServer" value="${esc(manageInfo?.license_server||'')}"></label><label class="wide">Nova chave (vazio mantém atual)<input id="manageLicenseKey" type="password"></label></div><div class="actions"><button onclick="el('manageOperation').innerHTML=''">Cancelar</button><button class="primary" onclick="manageAction('license',{license_server:el('manageLicenseServer').value,license_key:el('manageLicenseKey').value})">Aplicar e validar</button></div></div>`}
function showDeploy(){el('manageOperation').innerHTML=`<div class="card"><h2>Atualizar ou fazer downgrade</h2><div class="grid"><label class="wide">Repositório<input id="manageRepo" value="https://github.com/cortijo/epgserver2.git"></label><label>Tag/commit Git imutável<input id="manageRef"></label><label>Nova tag Docker<input id="manageImage" placeholder="epgserver:v1.22.0"></label></div><p>A mesma operação atende upgrade e downgrade. A imagem atual será preservada para rollback.</p><div class="actions"><button onclick="el('manageOperation').innerHTML=''">Cancelar</button><button class="primary" onclick="manageAction('deploy',{repository:el('manageRepo').value,ref:el('manageRef').value,image:el('manageImage').value})">Construir e aplicar</button></div></div>`}
async function manageAction(action,extra={}){if(!confirm(`Executar ${action} no servidor ${el('mHost').value}?`))return;try{const r=await call('/api/manage/action',{method:'POST',body:JSON.stringify({...mssh(),action,...extra})});jobId=r.job_id;el('jobCard').hidden=false;pollManage()}catch(e){alert(e.message)}}
async function pollManage(){try{const j=await call(`/api/jobs/${jobId}`);el('jobStatus').innerHTML=`Estado: <b>${esc(j.status)}</b>`;el('jobLog').textContent=j.log.map(x=>new Date(x.at*1000).toLocaleTimeString()+' '+x.message).join('\n');if(j.status==='running')setTimeout(pollManage,1500);else{el('mPassword').value=el('mSudo').value=el('mPanelPassword').value='';if(j.status==='completed')el('manageOperation').innerHTML='<div class="card ok">Ação concluída. Informe novamente as credenciais e atualize o diagnóstico.</div>'}}catch(e){el('jobStatus').innerHTML=`<span class="error">${esc(e.message)}</span>`}}
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
            if self.path == "/api/manage/inspect":
                self.send_json(inspect_existing(validate_manage(self.json_body())))
                return
            if self.path == "/api/manage/action":
                config = validate_action(self.json_body())
                job_id = uuid.uuid4().hex
                with JOBS_LOCK:
                    JOBS[job_id] = {"id": job_id, "status": "running", "created_at": int(time.time()), "updated_at": int(time.time()), "log": [], "result": None}
                threading.Thread(target=manage_worker, args=(job_id, config), daemon=True).start()
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
