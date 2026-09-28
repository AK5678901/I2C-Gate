import ctypes
import unittest
from pathlib import Path

from i2c_gate.config import ConfigError, default_config, validate
from i2c_gate.engine import WriteContext, simulate, simulate_combined
from i2c_gate.wire import encode_config


def rule(phase="write", action="block", payload=None, **extras):
    return {"name": "rule", "enabled": True, "phase": phase, "action": action,
            "match": {"address": 0x50, "payload": payload or []}, **extras}


def config(*rules):
    cfg = default_config()
    cfg["rules"] = list(rules)
    return cfg


class ByteModelTests(unittest.TestCase):
    def test_nack_position_rejects_late_conditions_and_unreachable_patches(self):
        for r in (rule("write", "modify", ack="nack", nack_at=1, patches=[{"offset": 1, "value": 2}]),
                  rule("read_response", "modify", [{"offset": 2, "value": 3}], ack="nack", nack_at=1),
                  rule("read_response", "modify", ack="nack", nack_at=4096)):
            with self.assertRaises(ConfigError):
                validate(config(r))

    def test_write_block_keeps_sent_prefix(self):
        cfg = config(rule(payload=[{"offset": 1, "value": 2}]))
        result = simulate(cfg, "write", 0x50, b"\x01\x02\x03")
        self.assertEqual((result.payload, result.nack_offset), (b"\x01", 1))
        self.assertTrue(result.downstream_access)

    def test_address_block_sends_nothing(self):
        for direction, phase in (("write", "write"), ("read", "read_request")):
            result = simulate(config(rule(phase)), direction, 0x50, b"abc")
            self.assertEqual((result.payload, result.nack_offset, result.downstream_access), (b"", -1, False))

    def test_device_nack_is_visible_at_corresponding_byte(self):
        result = simulate(config(), "write", 0x50, b"abc", device_write_acks=[True, False, True])
        self.assertEqual((result.payload, result.nack_offset), (b"ab", 1))
        result = simulate(config(), "write", 0x50, b"abc", device_address_ack=False)
        self.assertEqual((result.payload, result.nack_offset), (b"", -1))

    def test_read_ack_policies(self):
        for mode, expected, payload in (("host", (True, False), b"ab"),
                                         ("ack", (True, True), b"ab"),
                                         ("nack", (False,), b"a\xff")):
            r = rule("read_response", "modify", ack=mode, patches=[{"offset": 0, "value": 97}])
            result = simulate(config(r), "read", 0x50, b"ab")
            self.assertEqual((result.device_acks, result.payload), (expected, payload))

    def test_combined_uses_original_write_and_stop_clears_it(self):
        write = rule("write", "modify", patches=[{"offset": 0, "value": 0}])
        read = rule("read_response", "modify", ack="nack", patches=[{"offset": 0, "value": 42}])
        read["name"] = "read"
        read["match"]["write"] = {"address": 0x50, "payload": [{"offset": 0, "value": 16}]}
        cfg = config(write, read)
        written, received = simulate_combined(cfg, 0x50, b"\x10", 0x50, b"ab")
        self.assertEqual(written.payload, b"\x00")
        self.assertEqual((received.payload, received.device_acks), (b"*\xff", (False,)))
        self.assertEqual(simulate(cfg, "read", 0x50, b"ab").payload, b"ab")
        self.assertEqual(simulate(cfg, "read", 0x50, b"ab", write_context=WriteContext(0x51, b"\x10")).payload, b"ab")

    def test_reject_noncausal_write(self):
        for r in (rule("write", "modify", [{"offset": 1, "value": 1}], patches=[{"offset": 0, "value": 2}]),
                  rule("write", "modify", [{"offset": 0, "value": 1}], destination=0x51)):
            with self.assertRaises(ConfigError):
                validate(config(r))


LIBRARY = Path(__file__).resolve().parents[1] / "build" / "gate_native.dll"


