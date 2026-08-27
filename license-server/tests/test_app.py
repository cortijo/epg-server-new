import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import LicenseAuthority, Store


class LicenseServerTests(unittest.TestCase):
    def authority(self, directory):
        return LicenseAuthority(
            Store(Path(directory) / "licenses.json", "licenseadmin", "Senha-segura-123"),
            b"x" * 32,
        )

    def test_key_is_recoverable_but_only_hash_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            authority = self.authority(directory)
            created = authority.save_license({"name": "Cliente", "max_channels": 10})
            self.assertTrue(created["key"].startswith("EPG-"))
            payload = Path(directory, "licenses.json").read_text(encoding="utf-8")
            self.assertNotIn(created["key"], payload)
            self.assertEqual(authority.view_key(created["license"]["id"])["key"], created["key"])
            edited = authority.save_license({**created["license"], "max_channels": 20})
            self.assertNotIn("key", edited)

    def test_legacy_key_requires_rotation_and_old_key_stops_working(self):
        with tempfile.TemporaryDirectory() as directory:
            authority = self.authority(directory)
            created = authority.save_license({"name": "Cliente", "max_channels": 3})
            old_key = "EPG-" + "z" * 48
            item = authority.store.data["licenses"][0]
            item["key_hash"] = __import__("hashlib").sha256(old_key.encode()).hexdigest()
            item["key_prefix"] = old_key[:12]
            item.pop("key_version")
            authority.store.save()
            with self.assertRaisesRegex(Exception, "rotacione"):
                authority.view_key(item["id"])
            rotated = authority.rotate_key(item["id"])
            self.assertNotEqual(rotated["key"], old_key)
            denied, status = authority.validate({
                "key": old_key, "installation_id": "install-001", "channel_count": 1,
            })
            self.assertEqual(status, 403)
            self.assertFalse(denied["valid"])
            valid, status = authority.validate({
                "key": rotated["key"], "installation_id": "install-001", "channel_count": 1,
            })
            self.assertEqual(status, 200)
            self.assertTrue(valid["valid"])

    def test_validation_limit_binding_and_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            authority = self.authority(directory)
            created = authority.save_license({"name": "Cliente", "max_channels": 3})
            key = created["key"]
            valid, status = authority.validate({"key": key, "installation_id": "install-001", "channel_count": 3})
            self.assertEqual(status, 200)
            self.assertTrue(valid["valid"])
            excess, status = authority.validate({"key": key, "installation_id": "install-001", "channel_count": 4})
            self.assertEqual(status, 403)
            self.assertFalse(excess["valid"])
            other, status = authority.validate({"key": key, "installation_id": "install-002", "channel_count": 1})
            self.assertEqual(status, 403)
            authority.revoke(created["license"]["id"])
            revoked, status = authority.validate({"key": key, "installation_id": "install-001", "channel_count": 1})
            self.assertEqual(status, 403)
            self.assertIn("revogada", revoked["reason"])


if __name__ == "__main__":
    unittest.main()
