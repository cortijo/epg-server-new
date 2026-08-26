"""Fail-closed online license client used by the EPG control plane."""

from __future__ import annotations

import copy
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class LicenseError(Exception):
    pass


class LicenseManager:
    def __init__(self, server_url: str, key_file: str, installation_id: str,
                 check_seconds: int = 60, timeout: int = 5):
        self.server_url = server_url.strip().rstrip("/")
        self.key_file = Path(key_file) if key_file else None
        self.installation_id = installation_id.strip()
        self.check_seconds = max(10, min(3600, int(check_seconds)))
        self.timeout = max(1, min(30, int(timeout)))
        self.lock = threading.RLock()
        self.last_check_monotonic = 0.0
        self.status: dict[str, Any] = {
            "valid": False, "reason": "Licença não configurada", "max_channels": 0,
            "channel_count": 0, "checked_at": 0,
        }

    def _configuration_error(self) -> str:
        parsed = urllib.parse.urlparse(self.server_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "Servidor de licenças não configurado"
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", self.installation_id):
            return "Identificador da instalação não configurado"
        if not self.key_file or not self.key_file.is_file():
            return "Arquivo da chave de licença não encontrado"
        return ""

    def _key(self) -> str:
        assert self.key_file is not None
        value = self.key_file.read_text(encoding="utf-8").strip()
        if not value.startswith("EPG-") or len(value) < 44:
            raise LicenseError("Chave de licença inválida")
        return value

    def check(self, channel_count: int, force: bool = False) -> dict[str, Any]:
        with self.lock:
            now = time.monotonic()
            if not force and now - self.last_check_monotonic < self.check_seconds \
                    and int(self.status.get("channel_count", -1)) == int(channel_count):
                return copy.deepcopy(self.status)
            self.last_check_monotonic = now
            configuration_error = self._configuration_error()
            if configuration_error:
                self.status = {"valid": False, "reason": configuration_error,
                               "max_channels": 0, "channel_count": int(channel_count),
                               "checked_at": int(time.time())}
                return copy.deepcopy(self.status)
            try:
                payload = json.dumps({
                    "key": self._key(), "installation_id": self.installation_id,
                    "channel_count": int(channel_count),
                }, separators=(",", ":")).encode()
                request = urllib.request.Request(
                    self.server_url + "/api/validate", data=payload, method="POST",
                    headers={"Content-Type": "application/json", "User-Agent": "EPGStream-License/1"},
                )
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        result = json.load(response)
                except urllib.error.HTTPError as error:
                    result = json.loads(error.read().decode("utf-8", errors="replace"))
                if not isinstance(result, dict):
                    raise ValueError("resposta inválida")
                self.status = {
                    "valid": bool(result.get("valid")),
                    "reason": str(result.get("reason") or ("Licença válida" if result.get("valid") else "Licença recusada")),
                    "name": str(result.get("name") or ""),
                    "max_channels": int(result.get("max_channels", 0)),
                    "channel_count": int(channel_count),
                    "expires_at": int(result.get("expires_at", 0)),
                    "checked_at": int(result.get("checked_at", time.time())),
                }
            except Exception as error:
                self.status = {
                    "valid": False, "reason": f"Servidor de licenças indisponível: {error}",
                    "max_channels": 0, "channel_count": int(channel_count),
                    "checked_at": int(time.time()),
                }
            return copy.deepcopy(self.status)

    def require(self, channel_count: int, force: bool = False) -> dict[str, Any]:
        status = self.check(channel_count, force)
        if not status["valid"]:
            raise LicenseError(status["reason"])
        return status

    def public_status(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.status)
