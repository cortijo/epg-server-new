import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "firewall-manager.sh"
EXAMPLE = ROOT / "scripts" / "firewall.conf.example"


class FirewallManagerTests(unittest.TestCase):
    def setUp(self):
        self.script_text = SCRIPT.read_text(encoding="utf-8")

    def run_script(self, config: Path, *arguments: str, env=None):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash não está disponível neste ambiente")
        command = [bash, str(SCRIPT), "--config", str(config), *arguments]
        environment = (env or os.environ).copy()
        environment["EPG_FIREWALL_PYTHON"] = Path(sys.executable).as_posix()
        return subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def test_script_is_strict_and_never_flushes_global_rules(self):
        self.assertIn("set -Eeuo pipefail", self.script_text)
        self.assertIn('TABLE_NAME="epg_managed"', self.script_text)
        self.assertNotIn("flush ruleset", self.script_text.lower())
        self.assertNotIn("iptables -f", self.script_text.lower())
        self.assertNotIn("chain forward", self.script_text.lower())
        self.assertNotIn("chain nat", self.script_text.lower())

    def test_example_contains_ipv4_ipv6_and_declared_ports(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("NETWORK=45.224.164.0/22", text)
        self.assertIn("NETWORK=2804:44f0::/32", text)
        self.assertIn("TCP_PORT=22", text)
        self.assertIn("TCP_PORT=9100", text)

    def test_render_is_deterministic_and_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "firewall.conf"
            config.write_text(
                "NETWORK=192.168.1.99/24\n"
                "NETWORK=2804:44f0::/32\n"
                "TCP_PORT=9100\n"
                "TCP_PORT=22\n"
                "UDP_PORT=5000-5010\n",
                encoding="utf-8",
            )
            first = self.run_script(config, "render")
            second = self.run_script(config, "render")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            self.assertIn("table inet epg_managed", first.stdout)
            self.assertIn("192.168.1.0/24", first.stdout)
            self.assertIn("2804:44f0::/32", first.stdout)
            self.assertIn("meta l4proto tcp counter drop", first.stdout)
            self.assertNotIn("delete table", first.stdout)

    def test_invalid_network_and_port_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "firewall.conf"
            config.write_text("NETWORK=999.1.1.0/24\nTCP_PORT=70000\n", encoding="utf-8")
            result = self.run_script(config, "check")
            self.assertNotEqual(result.returncode, 0)

    def test_add_and_remove_update_only_the_declarative_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "firewall.conf"
            config.write_text("NETWORK=192.168.0.0/16\nTCP_PORT=22\n", encoding="utf-8")
            added_network = self.run_script(config, "network", "add", "10.20.30.40/8")
            added_port = self.run_script(config, "port", "add", "udp", "5000-5010")
            self.assertEqual(added_network.returncode, 0, added_network.stderr)
            self.assertEqual(added_port.returncode, 0, added_port.stderr)
            updated = config.read_text(encoding="utf-8")
            self.assertIn("NETWORK=10.0.0.0/8", updated)
            self.assertIn("UDP_PORT=5000-5010", updated)
            removed = self.run_script(config, "port", "remove", "udp", "5000-5010")
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertNotIn("UDP_PORT=5000-5010", config.read_text(encoding="utf-8"))

    def test_ssh_lockout_guard_rejects_unlisted_client(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "firewall.conf"
            config.write_text("NETWORK=192.168.0.0/16\nTCP_PORT=22\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["SSH_CONNECTION"] = "203.0.113.7 50000 192.0.2.10 22"
            result = self.run_script(config, "check", env=environment)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("IP da sessão SSH", result.stderr)

    def test_dry_run_needs_neither_root_nor_nft(self):
        if shutil.which("bash") is None:
            self.skipTest("bash não está disponível neste ambiente")
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "firewall.conf"
            config.write_text("NETWORK=192.168.0.0/16\nTCP_PORT=22\n", encoding="utf-8")
            environment = os.environ.copy()
            environment.pop("SSH_CONNECTION", None)
            environment["PATH"] = str(Path(shutil.which("bash")).parent)
            result = self.run_script(config, "--force", "--dry-run", "apply", env=environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Simulação aprovada", result.stdout)
            self.assertIn("table inet epg_managed", result.stdout)


if __name__ == "__main__":
    unittest.main()
