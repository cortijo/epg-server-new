#!/usr/bin/env python3
"""EPG Stream — control plane for the standalone ISDB-TB EPG emitters."""

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
import re
import signal
import struct
import subprocess
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


PRODUCT_NAME = "EPG Stream"
PRODUCT_VERSION = "1.9.0"
DEFAULT_SOURCE = {
    "id": "braziltvepg",
    "name": "BrazilTVEPG (padrão)",
    "url": "https://github.com/limaalef/BrazilTVEPG/raw/refs/heads/main/claro.xml",
    "is_default": True,
}
MAX_BODY = 3 * 1024 * 1024
MAX_LOGO = 2 * 1024 * 1024
MAX_XMLTV = 96 * 1024 * 1024
GUIDE_CACHE_SECONDS = 300
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


def parse_xmltv(payload: bytes) -> dict[str, Any]:
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
            if channel_id and stop > minimum and start < maximum and stop > start:
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


def normalize_uploaded_xmltv(payload: bytes) -> tuple[bytes, dict[str, Any]]:
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
        if channel_id not in declared:
            candidates = prefix_candidates.get(_numeric_channel_prefix(channel_id), set())
            if len(candidates) == 1:
                replacement = next(iter(candidates))
                if replacement != channel_id:
                    child.set("channel", replacement)
                    channel_id = replacement
                    stats["channel_refs_rewritten"] += 1
            elif channel_id:
                unresolved.add(channel_id)
        try:
            start_text, start_added = _normalized_xmltv_time(child.get("start") or "")
            stop_text, stop_added = _normalized_xmltv_time(child.get("stop") or "")
            start = parse_xmltv_datetime(start_text)
            stop = parse_xmltv_datetime(stop_text)
            if not channel_id or stop <= start:
                raise ValueError("evento sem canal ou duração válida")
        except ValueError:
            root.remove(child)
            stats["invalid_programmes_removed"] += 1
            continue
        child.set("channel", channel_id)
        child.set("start", start_text)
        child.set("stop", stop_text)
        stats["timezone_added"] += int(start_added) + int(stop_added)
        stats["programmes"] += 1
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
    parsed = parse_xmltv(normalized)
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
    result = {
        "id": str(source.get("id") or slug_id("source")),
        "name": str(source.get("name") or "").strip(),
        "url": str(source.get("url") or "").strip(),
        "is_default": bool(source.get("is_default", False)),
    }
    if not result["name"]:
        raise ApiError("Informe o nome da fonte XMLTV")
    parsed = urllib.parse.urlparse(result["url"])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ApiError("A fonte XMLTV deve usar uma URL HTTP ou HTTPS válida")
    return result


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
            }
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
    def __init__(self):
        self.lock = threading.RLock()
        self.entries: dict[str, dict[str, Any]] = {}

    @staticmethod
    def download(url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": f"EPGStream/{PRODUCT_VERSION}"})
        with urllib.request.urlopen(request, timeout=30) as response:
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
        return payload

    def get(self, source: dict[str, Any], force: bool = False) -> dict[str, Any]:
        source_id = source["id"]
        with self.lock:
            cached = self.entries.get(source_id)
            if cached and not force and time.time() - cached["fetched_at"] < GUIDE_CACHE_SECONDS:
                return cached
        payload = self.download(source["url"])
        parsed = parse_xmltv(payload)
        parsed.update({"fetched_at": time.time(), "bytes": len(payload), "source_id": source_id})
        with self.lock:
            self.entries[source_id] = parsed
        return parsed

    def invalidate(self, source_id: str) -> None:
        with self.lock:
            self.entries.pop(source_id, None)


class Supervisor:
    def __init__(self, store: Store, binary: str, log_dir: Path):
        self.store = store
        self.binary = binary
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
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
        source_by_id = {item["id"]: item for item in snapshot["sources"]}
        services = []
        for service in carrier["services"]:
            resolved = copy.deepcopy(service)
            service_source = source_by_id.get(service.get("source_id") or carrier["source_id"])
            if not service_source:
                raise ApiError(f"A fonte XMLTV do canal {service['name']} não existe")
            resolved["source_url"] = service_source["url"]
            services.append(resolved)
        environment = os.environ.copy()
        environment.update({
            "EPG_STREAM_ID": carrier["id"],
            "EPG_STREAM_NAME": carrier["name"],
            "EPG_SOURCE_URL": source["url"],
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
        })
        return environment

    def _start_locked(self, carrier: dict[str, Any]) -> None:
        carrier_id = carrier["id"]
        self._stop_locked(carrier_id, "stopped")
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
        return {"product": PRODUCT_NAME, "version": PRODUCT_VERSION, "carriers": carriers}

    def _loop(self) -> None:
        while not self.stopping.wait(1):
            snapshot = self.store.snapshot()
            configured = {carrier["id"]: carrier for carrier in snapshot["carriers"]}
            with self.lock:
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
                 public_base_url: str = ""):
        self.data_dir = data_dir
        self.public_base_url = public_base_url.strip().rstrip("/")
        self.logo_dir = data_dir / "logos"
        self.publication_dir = data_dir / "xmltv-publications"
        self.logo_dir.mkdir(parents=True, exist_ok=True)
        self.publication_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(data_dir / "epg-product.json")
        self._bootstrap_user(bootstrap_user, bootstrap_password)
        self._migrate_logos()
        self.guides = GuideCache()
        self.supervisor = Supervisor(self.store, binary, data_dir / "logs")
        self.supervisor.start()

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

    def delete_source(self, source_id: str) -> dict[str, Any]:
        with self.store.lock:
            if any(item["source_id"] == source_id or any(
                    service.get("source_id") == source_id for service in item["services"])
                    for item in self.store.data["carriers"]):
                raise ApiError("A fonte está em uso por uma portadora ou canal")
            items = self.store.data["sources"]
            filtered = [item for item in items if item["id"] != source_id]
            if len(filtered) == len(items):
                raise ApiError("Fonte XMLTV não encontrada", HTTPStatus.NOT_FOUND)
            if not filtered:
                raise ApiError("O sistema precisa manter ao menos uma fonte XMLTV")
            if not any(item["is_default"] for item in filtered):
                filtered[0]["is_default"] = True
            self.store.data["sources"] = filtered
            self.store.save()
        self.guides.invalidate(source_id)
        return {"result": "ok"}

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
        channels = sorted(guide["channels"].values(), key=lambda item: item["name"].casefold())
        return {"source_id": source_id, "fetched_at": int(guide["fetched_at"]), "bytes": guide["bytes"], "channels": channels}

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


