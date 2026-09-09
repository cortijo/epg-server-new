import json
import io
import inspect
import tarfile
import tempfile
import threading
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (
    ApiError, Application, GuideCache, INDEX_HTML, Store, Supervisor, parse_xmltv, parse_xmltv_datetime,
    normalize_uploaded_xmltv, password_matches, password_record,
    parse_update_release, runtime_source_url, select_publication_version, validate_carrier,
    validate_source, validate_config_backup,
)
from license_client import LicenseError, LicenseManager


class EpgProductTests(unittest.TestCase):
    def test_parse_xml_normalizes_provider_without_channel_declarations(self):
        now = datetime.now(timezone.utc)
        current_start = now.strftime("%Y%m%d%H%M%S")
        current_stop = (now + timedelta(hours=1)).strftime("%Y%m%d%H%M%S")
        distant_start = (now + timedelta(days=20)).strftime("%Y%m%d%H%M%S")
        distant_stop = (now + timedelta(days=20, hours=1)).strftime("%Y%m%d%H%M%S")
        payload = f'''<tv>
          <programme channel="0071 CANAL" start="{current_start}" stop="{current_stop}"><title>Atual</title><desc>Sinopse</desc></programme>
          <programme channel="0071 CANAL" start="{distant_start}" stop="{distant_stop}"><title>Futuro</title><desc>Sinopse</desc></programme>
          <programme channel="0043 OUTRO" start="{current_start}" stop="{current_start}"><title>Inválido</title></programme>
        </tv>'''.encode()
        normalized, stats = normalize_uploaded_xmltv(payload, collect_errors=True)
        full = parse_xmltv(normalized, bounded=False)
        self.assertEqual(stats["channels_synthesized"], 1)
        self.assertEqual(stats["invalid_programmes_removed"], 1)
        self.assertEqual(stats["programmes"], 2)
        self.assertEqual(stats["invalid_programmes"][0]["title"], "Inválido")
        self.assertIn("Duração inválida", stats["invalid_programmes"][0]["reason"])
        self.assertEqual(len(full["channels"]), 1)
        self.assertEqual(sum(map(len, full["programmes"].values())), 2)
        self.assertIn(b" -0300", normalized)

    def test_parse_xml_cache_keeps_last_valid_normalization(self):
        source = validate_source({"name": "Operadora", "url": "http://provider/guide.xml",
                                  "source_type": "parse_xml"})
        payload = '''<tv>
          <programme channel="0001 TESTE" start="20260902000000" stop="20260903000000"><title>Grade</title></programme>
          <programme channel="0001 TESTE" start="20260902000000" stop="20260902000000"><title>Sem duração</title></programme>
        </tv>'''.encode()
        cache = GuideCache()
        with mock.patch.object(cache, "download", return_value=payload):
            first = cache.get(source, force=True)
        self.assertIn("normalized_payload", first)
        self.assertEqual(first["normalization"]["channels_synthesized"], 1)
        with mock.patch.object(cache, "download", side_effect=OSError("offline")):
            second = cache.get(source, force=True)
        self.assertIs(second, first)

    def test_guide_cache_exposes_source_list_sync_status(self):
        source = validate_source({"name": "Operadora", "url": "http://provider/guide.xml"})
        start = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S +0000")
        stop = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y%m%d%H%M%S +0000")
        payload = f'''<tv><channel id="sport"><display-name>Sport</display-name></channel>
          <programme channel="sport" start="{start}" stop="{stop}"><title>Jogo</title></programme></tv>'''.encode()
        cache = GuideCache()
        with mock.patch.object(cache, "download", return_value=payload), \
                mock.patch("app.time.time", return_value=1000):
            cache.get(source, force=True)
        self.assertEqual(cache.status(source), {
            "channel_count": 1, "programme_count": 1,
            "fetched_at": 1000, "next_refresh_at": 4600,
        })

    def test_guide_cache_sync_status_survives_restart_without_source_url(self):
        source = validate_source({"name": "Operadora", "url": "http://provider/guide.xml"})
        start = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S +0000")
        stop = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y%m%d%H%M%S +0000")
        payload = f'''<tv><channel id="sport"><display-name>Sport</display-name></channel>
          <programme channel="sport" start="{start}" stop="{stop}"><title>Jogo</title></programme></tv>'''.encode()
        with tempfile.TemporaryDirectory() as directory:
            cache = GuideCache(Path(directory))
            with mock.patch.object(cache, "download", return_value=payload), \
                    mock.patch("app.time.time", return_value=1000):
                cache.get(source, force=True)
            status = GuideCache(Path(directory)).status(source)
            self.assertEqual(status["channel_count"], 1)
            self.assertEqual(status["programme_count"], 1)
            status_files = list(Path(directory).glob("*.status.json"))
            self.assertEqual(len(status_files), 1)
            self.assertNotIn("provider", status_files[0].read_text(encoding="utf-8"))

    def test_parse_xml_cache_survives_application_restart(self):
        source = validate_source({"name": "Operadora", "url": "http://provider/guide.xml",
                                  "source_type": "parse_xml"})
        payload = '''<tv>
          <programme channel="0001 TESTE" start="20260902000000" stop="20260903000000"><title>Grade</title></programme>
          <programme channel="0001 TESTE" start="20260902000000" stop="20260902000000"><title>Sem duração</title></programme>
        </tv>'''.encode()
        with tempfile.TemporaryDirectory() as directory:
            first_cache = GuideCache(Path(directory))
            with mock.patch.object(first_cache, "download", return_value=payload):
                first_cache.get(source, force=True)
            second_cache = GuideCache(Path(directory))
            with mock.patch.object(second_cache, "download", side_effect=OSError("offline")):
                restored = second_cache.get(source, force=True)
            self.assertTrue(restored["normalization"]["persisted_fallback"])
            self.assertEqual(len(restored["channels"]), 1)
            self.assertEqual(restored["normalization"]["invalid_programmes"][0]["title"],
                             "Sem duração")

    def test_parse_xml_runtime_url_is_internal_and_token_is_not_in_apis(self):
        source = validate_source({"name": "Operadora", "url": "http://provider/guide.xml",
                                  "source_type": "parse_xml"})
        with mock.patch.dict("os.environ", {"EPG_HTTP_PORT": "9100"}):
            self.assertEqual(runtime_source_url(source),
                             f"http://127.0.0.1:9100/parsed-xml/{source['parse_token']}.xml")
        application_source = (Path(__file__).resolve().parents[1] / "app.py").read_text(
            encoding="utf-8")
        self.assertIn('if key != "parse_token"', application_source)
        self.assertIn('if key not in {"url", "parse_token"}', application_source)

    def test_parse_xml_source_type_is_available_in_ui(self):
        self.assertIn("Parse-XML (normalizar provedor)", INDEX_HTML)
        self.assertIn("sourceTestMessage", INDEX_HTML)

    def test_source_catalog_ui_reports_sync_and_parse_adjustments(self):
        self.assertIn("Ver canais e programação", INDEX_HTML)
        self.assertIn("Sincronizando canais…", INDEX_HTML)
        self.assertIn("Ajustes aplicados pelo Parse-XML", INDEX_HTML)
        self.assertIn("channels_synthesized", INDEX_HTML)
        self.assertIn("Buscar canal por nome ou ID XMLTV", INDEX_HTML)
        self.assertIn("Consultar ${errors.length} programa(s) inválido(s)", INDEX_HTML)
        self.assertIn("CANAIS SINCRONIZADOS", INDEX_HTML)
        self.assertIn("PROGRAMAS SINCRONIZADOS", INDEX_HTML)
        self.assertIn("ÚLTIMA ATUALIZAÇÃO", INDEX_HTML)
        self.assertIn("PRÓXIMA ATUALIZAÇÃO", INDEX_HTML)
        self.assertIn("sourceSyncSummary", INDEX_HTML)
        self.assertIn("Ainda não sincronizada nesta instalação", INDEX_HTML)

    def test_source_catalog_returns_channel_schedule_and_normalization(self):
        now = int(datetime.now(timezone.utc).timestamp())
        guide = {
            "fetched_at": now, "bytes": 123,
            "channels": {"sport": {"id": "sport", "name": "Sport", "icon": ""}},
            "programmes": {"sport": [{"channel_id": "sport", "start": now - 60,
                                        "stop": now + 600, "title": "Ao vivo",
                                        "subtitle": "", "description": "Jogo", "category": "Esportes"}]},
            "normalization": {"channels_synthesized": 1},
        }
        application = object.__new__(Application)
        application.guides = mock.Mock()
        application.guides.get.return_value = guide
        application.source = mock.Mock(return_value={"id": "source"})
        catalog = application.catalog("source", force=True)
        self.assertEqual(catalog["programme_count"], 1)
        self.assertEqual(catalog["channel_count"], 1)
        self.assertEqual(catalog["next_refresh_at"], now + 3600)
        self.assertEqual(catalog["cache_seconds"], 3600)
        self.assertEqual(catalog["channels"][0]["current"]["title"], "Ao vivo")
        self.assertEqual(len(catalog["channels"][0]["schedule"]), 1)
        self.assertEqual(catalog["normalization"]["channels_synthesized"], 1)
        application.guides.get.assert_called_once_with({"id": "source"}, True)

    def test_background_sync_refreshes_only_sources_due_after_60_minutes(self):
        application = object.__new__(Application)
        application.store = mock.Mock()
        application.store.snapshot.return_value = {
            "carriers": [], "sources": [{"id": "due"}, {"id": "fresh"}],
        }
        application.license = mock.Mock()
        application.license.check.return_value = {"valid": True}
        application.guides = mock.Mock()
        application.guides.lock = threading.RLock()
        application.guides.errors = {}
        application.guides.status.side_effect = [
            {"next_refresh_at": 999}, {"next_refresh_at": 5000},
        ]
        application.source_sync_stopping = threading.Event()
        with mock.patch("app.time.time", return_value=1000):
            result = application.sync_due_sources_once()
        self.assertEqual(result["synchronized"], 1)
        application.guides.get.assert_called_once_with({"id": "due"}, force=True)

    def test_background_sync_loads_persisted_status_source_after_restart(self):
        application = object.__new__(Application)
        application.store = mock.Mock()
        application.store.snapshot.return_value = {
            "carriers": [], "sources": [{"id": "source-1"}],
        }
        application.license = mock.Mock()
        application.license.check.return_value = {"valid": True}
        application.guides = mock.Mock()
        application.guides.lock = threading.RLock()
        application.guides.errors = {}
        application.guides.entries = {}
        application.guides.status.return_value = {"next_refresh_at": 5000}
        application.source_sync_stopping = threading.Event()
        with mock.patch("app.time.time", return_value=1000):
            result = application.sync_due_sources_once()
        self.assertEqual(result["synchronized"], 1)
        application.guides.get.assert_called_once_with({"id": "source-1"}, force=True)

    def test_download_rejects_provider_html_instead_of_xmltv(self):
        response = mock.MagicMock()
        response.headers = {"Content-Type": "text/html; charset=UTF-8"}
        response.read.side_effect = [b"<span>download limit reached</span>", b""]
        response.__enter__.return_value = response
        with mock.patch("app.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ApiError, "página HTML em vez de XMLTV"):
                GuideCache.download("https://epg.example/guide.xml")

    def test_background_sync_respects_source_failure_backoff(self):
        application = object.__new__(Application)
        application.store = mock.Mock()
        application.store.snapshot.return_value = {"carriers": [], "sources": [{"id": "limited"}]}
        application.license = mock.Mock()
        application.license.check.return_value = {"valid": True}
        application.guides = mock.Mock()
        application.guides.lock = threading.RLock()
        application.guides.errors = {"limited": {"next_retry_at": 5000}}
        application.guides.status.return_value = None
        application.source_sync_stopping = threading.Event()
        with mock.patch("app.time.time", return_value=1000):
            result = application.sync_due_sources_once()
        self.assertEqual(result["synchronized"], 0)
        application.guides.get.assert_not_called()

    def test_configuration_backup_restore_round_trip_and_local_safety_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            store = Store(data_dir / "epg-product.json")
            store.data["users"] = [{
                "id": "admin-1", "username": "admin", "display_name": "Administrador",
                "role": "admin", "enabled": True, "created_at": 1, "updated_at": 1,
                **password_record("password123"),
            }]
            store.save()
            application = object.__new__(Application)
            application.data_dir = data_dir
            application.store = store
            application.guides = GuideCache(data_dir / "parsed-xml-cache")
            normalized = data_dir / "parsed-xml-cache" / "source.xml"
            normalized.parent.mkdir(parents=True, exist_ok=True)
            normalized.write_text("<tv><channel id='teste'/></tv>", encoding="utf-8")
            payload = application.configuration_backup()
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                envelope = json.load(archive.extractfile("manifest.json"))
                self.assertEqual(archive.extractfile("data/parsed-xml-cache/source.xml").read(),
                                 normalized.read_bytes())
            self.assertEqual(envelope["format"], "epg-stream-config-backup")
            store.data["sources"] = []
            store.save()
            normalized.unlink()
            result = application.restore_configuration(payload)
            self.assertTrue(result["restart"])
            self.assertEqual(len(store.data["sources"]), 1)
            self.assertEqual(normalized.read_text(encoding="utf-8"),
                             "<tv><channel id='teste'/></tv>")
            self.assertEqual(len(list((data_dir / "config-backups").glob("pre-restore-*.tar.gz"))), 1)

    def test_configuration_restore_rejects_invalid_format_and_missing_admin(self):
        with self.assertRaises(ApiError):
            validate_config_backup(b'{}')
        invalid = {"format": "epg-stream-config-backup", "format_version": 1,
                   "data": {"users": [], "sources": [], "carriers": [],
                            "xmltv_publications": []}}
        with self.assertRaises(ApiError):
            validate_config_backup(json.dumps(invalid).encode())

    def test_configuration_restore_rejects_archive_path_traversal(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            content = b"unsafe"
            member = tarfile.TarInfo("data/../../outside.txt")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ApiError):
                Application._extract_configuration_backup(output.getvalue(), Path(directory))

    def test_configuration_backup_restore_ui_is_admin_only(self):
        self.assertIn("Baixar backup completo", INDEX_HTML)
        self.assertIn("Restaurar backup completo", INDEX_HTML)
        self.assertIn("configBackupButton','configRestoreButton", INDEX_HTML)
        self.assertIn("/api/config/backup", INDEX_HTML)
        self.assertIn("/api/config/restore", INDEX_HTML)

    def test_epg_health_aggregates_partial_and_total_failures(self):
        now = int(datetime.now(timezone.utc).timestamp())
        application = object.__new__(Application)
        application.store = mock.Mock()
        application.store.snapshot.return_value = {
            "sources": [{"id": "source-1", "name": "Provedor principal",
                         "url": "https://user:secret@epg.example/guide.xml?token=hidden"}],
            "users": [], "xmltv_publications": [],
            "carriers": [{
                "id": "carrier-1", "name": "Portadora 1", "source_id": "source-1",
                "services": [
                    {"id": "service-ok", "name": "Canal OK", "epg_channel_id": "OK",
                     "source_id": ""},
                    {"id": "service-bad", "name": "Canal sem grade", "epg_channel_id": "BAD",
                     "source_id": ""},
                ],
            }],
        }
        application.supervisor = mock.Mock()
        application.supervisor.state.return_value = {
            "carriers": [{"id": "carrier-1", "active": True}], "license": {"valid": True}}
        application.guides = GuideCache()
        application.guides.entries["source-1"] = {
            "channels": {"OK": "Canal OK", "BAD": "Canal sem grade"},
            "programmes": {"OK": [
                {"start": now - 60, "stop": now + 60, "title": "Atual"},
                {"start": now + 60, "stop": now + 120, "title": "Próximo"},
            ], "BAD": []}, "fetched_at": now,
        }
        health = application.epg_health()
        self.assertEqual(health["carriers"][0]["status"], "warning")
        self.assertEqual(health["carriers"][0]["failing"], 1)
        self.assertEqual(health["errors"][0]["code"], "no_programmes")
        self.assertEqual(health["errors"][0]["source_name"], "Provedor principal")
        self.assertEqual(health["errors"][0]["source_url"],
                         "https://epg.example/guide.xml?…")
        self.assertNotIn("secret", json.dumps(health))
        self.assertNotIn("hidden", json.dumps(health))

    def test_epg_health_ui_has_carrier_channel_and_error_center_indicators(self):
        self.assertIn("Central de erros do EPG", INDEX_HTML)
        self.assertIn("health-dot", INDEX_HTML)
        self.assertIn("Todos os canais com erro", INDEX_HTML)
        self.assertIn("epgHealthAlert", INDEX_HTML)
        self.assertIn("Fonte XMLTV", INDEX_HTML)

    def test_about_and_update_ui_are_available(self):
        self.assertIn('onclick="openAbout()">Sobre</button>', INDEX_HTML)
        self.assertIn("Developed by Julio Cortijo", INDEX_HTML)
        self.assertIn("async function checkUpdate()", INDEX_HTML)
        self.assertIn("async function applyUpdate(tag)", INDEX_HTML)

    def test_release_parser_requires_matching_deb_and_digest(self):
        payload = {
            "tag_name": "epg-native-v1.14.0-1",
            "html_url": "https://github.com/cortijo/epgserver2/releases/tag/epg-native-v1.14.0-1",
            "assets": [{
                "name": "epg-stream_1.14.0-1_amd64.deb",
                "browser_download_url": "https://github.com/cortijo/epgserver2/releases/download/tag/package.deb",
                "digest": "sha256:" + "a" * 64,
            }],
        }
        result = parse_update_release(payload, "cortijo/epgserver2", "amd64")
        self.assertEqual(result["latest_version"], "1.14.0")
        self.assertTrue(result["asset_available"])
        payload["assets"][0]["digest"] = ""
        with self.assertRaises(ApiError):
            parse_update_release(payload, "cortijo/epgserver2", "amd64")

    def test_license_default_interval_is_12_hours_and_allows_long_overrides(self):
        manager = LicenseManager("http://license.test:9200", "license.key", "install-001")
        self.assertEqual(manager.check_seconds, 43200)
        manager = LicenseManager(
            "http://license.test:9200", "license.key", "install-001", 86400)
        self.assertEqual(manager.check_seconds, 86400)
        manager = LicenseManager(
            "http://license.test:9200", "license.key", "install-001", 9999999)
        self.assertEqual(manager.check_seconds, 604800)

    def test_license_first_check_is_never_satisfied_by_empty_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "license.key"
            key_path.write_text("EPG-" + "a" * 48, encoding="utf-8")
            manager = LicenseManager(
                "http://license.test:9200", str(key_path), "install-001", 43200)
            expected = {
                "valid": True, "reason": "Licença válida", "name": "Teste",
                "max_channels": 10, "channel_count": 1, "expires_at": 0,
                "checked_at": 1,
            }
            with mock.patch.object(manager, "_validate_key", return_value=expected) as validate:
                status = manager.check(1)
            validate.assert_called_once()
            self.assertTrue(status["valid"])

    def test_license_client_is_fail_closed_and_never_exposes_key(self):
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "license.key"
            manager = LicenseManager("http://license.test:9200", str(key_path), "install-001", 60)
            self.assertFalse(manager.check(2, force=True)["valid"])
            key_path.write_text("EPG-" + "a" * 48, encoding="utf-8")
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            response.read.return_value = b'{"valid":true,"name":"Teste","max_channels":3,"channel_count":2,"checked_at":1}'
            with mock.patch("urllib.request.urlopen", return_value=response):
                status = manager.check(2, force=True)
            self.assertTrue(status["valid"])
            self.assertEqual(status["max_channels"], 3)
            self.assertNotIn("key", status)

    def test_license_client_rejects_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "license.key"
            key_path.write_text("EPG-" + "a" * 48, encoding="utf-8")
            manager = LicenseManager("http://license.test:9200", str(key_path), "install-001")
            error = __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
                "http://license.test:9200/api/validate", 403, "Forbidden", {},
                __import__("io").BytesIO(b'{"valid":false,"reason":"Quantidade de canais acima do limite contratado","max_channels":1}')
            )
            with mock.patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaises(LicenseError):
                    manager.require(2, force=True)

    def test_license_key_is_validated_before_atomic_install(self):
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "license.key"
            old_key = "EPG-" + "a" * 48
            new_key = "EPG-" + "b" * 48
            key_path.write_text(old_key + "\n", encoding="utf-8")
            manager = LicenseManager("http://license.test:9200", str(key_path), "install-001")
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            response.read.return_value = b'{"valid":true,"name":"Teste","max_channels":10,"checked_at":1}'
            with mock.patch("urllib.request.urlopen", return_value=response):
                status = manager.install_key(new_key, 2)
            self.assertTrue(status["valid"])
            self.assertEqual(key_path.read_text(encoding="utf-8"), new_key + "\n")
            self.assertNotIn("key", status)

            error = __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
                "http://license.test:9200/api/validate", 403, "Forbidden", {},
                __import__("io").BytesIO(
                    '{"valid":false,"reason":"Chave não reconhecida"}'.encode("utf-8"))
            )
            with mock.patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaisesRegex(LicenseError, "não reconhecida"):
                    manager.install_key("EPG-" + "c" * 48, 2)
            self.assertEqual(key_path.read_text(encoding="utf-8"), new_key + "\n")

    def test_xmltv_timezone_and_schedule(self):
        parsed = parse_xmltv_datetime("20260824120000 -0300")
        self.assertEqual(parsed, datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc))
        reference = datetime.now(timezone.utc)
        start = reference.strftime("%Y%m%d%H%M%S +0000")
        stop = reference.replace(microsecond=0).timestamp() + 3600
        stop_text = datetime.fromtimestamp(stop, timezone.utc).strftime("%Y%m%d%H%M%S +0000")
        payload = f'''<?xml version="1.0"?><tv>
          <channel id="canal.br"><display-name>Canal Brasil</display-name></channel>
          <programme channel="canal.br" start="{start}" stop="{stop_text}">
            <title>Jornal</title><desc>Noticias do dia</desc><category>Noticias</category>
          </programme></tv>'''.encode()
        guide = parse_xmltv(payload)
        self.assertEqual(guide["channels"]["canal.br"]["name"], "Canal Brasil")
        self.assertEqual(guide["programmes"]["canal.br"][0]["title"], "Jornal")

    def test_carrier_validation_and_multiple_services(self):
        carrier = validate_carrier({
            "name": "Portadora 72", "source_id": "source", "destination": "239.192.1.201",
            "port": 5012, "interface_address": "10.0.0.10", "transport_stream_id": 72,
            "original_network_id": 72, "pmt_pid": 4096, "bitrate": 1000000, "ttl": 32,
            "services": [
                {"name": "Canal A", "epg_channel_id": "a.br", "service_id": 2301},
                {"name": "Canal B", "epg_channel_id": "b.br", "service_id": 2302},
            ],
        })
        self.assertEqual(len(carrier["services"]), 2)
        self.assertEqual(carrier["transport_stream_id"], 72)
        self.assertEqual(carrier["clock_mode"], "standard")
        self.assertEqual(carrier["clock_utc_offset_minutes"], -180)
        self.assertEqual(carrier["clock_correction_minutes"], 0)

    def test_custom_carrier_clock_is_validated_and_forwarded(self):
        carrier = validate_carrier({
            "name": "Portadora relógio", "source_id": "source",
            "destination": "239.192.1.212", "port": 5012,
            "interface_address": "10.0.0.10", "clock_mode": "custom",
            "clock_utc_offset_minutes": -240, "clock_correction_minutes": 30,
            "services": [{"name": "Canal", "epg_channel_id": "canal.br",
                          "service_id": 101}],
        })
        self.assertEqual(carrier["clock_utc_offset_minutes"], -240)
        self.assertEqual(carrier["clock_correction_minutes"], 30)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "epg-product.json")
            store.data["sources"] = [{"id": "source", "name": "Fonte",
                                      "url": "https://example.test/guide.xml",
                                      "is_default": True}]
            supervisor = Supervisor(store, "/bin/false", Path(directory) / "logs")
            environment = supervisor._environment(carrier, store.data["sources"][0])
            self.assertEqual(environment["EPG_CLOCK_UTC_OFFSET_MINUTES"], "-240")
            self.assertEqual(environment["EPG_CLOCK_CORRECTION_SECONDS"], "1800")
        self.assertIn('id="cClockMode"', INDEX_HTML)
        self.assertIn('id="cClockOffset"', INDEX_HTML)
        self.assertIn('id="cClockCorrection"', INDEX_HTML)

    def test_custom_carrier_clock_rejects_invalid_values(self):
        base = {
            "name": "Portadora relógio", "source_id": "source",
            "destination": "239.192.1.213", "port": 5012,
            "interface_address": "10.0.0.10", "clock_mode": "custom",
            "services": [{"name": "Canal", "epg_channel_id": "canal.br",
                          "service_id": 101}],
        }
        with self.assertRaises(ApiError):
            validate_carrier({**base, "clock_utc_offset_minutes": -181})
        with self.assertRaises(ApiError):
            validate_carrier({**base, "clock_correction_minutes": 1441})
        with self.assertRaises(ApiError):
            validate_carrier({**base, "clock_mode": "congelado"})

    def test_start_advances_and_persists_signal_version(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "epg-product.json")
            source = {"id": "source", "name": "Fonte",
                      "url": "https://example.test/guide.xml", "is_default": True}
            carrier = validate_carrier({
                "id": "carrier-version", "name": "Portadora versionada",
                "source_id": "source", "destination": "239.192.1.220", "port": 5012,
                "interface_address": "10.0.0.10",
                "services": [{"name": "Canal", "epg_channel_id": "canal.br",
                              "service_id": 101}],
            })
            carrier["signalling_version"] = 31
            store.data["sources"] = [source]
            store.data["carriers"] = [carrier]
            store.save()
            license_manager = mock.MagicMock()
            supervisor = Supervisor(
                store, "/bin/false", Path(directory) / "logs", license_manager)
            process = mock.MagicMock()
            process.pid = 1234
            process.poll.return_value = None
            with mock.patch("app.subprocess.Popen", return_value=process) as popen:
                supervisor._start_locked(store.snapshot()["carriers"][0])
            license_manager.require.assert_called_once_with(1)
            environment = popen.call_args.kwargs["env"]
            self.assertEqual(environment["EPG_SIGNAL_VERSION"], "0")
            self.assertEqual(store.snapshot()["carriers"][0]["signalling_version"], 0)
            supervisor._stop_locked("carrier-version")

    def test_epg_injector_uses_persistent_signal_version(self):
        root = Path(__file__).resolve().parents[2]
        injector = (root / "src" / "EpgInjector.cpp").read_text(encoding="utf-8")
        emitter = (root / "src" / "EpgOnlyMain.cpp").read_text(encoding="utf-8")
        self.assertIn("config.epgSignalVersion & 0x1F", injector)
        self.assertIn("config.epgSignalVersion = carrier.signalVersion", emitter)
        self.assertIn("makePatSection(config.transportStreamId, config.services, pmtPid,", emitter)
        self.assertIn("makePmtSection(service.serviceId, config.signalVersion)", emitter)
        auditor = (root / "scripts" / "verify_isdbtb_ts.py").read_text(encoding="utf-8")
        self.assertIn('"eit_versions": sorted(eit_versions)', auditor)

    def test_channel_category_fallback_is_validated_and_rendered(self):
        carrier = validate_carrier({
            "name": "Portadora esporte", "source_id": "source",
            "destination": "239.192.1.210", "port": 5012,
            "interface_address": "10.0.0.10",
            "services": [{"name": "SPORTV", "epg_channel_id": "sportv.br",
                          "service_id": 2304, "default_category": "Esportes"}],
        })
        self.assertEqual(carrier["services"][0]["default_category"], "Esportes")
        self.assertIn('class="s-category"', INDEX_HTML)
        self.assertIn("Usada apenas quando o XMLTV não informar", INDEX_HTML)
        with self.assertRaises(ApiError):
            validate_carrier({
                "name": "Inválida", "source_id": "source",
                "destination": "239.192.1.211", "port": 5012,
                "interface_address": "10.0.0.10",
                "services": [{"name": "Canal", "epg_channel_id": "canal.br",
                              "service_id": 1, "default_category": "Qualquer coisa"}],
            })

    def test_carrier_rejects_duplicate_service_ids(self):
        with self.assertRaises(ApiError):
            validate_carrier({
                "name": "Portadora", "source_id": "source", "destination": "239.1.1.1",
                "port": 5012, "interface_address": "10.0.0.10",
                "services": [
                    {"name": "A", "epg_channel_id": "a", "service_id": 10},
                    {"name": "B", "epg_channel_id": "b", "service_id": 10},
                ],
            })

    def test_store_is_created_and_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "epg-product.json"
            store = Store(path)
            self.assertTrue(path.exists())
            store.data["carriers"].append({"id": "one"})
            store.save()
            self.assertEqual(Store(path).data["carriers"][0]["id"], "one")
            json.loads(path.read_text(encoding="utf-8"))

    def test_provider_xmltv_is_normalized_for_both_parsers(self):
        start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=1)
        stop = start + timedelta(hours=2)
        start_text = start.strftime("%Y%m%d%H%M%S")
        stop_text = stop.strftime("%Y%m%d%H%M%S")
        payload = f'''<?xml version="1.0" encoding="UTF-8"?><tv>
          <channel id="0121 TELECINE PREMIUM"><display-name>Telecine Premium</display-name></channel>
          <channel id="0121 TELECINE PREMIUM"><display-name>Duplicado</display-name></channel>
          <programme channel="0121 TC PREMIUM" start="{start_text}" stop="{stop_text}"><title>Filme</title></programme>
          <programme channel="0121 TC PREMIUM" start="{stop_text}" stop="{stop_text}"><title>Invalido</title></programme>
        </tv>'''.encode("utf-8")
        normalized, stats = normalize_uploaded_xmltv(payload)
        root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(normalized)
        channels = root.findall("channel")
        programmes = root.findall("programme")
        self.assertEqual([item.get("id") for item in channels], ["0121 TELECINE PREMIUM"])
        self.assertEqual(len(programmes), 1)
        self.assertEqual(programmes[0].get("channel"), "0121 TELECINE PREMIUM")
        self.assertEqual(programmes[0].get("start"), start_text + " -0300")
        self.assertEqual(programmes[0].get("stop"), stop_text + " -0300")
        self.assertEqual(stats["channel_refs_rewritten"], 2)
        self.assertEqual(stats["duplicate_channels_removed"], 1)
        self.assertEqual(stats["invalid_programmes_removed"], 1)
        self.assertEqual(stats["timezone_added"], 2)
        self.assertEqual(sum(map(len, parse_xmltv(normalized)["programmes"].values())), 1)

    def test_publication_version_rotation_keeps_one_stable_feed(self):
        versions = [
            {"id": "old", "valid_from": 100, "valid_until": 200, "uploaded_at": 1},
            {"id": "current", "valid_from": 200, "valid_until": 300, "uploaded_at": 2},
            {"id": "next", "valid_from": 300, "valid_until": 400, "uploaded_at": 3},
        ]
        self.assertEqual(select_publication_version(versions, 250)["id"], "current")
        self.assertEqual(select_publication_version(versions, 50)["id"], "old")
        self.assertEqual(select_publication_version(versions, 450)["id"], "next")

    def test_publication_upload_persists_normalized_version_and_stable_token(self):
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        stop = start + timedelta(days=1)
        payload = f'''<tv><channel id="0001 CANAL"><display-name>Canal</display-name></channel>
          <programme channel="0001 CANAL" start="{start.strftime('%Y%m%d%H%M%S')}" stop="{stop.strftime('%Y%m%d%H%M%S')}"><title>Grade</title></programme></tv>'''.encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            app = Application.__new__(Application)
            app.data_dir = Path(directory)
            app.publication_dir = Path(directory) / "xmltv-publications"
            app.public_base_url = "http://epg.example:9100"
            app.publication_dir.mkdir()
            app.store = Store(Path(directory) / "epg-product.json")
            created = app.save_publication({"name": "Programadora"})
            first_path = created["public_path"]
            uploaded = app.upload_publication(created["id"], "guide.xml", payload)
            self.assertEqual(uploaded["version"]["programmes"], 1)
            listed = app.publications()[0]
            self.assertEqual(listed["public_path"], first_path)
            self.assertEqual(listed["public_url"], "http://epg.example:9100" + first_path)
            self.assertNotIn("path", listed["versions"][0])
            token = first_path.rsplit("/", 1)[-1].removesuffix(".xml")
            served, metadata = app.publication_payload(token)
            self.assertIn(b" -0300", served)
            self.assertEqual(metadata["filename"], "guide.xml")

    def test_password_is_hashed_and_verified(self):
        record = password_record("Senha-forte-123")
        self.assertNotIn("Senha-forte-123", json.dumps(record))
        self.assertTrue(password_matches(record, "Senha-forte-123"))
        self.assertFalse(password_matches(record, "Senha-incorreta"))

    def test_user_migration_creation_and_password_change(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application.__new__(Application)
            app.store = Store(Path(directory) / "epg-product.json")
            app._bootstrap_user("epgadmin", "Senha-inicial-123")
            admin = app.authenticate("EPGADMIN", "Senha-inicial-123")
            self.assertEqual(admin["role"], "admin")
            self.assertNotIn("Senha-inicial-123", app.store.path.read_text(encoding="utf-8"))
            created = app.save_user({
                "username": "operador.1", "display_name": "Operador Um",
                "role": "operator", "enabled": True, "password": "Senha-operador-123",
            })["user"]
            self.assertEqual(app.authenticate("operador.1", "Senha-operador-123")["id"], created["id"])
            app.save_user({**created, "password": "Senha-nova-456"})
            self.assertIsNone(app.authenticate("operador.1", "Senha-operador-123"))
            self.assertIsNotNone(app.authenticate("operador.1", "Senha-nova-456"))

    def test_last_active_admin_is_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application.__new__(Application)
            app.store = Store(Path(directory) / "epg-product.json")
            app._bootstrap_user("epgadmin", "Senha-inicial-123")
            admin = app.authenticate("epgadmin", "Senha-inicial-123")
            with self.assertRaises(ApiError):
                app.save_user({**admin, "enabled": False, "password": ""})
            with self.assertRaises(ApiError):
                app.save_user({**admin, "role": "operator", "password": ""})
            with self.assertRaises(ApiError):
                app.delete_user(admin["id"], admin["id"])

    def test_user_form_only_closes_by_explicit_action(self):
        self.assertNotIn("event.target===this", INDEX_HTML)
        self.assertIn('<div class="modal-back"><div class="modal${suffix}">', INDEX_HTML)
        self.assertIn('<button onclick="closeModal()">Cancelar</button>', INDEX_HTML)
        self.assertIn("closeModal();toast('Usuário salvo');refresh()", INDEX_HTML)

    def test_carriers_use_lazy_expanding_table(self):
        self.assertIn('class="carrier-table"', INDEX_HTML)
        self.assertIn('class="actions-col">Ações', INDEX_HTML)
        self.assertIn("function togglePrograms(id)", INDEX_HTML)
        self.assertIn("Ver programação", INDEX_HTML)
        self.assertIn("delete guideCache[id]", INDEX_HTML)
        self.assertNotIn("function loadNow()", INDEX_HTML)

    def test_carrier_clone_is_safe_and_requires_new_destination(self):
        self.assertIn("function cloneCarrier(id)", INDEX_HTML)
        self.assertIn(">Clonar</button>", INDEX_HTML)
        self.assertIn("destination:'',auto_start:false", INDEX_HTML)
        self.assertIn("services:original.services.map(service=>({...service,id:''}))", INDEX_HTML)
        self.assertIn("não altera a portadora original", INDEX_HTML)

    def test_xmltv_sources_has_explicit_close_button(self):
        self.assertIn('<h2>Fontes XMLTV</h2><div class="actions">', INDEX_HTML)
        self.assertIn('<button onclick="closeModal()">Fechar</button>', INDEX_HTML)
        self.assertIn("Histórico de sincronização", INDEX_HTML)
        self.assertIn("function openSourceHistory(sourceId='')", INDEX_HTML)
        self.assertIn('<button onclick="editSource()">+ Nova fonte</button>', INDEX_HTML)

    def test_timeline_guide_is_consolidated_and_searchable(self):
        self.assertIn('id="timelineButton" disabled onclick="openTimeline()"', INDEX_HTML)
        self.assertIn("el('timelineButton').disabled=!valid||!(state.carriers||[]).length", INDEX_HTML)
        self.assertIn("function renderTimeline(g)", INDEX_HTML)
        self.assertIn("const TIMELINE_STEP=2*3600", INDEX_HTML)
        self.assertIn('/api/guides?start=', INDEX_HTML)
        self.assertNotIn("self.guides.get", inspect.getsource(Application.all_guides))
        self.assertNotIn("_load_persisted", inspect.getsource(Application.all_guides))
        self.assertIn('id="timelineCategory"', INDEX_HTML)
        self.assertIn('id="timelineChannelSearch"', INDEX_HTML)
        self.assertIn('id="timelineProgramSearch"', INDEX_HTML)
        self.assertIn("function timelineDayButtons()", INDEX_HTML)
        self.assertIn("function zoomTimeline(direction)", INDEX_HTML)
        self.assertIn('onclick="resetTimeline()">Agora</button>', INDEX_HTML)
        self.assertIn('class="timeline-program', INDEX_HTML)
        self.assertIn('class="timeline-now"', INDEX_HTML)
        self.assertIn("function openTimelineProgram(rowId,start)", INDEX_HTML)

    def test_logo_controls_and_previews_are_hidden_but_backend_is_preserved(self):
        self.assertNotIn("Logo ISDB-TB<div", INDEX_HTML)
        self.assertNotIn('class="s-logo-file"', INDEX_HTML)
        self.assertNotIn('src="/api/logo?carrier_id=', INDEX_HTML)
        self.assertIn("async function uploadLogo(input)", INDEX_HTML)
        self.assertIn("async function deleteLogo(button)", INDEX_HTML)

    def test_license_status_is_visible_in_panel(self):
        self.assertIn('id="licenseMetric"', INDEX_HTML)
        self.assertIn('id="mLicense"', INDEX_HTML)
        self.assertIn("state.license?.valid", INDEX_HTML)
        self.assertIn('id="licenseButton"', INDEX_HTML)
        self.assertIn("function openLicense()", INDEX_HTML)
        self.assertIn("async function saveLicenseKey()", INDEX_HTML)
        self.assertIn("/api/license/key", INDEX_HTML)
        self.assertIn("el('licenseButton').style.display=admin?'':'none'", INDEX_HTML)
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn('elif path == "/api/license/key":\n                self._require_admin()', source)

    def test_invalid_license_blocks_management_ui_and_backend(self):
        self.assertIn('id="licenseAlert"', INDEX_HTML)
        self.assertIn('Licença inválida, entre em contato com o suporte', INDEX_HTML)
        for control in ["publicationsButton", "sourcesButton", "timelineButton",
                        "restartAllButton", "newCarrierButton"]:
            self.assertIn(control, INDEX_HTML)
        self.assertIn("for(const id of ['publicationsButton','sourcesButton','timelineButton','restartAllButton','newCarrierButton','tvSimulatorButton'])el(id).disabled=!valid", INDEX_HTML)

    def test_tv_simulator_ui_and_backend_are_wired(self):
        self.assertIn("Simular TV / PIDs", INDEX_HTML)
        self.assertIn("function openTvSimulator()", INDEX_HTML)
        self.assertIn("/api/carriers/audit", INDEX_HTML)
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn('"EPG_DIAGNOSTIC_DIR": str(self.diagnostic_dir)', source)
        self.assertIn("def audit(self, carrier_id", source)
        self.assertIn("const valid=!!state.license?.valid", INDEX_HTML)
        self.assertIn("disabled=state.license?.valid?'':' disabled'", INDEX_HTML)
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn("def _require_license(self) -> None:", source)
        self.assertIn('elif path == "/api/carriers/restart-all":', source)
        self.assertIn("self._require_license()", source)

    def test_isdbtb_keeps_title_in_0x4d_and_synopsis_in_0x4e(self):
        source = (Path(__file__).resolve().parents[2] / "src" / "EpgInjector.cpp").read_text(
            encoding="utf-8")
        self.assertIn("profile == EpgProfile::IsdbTb\n        ? std::string()", source)
        self.assertIn("appendExtendedEventDescriptors(descriptor, event, 0);", source)
        self.assertIn("descriptor.push_back(0x4D);", source)

    def test_restart_all_only_restarts_eligible_flows(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "epg-product.json")
            store.data["carriers"] = [
                {"id": "running", "auto_start": True, "services": [{}]},
                {"id": "manual", "auto_start": False, "services": [{}]},
            ]
            license_manager = mock.MagicMock()
            supervisor = Supervisor(
                store, "/bin/false", Path(directory) / "logs", license_manager)
            supervisor.runtime = {
                "running": {"manual_stop": False},
                "manual": {"manual_stop": True},
            }
            started = []
            supervisor._start_locked = lambda carrier: started.append(carrier["id"])
            result = supervisor.restart_all()
            license_manager.require.assert_called_once_with(2, force=True)
            self.assertEqual(started, ["running"])
            self.assertEqual(result, {"result": "ok", "restarted": 1, "errors": []})

    def test_restart_all_ui_requires_confirmation(self):
        self.assertIn('onclick="restartAllCarriers()"', INDEX_HTML)
        self.assertIn("confirm('Reiniciar agora todos os fluxos que deveriam estar ativos?')", INDEX_HTML)
        self.assertIn("/api/carriers/restart-all", INDEX_HTML)

    def test_publications_ui_has_raw_upload_and_stable_url(self):
        self.assertIn('onclick="openPublications()">Publicações XMLTV', INDEX_HTML)
        self.assertIn("function publicationCard(p)", INDEX_HTML)
        self.assertIn("/api/publications/upload?id=", INDEX_HTML)
        self.assertIn("URL permanente", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
