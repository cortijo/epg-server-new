import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install.sh"


class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = INSTALLER.read_text(encoding="utf-8")

    def test_installer_exists_and_uses_strict_shell(self):
        self.assertTrue(INSTALLER.exists())
        self.assertTrue(self.script.startswith("#!/usr/bin/env bash\nset -Eeuo pipefail"))

    def test_installer_collects_password_without_echo(self):
        self.assertIn('read -r -s -p "Senha inicial', self.script)
        self.assertIn('chmod 0600 "${TEMP_ENV}"', self.script)
        self.assertIn("trap cleanup EXIT", self.script)

    def test_final_container_has_security_controls(self):
        for control in (
            "--network host", "--read-only", "--cap-drop ALL",
            "--security-opt no-new-privileges:true", '--user "${CONTAINER_UID}:${CONTAINER_UID}"',
        ):
            self.assertIn(control, self.script)

    def test_installer_checks_health_and_has_rollback(self):
        self.assertIn("wait_for_health", self.script)
        self.assertIn("restore_previous_container", self.script)
        self.assertIn('docker_run rename "${previous}" "${CONTAINER_NAME}"', self.script)
        self.assertIn('root_run cp -a -- "${DATA_DIR}" "${backup_dir}"', self.script)

    def test_installer_rejects_latest_and_does_not_change_firewall(self):
        self.assertIn('!= "latest"', self.script)
        self.assertIn('if image_exists "${IMAGE_TAG}"', self.script)
        self.assertIn("nova tag imutável", self.script)
        executable_firewall = re.compile(r"^[^#\n]*(?:ufw|firewall-cmd|iptables|nft)\s", re.MULTILINE)
        self.assertIsNone(executable_firewall.search(self.script))

    def test_help_documents_no_firewall_mutation(self):
        self.assertIn("Ele não altera o firewall do servidor.", self.script)

    def test_installer_supports_stable_public_base_url(self):
        self.assertIn("URL pública base", self.script)
        self.assertIn('EPG_PUBLIC_BASE_URL=${PUBLIC_BASE_URL}', self.script)

    def test_license_key_directory_is_writable_only_by_runtime_user(self):
        self.assertIn('--volume "${LICENSE_KEY_DIR}:/license"', self.script)
        self.assertIn('EPG_LICENSE_KEY_FILE=/license/${LICENSE_KEY_NAME}', self.script)
        self.assertIn('chmod 0750 "${LICENSE_KEY_DIR}"', self.script)
        self.assertIn('chmod 0600 "${LICENSE_KEY_FILE}"', self.script)


if __name__ == "__main__":
    unittest.main()
