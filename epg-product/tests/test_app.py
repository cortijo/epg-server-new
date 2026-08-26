import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (
    ApiError, Application, INDEX_HTML, Store, Supervisor, parse_xmltv, parse_xmltv_datetime,
    normalize_uploaded_xmltv, password_matches, password_record,
    select_publication_version, validate_carrier,
)


class EpgProductTests(unittest.TestCase):
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
        payload = b'''<tv><channel id="0001 CANAL"><display-name>Canal</display-name></channel>
          <programme channel="0001 CANAL" start="20260825000000" stop="20260826000000"><title>Grade</title></programme></tv>'''
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
        self.assertIn('<button onclick="editSource()">+ Nova fonte</button>', INDEX_HTML)

    def test_timeline_guide_has_carrier_filter_and_navigation(self):
        self.assertIn('id="timelineButton" disabled onclick="openTimeline()"', INDEX_HTML)
        self.assertIn('el(\'timelineButton\').disabled=!(state.carriers||[]).length', INDEX_HTML)
        self.assertIn("function renderTimeline(g)", INDEX_HTML)
        self.assertIn("const TIMELINE_WINDOW=3*60*60,TIMELINE_STEP=90*60", INDEX_HTML)
        self.assertIn('id="timelineCarrier"', INDEX_HTML)
        self.assertIn('onclick="resetTimeline()">Agora</button>', INDEX_HTML)
        self.assertIn('class="timeline-program', INDEX_HTML)
        self.assertIn('class="timeline-now"', INDEX_HTML)
        self.assertIn("function openTimelineProgram(serviceId,start)", INDEX_HTML)

    def test_publications_ui_has_raw_upload_and_stable_url(self):
        self.assertIn('onclick="openPublications()">Publicações XMLTV', INDEX_HTML)
        self.assertIn("function publicationCard(p)", INDEX_HTML)
        self.assertIn("/api/publications/upload?id=", INDEX_HTML)
        self.assertIn("URL permanente", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
