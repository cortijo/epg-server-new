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
        self.assertIn("EPG_INSTALL_MODE=native", source)

    def test_privileged_updater_is_separated_and_digest_pinned(self):
        updater = (PACKAGE / "epg-stream-updater.py").read_text(encoding="utf-8")
        path_unit = (PACKAGE / "epg-stream-updater.path").read_text(encoding="utf-8")
        build = (PACKAGE / "build-deb.sh").read_text(encoding="utf-8")
        self.assertIn('re.fullmatch(r"sha256:', updater)
        self.assertIn('["apt-get", "install", "-y", str(package)]', updater)
        self.assertIn('fields[0] != "epg-stream"', updater)
        self.assertNotIn("shell=True", updater)
        self.assertIn("PathChanged=/var/lib/epg-stream/update-request.json", path_unit)
        self.assertIn('epg-stream-updater.py"', build)

    def test_configurator_scrubs_bootstrap_password(self):
        source = (PACKAGE / "epg-stream-configure").read_text(encoding="utf-8")
        self.assertIn("read -r -s", source)
        self.assertIn('replace_env EPG_ADMIN_PASSWORD ""', source)
        self.assertIn("systemctl restart epg-stream.service", source)
        self.assertNotIn("ufw", source)
        self.assertNotIn("nft ", source)

    def test_private_repository_token_is_stored_outside_environment(self):
        source = (PACKAGE / "epg-stream-configure").read_text(encoding="utf-8")
        environment = (PACKAGE / "epg-stream.env").read_text(encoding="utf-8")
        updater = (PACKAGE / "epg-stream-updater.py").read_text(encoding="utf-8")
        self.assertIn('read -r -s -p "Token GitHub somente leitura', source)
        self.assertIn('chmod 0640 "$TOKEN_TEMP"', source)
        self.assertIn('unset UPDATE_TOKEN', source)
        self.assertIn("EPG_UPDATE_TOKEN_FILE=/etc/epg-stream/update.token", environment)
        self.assertIn('headers["Authorization"] = f"Bearer {token}"', updater)

    def test_smoke_test_fails_when_health_never_becomes_ready(self):
        source = (PACKAGE / "smoke-test.sh").read_text(encoding="utf-8")
        self.assertIn("ready=0", source)
        self.assertIn("then ready=1; break", source)
        self.assertIn('test "$ready" = 1', source)


if __name__ == "__main__":
    unittest.main()
