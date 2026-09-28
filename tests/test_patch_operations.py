import unittest

from i2c_gate.config import ConfigError, default_config, dumps, loads, validate
from i2c_gate.engine import simulate, simulate_combined
from i2c_gate.patches import editor_operations, lower_patches
from i2c_gate.wire import encode_config


def config(patches, phase="write"):
    cfg = default_config()
    cfg["rules"] = [{"name": "bit operations", "enabled": True, "phase": phase, "action": "modify",
                     "match": {"address": 0x50, "payload": []}, "patches": patches}]
    return cfg


class PatchOperationTests(unittest.TestCase):
    def test_every_byte_and_operand_lower_to_identical_bit_operation(self):
        for operation in ("AND", "OR"):
            for operand in range(256):
                lowered = lower_patches([{"offset": 0, "operation": operation, "value": operand}])[0]
                for original in range(256):
                    expected = original & operand if operation == "AND" else original | operand
                    actual = (original & (255 ^ lowered["mask"])) | (lowered["value"] & lowered["mask"])
                    self.assertEqual(actual, expected)

    def test_write_read_and_combined_use_bit_operations(self):
        patches = [{"offset": 0, "operation": "AND", "value": 0xF0},
                   {"offset": 0, "operation": "OR", "value": 0x05}]
        for phase, direction in (("write", "write"), ("read_response", "read")):
            cfg = config(patches, phase)
            self.assertEqual(simulate(cfg, direction, 0x50, bytes([0xAB, 0x12])).payload, bytes([0xA5, 0x12]))
            self.assertEqual(loads(dumps(cfg)), validate(cfg))
        cfg["rules"][0]["match"]["write"] = {"address": 0x50, "payload": [{"offset": 0, "value": 16}]}
        self.assertEqual(simulate_combined(cfg, 0x50, bytes([16]), 0x50, bytes([0xAB]))[1].payload, bytes([0xA5]))
        self.assertEqual(simulate_combined(cfg, 0x50, bytes([17]), 0x50, bytes([0xAB]))[1].payload, bytes([0xAB]))

    def test_operation_order_matters(self):
        operations = [{"offset": 0, "operation": "AND", "value": 0xF0},
                      {"offset": 0, "operation": "OR", "value": 0x05}]
        self.assertEqual(simulate(config(operations), "write", 0x50, bytes([0xAB])).payload, bytes([0xA5]))
        self.assertEqual(simulate(config(operations[::-1]), "write", 0x50, bytes([0xAB])).payload, bytes([0xA0]))

    def test_legacy_masks_migrate_without_changing_results_or_wire(self):
        for mask in range(256):
            legacy = [{"offset": 0, "value": 0x5A, "mask": mask}]
            modern = editor_operations(legacy)
            self.assertEqual(encode_config(config(legacy)), encode_config(config(modern)))

    def test_reject_invalid_operator_or_mixed_format(self):
        for patch in ({"offset": 0, "value": 1, "operation": "XOR"},
                      {"offset": 0, "value": 1, "operation": "AND", "mask": 255},
                      {"offset": 0, "value": 256, "operation": "OR"}):
            with self.assertRaises(ConfigError):
                validate(config([patch]))