APP: Application | None = None


class Handler(BaseHTTPRequestHandler):
    server_version = "EPGStream/1.1"
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
        self.send_header("WWW-Authenticate", 'Basic realm="EPG Stream", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _require_admin(self) -> None:
        if not self.current_user or self.current_user.get("role") != "admin":
            raise ApiError("Apenas administradores podem gerenciar usuários", HTTPStatus.FORBIDDEN)

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

    def do_GET(self) -> None:
        assert APP is not None
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/health":
                self._json({"status": "ok", "product": PRODUCT_NAME, "version": PRODUCT_VERSION})
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
            if not self._require_auth():
                return
            if path == "/":
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
                state["sources"] = [{key: value for key, value in source.items() if key != "url"} for source in APP.store.snapshot()["sources"]]
                self._json(state)
            elif path == "/api/session":
                self._json({"user": self.current_user})
            elif path == "/api/users":
                self._require_admin()
                self._json({"users": APP.users()})
            elif path == "/api/sources":
                self._json({"sources": APP.store.snapshot()["sources"]})
            elif path == "/api/publications":
                self._json({"publications": APP.publications()})
            elif path == "/api/logo":
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
                source_id = self._query().get("source_id", [""])[0]
                self._json(APP.catalog(source_id))
            elif path == "/api/guide":
                carrier_id = self._query().get("carrier_id", [""])[0]
                self._json(APP.guide(carrier_id))
            elif path == "/api/logs":
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
            if path == "/api/publications/upload":
                query = self._query()
                publication_id = query.get("id", [""])[0]
                filename = query.get("filename", ["guide.xml"])[0]
                result = APP.upload_publication(publication_id, filename, self._raw_body(MAX_XMLTV))
                self._json(result)
                return
            request = self._body()
            if path == "/api/users":
                self._require_admin()
                result = APP.save_user(request)
            elif path == "/api/users/delete":
                self._require_admin()
                result = APP.delete_user(str(request.get("id") or ""), str(self.current_user["id"]))
            elif path == "/api/sources":
                result = APP.save_source(request)
            elif path == "/api/sources/test":
                source = validate_source(request)
                guide = APP.guides.get(source, force=True)
                result = {"result": "ok", "channels": len(guide["channels"]), "programmes": sum(map(len, guide["programmes"].values())), "bytes": guide["bytes"]}
            elif path == "/api/sources/delete":
                result = APP.delete_source(str(request.get("id") or ""))
            elif path == "/api/publications":
                result = APP.save_publication(request)
            elif path == "/api/publications/version/delete":
                result = APP.delete_publication_version(
                    str(request.get("publication_id") or ""), str(request.get("version_id") or "")
                )
            elif path == "/api/publications/delete":
                result = APP.delete_publication(str(request.get("id") or ""))
            elif path == "/api/carriers":
                result = APP.save_carrier(request)
            elif path == "/api/carriers/logo":
                result = APP.save_logo(request)
            elif path == "/api/carriers/logo/delete":
                result = APP.delete_logo(request)
            elif path == "/api/carriers/delete":
                result = APP.delete_carrier(str(request.get("id") or ""))
            elif path.startswith("/api/carriers/"):
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


INDEX_HTML = r'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EPG Stream</title><style>
:root{--navy:#071b33;--blue:#087ec1;--cyan:#1bb6e8;--bg:#f2f6fa;--card:#fff;--text:#14263a;--muted:#6d7c8d;--line:#dce6ef;--green:#19a974;--red:#df4c55;--amber:#d99a23}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px Inter,Segoe UI,Arial,sans-serif}.top{background:linear-gradient(120deg,var(--navy),#0b5689);color:#fff;padding:22px 30px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 8px 28px #071b3330}.brand{display:flex;gap:14px;align-items:center}.logo{width:46px;height:46px;border:2px solid #51c7ed;border-radius:14px;display:grid;place-items:center;font-size:23px;font-weight:800}.brand h1{margin:0;font-size:22px}.brand small{color:#bde8fa}.live{display:flex;gap:8px;align-items:center}.dot{width:9px;height:9px;background:#31dc9a;border-radius:50%;box-shadow:0 0 0 5px #31dc9a22}.wrap{max-width:1500px;margin:0 auto;padding:24px}.toolbar{display:flex;gap:10px;justify-content:space-between;align-items:center;margin-bottom:18px}.toolbar h2{margin:0;font-size:20px}.actions{display:flex;gap:8px;flex-wrap:wrap}button{border:0;border-radius:9px;padding:10px 14px;font-weight:700;cursor:pointer;background:#e7eef5;color:var(--text)}button.primary{background:linear-gradient(120deg,var(--blue),var(--cyan));color:#fff}button.danger{color:var(--red)}button:disabled{opacity:.5;cursor:not-allowed}.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}.metric,.card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:0 5px 18px #0b254012}.metric{padding:17px}.metric b{font-size:24px;display:block;margin-top:6px}.metric span{color:var(--muted);font-size:12px}.muted{color:var(--muted)}.badge{font-size:11px;font-weight:800;text-transform:uppercase;border-radius:999px;padding:5px 8px;background:#eef2f6;white-space:nowrap}.badge.running{background:#dcf8ec;color:#087d56}.badge.error{background:#ffe4e5;color:#b72a34}.table-wrap{background:#fff;border:1px solid var(--line);border-radius:14px;box-shadow:0 5px 18px #0b254012;overflow:auto}.carrier-table{width:100%;min-width:1050px;border-collapse:collapse}.carrier-table th{padding:12px 14px;background:#edf4fa;color:#526477;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em}.carrier-table td{padding:14px;border-top:1px solid var(--line);vertical-align:middle}.carrier-table tbody:first-child tr:first-child td{border-top:0}.carrier-table tr.main-row:hover td{background:#f8fbfd}.carrier-name{font-size:15px;font-weight:800}.carrier-sub{margin-top:4px;font-size:12px;color:var(--muted)}.actions-col{width:190px;position:sticky;right:0;background:#fff;box-shadow:-8px 0 14px -14px #071b33;z-index:1}.carrier-table th.actions-col{background:#edf4fa}.action-stack{display:grid;grid-template-columns:1fr 1fr;gap:6px}.action-stack button{padding:8px 9px;font-size:12px}.action-stack .wide{grid-column:1/-1}.program-row{display:none}.program-row.open{display:table-row}.program-row>td{padding:0;background:#f5f9fc}.program-panel{padding:18px 22px}.program-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.program-list{display:grid;gap:8px}.program-line{display:grid;grid-template-columns:1.1fr 80px 120px 2fr 2fr auto;gap:12px;align-items:center;padding:11px 12px;background:#fff;border:1px solid var(--line);border-radius:10px}.program-line button{padding:8px 10px}.program-now{font-weight:750}.progress{height:5px;background:#e6edf3;border-radius:9px;margin-top:6px;overflow:hidden}.progress i{display:block;height:100%;background:linear-gradient(90deg,var(--blue),var(--cyan))}.empty{padding:50px;text-align:center;color:var(--muted)}.modal-back{position:fixed;inset:0;background:#071b3399;display:grid;place-items:center;padding:20px;z-index:5}.modal{background:#fff;border-radius:16px;width:min(920px,100%);max-height:92vh;overflow:auto;padding:22px;box-shadow:0 24px 70px #0005}.modal h2{margin:0 0 18px}.form-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}label{display:flex;flex-direction:column;gap:6px;font-weight:700;font-size:12px}label.wide{grid-column:1/-1}input,select{width:100%;border:1px solid #cbd8e4;border-radius:8px;padding:10px;background:#fff;color:var(--text)}.service-edit{display:grid;grid-template-columns:1.2fr 1.5fr .6fr auto;gap:8px;margin:8px 0;align-items:end;padding:10px;background:#f5f8fb;border-radius:10px}.modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}.guide-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.guide-list{margin-top:14px;display:flex;flex-direction:column;gap:9px}.guide-item{display:grid;grid-template-columns:110px 1fr;gap:12px;padding:12px;border:1px solid var(--line);border-radius:10px}.guide-item.current{border-color:#23aee1;background:#edfaff}.toast{position:fixed;right:20px;bottom:20px;background:var(--navy);color:#fff;padding:13px 17px;border-radius:10px;z-index:9;box-shadow:0 10px 30px #0004}.error-text{color:var(--red)}@media(max-width:760px){.wrap{padding:14px}.summary{grid-template-columns:1fr 1fr}.form-grid{grid-template-columns:1fr}.service-edit{grid-template-columns:1fr}.top{padding:16px}.program-line{grid-template-columns:1fr 70px}.program-line .program-detail{grid-column:1/-1}.actions-col{position:static}.carrier-table{min-width:900px}}
</style></head><body><header class="top"><div class="brand"><div class="logo">E</div><div><h1>EPG Stream</h1><small>Programação ISDB-TB em multicast</small></div></div><div class="live"><i class="dot"></i><span id="clock">Conectando</span></div></header><main class="wrap"><div class="toolbar"><div><h2>Portadoras e programação</h2><div class="muted">Visualização compacta; expanda uma portadora para consultar a programação.</div></div><div class="actions"><button id="usersButton" style="display:none" onclick="openUsers()">Usuários</button><button onclick="openPublications()">Publicações XMLTV</button><button onclick="openSources()">Fontes XMLTV</button><button id="timelineButton" disabled onclick="openTimeline()">Grade de programação</button><button class="primary" onclick="openCarrier()">+ Nova portadora</button></div></div><section class="summary"><div class="metric"><span>PORTADORAS</span><b id="mCarriers">0</b></div><div class="metric"><span>EMISSORAS ATIVAS</span><b id="mActive">0</b></div><div class="metric"><span>CANAIS / SERVIÇOS</span><b id="mServices">0</b></div><div class="metric"><span>REINÍCIOS</span><b id="mRestarts">0</b></div></section><section id="carrierTable"></section></main><div id="overlay"></div><div id="toast"></div>
<script>
document.head.insertAdjacentHTML('beforeend','<style>.service-edit{grid-template-columns:1fr 1fr 1.25fr .42fr .9fr 1.1fr auto}.logo-tools{display:flex;gap:5px;align-items:center;flex-wrap:wrap}.logo-tools button{padding:8px}.logo-preview{width:64px;height:36px;object-fit:contain;background:#fff;border:1px solid var(--line);border-radius:6px;padding:2px}.logo-status{font-size:11px;color:var(--muted)}@media(max-width:1100px){.service-edit{grid-template-columns:1fr 1fr}}</style>');
document.head.insertAdjacentHTML('beforeend','<style>.modal.timeline-modal{width:min(1420px,100%);padding:0;overflow:hidden}.timeline-head{padding:22px 24px 16px;border-bottom:1px solid var(--line)}.timeline-tools{display:flex;gap:9px;align-items:end;flex-wrap:wrap}.timeline-tools label{min-width:240px}.timeline-scroll{overflow:auto;max-height:70vh;background:#f8fbfd}.timeline-board{min-width:1120px}.timeline-axis,.timeline-row{display:grid;grid-template-columns:180px 1fr}.timeline-axis{position:sticky;top:0;z-index:4;background:#eef5fa;border-bottom:1px solid #cbd8e4}.timeline-corner,.timeline-channel{position:sticky;left:0;z-index:3;background:#fff;border-right:1px solid #cbd8e4}.timeline-corner{background:#eef5fa;padding:13px 14px;font-weight:800}.timeline-hours{position:relative;height:45px;background:repeating-linear-gradient(to right,transparent 0,transparent calc(16.666% - 1px),#cbd8e4 calc(16.666% - 1px),#cbd8e4 16.666%)}.timeline-hour{position:absolute;top:13px;transform:translateX(8px);font-size:12px;font-weight:750;color:#526477}.timeline-row{min-height:82px;border-bottom:1px solid var(--line)}.timeline-channel{display:flex;gap:9px;align-items:center;padding:10px 12px}.timeline-channel img{width:54px;height:36px;object-fit:contain}.timeline-channel strong{display:block}.timeline-track{position:relative;min-height:82px;background:repeating-linear-gradient(to right,#fff 0,#fff calc(16.666% - 1px),#e1e8ee calc(16.666% - 1px),#e1e8ee 16.666%)}.timeline-program{position:absolute;top:7px;height:68px;overflow:hidden;padding:8px 9px;border:1px solid #a9cde2;border-radius:8px;background:linear-gradient(145deg,#e9f7ff,#d8eefb);color:#0a426a;text-align:left;font-weight:600}.timeline-program.current{background:linear-gradient(145deg,#087ec1,#14a9dd);border-color:#087ec1;color:#fff}.timeline-program b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.timeline-program small{display:block;margin-top:5px;opacity:.8}.timeline-empty{padding:29px 14px;color:var(--muted)}.timeline-now{position:absolute;top:0;bottom:0;width:2px;background:#df4c55;z-index:2;pointer-events:none}.timeline-now:before{content:"Agora";position:absolute;top:2px;left:4px;background:#df4c55;color:#fff;padding:2px 5px;border-radius:4px;font-size:9px;font-weight:800}.timeline-now.track:before{display:none}@media(max-width:760px){.modal-back{padding:8px}.modal.timeline-modal{max-height:96vh}.timeline-head{padding:16px}.timeline-tools label{min-width:100%}}</style>');
document.head.insertAdjacentHTML('beforeend','<style>.modal.publication-modal{width:min(1180px,100%)}.publication-card{border:1px solid var(--line);border-radius:13px;padding:16px;margin-top:13px;background:#f9fbfd}.publication-title{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.publication-url{display:flex;gap:7px;margin:12px 0}.publication-url input{font-family:Consolas,monospace;font-size:12px}.version-table{width:100%;border-collapse:collapse;background:#fff}.version-table th,.version-table td{padding:9px;border-top:1px solid var(--line);text-align:left;font-size:12px}.version-table th{color:var(--muted);font-size:10px;text-transform:uppercase}.upload-label{display:inline-flex;flex-direction:row;align-items:center;background:linear-gradient(120deg,var(--blue),var(--cyan));color:#fff;border-radius:9px;padding:10px 14px;cursor:pointer}.upload-label input{display:none}@media(max-width:760px){.publication-title,.publication-url{flex-direction:column}.version-table{min-width:850px}}</style>');
let state={carriers:[],sources:[]},sources=[],catalog=[],session={user:null},users=[],publications=[],expandedCarriers=new Set(),guideCache={},timelineCarrierId='',timelineStart=0;const el=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=t=>t?new Date(t*1000).toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'}):'--:--';
function toast(message,error=false){el('toast').innerHTML=`<div class="toast ${error?'error-text':''}">${esc(message)}</div>`;setTimeout(()=>el('toast').innerHTML='',3500)}
async function api(url,opt={}){const r=await fetch(url,{headers:{'Content-Type':'application/json'},...opt});const j=await r.json().catch(()=>({error:'Resposta inválida'}));if(!r.ok||j.error)throw Error(j.error||`HTTP ${r.status}`);return j}
async function refresh(){try{const loaded=await Promise.all([api('/api/state'),api('/api/sources'),api('/api/session')]);state=loaded[0];sources=loaded[1].sources;session=loaded[2];el('usersButton').style.display=session.user?.role==='admin'?'':'none';el('timelineButton').disabled=!(state.carriers||[]).length;render();el('clock').textContent=`${session.user?.display_name||''} · ${new Date().toLocaleTimeString('pt-BR')}`}catch(e){toast(e.message,true)}}
function render(){const cs=state.carriers||[];el('mCarriers').textContent=cs.length;el('mActive').textContent=cs.filter(c=>c.active).length;el('mServices').textContent=cs.reduce((n,c)=>n+c.services.length,0);el('mRestarts').textContent=cs.reduce((n,c)=>n+(c.restart_count||0),0);el('carrierTable').innerHTML=cs.length?`<div class="table-wrap"><table class="carrier-table"><thead><tr><th>Portadora</th><th>Destino multicast</th><th>Canais</th><th>Estado</th><th class="actions-col">Ações</th></tr></thead><tbody>${cs.map(carrierRows).join('')}</tbody></table></div>`:'<div class="card empty"><h3>Nenhuma portadora cadastrada</h3><p>Cadastre a primeira portadora e associe os canais do XMLTV.</p></div>'}
function utcOffsetLabel(minutes){const sign=minutes<0?'-':'+';const absolute=Math.abs(minutes);return `UTC${sign}${String(Math.floor(absolute/60)).padStart(2,'0')}:${String(absolute%60).padStart(2,'0')}`}
function clockLabel(c){return c.clock_mode==='custom'?`${utcOffsetLabel(c.clock_utc_offset_minutes??-180)} · correção ${c.clock_correction_minutes>0?'+':''}${c.clock_correction_minutes||0} min`:'Padrão UTC-03:00'}
function carrierRows(c){const opened=expandedCarriers.has(c.id),guide=guideCache[c.id];return `<tr class="main-row"><td><div class="carrier-name">${esc(c.name)}</div><div class="carrier-sub">TSID ${c.transport_stream_id} · ONID ${c.original_network_id} · ${esc(clockLabel(c))}</div></td><td><b>${esc(c.destination)}:${c.port}</b><div class="carrier-sub">${(c.bitrate/1000).toLocaleString('pt-BR')} kbit/s</div></td><td><b>${c.services.length}</b> serviço(s)</td><td><span class="badge ${esc(c.status)}">${c.active?'Em transmissão':c.status==='error'?'Falha':'Parada'}</span>${c.last_error?`<div class="error-text carrier-sub">${esc(c.last_error)}</div>`:''}</td><td class="actions-col"><div class="action-stack"><button id="programButton-${esc(c.id)}" class="primary wide" aria-expanded="${opened}" onclick="togglePrograms('${esc(c.id)}')">${opened?'Ocultar programação':'Ver programação'}</button><button onclick="actionCarrier('${esc(c.id)}','${c.active?'restart':'start'}')">${c.active?'Reiniciar':'Iniciar'}</button>${c.active?`<button onclick="actionCarrier('${esc(c.id)}','stop')">Parar</button>`:'<span></span>'}<button onclick="openCarrier('${esc(c.id)}')">Editar</button><button onclick="cloneCarrier('${esc(c.id)}')">Clonar</button><button onclick="openLogs('${esc(c.id)}')">Logs</button><button class="danger" onclick="deleteCarrier('${esc(c.id)}')">Excluir</button></div></td></tr><tr id="programs-${esc(c.id)}" class="program-row ${opened?'open':''}"><td colspan="5"><div class="program-panel">${opened?(guide?programPanel(c,guide):'<div class="muted">Carregando programação…</div>'):''}</div></td></tr>`}
function programPanel(c,g){return `<div class="program-title"><div><b>Programação da portadora</b><div class="muted">${esc(g.timezone||'America/Sao_Paulo')} · ${c.services.length} serviço(s)</div></div></div><div class="program-list">${g.services.map(s=>`<div class="program-line"><div><b>${esc(s.name)}</b><div class="muted">${esc(s.epg_channel_id)}</div></div><div>SID ${s.service_id}</div><div class="program-detail"><small class="muted">HORÁRIO</small><div>${s.current?`${fmt(s.current.start)}–${fmt(s.current.stop)}`:'--:--'}</div></div><div class="program-detail"><small class="muted">NO AR AGORA</small><div class="program-now">${esc(s.current?.title||'Sem programa no ar')}</div>${s.current?`<div class="progress"><i style="width:${s.current.progress||0}%"></i></div>`:''}</div><div class="program-detail"><small class="muted">A SEGUIR</small><div>${esc(s.next?.title||'Sem próxima atração')}</div></div><button onclick="openGuide('${esc(c.id)}','${esc(s.id)}')">Ver grade</button></div>`).join('')||'<div class="muted">Nenhum serviço cadastrado.</div>'}</div>`}
const TIMELINE_WINDOW=3*60*60,TIMELINE_STEP=90*60;
function defaultTimelineStart(){return Math.floor(Date.now()/1000/1800)*1800}
async function openTimeline(carrierId='',start=null){const carriers=state.carriers||[];if(!carriers.length){toast('Cadastre uma portadora antes de abrir a grade',true);return}timelineCarrierId=carrierId||timelineCarrierId||carriers[0].id;timelineStart=(start??timelineStart)||defaultTimelineStart();modal(`<div class="timeline-head"><div class="guide-head"><div><h2 style="margin-bottom:5px">Grade de programação</h2><div class="muted">Programação das portadoras em uma linha do tempo de três horas.</div></div><button onclick="closeModal()">Fechar</button></div><div class="timeline-tools"><label>Portadora<select id="timelineCarrier" onchange="timelineCarrierId=this.value;loadTimeline()">${carriers.map(c=>`<option value="${esc(c.id)}" ${c.id===timelineCarrierId?'selected':''}>${esc(c.name)} · TSID ${c.transport_stream_id}</option>`).join('')}</select></label><button onclick="shiftTimeline(-TIMELINE_STEP)">← 90 min</button><button onclick="resetTimeline()">Agora</button><button onclick="shiftTimeline(TIMELINE_STEP)">90 min →</button><span id="timelineRange" class="muted"></span></div></div><div id="timelineContent" class="timeline-scroll"><div class="empty">Carregando programação…</div></div>`,'timeline-modal');await loadTimeline()}
async function loadTimeline(){const content=el('timelineContent');if(!content)return;content.innerHTML='<div class="empty">Carregando programação…</div>';try{guideCache[timelineCarrierId]=guideCache[timelineCarrierId]||await api(`/api/guide?carrier_id=${encodeURIComponent(timelineCarrierId)}`);renderTimeline(guideCache[timelineCarrierId])}catch(e){content.innerHTML=`<div class="empty error-text">${esc(e.message)}</div>`}}
function renderTimeline(g){const content=el('timelineContent'),carrier=state.carriers.find(c=>c.id===timelineCarrierId),end=timelineStart+TIMELINE_WINDOW,now=Date.now()/1000;if(!content||!carrier)return;const hours=Array.from({length:6},(_,i)=>`<span class="timeline-hour" style="left:${i*100/6}%">${fmt(timelineStart+i*1800)}</span>`).join(''),nowPosition=(now-timelineStart)*100/TIMELINE_WINDOW,nowLine=now>=timelineStart&&now<=end?`<i class="timeline-now" style="left:${nowPosition}%"></i>`:'',trackNowLine=nowLine?`<i class="timeline-now track" style="left:${nowPosition}%"></i>`:'';const rows=g.services.map(s=>{const programmes=(s.schedule||[]).filter(p=>p.stop>timelineStart&&p.start<end);const blocks=programmes.map(p=>{const visibleStart=Math.max(p.start,timelineStart),visibleEnd=Math.min(p.stop,end),left=(visibleStart-timelineStart)*100/TIMELINE_WINDOW,width=Math.max((visibleEnd-visibleStart)*100/TIMELINE_WINDOW,.8),current=p.start<=now&&now<p.stop;return `<button class="timeline-program ${current?'current':''}" style="left:${left}%;width:${width}%" title="${esc(p.title)} · ${fmt(p.start)}–${fmt(p.stop)}" onclick="openTimelineProgram('${esc(s.id)}',${p.start})"><b>${esc(p.title||'Programa sem título')}</b><small>${fmt(p.start)} – ${fmt(p.stop)}</small></button>`}).join('');const logo=s.logo?.enabled?`<img src="/api/logo?carrier_id=${encodeURIComponent(carrier.id)}&service_id=${encodeURIComponent(s.id)}" alt="Logo de ${esc(s.name)}">`:'';return `<div class="timeline-row"><div class="timeline-channel">${logo}<div><strong>${esc(s.name)}</strong><small class="muted">SID ${s.service_id}</small></div></div><div class="timeline-track">${trackNowLine}${blocks||'<div class="timeline-empty">Sem programação nesta janela</div>'}</div></div>`}).join('');el('timelineRange').textContent=`${new Date(timelineStart*1000).toLocaleDateString('pt-BR')} · ${fmt(timelineStart)} até ${fmt(end)}`;content.innerHTML=`<div class="timeline-board"><div class="timeline-axis"><div class="timeline-corner">Canal</div><div class="timeline-hours">${nowLine}${hours}</div></div>${rows||'<div class="empty">Nenhum canal cadastrado nesta portadora.</div>'}</div>`}
function shiftTimeline(seconds){timelineStart+=seconds;loadTimeline()}
function resetTimeline(){timelineStart=defaultTimelineStart();loadTimeline()}
function openTimelineProgram(serviceId,start){const g=guideCache[timelineCarrierId],service=g?.services.find(s=>s.id===serviceId),program=service?.schedule.find(p=>p.start===start);if(!service||!program)return;modal(`<div class="guide-head"><div><h2>${esc(program.title||'Programa sem título')}</h2><div class="muted">${esc(service.name)} · SID ${service.service_id}</div></div><button onclick="openTimeline('${esc(timelineCarrierId)}',${timelineStart})">Voltar à grade</button></div><div class="card" style="padding:18px;margin-top:16px"><b>${fmt(program.start)} — ${fmt(program.stop)}</b><p>${esc(program.description||'Sem descrição disponível.')}</p>${program.category?`<span class="badge">${esc(program.category)}</span>`:''}</div><div class="modal-actions"><button onclick="closeModal()">Fechar</button></div>`)}
async function togglePrograms(id){const row=el(`programs-${id}`),button=el(`programButton-${id}`),carrier=state.carriers.find(c=>c.id===id);if(!row||!carrier)return;if(expandedCarriers.has(id)){expandedCarriers.delete(id);delete guideCache[id];row.classList.remove('open');row.querySelector('.program-panel').innerHTML='';button.textContent='Ver programação';button.setAttribute('aria-expanded','false');return}expandedCarriers.add(id);row.classList.add('open');button.textContent='Ocultar programação';button.setAttribute('aria-expanded','true');const panel=row.querySelector('.program-panel');panel.innerHTML='<div class="muted">Carregando programação…</div>';try{guideCache[id]=await api(`/api/guide?carrier_id=${encodeURIComponent(id)}`);panel.innerHTML=programPanel(carrier,guideCache[id])}catch(e){expandedCarriers.delete(id);row.classList.remove('open');button.textContent='Ver programação';button.setAttribute('aria-expanded','false');toast(e.message,true)}}
function closeModal(){el('overlay').innerHTML=''}function modal(html,extraClass=''){const suffix=extraClass?` ${esc(extraClass)}`:'';el('overlay').innerHTML=`<div class="modal-back"><div class="modal${suffix}">${html}</div></div>`}
let serviceRowSequence=0;
async function ensureCatalogFor(select){const row=select.closest('.service-edit'),list=row?.querySelector('datalist'),sourceId=select.value||el('cSource')?.value;if(!list||!sourceId)return;list.innerHTML='<option value="Carregando…">';try{const channels=(await api(`/api/catalog?source_id=${encodeURIComponent(sourceId)}`)).channels;list.innerHTML=channels.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}catch(e){list.innerHTML='';toast(e.message,true)}}
function sourceOptions(selected=''){return `<option value="">Herdar fonte da portadora</option>${sources.map(item=>`<option value="${esc(item.id)}" ${item.id===selected?'selected':''}>${esc(item.name)}</option>`).join('')}`}
function categoryOptions(selected=''){return `<option value="">Usar categoria do XMLTV</option>${['Filmes','Notícias','Entretenimento','Esportes','Infantil','Música','Cultura','Sociedade','Educação','Lazer'].map(v=>`<option ${v===selected?'selected':''}>${v}</option>`).join('')}`}
function utcOffsetOptions(selected=-180){let options='';for(let minutes=-720;minutes<=840;minutes+=15)options+=`<option value="${minutes}" ${minutes===selected?'selected':''}>${utcOffsetLabel(minutes)}</option>`;return options}
function clockModeChanged(){const custom=el('cClockMode')?.value==='custom';for(const id of ['cClockOffset','cClockCorrection'])if(el(id))el(id).disabled=!custom;const help=el('clockHelp');if(help)help.textContent=custom?'O relógio continua avançando; a correção apenas soma ou subtrai minutos.':'Mantém exatamente o fluxo atual: UTC-03:00 e nenhuma correção.'}
function serviceRow(s={}){const listId=`channelList-${++serviceRowSequence}`,saved=Boolean(s.id),hasLogo=Boolean(s.id&&s.logo?.enabled),owner=hasLogo?state.carriers.find(c=>c.services.some(item=>item.id===s.id&&item.logo?.path===s.logo?.path))?.id||'':'',preview=hasLogo&&owner?`<img class="logo-preview" src="/api/logo?carrier_id=${encodeURIComponent(owner)}&service_id=${encodeURIComponent(s.id)}" alt="Logo de ${esc(s.name||'canal')}">`:'';return `<div class="service-edit"><label>Fonte do canal<select class="s-source" onchange="ensureCatalogFor(this)">${sourceOptions(s.source_id||'')}</select></label><label>Nome<input class="s-name" value="${esc(s.name||'')}"></label><label>ID XMLTV<input class="s-channel" list="${listId}" value="${esc(s.epg_channel_id||'')}"><datalist id="${listId}"></datalist></label><label>SID<input class="s-sid" type="number" min="1" max="65535" value="${s.service_id||1}"></label><label>Categoria padrão<select class="s-category">${categoryOptions(s.default_category||'')}</select><span class="logo-status">Usada apenas quando o XMLTV não informar uma categoria reconhecida.</span></label><label>Logo ISDB-TB<div class="logo-tools">${preview}<input class="s-logo-file" type="file" accept="image/png" style="display:none" onchange="uploadLogo(this)"><button type="button" ${saved?'':'disabled'} onclick="this.parentElement.querySelector('input').click()">${hasLogo?'Trocar':'Enviar PNG'}</button>${hasLogo?'<button type="button" class="danger" onclick="deleteLogo(this)">Remover</button>':''}</div><span class="logo-status">${hasLogo?`6 formatos ARIB · v${s.logo.logo_version}`:'Salve o canal antes do upload'}</span></label><button class="danger" onclick="this.parentElement.remove()">Remover</button><input class="s-id" type="hidden" value="${esc(s.id||'')}"></div>`}
function cloneCarrier(id){const original=state.carriers.find(x=>x.id===id);if(!original){toast('Portadora não encontrada',true);return}const copy={...original,id:'',name:`${original.name} - Cópia`,destination:'',auto_start:false,services:original.services.map(service=>({...service,id:''}))};openCarrier('',copy,true)}
async function openCarrier(id='',template=null,cloning=false){const c=template||state.carriers.find(x=>x.id===id)||{auto_start:true,source_id:sources.find(s=>s.is_default)?.id||sources[0]?.id||'',transport_stream_id:1,original_network_id:1,port:5012,pmt_pid:4096,bitrate:1000000,ttl:32,clock_mode:'standard',clock_utc_offset_minutes:-180,clock_correction_minutes:0,services:[{service_id:1}]};modal(`<h2>${cloning?'Clonar':id?'Editar':'Nova'} portadora EPG</h2>${cloning?'<p class="muted">Informe um novo multicast. A cópia será salva com inicialização manual e não altera a portadora original.</p>':''}<div class="form-grid"><input id="cId" type="hidden" value="${esc(c.id||'')}"><label class="wide">Nome<input id="cName" value="${esc(c.name||'')}"></label><label>Fonte padrão da portadora<select id="cSource">${sources.map(s=>`<option value="${esc(s.id)}" ${s.id===c.source_id?'selected':''}>${esc(s.name)}</option>`).join('')}</select></label><label>TSID<input id="cTsid" type="number" value="${c.transport_stream_id}"></label><label>ONID<input id="cOnid" type="number" value="${c.original_network_id}"></label><label>Multicast<input id="cDest" value="${esc(c.destination||'')}" placeholder="239.192.1.201" ${cloning?'autofocus':''}></label><label>Porta<input id="cPort" type="number" value="${c.port}"></label><label>IP da interface<input id="cIface" value="${esc(c.interface_address||'')}"></label><label>PID base PMT<input id="cPmt" type="number" value="${c.pmt_pid}"></label><label>Bitrate (bit/s)<input id="cBitrate" type="number" value="${c.bitrate}"></label><label>TTL<input id="cTtl" type="number" value="${c.ttl}"></label><label><span>Inicialização</span><select id="cAuto"><option value="1" ${c.auto_start?'selected':''}>Automática</option><option value="0" ${!c.auto_start?'selected':''}>Manual</option></select></label><label>Relógio PID 0x0014<select id="cClockMode" onchange="clockModeChanged()"><option value="standard" ${(c.clock_mode||'standard')==='standard'?'selected':''}>Padrão do sistema</option><option value="custom" ${c.clock_mode==='custom'?'selected':''}>Fuso e correção personalizados</option></select></label><label>Fuso transmitido<select id="cClockOffset">${utcOffsetOptions(c.clock_utc_offset_minutes??-180)}</select></label><label>Correção do horário (minutos)<input id="cClockCorrection" type="number" min="-1440" max="1440" step="1" value="${c.clock_correction_minutes||0}"></label><div id="clockHelp" class="muted wide"></div></div><p class="muted">Cada canal pode herdar esta fonte ou selecionar outro XMLTV. O ajuste do relógio também mantém a EIT coerente com TDT/TOT.</p><div class="toolbar" style="margin-top:20px"><h3>Serviços da portadora</h3><button onclick="addServiceRow()">+ Canal</button></div><div id="serviceRows">${c.services.map(serviceRow).join('')}</div><div class="modal-actions"><button onclick="closeModal()">Cancelar</button><button class="primary" onclick="saveCarrier()">Salvar</button></div>`);clockModeChanged();for(const select of document.querySelectorAll('.s-source'))await ensureCatalogFor(select)}
function addServiceRow(){el('serviceRows').insertAdjacentHTML('beforeend',serviceRow());ensureCatalogFor(el('serviceRows').lastElementChild.querySelector('.s-source'))}
async function saveCarrier(){try{const original=state.carriers.find(c=>c.id===el('cId').value),services=[...document.querySelectorAll('.service-edit')].map(r=>{const id=r.querySelector('.s-id').value,previous=original?.services.find(s=>s.id===id);return{id,name:r.querySelector('.s-name').value,source_id:r.querySelector('.s-source').value,epg_channel_id:r.querySelector('.s-channel').value,service_id:+r.querySelector('.s-sid').value,default_category:r.querySelector('.s-category').value,logo:previous?.logo||{}}});await api('/api/carriers',{method:'POST',body:JSON.stringify({id:el('cId').value,name:el('cName').value,source_id:el('cSource').value,auto_start:el('cAuto').value==='1',transport_stream_id:+el('cTsid').value,original_network_id:+el('cOnid').value,destination:el('cDest').value,port:+el('cPort').value,interface_address:el('cIface').value,pmt_pid:+el('cPmt').value,bitrate:+el('cBitrate').value,ttl:+el('cTtl').value,clock_mode:el('cClockMode').value,clock_utc_offset_minutes:+el('cClockOffset').value,clock_correction_minutes:+el('cClockCorrection').value,services})});closeModal();toast('Portadora salva');refresh()}catch(e){toast(e.message,true)}}
async function uploadLogo(input){const file=input.files?.[0],carrierId=el('cId').value,serviceId=input.closest('.service-edit').querySelector('.s-id').value;if(!file)return;if(file.type!=='image/png'){toast('Selecione um arquivo PNG',true);return}if(file.size>2*1024*1024){toast('O logo deve possuir no máximo 2 MiB',true);return}try{const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file)});await api('/api/carriers/logo',{method:'POST',body:JSON.stringify({carrier_id:carrierId,service_id:serviceId,data})});closeModal();toast('Logo ISDB-TB salvo em seis formatos');await refresh();openCarrier(carrierId)}catch(e){toast(e.message,true)}}
async function deleteLogo(button){if(!confirm('Remover o logo deste canal?'))return;const row=button.closest('.service-edit');try{const carrierId=el('cId').value;await api('/api/carriers/logo/delete',{method:'POST',body:JSON.stringify({carrier_id:carrierId,service_id:row.querySelector('.s-id').value})});closeModal();toast('Logo removido');await refresh();openCarrier(carrierId)}catch(e){toast(e.message,true)}}
async function actionCarrier(id,action){try{await api(`/api/carriers/${action}`,{method:'POST',body:JSON.stringify({id})});toast('Ação executada');setTimeout(refresh,400)}catch(e){toast(e.message,true)}}
async function deleteCarrier(id){if(!confirm('Excluir esta portadora?'))return;try{await api('/api/carriers/delete',{method:'POST',body:JSON.stringify({id})});toast('Portadora excluída');refresh()}catch(e){toast(e.message,true)}}
async function openGuide(carrierId,serviceId){try{const g=await api(`/api/guide?carrier_id=${encodeURIComponent(carrierId)}`),s=g.services.find(x=>x.id===serviceId);modal(`<div class="guide-head"><div><h2>${esc(s.name)}</h2><div class="muted">SID ${s.service_id} · ${esc(s.epg_channel_id)} · ${esc(g.timezone)}</div></div><button onclick="closeModal()">Fechar</button></div>${s.current?`<div class="card" style="padding:16px;margin-top:15px"><small>NO AR AGORA</small><h3>${esc(s.current.title)}</h3><p>${esc(s.current.description)}</p><b>${fmt(s.current.start)} — ${fmt(s.current.stop)}</b><div class="progress"><i style="width:${s.current.progress}%"></i></div></div>`:'<p class="muted">Nenhum programa identificado no ar.</p>'}<div class="guide-list">${s.schedule.map(p=>`<div class="guide-item ${p===s.current?'current':''}"><b>${fmt(p.start)}<br><span class="muted">${fmt(p.stop)}</span></b><div><strong>${esc(p.title)}</strong><div class="muted">${esc(p.category||p.description||'')}</div></div></div>`).join('')||'<p>Sem grade para hoje.</p>'}</div>`)}catch(e){toast(e.message,true)}}
async function openLogs(id){try{const j=await api(`/api/logs?carrier_id=${encodeURIComponent(id)}`);modal(`<h2>Logs do emissor</h2><pre style="background:#071b33;color:#dff4ff;padding:16px;border-radius:10px;max-height:65vh;overflow:auto;white-space:pre-wrap">${esc(j.log||'Sem logs.')}</pre><div class="modal-actions"><button onclick="closeModal()">Fechar</button></div>`)}catch(e){toast(e.message,true)}}
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
function openSources(){modal(`<div class="guide-head"><h2>Fontes XMLTV</h2><div class="actions"><button onclick="editSource()">+ Nova fonte</button><button onclick="closeModal()">Fechar</button></div></div><div class="guide-list">${sources.map(s=>`<div class="guide-item"><div><b>${s.is_default?'PADRÃO':'XMLTV'}</b></div><div><strong>${esc(s.name)}</strong><div class="muted">${esc(s.url)}</div><div class="actions" style="margin-top:8px"><button onclick="editSource('${esc(s.id)}')">Editar</button><button onclick="testSource('${esc(s.id)}')">Testar</button>${sources.length>1?`<button class="danger" onclick="deleteSource('${esc(s.id)}')">Excluir</button>`:''}</div></div></div>`).join('')}</div>`)}
function editSource(id=''){const s=sources.find(x=>x.id===id)||{};modal(`<h2>${id?'Editar':'Nova'} fonte XMLTV</h2><div class="form-grid"><input id="srcId" type="hidden" value="${esc(s.id||'')}"><label class="wide">Nome<input id="srcName" value="${esc(s.name||'')}"></label><label class="wide">URL HTTP/HTTPS<input id="srcUrl" value="${esc(s.url||'')}"></label><label><span>Fonte padrão</span><select id="srcDefault"><option value="0">Não</option><option value="1" ${s.is_default?'selected':''}>Sim</option></select></label></div><div class="modal-actions"><button onclick="openSources()">Voltar</button><button onclick="testSourceForm()">Testar</button><button class="primary" onclick="saveSource()">Salvar</button></div>`)}
async function saveSource(){try{await api('/api/sources',{method:'POST',body:JSON.stringify({id:el('srcId').value,name:el('srcName').value,url:el('srcUrl').value,is_default:el('srcDefault').value==='1'})});await refresh();openSources();toast('Fonte salva')}catch(e){toast(e.message,true)}}
async function testSourceForm(){try{const r=await api('/api/sources/test',{method:'POST',body:JSON.stringify({id:el('srcId').value,name:el('srcName').value,url:el('srcUrl').value})});toast(`${r.channels} canais e ${r.programmes} programas encontrados`)}catch(e){toast(e.message,true)}}
async function testSource(id){const s=sources.find(x=>x.id===id);try{const r=await api('/api/sources/test',{method:'POST',body:JSON.stringify(s)});toast(`${r.channels} canais e ${r.programmes} programas encontrados`)}catch(e){toast(e.message,true)}}
async function deleteSource(id){if(!confirm('Excluir esta fonte?'))return;try{await api('/api/sources/delete',{method:'POST',body:JSON.stringify({id})});await refresh();openSources()}catch(e){toast(e.message,true)}}
refresh();setInterval(refresh,15000);
</script></body></html>'''


def main() -> None:
    global APP
    bootstrap_user = os.environ.get("EPG_ADMIN_USER", "").strip()
    bootstrap_password = os.environ.get("EPG_ADMIN_PASSWORD", "")
    public_base_url = os.environ.get("EPG_PUBLIC_BASE_URL", "")
    data_dir = Path(os.environ.get("EPG_DATA_DIR", "/data"))
    binary = os.environ.get("EPG_EMITTER_BINARY", "/app/TVStreamEpgOnly")
    if not Path(binary).is_file():
        raise SystemExit(f"Emissor EPG não encontrado: {binary}")
    try:
        APP = Application(data_dir, binary, bootstrap_user, bootstrap_password, public_base_url)
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
        APP.supervisor.close()


if __name__ == "__main__":
    main()
