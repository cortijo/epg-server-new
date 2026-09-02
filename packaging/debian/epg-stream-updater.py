#!/usr/bin/env python3
"""Privileged updater for official, digest-pinned EPG Stream Debian releases."""

import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


DATA_DIR = Path(os.environ.get("EPG_DATA_DIR", "/var/lib/epg-stream"))
REPOSITORY = os.environ.get("EPG_UPDATE_REPOSITORY", "cortijo/epgserver2")
REQUEST = DATA_DIR / "update-request.json"
STATUS = DATA_DIR / "update-status.json"
ARCH = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
TOKEN_FILE = Path(os.environ.get("EPG_UPDATE_TOKEN_FILE", ""))


def write_status(status: str, message: str, tag: str = "") -> None:
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps({"status": status, "message": message, "tag": tag,
                                     "updated_at": int(time.time())}, ensure_ascii=False), encoding="utf-8")
    os.chmod(temporary, 0o640)
    os.replace(temporary, STATUS)


def main() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("o atualizador precisa ser executado como root")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", REPOSITORY):
        raise RuntimeError("repositório inválido")
    request = json.loads(REQUEST.read_text(encoding="utf-8"))
    tag = str(request.get("tag") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", tag):
        raise RuntimeError("tag solicitada inválida")
    REQUEST.unlink()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "EPGStreamUpdater/1.0"}
    if str(TOKEN_FILE) and TOKEN_FILE.is_file():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    api = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{tag}",
        headers=headers,
    )
    with urllib.request.urlopen(api, timeout=20) as response:
        release = json.loads(response.read(1024 * 1024))
    asset = next((item for item in release.get("assets", [])
                  if str(item.get("name") or "").startswith("epg-stream_")
                  and str(item.get("name") or "").endswith(f"_{ARCH}.deb")), None)
    if not asset:
        raise RuntimeError(f"release sem pacote para {ARCH}")
    digest = str(asset.get("digest") or "").lower()
    url = str(asset.get("browser_download_url") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or not url.startswith("https://github.com/"):
        raise RuntimeError("pacote sem digest SHA-256 ou URL oficial")
    expected = digest.split(":", 1)[1]
    with tempfile.TemporaryDirectory(prefix="epg-update-") as directory:
        package = Path(directory) / str(asset["name"])
        sha256 = hashlib.sha256()
        download = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(download, timeout=60) as response, package.open("wb") as output:
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > 200 * 1024 * 1024:
                    raise RuntimeError("pacote excede 200 MiB")
                sha256.update(block)
                output.write(block)
        if sha256.hexdigest() != expected:
            raise RuntimeError("SHA-256 do pacote não confere")
        fields = subprocess.check_output(
            ["dpkg-deb", "-f", str(package), "Package", "Version", "Architecture"], text=True
        ).splitlines()
        if len(fields) != 3 or fields[0] != "epg-stream" or fields[2] != ARCH:
            raise RuntimeError("metadados do pacote não conferem")
        release_version = re.search(r"(\d+\.\d+\.\d+)", tag)
        if not release_version or not fields[1].startswith(release_version.group(1) + "-"):
            raise RuntimeError("versão do pacote diverge da release")
        subprocess.run(["apt-get", "install", "-y", str(package)], check=True)
    write_status("success", f"Versão {fields[1]} instalada", tag)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        write_status("error", str(error))
        raise
