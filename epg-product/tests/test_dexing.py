import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dexing import DexingClient


class FakeDexing(DexingClient):
    def __post_init__(self):
        self.calls = []
        self.pid_rows = [
            {"input_channel": 2, "input_pid": "0x1000", "output_pid": "0x1000"},
        ]

    def login(self):
        self.calls.append("login")

    def inventory(self, output_index):
        self.calls.append(("inventory", output_index))
        return {"tsin": [{"input_name": "IP2_Data1_239.192.1.200:5012"}],
                "tsout": {"prg_info": [{"program_number": 101, "service_name": "SPORTV"}]}}

    def parse_program(self, output_index, input_index, timeout=60):
        self.calls.append(("parse", output_index, input_index))

    def general(self, output_index):
        return {"TSID": 60, "ONID": 61}

    def get_pid_rows(self, output_index):
        return [dict(row) for row in self.pid_rows]

    def set_pid_rows(self, output_index, rows):
        self.calls.append(("set_pids", output_index, [dict(row) for row in rows]))
        self.pid_rows = rows


class DexingTests(unittest.TestCase):
    def test_existing_multicast_is_reused_and_pid_table_is_merged(self):
        client = FakeDexing("10.42.0.152", "admin", "admin")
        result = client.synchronize(7, "239.192.1.200", 5012, 1,
                                    [{"service_id": 101, "name": "SPORTV"}])
        self.assertFalse(result["input_created"])
        self.assertEqual(result["input_channel"], 2)
        self.assertEqual(result["pids_added"], ["0x0012", "0x0014"])
        self.assertEqual(client.calls[1], ("inventory", 6))
        self.assertIn(("parse", 6, 1), client.calls)
        self.assertEqual(client.pid_rows[0]["input_pid"], "0x1000")
        self.assertTrue(result["service_mapping"][0]["found"])
        self.assertEqual(result["transport_stream_id"], 60)
        self.assertEqual(result["original_network_id"], 61)

    def test_pid_merge_is_idempotent(self):
        client = FakeDexing("10.42.0.152", "admin", "admin")
        client.pid_rows.extend([
            {"input_channel": 2, "input_pid": "0x0012", "output_pid": "0x0012"},
            {"input_channel": 2, "input_pid": "0x0014", "output_pid": "0x0014"},
        ])
        result = client.ensure_pid_passthrough(0, 2)
        self.assertEqual(result["added"], [])
        self.assertFalse(any(call[0] == "set_pids" for call in client.calls if isinstance(call, tuple)))

    def test_input_name_parser_uses_one_based_input_channel(self):
        found = DexingClient.find_input(
            {"tsin": [{"input_name": "IP9_Data2_239.1.2.3:5001"}]}, "239.1.2.3", 5001)
        self.assertEqual(found["input_channel"], 9)
        self.assertEqual(found["input_index"], 8)
        self.assertEqual(found["data_interface"], 2)

    def test_real_firmware_pid_response_is_normalized(self):
        client = FakeDexing("10.42.0.152", "admin", "admin")
        client._post = lambda *_args, **_kwargs: {
            "count_pidpass": 2,
            "pidpass": [
                {"from_input_channel": 4, "old_pid": 18, "new_pid": 18},
                {"from_input_channel": 4, "old_pid": 20, "new_pid": 20},
            ],
        }
        rows = DexingClient.get_pid_rows(client, 0)
        self.assertEqual(rows, [
            {"input_channel": 4, "input_pid": 18, "output_pid": 18},
            {"input_channel": 4, "input_pid": 20, "output_pid": 20},
        ])

    def test_transport_ids_accept_firmware_wrappers(self):
        self.assertEqual(DexingClient.transport_ids({"stream": {"TSID": "60", "ONID": 61}}),
                         {"transport_stream_id": 60, "original_network_id": 61})


if __name__ == "__main__":
    unittest.main()
