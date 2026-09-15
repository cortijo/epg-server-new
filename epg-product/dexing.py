"""Idempotent HTTP integration for DeXin NDS3306I TS configuration."""

from __future__ import annotations

import http.cookiejar
import ipaddress
import json
import re
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class DexingError(RuntimeError):
    pass


@dataclass
class DexingClient:
    host: str
    username: str
    password: str
    scheme: str = "https"
    verify_tls: bool = False
    timeout: int = 12

    def __post_init__(self) -> None:
        try:
            ipaddress.ip_address(self.host)
        except ValueError as error:
            raise DexingError("O endereço do Dexing deve ser um IP válido") from error
        if self.scheme not in {"http", "https"}:
            raise DexingError("Protocolo do Dexing inválido")
        jar = http.cookiejar.CookieJar()
        handlers: list[Any] = [urllib.request.HTTPCookieProcessor(jar)]
        if self.scheme == "https":
            context = ssl.create_default_context() if self.verify_tls else ssl._create_unverified_context()
            handlers.append(urllib.request.HTTPSHandler(context=context))
        self._opener = urllib.request.build_opener(*handlers)
        self._base = f"{self.scheme}://{self.host}"

    def _post(self, path: str, fields: dict[str, Any], expect_json: bool = True) -> Any:
        request = urllib.request.Request(
            self._base + path,
            data=urllib.parse.urlencode(fields).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace").strip()
        except Exception as error:
            raise DexingError(f"Falha ao comunicar com o Dexing: {error}") from error
        if not expect_json:
            return body
        try:
            value = json.loads(body)
        except json.JSONDecodeError as error:
            raise DexingError(f"O Dexing retornou uma resposta inválida: {body[:160]}") from error
        if isinstance(value, dict) and value.get("error"):
            raise DexingError(str(value["error"]))
        return value

    def login(self) -> None:
        result = self._post("/login_process.php", {
            "action": "login", "username": self.username, "passwd": self.password,
        }, expect_json=False)
        if not result.lower().startswith("ok"):
            raise DexingError(f"Login recusado pelo Dexing: {result[:120]}")

    def inventory(self, output_index: int) -> dict[str, Any]:
        return self._post("/cgi.php?proctype=refreshPrg", {
            "op_code": 0, "tsout_ch_index": output_index,
            "dropdown_select": output_index, "tab_index": 1,
        })

    def general(self, output_index: int) -> dict[str, Any]:
        return self._post("/cgi.php?proctype=stream", {
            "op_code": 3, "tsout_ch_index": output_index,
            "dropdown_select": output_index, "tab_index": 2,
        })

    @staticmethod
    def transport_ids(value: Any) -> dict[str, int]:
        """Find TSID/ONID across firmware response wrappers and spelling variants."""
        found: dict[str, int] = {}
        aliases = {"tsid": "transport_stream_id", "ts_id": "transport_stream_id",
                   "transport_stream_id": "transport_stream_id", "onid": "original_network_id",
                   "on_id": "original_network_id", "original_network_id": "original_network_id"}
        def visit(item: Any) -> None:
            if isinstance(item, dict):
                for key, child in item.items():
                    normalized = re.sub(r"[^a-z0-9_]", "", str(key).lower())
                    if normalized in aliases and aliases[normalized] not in found:
                        try:
                            found[aliases[normalized]] = int(str(child), 0)
                        except (TypeError, ValueError):
                            pass
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)
        visit(value)
        return found

    @staticmethod
    def find_input(inventory: dict[str, Any], address: str, port: int) -> dict[str, Any] | None:
        pattern = re.compile(r"^IP(\d+)_Data(\d+)_([0-9.]+):(\d+)$")
        for item in inventory.get("tsin", []) if isinstance(inventory, dict) else []:
            match = pattern.match(str(item.get("input_name") or ""))
            if match and match.group(3) == address and int(match.group(4)) == int(port):
                result = dict(item)
                result.update({"input_channel": int(match.group(1)),
                               "input_index": int(match.group(1)) - 1,
                               "data_interface": int(match.group(2))})
                return result
        return None

    def add_input(self, address: str, port: int, data_interface: int) -> Any:
        # Defaults mirror the modal fields documented for mux_edit_input_ch.
        return self._post("/cgi.php?proctype=mux_edit_input_ch", {
            "op_code": 5, "data_interface": data_interface,
            "internal_interface": 0, "ch_step_en": 0, "step_channel": 1,
            "start_channel": 1, "end_channel": 1, "ip_bitrate_mode": 0,
            "ip_bitrate": 0, "ip_en": "on", "unicast": 0,
            "ipaddr": address, "step_en_ip": 0, "step_ip": 1,
            "ipaddr_end": address, "port": port, "step_en": 0,
            "step": 1, "end_port": port, "IGMPSnooping": "On",
            "record_type": 0, "sipaddr1": 0, "sipaddr2": 0,
            "sipaddr3": 0, "sipaddr4": 0, "protocol": "UDP",
            "backup_group": 0, "backup_status": 0, "backup_mode": 0,
            "service_id": 0,
        })

    def parse_program(self, output_index: int, input_index: int, timeout: int = 60) -> Any:
        return self._post("/cgi.php?proctype=refreshPrg", {
            "op_code": 3, "tsin_ch_index": input_index,
            "tsout_ch_index": output_index, "filt_input_ca": "on",
            "h_ParsePrg": 1, "time_out": timeout,
            "tab_index": 1, "dropdown_select": output_index,
        })

    def get_pid_rows(self, output_index: int) -> list[dict[str, Any]]:
        value = self._post("/cgi.php?proctype=pidpass", {
            "op_code": 3, "tsout_ch_index": output_index,
            "dropdown_select": output_index, "tab_index": 3,
        })
        rows: list[dict[str, Any]] = []
        for key in ("pid_info", "pidpass", "rows", "data"):
            candidate = value.get(key) if isinstance(value, dict) else None
            if isinstance(candidate, list):
                for row in candidate:
                    if isinstance(row, dict):
                        rows.append({"input_channel": row.get("iCh", row.get("input_channel")),
                                     "input_pid": row.get("iPID", row.get("input_pid")),
                                     "output_pid": row.get("oPID", row.get("output_pid"))})
                        rows[-1]["input_channel"] = row.get(
                            "from_input_channel", rows[-1]["input_channel"])
                        rows[-1]["input_pid"] = row.get("old_pid", rows[-1]["input_pid"])
                        rows[-1]["output_pid"] = row.get("new_pid", rows[-1]["output_pid"])
                break
        if not rows and isinstance(value, dict):
            count = int(value.get("pid_cnt") or 0)
            for index in range(1, count + 1):
                rows.append({"input_channel": value.get(f"iCh{index}"),
                             "input_pid": value.get(f"iPID{index}"),
                             "output_pid": value.get(f"oPID{index}")})
        return [row for row in rows if row["input_channel"] is not None]

    @staticmethod
    def _pid(value: Any) -> str:
        if isinstance(value, int):
            return f"0x{value:04X}"
        text = str(value or "").strip()
        try:
            return f"0x{int(text, 0):04X}"
        except ValueError:
            return text.upper()

    def set_pid_rows(self, output_index: int, rows: list[dict[str, Any]]) -> Any:
        fields: dict[str, Any] = {"op_code": 1, "pid_cnt": len(rows),
                                  "tsout_ch_index": output_index,
                                  "dropdown_select": output_index, "tab_index": 3}
        for index, row in enumerate(rows, 1):
            fields[f"iCh{index}"] = int(row["input_channel"])
            fields[f"iPID{index}"] = self._pid(row["input_pid"])
            fields[f"oPID{index}"] = self._pid(row["output_pid"])
        return self._post("/cgi.php?proctype=pidpass", fields)

    def ensure_pid_passthrough(self, output_index: int, input_channel: int,
                               pids: tuple[int, ...] = (0x0012, 0x0014)) -> dict[str, Any]:
        rows = self.get_pid_rows(output_index)
        existing = {(int(row["input_channel"]), self._pid(row["input_pid"]),
                     self._pid(row["output_pid"])) for row in rows}
        added = []
        for pid in pids:
            normalized = f"0x{pid:04X}"
            if (input_channel, normalized, normalized) not in existing:
                rows.append({"input_channel": input_channel, "input_pid": normalized,
                             "output_pid": normalized})
                added.append(normalized)
        if added:
            self.set_pid_rows(output_index, rows)
        return {"rows": rows, "added": added}

    @staticmethod
    def output_programs(inventory: dict[str, Any]) -> list[dict[str, Any]]:
        tsout = inventory.get("tsout", {}) if isinstance(inventory, dict) else {}
        return tsout.get("prg_info", []) if isinstance(tsout, dict) else []

    def synchronize(self, output_ts: int, address: str, port: int, data_interface: int,
                    expected_services: list[dict[str, Any]]) -> dict[str, Any]:
        output_index = output_ts - 1
        self.login()
        inventory = self.inventory(output_index)
        found = self.find_input(inventory, address, port)
        created = False
        if not found:
            self.add_input(address, port, data_interface)
            created = True
            inventory = self.inventory(output_index)
            found = self.find_input(inventory, address, port)
        if not found:
            raise DexingError("O input foi solicitado, mas não apareceu no inventário do Dexing")
        self.parse_program(output_index, found["input_index"])
        inventory = self.inventory(output_index)
        transport = self.transport_ids(self.general(output_index))
        pid_result = self.ensure_pid_passthrough(output_index, found["input_channel"])
        programs = self.output_programs(inventory)
        by_number = {int(item.get("program_number", item.get("prg_number", -1))): item
                     for item in programs}
        mapped = [{"service_id": int(service["service_id"]), "configured_name": service["name"],
                   "found": int(service["service_id"]) in by_number,
                   "dexing_name": by_number.get(int(service["service_id"]), {}).get("service_name", "")}
                  for service in expected_services]
        return {"input_created": created, "input_channel": found["input_channel"],
                "input_name": found.get("input_name", ""), "output_ts": output_ts,
                "pids_added": pid_result["added"], "pid_count": len(pid_result["rows"]),
                "programs": programs, "service_mapping": mapped, **transport}
