#!/usr/bin/env python3
"""OMNIEPG — control plane for standalone ISDB-TB EPG emitters."""

from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import hmac
import io
import ipaddress
import json
import os
import platform
import re
import signal
import shutil
import struct
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PIL import Image
from license_client import LicenseError, LicenseManager


PRODUCT_NAME = "OMNIEPG"
PRODUCT_VERSION = "1.24.4"
PRODUCT_DEVELOPER = "Julio Cortijo"
HOT_RELOAD_SIGNAL = getattr(signal, "SIGUSR1", None)
DEFAULT_UPDATE_REPOSITORY = "cortijo/epgserver2"
DEFAULT_SOURCE = {
    "id": "braziltvepg",
    "name": "BrazilTVEPG (padrão)",
    "url": "https://github.com/limaalef/BrazilTVEPG/raw/refs/heads/main/claro.xml",
    "source_type": "xmltv",
    "is_default": True,
}
MAX_BODY = 3 * 1024 * 1024
MAX_LOGO = 2 * 1024 * 1024
MAX_XMLTV = 96 * 1024 * 1024
MAX_BACKUP = 512 * 1024 * 1024
MAX_BACKUP_CONTENT = 2 * 1024 * 1024 * 1024
MAX_BACKUP_FILES = 20_000
GUIDE_CACHE_SECONDS = 300
SOURCE_SYNC_SECONDS = 3600
DEFAULT_GENERAL_SETTINGS = {
    "xmltv_sync_mode": "interval",
    "xmltv_sync_minutes": 60,
    "xmltv_daily_time": "04:15",
    "emitter_refresh_minutes": 180,
    "emitter_retry_minutes": 5,
    "detect_cache_updates": True,
    "license_primary_url": "",
    "license_secondary_url": "",
}
BRAZIL_TZ = timezone(timedelta(hours=-3))
PASSWORD_ITERATIONS = 310_000
PASSWORD_MINIMUM = 10
ARIB_LOGO_SIZES = {
    0: (48, 24),   # SD 4:3 small
    1: (36, 24),   # SD 16:9 small
    2: (48, 27),   # HD small
    3: (72, 36),   # SD 4:3 large
    4: (54, 36),   # SD 16:9 large
    5: (64, 36),   # HD large
}
CONTENT_CATEGORIES = (
    "Filmes", "Notícias", "Entretenimento", "Esportes", "Infantil",
    "Música", "Cultura", "Sociedade", "Educação", "Lazer",
)


class ApiError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = int(status)


def now_epoch() -> int:
    return int(time.time())


def version_tuple(value: str) -> tuple[int, int, int]:
    match = re.search(r"(?:^|[^0-9])(\d+)\.(\d+)\.(\d+)(?:[^0-9]|$)", str(value))
    if not match:
        raise ApiError("A release não possui uma versão válida")
    return tuple(int(part) for part in match.groups())


def update_architecture() -> str:
    architecture = platform.machine().lower()
    return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        architecture, architecture
    )


def parse_update_release(payload: dict[str, Any], repository: str, architecture: str) -> dict[str, Any]:
    tag = str(payload.get("tag_name") or "")
    latest = ".".join(map(str, version_tuple(tag)))
    suffix = f"_{architecture}.deb"
    assets = [item for item in payload.get("assets", []) if isinstance(item, dict)]
    asset = next((item for item in assets if str(item.get("name") or "").endswith(suffix)), None)
    digest = str((asset or {}).get("digest") or "")
    url = str((asset or {}).get("browser_download_url") or "")
    if asset and (not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) or not url.startswith("https://github.com/")):
        raise ApiError("A release possui um pacote sem assinatura SHA-256 válida")
    return {
        "repository": repository, "tag": tag, "latest_version": latest,
        "release_url": str(payload.get("html_url") or ""),
        "asset_name": str((asset or {}).get("name") or ""),
        "asset_available": bool(asset), "digest": digest.lower(),
    }


def update_information(data_dir: Path) -> dict[str, Any]:
    repository = os.environ.get("EPG_UPDATE_REPOSITORY", DEFAULT_UPDATE_REPOSITORY).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ApiError("Repositório de atualização inválido")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"OMNIEPG/{PRODUCT_VERSION}"}
    token_path = Path(os.environ.get("EPG_UPDATE_TOKEN_FILE", ""))
    if str(token_path) and token_path.is_file():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/releases/latest",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read(1024 * 1024 + 1)
    except Exception as error:
        raise ApiError(f"Não foi possível consultar atualizações: {error}", HTTPStatus.BAD_GATEWAY)
    if len(raw) > 1024 * 1024:
        raise ApiError("Resposta de atualização muito grande", HTTPStatus.BAD_GATEWAY)
    try:
        release = parse_update_release(json.loads(raw), repository, update_architecture())
    except (json.JSONDecodeError, TypeError) as error:
        raise ApiError(f"Resposta de atualização inválida: {error}", HTTPStatus.BAD_GATEWAY)
    release.update({
        "product": PRODUCT_NAME, "developer": PRODUCT_DEVELOPER,
        "current_version": PRODUCT_VERSION,
        "install_mode": os.environ.get("EPG_INSTALL_MODE", "docker").strip().lower() or "docker",
        "update_available": version_tuple(release["latest_version"]) > version_tuple(PRODUCT_VERSION),
    })
    status_path = data_dir / "update-status.json"
    if status_path.is_file():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            release["last_update"] = {key: status.get(key) for key in ("status", "message", "tag", "updated_at")}
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return release


def request_native_update(data_dir: Path, expected_tag: str) -> dict[str, Any]:
    information = update_information(data_dir)
    if information["install_mode"] != "native":
        raise ApiError("No modo Docker, atualize a imagem pelo host", HTTPStatus.CONFLICT)
    if not information["update_available"] or not information["asset_available"]:
        raise ApiError("Não existe atualização nativa disponível", HTTPStatus.CONFLICT)
    if expected_tag != information["tag"]:
        raise ApiError("A release mudou; consulte novamente antes de atualizar", HTTPStatus.CONFLICT)
    target = data_dir / "update-request.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"tag": information["tag"], "requested_at": now_epoch()}), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    return {"result": "scheduled", "tag": information["tag"], "message": "Atualização solicitada"}


def slug_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def password_record(password: str, salt: bytes | None = None) -> dict[str, Any]:
    if len(password) < PASSWORD_MINIMUM:
        raise ApiError(f"A senha deve possuir pelo menos {PASSWORD_MINIMUM} caracteres")
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return {
        "password_salt": base64.b64encode(salt).decode("ascii"),
        "password_hash": base64.b64encode(digest).decode("ascii"),
        "password_iterations": PASSWORD_ITERATIONS,
    }


def password_matches(user: dict[str, Any], password: str) -> bool:
    try:
        salt = base64.b64decode(user["password_salt"], validate=True)
        expected = base64.b64decode(user["password_hash"], validate=True)
        iterations = int(user.get("password_iterations", PASSWORD_ITERATIONS))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (KeyError, TypeError, ValueError):
        return False


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(user.get(key))
        for key in ("id", "username", "display_name", "role", "enabled", "created_at", "updated_at")
    }