@unittest.skipUnless(LIBRARY.exists(), "Run scripts/build_native.ps1")
class BridgeTests(unittest.TestCase):
    def test_previous_write_offset_independent_of_read_nack_position(self):
        r = rule("read_response", "modify", ack="nack", nack_at=0)
        r["match"]["write"] = {"address": 0x50, "payload": [{"offset": 3, "value": 16}]}
        self.run_bus(config(r), [0xA0, 1, 2, 3, 16, -2, 0xA1, -1], reads=b"a", host=[1])
        self.assertEqual(self.values(5), [97])
        self.assertEqual(self.values(6), [1])

    def test_decoder_rejects_conditions_and_patches_after_nack(self):
        for phase, patch in (("write", True), ("read_response", True), ("read_response", False)):
            r = rule(phase, "modify", ack="nack", nack_at=1,
                     patches=[{"offset": 0, "value": 0}] if patch else [])
            if not patch:
                r["match"]["payload"] = [{"offset": 0, "value": 0}]
            packet = bytearray(encode_config(config(r)))
            term = 14 + packet[12] + 13
            packet[term] = 1 if phase == "write" else 2
            self.assertEqual(self.lib.bridge_test_config(bytes(packet), len(packet)), 0)

    def test_and_or_operations_reach_actual_bridge(self):
        patches = [{"offset": 0, "operation": "AND", "value": 0xF0},
                   {"offset": 0, "operation": "OR", "value": 0x05}]
        self.run_bus(config(rule("write", "modify", patches=patches)), [0xA0, 0xAB, -1])
        self.assertEqual(self.values(3), [0xA0, 0xA5])
        self.run_bus(config(rule("read_response", "modify", patches=patches)), [0xA1, -1], reads=bytes([0xAB]), host=[1])
        self.assertEqual(self.values(5), [0xA5])
        self.assertEqual(self.values(6), [1])

    def test_write_nack_offset_with_earlier_modification(self):
        cfg = config(rule("write", "modify", ack="nack", nack_at=2,
                          patches=[{"offset": 0, "value": 90}]))
        self.run_bus(cfg, [0xA0, 1, 2, 3, 4, -1])
        self.assertEqual(self.values(3), [0xA0, 90, 2])
        self.assertEqual(self.values(4), [1, 1, 1, 0, 0])
        model = simulate(cfg, "write", 0x50, bytes([1, 2, 3, 4]))
        self.assertEqual((model.payload, model.nack_offset), (bytes([90, 2]), 2))

    def test_combined_read_nack_offset_with_modification(self):
        r = rule("read_response", "modify", ack="nack", nack_at=1,
                 patches=[{"offset": 0, "value": 90}])
        r["match"]["write"] = {"address": 0x50, "payload": [{"offset": 0, "value": 16}]}
        self.run_bus(config(r), [0xA0, 16, -2, 0xA1, -1], reads=b"abc", host=[0, 0, 1])
        self.assertEqual(self.values(5), [90, 98, 255])
        self.assertEqual(self.values(6), [0, 1])
        self.assertEqual(self.values(9), [97, 98])

    def test_v3_truncation_and_invalid_nack_position(self):
        packet = encode_config(config(rule("write", "modify", ack="nack", nack_at=1)))
        self.assertEqual(packet[0], 3)
        for size in range(len(packet)):
            self.assertEqual(self.lib.bridge_test_config(packet, size), 0)
        bad = bytearray(packet)
        offset = 14 + bad[12] + 11
        bad[offset:offset + 2] = b"\x00\x10"
        self.assertEqual(self.lib.bridge_test_config(bytes(bad), len(bad)), 0)

    @classmethod
    def setUpClass(cls):
        cls.lib = ctypes.CDLL(str(LIBRARY))
        cls.lib.bridge_test_config.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        cls.lib.bridge_test_input.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_size_t,
                                             ctypes.c_char_p, ctypes.c_size_t]
        cls.lib.bridge_test_acks.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_size_t,
                                            ctypes.POINTER(ctypes.c_int), ctypes.c_size_t]

    def run_bus(self, cfg, rx, reads=b"", host=(), device=()):
        packet = encode_config(cfg)
        self.assertEqual(self.lib.bridge_test_config(packet, len(packet)), 1)
        array = lambda values: (ctypes.c_int * len(values))(*values)
        self.lib.bridge_test_input(array(rx), len(rx), reads, len(reads))
        self.lib.bridge_test_acks(array(host), len(host), array(device), len(device))
        self.assertEqual(self.lib.bridge_test_run(), 0, "callback/device operation without host SCL held low")
        self.events = [(self.lib.bridge_test_event(i, 0), self.lib.bridge_test_event(i, 1))
                       for i in range(self.lib.bridge_test_event_count())]

    def values(self, kind):
        return [v for k, v in self.events if k == kind]

    def test_write_forwards_before_stop_and_relays_device_nack(self):
        self.run_bus(config(), [0xA0, 1, 2, 3, -1], device=[0, 0, 1])
        self.assertEqual(self.values(3), [0xA0, 1, 2])
        self.assertEqual(self.values(4), [1, 1, 0, 0])
        self.assertEqual(self.values(8), [1, 2])
        order = [event for event in self.events if event[0] in (3, 4, 7, 8)]
        self.assertEqual(order[:9], [(7, 0xA0), (3, 0xA0), (4, 1), (8, 1), (3, 1),
                                     (4, 1), (8, 2), (3, 2), (4, 0)])

    def test_address_block_never_sends_address(self):
        for phase, address in (("write", 0xA0), ("read_request", 0xA1)):
            self.run_bus(config(rule(phase)), [address, -1])
            self.assertEqual(self.values(3), [])
            self.assertEqual(self.values(4), [0])
            self.assertEqual(self.values(7), [address])

    def test_device_address_nack(self):
        for address in (0xA0, 0xA1):
            self.run_bus(config(), [address, -1], device=[1])
            self.assertEqual(self.values(3), [address])
            self.assertEqual(self.values(4), [0])
            self.assertEqual(self.values(8), [])

    def test_data_block_sends_only_prefix(self):
        self.run_bus(config(rule(payload=[{"offset": 1, "value": 2}])), [0xA0, 1, 2, 3, -1])
        self.assertEqual(self.values(3), [0xA0, 1])
        self.assertEqual(self.values(4), [1, 1, 0, 0])

    def test_write_modify_uses_original_prefix(self):
        first = rule("write", "modify", patches=[{"offset": 0, "value": 99}])
        second = rule(payload=[{"offset": 0, "value": 1}, {"offset": 1, "value": 2}])
        second["name"] = "block"
        # Higher-priority rule becomes eligible only after the second original byte.
        self.run_bus(config(second, first), [0xA0, 1, 2, -1])
        self.assertEqual(self.values(3), [0xA0, 99])
        self.assertEqual(self.values(4), [1, 1, 0])

    def test_repeated_start_context_and_stop_reset(self):
        read = rule("read_response", "modify", patches=[{"offset": 0, "value": 42}], ack="nack")
        read["match"]["write"] = {"address": 0x50, "payload": [{"offset": 0, "value": 16}]}
        self.run_bus(config(read), [0xA0, 16, -2, 0xA1, -1, 0xA1, -1], reads=b"ab", host=[1, 1])
        self.assertEqual(self.values(3), [0xA0, 16, 0xA1, 0xA1])
        self.assertEqual(self.values(5), [42, ord("b")])
        self.assertEqual(self.values(10), [-1, -1, 1, 1, -1, -1])
        self.assertEqual(self.values(11), [0x50, 0x50])
        self.assertEqual(self.values(12), [16, 16])
        # No downstream STOP is inserted before the repeated START address.
        first_stop = self.events.index((2, 0))
        self.assertGreater(first_stop, self.events.index((3, 0xA1)))

    def test_context_can_block_read_address(self):
        read = rule("read_request")
        read["match"]["write"] = {"address": 0x50, "payload": [{"offset": 0, "value": 16}]}
        self.run_bus(config(read), [0xA0, 16, -2, 0xA1, -1])
        self.assertEqual(self.values(3), [0xA0, 16])
        self.assertEqual(self.values(4), [1, 1, 0])

    def test_ack_override_and_no_read_after_nack(self):
        for mode, down_acks, output, reads in (("host", [0, 1], [42, 98], [97, 98]),
                                               ("ack", [0, 0], [42, 98], [97, 98]),
                                               ("nack", [1], [42, 255], [97])):
            cfg = config(rule("read_response", "modify", ack=mode, patches=[{"offset": 0, "value": 42}]))
            self.run_bus(cfg, [0xA1, -1], reads=b"ab", host=[0, 1])
            self.assertEqual(self.values(5), output)
            self.assertEqual(self.values(6), down_acks)
            self.assertEqual(self.values(9), reads)
            events = [k for k, v in self.events if k in (5, 6, 8, 9)]
            self.assertEqual(events[:4], [9, 8, 5, 6])

    def test_limit_nacks_without_forwarding_overflow(self):
        cfg = config()
        cfg["bus"]["max_write_bytes"] = 1
        self.run_bus(cfg, [0xA0, 1, 2, -1])
        self.assertEqual(self.values(3), [0xA0, 1])
        self.assertEqual(self.values(4), [1, 1, 0])

    def test_timeout_is_not_an_ack(self):
        self.run_bus(config(), [0xA0, -1], device=[-1])
        self.assertEqual(self.values(4), [0])

    def test_v2_truncation_and_invalid_ack_rejected(self):
        cfg = config(rule("read_response", "modify", ack="ack"))
        packet = encode_config(cfg)
        self.assertEqual(packet[0], 2)
        for length in range(len(packet)):
            self.assertEqual(self.lib.bridge_test_config(packet, length), 0)
        invalid = bytearray(packet)
        invalid[14 + invalid[12] + 7] = 3
        self.assertEqual(self.lib.bridge_test_config(bytes(invalid), len(invalid)), 0)
