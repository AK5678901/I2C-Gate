import copy
import struct
import unittest
import zlib

from i2c_gate.config import ConfigError, default_config, validate
from i2c_gate.engine import simulate
from i2c_gate.wire import encode_config, frame, upload


class FakeSerial:
    hello = b"I2C-GATE 1 GPIO-EXPERIMENTAL\n"
    error = None

    def __init__(self, *args, **kwargs):
        self.writes = []
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass

    def readline(self):
        self.reads += 1
        if self.reads == 1:
            return self.hello
        return self.error or f"OK {zlib.crc32(self.writes[-1][12:]):08X}\n".encode()


class USBTests(unittest.TestCase):
    def test_frame_integrity(self):
        packet = frame(default_config())
        length, crc = struct.unpack("<II", packet[4:12])
        self.assertEqual(packet[:4], b"I2CG")
        self.assertEqual(len(packet) - 12, length)
        self.assertEqual(zlib.crc32(packet[12:]), crc)
        self.assertEqual(struct.unpack("<BIIHBBB", packet[12:26]), (1, 100000, 25000, 256, 255, 1, 0))

    def test_upload_checks_ack(self):
        self.assertIn("RAM", upload("COM7", default_config(), FakeSerial))

    def test_wrong_device_gets_no_configuration(self):
        link = FakeSerial()
        link.hello = b"OTHER DEVICE\n"
        with self.assertRaises(RuntimeError):
            upload("COM7", default_config(), lambda *a, **kw: link)
        self.assertEqual(link.writes, [b"HELLO\n"])

    def test_error_and_timeout_do_not_report_success(self):
        for reply in (b"ERR CONFIG\n", b"ERR APPLY_PENDING\n", b"\n", b"OK 00000000\n"):
            link = FakeSerial()
            link.error = reply
            with self.subTest(reply=reply), self.assertRaises(RuntimeError):
                upload("COM7", default_config(), lambda *a, **kw: link)


class StreamingTests(unittest.TestCase):
    def config(self, action, predicates, patches=()):
        cfg = default_config()
        cfg["rules"] = [{"name": "stream", "enabled": True, "phase": "read_response",
                         "match": {"address": 0x50, "payload": list(predicates)},
                         "action": action, "patches": list(patches)}]
        return cfg

    def test_future_condition_cannot_rewrite_past(self):
        cfg = self.config("modify", [{"offset": 2, "value": 3}], [{"offset": 0, "value": 4}])
        with self.assertRaises(ConfigError):
            validate(cfg)
        with self.assertRaises(ConfigError):
            encode_config(cfg)

    def test_late_block_preserves_previously_delivered_bytes(self):
        cfg = self.config("block", [{"offset": 2, "value": 3}])
        self.assertEqual(simulate(cfg, "read", 0x50, b"\x01\x02\x03\x04").payload, b"\x01\x02\xff\xff")

    def test_priority_is_evaluated_at_each_byte(self):
        cfg = self.config("pass", [{"offset": 1, "value": 2}])
        later = copy.deepcopy(cfg["rules"][0])
        later.update(name="lower priority", action="modify", patches=[{"offset": 0, "value": 7}])
        later["match"]["payload"] = []
        cfg["rules"].append(later)
        self.assertEqual(simulate(cfg, "read", 0x50, b"\x01\x02").payload, b"\x07\x02")

    def test_matching_uses_original_read_bytes(self):
        cfg = self.config("modify", [{"offset": 0, "value": 1}],
                          [{"offset": 0, "value": 8}, {"offset": 1, "value": 9}])
        self.assertEqual(simulate(cfg, "read", 0x50, b"\x01\x02").payload, b"\x08\x09")


if __name__ == "__main__":
    unittest.main()
