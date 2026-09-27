"""Cross-check the actual C decoder/filter against the desktop reference model."""
import ctypes
from pathlib import Path
import random
import unittest
import zlib

from i2c_gate.config import default_config
from i2c_gate.engine import simulate
from i2c_gate.wire import encode_config

LIBRARY = Path(__file__).resolve().parents[1] / "build" / "gate_native.dll"


@unittest.skipUnless(LIBRARY.exists(), "Run scripts/build_native.ps1 to test the C filter")
class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = ctypes.CDLL(str(LIBRARY))
        cls.lib.decode.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        cls.lib.decode.restype = ctypes.c_int
        cls.lib.crc.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        cls.lib.crc.restype = ctypes.c_uint32
        cls.lib.evaluate.argtypes = [ctypes.c_uint, ctypes.c_uint8, ctypes.c_char_p, ctypes.c_size_t,
                                     ctypes.c_int, ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint8)]

    def test_crc_matches_usb(self):
        for value in (b"", b"123456789", bytes(range(256)), encode_config(default_config())):
            self.assertEqual(self.lib.crc(value, len(value)), zlib.crc32(value))

    def test_address_only_modify_and_rejected_empty_or_pass_rules(self):
        for phase in ("write", "read_request"):
            cfg = default_config()
            cfg["rules"] = [{"name": "redirect", "enabled": True, "phase": phase,
                             "match": {"address": 0x52, "payload": []}, "action": "modify",
                             "destination": 0x50}]
            packet = bytearray(encode_config(cfg))
            self.assertEqual(self.lib.decode(bytes(packet), len(packet)), 1)
            rule_start = 14 + packet[12]
            packet[rule_start + 2] = 0
            self.assertEqual(self.lib.decode(bytes(packet), len(packet)), 0)
            packet[rule_start + 2] = 1
            packet[rule_start + 4] = 255
            self.assertEqual(self.lib.decode(bytes(packet), len(packet)), 0)

    def test_truncated_frames_rejected(self):
        packet = encode_config(default_config())
        for size in range(len(packet)):
            self.assertEqual(self.lib.decode(packet, size), 0)
        self.assertEqual(self.lib.decode(packet, len(packet)), 1)
        self.assertEqual(self.lib.decode(packet + b"\x00", len(packet) + 1), 0)

    def test_firmware_rejects_future_to_past_patch(self):
        cfg = default_config()
        cfg["rules"] = [{"name": "causal", "enabled": True, "phase": "read_response",
                         "match": {"address": 0x50, "payload": [{"offset": 0, "value": 1}]},
                         "action": "modify", "patches": [{"offset": 1, "value": 0}]}]
        packet = bytearray(encode_config(cfg))
        self.assertEqual(self.lib.decode(bytes(packet), len(packet)), 1)
        packet[14 + packet[12] + 7] = 2  # Header, address list, rule header.
        self.assertEqual(self.lib.decode(bytes(packet), len(packet)), 0)

    def test_boot_defaults_pass_all_supported_addresses(self):
        self.lib.reset_config()
        for address in range(128):
            self.assertEqual(bool(self.lib.accepts_address(address)), 0x08 <= address <= 0x77)
        output = ctypes.c_uint8()
        self.assertEqual(self.lib.evaluate(0, 0x52, b"x", 1, 0, 0, ctypes.byref(output)), 0)
        self.assertEqual(output.value, ord("x"))

    def test_legacy_packet_list_no_longer_limits_forwarding(self):
        cfg = default_config()
        cfg["rules"] = [{"name": "block", "enabled": True, "phase": "write",
                         "match": {"address": 0x52, "payload": []}, "action": "block"}]
        packet = bytearray(encode_config(cfg))
        end = 14 + packet[12]
        packet[12] = 1
        packet[14:end] = b"\x50"
        self.assertEqual(self.lib.decode(bytes(packet), len(packet)), 1)
        for address in range(0x08, 0x78):
            self.assertEqual(self.lib.accepts_address(address), 1)
        output = ctypes.c_uint8()
        self.assertEqual(self.lib.evaluate(0, 0x52, b"x", 1, 0, 0, ctypes.byref(output)), 2)
        self.assertEqual(self.lib.evaluate(0, 0x53, b"x", 1, 0, 0, ctypes.byref(output)), 0)

    def test_randomized_desktop_firmware_parity(self):
        rng = random.Random(402)
        for trial in range(300):
            cfg = default_config()
            direction = rng.choice(("read", "write"))
            payload = bytes(rng.randrange(8) for _ in range(rng.randrange(1, 9)))
            for index in range(6):
                action = rng.choice(("modify", "block"))
                pred_offset = rng.randrange(len(payload))
                cfg["rules"].append({
                    "name": str(index), "enabled": rng.choice((True, True, False)),
                    "phase": "write" if direction == "write" else "read_response",
                    "match": {"address": "*", "payload": [{"offset": pred_offset,
                        "value": rng.randrange(8), "mask": rng.choice((1, 3, 255))}]},
                    "action": action, "patches": [{"offset": rng.randrange(pred_offset, len(payload)),
                        "value": rng.randrange(256), "mask": rng.choice((15, 240, 255))}] if action == "modify" else []})
            packet = encode_config(cfg)
            self.assertEqual(self.lib.decode(packet, len(packet)), 1)
            expected = simulate(cfg, direction, 0x50, payload)
            actual = bytearray()
            blocked = False
            for offset, value in enumerate(payload):
                output = ctypes.c_uint8()
                count = offset + 1
                action = self.lib.evaluate(2 if direction == "read" else 0, 0x50,
                                          payload, count, True, offset, ctypes.byref(output))
                blocked = blocked or action == 2
                if blocked and direction == "write":
                    break
                actual.append(255 if blocked and direction == "read" else output.value)
            with self.subTest(trial=trial):
                self.assertEqual(bytes(actual), expected.payload)


if __name__ == "__main__":
    unittest.main()
