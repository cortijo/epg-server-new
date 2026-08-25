import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (
    ApiError, Application, INDEX_HTML, Store, parse_xmltv, parse_xmltv_datetime,
    password_matches, password_record, validate_carrier,
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


if __name__ == "__main__":
    unittest.main()
