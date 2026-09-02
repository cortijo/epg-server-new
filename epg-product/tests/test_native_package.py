import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packaging" / "debian"


class NativePackageTests(unittest.TestCase):
    def test_build_targets_supported_ubuntu_architectures(self):
        source = (PACKAGE / "build-deb.sh").read_text(encoding="utf-8")
        self.assertIn("set -Eeuo pipefail", source)
        self.assertIn("amd64|arm64", source)
        self.assertIn("dpkg-deb --build --root-owner-group", source)
        self.assertIn("Depends: adduser,", source)
        self.assertNotIn("latest", source)

    def test_service_is_restricted_and_recovers(self):
        source = (PACKAGE / "epg-stream.service").read_text(encoding="utf-8")
        for expected in (
                "User=epgstream", "Restart=on-failure", "NoNewPrivileges=true",
                "ProtectSystem=strict", "ReadWritePaths=/var/lib/epg-stream"):
            self.assertIn(expected, source)

    def test_paths_and_native_auditor_are_configured(self):
        source = (PACKAGE / "epg-stream.env").read_text(encoding="utf-8")
        self.assertIn("EPG_DATA_DIR=/var/lib/epg-stream", source)
        self.assertIn("EPG_EMITTER_BINARY=/usr/lib/epg-stream/TVStreamEpgOnly", source)
        self.assertIn("EPG_AUDITOR_SCRIPT=/usr/lib/epg-stream/verify_isdbtb_ts.py", source)
        self.assertIn("EPG_LICENSE_CHECK_SECONDS=43200", source)

    def test_configurator_scrubs_bootstrap_password(self):
        source = (PACKAGE / "epg-stream-configure").read_text(encoding="utf-8")
        self.assertIn("read -r -s", source)
        self.assertIn('replace_env EPG_ADMIN_PASSWORD ""', source)
        self.assertIn("systemctl restart epg-stream.service", source)
        self.assertNotIn("ufw", source)
        self.assertNotIn("nft ", source)


if __name__ == "__main__":
    unittest.main()
