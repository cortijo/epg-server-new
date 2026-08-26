import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_isdbtb_ts.py"
SPEC = importlib.util.spec_from_file_location("verify_isdbtb_ts", SCRIPT)
AUDITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDITOR)


class BitAuditorTests(unittest.TestCase):
    def test_second_loop_announces_cdt(self):
        # Header + empty first loop + broadcaster 0 + descriptor D7 + dummy CRC.
        section = bytes.fromhex(
            "c4 f0 12 00 3d c7 00 00 f0 00 00 f0 07 "
            "d7 05 03 00 00 c8 00 00 00 00 00"
        )
        errors = []
        parsed = AUDITOR.parse_bit_section(section, errors)
        self.assertEqual([], errors)
        self.assertEqual(61, parsed["onid"])
        self.assertEqual(3, parsed["version"])
        self.assertEqual({0xC8}, parsed["announced_tables"])
        self.assertEqual([0xC8], parsed["broadcasters"][0]["announced_tables"])

    def test_first_loop_does_not_count_as_announcement(self):
        section = bytes.fromhex(
            "c4 f0 0f 00 3d c7 00 00 f0 07 "
            "d7 05 03 00 00 c8 00 00 00 00 00"
        )
        errors = []
        parsed = AUDITOR.parse_bit_section(section, errors)
        self.assertEqual(set(), parsed["announced_tables"])


if __name__ == "__main__":
    unittest.main()