def public_channel(channel: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(channel)
    logo = result.get("logo")
    if isinstance(logo, dict):
        result["logo"] = {key: value for key, value in logo.items()
                          if key not in {"path", "variants"}}
    return result


def public_carrier(carrier: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(carrier)
    result["services"] = [public_channel(item) for item in result.get("services", [])]
    return result


def normalized_username(value: Any) -> str:
    username = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,31}", username):
        raise ApiError("O usuário deve ter de 3 a 32 caracteres: letras, números, ponto, hífen ou sublinhado")
    return username


def parse_xmltv_datetime(value: str) -> datetime:
    text = (value or "").strip()
    match = re.match(r"^(\d{14})(?:\s*([+-]\d{4}|Z))?", text)
    if not match:
        raise ValueError(f"data XMLTV inválida: {value!r}")
    moment = datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
    offset = match.group(2)
    if not offset:
        return moment.replace(tzinfo=BRAZIL_TZ).astimezone(timezone.utc)
    if offset == "Z":
        return moment.replace(tzinfo=timezone.utc)
    sign = 1 if offset[0] == "+" else -1
    minutes = int(offset[1:3]) * 60 + int(offset[3:5])
    return moment.replace(tzinfo=timezone(sign * timedelta(minutes=minutes))).astimezone(timezone.utc)


def element_text(element: ET.Element | None, fallback: str = "") -> str:
    if element is None:
        return fallback
    return " ".join("".join(element.itertext()).split()) or fallback


def parse_xmltv(payload: bytes, bounded: bool = True) -> dict[str, Any]:
    """Parse channels and a bounded programme window without retaining the XML tree."""
    channels: dict[str, dict[str, str]] = {}
    programmes: dict[str, list[dict[str, Any]]] = {}
    reference = datetime.now(timezone.utc)
    minimum = reference - timedelta(days=1)
    maximum = reference + timedelta(days=8)
    for _, element in ET.iterparse(io.BytesIO(payload), events=("end",)):
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "channel":
            channel_id = (element.get("id") or "").strip()
            if channel_id:
                display = element_text(element.find("display-name"), channel_id)
                icon = element.find("icon")
                channels[channel_id] = {
                    "id": channel_id,
                    "name": display,
                    "icon": (icon.get("src") if icon is not None else "") or "",
                }
            element.clear()
        elif tag == "programme":
            channel_id = (element.get("channel") or "").strip()
            try:
                start = parse_xmltv_datetime(element.get("start") or "")
                stop = parse_xmltv_datetime(element.get("stop") or "")
            except ValueError:
                element.clear()
                continue
            if channel_id and stop > start and (not bounded or (stop > minimum and start < maximum)):
                item = {
                    "channel_id": channel_id,
                    "start": int(start.timestamp()),
                    "stop": int(stop.timestamp()),
                    "title": element_text(element.find("title"), "Sem título"),
                    "subtitle": element_text(element.find("sub-title")),
                    "description": element_text(element.find("desc")),
                    "category": element_text(element.find("category")),
                }
                programmes.setdefault(channel_id, []).append(item)
            element.clear()
    for items in programmes.values():
        items.sort(key=lambda item: (item["start"], item["stop"]))
    return {"channels": channels, "programmes": programmes}


def guide_content_fingerprint(parsed: dict[str, Any]) -> str:
    """Hash only data that changes the EPG seen by receivers.

    XML formatting, element order and provider generation timestamps must not
    trigger an emitter reload.
    """
    channels = sorted(
        (str(channel_id), str(channel.get("name") or ""))
        for channel_id, channel in parsed.get("channels", {}).items()
    )
    programmes = sorted(
        (
            str(item.get("channel_id") or channel_id),
            int(item.get("start") or 0),
            int(item.get("stop") or 0),
            str(item.get("title") or ""),
            str(item.get("subtitle") or ""),
            str(item.get("description") or ""),
            str(item.get("category") or ""),
        )
        for channel_id, items in parsed.get("programmes", {}).items()
        for item in items
    )
    canonical = json.dumps(
        {"channels": channels, "programmes": programmes},
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _local_tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _numeric_channel_prefix(value: str) -> str:
    match = re.match(r"^\s*(\d{3,6})(?:\s|$)", value or "")
    return match.group(1) if match else ""


def _normalized_xmltv_time(value: str) -> tuple[str, bool]:
    text = (value or "").strip()
    if re.fullmatch(r"\d{14}", text):
        text += " -0300"
        added = True
    else:
        added = False
    # Use the same validation and offset semantics as the panel.
    parse_xmltv_datetime(text)
    return text, added


def normalize_uploaded_xmltv(payload: bytes, collect_errors: bool = False) -> tuple[bytes, dict[str, Any]]:
    """Normalize a provider XMLTV into the strict feed consumed by both parsers."""
    if payload[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
                payload = compressed.read(MAX_XMLTV + 1)
        except (OSError, EOFError) as error:
            raise ApiError(f"Arquivo GZIP inválido: {error}")
    if not payload or len(payload) > MAX_XMLTV:
        raise ApiError("O XMLTV vazio ou descompactado excede o limite de 96 MiB")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise ApiError(f"XMLTV inválido: {error}")
    if _local_tag(root) != "tv":
        raise ApiError("O arquivo precisa possuir o elemento raiz <tv>")

    stats: dict[str, Any] = {
        "channels_original": 0, "channels": 0, "programmes_original": 0,
        "programmes": 0, "timezone_added": 0, "channel_refs_rewritten": 0,
        "channels_synthesized": 0, "duplicate_channels_removed": 0,
        "invalid_programmes_removed": 0,
    }
    if collect_errors:
        stats["invalid_programmes"] = []
        stats["invalid_programmes_truncated"] = 0
    declared: dict[str, ET.Element] = {}
    prefix_candidates: dict[str, set[str]] = {}
    for child in list(root):
        if _local_tag(child) != "channel":
            continue
        stats["channels_original"] += 1
        channel_id = (child.get("id") or "").strip()
        if not channel_id or channel_id in declared:
            root.remove(child)
            stats["duplicate_channels_removed"] += 1
            continue
        child.set("id", channel_id)
        declared[channel_id] = child
        prefix = _numeric_channel_prefix(channel_id)
        if prefix:
            prefix_candidates.setdefault(prefix, set()).add(channel_id)

    valid_moments: list[datetime] = []
    unresolved: set[str] = set()
    for child in list(root):
        if _local_tag(child) != "programme":
            continue
        stats["programmes_original"] += 1
        channel_id = (child.get("channel") or "").strip()
        raw_start = (child.get("start") or "").strip()
        raw_stop = (child.get("stop") or "").strip()
        title = element_text(child.find("title"), "Sem título")
        missing_channel = False
        if channel_id not in declared:
            candidates = prefix_candidates.get(_numeric_channel_prefix(channel_id), set())
            if len(candidates) == 1:
                replacement = next(iter(candidates))
                if replacement != channel_id:
                    child.set("channel", replacement)
                    channel_id = replacement
                    stats["channel_refs_rewritten"] += 1
            elif channel_id:
                missing_channel = True
        reason = ""
        try:
            start_text, start_added = _normalized_xmltv_time(raw_start)
            stop_text, stop_added = _normalized_xmltv_time(raw_stop)
            start = parse_xmltv_datetime(start_text)
            stop = parse_xmltv_datetime(stop_text)
            if not channel_id:
                reason = "Canal não informado"
            elif stop <= start:
                reason = "Duração inválida: término menor ou igual ao início"
        except ValueError:
            reason = "Data de início ou término inválida"
        if reason:
            root.remove(child)
            stats["invalid_programmes_removed"] += 1
            if collect_errors:
                details = stats["invalid_programmes"]
                if len(details) < 2000:
                    details.append({"channel_id": channel_id[:256], "title": title[:512],
                                    "start": raw_start[:64], "stop": raw_stop[:64],
                                    "reason": reason})
                else:
                    stats["invalid_programmes_truncated"] += 1
            continue
        child.set("channel", channel_id)
        child.set("start", start_text)
        child.set("stop", stop_text)
        stats["timezone_added"] += int(start_added) + int(stop_added)
        stats["programmes"] += 1
        if missing_channel:
            unresolved.add(channel_id)
        valid_moments.extend((start, stop))

    # C++ requires a matching <channel> declaration. Preserve otherwise valid
    # provider events by synthesizing only the declarations still missing.
    for channel_id in sorted(unresolved):
        if channel_id in declared:
            continue
        element = ET.Element("channel", {"id": channel_id})
        ET.SubElement(element, "display-name", {"lang": "pt"}).text = channel_id
        root.insert(len(declared), element)
        declared[channel_id] = element
        stats["channels_synthesized"] += 1

    if not valid_moments or not stats["programmes"]:
        raise ApiError("O XMLTV não possui programas válidos")
    stats["channels"] = len(declared)
    stats["valid_from"] = int(min(valid_moments).timestamp())
    stats["valid_until"] = int(max(valid_moments).timestamp())
    try:
        ET.indent(root, space="  ")
    except AttributeError:
        pass
    normalized = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if len(normalized) > MAX_XMLTV:
        raise ApiError("O XMLTV normalizado excede o limite de 96 MiB")
    parsed = parse_xmltv(normalized, bounded=False)
    parsed_count = sum(map(len, parsed["programmes"].values()))
    if parsed_count != stats["programmes"]:
        raise ApiError("A validação do XMLTV normalizado encontrou divergência na programação")
    stats["bytes"] = len(normalized)
    return normalized, stats


def select_publication_version(versions: list[dict[str, Any]], at: int | None = None) -> dict[str, Any] | None:
    if not versions:
        return None
    current = now_epoch() if at is None else int(at)
    active = [item for item in versions if int(item["valid_from"]) <= current < int(item["valid_until"])]
    if active:
        return max(active, key=lambda item: (int(item["valid_from"]), int(item.get("uploaded_at", 0))))
    future = [item for item in versions if int(item["valid_from"]) > current]
    if future:
        return min(future, key=lambda item: (int(item["valid_from"]), -int(item.get("uploaded_at", 0))))
    return max(versions, key=lambda item: (int(item["valid_until"]), int(item.get("uploaded_at", 0))))


def validate_source(source: dict[str, Any]) -> dict[str, Any]:
    source_type = str(source.get("source_type") or "xmltv").strip().lower()
    if source_type not in {"xmltv", "parse_xml"}:
        raise ApiError("Tipo de fonte inválido")
    result = {
        "id": str(source.get("id") or slug_id("source")),
        "name": str(source.get("name") or "").strip(),
        "url": str(source.get("url") or "").strip(),
        "source_type": source_type,
        "is_default": bool(source.get("is_default", False)),
    }
    if source_type == "parse_xml":
        token = str(source.get("parse_token") or uuid.uuid4().hex).strip().lower()
        if not re.fullmatch(r"[a-f0-9]{32}", token):
            raise ApiError("Token interno Parse-XML inválido")
        result["parse_token"] = token
    if not result["name"]:
        raise ApiError("Informe o nome da fonte XMLTV")
    parsed = urllib.parse.urlparse(result["url"])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ApiError("A fonte XMLTV deve usar uma URL HTTP ou HTTPS válida")
    return result


def validate_general_settings(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ApiError("Configurações gerais inválidas")
    result: dict[str, Any] = {}
    mode = str(value.get("xmltv_sync_mode") or "interval").strip().lower()
    if mode not in {"interval", "daily"}:
        raise ApiError("O modo de sincronização XMLTV deve ser por intervalo ou diário")
    daily_time = str(value.get("xmltv_daily_time") or "04:15").strip()
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", daily_time)
    if not match:
        raise ApiError("O horário diário deve usar o formato HH:MM")
    result["xmltv_sync_mode"] = mode
    result["xmltv_daily_time"] = daily_time
    limits = {
        "xmltv_sync_minutes": (5, 10080),
        "emitter_refresh_minutes": (1, 10080),
        "emitter_retry_minutes": (1, 1440),
    }
    for key, (minimum, maximum) in limits.items():
        try:
            number = int(value.get(key, DEFAULT_GENERAL_SETTINGS[key]))
        except (TypeError, ValueError) as error:
            raise ApiError("Os intervalos devem ser informados em minutos") from error
        if number < minimum or number > maximum:
            raise ApiError(f"{key} deve ficar entre {minimum} e {maximum} minutos")
        result[key] = number
    result["detect_cache_updates"] = bool(
        value.get("detect_cache_updates", DEFAULT_GENERAL_SETTINGS["detect_cache_updates"]))
    for key, label in (
            ("license_primary_url", "Servidor principal de licenças"),
            ("license_secondary_url", "Servidor redundante de licenças")):
        url = str(value.get(key) or "").strip().rstrip("/")
        if url:
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ApiError(f"{label} deve usar uma URL HTTP ou HTTPS válida")
        result[key] = url
    return result


def validate_config_backup(payload: bytes) -> dict[str, Any]:
    try:
        envelope = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApiError("O arquivo de backup não contém JSON válido")
    if not isinstance(envelope, dict) or envelope.get("format") != "epg-stream-config-backup":
        raise ApiError("Formato de backup incompatível")
    if int(envelope.get("format_version", 0)) != 1:
        raise ApiError("Versão do backup incompatível")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise ApiError("Configuração ausente no backup")
    required_lists = ("users", "sources", "carriers", "xmltv_publications")
    if any(not isinstance(data.get(key), list) for key in required_lists):
        raise ApiError("Estrutura de configuração incompleta")
    users = data["users"]
    if not users or not any(user.get("enabled") and user.get("role") == "admin" for user in users):
        raise ApiError("O backup precisa possuir ao menos um administrador ativo")
    usernames = set()
    for user in users:
        username = str(user.get("username") or "")
        if (not re.fullmatch(r"[a-z0-9._-]{3,32}", username) or username in usernames or
                user.get("role") not in {"admin", "operator"} or
                not all(isinstance(user.get(key), str) and user.get(key)
                        for key in ("id", "password_salt", "password_hash"))):
            raise ApiError("O backup contém usuário inválido ou duplicado")
        usernames.add(username)
    sources = [validate_source(source) for source in data["sources"]]
    source_ids = {source["id"] for source in sources}
    if len(source_ids) != len(sources):
        raise ApiError("O backup contém fontes duplicadas")
    carriers = [validate_carrier(carrier) for carrier in data["carriers"]]
    carrier_ids = {carrier["id"] for carrier in carriers}
    if len(carrier_ids) != len(carriers):
        raise ApiError("O backup contém portadoras duplicadas")
    for carrier in carriers:
        if carrier["source_id"] not in source_ids:
            raise ApiError(f"A portadora {carrier['name']} referencia uma fonte inexistente")
        for service in carrier["services"]:
            if service["source_id"] and service["source_id"] not in source_ids:
                raise ApiError(f"O canal {service['name']} referencia uma fonte inexistente")
    publications = []
    publication_ids = set()
    publication_tokens = set()
    for publication in data["xmltv_publications"]:
        publication_id = str(publication.get("id") or "")
        token = str(publication.get("token") or "")
        name = bounded_text(publication.get("name"), 100)
        if (not publication_id or not token or not name or publication_id in publication_ids or
                token in publication_tokens):
            raise ApiError("O backup contém publicação XMLTV inválida ou duplicada")
        publication_ids.add(publication_id)
        publication_tokens.add(token)
        versions = publication.get("versions", [])
        if not isinstance(versions, list):
            raise ApiError("O backup contém versões XMLTV inválidas")
        for version in versions:
            stored_path = Path(str(version.get("path") or ""))
            expected_root = Path("/data/xmltv-publications") / publication_id
            if not stored_path.is_absolute() or expected_root not in stored_path.parents:
                raise ApiError("O backup contém caminho de publicação XMLTV inválido")
        publications.append(copy.deepcopy(publication))
    restored = copy.deepcopy(data)
    restored["sources"] = sources
    restored["carriers"] = carriers
    restored["xmltv_publications"] = publications
    restored["schema_version"] = int(data.get("schema_version", 1))
    return restored


def validate_png(payload: bytes) -> tuple[int, int]:
    """Validate the bounded source PNG envelope accepted by the logo API."""
    if not payload.startswith(b"\x89PNG\r\n\x1a\n") or len(payload) < 33:
        raise ApiError("O logo precisa ser um arquivo PNG válido")
    length = struct.unpack(">I", payload[8:12])[0]
    if payload[12:16] != b"IHDR" or length != 13:
        raise ApiError("Cabeçalho PNG inválido")
    width, height = struct.unpack(">II", payload[16:24])
    if width < 1 or height < 1 or width > 1920 or height > 1080:
        raise ApiError("O logo PNG possui dimensões inválidas")
    if b"IEND" not in payload[-32:]:
        raise ApiError("O arquivo PNG está incompleto")
    return width, height


def normalize_logo_png(payload: bytes) -> bytes:
    """Build the conventional 64x36 PNG used only by the web preview."""
    try:
        with Image.open(io.BytesIO(payload)) as source:
            source.load()
            image = source.convert("RGBA")
    except Exception as error:
        raise ApiError(f"Não foi possível processar o PNG: {error}")
    image.thumbnail((64, 36), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (64, 36), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((64 - image.width) // 2, (36 - image.height) // 2))
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    normalized = output.getvalue()
    if len(normalized) > 3800:
        raise ApiError("A miniatura normalizada excedeu o limite interno")
    return normalized


def arib_fixed_clut() -> list[tuple[int, int, int, int]]:
    """Return the 128-entry common CLUT defined by ARIB TR-B15."""
    opaque_special = [
        (0, 0, 0, 255), (255, 0, 0, 255), (0, 255, 0, 255),
        (255, 255, 0, 255), (0, 0, 255, 255), (255, 0, 255, 255),
        (0, 255, 255, 255), (255, 255, 255, 255),
    ]
    half_bright = [
        (170, 0, 0, 255), (0, 170, 0, 255), (170, 170, 0, 255),
        (0, 0, 170, 255), (170, 0, 170, 255), (0, 170, 170, 255),
        (170, 170, 170, 255),
    ]
    palette = opaque_special + [(0, 0, 0, 0)] + half_bright
    represented = set(opaque_special + half_bright)
    levels = (0, 85, 170, 255)
    palette.extend(
        (red, green, blue, 255)
        for red in levels for green in levels for blue in levels
        if (red, green, blue, 255) not in represented
    )
    # The normative table reserves one entry, so the half-alpha grid omits
    # (255,255,170). This yields exactly 128 common receiver-side entries.
    palette.extend(
        (red, green, blue, 128)
        for red in levels for green in levels for blue in levels
        if (red, green, blue) != (255, 255, 170)
    )
    if len(palette) != 128:
        raise RuntimeError("ARIB fixed CLUT must contain 128 entries")
    return palette


ARIB_FIXED_CLUT = arib_fixed_clut()


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def strict_indexed_png(width: int, height: int, indices: bytes) -> bytes:
    """Encode an ARIB indexed PNG whose palette is supplied by the receiver."""
    if len(indices) != width * height:
        raise ValueError("invalid indexed image length")
    scanlines = b"".join(
        b"\x00" + indices[row * width:(row + 1) * width]
        for row in range(height)
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) +
            png_chunk(b"IDAT", zlib.compress(scanlines, 9)) + png_chunk(b"IEND", b""))


def _clut_index(pixel: tuple[int, int, int, int]) -> int:
    red, green, blue, alpha = pixel
    if alpha < 64:
        return 8
    candidates = range(65, 128) if alpha < 192 else list(range(0, 8)) + list(range(9, 65))
    premultiplied = (red * alpha // 255, green * alpha // 255, blue * alpha // 255)
    return min(candidates, key=lambda index: (
        (ARIB_FIXED_CLUT[index][0] * ARIB_FIXED_CLUT[index][3] // 255 - premultiplied[0]) ** 2 +
        (ARIB_FIXED_CLUT[index][1] * ARIB_FIXED_CLUT[index][3] // 255 - premultiplied[1]) ** 2 +
        (ARIB_FIXED_CLUT[index][2] * ARIB_FIXED_CLUT[index][3] // 255 - premultiplied[2]) ** 2 +
        (ARIB_FIXED_CLUT[index][3] - alpha) ** 2
    ))


def build_arib_logo_variants(payload: bytes) -> dict[int, bytes]:
    """Render all six ARIB logo formats from one source image."""
    try:
        with Image.open(io.BytesIO(payload)) as source:
            source.load()
            original = source.convert("RGBA")
    except Exception as error:
        raise ApiError(f"Não foi possível processar o PNG: {error}")
    variants: dict[int, bytes] = {}
    for logo_type, (width, height) in ARIB_LOGO_SIZES.items():
        image = original.copy()
        image.thumbnail((width, height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.alpha_composite(image, ((width - image.width) // 2, (height - image.height) // 2))
        pixels = canvas.load()
        indices = bytes(
            _clut_index(pixels[x, y]) for y in range(height) for x in range(width)
        )
        encoded = strict_indexed_png(width, height, indices)
        if len(encoded) > 3800:
            raise ApiError(f"O logo ARIB tipo {logo_type} excedeu o limite da seção CDT")
        variants[logo_type] = encoded
    return variants


def integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ApiError(f"{name} deve ser um número inteiro")
    if number < minimum or number > maximum:
        raise ApiError(f"{name} deve estar entre {minimum} e {maximum}")
    return number


def validate_carrier(carrier: dict[str, Any]) -> dict[str, Any]:
    clock_mode = str(carrier.get("clock_mode") or "standard").strip().lower()
    if clock_mode not in {"standard", "custom"}:
        raise ApiError("Modo do relógio inválido")
    clock_utc_offset_minutes = integer(
        carrier.get("clock_utc_offset_minutes", -180), "Fuso do relógio", -720, 840)
    clock_correction_minutes = integer(
        carrier.get("clock_correction_minutes", 0), "Correção do relógio", -1440, 1440)
    if clock_utc_offset_minutes % 15:
        raise ApiError("O fuso do relógio deve usar intervalos de 15 minutos")
    if clock_mode == "standard":
        clock_utc_offset_minutes = -180
        clock_correction_minutes = 0
    result = {
        "id": str(carrier.get("id") or slug_id("carrier")),
        "name": str(carrier.get("name") or "").strip(),
        "source_id": str(carrier.get("source_id") or "").strip(),
        "auto_start": bool(carrier.get("auto_start", True)),
        "transport_stream_id": integer(carrier.get("transport_stream_id", 1), "TSID", 1, 65535),
        "original_network_id": integer(carrier.get("original_network_id", 1), "ONID", 1, 65535),
        "destination": str(carrier.get("destination") or "").strip(),
        "port": integer(carrier.get("port", 5000), "Porta", 1, 65535),
        "interface_address": str(carrier.get("interface_address") or "").strip(),
        "pmt_pid": integer(carrier.get("pmt_pid", 4096), "PID base da PMT", 32, 8190),
        "bitrate": integer(carrier.get("bitrate", 1000000), "Bitrate", 100000, 100000000),
        "ttl": integer(carrier.get("ttl", 32), "TTL", 1, 255),
        "clock_mode": clock_mode,
        "clock_utc_offset_minutes": clock_utc_offset_minutes,
        "clock_correction_minutes": clock_correction_minutes,
        "services": [],
    }
    if not result["name"]:
        raise ApiError("Informe o nome da portadora")
    if not result["source_id"]:
        raise ApiError("Selecione a fonte XMLTV")
    try:
        destination = ipaddress.IPv4Address(result["destination"])
        if not destination.is_multicast:
            raise ValueError
    except ipaddress.AddressValueError:
        raise ApiError("Informe um destino multicast IPv4 válido")
    except ValueError:
        raise ApiError("O destino precisa ser um endereço multicast IPv4")
    try:
        ipaddress.IPv4Address(result["interface_address"])
    except ipaddress.AddressValueError:
        raise ApiError("Informe o IPv4 da interface de saída")
    services = carrier.get("services")
    if not isinstance(services, list) or not 1 <= len(services) <= 64:
        raise ApiError("A portadora deve possuir entre 1 e 64 serviços")
    service_ids: set[int] = set()
    for index, service in enumerate(services):
        service_id = integer(service.get("service_id", 0), "Program Number", 1, 65535)
        if service_id in service_ids:
            raise ApiError("Os Program Numbers não podem se repetir na mesma portadora")
        service_ids.add(service_id)
        name = str(service.get("name") or "").strip()
        channel_id = str(service.get("epg_channel_id") or "").strip()
        source_id = str(service.get("source_id") or "").strip()
        default_category = str(service.get("default_category") or "").strip()
        if default_category and default_category not in CONTENT_CATEGORIES:
            raise ApiError(f"Categoria padrão inválida no canal {name}")
        if not name or not channel_id:
            raise ApiError("Todos os serviços precisam de nome e ID XMLTV")
        result["services"].append({
            "id": str(service.get("id") or f"service-{service_id}-{index}"),
            "name": name,
            "epg_channel_id": channel_id,
            "service_id": service_id,
            "source_id": source_id,
            "default_category": default_category,
            # Logo metadata and filesystem paths are server-owned and never
            # accepted from a carrier save request.
            "logo": {},
        })
    if result["pmt_pid"] + len(result["services"]) - 1 > 8190:
        raise ApiError("O intervalo de PIDs das PMTs ultrapassa 0x1FFE")
    reserved = {0, 17, 18, 20, 8191}
    if any(pid in reserved for pid in range(result["pmt_pid"], result["pmt_pid"] + len(result["services"]))):
        raise ApiError("O intervalo de PIDs das PMTs contém um PID reservado")
    return result


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        with self.lock:
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
            else:
                loaded = {}
            self.data = {
                "schema_version": 3,
                "sources": loaded.get("sources") or [copy.deepcopy(DEFAULT_SOURCE)],
                "carriers": loaded.get("carriers") or [],
                "users": loaded.get("users") if isinstance(loaded.get("users"), list) else [],
                "xmltv_publications": loaded.get("xmltv_publications")
                if isinstance(loaded.get("xmltv_publications"), list) else [],
                "general_settings": validate_general_settings(
                    loaded.get("general_settings") or DEFAULT_GENERAL_SETTINGS),
            }
            for source in self.data["sources"]:
                source.setdefault("source_type", "xmltv")
                if source["source_type"] == "parse_xml" and not re.fullmatch(
                        r"[a-f0-9]{32}", str(source.get("parse_token") or "")):
                    source["parse_token"] = uuid.uuid4().hex
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        os.chmod(self.path, 0o600)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.data)


class GuideCache:
    def __init__(self, cache_dir: Path | None = None, sync_seconds: int = SOURCE_SYNC_SECONDS,
                 sync_mode: str = "interval", daily_time: str = "04:15"):
        self.lock = threading.RLock()
        self.entries: dict[str, dict[str, Any]] = {}
        self.errors: dict[str, dict[str, Any]] = {}
        self.cache_dir = cache_dir
        self.sync_seconds = int(sync_seconds)
        self.sync_mode = sync_mode
        self.daily_time = daily_time
        self.history_path = cache_dir / "source-sync-history.json" if cache_dir else None
        self.history: list[dict[str, Any]] = []
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
        if self.history_path and self.history_path.is_file():
            try:
                loaded = json.loads(self.history_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    self.history = loaded[-500:]
            except (OSError, ValueError):
                self.history = []

    def next_refresh_at(self, fetched_at: int | float) -> int:
        if self.sync_mode != "daily":
            return int(fetched_at) + self.sync_seconds
        hour, minute = (int(item) for item in self.daily_time.split(":"))
        fetched = datetime.fromtimestamp(float(fetched_at), BRAZIL_TZ)
        candidate = fetched.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.timestamp() <= float(fetched_at):
            candidate += timedelta(days=1)
        return int(candidate.timestamp())

    def _record_sync(self, event: dict[str, Any]) -> None:
        event = {"id": uuid.uuid4().hex, **event}
        with self.lock:
            self.history = ([event] + self.history)[:500]
            if self.history_path:
                temporary = self.history_path.with_name(f".{self.history_path.name}.{uuid.uuid4().hex}.tmp")
                temporary.write_text(json.dumps(self.history, ensure_ascii=False, indent=2), encoding="utf-8")
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.history_path)

    def sync_history(self, source_id: str = "") -> list[dict[str, Any]]:
        with self.lock:
            events = copy.deepcopy(self.history)
        return [event for event in events if not source_id or event.get("source_id") == source_id]

    def _parse_cache_path(self, source: dict[str, Any]) -> Path | None:
        token = str(source.get("parse_token") or "")
        if not self.cache_dir or source.get("source_type") != "parse_xml" or not re.fullmatch(
                r"[a-f0-9]{32}", token):
            return None
        return self.cache_dir / f"{token}.xml"

    def _status_path(self, source_id: str) -> Path | None:
        if not self.cache_dir:
            return None
        key = hashlib.sha256(source_id.encode("utf-8")).hexdigest()
        return self.cache_dir / f"source-{key}.status.json"

    def _guide_path(self, source_id: str) -> Path | None:
        if not self.cache_dir:
            return None
        key = hashlib.sha256(source_id.encode("utf-8")).hexdigest()
        return self.cache_dir / f"source-{key}.xml"

    def _persist_guide(self, source_id: str, payload: bytes) -> None:
        path = self._guide_path(source_id)
        if not path:
            return
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(payload)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def _persist_status(self, source_id: str, parsed: dict[str, Any]) -> None:
        path = self._status_path(source_id)
        if not path:
            return
        fetched_at = int(parsed["fetched_at"])
        status = {
            "channel_count": len(parsed["channels"]),
            "programme_count": sum(map(len, parsed["programmes"].values())),
            "fetched_at": fetched_at,
            "next_refresh_at": self.next_refresh_at(fetched_at),
        }
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def _persist_normalized(self, source: dict[str, Any], payload: bytes,
                            diagnostics: dict[str, Any]) -> None:
        path = self._parse_cache_path(source)
        if not path:
            return
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(payload)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        diagnostics_path = path.with_suffix(".diagnostics.json")
        diagnostics_temporary = diagnostics_path.with_name(
            f".{diagnostics_path.name}.{uuid.uuid4().hex}.tmp")
        diagnostics_temporary.write_text(json.dumps(diagnostics, ensure_ascii=False), encoding="utf-8")
        os.chmod(diagnostics_temporary, 0o600)
        os.replace(diagnostics_temporary, diagnostics_path)

    def _load_persisted(self, source: dict[str, Any]) -> dict[str, Any] | None:
        path = self._parse_cache_path(source)
        if not path or not path.is_file():
            path = self._guide_path(source["id"])
        if not path or not path.is_file() or path.stat().st_size > MAX_XMLTV:
            return None
        payload = path.read_bytes()
        parsed = parse_xmltv(payload)
        diagnostics = None
        diagnostics_path = path.with_suffix(".diagnostics.json")
        if diagnostics_path.is_file() and diagnostics_path.stat().st_size <= 4 * 1024 * 1024:
            try:
                diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                diagnostics = None
        normalization = copy.deepcopy(diagnostics) if isinstance(diagnostics, dict) else {
            "channels": len(parsed["channels"]),
            "programmes": sum(map(len, parsed["programmes"].values())),
        }
        normalization["persisted_fallback"] = True
        parsed.update({
            "fetched_at": path.stat().st_mtime, "bytes": len(payload),
            "source_id": source["id"], "normalized_payload": payload,
            "normalization": normalization,
        })
        return parsed

    @staticmethod
    def download(url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": f"OMNIEPG/{PRODUCT_VERSION}"})
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_XMLTV:
                    raise ApiError("A fonte XMLTV excede o limite de 96 MiB")
                chunks.append(chunk)
        payload = b"".join(chunks)
        if payload[:2] == b"\x1f\x8b" or url.lower().endswith(".gz"):
            payload = gzip.decompress(payload)
            if len(payload) > MAX_XMLTV:
                raise ApiError("O XMLTV descompactado excede o limite de 96 MiB")
        prefix = payload[:4096].lstrip().lower()
        if ("text/html" in content_type or prefix.startswith((b"<html", b"<!doctype html", b"<span"))) and b"<tv" not in prefix:
            raise ApiError("A fonte retornou uma página HTML em vez de XMLTV; verifique limite de downloads, autenticação ou endereço")
        return payload

    def get(self, source: dict[str, Any], force: bool = False) -> dict[str, Any]:
        source_id = source["id"]
        started_at = now_epoch()
        with self.lock:
            cached = self.entries.get(source_id)
            if cached and not force and time.time() - cached["fetched_at"] < GUIDE_CACHE_SECONDS:
                return cached
        if not cached and not force:
            cached = self._load_persisted(source)
            if cached:
                with self.lock:
                    self.entries[source_id] = cached
                return cached
        try:
            payload = self.download(source["url"])
            stats = None
            if source.get("source_type", "xmltv") == "parse_xml":
                payload, stats = normalize_uploaded_xmltv(payload, collect_errors=True)
            parsed = parse_xmltv(payload)
            parsed.update({"fetched_at": time.time(), "bytes": len(payload), "source_id": source_id})
            if stats is not None:
                parsed.update({"normalized_payload": payload, "normalization": stats})
                self._persist_normalized(source, payload, stats)
        except Exception as error:
            self._record_sync({"source_id": source_id, "source_name": source.get("name", source_id),
                               "status": "error", "started_at": started_at, "finished_at": now_epoch(),
                               "duration_ms": max(0, int((time.time() - started_at) * 1000)),
                               "error": str(error)})
            with self.lock:
                failed_at = now_epoch()
                self.errors[source_id] = {"message": str(error), "at": failed_at,
                                          "next_retry_at": failed_at + self.sync_seconds}
            # Never replace a previously validated guide with a broken refresh.
            if cached:
                return cached
            persisted = self._load_persisted(source)
            if persisted:
                with self.lock:
                    self.entries[source_id] = persisted
                return persisted
            raise
        with self.lock:
            previous = self.entries.get(source_id)
            self.entries[source_id] = parsed
            self.errors.pop(source_id, None)
        digest = guide_content_fingerprint(parsed)
        payload_digest = hashlib.sha256(payload).hexdigest()
        previous_digest = str(previous.get("content_sha256") or "") if previous else ""
        parsed["content_sha256"] = digest
        self._persist_guide(source_id, payload)
        self._persist_status(source_id, parsed)
        self._record_sync({"source_id": source_id, "source_name": source.get("name", source_id),
                           "status": "success", "started_at": started_at, "finished_at": now_epoch(),
                           "duration_ms": max(0, int((time.time() - started_at) * 1000)),
                           "changed": not previous_digest or previous_digest != digest,
                           "sha256": digest, "payload_sha256": payload_digest,
                           "bytes": len(payload),
                           "channels": len(parsed["channels"]),
                           "programmes": sum(map(len, parsed["programmes"].values()))})
        return parsed

    def invalidate(self, source_id: str) -> None:
        with self.lock:
            self.entries.pop(source_id, None)

    def status(self, source: dict[str, Any]) -> dict[str, Any] | None:
        with self.lock:
            cached = self.entries.get(source["id"])
            if cached:
                fetched_at = int(cached["fetched_at"])
                return {
                    "channel_count": len(cached["channels"]),
                    "programme_count": sum(map(len, cached["programmes"].values())),
                    "fetched_at": fetched_at,
                    "next_refresh_at": self.next_refresh_at(fetched_at),
                }
        status_path = self._status_path(source["id"])
        if status_path and status_path.is_file() and status_path.stat().st_size <= 4096:
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
                return {
                    "channel_count": int(status["channel_count"]),
                    "programme_count": int(status["programme_count"]),
                    "fetched_at": int(status["fetched_at"]),
                    "next_refresh_at": self.next_refresh_at(int(status["fetched_at"])),
                }
            except (OSError, ValueError, KeyError, TypeError):
                pass
        path = self._parse_cache_path(source)
        diagnostics_path = path.with_suffix(".diagnostics.json") if path else None
        if not path or not path.is_file() or not diagnostics_path or not diagnostics_path.is_file():
            return None
        try:
            diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            fetched_at = int(path.stat().st_mtime)
            return {
                "channel_count": int(diagnostics.get("channels", 0)),
                "programme_count": int(diagnostics.get("programmes", 0)),
                "fetched_at": fetched_at,
                "next_refresh_at": self.next_refresh_at(fetched_at),
            }
        except (OSError, ValueError, TypeError):
            return None


def runtime_source_token(source_id: str) -> str:
    return hashlib.sha256(f"omniepg-runtime:{source_id}".encode()).hexdigest()


def runtime_source_url(source: dict[str, Any]) -> str:
    token = runtime_source_token(str(source.get("id") or ""))
    port = int(os.environ.get("EPG_HTTP_PORT", "9100"))
    return f"http://127.0.0.1:{port}/runtime-xmltv/{token}.xml"


class Supervisor:
    def __init__(self, store: Store, binary: str, log_dir: Path,
                 license_manager: LicenseManager | None = None):
        self.store = store
        self.binary = binary
        self.log_dir = log_dir
        self.diagnostic_dir = self.log_dir.parent / "diagnostics"
        self.license = license_manager or LicenseManager("", "", "unconfigured")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.diagnostic_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.log_handles: dict[str, Any] = {}
        self.runtime: dict[str, dict[str, Any]] = {}
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="epg-supervisor", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.stopping.set()
        self.thread.join(timeout=4)
        with self.lock:
            for carrier_id in list(self.processes):
                self._stop_locked(carrier_id, "stopped")

    def _source_for(self, carrier: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.store.snapshot()
        source = next((item for item in snapshot["sources"] if item["id"] == carrier["source_id"]), None)
        if not source:
            raise ApiError("A fonte XMLTV da portadora não existe")
        return source

    def _environment(self, carrier: dict[str, Any], source: dict[str, Any]) -> dict[str, str]:
        snapshot = self.store.snapshot()
        settings = snapshot.get("general_settings", DEFAULT_GENERAL_SETTINGS)
        source_by_id = {item["id"]: item for item in snapshot["sources"]}
        services = []
        for service in carrier["services"]:
            resolved = copy.deepcopy(service)
            service_source = source_by_id.get(service.get("source_id") or carrier["source_id"])
            if not service_source:
                raise ApiError(f"A fonte XMLTV do canal {service['name']} não existe")
            resolved["source_url"] = runtime_source_url(service_source)
            services.append(resolved)
        environment = os.environ.copy()
        environment.update({
            "EPG_STREAM_ID": carrier["id"],
            "EPG_STREAM_NAME": carrier["name"],
            "EPG_SOURCE_URL": runtime_source_url(source),
            "EPG_SERVICES_JSON": json.dumps(services, ensure_ascii=False, separators=(",", ":")),
            "EPG_TSID": str(carrier["transport_stream_id"]),
            "EPG_ONID": str(carrier["original_network_id"]),
            "EPG_DESTINATION": carrier["destination"],
            "EPG_PORT": str(carrier["port"]),
            "EPG_INTERFACE": carrier["interface_address"],
            "EPG_PMT_PID": str(carrier["pmt_pid"]),
            "EPG_BITRATE": str(carrier["bitrate"]),
            "EPG_TTL": str(carrier["ttl"]),
            "EPG_SIGNAL_VERSION": str(int(carrier.get("signalling_version", 0)) % 32),
            "EPG_CLOCK_UTC_OFFSET_MINUTES": str(
                int(carrier.get("clock_utc_offset_minutes", -180))),
            "EPG_CLOCK_CORRECTION_SECONDS": str(
                int(carrier.get("clock_correction_minutes", 0)) * 60),
            "EPG_DIAGNOSTIC_DIR": str(self.diagnostic_dir),
            "EPG_GUIDE_REFRESH_SECONDS": str(settings["emitter_refresh_minutes"] * 60),
            "EPG_GUIDE_RETRY_SECONDS": str(settings["emitter_retry_minutes"] * 60),
        })
        return environment

    def _start_locked(self, carrier: dict[str, Any]) -> None:
        channel_count = sum(len(item.get("services", [])) for item in self.store.snapshot()["carriers"])
        try:
            self.license.require(channel_count)
        except LicenseError as error:
            raise ApiError(str(error), HTTPStatus.PAYMENT_REQUIRED) from error
        carrier_id = carrier["id"]
        self._stop_locked(carrier_id, "stopped")
        # Reserve a new PSI/SI version before every process generation. This
        # survives container restarts and makes muxes/receivers invalidate
        # cached PAT/PMT/SDT/EIT without requiring a manual Parse Program.
        with self.store.lock:
            stored = next((item for item in self.store.data["carriers"]
                           if item["id"] == carrier_id), None)
            if not stored:
                raise ApiError("Portadora não encontrada", HTTPStatus.NOT_FOUND)
            signal_version = (int(stored.get("signalling_version", 0)) + 1) % 32
            stored["signalling_version"] = signal_version
            self.store.save()
        carrier = copy.deepcopy(carrier)
        carrier["signalling_version"] = signal_version
        source = self._source_for(carrier)
        log_path = self.log_dir / f"{carrier_id}.log"
        handle = log_path.open("ab", buffering=0)
        handle.write(f"\n[{datetime.now().isoformat()}] iniciando {carrier['name']}\n".encode())
        process = subprocess.Popen(
            [self.binary], env=self._environment(carrier, source), stdout=handle,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        self.processes[carrier_id] = process
        self.log_handles[carrier_id] = handle
        runtime = self.runtime.setdefault(carrier_id, {})
        runtime.update({
            "status": "running", "active": True, "pid": process.pid,
            "started_at": now_epoch(), "last_error": "",
            "manual_stop": False, "next_retry": 0,
            "restart_count": int(runtime.get("restart_count", 0)),
        })

    def _stop_locked(self, carrier_id: str, status: str = "manual-stop") -> None:
        process = self.processes.pop(carrier_id, None)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        handle = self.log_handles.pop(carrier_id, None)
        if handle:
            handle.close()
        runtime = self.runtime.setdefault(carrier_id, {"restart_count": 0})
        runtime.update({"status": status, "active": False, "pid": 0})
        if status == "manual-stop":
            runtime["manual_stop"] = True

    def action(self, carrier_id: str, action: str) -> None:
        snapshot = self.store.snapshot()
        carrier = next((item for item in snapshot["carriers"] if item["id"] == carrier_id), None)
        if not carrier:
            raise ApiError("Portadora não encontrada", HTTPStatus.NOT_FOUND)
        with self.lock:
            if action == "stop":
                self._stop_locked(carrier_id)
            elif action in {"start", "restart"}:
                self.runtime.setdefault(carrier_id, {})["manual_stop"] = False
                self._start_locked(carrier)
            else:
                raise ApiError("Ação inválida")

    def audit(self, carrier_id: str, seconds: int = 8) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", carrier_id):
            raise ApiError("ID de portadora inválido")
        snapshot = self.store.snapshot()
        carrier = next((item for item in snapshot["carriers"] if item["id"] == carrier_id), None)
        if not carrier:
            raise ApiError("Portadora não encontrada", HTTPStatus.NOT_FOUND)
        with self.lock:
            process = self.processes.get(carrier_id)
            if not process or process.poll() is not None:
                raise ApiError("Inicie a portadora antes de executar o simulador")
        seconds = max(2, min(int(seconds), 8))
        request_path = self.diagnostic_dir / f"{carrier_id}.request"
        sample_path = self.diagnostic_dir / f"{carrier_id}.ts"
        temporary_path = self.diagnostic_dir / f"{carrier_id}.ts.tmp"
        for path in (sample_path, temporary_path):
            path.unlink(missing_ok=True)
        request_temporary = request_path.with_suffix(".request.tmp")
        request_temporary.write_text(str(seconds), encoding="ascii")
        os.replace(request_temporary, request_path)
        deadline = time.monotonic() + seconds + 8
        while time.monotonic() < deadline:
            if sample_path.exists() and sample_path.stat().st_size:
                break
            if process.poll() is not None:
                raise ApiError("O emissor encerrou durante a captura")
            time.sleep(0.15)
        if not sample_path.exists() or not sample_path.stat().st_size:
            raise ApiError("O emissor não entregou a amostra no tempo esperado")
        command = [
            "python3", os.environ.get("EPG_AUDITOR_SCRIPT", "/app/verify_isdbtb_ts.py"),
            str(sample_path), "--tsid", str(carrier["transport_stream_id"]),
            "--onid", str(carrier["original_network_id"]), "--epg-only",
        ]
        for index, service in enumerate(carrier["services"]):
            command.extend(["--service-id", str(service["service_id"])])
            command.extend(["--pmt-pid", str(carrier["pmt_pid"] + index)])
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20)
        try:
            report = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise ApiError("O auditor não conseguiu interpretar a amostra") from error
        report["carrier"] = {
            "id": carrier["id"], "name": carrier["name"],
            "destination": carrier["destination"], "port": carrier["port"],
            "interface_address": carrier["interface_address"],
            "transport_stream_id": carrier["transport_stream_id"],
            "original_network_id": carrier["original_network_id"],
            "services": [{"name": item["name"], "service_id": item["service_id"]}
                         for item in carrier["services"]],
        }
        report["required_passthrough"] = ["0x0012 -> 0x0012", "0x0014 -> 0x0014"]
        report["sample_seconds"] = seconds
        return report

    def restart_all(self) -> dict[str, Any]:
        snapshot = self.store.snapshot()
        channel_count = sum(len(item.get("services", [])) for item in snapshot["carriers"])
        try:
            self.license.require(channel_count, force=True)
        except LicenseError as error:
            raise ApiError(str(error), HTTPStatus.PAYMENT_REQUIRED) from error
        restarted: list[str] = []
        errors: list[dict[str, str]] = []
        with self.lock:
            eligible = [carrier for carrier in snapshot["carriers"]
                        if carrier["id"] in self.processes
                        or not self.runtime.get(carrier["id"], {}).get(
                            "manual_stop", not carrier.get("auto_start", False))]
            for carrier in eligible:
                try:
                    self.runtime.setdefault(carrier["id"], {})["manual_stop"] = False
                    self._start_locked(carrier)
                    restarted.append(carrier["id"])
                except Exception as error:
                    errors.append({"id": carrier["id"], "error": str(error)})
        return {"result": "ok" if not errors else "partial",
                "restarted": len(restarted), "errors": errors}

    def refresh_for_sources(self, source_ids: set[str]) -> list[str]:
        snapshot = self.store.snapshot()
        affected = [carrier for carrier in snapshot["carriers"] if any(
            (service.get("source_id") or carrier.get("source_id")) in source_ids
            for service in carrier.get("services", []))]
        refreshed: list[str] = []
        with self.lock:
            for carrier in affected:
                process = self.processes.get(carrier["id"])
                if not process or process.poll() is not None:
                    continue
                try:
                    if HOT_RELOAD_SIGNAL is None:
                        raise RuntimeError("SIGUSR1 não está disponível nesta plataforma")
                    process.send_signal(HOT_RELOAD_SIGNAL)
                    refreshed.append(carrier["id"])
                except Exception as error:
                    print(f"Falha no hot reload do cache em {carrier['id']}: {error}", flush=True)
        return refreshed

    def remove(self, carrier_id: str) -> None:
        with self.lock:
            self._stop_locked(carrier_id)
            self.runtime.pop(carrier_id, None)

    def state(self) -> dict[str, Any]:
        snapshot = self.store.snapshot()
        with self.lock:
            carriers = []
            for carrier in snapshot["carriers"]:
                value = copy.deepcopy(carrier)
                value.update(copy.deepcopy(self.runtime.get(carrier["id"], {
                    "status": "stopped", "active": False, "pid": 0,
                    "restart_count": 0, "last_error": "", "manual_stop": not carrier["auto_start"],
                })))
                carriers.append(value)
        return {"product": PRODUCT_NAME, "version": PRODUCT_VERSION, "carriers": carriers,
                "license": self.license.public_status()}

    def _loop(self) -> None:
        while not self.stopping.wait(1):
            snapshot = self.store.snapshot()
            configured = {carrier["id"]: carrier for carrier in snapshot["carriers"]}
            channel_count = sum(len(item.get("services", [])) for item in snapshot["carriers"])
            license_status = self.license.check(channel_count)
            with self.lock:
                if not license_status["valid"]:
                    for carrier_id in list(self.processes):
                        self._stop_locked(carrier_id, "license-blocked")
                    for carrier_id, carrier in configured.items():
                        runtime = self.runtime.setdefault(carrier_id, {
                            "restart_count": 0, "manual_stop": not carrier["auto_start"]})
                        runtime.update({"status": "license-blocked", "active": False, "pid": 0,
                                        "last_error": license_status["reason"]})
                    continue
                for carrier_id, process in list(self.processes.items()):
                    code = process.poll()
                    if code is None:
                        continue
                    self.processes.pop(carrier_id, None)
                    handle = self.log_handles.pop(carrier_id, None)
                    if handle:
                        handle.close()
                    runtime = self.runtime.setdefault(carrier_id, {})
                    runtime.update({
                        "status": "error", "active": False, "pid": 0,
                        "last_error": f"Emissor encerrou com código {code}",
                        "restart_count": int(runtime.get("restart_count", 0)) + 1,
                        "next_retry": time.time() + 3,
                    })
                for carrier_id, carrier in configured.items():
                    runtime = self.runtime.setdefault(carrier_id, {
                        "status": "stopped", "active": False, "pid": 0,
                        "restart_count": 0, "last_error": "",
                        "manual_stop": not carrier["auto_start"], "next_retry": 0,
                    })
                    # auto_start defines only the initial state. After a manual
                    # start, keep supervising until an explicit manual stop.
                    should_run = not runtime.get("manual_stop", False)
                    if should_run and carrier_id not in self.processes and time.time() >= runtime.get("next_retry", 0):
                        try:
                            self._start_locked(carrier)
                        except Exception as error:
                            runtime.update({"status": "error", "last_error": str(error), "next_retry": time.time() + 5})


class Application:
    def __init__(self, data_dir: Path, binary: str,
                 bootstrap_user: str = "", bootstrap_password: str = "",
                 public_base_url: str = "", license_server_url: str = "",
                 license_key_file: str = "", license_installation_id: str = "",
                 license_check_seconds: int = 43200):
        self.data_dir = data_dir
        self.public_base_url = public_base_url.strip().rstrip("/")
        self.logo_dir = data_dir / "logos"
        self.publication_dir = data_dir / "xmltv-publications"
        self.logo_dir.mkdir(parents=True, exist_ok=True)
        self.publication_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(data_dir / "epg-product.json")
        settings = self.store.snapshot()["general_settings"]
        primary_license_url = settings.get("license_primary_url") or license_server_url
        secondary_license_url = (settings.get("license_secondary_url") or
                                 os.environ.get("EPG_LICENSE_SECONDARY_SERVER_URL", ""))
        self.license = LicenseManager(
            primary_license_url, license_key_file, license_installation_id,
            license_check_seconds, secondary_server_url=secondary_license_url,
        )
        if not settings.get("license_primary_url") and primary_license_url:
            with self.store.lock:
                self.store.data["general_settings"]["license_primary_url"] = primary_license_url
                self.store.data["general_settings"]["license_secondary_url"] = secondary_license_url
                self.store.save()
        self._bootstrap_user(bootstrap_user, bootstrap_password)
        self._migrate_logos()
        settings = self.store.snapshot()["general_settings"]
        self.guides = GuideCache(
            data_dir / "parsed-xml-cache", settings["xmltv_sync_minutes"] * 60,
            settings["xmltv_sync_mode"], settings["xmltv_daily_time"])
        self.source_sync_stopping = threading.Event()
        self.source_sync_thread = threading.Thread(
            target=self._source_sync_loop, name="xmltv-background-sync", daemon=True)
        self.supervisor = Supervisor(self.store, binary, data_dir / "logs", self.license)
        self.supervisor.start()
        self.source_sync_thread.start()

    def _source_sync_loop(self) -> None:
        # Aguarda a inicialização da licença e dos emissores antes da primeira rodada.
        if self.source_sync_stopping.wait(5):
            return
        while not self.source_sync_stopping.is_set():
            self.sync_due_sources_once()
            self.source_sync_stopping.wait(60)

    def sync_due_sources_once(self) -> dict[str, Any]:
        snapshot = self.store.snapshot()
        channel_count = sum(len(item.get("services", [])) for item in snapshot["carriers"])
        if not self.license.check(channel_count).get("valid"):
            return {"synchronized": 0, "errors": [], "license_blocked": True}
        synchronized = 0
        changed_sources: set[str] = set()
        errors = []
        current_time = int(time.time())
        for source in snapshot["sources"]:
            if self.source_sync_stopping.is_set():
                break
            status = self.guides.status(source)
            with self.guides.lock:
                source_error = copy.deepcopy(self.guides.errors.get(source["id"]))
            if source_error and current_time < int(source_error.get("next_retry_at", 0)):
                continue
            cached_entries = getattr(self.guides, "entries", None)
            loaded_in_memory = (source["id"] in cached_entries
                                if isinstance(cached_entries, dict) else True)
            if status and loaded_in_memory and current_time < int(status["next_refresh_at"]):
                continue
            try:
                with self.guides.lock:
                    entries = self.guides.entries if isinstance(self.guides.entries, dict) else {}
                    previous_digest = entries.get(source["id"], {}).get("content_sha256") or ""
                guide = self.guides.get(source, force=True)
                current_digest = guide.get("content_sha256") if isinstance(guide, dict) else ""
                if (isinstance(previous_digest, str) and isinstance(current_digest, str)
                        and previous_digest and current_digest and previous_digest != current_digest):
                    changed_sources.add(source["id"])
                synchronized += 1
            except Exception as error:
                errors.append({"source_id": source["id"], "error": str(error)})
                print(f"Falha na sincronização XMLTV de {source['id']}: {error}", flush=True)
        settings = snapshot.get("general_settings", DEFAULT_GENERAL_SETTINGS)
        refreshed = []
        if changed_sources and settings.get("detect_cache_updates"):
            refreshed = self.supervisor.refresh_for_sources(changed_sources)
        return {"synchronized": synchronized, "errors": errors, "license_blocked": False,
                "changed_sources": sorted(changed_sources), "refreshed": refreshed}

    def save_general_settings(self, request: dict[str, Any]) -> dict[str, Any]:
        current = self.store.snapshot()["general_settings"]
        settings = validate_general_settings({**current, **request})
        with self.store.lock:
            self.store.data["general_settings"] = settings
            self.store.save()
        self.guides.sync_seconds = settings["xmltv_sync_minutes"] * 60
        self.guides.sync_mode = settings["xmltv_sync_mode"]
        self.guides.daily_time = settings["xmltv_daily_time"]
        self.license.set_servers(settings["license_primary_url"], settings["license_secondary_url"])
        self.license.check(self.channel_count(), force=True)
        restarted = self.supervisor.restart_all()
        return {"result": "ok", "settings": settings, **restarted}

    def close(self) -> None:
        self.source_sync_stopping.set()
        self.source_sync_thread.join(timeout=4)
        self.supervisor.close()

    @staticmethod
    def _portable_config(data: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(data)

    def _configuration_envelope(self) -> bytes:
        envelope = {
            "format": "epg-stream-config-backup",
            "format_version": 1,
            "product": PRODUCT_NAME,
            "product_version": PRODUCT_VERSION,
            "exported_at": now_epoch(),
            "data": self._portable_config(self.store.snapshot()),
        }
        return (json.dumps(envelope, ensure_ascii=False, indent=2) + "\n").encode()

    def configuration_backup(self) -> bytes:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            manifest = self._configuration_envelope()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest)
            info.mode = 0o600
            info.mtime = now_epoch()
            archive.addfile(info, io.BytesIO(manifest))
            for path in sorted(self.data_dir.rglob("*")):
                relative = path.relative_to(self.data_dir)
                if not path.is_file() or path.is_symlink() or relative.parts[0] == "config-backups":
                    continue
                if relative.as_posix() == "epg-product.json":
                    portable_store = (json.dumps(json.loads(manifest)["data"], ensure_ascii=False,
                                                 indent=2) + "\n").encode()
                    store_info = tarfile.TarInfo("data/epg-product.json")
                    store_info.size = len(portable_store)
                    store_info.mode = 0o600
                    store_info.mtime = now_epoch()
                    archive.addfile(store_info, io.BytesIO(portable_store))
                    continue
                archive.add(path, arcname=(Path("data") / relative).as_posix(), recursive=False)
        return output.getvalue()

    @staticmethod
    def _extract_configuration_backup(payload: bytes, destination: Path) -> dict[str, Any]:
        total = 0
        members = 0
        manifest = None
        try:
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                for member in archive:
                    members += 1
                    parts = Path(member.name).parts
                    if (members > MAX_BACKUP_FILES or member.issym() or member.islnk() or
                            member.isdev() or not parts or Path(member.name).is_absolute() or
                            ".." in parts or (parts[0] not in {"manifest.json", "data"})):
                        raise ApiError("O backup contém caminho ou tipo de arquivo inseguro")
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise ApiError("O backup contém uma entrada incompatível")
                    total += member.size
                    if total > MAX_BACKUP_CONTENT:
                        raise ApiError("O conteúdo descompactado do backup excede 2 GiB")
                    source = archive.extractfile(member)
                    if source is None:
                        raise ApiError("Não foi possível ler um arquivo do backup")
                    if member.name == "manifest.json":
                        manifest = source.read()
                        continue
                    target = destination.joinpath(*parts[1:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("wb") as handle:
                        shutil.copyfileobj(source, handle)
                    os.chmod(target, member.mode & 0o777 or 0o600)
        except (tarfile.TarError, OSError) as error:
            raise ApiError(f"Pacote de backup inválido: {error}")
        if manifest is None:
            raise ApiError("O manifesto não foi encontrado no backup")
        restored = validate_config_backup(manifest)
        config_path = destination / "epg-product.json"
        if not config_path.is_file():
            raise ApiError("A configuração principal não foi encontrada em data/")
        file_config = json.loads(config_path.read_bytes())
        if file_config != json.loads(manifest)["data"]:
            raise ApiError("A configuração de data/ diverge do manifesto do backup")
        return restored

    def restore_configuration(self, payload: bytes) -> dict[str, Any]:
        staging_root = Path(tempfile.mkdtemp(prefix="epg-restore-", dir=self.data_dir.parent))
        staging_data = staging_root / "data"
        staging_data.mkdir()
        try:
            restored = self._extract_configuration_backup(payload, staging_data)
        except Exception:
            shutil.rmtree(staging_root, ignore_errors=True)
            raise
        backup_dir = self.data_dir / "config-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        previous_path = backup_dir / f"pre-restore-{timestamp}-{uuid.uuid4().hex[:8]}.tar.gz"
        previous_path.write_bytes(self.configuration_backup())
        os.chmod(previous_path, 0o600)
        for path in list(self.data_dir.iterdir()):
            if path.name == "config-backups":
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        for path in list(staging_data.iterdir()):
            shutil.move(str(path), self.data_dir / path.name)
        shutil.rmtree(staging_root, ignore_errors=True)
        with self.store.lock:
            self.store.data = restored
        with self.guides.lock:
            self.guides.entries.clear()
        return {"result": "ok", "restart": True,
                "message": "Configuração restaurada; o serviço será reiniciado"}

    def channel_count(self, replacement: dict[str, Any] | None = None) -> int:
        carriers = self.store.snapshot()["carriers"]
        total = 0
        replaced = False
        for carrier in carriers:
            if replacement and carrier["id"] == replacement["id"]:
                total += len(replacement.get("services", []))
                replaced = True
            else:
                total += len(carrier.get("services", []))
        if replacement and not replaced:
            total += len(replacement.get("services", []))
        return total

    def require_license(self, channel_count: int | None = None, force: bool = False) -> dict[str, Any]:
        try:
            return self.license.require(
                self.channel_count() if channel_count is None else channel_count, force)
        except LicenseError as error:
            raise ApiError(str(error), HTTPStatus.PAYMENT_REQUIRED) from error

    def install_license_key(self, request: dict[str, Any]) -> dict[str, Any]:
        key = str(request.get("key") or "").strip()
        try:
            status = self.license.install_key(key, self.channel_count())
        except LicenseError as error:
            raise ApiError(str(error)) from error
        return {"result": "ok", "license": status}

    @staticmethod
    def _write_atomic(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, path)

    def _store_logo_files(self, carrier_id: str, service_id: str,
                          payload: bytes) -> tuple[Path, dict[str, str], int]:
        directory = self.logo_dir / carrier_id
        preview = normalize_logo_png(payload)
        variants = build_arib_logo_variants(payload)
        preview_path = directory / f"{service_id}-preview.png"
        self._write_atomic(preview_path, preview)
        variant_paths: dict[str, str] = {}
        for logo_type, encoded in variants.items():
            path = directory / f"{service_id}-type-{logo_type:02d}.png"
            self._write_atomic(path, encoded)
            variant_paths[str(logo_type)] = str(path)
        return preview_path, variant_paths, sum(len(item) for item in variants.values())

    def _migrate_logos(self) -> None:
        """Convert the v1.3 single-logo format before emitters are started."""
        changed = False
        with self.store.lock:
            for carrier in self.store.data["carriers"]:
                carrier.setdefault("signalling_version", 0)
                carrier_changed = False
                for service in carrier.get("services", []):
                    logo = service.get("logo") if isinstance(service.get("logo"), dict) else {}
                    if not logo.get("enabled") or logo.get("variants"):
                        continue
                    legacy = Path(str(logo.get("path") or ""))
                    try:
                        resolved = legacy.resolve()
                        if self.logo_dir.resolve() not in resolved.parents or not resolved.is_file():
                            continue
                        preview_path, variants, transmitted_bytes = self._store_logo_files(
                            carrier["id"], service["id"], resolved.read_bytes())
                    except Exception:
                        continue
                    logo.update({
                        "path": str(preview_path), "variants": variants,
                        "logo_version": (int(logo.get("logo_version", 0)) + 1) % 4096,
                        "bytes": transmitted_bytes, "updated_at": now_epoch(),
                    })
                    carrier_changed = True
                    changed = True
                if carrier_changed:
                    carrier["signalling_version"] = (int(carrier.get("signalling_version", 0)) + 1) % 32
            if changed:
                self.store.save()

    def _bootstrap_user(self, username: str, password: str) -> None:
        with self.store.lock:
            if self.store.data["users"]:
                return
            if not username or len(password) < PASSWORD_MINIMUM:
                raise ApiError(
                    "O primeiro início exige EPG_ADMIN_USER e EPG_ADMIN_PASSWORD "
                    f"com pelo menos {PASSWORD_MINIMUM} caracteres"
                )
            created = now_epoch()
            user = {
                "id": slug_id("user"),
                "username": normalized_username(username),
                "display_name": "Administrador",
                "role": "admin",
                "enabled": True,
                "created_at": created,
                "updated_at": created,
                **password_record(password),
            }
            self.store.data["users"] = [user]
            self.store.save()

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        candidate = username.strip().lower()
        for user in self.store.snapshot()["users"]:
            if (user.get("enabled") and
                    hmac.compare_digest(str(user.get("username", "")), candidate) and
                    password_matches(user, password)):
                return public_user(user)
        return None

    def users(self) -> list[dict[str, Any]]:
        return [public_user(user) for user in self.store.snapshot()["users"]]

    @staticmethod
    def _active_admins(users: list[dict[str, Any]]) -> int:
        return sum(1 for user in users if user.get("enabled") and user.get("role") == "admin")

    def save_user(self, request: dict[str, Any]) -> dict[str, Any]:
        user_id = str(request.get("id") or "")
        username = normalized_username(request.get("username"))
        display_name = str(request.get("display_name") or "").strip()
        role = str(request.get("role") or "operator")
        enabled = bool(request.get("enabled", True))
        password = str(request.get("password") or "")
        if not display_name:
            raise ApiError("Informe o nome de exibição")
        if role not in {"admin", "operator"}:
            raise ApiError("Perfil inválido")
        with self.store.lock:
            users = self.store.data["users"]
            if any(item["username"] == username and item["id"] != user_id for item in users):
                raise ApiError("Este nome de usuário já está em uso")
            existing_index = next(
                (index for index, item in enumerate(users) if item["id"] == user_id), None
            )
            timestamp = now_epoch()
            if existing_index is None:
                if user_id:
                    raise ApiError("Usuário não encontrado", HTTPStatus.NOT_FOUND)
                if not password:
                    raise ApiError("Informe a senha do novo usuário")
                user = {
                    "id": slug_id("user"), "created_at": timestamp,
                    **password_record(password),
                }
            else:
                user = copy.deepcopy(users[existing_index])
                if password:
                    user.update(password_record(password))
            user.update({
                "username": username, "display_name": display_name,
                "role": role, "enabled": enabled, "updated_at": timestamp,
            })
            proposed = copy.deepcopy(users)
            if existing_index is None:
                proposed.append(user)
            else:
                proposed[existing_index] = user
            if self._active_admins(proposed) < 1:
                raise ApiError("O sistema precisa manter ao menos um administrador ativo")
            self.store.data["users"] = proposed
            self.store.save()
        return {"result": "ok", "user": public_user(user)}

    def delete_user(self, user_id: str, actor_id: str) -> dict[str, Any]:
        if user_id == actor_id:
            raise ApiError("Não é possível excluir o usuário da sessão atual")
        with self.store.lock:
            users = self.store.data["users"]
            proposed = [user for user in users if user["id"] != user_id]
            if len(proposed) == len(users):
                raise ApiError("Usuário não encontrado", HTTPStatus.NOT_FOUND)
            if self._active_admins(proposed) < 1:
                raise ApiError("O sistema precisa manter ao menos um administrador ativo")
            self.store.data["users"] = proposed
            self.store.save()
        return {"result": "ok"}

    def source(self, source_id: str) -> dict[str, Any]:
        source = next((item for item in self.store.snapshot()["sources"] if item["id"] == source_id), None)
        if not source:
            raise ApiError("Fonte XMLTV não encontrada", HTTPStatus.NOT_FOUND)
        return source

    def save_source(self, request: dict[str, Any]) -> dict[str, Any]:
        request = copy.deepcopy(request)
        requested_id = str(request.get("id") or "")
        previous = next((item for item in self.store.snapshot()["sources"]
                         if item["id"] == requested_id), None)
        if previous and previous.get("parse_token"):
            request["parse_token"] = previous["parse_token"]
        source = validate_source(request)
        with self.store.lock:
            items = self.store.data["sources"]
            existing = next((index for index, item in enumerate(items) if item["id"] == source["id"]), None)
            if source["is_default"]:
                for item in items:
                    item["is_default"] = False
            if existing is None:
                items.append(source)
            else:
                items[existing] = source
            if not any(item["is_default"] for item in items):
                items[0]["is_default"] = True
            self.store.save()
        self.guides.invalidate(source["id"])
        # The source URL is copied into a child process at startup. Restart
        # only active carriers that use the edited source.
        snapshot = self.store.snapshot()
        running = {
            item["id"] for item in self.supervisor.state()["carriers"]
            if item.get("active")
        }
        for carrier in snapshot["carriers"]:
            uses_source = carrier["source_id"] == source["id"] or any(
                service.get("source_id") == source["id"] for service in carrier["services"]
            )
            if uses_source and carrier["id"] in running:
                self.supervisor.action(carrier["id"], "restart")
        return {"result": "ok", "id": source["id"]}

    def parsed_source_payload(self, token: str) -> tuple[bytes, dict[str, Any]]:
        source = next((item for item in self.store.snapshot()["sources"]
                       if item.get("source_type") == "parse_xml"
                       and hmac.compare_digest(str(item.get("parse_token") or ""), token)), None)
        if not source:
            raise ApiError("Fonte Parse-XML não encontrada", HTTPStatus.NOT_FOUND)
        guide = self.guides.get(source)
        payload = guide.get("normalized_payload")
        if not isinstance(payload, bytes):
            raise ApiError("A fonte Parse-XML ainda não possui conteúdo válido",
                           HTTPStatus.SERVICE_UNAVAILABLE)
        return payload, guide.get("normalization") or {}

    def runtime_source_payload(self, token: str) -> tuple[bytes, dict[str, Any]]:
        source = next((item for item in self.store.snapshot()["sources"]
                       if hmac.compare_digest(runtime_source_token(item["id"]), token)), None)
        if not source:
            raise ApiError("Fonte interna não encontrada", HTTPStatus.NOT_FOUND)
        path = self.guides._guide_path(source["id"])
        # Emitters must never wait for an upstream refresh when a last-known-good
        # guide exists. Source synchronization owns network access in background.
        payload = path.read_bytes() if path and path.is_file() else None
        guide = None
        if not isinstance(payload, bytes) or not payload:
            guide = self.guides.get(source)
            payload = guide.get("normalized_payload")
            if not isinstance(payload, bytes) and path and path.is_file():
                payload = path.read_bytes()
        if not isinstance(payload, bytes) or not payload:
            raise ApiError("Cache XMLTV interno indisponível", HTTPStatus.SERVICE_UNAVAILABLE)
        status = self.guides.status(source) or {}
        return payload, {
            "source_id": source["id"],
            "fetched_at": int(status.get("fetched_at") or (path.stat().st_mtime if path else 0)),
            "channels": int(status.get("channel_count", 0)),
            "programmes": int(status.get("programme_count", 0)),
        }

    def delete_source(self, source_id: str, replacement_id: str = "") -> dict[str, Any]:
        affected_carriers: set[str] = set()
        with self.store.lock:
            items = self.store.data["sources"]
            removed = next((item for item in items if item["id"] == source_id), None)
            if not removed:
                raise ApiError("Fonte XMLTV não encontrada", HTTPStatus.NOT_FOUND)
            filtered = [item for item in items if item["id"] != source_id]
            if not filtered:
                raise ApiError("O sistema precisa manter ao menos uma fonte XMLTV")
            referenced = any(
                carrier["source_id"] == source_id or any(
                    service.get("source_id") == source_id for service in carrier["services"])
                for carrier in self.store.data["carriers"]
            )
            replacement = next((item for item in filtered if item["id"] == replacement_id), None)
            if referenced and not replacement:
                raise ApiError("Escolha uma fonte substituta para os canais e portadoras vinculados")
            if replacement_id == source_id:
                raise ApiError("A fonte substituta deve ser diferente da fonte excluída")
            if replacement:
                for carrier in self.store.data["carriers"]:
                    changed = False
                    if carrier["source_id"] == source_id:
                        carrier["source_id"] = replacement_id
                        changed = True
                    for service in carrier["services"]:
                        if service.get("source_id") == source_id:
                            service["source_id"] = replacement_id
                            changed = True
                    if changed:
                        affected_carriers.add(carrier["id"])
                if removed.get("is_default"):
                    for item in filtered:
                        item["is_default"] = item["id"] == replacement_id
            if not any(item["is_default"] for item in filtered):
                filtered[0]["is_default"] = True
            self.store.data["sources"] = filtered
            self.store.save()
        self.guides.invalidate(source_id)
        running = {item["id"] for item in self.supervisor.state()["carriers"] if item.get("active")}
        restarted = []
        for carrier_id in sorted(affected_carriers & running):
            self.supervisor.action(carrier_id, "restart")
            restarted.append(carrier_id)
        return {"result": "ok", "replaced_by": replacement_id, "restarted": restarted}

    @staticmethod
    def _public_version(version: dict[str, Any], current: int, selected_id: str) -> dict[str, Any]:
        result = {key: copy.deepcopy(value) for key, value in version.items() if key != "path"}
        if version["id"] == selected_id:
            result["status"] = "selected"
        elif int(version["valid_until"]) <= current:
            result["status"] = "expired"
        elif int(version["valid_from"]) > current:
            result["status"] = "future"
        else:
            result["status"] = "available"
        return result

    def publications(self) -> list[dict[str, Any]]:
        current = now_epoch()
        result = []
        for publication in self.store.snapshot()["xmltv_publications"]:
            selected = select_publication_version(publication.get("versions", []), current)
            selected_id = selected["id"] if selected else ""
            public_path = f"/xmltv/{publication['token']}.xml"
            result.append({
                "id": publication["id"], "name": publication["name"],
                "public_path": public_path,
                "public_url": f"{getattr(self, 'public_base_url', '')}{public_path}"
                if getattr(self, "public_base_url", "") else "",
                "created_at": publication.get("created_at", 0),
                "updated_at": publication.get("updated_at", 0),
                "selected_version_id": selected_id,
                "versions": [self._public_version(item, current, selected_id)
                             for item in sorted(publication.get("versions", []),
                                                key=lambda value: (value["valid_from"], value.get("uploaded_at", 0)))],
            })
        return sorted(result, key=lambda item: item["name"].casefold())

    def save_publication(self, request: dict[str, Any]) -> dict[str, Any]:
        publication_id = str(request.get("id") or "")
        name = str(request.get("name") or "").strip()
        if not 3 <= len(name) <= 100:
            raise ApiError("O nome da publicação deve possuir de 3 a 100 caracteres")
        timestamp = now_epoch()
        with self.store.lock:
            items = self.store.data["xmltv_publications"]
            existing = next((item for item in items if item["id"] == publication_id), None)
            if existing:
                existing["name"] = name
                existing["updated_at"] = timestamp
                saved = existing
            else:
                if publication_id:
                    raise ApiError("Publicação XMLTV não encontrada", HTTPStatus.NOT_FOUND)
                saved = {
                    "id": slug_id("publication"), "name": name,
                    "token": uuid.uuid4().hex, "versions": [],
                    "created_at": timestamp, "updated_at": timestamp,
                }
                items.append(saved)
            self.store.save()
        public_path = f"/xmltv/{saved['token']}.xml"
        return {"result": "ok", "id": saved["id"], "public_path": public_path,
                "public_url": f"{getattr(self, 'public_base_url', '')}{public_path}"
                if getattr(self, "public_base_url", "") else ""}

    def upload_publication(self, publication_id: str, filename: str, payload: bytes) -> dict[str, Any]:
        snapshot = self.store.snapshot()
        publication = next((item for item in snapshot["xmltv_publications"]
                            if item["id"] == publication_id), None)
        if not publication:
            raise ApiError("Publicação XMLTV não encontrada", HTTPStatus.NOT_FOUND)
        normalized, stats = normalize_uploaded_xmltv(payload)
        version_id = slug_id("version")
        path = self.publication_dir / publication_id / f"{version_id}.xml"
        self._write_atomic(path, normalized)
        os.chmod(path, 0o640)
        timestamp = now_epoch()
        version = {
            "id": version_id,
            "filename": Path(filename or "guide.xml").name[:180] or "guide.xml",
            "path": str(path), "uploaded_at": timestamp,
            "sha256": hashlib.sha256(normalized).hexdigest(), **stats,
        }
        try:
            with self.store.lock:
                stored = next((item for item in self.store.data["xmltv_publications"]
                               if item["id"] == publication_id), None)
                if not stored:
                    raise ApiError("Publicação XMLTV não encontrada", HTTPStatus.NOT_FOUND)
                stored.setdefault("versions", []).append(version)
                stored["updated_at"] = timestamp
                self.store.save()
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {"result": "ok", "version": self._public_version(version, timestamp, version_id)}

    def _publication_version_path(self, version: dict[str, Any]) -> Path:
        path = Path(str(version.get("path") or "")).resolve()
        if self.publication_dir.resolve() not in path.parents or not path.is_file():
            raise ApiError("Arquivo XMLTV publicado não foi encontrado", HTTPStatus.NOT_FOUND)
        return path

    def publication_payload(self, token: str) -> tuple[bytes, dict[str, Any]]:
        publication = next((item for item in self.store.snapshot()["xmltv_publications"]
                            if hmac.compare_digest(str(item.get("token", "")), token)), None)
        if not publication:
            raise ApiError("Publicação XMLTV não encontrada", HTTPStatus.NOT_FOUND)
        selected = select_publication_version(publication.get("versions", []))
        if not selected:
            raise ApiError("A publicação XMLTV ainda não possui arquivos", HTTPStatus.NOT_FOUND)
        return self._publication_version_path(selected).read_bytes(), selected

    def delete_publication_version(self, publication_id: str, version_id: str) -> dict[str, Any]:
        removed: dict[str, Any] | None = None
        with self.store.lock:
            publication = next((item for item in self.store.data["xmltv_publications"]
                                if item["id"] == publication_id), None)
            if not publication:
                raise ApiError("Publicação XMLTV não encontrada", HTTPStatus.NOT_FOUND)
            versions = publication.get("versions", [])
            removed = next((item for item in versions if item["id"] == version_id), None)
            if not removed:
                raise ApiError("Versão XMLTV não encontrada", HTTPStatus.NOT_FOUND)
            publication["versions"] = [item for item in versions if item["id"] != version_id]
            publication["updated_at"] = now_epoch()
            self.store.save()
        path = Path(str(removed.get("path") or "")).resolve()
        if path.is_file() and self.publication_dir.resolve() in path.parents:
            path.unlink()
        return {"result": "ok"}

    def delete_publication(self, publication_id: str) -> dict[str, Any]:
        removed: dict[str, Any] | None = None
        with self.store.lock:
            removed = next((item for item in self.store.data["xmltv_publications"]
                            if item["id"] == publication_id), None)
            if not removed:
                raise ApiError("Publicação XMLTV não encontrada", HTTPStatus.NOT_FOUND)
            self.store.data["xmltv_publications"] = [item for item in self.store.data["xmltv_publications"]
                                                      if item["id"] != publication_id]
            self.store.save()
        directory = (self.publication_dir / publication_id).resolve()
        if directory.is_dir() and self.publication_dir.resolve() in directory.parents:
            for path in directory.iterdir():
                if path.is_file():
                    path.unlink()
            directory.rmdir()
        return {"result": "ok"}

    def save_carrier(self, request: dict[str, Any]) -> dict[str, Any]:
        carrier = validate_carrier(request)
        self.source(carrier["source_id"])
        for service in carrier["services"]:
            if service.get("source_id"):
                self.source(service["source_id"])
        self.require_license(self.channel_count(carrier), force=True)
        with self.store.lock:
            stored = next((item for item in self.store.data["carriers"] if item["id"] == carrier["id"]), None)
            carrier["signalling_version"] = int(stored.get("signalling_version", 0)) if stored else 0
            if stored:
                stored_services = {item["id"]: item for item in stored["services"]}
                for service in carrier["services"]:
                    previous = stored_services.get(service["id"], {})
                    service["logo"] = copy.deepcopy(previous.get("logo", {}))
            collision = next((item for item in self.store.data["carriers"]
                              if item["id"] != carrier["id"] and item["destination"] == carrier["destination"]
                              and item["port"] == carrier["port"]), None)
            if collision:
                raise ApiError(f"O destino já pertence à portadora {collision['name']}")
            items = self.store.data["carriers"]
            existing = next((index for index, item in enumerate(items) if item["id"] == carrier["id"]), None)
            if existing is None:
                items.append(carrier)
            else:
                items[existing] = carrier
            self.store.save()
        self.supervisor.remove(carrier["id"])
        if carrier["auto_start"]:
            self.supervisor.action(carrier["id"], "start")
        return {"result": "ok", "id": carrier["id"]}

    def save_logo(self, request: dict[str, Any]) -> dict[str, Any]:
        carrier_id = str(request.get("carrier_id") or "")
        service_id = str(request.get("service_id") or "")
        was_active = any(item["id"] == carrier_id and item.get("active")
                         for item in self.supervisor.state()["carriers"])
        encoded = str(request.get("data") or "")
        if encoded.startswith("data:image/png;base64,"):
            encoded = encoded.split(",", 1)[1]
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise ApiError("Conteúdo base64 do logo inválido")
        if not payload or len(payload) > MAX_LOGO:
            raise ApiError("O logo PNG deve possuir no máximo 2 MiB")
        width, height = validate_png(payload)
        preview = normalize_logo_png(payload)
        variants = build_arib_logo_variants(payload)
        with self.store.lock:
            carrier = next((item for item in self.store.data["carriers"] if item["id"] == carrier_id), None)
            if not carrier:
                raise ApiError("Portadora não encontrada", HTTPStatus.NOT_FOUND)
            service = next((item for item in carrier["services"] if item["id"] == service_id), None)
            if not service:
                raise ApiError("Canal não encontrado", HTTPStatus.NOT_FOUND)
            directory = self.logo_dir / carrier_id
            path = directory / f"{service_id}-preview.png"
            self._write_atomic(path, preview)
            variant_paths: dict[str, str] = {}
            for logo_type, transmitted in variants.items():
                variant_path = directory / f"{service_id}-type-{logo_type:02d}.png"
                self._write_atomic(variant_path, transmitted)
                variant_paths[str(logo_type)] = str(variant_path)
            previous = service.get("logo") if isinstance(service.get("logo"), dict) else {}
            version = (int(previous.get("logo_version", -1)) + 1) % 4096
            transmitted_bytes = sum(len(item) for item in variants.values())
            service["logo"] = {
                "enabled": True,
                "path": str(path),
                "variants": variant_paths,
                "logo_id": int(service["service_id"]) & 0x1FF,
                "logo_version": version,
                "download_data_id": int(service["service_id"]),
                "width": 64,
                "height": 36,
                "source_width": width,
                "source_height": height,
                "bytes": transmitted_bytes,
                "updated_at": now_epoch(),
            }
            carrier["signalling_version"] = (int(carrier.get("signalling_version", 0)) + 1) % 32
            self.store.save()
        self.supervisor.remove(carrier_id)
        if was_active or carrier.get("auto_start"):
            self.supervisor.action(carrier_id, "start")
        return {"result": "ok", "width": 64, "height": 36,
                "bytes": transmitted_bytes, "variants": len(variants)}

    def delete_logo(self, request: dict[str, Any]) -> dict[str, Any]:
        carrier_id = str(request.get("carrier_id") or "")
        service_id = str(request.get("service_id") or "")
        was_active = any(item["id"] == carrier_id and item.get("active")
                         for item in self.supervisor.state()["carriers"])
        paths: set[Path] = set()
        with self.store.lock:
            carrier = next((item for item in self.store.data["carriers"] if item["id"] == carrier_id), None)
            if not carrier:
                raise ApiError("Portadora não encontrada", HTTPStatus.NOT_FOUND)
            service = next((item for item in carrier["services"] if item["id"] == service_id), None)
            if not service:
                raise ApiError("Canal não encontrado", HTTPStatus.NOT_FOUND)
            logo = service.get("logo") if isinstance(service.get("logo"), dict) else {}
            if logo.get("path"):
                paths.add(Path(str(logo["path"])))
            if isinstance(logo.get("variants"), dict):
                paths.update(Path(str(item)) for item in logo["variants"].values())
            service["logo"] = {}
            carrier["signalling_version"] = (int(carrier.get("signalling_version", 0)) + 1) % 32
            self.store.save()
        for path in paths:
            resolved = path.resolve()
            if resolved.is_file() and self.logo_dir.resolve() in resolved.parents:
                resolved.unlink()
        self.supervisor.remove(carrier_id)
        if was_active or carrier.get("auto_start"):
            self.supervisor.action(carrier_id, "start")
        return {"result": "ok"}

    def logo_file(self, carrier_id: str, service_id: str) -> Path:
        carrier = next((item for item in self.store.snapshot()["carriers"] if item["id"] == carrier_id), None)
        if not carrier:
            raise ApiError("Portadora não encontrada", HTTPStatus.NOT_FOUND)
        service = next((item for item in carrier["services"] if item["id"] == service_id), None)
        logo = service.get("logo") if service and isinstance(service.get("logo"), dict) else {}
        if not logo.get("enabled") or not logo.get("path"):
            raise ApiError("Logo não encontrado", HTTPStatus.NOT_FOUND)
        path = Path(str(logo["path"])).resolve()
        if self.logo_dir.resolve() not in path.parents or not path.is_file():
            raise ApiError("Logo não encontrado", HTTPStatus.NOT_FOUND)
        return path

    def carrier(self, carrier_id: str) -> dict[str, Any]:
        carrier = next((item for item in self.store.snapshot()["carriers"]
                        if item["id"] == carrier_id), None)
        if not carrier:
            raise ApiError("Portadora não encontrada", HTTPStatus.NOT_FOUND)
        return carrier

    def save_channel(self, carrier_id: str, request: dict[str, Any],
                     service_id: str = "") -> dict[str, Any]:
        carrier = self.carrier(carrier_id)
        services = copy.deepcopy(carrier["services"])
        index = next((position for position, item in enumerate(services)
                      if item["id"] == service_id), None)
        if service_id and index is None:
            raise ApiError("Canal não encontrado", HTTPStatus.NOT_FOUND)
        candidate = copy.deepcopy(request)
        candidate["id"] = service_id if service_id else str(candidate.get("id") or "")
        if index is None:
            services.append(candidate)
        else:
            # PUT accepts a complete representation, while retaining logo metadata.
            candidate["logo"] = copy.deepcopy(services[index].get("logo", {}))
            services[index] = candidate
        result = self.save_carrier({**carrier, "services": services})
        saved = self.carrier(carrier_id)
        if index is None:
            channel = next((item for item in saved["services"]
                            if item["service_id"] == int(request.get("service_id", 0))), None)
        else:
            channel = next(item for item in saved["services"] if item["id"] == service_id)
        return {"result": result["result"], "carrier_id": carrier_id,
                "channel": public_channel(channel or {})}

    def delete_channel(self, carrier_id: str, service_id: str) -> dict[str, Any]:
        carrier = self.carrier(carrier_id)
        services = [item for item in carrier["services"] if item["id"] != service_id]
        if len(services) == len(carrier["services"]):
            raise ApiError("Canal não encontrado", HTTPStatus.NOT_FOUND)
        if not services:
            raise ApiError("A portadora precisa manter ao menos um canal")
        self.save_carrier({**carrier, "services": services})
        return {"result": "ok", "carrier_id": carrier_id, "channel_id": service_id}

    def delete_carrier(self, carrier_id: str) -> dict[str, Any]:
        self.supervisor.remove(carrier_id)
        with self.store.lock:
            before = len(self.store.data["carriers"])
            self.store.data["carriers"] = [item for item in self.store.data["carriers"] if item["id"] != carrier_id]
            if len(self.store.data["carriers"]) == before:
                raise ApiError("Portadora não encontrada", HTTPStatus.NOT_FOUND)
            self.store.save()
        return {"result": "ok"}

    def catalog(self, source_id: str, force: bool = False) -> dict[str, Any]:
        guide = self.guides.get(self.source(source_id), force)
        current_time = now_epoch()
        channels = []
        for channel in sorted(guide["channels"].values(), key=lambda item: item["name"].casefold()):
            schedule = guide["programmes"].get(channel["id"], [])
            current = next((item for item in schedule
                            if item["start"] <= current_time < item["stop"]), None)
            upcoming = [item for item in schedule if item["stop"] > current_time][:24]
            channels.append({**copy.deepcopy(channel), "programme_count": len(schedule),
                             "current": copy.deepcopy(current),
                             "schedule": copy.deepcopy(upcoming)})
        sync_seconds = getattr(self.guides, "sync_seconds", SOURCE_SYNC_SECONDS)
        if not isinstance(sync_seconds, (int, float)):
            sync_seconds = SOURCE_SYNC_SECONDS
        return {
            "source_id": source_id, "fetched_at": int(guide["fetched_at"]),
            "next_refresh_at": (self.guides.next_refresh_at(guide["fetched_at"])
                                if isinstance(self.guides, GuideCache)
                                else int(guide["fetched_at"] + sync_seconds)),
            "cache_seconds": sync_seconds,
            "bytes": guide["bytes"],
            "channel_count": len(channels),
            "programme_count": sum(map(len, guide["programmes"].values())),
            "window": {"past_days": 1, "future_days": 8},
            "normalization": copy.deepcopy(guide.get("normalization")),
            "channels": channels,
        }

    def guide(self, carrier_id: str) -> dict[str, Any]:
        carrier = next((item for item in self.store.snapshot()["carriers"] if item["id"] == carrier_id), None)
        if not carrier:
            raise ApiError("Portadora não encontrada", HTTPStatus.NOT_FOUND)
        current_time = now_epoch()
        local_now = datetime.now(BRAZIL_TZ)
        day_start = int(local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).timestamp())
        day_end = day_start + 86400
        services = []
        for service in carrier["services"]:
            source_id = service.get("source_id") or carrier["source_id"]
            guide = self.guides.get(self.source(source_id))
            schedule = [copy.deepcopy(item) for item in guide["programmes"].get(service["epg_channel_id"], [])
                        if item["stop"] > day_start and item["start"] < day_end]
            current = next((item for item in schedule if item["start"] <= current_time < item["stop"]), None)
            future = [item for item in schedule if item["start"] >= current_time]
            next_item = future[0] if future else None
            if current:
                duration = max(1, current["stop"] - current["start"])
                current = copy.deepcopy(current)
                current["progress"] = round(max(0, min(100, (current_time - current["start"]) * 100 / duration)), 1)
            services.append({**service, "source_id": source_id, "current": current, "next": next_item, "schedule": schedule})
        return {
            "carrier_id": carrier_id, "carrier_name": carrier["name"],
            "source_id": carrier["source_id"], "generated_at": current_time,
            "fetched_at": max((int(self.guides.get(self.source(item.get("source_id") or carrier["source_id"]))["fetched_at"])
                               for item in carrier["services"]), default=0), "timezone": "America/Sao_Paulo",
            "services": services,
        }

    def all_guides(self, start: int, end: int) -> dict[str, Any]:
        """Return every configured service for the consolidated EPG grid."""
        if end <= start or end - start > 9 * 86400:
            raise ApiError("Intervalo da grade inválido")
        snapshot = self.store.snapshot()
        source_cache: dict[str, dict[str, Any]] = {}
        services = []
        unavailable_sources = set()
        fetched_at = 0
        for carrier in snapshot["carriers"]:
            for service in carrier["services"]:
                source_id = service.get("source_id") or carrier["source_id"]
                if source_id not in source_cache:
                    # The background synchronizer owns refreshes. Opening a read-only
                    # grid must never serially redownload every provider feed.
                    with self.guides.lock:
                        cached = self.guides.entries.get(source_id)
                    source_cache[source_id] = cached
                guide = source_cache[source_id]
                if not guide:
                    unavailable_sources.add(source_id)
                    schedule = []
                    services.append({**copy.deepcopy(service),
                        "row_id": f'{carrier["id"]}:{service["id"]}',
                        "carrier_id": carrier["id"], "carrier_name": carrier["name"],
                        "transport_stream_id": carrier["transport_stream_id"],
                        "original_network_id": carrier["original_network_id"],
                        "source_id": source_id, "schedule": schedule, "cache_pending": True})
                    continue
                fetched_at = max(fetched_at, int(guide["fetched_at"]))
                schedule = [copy.deepcopy(item) for item in guide["programmes"].get(service["epg_channel_id"], [])
                            if item["stop"] > start and item["start"] < end]
                services.append({
                    **copy.deepcopy(service), "row_id": f'{carrier["id"]}:{service["id"]}',
                    "carrier_id": carrier["id"], "carrier_name": carrier["name"],
                    "transport_stream_id": carrier["transport_stream_id"],
                    "original_network_id": carrier["original_network_id"],
                    "source_id": source_id, "schedule": schedule,
                })
        return {"generated_at": now_epoch(), "fetched_at": fetched_at,
                "timezone": "America/Sao_Paulo", "start": start, "end": end,
                "services": services,
                "unavailable_sources": sorted(unavailable_sources),
                "programme_count": sum(len(item["schedule"]) for item in services)}

    def epg_health(self) -> dict[str, Any]:
        snapshot = self.store.snapshot()
        sources = {item["id"]: item for item in snapshot["sources"]}
        runtime = {item["id"]: item for item in self.supervisor.state()["carriers"]}
        with self.guides.lock:
            guides = dict(self.guides.entries)
            source_errors = copy.deepcopy(self.guides.errors)
        current = now_epoch()
        carriers = []
        all_errors = []
        for carrier in snapshot["carriers"]:
            carrier_runtime = runtime.get(carrier["id"], {})
            services = []
            for service in carrier["services"]:
                source_id = service.get("source_id") or carrier["source_id"]
                source = sources.get(source_id, {})
                parsed_url = urllib.parse.urlsplit(str(source.get("url") or ""))
                safe_host = parsed_url.hostname or ""
                if parsed_url.port:
                    safe_host += f":{parsed_url.port}"
                safe_url = urllib.parse.urlunsplit((parsed_url.scheme, safe_host,
                                                    parsed_url.path,
                                                    "…" if parsed_url.query else "", ""))
                guide = guides.get(source_id)
                status = "ok"
                code = "ok"
                message = "Programação atual e futura disponível"
                programme_count = 0
                current_event = None
                next_event = None
                if not carrier_runtime.get("active"):
                    status, code = "error", "emitter_stopped"
                    message = carrier_runtime.get("last_error") or "Emissor multicast não está ativo"
                elif source_id in source_errors and not guide:
                    status, code = "error", "source_failure"
                    message = f"Falha na fonte XMLTV: {source_errors[source_id]['message']}"
                elif not guide:
                    status, code = "warning", "awaiting_sync"
                    message = "Fonte aguardando a primeira sincronização"
                elif service["epg_channel_id"] not in guide["channels"]:
                    status, code = "error", "channel_missing"
                    message = f"ID XMLTV {service['epg_channel_id']} não encontrado na fonte"
                else:
                    programmes = guide["programmes"].get(service["epg_channel_id"], [])
                    programme_count = len(programmes)
                    current_event = next((item for item in programmes
                                          if item["start"] <= current < item["stop"]), None)
                    next_event = next((item for item in programmes if item["start"] >= current), None)
                    if not programmes:
                        status, code, message = "error", "no_programmes", "Canal existe, mas o XML não possui programação"
                    elif not current_event:
                        status, code = "error", "no_current_programme"
                        message = (f"Sem programação no horário atual; próximo evento em "
                                   f"{datetime.fromtimestamp(next_event['start'], BRAZIL_TZ).strftime('%d/%m %H:%M')}"
                                   if next_event else "Programação expirada; nenhum evento atual ou futuro")
                    elif not next_event:
                        status, code, message = "warning", "no_future_programme", "Programa atual disponível, mas não há próximo evento"
                    elif source_id in source_errors:
                        status, code = "warning", "stale_source"
                        message = f"Usando última cópia válida; atualização falhou: {source_errors[source_id]['message']}"
                item = {
                    "carrier_id": carrier["id"], "carrier_name": carrier["name"],
                    "service_id": service["id"], "service_name": service["name"],
                    "epg_channel_id": service["epg_channel_id"], "source_id": source_id,
                    "source_name": source.get("name") or source_id,
                    "source_url": safe_url,
                    "status": status, "code": code, "message": message,
                    "programme_count": programme_count,
                    "current_title": current_event.get("title", "") if current_event else "",
                    "next_title": next_event.get("title", "") if next_event else "",
                }
                services.append(item)
                if status != "ok":
                    all_errors.append(item)
            failing = sum(item["status"] == "error" for item in services)
            warnings = sum(item["status"] == "warning" for item in services)
            carrier_status = ("error" if services and failing == len(services) else
                              "warning" if failing or warnings else "ok")
            carriers.append({"id": carrier["id"], "name": carrier["name"],
                             "status": carrier_status, "failing": failing,
                             "warnings": warnings, "total": len(services), "services": services})
        return {"generated_at": current, "carriers": carriers, "errors": all_errors,
                "summary": {"ok": sum(c["status"] == "ok" for c in carriers),
                            "warning": sum(c["status"] == "warning" for c in carriers),
                            "error": sum(c["status"] == "error" for c in carriers)}}


def openapi_document() -> dict[str, Any]:
    """Machine-readable contract for integrations; no runtime dependency required."""
    operations = {
        "/api/v1/system": {"get": "Estado, licença e saúde do sistema"},
        "/api/v1/settings": {"get": "Consultar configurações", "put": "Atualizar configurações"},
        "/api/v1/sources": {"get": "Listar fontes XMLTV", "post": "Cadastrar fonte XMLTV"},
        "/api/v1/sources/{source_id}": {"get": "Consultar fonte", "put": "Editar fonte", "delete": "Excluir fonte"},
        "/api/v1/sources/{source_id}/catalog": {"get": "Consultar canais e programas da fonte"},
        "/api/v1/sources/{source_id}/sync": {"post": "Sincronizar fonte imediatamente"},
        "/api/v1/sources/{source_id}/history": {"get": "Consultar histórico de sincronização"},
        "/api/v1/carriers": {"get": "Listar portadoras", "post": "Cadastrar portadora"},
        "/api/v1/carriers/{carrier_id}": {"get": "Consultar portadora", "put": "Editar portadora", "delete": "Excluir portadora"},
        "/api/v1/carriers/{carrier_id}/channels": {"get": "Listar canais", "post": "Cadastrar canal"},
        "/api/v1/carriers/{carrier_id}/channels/{channel_id}": {"get": "Consultar canal", "put": "Editar canal", "delete": "Excluir canal"},
        "/api/v1/carriers/{carrier_id}/channels/{channel_id}/logo": {"get": "Baixar logo", "put": "Enviar logo PNG em base64", "delete": "Excluir logo"},
        "/api/v1/carriers/{carrier_id}/guide": {"get": "Consultar grade da portadora"},
        "/api/v1/carriers/{carrier_id}/logs": {"get": "Consultar log do emissor"},
        "/api/v1/carriers/{carrier_id}/actions/{action}": {"post": "Iniciar, parar, reiniciar ou auditar"},
        "/api/v1/carriers/actions/restart-all": {"post": "Reiniciar todos os emissores"},
        "/api/v1/channels": {"get": "Listar todos os canais"},
        "/api/v1/guides": {"get": "Consultar grade consolidada"},
        "/api/v1/errors": {"get": "Consultar erros de EPG"},
        "/api/v1/publications": {"get": "Listar publicações", "post": "Cadastrar publicação"},
        "/api/v1/publications/{publication_id}": {"put": "Editar publicação", "delete": "Excluir publicação"},
        "/api/v1/publications/{publication_id}/versions": {"post": "Enviar uma versão XMLTV"},
        "/api/v1/publications/{publication_id}/versions/{version_id}": {"delete": "Excluir versão XMLTV"},
        "/api/v1/users": {"get": "Listar usuários", "post": "Cadastrar usuário"},
        "/api/v1/users/{user_id}": {"put": "Editar usuário", "delete": "Excluir usuário"},
        "/api/v1/license": {"get": "Consultar licença", "put": "Instalar chave"},
        "/api/v1/update": {"get": "Consultar atualização"},
        "/api/v1/update/apply": {"post": "Aplicar atualização nativa"},
        "/api/v1/backup": {"get": "Baixar backup completo"},
        "/api/v1/restore": {"post": "Restaurar backup completo"},
    }
    paths: dict[str, Any] = {}
    for path, methods in operations.items():
        paths[path] = {}
        for method, summary in methods.items():
            operation: dict[str, Any] = {
                "summary": summary, "security": [{"basicAuth": []}],
                "responses": {"200": {"description": "Operação concluída"},
                              "400": {"description": "Requisição inválida"},
                              "401": {"description": "Autenticação necessária"},
                              "402": {"description": "Licença inválida"},
                              "404": {"description": "Recurso não encontrado"}},
            }
            parameters = re.findall(r"\{([^}]+)\}", path)
            if parameters:
                operation["parameters"] = [{"name": name, "in": "path", "required": True,
                                             "schema": {"type": "string"}}
                                            for name in parameters]
            if method in {"post", "put"}:
                media_type = ("application/xml" if path.endswith("/versions") else
                              "application/gzip" if path == "/api/v1/restore" else
                              "application/json")
                schema = ({"type": "string", "format": "binary"}
                          if media_type != "application/json" else {"type": "object"})
                operation["requestBody"] = {"required": True,
                                            "content": {media_type: {"schema": schema}}}
            paths[path][method] = operation
    return {
        "openapi": "3.0.3",
        "info": {"title": f"{PRODUCT_NAME} API", "version": PRODUCT_VERSION,
                 "description": "API REST para cadastro, gestão e consulta do OMNIEPG."},
        "servers": [{"url": "/"}], "paths": paths,
        "components": {"securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"}}},
    }


API_DOC_HTML = r'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>OMNIEPG API</title>
<style>body{font:15px system-ui;margin:0;background:#f2f6fa;color:#14263a}main{max-width:1000px;margin:40px auto;background:#fff;padding:32px;border-radius:14px}code,pre{background:#071b33;color:#dff4ff;padding:3px 6px;border-radius:5px}pre{padding:16px;overflow:auto}a{color:#087ec1}li{margin:8px 0}</style></head><body><main>
<h1>OMNIEPG API REST v1</h1><p>Integração autenticada para cadastro, gestão e consulta. O contrato completo está em
<a href="/api/v1/openapi.json">OpenAPI 3.0 JSON</a>.</p>
<h2>Autenticação</h2><p>Use HTTP Basic com um usuário ativo do painel. Operações de usuários, licença e configurações exigem administrador.</p>
<pre>curl -u epgadmin:SENHA http://servidor:9100/api/v1/system</pre>
<h2>Recursos</h2><ul><li><code>/api/v1/sources</code> — fontes XMLTV e sincronização</li>
<li><code>/api/v1/carriers</code> — portadoras, canais, ações, logs e grade</li>
<li><code>/api/v1/guides</code> e <code>/api/v1/errors</code> — programação e diagnóstico</li>
<li><code>/api/v1/publications</code> — URLs XMLTV permanentes e versões</li>
<li><code>/api/v1/users</code>, <code>/api/v1/license</code> e <code>/api/v1/settings</code> — administração</li></ul>
<p>Consulte também <code>API_REST.md</code> no pacote do produto para exemplos e modelos completos.</p></main></body></html>'''


APP: Application | None = None


class Handler(BaseHTTPRequestHandler):
    server_version = "OMNIEPG/1.24"
    current_user: dict[str, Any] | None = None

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.client_address[0]} - {fmt % args}", flush=True)

    def _authenticated_user(self) -> dict[str, Any] | None:
        assert APP is not None
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return None
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
            user, password = decoded.split(":", 1)
        except Exception:
            return None
        return APP.authenticate(user, password)

    def _require_auth(self) -> bool:
        self.current_user = self._authenticated_user()
        if self.current_user:
            return True
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="OMNIEPG", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _require_admin(self) -> None:
        if not self.current_user or self.current_user.get("role") != "admin":
            raise ApiError("Apenas administradores podem gerenciar usuários", HTTPStatus.FORBIDDEN)

    def _require_license(self) -> None:
        assert APP is not None
        APP.require_license()

    def _json(self, value: Any, status: int = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError("Content-Length inválido")
        if length <= 0 or length > MAX_BODY:
            raise ApiError("Corpo da requisição vazio ou muito grande")
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            raise ApiError("JSON inválido")
        if not isinstance(value, dict):
            raise ApiError("O corpo deve ser um objeto JSON")
        return value

    def _raw_body(self, limit: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError("Content-Length inválido")
        if length <= 0 or length > limit:
            raise ApiError(f"O arquivo deve possuir no máximo {limit // (1024 * 1024)} MiB")
        payload = self.rfile.read(length)
        if len(payload) != length:
            raise ApiError("O upload XMLTV foi interrompido")
        return payload

    def _query(self) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    @staticmethod
    def _public_source(source: dict[str, Any]) -> dict[str, Any]:
        return {key: copy.deepcopy(value) for key, value in source.items()
                if key != "parse_token"}

    def _carrier_log(self, carrier_id: str) -> dict[str, Any]:
        assert APP is not None
        APP.carrier(carrier_id)
        log_path = APP.supervisor.log_dir / f"{carrier_id}.log"
        content = log_path.read_text(encoding="utf-8", errors="replace")[-30000:] if log_path.exists() else ""
        return {"carrier_id": carrier_id, "log": content}

    def _v1_get(self, path: str) -> bool:
        assert APP is not None
        if path == "/api/v1":
            self._json({"product": PRODUCT_NAME, "version": PRODUCT_VERSION,
                        "documentation": "/api/v1/docs", "openapi": "/api/v1/openapi.json"})
            return True
        if path == "/api/v1/docs":
            payload = API_DOC_HTML.encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers(); self.wfile.write(payload)
            return True
        if path == "/api/v1/openapi.json":
            self._json(openapi_document()); return True
        if path == "/api/v1/license":
            self._require_admin(); self._json(APP.license.check(APP.channel_count(), force=True)); return True
        if path == "/api/v1/users":
            self._require_admin(); self._json({"users": APP.users()}); return True
        if path == "/api/v1/update":
            self._require_admin(); self._json(update_information(APP.store.path.parent)); return True
        if path == "/api/v1/backup":
            self._require_admin(); payload = APP.configuration_backup()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Disposition", 'attachment; filename="omniepg-backup.tar.gz"')
            self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            return True
        self._require_license()
        snapshot = APP.store.snapshot()
        if path == "/api/v1/system":
            state = APP.supervisor.state()
            state.update({"epg_health": APP.epg_health(),
                          "general_settings": snapshot["general_settings"],
                          "license": APP.license.check(APP.channel_count())})
            self._json(state); return True
        if path == "/api/v1/settings":
            self._require_admin(); self._json({"settings": snapshot["general_settings"]}); return True
        if path == "/api/v1/sources":
            self._json({"sources": [{**self._public_source(item), "sync_status": APP.guides.status(item)}
                                    for item in snapshot["sources"]]}); return True
        match = re.fullmatch(r"/api/v1/sources/([^/]+)(?:/(catalog))?", path)
        if match:
            source = APP.source(match.group(1))
            if match.group(2):
                self._json(APP.catalog(source["id"], self._query().get("force", ["0"])[0] == "1"))
            else:
                self._json({"source": {**self._public_source(source),
                                       "sync_status": APP.guides.status(source)}})
            return True
        match = re.fullmatch(r"/api/v1/sources/([^/]+)/history", path)
        if match:
            APP.source(match.group(1)); self._json({"events": APP.guides.sync_history(match.group(1))}); return True
        if path == "/api/v1/carriers":
            runtime = {item["id"]: item for item in APP.supervisor.state()["carriers"]}
            self._json({"carriers": [{**public_carrier(item), "runtime": runtime.get(item["id"], {})}
                                     for item in snapshot["carriers"]]}); return True
        if path == "/api/v1/channels":
            channels = [{**public_channel(service), "carrier_id": carrier["id"],
                         "carrier_name": carrier["name"]}
                        for carrier in snapshot["carriers"] for service in carrier["services"]]
            self._json({"channels": channels, "count": len(channels)}); return True
        match = re.fullmatch(r"/api/v1/carriers/([^/]+)/channels/([^/]+)/logo", path)
        if match:
            payload = APP.logo_file(*match.groups()).read_bytes()
            self.send_response(HTTPStatus.OK); self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            return True
        match = re.fullmatch(r"/api/v1/carriers/([^/]+)(?:/(channels|guide|logs))?(?:/([^/]+))?", path)
        if match:
            carrier = APP.carrier(match.group(1)); resource, child_id = match.group(2), match.group(3)
            if not resource:
                self._json({"carrier": public_carrier(carrier)}); return True
            if resource == "guide":
                self._json(APP.guide(carrier["id"])); return True
            if resource == "logs":
                self._json(self._carrier_log(carrier["id"])); return True
            if child_id:
                channel = next((item for item in carrier["services"] if item["id"] == child_id), None)
                if not channel: raise ApiError("Canal não encontrado", HTTPStatus.NOT_FOUND)
                self._json({"carrier_id": carrier["id"], "channel": public_channel(channel)})
            else:
                self._json({"carrier_id": carrier["id"],
                            "channels": [public_channel(item) for item in carrier["services"]]})
            return True
        if path == "/api/v1/guides":
            query = self._query(); start = int(query.get("start", [str(now_epoch() - 21600)])[0]); end = int(query.get("end", [str(start + 86400)])[0])
            self._json(APP.all_guides(start, end)); return True
        if path == "/api/v1/errors":
            self._json(APP.epg_health()); return True
        if path == "/api/v1/publications":
            self._json({"publications": APP.publications()}); return True
        raise ApiError("Endpoint não encontrado", HTTPStatus.NOT_FOUND)

    def _v1_write(self, method: str, path: str, request: dict[str, Any]) -> tuple[dict[str, Any], int]:
        assert APP is not None
        created = HTTPStatus.CREATED
        if path == "/api/v1/license" and method == "PUT":
            self._require_admin(); return APP.install_license_key(request), HTTPStatus.OK
        if path == "/api/v1/users" and method == "POST":
            self._require_admin(); return APP.save_user(request), created
        user_match = re.fullmatch(r"/api/v1/users/([^/]+)", path)
        if user_match:
            self._require_admin()
            if method == "PUT": return APP.save_user({**request, "id": user_match.group(1)}), HTTPStatus.OK
            if method == "DELETE": return APP.delete_user(user_match.group(1), str(self.current_user["id"])), HTTPStatus.OK
        if path == "/api/v1/update/apply" and method == "POST":
            self._require_admin(); return request_native_update(APP.store.path.parent, str(request.get("tag") or "")), HTTPStatus.OK
        self._require_license()
        if method == "POST" and path == "/api/v1/sources": return APP.save_source(request), created
        match = re.fullmatch(r"/api/v1/sources/([^/]+)(?:/(sync))?", path)
        if match:
            source_id, action = match.groups()
            if method == "POST" and action == "sync": return APP.catalog(source_id, force=True), HTTPStatus.OK
            if method == "PUT" and not action: return APP.save_source({**request, "id": source_id}), HTTPStatus.OK
            if method == "DELETE" and not action:
                replacement = self._query().get("replacement_id", [""])[0]
                return APP.delete_source(source_id, replacement), HTTPStatus.OK
        if method == "POST" and path == "/api/v1/carriers": return APP.save_carrier(request), created
        if method == "POST" and path == "/api/v1/carriers/actions/restart-all":
            self._require_admin(); return APP.supervisor.restart_all(), HTTPStatus.OK
        match = re.fullmatch(r"/api/v1/carriers/([^/]+)(?:/(channels|actions))?(?:/([^/]+))?", path)
        if match:
            carrier_id, resource, child = match.groups()
            if method == "PUT" and not resource: return APP.save_carrier({**request, "id": carrier_id}), HTTPStatus.OK
            if method == "DELETE" and not resource: return APP.delete_carrier(carrier_id), HTTPStatus.OK
            if resource == "channels":
                if method == "POST" and not child: return APP.save_channel(carrier_id, request), created
                if method == "PUT" and child: return APP.save_channel(carrier_id, request, child), HTTPStatus.OK
                if method == "DELETE" and child: return APP.delete_channel(carrier_id, child), HTTPStatus.OK
            if method == "POST" and resource == "actions" and child:
                if child == "audit": return APP.supervisor.audit(carrier_id, int(request.get("seconds", 8))), HTTPStatus.OK
                if child not in {"start", "stop", "restart"}: raise ApiError("Ação inválida")
                APP.supervisor.action(carrier_id, child); return {"result": "ok", "action": child}, HTTPStatus.OK
        match = re.fullmatch(r"/api/v1/carriers/([^/]+)/channels/([^/]+)/logo", path)
        if match:
            carrier_id, channel_id = match.groups()
            if method == "PUT": return APP.save_logo({"carrier_id": carrier_id, "service_id": channel_id,
                                                       "data": request.get("data")}), HTTPStatus.OK
            if method == "DELETE": return APP.delete_logo({"carrier_id": carrier_id,
                                                            "service_id": channel_id}), HTTPStatus.OK
        if method == "POST" and path == "/api/v1/publications": return APP.save_publication(request), created
        match = re.fullmatch(r"/api/v1/publications/([^/]+)(?:/versions/([^/]+))?", path)
        if match:
            publication_id, version_id = match.groups()
            if method == "PUT" and not version_id: return APP.save_publication({**request, "id": publication_id}), HTTPStatus.OK
            if method == "DELETE" and version_id: return APP.delete_publication_version(publication_id, version_id), HTTPStatus.OK
            if method == "DELETE" and not version_id: return APP.delete_publication(publication_id), HTTPStatus.OK
        if path == "/api/v1/settings" and method == "PUT":
            self._require_admin(); return APP.save_general_settings(request), HTTPStatus.OK
        raise ApiError("Endpoint ou método não encontrado", HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:
        assert APP is not None
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/health":
                license_status = APP.license.check(APP.channel_count())
                self._json({"status": "ok" if license_status["valid"] else "degraded",
                            "product": PRODUCT_NAME, "version": PRODUCT_VERSION,
                            "license": license_status},
                           HTTPStatus.OK if license_status["valid"] else HTTPStatus.SERVICE_UNAVAILABLE)
                return
            public_match = re.fullmatch(r"/xmltv/([a-f0-9]{32})\.xml", path)
            if public_match:
                payload, version = APP.publication_payload(public_match.group(1))
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/xml; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, max-age=0, must-revalidate")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-XMLTV-Version", str(version["id"]))
                self.send_header("X-XMLTV-Valid-Until", str(version["valid_until"]))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            asset_match = re.fullmatch(r"/assets/(omniepg_(?:icone|logotipo_escuro|logotipo_transparente)\.svg)", path)
            if asset_match:
                payload = (Path(__file__).parent / "assets" / asset_match.group(1)).read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            parsed_match = re.fullmatch(r"/parsed-xml/([a-f0-9]{32})\.xml", path)
            if parsed_match:
                payload, stats = APP.parsed_source_payload(parsed_match.group(1))
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/xml; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, max-age=0, must-revalidate")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-XMLTV-Normalized", "parse-xml")
                self.send_header("X-XMLTV-Channels", str(stats.get("channels", 0)))
                self.send_header("X-XMLTV-Programmes", str(stats.get("programmes", 0)))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            runtime_match = re.fullmatch(r"/runtime-xmltv/([a-f0-9]{64})\.xml", path)
            if runtime_match:
                if self.client_address[0] not in {"127.0.0.1", "::1"}:
                    raise ApiError("Fonte interna disponível apenas localmente", HTTPStatus.FORBIDDEN)
                payload, stats = APP.runtime_source_payload(runtime_match.group(1))
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/xml; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, max-age=0, must-revalidate")
                self.send_header("X-OMNIEPG-Source", str(stats["source_id"]))
                self.send_header("X-OMNIEPG-Fetched-At", str(stats["fetched_at"]))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if not self._require_auth():
                return
            if path.startswith("/api/v1"):
                self._v1_get(path)
            elif path == "/":
                payload = INDEX_HTML.encode()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self'")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif path == "/api/state":
                state = APP.supervisor.state()
                state["epg_health"] = APP.epg_health()
                state["general_settings"] = APP.store.snapshot()["general_settings"]
                state["sources"] = [{key: value for key, value in source.items()
                                     if key not in {"url", "parse_token"}}
                                    for source in APP.store.snapshot()["sources"]]
                self._json(state)
            elif path == "/api/session":
                self._json({"user": self.current_user})
            elif path == "/api/license":
                self._json(APP.license.check(APP.channel_count(), force=True))
            elif path == "/api/users":
                self._require_admin()
                self._json({"users": APP.users()})
            elif path == "/api/update":
                self._require_admin()
                self._json(update_information(APP.store.path.parent))
            elif path == "/api/config/backup":
                self._require_admin()
                payload = APP.configuration_backup()
                filename = datetime.now(timezone.utc).strftime("epg-stream-backup-%Y%m%dT%H%M%SZ.tar.gz")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif path == "/api/sources":
                self._require_license()
                self._json({"sources": [
                    {**{key: value for key, value in source.items() if key != "parse_token"},
                     "sync_status": APP.guides.status(source)}
                    for source in APP.store.snapshot()["sources"]
                ]})
            elif path == "/api/sources/history":
                self._require_license()
                source_id = self._query().get("source_id", [""])[0]
                self._json({"events": APP.guides.sync_history(source_id)})
            elif path == "/api/publications":
                self._require_license()
                self._json({"publications": APP.publications()})
            elif path == "/api/logo":
                self._require_license()
                query = self._query()
                payload = APP.logo_file(
                    query.get("carrier_id", [""])[0], query.get("service_id", [""])[0]
                ).read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif path == "/api/catalog":
                self._require_license()
                query = self._query()
                source_id = query.get("source_id", [""])[0]
                self._json(APP.catalog(source_id, force=query.get("force", ["0"])[0] == "1"))
            elif path == "/api/guide":
                self._require_license()
                carrier_id = self._query().get("carrier_id", [""])[0]
                self._json(APP.guide(carrier_id))
            elif path == "/api/guides":
                self._require_license()
                query = self._query()
                start = int(query.get("start", [str(now_epoch() - 21600)])[0])
                end = int(query.get("end", [str(start + 86400)])[0])
                self._json(APP.all_guides(start, end))
            elif path == "/api/logs":
                self._require_license()
                carrier_id = self._query().get("carrier_id", [""])[0]
                if not re.fullmatch(r"[A-Za-z0-9_-]+", carrier_id):
                    raise ApiError("ID de portadora inválido")
                log_path = APP.supervisor.log_dir / f"{carrier_id}.log"
                text = log_path.read_text(encoding="utf-8", errors="replace")[-30000:] if log_path.exists() else ""
                self._json({"carrier_id": carrier_id, "log": text})
            else:
                raise ApiError("Endpoint não encontrado", HTTPStatus.NOT_FOUND)
        except ApiError as error:
            self._json({"error": str(error)}, error.status)
        except Exception as error:
            self._json({"error": f"Falha interna: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        assert APP is not None
        path = urllib.parse.urlparse(self.path).path
        if not self._require_auth():
            return
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
            self._json({"error": "Origem da requisição não permitida"}, HTTPStatus.FORBIDDEN)
            return
        try:
            if path == "/api/v1/restore":
                self._require_admin()
                result = APP.restore_configuration(self._raw_body(MAX_BACKUP))
                self._json(result)
                restart = threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGTERM))
                restart.daemon = True; restart.start()
                return
            version_match = re.fullmatch(r"/api/v1/publications/([^/]+)/versions", path)
            if version_match:
                self._require_license()
                filename = self._query().get("filename", ["guide.xml"])[0]
                self._json(APP.upload_publication(version_match.group(1), filename,
                                                  self._raw_body(MAX_XMLTV)), HTTPStatus.CREATED)
                return
            if path == "/api/publications/upload":
                self._require_license()
                query = self._query()
                publication_id = query.get("id", [""])[0]
                filename = query.get("filename", ["guide.xml"])[0]
                result = APP.upload_publication(publication_id, filename, self._raw_body(MAX_XMLTV))
                self._json(result)
                return
            if path == "/api/config/restore":
                self._require_admin()
                result = APP.restore_configuration(self._raw_body(MAX_BACKUP))
                self._json(result)
                restart = threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGTERM))
                restart.daemon = True
                restart.start()
                return
            request = self._body()
            if path.startswith("/api/v1"):
                result, status = self._v1_write("POST", path, request)
                self._json(result, status)
                return
            if path == "/api/users":
                self._require_admin()
                result = APP.save_user(request)
            elif path == "/api/settings":
                self._require_admin()
                self._require_license()
                result = APP.save_general_settings(request)
            elif path == "/api/update/apply":
                self._require_admin()
                result = request_native_update(APP.store.path.parent, str(request.get("tag") or ""))
            elif path == "/api/license/key":
                self._require_admin()
                result = APP.install_license_key(request)
            elif path == "/api/users/delete":
                self._require_admin()
                result = APP.delete_user(str(request.get("id") or ""), str(self.current_user["id"]))
            elif path == "/api/sources":
                self._require_license()
                result = APP.save_source(request)
            elif path == "/api/sources/test":
                self._require_license()
                source = validate_source(request)
                guide = APP.guides.get(source, force=True)
                result = {"result": "ok", "channels": len(guide["channels"]), "programmes": sum(map(len, guide["programmes"].values())), "bytes": guide["bytes"]}
                if guide.get("normalization"):
                    result["normalization"] = guide["normalization"]
            elif path == "/api/sources/delete":
                self._require_license()
                result = APP.delete_source(str(request.get("id") or ""),
                                           str(request.get("replacement_id") or ""))
            elif path == "/api/publications":
                self._require_license()
                result = APP.save_publication(request)
            elif path == "/api/publications/version/delete":
                self._require_license()
                result = APP.delete_publication_version(
                    str(request.get("publication_id") or ""), str(request.get("version_id") or "")
                )
            elif path == "/api/publications/delete":
                self._require_license()
                result = APP.delete_publication(str(request.get("id") or ""))
            elif path == "/api/carriers":
                self._require_license()
                result = APP.save_carrier(request)
            elif path == "/api/carriers/logo":
                self._require_license()
                result = APP.save_logo(request)
            elif path == "/api/carriers/logo/delete":
                self._require_license()
                result = APP.delete_logo(request)
            elif path == "/api/carriers/delete":
                self._require_license()
                result = APP.delete_carrier(str(request.get("id") or ""))
            elif path == "/api/carriers/restart-all":
                self._require_admin()
                self._require_license()
                result = APP.supervisor.restart_all()
            elif path == "/api/carriers/audit":
                self._require_license()
                result = APP.supervisor.audit(
                    str(request.get("id") or ""), int(request.get("seconds", 8)))
            elif path.startswith("/api/carriers/"):
                self._require_license()
                action = path.rsplit("/", 1)[-1]
                APP.supervisor.action(str(request.get("id") or ""), action)
                result = {"result": "ok"}
            else:
                raise ApiError("Endpoint não encontrado", HTTPStatus.NOT_FOUND)
            self._json(result)
        except ApiError as error:
            self._json({"error": str(error)}, error.status)
        except Exception as error:
            self._json({"error": f"Falha interna: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _mutation(self, method: str) -> None:
        path = urllib.parse.urlparse(self.path).path
        if not self._require_auth():
            return
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
            self._json({"error": "Origem da requisição não permitida"}, HTTPStatus.FORBIDDEN)
            return
        try:
            request = self._body() if method == "PUT" else {}
            result, status = self._v1_write(method, path, request)
            self._json(result, status)
        except ApiError as error:
            self._json({"error": str(error)}, error.status)
        except Exception as error:
            self._json({"error": f"Falha interna: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PUT(self) -> None:
        self._mutation("PUT")

    def do_DELETE(self) -> None:
        self._mutation("DELETE")


INDEX_HTML = r'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OMNIEPG</title><link rel="icon" type="image/svg+xml" href="/assets/omniepg_icone.svg"><style>
:root{--navy:#071b33;--blue:#087ec1;--cyan:#1bb6e8;--bg:#f2f6fa;--card:#fff;--text:#14263a;--muted:#6d7c8d;--line:#dce6ef;--green:#19a974;--red:#df4c55;--amber:#d99a23}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px Inter,Segoe UI,Arial,sans-serif}.top{background:linear-gradient(120deg,var(--navy),#0b5689);color:#fff;padding:22px 30px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 8px 28px #071b3330}.brand{display:flex;gap:14px;align-items:center}.logo{width:46px;height:46px;border:2px solid #51c7ed;border-radius:14px;display:grid;place-items:center;font-size:23px;font-weight:800}.brand h1{margin:0;font-size:22px}.brand small{color:#bde8fa}.live{display:flex;gap:8px;align-items:center}.dot{width:9px;height:9px;background:#31dc9a;border-radius:50%;box-shadow:0 0 0 5px #31dc9a22}.wrap{max-width:1500px;margin:0 auto;padding:24px}.toolbar{display:flex;gap:10px;justify-content:space-between;align-items:center;margin-bottom:18px}.toolbar h2{margin:0;font-size:20px}.actions{display:flex;gap:8px;flex-wrap:wrap}button{border:0;border-radius:9px;padding:10px 14px;font-weight:700;cursor:pointer;background:#e7eef5;color:var(--text)}button.primary{background:linear-gradient(120deg,var(--blue),var(--cyan));color:#fff}button.danger{color:var(--red)}button:disabled{opacity:.5;cursor:not-allowed}.summary{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:18px}.metric,.card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:0 5px 18px #0b254012}.metric{padding:17px}.metric b{font-size:24px;display:block;margin-top:6px}.metric span{color:var(--muted);font-size:12px}.metric.license-valid{border-color:#8ce2bd}.metric.license-invalid{border-color:#f1a4aa}.muted{color:var(--muted)}.badge{font-size:11px;font-weight:800;text-transform:uppercase;border-radius:999px;padding:5px 8px;background:#eef2f6;white-space:nowrap}.badge.running{background:#dcf8ec;color:#087d56}.badge.error{background:#ffe4e5;color:#b72a34}.table-wrap{background:#fff;border:1px solid var(--line);border-radius:14px;box-shadow:0 5px 18px #0b254012;overflow:auto}.carrier-table{width:100%;min-width:1050px;border-collapse:collapse}.carrier-table th{padding:12px 14px;background:#edf4fa;color:#526477;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em}.carrier-table td{padding:14px;border-top:1px solid var(--line);vertical-align:middle}.carrier-table tbody:first-child tr:first-child td{border-top:0}.carrier-table tr.main-row:hover td{background:#f8fbfd}.carrier-name{font-size:15px;font-weight:800}.carrier-sub{margin-top:4px;font-size:12px;color:var(--muted)}.actions-col{width:190px;position:sticky;right:0;background:#fff;box-shadow:-8px 0 14px -14px #071b33;z-index:1}.carrier-table th.actions-col{background:#edf4fa}.action-stack{display:grid;grid-template-columns:1fr 1fr;gap:6px}.action-stack button{padding:8px 9px;font-size:12px}.action-stack .wide{grid-column:1/-1}.program-row{display:none}.program-row.open{display:table-row}.program-row>td{padding:0;background:#f5f9fc}.program-panel{padding:18px 22px}.program-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.program-list{display:grid;gap:8px}.program-line{display:grid;grid-template-columns:1.1fr 80px 120px 2fr 2fr auto;gap:12px;align-items:center;padding:11px 12px;background:#fff;border:1px solid var(--line);border-radius:10px}.program-line button{padding:8px 10px}.program-now{font-weight:750}.progress{height:5px;background:#e6edf3;border-radius:9px;margin-top:6px;overflow:hidden}.progress i{display:block;height:100%;background:linear-gradient(90deg,var(--blue),var(--cyan))}.empty{padding:50px;text-align:center;color:var(--muted)}.modal-back{position:fixed;inset:0;background:#071b3399;display:grid;place-items:center;padding:20px;z-index:5}.modal{background:#fff;border-radius:16px;width:min(920px,100%);max-height:92vh;overflow:auto;padding:22px;box-shadow:0 24px 70px #0005}.modal h2{margin:0 0 18px}.form-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}label{display:flex;flex-direction:column;gap:6px;font-weight:700;font-size:12px}label.wide{grid-column:1/-1}input,select{width:100%;border:1px solid #cbd8e4;border-radius:8px;padding:10px;background:#fff;color:var(--text)}.service-edit{display:grid;grid-template-columns:1.2fr 1.5fr .6fr auto;gap:8px;margin:8px 0;align-items:end;padding:10px;background:#f5f8fb;border-radius:10px}.modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}.guide-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.guide-list{margin-top:14px;display:flex;flex-direction:column;gap:9px}.guide-item{display:grid;grid-template-columns:110px 1fr;gap:12px;padding:12px;border:1px solid var(--line);border-radius:10px}.guide-item.current{border-color:#23aee1;background:#edfaff}.toast{position:fixed;right:20px;bottom:20px;background:var(--navy);color:#fff;padding:13px 17px;border-radius:10px;z-index:9;box-shadow:0 10px 30px #0004}.error-text{color:var(--red)}@media(max-width:760px){.wrap{padding:14px}.summary{grid-template-columns:1fr 1fr}.form-grid{grid-template-columns:1fr}.service-edit{grid-template-columns:1fr}.top{padding:16px}.program-line{grid-template-columns:1fr 70px}.program-line .program-detail{grid-column:1/-1}.actions-col{position:static}.carrier-table{min-width:900px}}
</style></head><body><header class="top"><div class="brand"><div class="logo">E</div><div><h1>EPG Stream</h1><small>Programação ISDB-TB em multicast</small></div></div><div class="live"><i class="dot"></i><span id="clock">Conectando</span></div></header><main class="wrap"><div id="licenseAlert" class="license-alert" hidden>Licença inválida, entre em contato com o suporte</div><div id="epgHealthAlert" class="epg-health-alert" hidden></div><div class="toolbar"><div><h2>Visão geral</h2><div class="muted">Escolha visualizar as portadoras ou gerenciar todos os canais diretamente.</div></div><div class="actions"><button id="epgErrorsButton" onclick="openEpgErrors()">Erros do EPG</button><button onclick="openAbout()">Sobre</button><button id="usersButton" style="display:none" onclick="openUsers()">Usuários</button><button id="publicationsButton" onclick="openPublications()">Publicações XMLTV</button><button id="sourcesButton" onclick="openSources()">Fontes XMLTV</button><button id="timelineButton" disabled onclick="openTimeline()">Grade de programação</button><button id="restartAllButton" style="display:none" onclick="restartAllCarriers()">Reiniciar todos os fluxos</button><button id="newCarrierButton" class="primary" onclick="openCarrier()">+ Nova portadora</button></div></div><section class="summary"><div class="metric"><span>PORTADORAS</span><b id="mCarriers">0</b></div><div class="metric"><span>EMISSORAS ATIVAS</span><b id="mActive">0</b></div><div class="metric"><span>CANAIS / SERVIÇOS</span><b id="mServices">0</b></div><div class="metric"><span>REINÍCIOS</span><b id="mRestarts">0</b></div><div id="licenseMetric" class="metric license-invalid"><span>LICENÇA</span><b id="mLicense">Verificando</b><small id="licenseReason" class="muted"></small></div></section><section id="carrierTable"></section></main><div id="overlay"></div><div id="toast"></div>
<script>
document.head.insertAdjacentHTML('beforeend','<style>.license-alert{margin-bottom:18px;padding:14px 18px;border:1px solid #ef9da4;border-radius:11px;background:#fff0f1;color:#b4232d;font-weight:800;font-size:15px}.license-alert[hidden]{display:none}</style>');
document.head.insertAdjacentHTML('beforeend','<style>.service-edit{grid-template-columns:1fr 1fr 1.25fr .42fr .9fr 1.1fr auto}.logo-tools{display:flex;gap:5px;align-items:center;flex-wrap:wrap}.logo-tools button{padding:8px}.logo-preview{width:64px;height:36px;object-fit:contain;background:#fff;border:1px solid var(--line);border-radius:6px;padding:2px}.logo-status{font-size:11px;color:var(--muted)}@media(max-width:1100px){.service-edit{grid-template-columns:1fr 1fr}}</style>');
document.head.insertAdjacentHTML('beforeend','<style>.modal.timeline-modal{width:min(1420px,100%);padding:0;overflow:hidden}.timeline-head{padding:22px 24px 16px;border-bottom:1px solid var(--line)}.timeline-tools{display:flex;gap:9px;align-items:end;flex-wrap:wrap}.timeline-tools label{min-width:240px}.timeline-scroll{overflow:auto;max-height:70vh;background:#f8fbfd}.timeline-board{min-width:1120px}.timeline-axis,.timeline-row{display:grid;grid-template-columns:180px 1fr}.timeline-axis{position:sticky;top:0;z-index:4;background:#eef5fa;border-bottom:1px solid #cbd8e4}.timeline-corner,.timeline-channel{position:sticky;left:0;z-index:3;background:#fff;border-right:1px solid #cbd8e4}.timeline-corner{background:#eef5fa;padding:13px 14px;font-weight:800}.timeline-hours{position:relative;height:45px;background:repeating-linear-gradient(to right,transparent 0,transparent calc(16.666% - 1px),#cbd8e4 calc(16.666% - 1px),#cbd8e4 16.666%)}.timeline-hour{position:absolute;top:13px;transform:translateX(8px);font-size:12px;font-weight:750;color:#526477}.timeline-row{min-height:82px;border-bottom:1px solid var(--line)}.timeline-channel{display:flex;gap:9px;align-items:center;padding:10px 12px}.timeline-channel img{width:54px;height:36px;object-fit:contain}.timeline-channel strong{display:block}.timeline-track{position:relative;min-height:82px;background:repeating-linear-gradient(to right,#fff 0,#fff calc(16.666% - 1px),#e1e8ee calc(16.666% - 1px),#e1e8ee 16.666%)}.timeline-program{position:absolute;top:7px;height:68px;overflow:hidden;padding:8px 9px;border:1px solid #a9cde2;border-radius:8px;background:linear-gradient(145deg,#e9f7ff,#d8eefb);color:#0a426a;text-align:left;font-weight:600}.timeline-program.current{background:linear-gradient(145deg,#087ec1,#14a9dd);border-color:#087ec1;color:#fff}.timeline-program b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.timeline-program small{display:block;margin-top:5px;opacity:.8}.timeline-empty{padding:29px 14px;color:var(--muted)}.timeline-now{position:absolute;top:0;bottom:0;width:2px;background:#df4c55;z-index:2;pointer-events:none}.timeline-now:before{content:"Agora";position:absolute;top:2px;left:4px;background:#df4c55;color:#fff;padding:2px 5px;border-radius:4px;font-size:9px;font-weight:800}.timeline-now.track:before{display:none}@media(max-width:760px){.modal-back{padding:8px}.modal.timeline-modal{max-height:96vh}.timeline-head{padding:16px}.timeline-tools label{min-width:100%}}</style>');
document.head.insertAdjacentHTML('beforeend','<style>.modal.timeline-modal{width:calc(100vw - 24px);height:calc(100vh - 24px);max-height:none;padding:0;background:#0c0b10;color:#f4ece6}.timeline-head{padding:14px 16px;background:#100e13;border-color:#39313a}.timeline-days,.timeline-tools{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.timeline-days{margin-bottom:10px}.timeline-days button,.timeline-tools button{background:#191821;color:#eaded7;border:1px solid #3b3541}.timeline-days button.active,.timeline-tools button.primary{background:linear-gradient(120deg,#f4b5a3,#e51f32);color:#fff}.timeline-tools input,.timeline-tools select{width:auto;min-width:180px;background:#171621;color:#eee;border-color:#3b3541}.timeline-stats{margin-left:auto;color:#cfc0b7}.timeline-scroll{height:calc(100vh - 190px);max-height:none;background:#0c0b10}.timeline-board{min-width:1250px}.timeline-axis,.timeline-row{grid-template-columns:290px 1fr}.timeline-axis{background:#121018;border-color:#40353a}.timeline-corner,.timeline-channel{background:#111019;border-color:#40353a}.timeline-corner{background:#121018}.timeline-hours{background:repeating-linear-gradient(to right,transparent 0,transparent calc(12.5% - 1px),#302a31 calc(12.5% - 1px),#302a31 12.5%)}.timeline-hour{color:#d5c6be}.timeline-row{min-height:72px;border-color:#302a31}.timeline-track{min-height:72px;background:repeating-linear-gradient(to right,#14121a 0,#14121a calc(12.5% - 1px),#2b252c calc(12.5% - 1px),#2b252c 12.5%)}.timeline-program{top:5px;height:62px;border-color:#4c3b3c;background:#28191d;color:#fff}.timeline-program.current{background:#28191d;border:2px solid #f12532;box-shadow:0 0 10px #f1253244}.timeline-program small{color:#dbc7ba}.timeline-now{background:#f6c348}.timeline-now:before{background:#f6c348;color:#241b12}.timeline-channel small{display:block;margin-top:3px;color:#9d919a}@media(max-width:760px){.modal.timeline-modal{width:100vw;height:100vh;border-radius:0}.timeline-axis,.timeline-row{grid-template-columns:210px 1fr}.timeline-stats{margin-left:0;width:100%}.timeline-scroll{height:calc(100vh - 245px)}}</style>');
document.head.insertAdjacentHTML('beforeend','<style>.modal.publication-modal{width:min(1180px,100%)}.publication-card{border:1px solid var(--line);border-radius:13px;padding:16px;margin-top:13px;background:#f9fbfd}.publication-title{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.publication-url{display:flex;gap:7px;margin:12px 0}.publication-url input{font-family:Consolas,monospace;font-size:12px}.version-table{width:100%;border-collapse:collapse;background:#fff}.version-table th,.version-table td{padding:9px;border-top:1px solid var(--line);text-align:left;font-size:12px}.version-table th{color:var(--muted);font-size:10px;text-transform:uppercase}.upload-label{display:inline-flex;flex-direction:row;align-items:center;background:linear-gradient(120deg,var(--blue),var(--cyan));color:#fff;border-radius:9px;padding:10px 14px;cursor:pointer}.upload-label input{display:none}@media(max-width:760px){.publication-title,.publication-url{flex-direction:column}.version-table{min-width:850px}}</style>');
document.head.insertAdjacentHTML('beforeend','<style>.modal.source-catalog-modal{width:min(1320px,100%)}.sync-state{display:flex;min-height:280px;align-items:center;justify-content:center;flex-direction:column;gap:16px;text-align:center}.sync-spinner{width:48px;height:48px;border:5px solid #dbe9f2;border-top-color:var(--blue);border-radius:50%;animation:syncspin .8s linear infinite}@keyframes syncspin{to{transform:rotate(360deg)}}.parse-report{margin:15px 0;padding:14px 16px;border:1px solid #9bd9be;border-radius:11px;background:#effbf5}.parse-report h3{margin:0 0 8px}.parse-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.parse-grid div{padding:9px;background:#fff;border-radius:8px}.source-channel-table{width:100%;border-collapse:collapse}.source-channel-table th,.source-channel-table td{padding:10px;border-top:1px solid var(--line);text-align:left;vertical-align:top}.source-channel-table th{position:sticky;top:0;background:#edf4fa;z-index:1}.source-schedule{margin-top:8px;display:grid;gap:5px}.source-schedule div{padding:7px 9px;border-radius:7px;background:#f4f8fb}.source-schedule time{display:inline-block;min-width:112px;color:var(--muted)}@media(max-width:760px){.parse-grid{grid-template-columns:1fr 1fr}.source-channel-table{min-width:850px}}</style>');
document.head.insertAdjacentHTML('beforeend','<style>.catalog-search{margin:12px 0}.catalog-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:15px 0}.catalog-metric{padding:13px 14px;border:1px solid var(--line);border-radius:10px;background:#f8fbfd}.catalog-metric b{display:block;margin-top:5px;font-size:17px}.parse-errors{margin:12px 0;border:1px solid #efb5ba;border-radius:10px;background:#fff7f7;padding:12px}.parse-errors summary{cursor:pointer;font-weight:800;color:#a52b34}.parse-error-table{width:100%;border-collapse:collapse;margin-top:9px}.parse-error-table th,.parse-error-table td{padding:8px;border-top:1px solid #f1d6d8;text-align:left;vertical-align:top;font-size:12px}.parse-error-table th{color:var(--muted)}@media(max-width:760px){.catalog-metrics{grid-template-columns:1fr 1fr}.catalog-metric b{font-size:14px}}</style>');
document.head.insertAdjacentHTML('beforeend','<style>.health-dot{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:7px;vertical-align:-1px}.health-dot.ok{background:var(--green);box-shadow:0 0 0 4px #19a97422}.health-dot.warning{background:var(--amber);box-shadow:0 0 0 4px #d99a2322}.health-dot.error{background:var(--red);box-shadow:0 0 0 4px #df4c5522}.main-row.health-warning td{background:#fffaf0}.main-row.health-error td{background:#fff1f2}.program-line.health-error{border-color:#ef9da4;background:#fff5f5}.program-line.health-warning{border-color:#eac474;background:#fffbf0}.health-message{margin-top:5px;font-size:12px;font-weight:700}.health-message.error{color:#b4232d}.health-message.warning{color:#956409}.epg-health-alert{margin-bottom:14px;padding:12px 16px;border:1px solid #eac474;border-radius:10px;background:#fffaeb;color:#805900;font-weight:800}.epg-health-alert[hidden]{display:none}#epgErrorsButton.has-errors{background:#ffe1e3;color:#ae202a}.health-error-table{width:100%;border-collapse:collapse}.health-error-table th,.health-error-table td{padding:10px;border-top:1px solid var(--line);text-align:left;vertical-align:top}</style>');
document.head.insertAdjacentHTML('beforeend','<style>.modal.timeline-modal{background:var(--card);color:var(--text);width:min(1500px,calc(100vw - 32px));height:min(92vh,920px);border-radius:16px}.timeline-head{background:var(--card);border-color:var(--line);padding:20px 22px}.timeline-head .guide-head>div>div{color:var(--muted)!important}.timeline-days button,.timeline-tools button{background:#e7eef5;color:var(--text);border:1px solid var(--line)}.timeline-days button.active,.timeline-tools button.primary{background:linear-gradient(120deg,var(--blue),var(--cyan));color:#fff;border-color:transparent}.timeline-tools input,.timeline-tools select{background:#fff;color:var(--text);border-color:#cbd8e4}.timeline-stats{color:var(--muted)}.timeline-scroll{height:calc(min(92vh,920px) - 172px);background:#f8fbfd}.timeline-axis{background:#edf4fa;border-color:#cbd8e4}.timeline-corner{background:#edf4fa;color:var(--text);border-color:#cbd8e4}.timeline-channel{background:#fff;color:var(--text);border-color:#cbd8e4}.timeline-channel small{color:var(--muted)}.timeline-row{border-color:var(--line)}.timeline-hours{background:repeating-linear-gradient(to right,transparent 0,transparent calc(12.5% - 1px),#cbd8e4 calc(12.5% - 1px),#cbd8e4 12.5%)}.timeline-hour{color:#526477}.timeline-track{background:repeating-linear-gradient(to right,#fff 0,#fff calc(12.5% - 1px),#e1e8ee calc(12.5% - 1px),#e1e8ee 12.5%)}.timeline-program,.timeline-program:nth-of-type(even){background:linear-gradient(145deg,#e9f7ff,#d8eefb);border-color:#a9cde2;color:#0a426a}.timeline-program.current{background:linear-gradient(145deg,var(--blue),var(--cyan));border-color:var(--blue);color:#fff;box-shadow:0 0 0 2px #087ec133}.timeline-program small{color:inherit}.timeline-now{background:var(--red)}.timeline-now:before{background:var(--red);color:#fff}@media(max-width:760px){.modal.timeline-modal{width:100vw;height:100vh;max-height:none;border-radius:0}.timeline-scroll{height:calc(100vh - 245px)}}</style>');
document.head.insertAdjacentHTML('beforeend','<style>:root{--navy:#062b78;--blue:#0646bd;--cyan:#1677e8;--bg:#f5f7fa;--line:#d4dbe5}.app-rail{position:fixed;inset:0 auto 0 0;width:58px;background:#062a78;color:#fff;z-index:10;display:flex;flex-direction:column;align-items:center;padding:10px 0;box-shadow:2px 0 8px #001c5522}.rail-logo{width:38px;height:38px;border-radius:8px;display:grid;place-items:center;background:#0d4bc3;font-weight:900;font-size:18px;margin-bottom:14px}.rail-nav{display:flex;flex-direction:column;gap:7px;width:100%}.rail-nav button{width:100%;height:42px;padding:0;border-radius:0;background:transparent;color:#fff;font-size:18px;opacity:.82;border-left:3px solid transparent}.rail-nav button:hover,.rail-nav button.active{background:#0b43ae;opacity:1;border-left-color:#50b7ff}.rail-bottom{margin-top:auto}.top{margin-left:58px;background:#fff;color:var(--text);padding:13px 18px;border-bottom:1px solid var(--line);box-shadow:none}.top .logo{display:none}.brand{gap:8px}.brand h1{font-size:18px}.brand small{color:var(--muted)}.live{font-size:12px}.wrap{max-width:none;margin-left:58px;padding:16px 22px}.toolbar{align-items:flex-end;margin-bottom:0;border-bottom:1px solid var(--line)}.toolbar>div:first-child{padding-bottom:12px}.toolbar h2{font-size:20px}.actions{gap:0;align-self:stretch}.actions>button{border-radius:7px 7px 0 0;background:#eef2f7;border:1px solid var(--line);border-bottom:0;margin-left:-1px;padding:9px 13px;color:#26384f}.actions>button.primary{background:#0646bd;color:#fff}.summary{display:flex;justify-content:flex-end;gap:0;margin:0 0 16px;border:1px solid var(--line);border-radius:20px;background:#fff;float:right;position:relative;z-index:2}.metric{box-shadow:none;border:0;border-right:1px solid var(--line);border-radius:0;padding:6px 14px;min-width:105px}.metric:last-child{border-right:0}.metric b{display:inline;font-size:13px;margin:0 0 0 5px;color:#0646bd}.metric span{font-size:10px}.metric small{display:none}.table-wrap{clear:both;border-radius:3px;box-shadow:none}.carrier-table{min-width:1050px}.carrier-table th{background:#e6eaf0;color:#24364c;padding:11px 12px;font-size:11px;border-right:1px solid #c7d0dc}.carrier-table td{padding:11px 12px}.carrier-table tr.main-row:nth-of-type(4n+1) td{background:#f7f9fb}.carrier-name{color:#003ca6;font-size:13px}.carrier-sub{font-size:11px}.actions-col{width:230px}.action-stack{display:flex;flex-wrap:wrap}.action-stack button{padding:6px 8px;border:1px solid #b8c6d8;background:#fff;color:#0646bd}.action-stack .wide{grid-column:auto}.panel-filter{clear:both;display:flex;gap:10px;align-items:center;background:#fff;border:1px solid var(--line);padding:11px 14px;margin:0 0 14px}.panel-filter input{max-width:520px;padding:8px 10px}.filter-pill{border:1px solid var(--line);background:#fff;color:#33465d;padding:7px 10px}.filter-pill.active{color:#087d56;border-color:#8bdabd;background:#edfbf5}@media(max-width:900px){.app-rail{width:48px}.top,.wrap{margin-left:48px}.summary{float:none;overflow:auto;justify-content:flex-start}.actions{overflow:auto}.toolbar{display:block}.panel-filter{flex-wrap:wrap}}</style>');
document.head.insertAdjacentHTML('beforeend','<style>.app-rail{width:224px;align-items:stretch;padding:12px 10px}.rail-brand{display:flex;align-items:center;gap:10px;color:#fff;font-weight:800;font-size:15px;padding:0 8px 14px;border-bottom:1px solid #ffffff22;margin-bottom:10px}.rail-logo{flex:0 0 36px;margin:0}.rail-brand small{display:block;color:#9fc3ff;font-size:10px;margin-top:2px}.rail-nav{gap:3px}.rail-nav button,.rail-bottom{height:39px;display:flex;align-items:center;gap:11px;justify-content:flex-start;padding:0 11px;border-radius:6px;border-left:0;font-size:12px;font-weight:650}.rail-nav button:hover,.rail-nav button.active{border-left:0}.rail-icon{width:21px;text-align:center;font-size:16px}.rail-section{color:#84a9eb;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;padding:13px 11px 5px}.rail-separator{height:1px;background:#ffffff1b;margin:7px 8px}.top,.wrap{margin-left:224px}.toolbar .actions{display:none}.rail-nav button:disabled{opacity:.35}.rail-admin{display:none}@media(max-width:900px){.app-rail{width:58px;padding:10px 5px}.rail-brand span:not(.rail-logo),.rail-label,.rail-section{display:none}.rail-brand{padding:0 5px 12px}.rail-nav button,.rail-bottom{justify-content:center;padding:0}.top,.wrap{margin-left:58px}}</style>');
document.body.insertAdjacentHTML('afterbegin','<aside class="app-rail"><div class="rail-brand"><span class="rail-logo">E</span><span>EPG Stream<small>Central de operação</small></span></div><nav class="rail-nav"><div class="rail-section">Operação</div><button class="active" onclick="window.scrollTo({top:0,behavior:\'smooth\'})"><span class="rail-icon">▶</span><span class="rail-label">Portadoras</span></button><button id="railNewCarrier" onclick="openCarrier()"><span class="rail-icon">＋</span><span class="rail-label">Nova portadora</span></button><button id="railTimeline" onclick="openTimeline()"><span class="rail-icon">▦</span><span class="rail-label">Grade de programação</span></button><button id="railSimulator" onclick="openTvSimulator()"><span class="rail-icon">▣</span><span class="rail-label">Simulador TV / PIDs</span></button><button id="railRestart" class="rail-admin" onclick="restartAllCarriers()"><span class="rail-icon">↻</span><span class="rail-label">Reiniciar todos os fluxos</span></button><div class="rail-section">Dados do EPG</div><button id="railSources" onclick="openSources()"><span class="rail-icon">⇄</span><span class="rail-label">Fontes XMLTV</span></button><button id="railPublications" onclick="openPublications()"><span class="rail-icon">≡</span><span class="rail-label">Publicações XMLTV</span></button><button id="railErrors" onclick="openEpgErrors()"><span class="rail-icon">!</span><span class="rail-label">Central de erros</span></button><div class="rail-section">Administração</div><button id="railUsers" class="rail-admin" onclick="openUsers()"><span class="rail-icon">♙</span><span class="rail-label">Usuários</span></button><button id="railLicense" class="rail-admin" onclick="openLicense()"><span class="rail-icon">◆</span><span class="rail-label">Licença</span></button><button id="railBackup" class="rail-admin" onclick="backupConfiguration()"><span class="rail-icon">⇩</span><span class="rail-label">Baixar backup</span></button><button id="railRestore" class="rail-admin" onclick="el(\'configRestoreFile\').click()"><span class="rail-icon">⇧</span><span class="rail-label">Restaurar backup</span></button><div class="rail-separator"></div><button onclick="openAbout()"><span class="rail-icon">ⓘ</span><span class="rail-label">Sobre o sistema</span></button></nav><button class="rail-bottom" onclick="location.reload()"><span class="rail-icon">⟳</span><span class="rail-label">Atualizar painel</span></button></aside>');
document.head.insertAdjacentHTML('beforeend','<style>.modal-back{left:224px;padding:16px;z-index:9}.modal.timeline-modal{width:calc(100vw - 256px);max-width:1500px}.timeline-corner,.timeline-channel{padding-left:14px}@media(max-width:900px){.modal-back{left:58px;padding:8px}.modal.timeline-modal{width:calc(100vw - 74px);height:calc(100vh - 16px);border-radius:10px}}@media(max-width:560px){.modal-back{left:0;padding:4px;z-index:11}.modal.timeline-modal{width:calc(100vw - 8px);height:calc(100vh - 8px)}}</style>');
document.head.insertAdjacentHTML('beforeend','<style>.top .brand{width:190px;height:46px;background:url("/assets/omniepg_logotipo_escuro.svg") center/contain no-repeat}.top .brand>*{display:none}.rail-brand{height:58px;background:url("/assets/omniepg_logotipo_transparente.svg") center/contain no-repeat}.rail-brand>*{display:none}.brand-image{width:190px;height:46px;object-fit:contain}@media(max-width:900px){.rail-brand{height:42px;background-image:url("/assets/omniepg_icone.svg");background-size:38px 38px;border-bottom:0}}</style>');
document.head.insertAdjacentHTML('beforeend','<style>.source-usage-summary{display:inline-flex;align-items:center;gap:6px;margin-top:9px;padding:6px 9px;border:1px solid #bad7ea;border-radius:8px;background:#eef8fe;color:#275675;font-size:12px}.source-usage-summary b{font-size:15px;color:var(--blue)}</style>');
document.getElementById('railUsers')?.insertAdjacentHTML('beforebegin','<button id="railSettings" class="rail-admin" onclick="openGeneralSettings()"><span class="rail-icon">⚙</span><span class="rail-label">Configurações gerais</span></button>');
setInterval(()=>{const button=document.getElementById('railSettings');if(button)button.style.display=session?.user?.role==='admin'?'flex':'none'},500);
let state={carriers:[],sources:[]},sources=[],catalog=[],session={user:null},users=[],publications=[],expandedCarriers=new Set(),guideCache={},overviewMode='carriers',timelineData=null,timelineStart=0,timelineWindow=8*3600,timelineDay=0;const el=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
document.head.insertAdjacentHTML('beforeend','<style>.overview-switch{display:flex;gap:6px;padding:5px;background:#dfe9f2;border-radius:11px}.overview-switch button{min-width:130px}.overview-switch button.active{background:linear-gradient(120deg,var(--blue),var(--cyan));color:#fff}.channel-table{width:100%;min-width:1120px;border-collapse:collapse}.channel-table th,.channel-table td{padding:12px 14px;border-top:1px solid var(--line);text-align:left;vertical-align:middle}.channel-table th{background:#edf4fa;color:#526477;font-size:11px;text-transform:uppercase}.channel-table tr:hover td{background:#f8fbfd}.channel-actions{display:flex;gap:6px;justify-content:flex-end}.channel-actions button{padding:8px 10px}.view-empty{padding:42px;text-align:center;color:var(--muted)}@media(max-width:760px){.overview-switch{width:100%}.overview-switch button{flex:1;min-width:0}}</style>');
document.querySelector('.summary')?.insertAdjacentHTML('afterend','<div class="panel-filter"><div class="overview-switch"><button id="viewCarriers" class="active" onclick="setOverviewMode(\'carriers\')">Por modulador</button><button id="viewChannels" onclick="setOverviewMode(\'channels\')">Por canal</button></div><input id="carrierFilter" placeholder="Filtrar por modulador, canal, multicast, TSID ou ONID" oninput="filterCarriers()"><label class="overview-sort">Ordenar por <select id="carrierSort" onchange="setCarrierSort(this.value)"><option value="name">Portadora</option><option value="multicast">Destino multicast</option></select></label><button class="filter-pill active" onclick="setCarrierFilter(this,\'all\')">Todos</button><button class="filter-pill" onclick="setCarrierFilter(this,\'running\')">Em transmissão</button><button class="filter-pill" onclick="setCarrierFilter(this,\'error\')">Com erro</button></div>');
let carrierStatusFilter='all',carrierSortMode='name';
function setCarrierFilter(button,status){carrierStatusFilter=status;document.querySelectorAll('.filter-pill').forEach(x=>x.classList.toggle('active',x===button));filterCarriers()}
function setOverviewMode(mode){overviewMode=mode==='channels'?'channels':'carriers';el('viewCarriers')?.classList.toggle('active',overviewMode==='carriers');el('viewChannels')?.classList.toggle('active',overviewMode==='channels');render();filterCarriers()}
function multicastSortKey(carrier){return String(carrier.destination||'').split('.').reduce((value,part)=>value*256+(Number(part)||0),0)*65536+(Number(carrier.port)||0)}
function sortedCarriers(carriers){return [...carriers].sort((a,b)=>carrierSortMode==='multicast'?multicastSortKey(a)-multicastSortKey(b)||String(a.name||'').localeCompare(String(b.name||''),'pt-BR',{numeric:true}):String(a.name||'').localeCompare(String(b.name||''),'pt-BR',{numeric:true,sensitivity:'base'}))}
function setCarrierSort(mode){carrierSortMode=mode==='multicast'?'multicast':'name';render();filterCarriers()}
function filterCarriers(){const term=(el('carrierFilter')?.value||'').toLocaleLowerCase('pt-BR');if(overviewMode==='channels'){document.querySelectorAll('.channel-table tbody tr').forEach(row=>{const status=carrierStatusFilter==='all'||(carrierStatusFilter==='running'&&row.dataset.active==='1')||(carrierStatusFilter==='error'&&row.dataset.error==='1');row.hidden=!(status&&(!term||row.dataset.search.includes(term)))});return}document.querySelectorAll('.carrier-table tr.main-row').forEach((row,index)=>{const carrier=(state.carriers||[])[index],channels=(carrier?.services||[]).map(s=>`${s.name||''} ${s.epg_channel_id||''}`).join(' '),search=`${row.textContent} ${channels}`.toLocaleLowerCase('pt-BR'),status=carrierStatusFilter==='all'||(carrierStatusFilter==='running'&&row.querySelector('.badge.running'))||(carrierStatusFilter==='error'&&(row.classList.contains('health-error')||row.querySelector('.badge.error'))),show=status&&(!term||search.includes(term));row.hidden=!show;const details=row.nextElementSibling;if(details?.classList.contains('program-row'))details.hidden=!show})}
document.querySelector('main .toolbar .actions').insertAdjacentHTML('afterbegin','<button id="licenseButton" style="display:none" onclick="openLicense()">Licença</button>');
document.querySelector('main .toolbar .actions').insertAdjacentHTML('afterbegin','<button id="configRestoreButton" style="display:none" onclick="el(\'configRestoreFile\').click()">Restaurar backup completo</button><input id="configRestoreFile" type="file" accept=".gz,.tgz,application/gzip" hidden onchange="restoreConfiguration(this)"><button id="configBackupButton" style="display:none" onclick="backupConfiguration()">Baixar backup completo</button>');
document.querySelector('main .toolbar .actions').insertAdjacentHTML('afterbegin','<button id="tvSimulatorButton" onclick="openTvSimulator()">Simular TV / PIDs</button>');
const fmt=t=>t?new Date(t*1000).toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'}):'--:--';
function toast(message,error=false){el('toast').innerHTML=`<div class="toast ${error?'error-text':''}">${esc(message)}</div>`;setTimeout(()=>el('toast').innerHTML='',3500)}
async function api(url,opt={}){const r=await fetch(url,{headers:{'Content-Type':'application/json'},...opt});const j=await r.json().catch(()=>({error:'Resposta inválida'}));if(!r.ok||j.error)throw Error(j.error||`HTTP ${r.status}`);return j}
async function refresh(){try{const loaded=await Promise.all([api('/api/state'),api('/api/session')]);state=loaded[0];session=loaded[1];const valid=!!state.license?.valid,admin=session.user?.role==='admin';sources=valid?(await api('/api/sources')).sources:[];el('usersButton').style.display=admin?'':'none';el('licenseButton').style.display=admin?'':'none';for(const id of ['configBackupButton','configRestoreButton','restartAllButton'])el(id).style.display=admin?'':'none';for(const id of ['publicationsButton','sourcesButton','timelineButton','restartAllButton','newCarrierButton','tvSimulatorButton'])el(id).disabled=!valid;el('timelineButton').disabled=!valid||!(state.carriers||[]).length;for(const id of ['railNewCarrier','railTimeline','railSimulator','railSources','railPublications','railErrors'])if(el(id))el(id).disabled=!valid;if(el('railTimeline'))el('railTimeline').disabled=!valid||!(state.carriers||[]).length;for(const id of ['railRestart','railUsers','railLicense','railBackup','railRestore'])if(el(id))el(id).style.display=admin?'flex':'none';if(el('railRestart'))el('railRestart').disabled=!valid;el('licenseAlert').hidden=valid;render();filterCarriers();el('clock').textContent=`${session.user?.display_name||''} · ${new Date().toLocaleTimeString('pt-BR')}`}catch(e){toast(e.message,true)}}
function backupConfiguration(){if(confirm('O backup contém usuários, hashes de senha, URLs e tokens de fontes. Guarde o arquivo em local seguro. Deseja continuar?'))location.href='/api/config/backup'}
async function restoreConfiguration(input){const file=input.files?.[0];input.value='';if(!file)return;if(file.size>512*1024*1024){toast('O backup deve possuir no máximo 512 MiB',true);return}if(!confirm('Restaurar este backup completo? Configurações, XMLTV, caches e demais arquivos persistentes atuais serão substituídos e o serviço será reiniciado.'))return;try{const response=await fetch('/api/config/restore',{method:'POST',headers:{'Content-Type':'application/gzip'},body:file});const result=await response.json().catch(()=>({error:'Resposta inválida'}));if(!response.ok||result.error)throw Error(result.error||`HTTP ${response.status}`);alert(result.message||'Backup restaurado. O serviço será reiniciado.');setTimeout(()=>location.reload(),5000)}catch(e){toast(e.message,true)}}
function healthCarrier(id){return (state.epg_health?.carriers||[]).find(c=>c.id===id)||{status:'warning',failing:0,warnings:0,total:0,services:[]}}
function healthService(carrierId,serviceId){return healthCarrier(carrierId).services.find(s=>s.service_id===serviceId)||{status:'warning',message:'Diagnóstico aguardando sincronização'}}
function render(){const cs=state.carriers||[],license=state.license||{},errors=state.epg_health?.errors||[];el('mCarriers').textContent=cs.length;el('mActive').textContent=cs.filter(c=>c.active).length;el('mServices').textContent=cs.reduce((n,c)=>n+c.services.length,0);el('mRestarts').textContent=cs.reduce((n,c)=>n+(c.restart_count||0),0);el('mLicense').textContent=license.valid?`${license.channel_count}/${license.max_channels}`:'Bloqueada';el('licenseReason').textContent=license.valid?'canais utilizados':license.reason||'Licença inválida';el('licenseMetric').className=`metric ${license.valid?'license-valid':'license-invalid'}`;el('epgErrorsButton').textContent=`Erros do EPG (${errors.length})`;el('epgErrorsButton').className=errors.length?'has-errors':'';el('epgHealthAlert').hidden=!errors.length;el('epgHealthAlert').innerHTML=errors.length?`⚠ ${errors.length} canal(is) exigem atenção. <button onclick="openEpgErrors()">Consultar erros</button>`:'';el('viewCarriers')?.classList.toggle('active',overviewMode==='carriers');el('viewChannels')?.classList.toggle('active',overviewMode==='channels');if(overviewMode==='channels'){renderChannelOverview(cs);return}el('carrierTable').innerHTML=cs.length?`<div class="table-wrap"><table class="carrier-table"><thead><tr><th>Portadora</th><th>Destino multicast</th><th>Canais</th><th>Estado</th><th class="actions-col">Ações</th></tr></thead><tbody>${cs.map(carrierRows).join('')}</tbody></table></div>`:'<div class="card empty"><h3>Nenhuma portadora cadastrada</h3><p>Cadastre a primeira portadora e associe os canais do XMLTV.</p></div>'}
function renderChannelOverview(carriers){const errors=state.epg_health?.errors||[],rows=carriers.flatMap(carrier=>carrier.services.map(service=>{const source=sources.find(item=>item.id===(service.source_id||carrier.source_id)),error=errors.some(item=>item.carrier_id===carrier.id&&(item.service_id===service.id||item.service_id===service.service_id)),search=`${service.name} ${service.epg_channel_id} ${service.service_id} ${carrier.name} ${carrier.destination} ${carrier.port} ${carrier.transport_stream_id} ${carrier.original_network_id}`.toLocaleLowerCase('pt-BR');return `<tr data-active="${carrier.active?'1':'0'}" data-error="${error?'1':'0'}" data-search="${esc(search)}"><td><div class="carrier-name"><i class="health-dot ${error?'error':carrier.active?'ok':'warning'}"></i>${esc(service.name)}</div><div class="carrier-sub">XMLTV: ${esc(service.epg_channel_id)}</div></td><td><b>SID ${service.service_id}</b><div class="carrier-sub">${esc(source?.name||'Fonte não encontrada')}</div></td><td><b>${esc(carrier.name)}</b><div class="carrier-sub">TSID ${carrier.transport_stream_id} · ONID ${carrier.original_network_id}</div></td><td><b>${esc(carrier.destination)}:${carrier.port}</b></td><td><span class="badge ${error?'error':carrier.active?'running':''}">${error?'Erro de EPG':carrier.active?'Em transmissão':'Parado'}</span></td><td><div class="channel-actions"><button onclick="editChannel('${esc(carrier.id)}','${esc(service.id)}')" ${state.license?.valid?'':'disabled'}>Editar</button><button class="danger" onclick="deleteChannel('${esc(carrier.id)}','${esc(service.id)}')" ${state.license?.valid?'':'disabled'}>Excluir</button></div></td></tr>`}));el('carrierTable').innerHTML=rows.length?`<div class="table-wrap"><table class="channel-table"><thead><tr><th>Canal</th><th>SID / fonte</th><th>Modulador</th><th>Multicast</th><th>Estado</th><th style="text-align:right">Ações</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`:'<div class="card view-empty"><h3>Nenhum canal cadastrado</h3><p>Adicione canais em uma portadora para gerenciá-los nesta visão.</p></div>'}
function utcOffsetLabel(minutes){const sign=minutes<0?'-':'+';const absolute=Math.abs(minutes);return `UTC${sign}${String(Math.floor(absolute/60)).padStart(2,'0')}:${String(absolute%60).padStart(2,'0')}`}
function clockLabel(c){return c.clock_mode==='custom'?`${utcOffsetLabel(c.clock_utc_offset_minutes??-180)} · correção ${c.clock_correction_minutes>0?'+':''}${c.clock_correction_minutes||0} min`:'Padrão UTC-03:00'}
function carrierRows(c){const opened=expandedCarriers.has(c.id),guide=guideCache[c.id],disabled=state.license?.valid?'':' disabled',health=healthCarrier(c.id),label=health.status==='ok'?'EPG normal':health.status==='error'?'Todos os canais com erro':`${health.failing+health.warnings} canal(is) com problema`;return `<tr class="main-row health-${health.status}"><td><div class="carrier-name"><i class="health-dot ${health.status}"></i>${esc(c.name)}</div><div class="carrier-sub">TSID ${c.transport_stream_id} · ONID ${c.original_network_id} · ${esc(clockLabel(c))}</div></td><td><b>${esc(c.destination)}:${c.port}</b><div class="carrier-sub">${(c.bitrate/1000).toLocaleString('pt-BR')} kbit/s</div></td><td><b>${c.services.length}</b> serviço(s)<div class="health-message ${health.status}">${esc(label)}</div></td><td><span class="badge ${esc(c.status)}">${c.active?'Em transmissão':c.status==='error'?'Falha':'Parada'}</span>${c.last_error?`<div class="error-text carrier-sub">${esc(c.last_error)}</div>`:''}</td><td class="actions-col"><div class="action-stack"><button id="programButton-${esc(c.id)}" class="primary wide" aria-expanded="${opened}" onclick="togglePrograms('${esc(c.id)}')"${disabled}>${opened?'Ocultar programação':'Ver programação'}</button><button onclick="actionCarrier('${esc(c.id)}','${c.active?'restart':'start'}')"${disabled}>${c.active?'Reiniciar':'Iniciar'}</button>${c.active?`<button onclick="actionCarrier('${esc(c.id)}','stop')"${disabled}>Parar</button>`:'<span></span>'}<button onclick="openCarrier('${esc(c.id)}')"${disabled}>Editar</button><button onclick="cloneCarrier('${esc(c.id)}')"${disabled}>Clonar</button><button onclick="openLogs('${esc(c.id)}')"${disabled}>Logs</button><button class="danger" onclick="deleteCarrier('${esc(c.id)}')"${disabled}>Excluir</button></div></td></tr><tr id="programs-${esc(c.id)}" class="program-row ${opened?'open':''}"><td colspan="5"><div class="program-panel">${opened?(guide?programPanel(c,guide):'<div class="muted">Carregando programação…</div>'):''}</div></td></tr>`}
function programPanel(c,g){return `<div class="program-title"><div><b>Programação da portadora</b><div class="muted">${esc(g.timezone||'America/Sao_Paulo')} · ${c.services.length} serviço(s)</div></div></div><div class="program-list">${g.services.map(s=>{const health=healthService(c.id,s.id);return `<div class="program-line health-${health.status}"><div><b><i class="health-dot ${health.status}"></i>${esc(s.name)}</b><div class="muted">${esc(s.epg_channel_id)}</div><div class="health-message ${health.status}">${esc(health.message)}</div></div><div>SID ${s.service_id}</div><div class="program-detail"><small class="muted">HORÁRIO</small><div>${s.current?`${fmt(s.current.start)}–${fmt(s.current.stop)}`:'--:--'}</div></div><div class="program-detail"><small class="muted">NO AR AGORA</small><div class="program-now">${esc(s.current?.title||'Sem programa no ar')}</div>${s.current?`<div class="progress"><i style="width:${s.current.progress||0}%"></i></div>`:''}</div><div class="program-detail"><small class="muted">A SEGUIR</small><div>${esc(s.next?.title||'Sem próxima atração')}</div></div><button onclick="openGuide('${esc(c.id)}','${esc(s.id)}')">Ver grade</button></div>`}).join('')||'<div class="muted">Nenhum serviço cadastrado.</div>'}</div>`}
function openEpgErrors(){const errors=state.epg_health?.errors||[];modal(`<div class="guide-head"><div><h2>Central de erros do EPG</h2><div class="muted">Diagnóstico do XMLTV e do processo de emissão multicast · atualizado ${new Date((state.epg_health?.generated_at||0)*1000).toLocaleString('pt-BR')}</div></div><button onclick="closeModal()">Fechar</button></div>${errors.length?`<div class="table-wrap" style="margin-top:15px"><table class="health-error-table"><thead><tr><th>Portadora</th><th>Canal</th><th>Fonte XMLTV</th><th>ID XMLTV</th><th>Diagnóstico</th></tr></thead><tbody>${errors.map(e=>`<tr><td>${esc(e.carrier_name)}</td><td><b><i class="health-dot ${e.status}"></i>${esc(e.service_name)}</b></td><td><b>${esc(e.source_name||e.source_id)}</b><div class="muted"><code>${esc(e.source_url||'URL não disponível')}</code></div></td><td><code>${esc(e.epg_channel_id)}</code></td><td><b>${esc(e.code)}</b><div class="health-message ${e.status}">${esc(e.message)}</div></td></tr>`).join('')}</tbody></table></div>`:'<div class="card empty"><h3>Todos os canais estão com programação normal</h3><p>Nenhum erro de XMLTV ou emissão foi detectado.</p></div>'}`,'publication-modal')}
const TIMELINE_STEP=2*3600;
function defaultTimelineStart(){return Math.floor((Date.now()/1000-3600)/1800)*1800}
function timelineDayStart(offset=0){const d=new Date();d.setHours(0,0,0,0);d.setDate(d.getDate()+offset);return Math.floor(d.getTime()/1000)}
function timelineDayButtons(){return Array.from({length:7},(_,i)=>{const d=new Date(timelineDayStart(i)*1000),label=i===0?'Hoje':i===1?'Amanhã':d.toLocaleDateString('pt-BR',{weekday:'short',day:'2-digit'});return `<button class="${i===timelineDay?'active':''}" onclick="selectTimelineDay(${i})">${esc(label)}</button>`}).join('')}
async function openTimeline(_carrierId='',start=null){if(!(state.carriers||[]).length){toast('Cadastre uma portadora antes de abrir a grade',true);return}timelineStart=(start??timelineStart)||defaultTimelineStart();modal(`<div class="timeline-head"><div class="guide-head"><div><h2 style="margin:0 0 5px">Grade geral de programação</h2><div style="color:#aa9ca5">Todos os canais e portadoras configurados no EPG Stream.</div></div><button onclick="closeModal()">Fechar</button></div><div class="timeline-days">${timelineDayButtons()}<button class="primary" onclick="resetTimeline()">Agora</button></div><div class="timeline-tools"><select id="timelineCategory" onchange="renderTimeline(timelineData)"><option value="">Todas as categorias</option></select><input id="timelineChannelSearch" placeholder="Buscar canais" oninput="renderTimeline(timelineData)"><input id="timelineProgramSearch" placeholder="Buscar programas" oninput="renderTimeline(timelineData)"><button onclick="shiftTimeline(-TIMELINE_STEP)">←</button><button onclick="zoomTimeline(-1)">−</button><button onclick="zoomTimeline(1)">+</button><button onclick="shiftTimeline(TIMELINE_STEP)">→</button><button onclick="loadTimeline(true)">Atualizar</button><span id="timelineStats" class="timeline-stats"></span></div></div><div id="timelineContent" class="timeline-scroll"><div class="empty">Sincronizando programação…</div></div>`,'timeline-modal');await loadTimeline()}
async function loadTimeline(force=false){const content=el('timelineContent');if(!content)return;content.innerHTML='<div class="empty">Sincronizando canais e programação…</div>';try{const start=timelineDayStart(timelineDay),end=start+86400;timelineData=await api(`/api/guides?start=${start}&end=${end}${force?'&force=1':''}`);const categories=[...new Set(timelineData.services.flatMap(s=>s.schedule.map(p=>p.category).filter(Boolean)))].sort();const select=el('timelineCategory');if(select)select.innerHTML='<option value="">Todas as categorias</option>'+categories.map(c=>`<option>${esc(c)}</option>`).join('');renderTimeline(timelineData)}catch(e){content.innerHTML=`<div class="empty error-text">${esc(e.message)}</div>`}}
function renderTimeline(g){const content=el('timelineContent');if(!content||!g)return;const end=timelineStart+timelineWindow,now=Date.now()/1000,channelQuery=(el('timelineChannelSearch')?.value||'').toLocaleLowerCase('pt-BR'),programQuery=(el('timelineProgramSearch')?.value||'').toLocaleLowerCase('pt-BR'),category=el('timelineCategory')?.value||'',services=g.services.filter(s=>(!channelQuery||`${s.name} ${s.carrier_name}`.toLocaleLowerCase('pt-BR').includes(channelQuery))&&(!category||s.schedule.some(p=>p.category===category))&&(!programQuery||s.schedule.some(p=>`${p.title} ${p.description||''}`.toLocaleLowerCase('pt-BR').includes(programQuery))));const ticks=Math.max(2,Math.round(timelineWindow/3600)),hours=Array.from({length:ticks},(_,i)=>`<span class="timeline-hour" style="left:${i*100/ticks}%">${fmt(timelineStart+i*timelineWindow/ticks)}</span>`).join(''),nowPosition=(now-timelineStart)*100/timelineWindow,nowLine=now>=timelineStart&&now<=end?`<i class="timeline-now" style="left:${nowPosition}%"></i>`:'',trackNowLine=nowLine?`<i class="timeline-now track" style="left:${nowPosition}%"></i>`:'';let visiblePrograms=0;const rows=services.map(s=>{const programmes=s.schedule.filter(p=>p.stop>timelineStart&&p.start<end&&(!category||p.category===category)&&(!programQuery||`${p.title} ${p.description||''}`.toLocaleLowerCase('pt-BR').includes(programQuery)));visiblePrograms+=programmes.length;const blocks=programmes.map(p=>{const left=(Math.max(p.start,timelineStart)-timelineStart)*100/timelineWindow,width=Math.max((Math.min(p.stop,end)-Math.max(p.start,timelineStart))*100/timelineWindow,.8),current=p.start<=now&&now<p.stop;return `<button class="timeline-program ${current?'current':''}" style="left:${left}%;width:${width}%" onclick="openTimelineProgram('${esc(s.row_id)}',${p.start})"><b>${esc(p.title||'Programa sem título')}</b><small>${fmt(p.start)} – ${fmt(p.stop)}</small></button>`}).join('');return `<div class="timeline-row"><div class="timeline-channel"><div><strong>${esc(s.name)}</strong><small>${esc(s.carrier_name)} · TSID ${s.transport_stream_id} · SID ${s.service_id}</small></div></div><div class="timeline-track">${trackNowLine}${blocks||'<div class="timeline-empty">Sem programação nesta janela</div>'}</div></div>`}).join('');el('timelineStats').textContent=`${services.length} canais · ${visiblePrograms} programas`;content.innerHTML=`<div class="timeline-board"><div class="timeline-axis"><div class="timeline-corner">Canal / portadora</div><div class="timeline-hours">${nowLine}${hours}</div></div>${rows||'<div class="empty">Nenhum canal corresponde aos filtros.</div>'}</div>`}
function shiftTimeline(seconds){timelineStart+=seconds;renderTimeline(timelineData)}
function zoomTimeline(direction){const levels=[2,4,6,8,12,24],index=levels.indexOf(timelineWindow/3600),next=levels[Math.max(0,Math.min(levels.length-1,index-direction))];timelineWindow=next*3600;renderTimeline(timelineData)}
function selectTimelineDay(offset){timelineDay=offset;timelineStart=offset===0?defaultTimelineStart():timelineDayStart(offset)+6*3600;openTimeline('',timelineStart)}
function resetTimeline(){timelineDay=0;timelineStart=defaultTimelineStart();openTimeline('',timelineStart)}
function openTimelineProgram(rowId,start){const service=timelineData?.services.find(s=>s.row_id===rowId),program=service?.schedule.find(p=>p.start===start);if(!service||!program)return;modal(`<div class="guide-head"><div><h2>${esc(program.title||'Programa sem título')}</h2><div class="muted">${esc(service.name)} · ${esc(service.carrier_name)} · SID ${service.service_id}</div></div><button onclick="openTimeline('',${timelineStart})">Voltar à grade</button></div><div class="card" style="padding:18px;margin-top:16px"><b>${fmt(program.start)} — ${fmt(program.stop)}</b><p>${esc(program.description||'Sem descrição disponível.')}</p>${program.category?`<span class="badge">${esc(program.category)}</span>`:''}</div>`)}
async function togglePrograms(id){const row=el(`programs-${id}`),button=el(`programButton-${id}`),carrier=state.carriers.find(c=>c.id===id);if(!row||!carrier)return;if(expandedCarriers.has(id)){expandedCarriers.delete(id);delete guideCache[id];row.classList.remove('open');row.querySelector('.program-panel').innerHTML='';button.textContent='Ver programação';button.setAttribute('aria-expanded','false');return}expandedCarriers.add(id);row.classList.add('open');button.textContent='Ocultar programação';button.setAttribute('aria-expanded','true');const panel=row.querySelector('.program-panel');panel.innerHTML='<div class="muted">Carregando programação…</div>';try{guideCache[id]=await api(`/api/guide?carrier_id=${encodeURIComponent(id)}`);panel.innerHTML=programPanel(carrier,guideCache[id])}catch(e){expandedCarriers.delete(id);row.classList.remove('open');button.textContent='Ver programação';button.setAttribute('aria-expanded','false');toast(e.message,true)}}
function closeModal(){el('overlay').innerHTML=''}function modal(html,extraClass=''){const suffix=extraClass?` ${esc(extraClass)}`:'';el('overlay').innerHTML=`<div class="modal-back"><div class="modal${suffix}">${html}</div></div>`}
let serviceRowSequence=0;
async function ensureCatalogFor(select){const row=select.closest('.service-edit'),list=row?.querySelector('datalist'),sourceId=select.value||el('cSource')?.value;if(!list||!sourceId)return;list.innerHTML='<option value="Carregando…">';try{const channels=(await api(`/api/catalog?source_id=${encodeURIComponent(sourceId)}`)).channels;list.innerHTML=channels.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}catch(e){list.innerHTML='';toast(e.message,true)}}
function sourceOptions(selected=''){return `<option value="">Herdar fonte da portadora</option>${sources.map(item=>`<option value="${esc(item.id)}" ${item.id===selected?'selected':''}>${esc(item.name)}</option>`).join('')}`}
function categoryOptions(selected=''){return `<option value="">Usar categoria do XMLTV</option>${['Filmes','Notícias','Entretenimento','Esportes','Infantil','Música','Cultura','Sociedade','Educação','Lazer'].map(v=>`<option ${v===selected?'selected':''}>${v}</option>`).join('')}`}
function utcOffsetOptions(selected=-180){let options='';for(let minutes=-720;minutes<=840;minutes+=15)options+=`<option value="${minutes}" ${minutes===selected?'selected':''}>${utcOffsetLabel(minutes)}</option>`;return options}
function clockModeChanged(){const custom=el('cClockMode')?.value==='custom';for(const id of ['cClockOffset','cClockCorrection'])if(el(id))el(id).disabled=!custom;const help=el('clockHelp');if(help)help.textContent=custom?'O relógio continua avançando; a correção apenas soma ou subtrai minutos.':'Mantém exatamente o fluxo atual: UTC-03:00 e nenhuma correção.'}
function serviceRow(s={}){const listId=`channelList-${++serviceRowSequence}`;return `<div class="service-edit"><label>Fonte do canal<select class="s-source" onchange="ensureCatalogFor(this)">${sourceOptions(s.source_id||'')}</select></label><label>Nome<input class="s-name" value="${esc(s.name||'')}"></label><label>ID XMLTV<input class="s-channel" list="${listId}" value="${esc(s.epg_channel_id||'')}"><datalist id="${listId}"></datalist></label><label>SID<input class="s-sid" type="number" min="1" max="65535" value="${s.service_id||1}"></label><label>Categoria padrão<select class="s-category">${categoryOptions(s.default_category||'')}</select><span class="logo-status">Usada apenas quando o XMLTV não informar uma categoria reconhecida.</span></label><button class="danger" onclick="this.parentElement.remove()">Remover</button><input class="s-id" type="hidden" value="${esc(s.id||'')}"></div>`}
async function editChannel(carrierId,serviceId){const carrier=state.carriers.find(item=>item.id===carrierId),service=carrier?.services.find(item=>item.id===serviceId);if(!carrier||!service){toast('Canal não encontrado',true);return}const effectiveSource=service.source_id||carrier.source_id;let options=`<option value="">Herdar de ${esc(sources.find(item=>item.id===carrier.source_id)?.name||'portadora')}</option>`+sources.map(item=>`<option value="${esc(item.id)}" ${item.id===service.source_id?'selected':''}>${esc(item.name)}</option>`).join('');modal(`<div class="guide-head"><div><h2>Editar canal</h2><div class="muted">${esc(carrier.name)} · ${esc(carrier.destination)}:${carrier.port} · TSID ${carrier.transport_stream_id} · ONID ${carrier.original_network_id}</div></div><button onclick="closeModal()">Fechar</button></div><div class="form-grid" style="margin-top:18px"><label>Nome do canal<input id="channelName" value="${esc(service.name)}"></label><label>SID<input id="channelSid" type="number" min="1" max="65535" value="${service.service_id}"></label><label>Fonte XMLTV<select id="channelSource" onchange="populateChannelCatalog(this.value||'${esc(carrier.source_id)}')">${options}</select></label><label class="wide">ID do canal no XMLTV<input id="channelXmltv" list="channelDirectCatalog" value="${esc(service.epg_channel_id)}"><datalist id="channelDirectCatalog"></datalist></label><label class="wide">Categoria padrão<select id="channelCategory">${categoryOptions(service.default_category||'')}</select></label></div><p class="muted">Ao salvar, somente esta portadora será atualizada. Se estiver ativa, o emissor aplicará a nova configuração automaticamente.</p><div class="modal-actions"><button onclick="closeModal()">Cancelar</button><button class="primary" onclick="saveChannel('${esc(carrierId)}','${esc(serviceId)}')">Salvar canal</button></div>`);await populateChannelCatalog(effectiveSource)}
async function populateChannelCatalog(sourceId){const list=el('channelDirectCatalog');if(!list||!sourceId)return;list.innerHTML='<option value="Carregando…">';try{const channels=(await api(`/api/catalog?source_id=${encodeURIComponent(sourceId)}`)).channels||[];list.innerHTML=channels.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('')}catch(_){list.innerHTML=''}}
async function saveChannel(carrierId,serviceId){try{const carrier=state.carriers.find(item=>item.id===carrierId),sid=+el('channelSid').value;if(!carrier)throw new Error('Portadora não encontrada');const services=carrier.services.map(item=>item.id===serviceId?{...item,name:el('channelName').value.trim(),service_id:sid,source_id:el('channelSource').value,epg_channel_id:el('channelXmltv').value.trim(),default_category:el('channelCategory').value}:item);await api('/api/carriers',{method:'POST',body:JSON.stringify({...carrier,services})});closeModal();toast('Canal atualizado');await refresh();setOverviewMode('channels')}catch(e){toast(e.message,true)}}
async function deleteChannel(carrierId,serviceId){const carrier=state.carriers.find(item=>item.id===carrierId),service=carrier?.services.find(item=>item.id===serviceId);if(!carrier||!service){toast('Canal não encontrado',true);return}if(carrier.services.length===1){toast('A portadora precisa manter ao menos um canal. Exclua a portadora ou adicione outro canal primeiro.',true);return}if(!confirm(`Excluir o canal ${service.name} da portadora ${carrier.name}?`))return;try{await api('/api/carriers',{method:'POST',body:JSON.stringify({...carrier,services:carrier.services.filter(item=>item.id!==serviceId)})});toast('Canal excluído');await refresh();setOverviewMode('channels')}catch(e){toast(e.message,true)}}
function cloneCarrier(id){const original=state.carriers.find(x=>x.id===id);if(!original){toast('Portadora não encontrada',true);return}const copy={...original,id:'',name:`${original.name} - Cópia`,destination:'',auto_start:false,services:original.services.map(service=>({...service,id:''}))};openCarrier('',copy,true)}
async function openCarrier(id='',template=null,cloning=false){const c=template||state.carriers.find(x=>x.id===id)||{auto_start:true,source_id:sources.find(s=>s.is_default)?.id||sources[0]?.id||'',transport_stream_id:1,original_network_id:1,port:5012,pmt_pid:4096,bitrate:1000000,ttl:32,clock_mode:'standard',clock_utc_offset_minutes:-180,clock_correction_minutes:0,services:[{service_id:1}]};modal(`<h2>${cloning?'Clonar':id?'Editar':'Nova'} portadora EPG</h2>${cloning?'<p class="muted">Informe um novo multicast. A cópia será salva com inicialização manual e não altera a portadora original.</p>':''}<div class="form-grid"><input id="cId" type="hidden" value="${esc(c.id||'')}"><label class="wide">Nome<input id="cName" value="${esc(c.name||'')}"></label><label>Fonte padrão da portadora<select id="cSource">${sources.map(s=>`<option value="${esc(s.id)}" ${s.id===c.source_id?'selected':''}>${esc(s.name)}</option>`).join('')}</select></label><label>TSID<input id="cTsid" type="number" value="${c.transport_stream_id}"></label><label>ONID<input id="cOnid" type="number" value="${c.original_network_id}"></label><label>Multicast<input id="cDest" value="${esc(c.destination||'')}" placeholder="239.192.1.201" ${cloning?'autofocus':''}></label><label>Porta<input id="cPort" type="number" value="${c.port}"></label><label>IP da interface<input id="cIface" value="${esc(c.interface_address||'')}"></label><label>PID base PMT<input id="cPmt" type="number" value="${c.pmt_pid}"></label><label>Bitrate (bit/s)<input id="cBitrate" type="number" value="${c.bitrate}"></label><label>TTL<input id="cTtl" type="number" value="${c.ttl}"></label><label><span>Inicialização</span><select id="cAuto"><option value="1" ${c.auto_start?'selected':''}>Automática</option><option value="0" ${!c.auto_start?'selected':''}>Manual</option></select></label><label>Relógio PID 0x0014<select id="cClockMode" onchange="clockModeChanged()"><option value="standard" ${(c.clock_mode||'standard')==='standard'?'selected':''}>Padrão do sistema</option><option value="custom" ${c.clock_mode==='custom'?'selected':''}>Fuso e correção personalizados</option></select></label><label>Fuso transmitido<select id="cClockOffset">${utcOffsetOptions(c.clock_utc_offset_minutes??-180)}</select></label><label>Correção do horário (minutos)<input id="cClockCorrection" type="number" min="-1440" max="1440" step="1" value="${c.clock_correction_minutes||0}"></label><div id="clockHelp" class="muted wide"></div></div><p class="muted">Cada canal pode herdar esta fonte ou selecionar outro XMLTV. O ajuste do relógio também mantém a EIT coerente com TDT/TOT.</p><div class="toolbar" style="margin-top:20px"><h3>Serviços da portadora</h3><button onclick="addServiceRow()">+ Canal</button></div><div id="serviceRows">${c.services.map(serviceRow).join('')}</div><div class="modal-actions"><button onclick="closeModal()">Cancelar</button><button class="primary" onclick="saveCarrier()">Salvar</button></div>`);clockModeChanged();for(const select of document.querySelectorAll('.s-source'))await ensureCatalogFor(select)}
function addServiceRow(){el('serviceRows').insertAdjacentHTML('beforeend',serviceRow());ensureCatalogFor(el('serviceRows').lastElementChild.querySelector('.s-source'))}
async function saveCarrier(){try{const original=state.carriers.find(c=>c.id===el('cId').value),services=[...document.querySelectorAll('.service-edit')].map(r=>{const id=r.querySelector('.s-id').value,previous=original?.services.find(s=>s.id===id);return{id,name:r.querySelector('.s-name').value,source_id:r.querySelector('.s-source').value,epg_channel_id:r.querySelector('.s-channel').value,service_id:+r.querySelector('.s-sid').value,default_category:r.querySelector('.s-category').value,logo:previous?.logo||{}}});await api('/api/carriers',{method:'POST',body:JSON.stringify({id:el('cId').value,name:el('cName').value,source_id:el('cSource').value,auto_start:el('cAuto').value==='1',transport_stream_id:+el('cTsid').value,original_network_id:+el('cOnid').value,destination:el('cDest').value,port:+el('cPort').value,interface_address:el('cIface').value,pmt_pid:+el('cPmt').value,bitrate:+el('cBitrate').value,ttl:+el('cTtl').value,clock_mode:el('cClockMode').value,clock_utc_offset_minutes:+el('cClockOffset').value,clock_correction_minutes:+el('cClockCorrection').value,services})});closeModal();toast('Portadora salva');refresh()}catch(e){toast(e.message,true)}}
async function uploadLogo(input){const file=input.files?.[0],carrierId=el('cId').value,serviceId=input.closest('.service-edit').querySelector('.s-id').value;if(!file)return;if(file.type!=='image/png'){toast('Selecione um arquivo PNG',true);return}if(file.size>2*1024*1024){toast('O logo deve possuir no máximo 2 MiB',true);return}try{const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file)});await api('/api/carriers/logo',{method:'POST',body:JSON.stringify({carrier_id:carrierId,service_id:serviceId,data})});closeModal();toast('Logo ISDB-TB salvo em seis formatos');await refresh();openCarrier(carrierId)}catch(e){toast(e.message,true)}}
async function deleteLogo(button){if(!confirm('Remover o logo deste canal?'))return;const row=button.closest('.service-edit');try{const carrierId=el('cId').value;await api('/api/carriers/logo/delete',{method:'POST',body:JSON.stringify({carrier_id:carrierId,service_id:row.querySelector('.s-id').value})});closeModal();toast('Logo removido');await refresh();openCarrier(carrierId)}catch(e){toast(e.message,true)}}
async function actionCarrier(id,action){try{await api(`/api/carriers/${action}`,{method:'POST',body:JSON.stringify({id})});toast('Ação executada');setTimeout(refresh,400)}catch(e){toast(e.message,true)}}
async function restartAllCarriers(){if(!confirm('Reiniciar agora todos os fluxos que deveriam estar ativos?'))return;try{const result=await api('/api/carriers/restart-all',{method:'POST',body:'{}'});toast(result.errors?.length?`${result.restarted} fluxo(s) reiniciado(s); ${result.errors.length} falha(s)`:`${result.restarted} fluxo(s) reiniciado(s)`);setTimeout(refresh,500)}catch(e){toast(e.message,true)}}
async function deleteCarrier(id){if(!confirm('Excluir esta portadora?'))return;try{await api('/api/carriers/delete',{method:'POST',body:JSON.stringify({id})});toast('Portadora excluída');refresh()}catch(e){toast(e.message,true)}}
function openGeneralSettings(){const s=state.general_settings||{xmltv_sync_mode:'interval',xmltv_sync_minutes:60,xmltv_daily_time:'04:15',emitter_refresh_minutes:180,emitter_retry_minutes:5,detect_cache_updates:true};modal(`<div class="guide-head"><div><h2>Configurações gerais</h2><div class="muted">Sincronização das fontes e atualização dos emissores</div></div><button onclick="closeModal()">Fechar</button></div><div class="form-grid" style="margin-top:18px"><label>Modo de atualização das fontes<select id="gXmltvMode" onchange="generalSyncModeChanged()"><option value="interval" ${s.xmltv_sync_mode==='interval'?'selected':''}>Por intervalo</option><option value="daily" ${s.xmltv_sync_mode==='daily'?'selected':''}>Diariamente em horário definido</option></select></label><label id="gIntervalField">Intervalo XMLTV (minutos)<input id="gXmltvSync" type="number" min="5" max="10080" value="${s.xmltv_sync_minutes}"></label><label id="gDailyField">Horário diário — America/Sao_Paulo<input id="gXmltvDaily" type="time" value="${esc(s.xmltv_daily_time||'04:15')}"></label><label>Recarga pelo emissor (minutos)<input id="gEmitterRefresh" type="number" min="1" max="10080" value="${s.emitter_refresh_minutes}"></label><label>Nova tentativa após falha (minutos)<input id="gEmitterRetry" type="number" min="1" max="1440" value="${s.emitter_retry_minutes}"></label><label class="wide"><span>Detecção imediata do cache</span><select id="gDetectCache"><option value="1" ${s.detect_cache_updates?'selected':''}>Ativada — hot reload sem interromper o multicast</option><option value="0" ${!s.detect_cache_updates?'selected':''}>Desativada — respeitar o intervalo do emissor</option></select></label></div><p class="muted">No modo diário, a fonte é sincronizada uma vez por dia no horário escolhido. Se o servidor estava desligado nesse horário, a sincronização pendente acontece após iniciar. Mudanças reais na grade são carregadas a quente, mantendo o mesmo processo e o envio multicast. Ao salvar estas configurações, os emissores ativos reiniciam uma vez para receber os novos parâmetros.</p><div class="modal-actions"><button onclick="closeModal()">Cancelar</button><button class="primary" onclick="saveGeneralSettings()">Salvar e aplicar</button></div>`);generalSyncModeChanged()}
function generalSyncModeChanged(){const daily=el('gXmltvMode')?.value==='daily';if(el('gIntervalField'))el('gIntervalField').style.display=daily?'none':'';if(el('gDailyField'))el('gDailyField').style.display=daily?'':'none'}
async function saveGeneralSettings(){if(!confirm('Salvar a programação e reiniciar os emissores ativos agora?'))return;try{const result=await api('/api/settings',{method:'POST',body:JSON.stringify({xmltv_sync_mode:el('gXmltvMode').value,xmltv_sync_minutes:+el('gXmltvSync').value,xmltv_daily_time:el('gXmltvDaily').value,emitter_refresh_minutes:+el('gEmitterRefresh').value,emitter_retry_minutes:+el('gEmitterRetry').value,detect_cache_updates:el('gDetectCache').value==='1'})});state.general_settings=result.settings;closeModal();toast(`Configurações aplicadas; ${result.restarted||0} emissor(es) reiniciado(s)`);setTimeout(refresh,600)}catch(e){toast(e.message,true)}}
const openGeneralSettingsWithLicenseServers=openGeneralSettings;
openGeneralSettings=function(){openGeneralSettingsWithLicenseServers();const grid=document.querySelector('.modal .form-grid'),s=state.general_settings||{};if(grid)grid.insertAdjacentHTML('beforeend',`<label class="wide">Servidor principal de licenças<input id="gLicensePrimary" type="url" value="${esc(s.license_primary_url||'')}" placeholder="http://servidor-principal:9200"></label><label class="wide">Servidor redundante de licenças<input id="gLicenseSecondary" type="url" value="${esc(s.license_secondary_url||'')}" placeholder="http://servidor-redundante:9200"></label>`)};
const saveGeneralSettingsWithLicenseServers=saveGeneralSettings;
saveGeneralSettings=async function(){if(!el('gLicensePrimary')?.value.trim()){toast('Informe o servidor principal de licenças',true);return}if(!confirm('Salvar a programação, os servidores de licença e reiniciar os emissores ativos agora?'))return;try{const result=await api('/api/settings',{method:'POST',body:JSON.stringify({xmltv_sync_mode:el('gXmltvMode').value,xmltv_sync_minutes:+el('gXmltvSync').value,xmltv_daily_time:el('gXmltvDaily').value,emitter_refresh_minutes:+el('gEmitterRefresh').value,emitter_retry_minutes:+el('gEmitterRetry').value,detect_cache_updates:el('gDetectCache').value==='1',license_primary_url:el('gLicensePrimary').value.trim(),license_secondary_url:el('gLicenseSecondary').value.trim()})});state.general_settings=result.settings;closeModal();toast(`Configurações aplicadas; ${result.restarted||0} emissor(es) reiniciado(s)`);setTimeout(refresh,600)}catch(e){toast(e.message,true)}};
async function openGuide(carrierId,serviceId){try{const g=await api(`/api/guide?carrier_id=${encodeURIComponent(carrierId)}`),s=g.services.find(x=>x.id===serviceId);modal(`<div class="guide-head"><div><h2>${esc(s.name)}</h2><div class="muted">SID ${s.service_id} · ${esc(s.epg_channel_id)} · ${esc(g.timezone)}</div></div><button onclick="closeModal()">Fechar</button></div>${s.current?`<div class="card" style="padding:16px;margin-top:15px"><small>NO AR AGORA</small><h3>${esc(s.current.title)}</h3><p>${esc(s.current.description)}</p><b>${fmt(s.current.start)} — ${fmt(s.current.stop)}</b><div class="progress"><i style="width:${s.current.progress}%"></i></div></div>`:'<p class="muted">Nenhum programa identificado no ar.</p>'}<div class="guide-list">${s.schedule.map(p=>`<div class="guide-item ${p===s.current?'current':''}"><b>${fmt(p.start)}<br><span class="muted">${fmt(p.stop)}</span></b><div><strong>${esc(p.title)}</strong><div class="muted">${esc(p.category||p.description||'')}</div></div></div>`).join('')||'<p>Sem grade para hoje.</p>'}</div>`)}catch(e){toast(e.message,true)}}
async function openLogs(id){try{const j=await api(`/api/logs?carrier_id=${encodeURIComponent(id)}`);modal(`<h2>Logs do emissor</h2><pre style="background:#071b33;color:#dff4ff;padding:16px;border-radius:10px;max-height:65vh;overflow:auto;white-space:pre-wrap">${esc(j.log||'Sem logs.')}</pre><div class="modal-actions"><button onclick="closeModal()">Fechar</button></div>`)}catch(e){toast(e.message,true)}}
function openTvSimulator(){const active=(state.carriers||[]).filter(c=>c.active);modal(`<div class="guide-head"><div><h2>Simulador de TV ISDB-TB</h2><div class="muted">Analisa exatamente os datagramas gerados pelo EPG Server antes do envio multicast.</div></div><button onclick="closeModal()">Fechar</button></div><div class="card" style="padding:18px;margin-top:16px"><label>Portadora ativa<select id="tvCarrier">${active.map(c=>`<option value="${esc(c.id)}">${esc(c.name)} · ${esc(c.destination)}:${c.port}</option>`).join('')}</select></label><p class="muted">O teste captura quatro segundos sem interromper a transmissão e reconstrói os metadados como um receptor ISDB-TB.</p></div><div class="modal-actions"><button onclick="closeModal()">Cancelar</button><button class="primary" onclick="runTvSimulator()" ${active.length?'':'disabled'}>${active.length?'Capturar e analisar':'Nenhuma portadora ativa'}</button></div>`)}
async function runTvSimulator(){const id=el('tvCarrier')?.value;if(!id)return;toast('Capturando o transporte gerado…');try{const r=await api('/api/carriers/audit',{method:'POST',body:JSON.stringify({id,seconds:8})});showTvSimulatorReport(r)}catch(e){toast(e.message,true)}}
function showTvSimulatorReport(r){const events=r.eit_present_following_events||[],pids=Object.entries(r.pid_packets||{}),continuity=Object.values(r.continuity_errors||{}).reduce((a,b)=>a+b,0);modal(`<div class="guide-head"><div><h2>Simulador de TV ISDB-TB</h2><div class="muted">${esc(r.carrier.name)} · ${esc(r.carrier.destination)}:${r.carrier.port} · TSID ${r.carrier.transport_stream_id} · ONID ${r.carrier.original_network_id}</div></div><button onclick="closeModal()">Fechar</button></div><div class="card" style="padding:16px;margin-top:14px;border-color:${r.ok?'#8ce2bd':'#f1a4aa'}"><b>${r.ok?'TRANSPORTE VÁLIDO':'FALHAS ENCONTRADAS'}</b><div class="muted">${r.packet_count} pacotes · CRC ${r.crc_errors} erro(s) · continuidade ${continuity} erro(s) · sinopses repetidas ${r.repeated_synopsis_prefixes}</div>${(r.errors||[]).map(e=>`<div class="error-text">${esc(e)}</div>`).join('')}</div><h3>PIDs recebidos</h3><div class="table-wrap"><table class="carrier-table" style="min-width:0"><thead><tr><th>PID</th><th>Pacotes</th><th>Interpretação</th></tr></thead><tbody>${pids.map(([pid,count])=>`<tr><td><b>${esc(pid)}</b></td><td>${count}</td><td>${pid==='0x0012'?'EIT — programação':pid==='0x0014'?'TDT/TOT — relógio':pid==='0x0011'?'SDT — serviços':pid==='0x0000'?'PAT':pid==='0x1FFF'?'Preenchimento':'PMT/sinalização'}</td></tr>`).join('')}</tbody></table></div><h3>Como a TV recebe os eventos</h3><div class="guide-list">${events.map(e=>`<div class="guide-item"><div><b>SID ${e.service_id}</b><br><span class="muted">Seção ${e.section_number} · evento ${e.event_id}</span></div><div><strong>${esc(e.title||'Sem título')}</strong><div><small>0x4D:</small> ${esc(e.short_text_0x4d||'—')}</div><div><small>0x4E:</small> ${esc(e.extended_text_0x4e||'—')}</div><div style="margin-top:6px"><b>Texto reconstruído pela TV:</b> ${esc(e.tv_text||'—')}</div><div class="muted">Descritores ${esc((e.descriptor_tags||[]).join(', '))} · categorias ${esc((e.content_categories_0x54||[]).join(', ')||'não informada')}</div></div></div>`).join('')||'<div class="empty">Nenhum evento presente/próximo encontrado.</div>'}</div><h3>Passthrough mínimo esperado</h3><pre>${esc((r.required_passthrough||[]).join('\n'))}</pre><div class="modal-actions"><button onclick="closeModal()">Fechar</button></div>`,'publication-modal')}
function openAbout(){const admin=session.user?.role==='admin';modal(`<div class="guide-head"><div><h2>Sobre</h2><div class="muted">Informações do produto e atualizações</div></div><button onclick="closeModal()">Fechar</button></div><div class="card" style="padding:22px;margin-top:16px;text-align:center"><img src="/assets/omniepg_logotipo_escuro.svg" alt="OMNIEPG" style="width:min(360px,90%);height:auto"><p><b>Versão:</b> ${esc(state.version||'1.23.0')}</p><p><b>Developed by Julio Cortijo</b></p><div id="updateInfo" class="muted">${admin?'Consulte o repositório oficial para verificar uma nova versão.':'Somente administradores podem gerenciar atualizações.'}</div></div><div class="modal-actions"><button onclick="closeModal()">Fechar</button>${admin?'<button class="primary" onclick="checkUpdate()">Verificar atualização</button>':''}</div>`) }
async function checkUpdate(){const info=el('updateInfo');if(info)info.textContent='Consultando a release oficial…';try{const u=await api('/api/update');const mode=u.install_mode==='native'?'Pacote nativo':'Docker';const last=u.last_update?.message?`<p class="muted">Última tentativa: ${esc(u.last_update.message)}</p>`:'';const action=u.update_available&&u.asset_available&&u.install_mode==='native'?`<button class="primary" onclick="applyUpdate('${esc(u.tag)}')">Atualizar agora para ${esc(u.latest_version)}</button>`:'';const docker=u.install_mode!=='native'&&u.update_available?'<p class="muted">Esta instalação usa Docker. Atualize a imagem pelo host para preservar volumes e rollback.</p>':'';info.innerHTML=`<p><b>Instalação:</b> ${mode}</p><p><b>Versão instalada:</b> ${esc(u.current_version)}</p><p><b>Última release:</b> ${esc(u.latest_version)}</p><p>${u.update_available?'Existe uma atualização disponível.':'O sistema está atualizado.'}</p>${docker}${last}${action}`}catch(e){if(info)info.innerHTML=`<span class="error-text">${esc(e.message)}</span>`}}
async function applyUpdate(tag){if(!confirm(`Atualizar o OMNIEPG para ${tag}? Os serviços serão reiniciados durante a instalação.`))return;try{const r=await api('/api/update/apply',{method:'POST',body:JSON.stringify({tag})});toast(r.message||'Atualização solicitada');const info=el('updateInfo');if(info)info.innerHTML='<b>Atualização agendada.</b><p class="muted">O serviço será reiniciado após validar e instalar o pacote. Reabra o painel em alguns instantes.</p>'}catch(e){toast(e.message,true)}}
function openLicense(){const license=state.license||{};modal(`<div class="guide-head"><div><h2>Licença do OMNIEPG</h2><div class="muted">${license.valid?`${license.channel_count}/${license.max_channels} canais utilizados`:`Bloqueada · ${esc(license.reason||'Licença inválida')}`}</div></div><button onclick="closeModal()">Fechar</button></div><div class="card" style="padding:18px;margin-top:16px"><p>Cole abaixo a chave gerada no EPG License Server. Ela será validada antes de substituir a chave atual.</p><label>Chave da licença<input id="epgLicenseKey" type="text" autocomplete="off" spellcheck="false" placeholder="EPG-..."></label><p class="muted">A chave instalada não é exibida pelo painel e não aparece nos logs ou na API.</p></div><div class="modal-actions"><button onclick="closeModal()">Cancelar</button><button class="primary" onclick="saveLicenseKey()">Validar e salvar</button></div>`);el('epgLicenseKey').focus()}
async function saveLicenseKey(){try{const key=el('epgLicenseKey').value.trim();const result=await api('/api/license/key',{method:'POST',body:JSON.stringify({key})});state.license=result.license;closeModal();toast('Chave validada e instalada');refresh()}catch(e){toast(e.message,true)}}
async function openUsers(){try{users=(await api('/api/users')).users;modal(`<div class="guide-head"><div><h2>Usuários do sistema</h2><div class="muted">Administradores gerenciam acessos; operadores trabalham com fontes e portadoras.</div></div><button class="primary" onclick="editUser()">+ Novo usuário</button></div><div class="guide-list">${users.map(u=>`<div class="guide-item"><div><span class="badge ${u.enabled?'running':'error'}">${u.enabled?'Ativo':'Desativado'}</span></div><div><strong>${esc(u.display_name)}</strong><div class="muted">${esc(u.username)} · ${u.role==='admin'?'Administrador':'Operador'}</div><div class="actions" style="margin-top:8px"><button onclick="editUser('${esc(u.id)}')">Editar / senha</button>${u.id!==session.user?.id?`<button class="danger" onclick="deleteUser('${esc(u.id)}')">Excluir</button>`:''}</div></div></div>`).join('')}</div><div class="modal-actions"><button onclick="closeModal()">Fechar</button></div>`) }catch(e){toast(e.message,true)}}
function editUser(id=''){const u=users.find(item=>item.id===id)||{role:'operator',enabled:true};modal(`<h2>${id?'Editar usuário':'Novo usuário'}</h2><div class="form-grid"><input id="userId" type="hidden" value="${esc(u.id||'')}"><label>Usuário<input id="userName" maxlength="32" autocomplete="off" value="${esc(u.username||'')}"></label><label>Nome de exibição<input id="userDisplay" maxlength="80" value="${esc(u.display_name||'')}"></label><label>Perfil<select id="userRole"><option value="operator" ${u.role==='operator'?'selected':''}>Operador</option><option value="admin" ${u.role==='admin'?'selected':''}>Administrador</option></select></label><label>Status<select id="userEnabled"><option value="1" ${u.enabled?'selected':''}>Ativo</option><option value="0" ${!u.enabled?'selected':''}>Desativado</option></select></label><label class="wide">${id?'Nova senha (deixe vazio para manter)':'Senha'}<input id="userPassword" type="password" minlength="10" autocomplete="new-password" placeholder="Mínimo de 10 caracteres"></label></div><p class="muted">A senha nunca é exibida ou armazenada em texto legível.</p><div class="modal-actions"><button onclick="closeModal()">Cancelar</button><button class="primary" onclick="saveUser()">Salvar usuário</button></div>`)}
async function saveUser(){try{const id=el('userId').value,password=el('userPassword').value;await api('/api/users',{method:'POST',body:JSON.stringify({id,username:el('userName').value,display_name:el('userDisplay').value,role:el('userRole').value,enabled:el('userEnabled').value==='1',password})});if(id===session.user?.id&&(password||el('userName').value!==session.user.username||el('userEnabled').value!=='1')){alert('Seu acesso foi alterado. O navegador solicitará as novas credenciais.');location.reload();return}closeModal();toast('Usuário salvo');refresh()}catch(e){toast(e.message,true)}}
async function deleteUser(id){if(!confirm('Excluir este usuário?'))return;try{await api('/api/users/delete',{method:'POST',body:JSON.stringify({id})});toast('Usuário excluído');openUsers()}catch(e){toast(e.message,true)}}
const publicationDate=t=>t?new Date(t*1000).toLocaleString('pt-BR'):'—';
const publicationStatus={selected:'Em entrega',future:'Futura',expired:'Expirada',available:'Disponível'};
async function openPublications(){try{publications=(await api('/api/publications')).publications;modal(`<div class="guide-head"><div><h2>Publicações XMLTV</h2><div class="muted">Converta arquivos da programadora e mantenha uma URL permanente para todas as vigências.</div></div><div class="actions"><button class="primary" onclick="editPublication()">+ Nova publicação</button><button onclick="closeModal()">Fechar</button></div></div>${publications.length?publications.map(publicationCard).join(''):'<div class="empty">Nenhuma publicação criada.</div>'}`,'publication-modal')}catch(e){toast(e.message,true)}}
function publicationCard(p){const url=p.public_url||location.origin+p.public_path;return `<div class="publication-card"><div class="publication-title"><div><h3 style="margin:0">${esc(p.name)}</h3><div class="muted">${p.versions.length} arquivo(s) · URL permanente</div></div><div class="actions"><button onclick="editPublication('${esc(p.id)}')">Renomear</button><label class="upload-label">Enviar XMLTV<input type="file" accept=".xml,.xmltv,.gz,application/xml,text/xml,application/gzip" onchange="uploadPublication('${esc(p.id)}',this)"></label><button class="danger" onclick="deletePublication('${esc(p.id)}')">Excluir</button></div></div><div class="publication-url"><input id="publicationUrl-${esc(p.id)}" readonly value="${esc(url)}"><button onclick="copyPublicationUrl('${esc(p.id)}')">Copiar URL</button></div><div class="table-wrap"><table class="version-table"><thead><tr><th>Situação</th><th>Arquivo</th><th>Válido de</th><th>Válido até</th><th>Conteúdo</th><th>Normalização</th><th></th></tr></thead><tbody>${p.versions.length?p.versions.map(v=>publicationVersionRow(p,v)).join(''):'<tr><td colspan="7" class="muted">Envie o primeiro XMLTV para ativar esta URL.</td></tr>'}</tbody></table></div></div>`}
function publicationVersionRow(p,v){const corrections=(v.timezone_added||0)+(v.channel_refs_rewritten||0)+(v.channels_synthesized||0)+(v.duplicate_channels_removed||0)+(v.invalid_programmes_removed||0);return `<tr><td><span class="badge ${v.status==='selected'?'running':v.status==='expired'?'error':''}">${esc(publicationStatus[v.status]||v.status)}</span></td><td><b>${esc(v.filename)}</b><div class="muted">${(v.bytes/1024/1024).toFixed(1)} MiB</div></td><td>${publicationDate(v.valid_from)}</td><td>${publicationDate(v.valid_until)}</td><td>${v.channels} canais<br>${v.programmes} programas</td><td>${corrections.toLocaleString('pt-BR')} ajustes<div class="muted">${v.channel_refs_rewritten||0} IDs · ${v.invalid_programmes_removed||0} descartados</div></td><td><button class="danger" onclick="deletePublicationVersion('${esc(p.id)}','${esc(v.id)}')">Excluir</button></td></tr>`}
function editPublication(id=''){const p=publications.find(item=>item.id===id)||{};modal(`<h2>${id?'Renomear':'Nova'} publicação XMLTV</h2><input id="publicationId" type="hidden" value="${esc(p.id||'')}"><label>Nome da publicação<input id="publicationName" maxlength="100" value="${esc(p.name||'')}" placeholder="Ex.: Grade semanal da programadora"></label><div class="muted" style="margin-top:10px">A URL é criada uma única vez e não muda nos próximos uploads.</div><div class="modal-actions"><button onclick="openPublications()">Cancelar</button><button class="primary" onclick="savePublication()">Salvar</button></div>`)}
async function savePublication(){try{await api('/api/publications',{method:'POST',body:JSON.stringify({id:el('publicationId').value,name:el('publicationName').value})});toast('Publicação salva');openPublications()}catch(e){toast(e.message,true)}}
async function uploadPublication(id,input){const file=input.files?.[0];if(!file)return;if(file.size>96*1024*1024){toast('O XMLTV deve possuir no máximo 96 MiB',true);return}toast('Enviando e normalizando o XMLTV…');try{const response=await fetch(`/api/publications/upload?id=${encodeURIComponent(id)}&filename=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':file.type||'application/xml'},body:file});const result=await response.json().catch(()=>({error:'Resposta inválida'}));if(!response.ok||result.error)throw Error(result.error||`HTTP ${response.status}`);toast(`${result.version.programmes.toLocaleString('pt-BR')} programas publicados`);openPublications()}catch(e){toast(e.message,true)}}
async function copyPublicationUrl(id){const input=el(`publicationUrl-${id}`);try{await navigator.clipboard.writeText(input.value);toast('URL permanente copiada')}catch(e){input.select();document.execCommand('copy');toast('URL permanente copiada')}}
async function deletePublicationVersion(publicationId,versionId){if(!confirm('Excluir esta versão do XMLTV? A URL poderá selecionar outro arquivo.'))return;try{await api('/api/publications/version/delete',{method:'POST',body:JSON.stringify({publication_id:publicationId,version_id:versionId})});toast('Versão excluída');openPublications()}catch(e){toast(e.message,true)}}
async function deletePublication(id){if(!confirm('Excluir a publicação, todos os arquivos e sua URL permanente?'))return;try{await api('/api/publications/delete',{method:'POST',body:JSON.stringify({id})});toast('Publicação excluída');openPublications()}catch(e){toast(e.message,true)}}
function sourceSyncSummary(s){const x=s.sync_status;if(!x)return '<div class="muted" style="margin-top:7px">Ainda não sincronizada nesta instalação.</div>';const last=new Date(x.fetched_at*1000).toLocaleString('pt-BR'),next=new Date(x.next_refresh_at*1000).toLocaleString('pt-BR');return `<div class="muted" style="margin-top:7px"><b>${x.channel_count.toLocaleString('pt-BR')}</b> canais · <b>${x.programme_count.toLocaleString('pt-BR')}</b> programas · Atualizada: <b>${last}</b> · Próxima: <b>${next}</b></div>`}
function sourceUsage(sourceId){const rows=[];for(const carrier of state.carriers||[])for(const service of carrier.services||[]){const inherited=!service.source_id,effectiveSource=service.source_id||carrier.source_id;if(effectiveSource===sourceId)rows.push({carrier,service,inherited})}return rows}
function openSourceUsage(sourceId){const source=sources.find(item=>item.id===sourceId),usage=sourceUsage(sourceId);if(!source)return;modal(`<div class="guide-head"><div><h2>Canais alimentados por ${esc(source.name)}</h2><div class="muted">${usage.length} canal(is) usam esta fonte XMLTV na configuração atual.</div></div><div class="actions"><button onclick="openSources()">Voltar</button><button onclick="closeModal()">Fechar</button></div></div>${usage.length?`<div class="table-wrap" style="margin-top:15px"><table class="version-table"><thead><tr><th>Canal</th><th>ID XMLTV</th><th>Portadora</th><th>Associação</th><th>Identificação</th><th>Destino EPG</th></tr></thead><tbody>${usage.map(({carrier,service,inherited})=>`<tr><td><b>${esc(service.name||'Canal sem nome')}</b><div class="muted">SID ${service.service_id}</div></td><td><code>${esc(service.epg_channel_id||'não informado')}</code></td><td><b>${esc(carrier.name)}</b></td><td><span class="badge ${inherited?'':'running'}">${inherited?'Herdada da portadora':'Direta no canal'}</span></td><td>TSID ${carrier.transport_stream_id}<br>ONID ${carrier.original_network_id}</td><td><code>${esc(carrier.destination)}:${carrier.port}</code></td></tr>`).join('')}</tbody></table></div>`:'<div class="card empty"><h3>Nenhum canal vinculado</h3><p>Esta fonte está cadastrada, mas não alimenta nenhum canal na configuração atual.</p></div>'}`,'publication-modal')}
function openSources(){modal(`<div class="guide-head"><h2>Fontes XMLTV</h2><div class="actions"><button onclick="openSourceHistory()">Histórico de sincronização</button><button onclick="editSource()">+ Nova fonte</button><button onclick="closeModal()">Fechar</button></div></div><div class="guide-list">${sources.map(s=>`<div class="guide-item"><div><b>${s.source_type==='parse_xml'?'PARSE-XML':s.is_default?'PADRÃO':'XMLTV'}</b></div><div><strong>${esc(s.name)}</strong><div class="muted">${esc(s.url)}</div>${sourceSyncSummary(s)}<div class="source-usage-summary"><b>${sourceUsage(s.id).length}</b> canal(is) alimentado(s) por esta fonte</div><div class="actions" style="margin-top:8px"><button class="primary" onclick="openSourceCatalog('${esc(s.id)}')">Ver canais e programação</button><button onclick="openSourceUsage('${esc(s.id)}')">Ver canais alimentados</button><button onclick="openSourceHistory('${esc(s.id)}')">Histórico</button><button onclick="editSource('${esc(s.id)}')">Editar</button><button onclick="testSource('${esc(s.id)}')">Sincronizar</button>${sources.length>1?`<button class="danger" onclick="deleteSource('${esc(s.id)}')">Excluir</button>`:''}</div></div></div>`).join('')}</div>`)}
async function openSourceHistory(sourceId=''){try{const events=(await api(`/api/sources/history${sourceId?`?source_id=${encodeURIComponent(sourceId)}`:''}`)).events;modal(`<div class="guide-head"><div><h2>Histórico de sincronização XMLTV</h2><div class="muted">Cada download, validação e mudança detectada nas fontes.</div></div><div class="actions"><button onclick="openSources()">Voltar</button><button onclick="closeModal()">Fechar</button></div></div><div class="table-wrap" style="margin-top:15px"><table class="version-table"><thead><tr><th>Resultado</th><th>Fonte</th><th>Atualização</th><th>Conteúdo</th><th>Versão XMLTV</th><th>Detalhes</th></tr></thead><tbody>${events.length?events.map(e=>`<tr><td><span class="badge ${e.status==='success'?'running':'error'}">${e.status==='success'?'Sucesso':'Erro'}</span></td><td><b>${esc(e.source_name||e.source_id)}</b><div class="muted"><code>${esc(e.source_id)}</code></div></td><td>${publicationDate(e.finished_at)}<div class="muted">${(e.duration_ms||0).toLocaleString('pt-BR')} ms</div></td><td>${e.status==='success'?`${(e.channels||0).toLocaleString('pt-BR')} canais<br>${(e.programmes||0).toLocaleString('pt-BR')} programas<br>${((e.bytes||0)/1024/1024).toFixed(1)} MiB`:'—'}</td><td>${e.status==='success'?`<b>${e.changed?'Nova versão':'Sem alteração'}</b><div class="muted"><code>${esc((e.sha256||'').slice(0,16))}…</code></div>`:'—'}</td><td class="${e.status==='error'?'error-text':''}">${esc(e.error||'XMLTV baixado e validado corretamente')}</td></tr>`).join(''):'<tr><td colspan="6" class="empty">Nenhuma sincronização registrada desde esta atualização.</td></tr>'}</tbody></table></div>`,'publication-modal')}catch(e){toast(e.message,true)}}
function editSource(id=''){const s=sources.find(x=>x.id===id)||{};modal(`<h2>${id?'Editar':'Nova'} fonte XMLTV</h2><div class="form-grid"><input id="srcId" type="hidden" value="${esc(s.id||'')}"><label class="wide">Nome<input id="srcName" value="${esc(s.name||'')}"></label><label>Tipo de entrada<select id="srcType" onchange="sourceTypeChanged()"><option value="xmltv" ${(s.source_type||'xmltv')==='xmltv'?'selected':''}>XMLTV padrão</option><option value="parse_xml" ${s.source_type==='parse_xml'?'selected':''}>Parse-XML (normalizar provedor)</option></select></label><label><span>Fonte padrão</span><select id="srcDefault"><option value="0">Não</option><option value="1" ${s.is_default?'selected':''}>Sim</option></select></label><label class="wide">URL HTTP/HTTPS<input id="srcUrl" value="${esc(s.url||'')}"></label><div id="srcTypeHelp" class="muted wide"></div></div><div class="modal-actions"><button onclick="openSources()">Voltar</button><button onclick="testSourceForm()">Testar</button><button class="primary" onclick="saveSource()">Salvar</button></div>`);sourceTypeChanged()}
function sourceTypeChanged(){const help=el('srcTypeHelp');if(help)help.textContent=el('srcType').value==='parse_xml'?'Cria canais ausentes, aplica UTC-03:00, remove eventos inválidos e entrega uma URL interna estável ao emissor.':'Usa o XMLTV original sem transformação estrutural.'}
async function saveSource(){try{await api('/api/sources',{method:'POST',body:JSON.stringify({id:el('srcId').value,name:el('srcName').value,url:el('srcUrl').value,source_type:el('srcType').value,is_default:el('srcDefault').value==='1'})});await refresh();openSources();toast('Fonte salva')}catch(e){toast(e.message,true)}}
function sourceTestMessage(r){const n=r.normalization;if(!n)return `${r.channels} canais e ${r.programmes} programas encontrados`;return `${r.channels} canais e ${r.programmes} programas na janela · ${n.channels_synthesized||0} canais criados · ${n.invalid_programmes_removed||0} eventos inválidos removidos`}
function syncingSource(name='fonte XMLTV'){modal(`<div class="sync-state"><div class="sync-spinner"></div><div><h2>Sincronizando canais…</h2><p class="muted">Baixando e processando ${esc(name)}. Arquivos grandes podem levar alguns segundos.</p></div></div>`,'source-catalog-modal')}
function parseReport(n){if(!n)return '<div class="parse-report"><h3>XMLTV padrão</h3><div class="muted">O link foi lido no formato original; nenhuma transformação estrutural foi aplicada.</div></div>';return `<div class="parse-report"><h3>Ajustes aplicados pelo Parse-XML</h3><p>O arquivo do provedor foi convertido para o padrão aceito pelo EPG Stream antes de ser disponibilizado aos emissores.</p><div class="parse-grid"><div><b>${n.channels_synthesized||0}</b><br><small>canais criados</small></div><div><b>${n.timezone_added||0}</b><br><small>horários com UTC−03</small></div><div><b>${n.channel_refs_rewritten||0}</b><br><small>IDs ajustados</small></div><div><b>${n.duplicate_channels_removed||0}</b><br><small>duplicados removidos</small></div><div><b>${n.invalid_programmes_removed||0}</b><br><small>programas inválidos removidos</small></div></div><p class="muted">A cópia normalizada usa URL interna estável e só substitui a anterior depois de validada. Se a origem falhar, a última cópia válida permanece em uso.</p></div>`}
function parseErrors(n){const errors=n?.invalid_programmes||[];if(!errors.length)return '';return `<details class="parse-errors"><summary>Consultar ${errors.length} programa(s) inválido(s)${n.invalid_programmes_truncated?` · mais ${n.invalid_programmes_truncated} omitidos pelo limite de segurança`:''}</summary><input class="catalog-search" placeholder="Buscar por canal, programa ou motivo" oninput="filterParseErrors(this.value)"><div class="table-wrap"><table class="parse-error-table"><thead><tr><th>Canal</th><th>Programa</th><th>Início/fim recebidos</th><th>Motivo</th></tr></thead><tbody>${errors.map(e=>`<tr class="parse-error-row" data-search="${esc(`${e.channel_id} ${e.title} ${e.reason}`.toLowerCase())}"><td><code>${esc(e.channel_id||'não informado')}</code></td><td>${esc(e.title)}</td><td><code>${esc(e.start||'—')}</code><br><code>${esc(e.stop||'—')}</code></td><td class="error-text">${esc(e.reason)}</td></tr>`).join('')}</tbody></table></div></details>`}
function filterParseErrors(value){const term=value.trim().toLocaleLowerCase('pt-BR');document.querySelectorAll('.parse-error-row').forEach(row=>row.hidden=!!term&&!row.dataset.search.includes(term))}
function sourceScheduleRows(items){return (items||[]).map(p=>`<div><time>${new Date(p.start*1000).toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}</time><b>${esc(p.title||'Sem título')}</b>${p.category?` <span class="badge">${esc(p.category)}</span>`:''}<div class="muted">${esc(p.description||'Sem sinopse')}</div></div>`).join('')||'<div class="muted">Nenhuma programação futura disponível nesta janela.</div>'}
function toggleSourceSchedule(index){const row=el(`sourceSchedule-${index}`);if(row)row.hidden=!row.hidden}
function filterSourceChannels(value){const term=value.trim().toLocaleLowerCase('pt-BR');document.querySelectorAll('.catalog-channel').forEach(row=>{const hidden=!!term&&!row.dataset.search.includes(term);row.hidden=hidden;if(row.nextElementSibling)row.nextElementSibling.hidden=true})}
function renderSourceCatalog(source,data){const last=new Date(data.fetched_at*1000).toLocaleString('pt-BR'),next=new Date(data.next_refresh_at*1000).toLocaleString('pt-BR');modal(`<div class="guide-head"><div><h2>${esc(source.name)}</h2><div class="muted">Resumo da última cópia XMLTV validada</div></div><div class="actions"><button onclick="openSourceCatalog('${esc(source.id)}',true)">Sincronizar novamente</button><button onclick="openSources()">Voltar</button><button onclick="closeModal()">Fechar</button></div></div><div class="catalog-metrics"><div class="catalog-metric"><small class="muted">CANAIS SINCRONIZADOS</small><b>${data.channel_count.toLocaleString('pt-BR')}</b></div><div class="catalog-metric"><small class="muted">PROGRAMAS SINCRONIZADOS</small><b>${data.programme_count.toLocaleString('pt-BR')}</b></div><div class="catalog-metric"><small class="muted">ÚLTIMA ATUALIZAÇÃO</small><b>${last}</b></div><div class="catalog-metric"><small class="muted">PRÓXIMA ATUALIZAÇÃO</small><b>${next}</b></div></div><p class="muted">A sincronização de todas as fontes roda automaticamente em segundo plano a cada 60 minutos. O botão Sincronizar novamente atualiza esta fonte imediatamente.</p>${parseReport(data.normalization)}${parseErrors(data.normalization)}<input class="catalog-search" placeholder="Buscar canal por nome ou ID XMLTV" oninput="filterSourceChannels(this.value)"><div class="table-wrap"><table class="source-channel-table"><thead><tr><th>Canal disponível</th><th>ID XMLTV</th><th>Programas</th><th>No ar agora</th><th></th></tr></thead><tbody>${data.channels.map((c,i)=>`<tr class="catalog-channel" data-search="${esc(`${c.name} ${c.id}`.toLowerCase())}"><td><b>${esc(c.name)}</b></td><td><code>${esc(c.id)}</code></td><td>${c.programme_count}</td><td>${c.current?`<b>${esc(c.current.title)}</b><div class="muted">${fmt(c.current.start)}–${fmt(c.current.stop)}</div>`:'Sem programa no ar'}</td><td><button onclick="toggleSourceSchedule(${i})">Ver programação</button></td></tr><tr id="sourceSchedule-${i}" hidden><td colspan="5"><div class="source-schedule">${sourceScheduleRows(c.schedule)}</div></td></tr>`).join('')}</tbody></table></div>`,'source-catalog-modal')}
async function openSourceCatalog(id,force=false){const s=sources.find(x=>x.id===id);if(!s)return;syncingSource(s.name);try{const data=await api(`/api/catalog?source_id=${encodeURIComponent(id)}&force=${force?1:0}`);s.sync_status={channel_count:data.channel_count,programme_count:data.programme_count,fetched_at:data.fetched_at,next_refresh_at:data.next_refresh_at};renderSourceCatalog(s,data)}catch(e){modal(`<div class="sync-state"><div><h2 class="error-text">Falha ao sincronizar</h2><p>${esc(e.message)}</p><div class="actions"><button onclick="openSources()">Voltar</button><button class="primary" onclick="openSourceCatalog('${esc(id)}',true)">Tentar novamente</button></div></div></div>`,'source-catalog-modal')}}
async function testSourceForm(){const request={id:el('srcId').value,name:el('srcName').value,url:el('srcUrl').value,source_type:el('srcType').value};syncingSource(request.name||'fonte XMLTV');try{const r=await api('/api/sources/test',{method:'POST',body:JSON.stringify(request)});modal(`<div class="guide-head"><h2>Sincronização concluída</h2><button onclick="closeModal()">Fechar</button></div>${parseReport(r.normalization)}${parseErrors(r.normalization)}<div class="card" style="padding:18px"><b>${esc(sourceTestMessage(r))}</b></div><div class="modal-actions"><button onclick="closeModal()">Fechar</button></div>`)}catch(e){toast(e.message,true);editSource(request.id)}}
async function testSource(id){await openSourceCatalog(id,true)}
function deleteSource(id){const source=sources.find(item=>item.id===id),alternatives=sources.filter(item=>item.id!==id),usage=sourceUsage(id);if(!source||!alternatives.length){toast('O sistema precisa manter ao menos uma fonte XMLTV',true);return}modal(`<h2>Excluir fonte XMLTV</h2><div class="card" style="padding:18px"><p>Você está excluindo <b>${esc(source.name)}</b>.</p><p><b>${usage.length} canal(is)</b> usam efetivamente esta fonte. Referências de portadoras e canais serão migradas para a substituta.</p><label>Fonte substituta<select id="sourceReplacement">${alternatives.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('')}</select></label><p class="muted">Somente portadoras ativas afetadas serão reiniciadas após a alteração.</p></div><div class="modal-actions"><button onclick="openSources()">Cancelar</button><button class="danger" onclick="confirmDeleteSource('${esc(id)}')">Migrar e excluir</button></div>`)}
async function confirmDeleteSource(id){if(!confirm('Confirmar a migração das referências e a exclusão desta fonte?'))return;try{const result=await api('/api/sources/delete',{method:'POST',body:JSON.stringify({id,replacement_id:el('sourceReplacement').value})});await refresh();openSources();toast(`Fonte excluída; ${result.restarted?.length||0} portadora(s) reiniciada(s)`) }catch(e){toast(e.message,true)}}
document.head.insertAdjacentHTML('beforeend','<style>.monitor-summary{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px;margin-bottom:14px}.monitor-kpi{background:#fff;border:1px solid var(--line);border-radius:10px;padding:15px}.monitor-kpi small{display:block;color:var(--muted);font-weight:800;font-size:10px;letter-spacing:.05em}.monitor-kpi b{display:block;font-size:28px;margin-top:5px}.monitor-kpi.ok b{color:var(--green)}.monitor-kpi.warning b{color:var(--amber)}.monitor-kpi.error b{color:var(--red)}.monitor-layout{display:grid;grid-template-columns:minmax(520px,1.1fr) minmax(480px,.9fr);gap:14px}.monitor-card{background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden}.monitor-card-head{display:flex;align-items:center;justify-content:space-between;padding:13px 15px;border-bottom:1px solid var(--line)}.monitor-card-head h3{margin:0}.monitor-table{width:100%;border-collapse:collapse}.monitor-table th,.monitor-table td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line);font-size:12px}.monitor-table th{background:#edf4fa;color:#526477}.monitor-table-wrap{max-height:520px;overflow:auto}.monitor-guide{grid-column:1/-1}.monitor-guide-scroll{overflow:auto;max-height:560px}.monitor-guide-board{min-width:1180px}.monitor-guide-row{display:grid;grid-template-columns:230px 1fr;min-height:64px;border-bottom:1px solid var(--line)}.monitor-guide-channel{position:sticky;left:0;z-index:2;padding:10px 12px;background:#fff;border-right:1px solid var(--line)}.monitor-guide-channel small{display:block;color:var(--muted);margin-top:3px}.monitor-guide-track{position:relative;background:repeating-linear-gradient(to right,#fff 0,#fff calc(12.5% - 1px),#e1e8ee calc(12.5% - 1px),#e1e8ee 12.5%)}.monitor-guide-program{position:absolute;top:5px;height:53px;padding:7px 8px;border:1px solid #a9cde2;border-radius:7px;background:#e2f2fc;overflow:hidden;text-align:left;color:#0a426a}.monitor-guide-program.current{background:linear-gradient(120deg,var(--blue),var(--cyan));color:#fff}.monitor-guide-program b,.monitor-guide-program small{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.monitor-loading{padding:45px;text-align:center;color:var(--muted)}@media(max-width:1050px){.monitor-layout{grid-template-columns:1fr}.monitor-summary{grid-template-columns:1fr 1fr}}</style>');
document.head.insertAdjacentHTML('beforeend','<style>.monitor-guide-actions{display:flex!important;align-items:center;justify-content:flex-end;gap:9px;flex-wrap:nowrap}.monitor-guide-actions input{width:320px;min-width:220px;height:40px}.monitor-refresh-button{height:40px;min-width:154px;display:inline-flex;align-items:center;justify-content:center;gap:7px;background:linear-gradient(120deg,var(--blue),var(--cyan))!important;color:#fff!important;border:0!important;border-radius:8px!important;box-shadow:0 4px 12px #0646bd2c}.monitor-refresh-button .refresh-icon{font-size:17px;line-height:1}.monitor-refresh-button.loading .refresh-icon{animation:monitor-spin .8s linear infinite}@keyframes monitor-spin{to{transform:rotate(360deg)}}@media(max-width:760px){.monitor-card-head{align-items:stretch;flex-direction:column;gap:10px}.monitor-guide-actions{width:100%;flex-wrap:wrap}.monitor-guide-actions input{width:100%;min-width:0}.monitor-refresh-button{flex:1}}</style>');
let mainPage='monitor',monitorGuide=null,monitorGuideLoadedAt=0,monitorLoadSequence=0;
const firstRailOperation=document.querySelector('.rail-nav .rail-section+button');
if(firstRailOperation){firstRailOperation.id='railMonitor';firstRailOperation.setAttribute('onclick',"showMainPage('monitor')");firstRailOperation.innerHTML='<span class="rail-icon">◉</span><span class="rail-label">Monitoramento</span>';firstRailOperation.insertAdjacentHTML('afterend','<button id="railCarriers" onclick="showMainPage(\'carriers\')"><span class="rail-icon">▶</span><span class="rail-label">Portadoras</span></button>')}
function showMainPage(page){mainPage=page==='carriers'?'carriers':'monitor';const monitoring=mainPage==='monitor',toolbar=document.querySelector('.toolbar'),summary=document.querySelector('.summary'),filters=document.querySelector('.panel-filter');if(toolbar)toolbar.style.display=monitoring?'none':'';if(summary)summary.style.display=monitoring?'none':'';if(filters)filters.style.display=monitoring?'none':'flex';el('railMonitor')?.classList.toggle('active',monitoring);el('railCarriers')?.classList.toggle('active',!monitoring);render()}
function monitorStatusRows(){const health=state.epg_health?.carriers||[];return health.flatMap(carrier=>carrier.services.map(service=>`<tr><td><b><i class="health-dot ${service.status}"></i>${esc(service.service_name)}</b><div class="muted">${esc(carrier.name)}</div></td><td>${esc(service.current_title||'Sem programa no ar')}<div class="muted">Próximo: ${esc(service.next_title||'não informado')}</div></td><td><span class="badge ${service.status==='error'?'error':service.status==='ok'?'running':''}">${service.status==='ok'?'Normal':service.status==='warning'?'Atenção':'Erro'}</span><div class="health-message ${service.status}">${esc(service.message)}</div></td></tr>`)).join('')}
function renderMonitorGuide(data){if(!data)return '<div class="monitor-loading">Carregando grade completa…</div>';const start=timelineDayStart(0),end=start+86400,now=Date.now()/1000;return `<div class="monitor-guide-board">${data.services.map(service=>{const blocks=service.schedule.filter(p=>p.stop>start&&p.start<end).map(p=>{const left=Math.max(0,(Math.max(p.start,start)-start)*100/86400),width=Math.max(.45,(Math.min(p.stop,end)-Math.max(p.start,start))*100/86400),current=p.start<=now&&now<p.stop;return `<button class="monitor-guide-program ${current?'current':''}" style="left:${left}%;width:${width}%" title="${esc(p.title)} · ${fmt(p.start)}–${fmt(p.stop)}" onclick="openTimelineProgram('${esc(service.row_id)}',${p.start})"><b>${esc(p.title)}</b><small>${fmt(p.start)}–${fmt(p.stop)}</small></button>`}).join('');return `<div class="monitor-guide-row"><div class="monitor-guide-channel"><b>${esc(service.name)}</b><small>${esc(service.carrier_name)} · SID ${service.service_id}</small></div><div class="monitor-guide-track">${blocks||'<span class="timeline-empty">Sem programação</span>'}</div></div>`}).join('')}</div>`}
async function loadMonitorGuide(){const sequence=++monitorLoadSequence;try{const start=timelineDayStart(0),data=await api(`/api/guides?start=${start}&end=${start+86400}`);if(sequence!==monitorLoadSequence||mainPage!=='monitor')return;monitorGuide=data;monitorGuideLoadedAt=Date.now();timelineData=data;const target=el('monitorGuideContent');if(target)target.innerHTML=renderMonitorGuide(data);const count=el('monitorProgrammeCount');if(count)count.textContent=`${data.services.length} canais · ${data.programme_count} programas`;}catch(error){const target=el('monitorGuideContent');if(target)target.innerHTML=`<div class="monitor-loading error-text">${esc(error.message)}</div>`}}
async function refreshMonitorGuide(){const button=el('monitorRefreshButton');if(button){button.disabled=true;button.classList.add('loading');button.innerHTML='<span class="refresh-icon">⟳</span> Atualizando…'}monitorGuide=null;const target=el('monitorGuideContent');if(target)target.innerHTML='<div class="monitor-loading">Atualizando canais e programação…</div>';await loadMonitorGuide();const current=el('monitorRefreshButton');if(current){current.disabled=false;current.classList.remove('loading');current.innerHTML='<span class="refresh-icon">⟳</span> Atualizar grade'}}
function renderMonitor(){const carriers=state.carriers||[],health=state.epg_health||{},services=carriers.flatMap(c=>c.services),issues=health.errors||[],epgIssues=issues.filter(e=>e.code!=='emitter_stopped'),activeServices=carriers.filter(c=>c.active).reduce((n,c)=>n+c.services.length,0);el('mCarriers').textContent=carriers.length;el('mActive').textContent=carriers.filter(c=>c.active).length;el('mServices').textContent=services.length;el('mRestarts').textContent=carriers.reduce((n,c)=>n+(c.restart_count||0),0);el('carrierTable').innerHTML=`<section class="monitor-summary"><div class="monitor-kpi"><small>CANAIS CADASTRADOS</small><b>${services.length}</b></div><div class="monitor-kpi ok"><small>CANAIS EM EMISSÃO</small><b>${activeServices}</b></div><div class="monitor-kpi ${issues.length?'error':'ok'}"><small>ERROS GERAIS</small><b>${issues.length}</b></div><div class="monitor-kpi ${epgIssues.length?'warning':'ok'}"><small>ERROS DE EPG</small><b>${epgIssues.length}</b></div></section><section class="monitor-layout"><div class="monitor-card"><div class="monitor-card-head"><h3>Monitoramento dos canais</h3><span>${services.length} canais</span></div><div class="monitor-table-wrap"><table class="monitor-table"><thead><tr><th>Canal</th><th>Programação</th><th>Situação</th></tr></thead><tbody>${monitorStatusRows()||'<tr><td colspan="3">Nenhum canal cadastrado.</td></tr>'}</tbody></table></div></div><div class="monitor-card"><div class="monitor-card-head"><h3>Erros e alertas</h3><button onclick="openEpgErrors()">Ver central</button></div><div class="monitor-table-wrap"><table class="monitor-table"><tbody>${issues.slice(0,20).map(e=>`<tr><td><b>${esc(e.service_name)}</b><div class="muted">${esc(e.carrier_name)} · ${esc(e.source_name)}</div></td><td><span class="health-message ${e.status}">${esc(e.message)}</span></td></tr>`).join('')||'<tr><td><i class="health-dot ok"></i>Todos os canais estão normais.</td></tr>'}</tbody></table></div></div><div class="monitor-card monitor-guide"><div class="monitor-card-head"><div><h3>Grade EPG completa — hoje</h3><small id="monitorProgrammeCount" class="muted">Carregando programação…</small></div><button onclick="monitorGuide=null;renderMonitor();loadMonitorGuide()">Atualizar grade</button></div><div id="monitorGuideContent" class="monitor-guide-scroll">${renderMonitorGuide(monitorGuide)}</div></div></section>`;if(!monitorGuide)loadMonitorGuide()}
let monitorSearch='';
function monitorMatches(service){const query=monitorSearch.trim().toLocaleLowerCase('pt-BR');if(!query)return true;const channel=`${service.name||''} ${service.carrier_name||''} ${service.service_id||''}`.toLocaleLowerCase('pt-BR');return channel.includes(query)||(service.schedule||[]).some(program=>`${program.title||''} ${program.description||''} ${program.category||''}`.toLocaleLowerCase('pt-BR').includes(query))}
function filterMonitorGuide(value){monitorSearch=value;const target=el('monitorGuideContent');if(target)target.innerHTML=renderMonitorGuide(monitorGuide);const count=el('monitorProgrammeCount'),visible=(monitorGuide?.services||[]).filter(monitorMatches);if(count)count.textContent=`${visible.length} canais · ${visible.reduce((total,item)=>total+(item.schedule||[]).length,0)} programas exibidos`}
const renderMonitorGuideBase=renderMonitorGuide;
renderMonitorGuide=function(data){if(!data)return '<div class="monitor-loading">Carregando grade completa…</div>';return renderMonitorGuideBase({...data,services:(data.services||[]).filter(monitorMatches)})}
renderMonitor=function(){const carriers=state.carriers||[],health=state.epg_health||{},services=carriers.flatMap(c=>c.services),issues=health.errors||[],epgIssues=issues.filter(e=>e.code!=='emitter_stopped'),activeServices=carriers.filter(c=>c.active).reduce((n,c)=>n+c.services.length,0);el('mCarriers').textContent=carriers.length;el('mActive').textContent=carriers.filter(c=>c.active).length;el('mServices').textContent=services.length;el('mRestarts').textContent=carriers.reduce((n,c)=>n+(c.restart_count||0),0);el('carrierTable').innerHTML=`<section class="monitor-summary"><div class="monitor-kpi"><small>CANAIS CADASTRADOS</small><b>${services.length}</b></div><div class="monitor-kpi ok"><small>CANAIS EM EMISSÃO</small><b>${activeServices}</b></div><div class="monitor-kpi ${issues.length?'error':'ok'}"><small>ERROS GERAIS</small><b>${issues.length}</b></div><div class="monitor-kpi ${epgIssues.length?'warning':'ok'}"><small>ERROS DE EPG</small><b>${epgIssues.length}</b></div></section><section class="monitor-layout"><div class="monitor-card monitor-guide"><div class="monitor-card-head"><div><h3>Grade EPG completa — hoje</h3><small id="monitorProgrammeCount" class="muted">${monitorGuide?`${monitorGuide.services.filter(monitorMatches).length} canais · ${monitorGuide.services.filter(monitorMatches).reduce((total,item)=>total+(item.schedule||[]).length,0)} programas exibidos`:'Carregando programação…'}</small></div><div class="monitor-guide-actions"><input id="monitorGuideSearch" value="${esc(monitorSearch)}" placeholder="Pesquisar canal ou programação" oninput="filterMonitorGuide(this.value)"><button id="monitorRefreshButton" class="monitor-refresh-button" onclick="refreshMonitorGuide()"><span class="refresh-icon">⟳</span> Atualizar grade</button></div></div><div id="monitorGuideContent" class="monitor-guide-scroll">${renderMonitorGuide(monitorGuide)}</div></div><div class="monitor-card"><div class="monitor-card-head"><h3>Monitoramento dos canais</h3><span>${services.length} canais</span></div><div class="monitor-table-wrap"><table class="monitor-table"><thead><tr><th>Canal</th><th>Programação</th><th>Situação</th></tr></thead><tbody>${monitorStatusRows()||'<tr><td colspan="3">Nenhum canal cadastrado.</td></tr>'}</tbody></table></div></div><div class="monitor-card"><div class="monitor-card-head"><h3>Erros e alertas</h3><button onclick="openEpgErrors()">Ver central</button></div><div class="monitor-table-wrap"><table class="monitor-table"><tbody>${issues.slice(0,20).map(e=>`<tr><td><b>${esc(e.service_name)}</b><div class="muted">${esc(e.carrier_name)} · ${esc(e.source_name)}</div></td><td><span class="health-message ${e.status}">${esc(e.message)}</span></td></tr>`).join('')||'<tr><td><i class="health-dot ok"></i>Todos os canais estão normais.</td></tr>'}</tbody></table></div></div></section>`;if(!monitorGuide)loadMonitorGuide()}
const renderManagementPage=render;
render=function(){if(mainPage==='monitor'){const license=state.license||{},errors=state.epg_health?.errors||[];el('mLicense').textContent=license.valid?`${license.channel_count}/${license.max_channels}`:'Bloqueada';el('licenseReason').textContent=license.valid?'canais utilizados':license.reason||'Licença inválida';el('licenseMetric').className=`metric ${license.valid?'license-valid':'license-invalid'}`;el('epgErrorsButton').textContent=`Erros do EPG (${errors.length})`;el('epgErrorsButton').className=errors.length?'has-errors':'';el('epgHealthAlert').hidden=true;renderMonitor();return}renderManagementPage()};
setInterval(()=>{if(mainPage==='monitor')loadMonitorGuide()},60000);
showMainPage('monitor');
document.head.insertAdjacentHTML('beforeend','<style>.overview-sort{display:flex;align-items:center;gap:7px;white-space:nowrap;color:#526477;font-size:12px}.overview-sort select{min-width:170px;padding:8px 30px 8px 10px;background:#fff}@media(max-width:900px){.overview-sort{width:100%}.overview-sort select{flex:1}}</style>');
const renderWithCarrierOrder=render;
render=function(){state.carriers=sortedCarriers(state.carriers||[]);if(el('carrierSort'))el('carrierSort').value=carrierSortMode;renderWithCarrierOrder()};
refresh();setInterval(refresh,15000);
</script></body></html>'''


def main() -> None:
    global APP
    bootstrap_user = os.environ.get("EPG_ADMIN_USER", "").strip()
    bootstrap_password = os.environ.get("EPG_ADMIN_PASSWORD", "")
    public_base_url = os.environ.get("EPG_PUBLIC_BASE_URL", "")
    license_server_url = os.environ.get("EPG_LICENSE_SERVER_URL", "")
    license_key_file = os.environ.get("EPG_LICENSE_KEY_FILE", "")
    license_installation_id = os.environ.get("EPG_LICENSE_INSTALLATION_ID", "")
    license_check_seconds = int(os.environ.get("EPG_LICENSE_CHECK_SECONDS", "43200"))
    data_dir = Path(os.environ.get("EPG_DATA_DIR", "/data"))
    binary = os.environ.get("EPG_EMITTER_BINARY", "/app/TVStreamEpgOnly")
    if not Path(binary).is_file():
        raise SystemExit(f"Emissor EPG não encontrado: {binary}")
    try:
        APP = Application(
            data_dir, binary, bootstrap_user, bootstrap_password, public_base_url,
            license_server_url, license_key_file, license_installation_id,
            license_check_seconds,
        )
    except ApiError as error:
        raise SystemExit(str(error)) from error
    host = os.environ.get("EPG_HTTP_HOST", "0.0.0.0")
    port = int(os.environ.get("EPG_HTTP_PORT", "9100"))
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    stopping = threading.Event()

    def shutdown(_signum: int, _frame: Any) -> None:
        if stopping.is_set():
            return
        stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"{PRODUCT_NAME} {PRODUCT_VERSION} em http://{host}:{port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        APP.close()


if __name__ == "__main__":
    main()
