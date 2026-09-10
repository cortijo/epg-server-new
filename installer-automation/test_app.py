import os
import unittest
from pathlib import Path

os.environ.setdefault("INSTALLER_ADMIN_USER", "test-admin")
os.environ.setdefault("INSTALLER_ADMIN_PASSWORD", "test-password-long")

import app


class InstallerTests(unittest.TestCase):
    def base(self):
        return {
            "host": "192.0.2.10", "port": 22, "username": "operator",
            "password": "ssh-secret", "sudo_password": "sudo-secret",
            "fingerprint": "SHA256:expected",
            "repository": "https://github.com/cortijo/epgserver2.git",
            "ref": "epg-v1.22.0", "image": "epgserver:v1.22.0",
            "container": "epg-stream", "data_dir": "/srv/epg-stream",
            "http_port": 9100, "license_server": "http://192.0.2.20:9200",
            "license_key": "EPG-not-a-real-key", "license_interval": 43200,
            "installation_id": "client-001", "admin_user": "epgadmin",
            "admin_password": "admin-secret-long",
        }

    def test_detects_supported_linux_families(self):
        self.assertEqual(app.parse_os_release('ID=ubuntu\nPRETTY_NAME="Ubuntu 24.04"')["family"], "apt")
        self.assertEqual(app.parse_os_release('ID=rocky\nPRETTY_NAME="Rocky Linux 9"')["family"], "dnf")
        self.assertEqual(app.parse_os_release("ID=arch")["family"], "unsupported")

    def test_validates_and_rejects_injection(self):
        result = app.validate_request(self.base(), install=True)
        self.assertEqual(result["container"], "epg-stream")
        bad = self.base()
        bad["container"] = "epg; reboot"
        with self.assertRaises(app.InstallError):
            app.validate_request(bad, install=True)

    def test_script_has_host_network_persistence_health_and_rollback(self):
        script = app.install_script(app.validate_request(self.base(), install=True))
        self.assertIn("--network host", script)
        self.assertIn("/srv/epg-stream:/data", script)
        self.assertIn("rollback-$(date", script)
        self.assertIn("docker exec \"$name\" python3", script)
        self.assertIn("docker update --restart=no", script)

    def test_script_selects_official_docker_repo_for_rhel_family(self):
        config = app.validate_request(self.base(), install=True)
        config.update({"os_family": "dnf", "os_id": "rocky"})
        script = app.install_script(config)
        self.assertIn("download.docker.com/linux/centos/docker-ce.repo", script)
        self.assertIn("docker-ce-cli", script)

    def test_redaction_removes_all_secrets(self):
        config = self.base()
        text = " ".join([config["password"], config["sudo_password"], config["license_key"], config["admin_password"]])
        clean = app.redact(text, [config["password"], config["sudo_password"], config["license_key"], config["admin_password"]])
        self.assertNotIn("secret", clean)
        self.assertNotIn("EPG-not", clean)

    def test_remote_script_is_not_embedded_in_process_arguments(self):
        source = Path(app.__file__).read_text(encoding="utf-8")
        self.assertIn('command = "sudo -S -p \'\' bash"', source)
        self.assertNotIn("base64 -d | sudo", source)

    def test_jobs_never_receive_secret_fields(self):
        public = {"id": "1", "status": "running", "log": [], "result": None}
        self.assertFalse(set(public) & {"password", "sudo_password", "license_key", "admin_password"})

    def manage(self, action="restart"):
        return {
            "host": "192.0.2.10", "port": 22, "username": "operator",
            "password": "ssh-secret", "sudo_password": "sudo-secret",
            "fingerprint": "SHA256:expected", "container": "epg-stream",
            "panel_user": "epgadmin", "panel_password": "panel-secret-long",
            "action": action,
        }

    def test_management_actions_validate_destructive_inputs(self):
        self.assertEqual(app.validate_action(self.manage())["action"], "restart")
        restore = self.manage("restore")
        restore["backup"] = "../../etc/shadow"
        with self.assertRaises(app.InstallError):
            app.validate_action(restore)
        deploy = self.manage("deploy")
        deploy.update({"repository": "https://github.com/cortijo/epgserver2.git", "ref": "main", "image": "epgserver:latest"})
        with self.assertRaises(app.InstallError):
            app.validate_action(deploy)

    def test_management_script_preserves_backup_and_rollback(self):
        self.assertIn("safety=", app.REMOTE_ACTION)
        self.assertIn("rollback=name+", app.REMOTE_ACTION)
        self.assertIn('call(["docker","update","--restart=no",rollback])', app.REMOTE_ACTION)
        self.assertIn("shutil.rmtree(data,ignore_errors=True)", app.REMOTE_ACTION)
        self.assertIn("--warning=no-file-changed", app.REMOTE_ACTION)
        self.assertIn("os.unlink(target)", app.REMOTE_ACTION)
        self.assertIn("packed.returncode not in (0,1)", app.REMOTE_ACTION)
        self.assertIn('["tar","-tzf",target]', app.REMOTE_ACTION)

    def test_management_ui_has_both_modes_and_diagnostics(self):
        self.assertIn("Nova instalação", app.INDEX)
        self.assertIn("Instalação existente", app.INDEX)
        self.assertIn("LATÊNCIA HEALTH", app.INDEX)
        self.assertIn("Atualizar / downgrade", app.INDEX)
        self.assertIn("Erros nos canais", app.INDEX)

    def test_structured_response_ignores_shell_noise(self):
        parsed = app.parse_marked_json("sudo notice\n42\n__OMNIEPG_JSON__{\"ok\":true}\n", "missing")
        self.assertTrue(parsed["ok"])
        with self.assertRaises(app.InstallError):
            app.parse_marked_json("only shell noise", "missing")


if __name__ == "__main__":
    unittest.main()
