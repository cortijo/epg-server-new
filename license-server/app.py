#!/usr/bin/env python3
"""Small standalone licensing authority for EPG Stream installations."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import threading
import time
import urllib.parse
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


PRODUCT = "EPG License Server"
VERSION = "1.0.0"
PASSWORD_ITERATIONS = 310_000
MAX_BODY = 64 * 1024
KEY_PATTERN = re.compile(r"^EPG-[A-Za-z0-9_-]{40,80}$")


class ApiError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = int(status)


def now_epoch() -> int:
    return int(time.time())


def password_record(password: str) -> dict[str, Any]:
    if len(password) < 12:
        raise ApiError("A senha administrativa deve possuir ao menos 12 caracteres")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return {
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(digest).decode(),
        "iterations": PASSWORD_ITERATIONS,
    }


def password_matches(record: dict[str, Any], password: str) -> bool:
    try:
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(record["salt"], validate=True),
            int(record["iterations"]),
        )
        return hmac.compare_digest(actual, base64.b64decode(record["hash"], validate=True))
    except (KeyError, ValueError, TypeError):
        return False


def hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_key() -> str:
    return "EPG-" + secrets.token_urlsafe(36)


def public_license(item: dict[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(item.get(key)) for key in (
        "id", "name", "key_prefix", "max_channels", "enabled", "installation_id",
        "expires_at", "created_at", "updated_at", "last_seen_at", "last_channel_count",
    )}


class Store:
    def __init__(self, path: Path, admin_user: str = "", admin_password: str = ""):
        self.path = path
        self.lock = threading.RLock()
        self.data: dict[str, Any] = {}
        self.load(admin_user, admin_password)

    def load(self, admin_user: str, admin_password: str) -> None:
        with self.lock:
            loaded = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            self.data = {
                "schema_version": 1,
                "admin": loaded.get("admin") or {},
                "licenses": loaded.get("licenses") if isinstance(loaded.get("licenses"), list) else [],
            }
            if not self.data["admin"]:
                username = admin_user.strip().lower()
                if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,31}", username):
                    raise ApiError("O primeiro início exige LICENSE_ADMIN_USER válido")
                self.data["admin"] = {"username": username, **password_record(admin_password)}
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        os.chmod(self.path, 0o600)


class LicenseAuthority:
    def __init__(self, store: Store):
        self.store = store

    def authenticate(self, username: str, password: str) -> bool:
        admin = self.store.data["admin"]
        return hmac.compare_digest(username.strip().lower(), str(admin.get("username", ""))) \
            and password_matches(admin, password)

    def licenses(self) -> list[dict[str, Any]]:
        with self.store.lock:
            return [public_license(item) for item in self.store.data["licenses"]]

    def save_license(self, request: dict[str, Any]) -> dict[str, Any]:
        name = str(request.get("name") or "").strip()
        if not name or len(name) > 100:
            raise ApiError("Informe um nome de até 100 caracteres")
        try:
            max_channels = int(request.get("max_channels", 0))
        except (TypeError, ValueError):
            raise ApiError("O limite de canais deve ser inteiro")
        if not 1 <= max_channels <= 100000:
            raise ApiError("O limite de canais deve estar entre 1 e 100000")
        installation_id = str(request.get("installation_id") or "").strip()
        if installation_id and not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", installation_id):
            raise ApiError("Identificador de instalação inválido")
        try:
            expires_at = int(request.get("expires_at") or 0)
        except (TypeError, ValueError):
            raise ApiError("A validade deve ser um timestamp inteiro")
        license_id = str(request.get("id") or "").strip()
        created_key = ""
        with self.store.lock:
            item = next((value for value in self.store.data["licenses"] if value["id"] == license_id), None)
            if item is None:
                created_key = generate_key()
                created = now_epoch()
                item = {
                    "id": "license-" + uuid.uuid4().hex[:12],
                    "key_hash": hash_key(created_key),
                    "key_prefix": created_key[:12],
                    "created_at": created,
                    "last_seen_at": 0,
                    "last_channel_count": 0,
                }
                self.store.data["licenses"].append(item)
            item.update({
                "name": name,
                "max_channels": max_channels,
                "enabled": bool(request.get("enabled", True)),
                "installation_id": installation_id,
                "expires_at": max(0, expires_at),
                "updated_at": now_epoch(),
            })
            self.store.save()
        result = {"result": "ok", "license": public_license(item)}
        if created_key:
            result["key"] = created_key
        return result

    def revoke(self, license_id: str) -> dict[str, Any]:
        with self.store.lock:
            item = next((value for value in self.store.data["licenses"] if value["id"] == license_id), None)
            if not item:
                raise ApiError("Licença não encontrada", HTTPStatus.NOT_FOUND)
            item["enabled"] = False
            item["updated_at"] = now_epoch()
            self.store.save()
        return {"result": "ok"}

    def validate(self, request: dict[str, Any]) -> tuple[dict[str, Any], int]:
        key = str(request.get("key") or "").strip()
        installation_id = str(request.get("installation_id") or "").strip()
        try:
            channel_count = int(request.get("channel_count", -1))
        except (TypeError, ValueError):
            channel_count = -1
        if not KEY_PATTERN.fullmatch(key) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", installation_id) \
                or channel_count < 0:
            return {"valid": False, "reason": "Requisição de licença inválida"}, HTTPStatus.BAD_REQUEST
        digest = hash_key(key)
        with self.store.lock:
            item = next((value for value in self.store.data["licenses"]
                         if hmac.compare_digest(str(value.get("key_hash", "")), digest)), None)
            if not item:
                return {"valid": False, "reason": "Chave não reconhecida"}, HTTPStatus.FORBIDDEN
            now = now_epoch()
            reason = ""
            if not item.get("enabled"):
                reason = "Licença revogada"
            elif int(item.get("expires_at", 0)) and now >= int(item["expires_at"]):
                reason = "Licença expirada"
            elif item.get("installation_id") and item["installation_id"] != installation_id:
                reason = "Licença vinculada a outra instalação"
            elif channel_count > int(item["max_channels"]):
                reason = "Quantidade de canais acima do limite contratado"
            if reason:
                return {"valid": False, "reason": reason,
                        "max_channels": int(item["max_channels"]), "channel_count": channel_count}, HTTPStatus.FORBIDDEN
            if not item.get("installation_id"):
                item["installation_id"] = installation_id
            item["last_seen_at"] = now
            item["last_channel_count"] = channel_count
            self.store.save()
            return {
                "valid": True,
                "license_id": item["id"],
                "name": item["name"],
                "max_channels": int(item["max_channels"]),
                "channel_count": channel_count,
                "expires_at": int(item.get("expires_at", 0)),
                "checked_at": now,
            }, HTTPStatus.OK


AUTHORITY: LicenseAuthority | None = None


class Handler(BaseHTTPRequestHandler):
    server_version = "EPGLicense/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.client_address[0]} - {fmt % args}", flush=True)

    def json_response(self, value: Any, status: int = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            raise ApiError("Corpo vazio ou muito grande")
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            raise ApiError("JSON inválido")
        if not isinstance(value, dict):
            raise ApiError("O corpo deve ser um objeto JSON")
        return value

    def require_admin(self) -> bool:
        assert AUTHORITY is not None
        header = self.headers.get("Authorization", "")
        try:
            decoded = base64.b64decode(header.removeprefix("Basic "), validate=True).decode()
            username, password = decoded.split(":", 1)
        except Exception:
            username = password = ""
        if header.startswith("Basic ") and AUTHORITY.authenticate(username, password):
            return True
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="EPG License Server", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self) -> None:
        assert AUTHORITY is not None
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            self.json_response({"status": "ok", "product": PRODUCT, "version": VERSION})
            return
        if not self.require_admin():
            return
        if path == "/api/licenses":
            self.json_response({"licenses": AUTHORITY.licenses()})
        elif path == "/":
            payload = INDEX_HTML.encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.json_response({"error": "Endpoint não encontrado"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        assert AUTHORITY is not None
        path = urllib.parse.urlparse(self.path).path
        try:
            request = self.body()
            if path == "/api/validate":
                result, status = AUTHORITY.validate(request)
                self.json_response(result, status)
                return
            if not self.require_admin():
                return
            if path == "/api/licenses":
                result = AUTHORITY.save_license(request)
            elif path == "/api/licenses/revoke":
                result = AUTHORITY.revoke(str(request.get("id") or ""))
            else:
                raise ApiError("Endpoint não encontrado", HTTPStatus.NOT_FOUND)
            self.json_response(result)
        except ApiError as error:
            self.json_response({"error": str(error)}, error.status)
        except Exception as error:
            self.json_response({"error": f"Falha interna: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)


INDEX_HTML = r'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>EPG License Server</title><style>
:root{--navy:#071b33;--blue:#087ec1;--bg:#f2f6fa;--line:#dce6ef;--red:#d64550}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#14263a;font:14px Segoe UI,Arial,sans-serif}header{padding:24px;background:linear-gradient(120deg,var(--navy),#0b5689);color:white}main{max-width:1100px;margin:auto;padding:24px}.toolbar,.actions{display:flex;gap:10px;align-items:center;justify-content:space-between}button,input{font:inherit;padding:10px;border:1px solid var(--line);border-radius:8px}button{cursor:pointer;background:white}.primary{background:var(--blue);color:white}.danger{color:var(--red)}.card{background:white;border:1px solid var(--line);border-radius:14px;padding:18px;margin:14px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.grid label{display:grid;gap:6px}.key{padding:12px;background:#edf8ff;border-radius:8px;word-break:break-all}.muted{color:#6d7c8d}.modal{position:fixed;inset:0;background:#071b3388;display:grid;place-items:center;padding:15px}.modal>div{background:white;padding:22px;border-radius:14px;width:min(620px,100%)}@media(max-width:650px){.grid{grid-template-columns:1fr}.toolbar{align-items:flex-start;flex-direction:column}}
</style></head><body><header><h1>EPG License Server</h1><div>Gestão de licenças por quantidade de canais</div></header><main><div class="toolbar"><h2>Licenças</h2><button class="primary" onclick="edit()">+ Gerar chave</button></div><div id="list"></div></main><div id="modal"></div><script>
const el=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let items=[];async function api(url,opt={}){const r=await fetch(url,{headers:{'Content-Type':'application/json'},...opt}),j=await r.json().catch(()=>({error:'Resposta inválida'}));if(!r.ok)throw Error(j.error||`HTTP ${r.status}`);return j}async function refresh(){items=(await api('/api/licenses')).licenses;el('list').innerHTML=items.map(x=>`<div class="card"><div class="toolbar"><div><b>${esc(x.name)}</b><div class="muted">${esc(x.key_prefix)}… · ${x.last_channel_count||0}/${x.max_channels} canais</div><div class="muted">${x.installation_id?`Instalação: ${esc(x.installation_id)}`:'Ainda não vinculada'}</div></div><div class="actions"><button onclick="edit('${x.id}')">Editar</button>${x.enabled?`<button class="danger" onclick="revoke('${x.id}')">Revogar</button>`:'<b class="danger">Revogada</b>'}</div></div></div>`).join('')||'<div class="card muted">Nenhuma licença.</div>'}function edit(id=''){const x=items.find(v=>v.id===id)||{enabled:true,max_channels:100};el('modal').innerHTML=`<div class="modal"><div><h2>${id?'Editar licença':'Gerar licença'}</h2><input id="id" type="hidden" value="${esc(id)}"><div class="grid"><label>Cliente / instalação<input id="name" value="${esc(x.name||'')}"></label><label>Quantidade de canais<input id="limit" type="number" min="1" value="${x.max_channels}"></label><label>Identificador da instalação<input id="installation" value="${esc(x.installation_id||'')}" placeholder="vazio vincula no primeiro uso"></label><label>Status<select id="enabled"><option value="1" ${x.enabled?'selected':''}>Ativa</option><option value="0" ${!x.enabled?'selected':''}>Revogada</option></select></label></div><div class="actions" style="margin-top:18px;justify-content:flex-end"><button onclick="closeModal()">Cancelar</button><button class="primary" onclick="save()">Salvar</button></div></div></div>`}function closeModal(){el('modal').innerHTML=''}async function save(){try{const r=await api('/api/licenses',{method:'POST',body:JSON.stringify({id:el('id').value,name:el('name').value,max_channels:+el('limit').value,installation_id:el('installation').value,enabled:el('enabled').value==='1'})});if(r.key)alert(`Copie agora. A chave não será exibida novamente:\n\n${r.key}`);closeModal();refresh()}catch(e){alert(e.message)}}async function revoke(id){if(confirm('Revogar esta licença?')){await api('/api/licenses/revoke',{method:'POST',body:JSON.stringify({id})});refresh()}}refresh();
</script></body></html>'''


def main() -> None:
    global AUTHORITY
    data_dir = Path(os.environ.get("LICENSE_DATA_DIR", "/data"))
    AUTHORITY = LicenseAuthority(Store(
        data_dir / "licenses.json", os.environ.get("LICENSE_ADMIN_USER", ""),
        os.environ.get("LICENSE_ADMIN_PASSWORD", ""),
    ))
    server = ThreadingHTTPServer((os.environ.get("LICENSE_HTTP_HOST", "0.0.0.0"),
                                  int(os.environ.get("LICENSE_HTTP_PORT", "9200"))), Handler)
    server.daemon_threads = True
    stopping = threading.Event()
    def shutdown(_signum: int, _frame: Any) -> None:
        if not stopping.is_set():
            stopping.set()
            threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"{PRODUCT} {VERSION} iniciado", flush=True)
    server.serve_forever(poll_interval=0.5)
    server.server_close()


if __name__ == "__main__":
    main()
