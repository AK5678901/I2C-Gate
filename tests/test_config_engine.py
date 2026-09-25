import copy
import json
from pathlib import Path
import tempfile
import unittest

from i2c_gate.config import ConfigError, default_config, dumps, loads, validate
from i2c_gate.engine import simulate
from i2c_gate.gui import atomic_save


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.cfg = loads((Path(__file__).resolve().parents[1] / "examples" / "filters.json").read_text(encoding="utf-8"))

    def test_write_modify_preserves_other_bytes(self):
        result = simulate(self.cfg, "write", 0x50, bytes.fromhex("10 AA 55"))
        self.assertEqual(result.payload, bytes.fromhex("10 00 55"))
        self.assertTrue(result.downstream_access)

    def test_write_block_does_not_forward_prefix(self):
        result = simulate(self.cfg, "write", 0x50, bytes.fromhex("20 AA"))
        self.assertEqual(result.payload, b"")
        self.assertFalse(result.downstream_access)

    def test_read_mask_preserves_unmasked_bits(self):
        result = simulate(self.cfg, "read", 0x40, bytes.fromhex("EF 12"))
        self.assertEqual(result.payload, bytes.fromhex("6F 12"))

    def test_read_redirect_and_response_use_original_address(self):
        self.cfg["rules"][-1]["enabled"] = True
        result = simulate(self.cfg, "read", 0x50, bytes.fromhex("DE 01"))
        self.assertEqual(result.destination, 0x51)
        self.assertEqual(result.payload, b"\xff\xff")
        self.assertTrue(result.downstream_access)
        self.assertEqual(len(result.rules), 2)

    def test_read_preblock_never_accesses_downstream(self):
        self.cfg["rules"][-2]["enabled"] = True
        result = simulate(self.cfg, "read", 0x40, b"\xef")
        self.assertFalse(result.downstream_access)
        self.assertEqual(result.payload, b"")

    def test_first_rule_wins(self):
        other = copy.deepcopy(self.cfg["rules"][0])
        other.update(name="later", action="block", patches=[])
        self.cfg["rules"].append(other)
        self.assertEqual(simulate(self.cfg, "write", 0x50, b"\x10\xaa").action, "modify")

    def test_disabled_rule_and_short_patch_do_not_match(self):
        self.assertEqual(simulate(self.cfg, "write", 0x50, b"\x10").action, None)
        self.cfg["rules"][0]["enabled"] = False
        self.assertEqual(simulate(self.cfg, "write", 0x50, b"\x10\xaa").payload, b"\x10\xaa")

    def test_short_match_does_not_match(self):
        self.cfg["rules"][1]["match"]["payload"][0]["offset"] = 2
        self.assertEqual(simulate(self.cfg, "write", 0x50, b"\x20").action, None)

    def test_write_limit_fails_closed(self):
        self.cfg["bus"]["max_write_bytes"] = 1
        self.assertFalse(simulate(self.cfg, "write", 0x50, b"\x10\xaa").downstream_access)

    def test_unmatched_addresses_pass(self):
        for direction in ("read", "write"):
            result = simulate(self.cfg, direction, 0x51, b"x")
            self.assertTrue(result.downstream_access)
            self.assertEqual((result.destination, result.payload, result.action), (0x51, b"x", None))

    def test_default_passes_every_supported_address(self):
        for address in range(0x08, 0x78):
            for direction in ("read", "write"):
                result = simulate(default_config(), direction, address, b"\x10\xAA")
                self.assertEqual((result.action, result.destination, result.payload),
                                 (None, address, b"\x10\xAA"))
                self.assertTrue(result.downstream_access)

    def test_legacy_allowlist_does_not_restrict_rules_or_traffic(self):
        cfg = default_config()
        cfg["bus"]["addresses"] = [0x50]
        cfg["rules"] = [{"name": "redirect", "enabled": True, "phase": "write",
                         "match": {"address": 0x52, "payload": []}, "action": "modify",
                         "destination": 0x50, "patches": [{"offset": 3, "value": 0x5A}]}]
        self.assertNotIn("addresses", validate(cfg)["bus"])
        result = simulate(cfg, "write", 0x52, b"\x01\x02\x03\x04")
        self.assertEqual((result.destination, result.payload), (0x50, b"\x01\x02\x03\x5A"))
        self.assertEqual(simulate(cfg, "write", 0x53, b"abcd").payload, b"abcd")
        self.assertEqual(cfg["bus"]["addresses"], [0x50])

    def test_invalid_direction(self):
        with self.assertRaises(ValueError):
            simulate(self.cfg, "typo", 0x50, b"")

    def test_only_modify_and_block_rules_are_allowed(self):
        for action in ("pass", "modify"):
            cfg = default_config()
            cfg["rules"] = [{"name": "empty", "enabled": True, "phase": "write",
                             "match": {"address": "*", "payload": []}, "action": action}]
            with self.subTest(action=action), self.assertRaises(ConfigError):
                validate(cfg)

    def test_address_only_modify_for_write_and_read(self):
        for phase, direction in (("write", "write"), ("read_request", "read")):
            cfg = default_config()
            cfg["rules"] = [{"name": "redirect", "enabled": True, "phase": phase,
                             "match": {"address": 0x52, "payload": []}, "action": "modify",
                             "destination": 0x50}]
            result = simulate(cfg, direction, 0x52, b"abc")
            self.assertEqual((result.action, result.destination, result.payload), ("modify", 0x50, b"abc"))
            self.assertIsNone(simulate(cfg, direction, 0x53, b"abc").action)

    def test_validation_rejects_impossible_rules(self):
        variants = []
        cfg = copy.deepcopy(self.cfg)
        cfg["rules"][2]["match"]["payload"] = [{"offset": 0, "value": 1}]
        variants.append(cfg)
        cfg = copy.deepcopy(self.cfg)
        cfg["rules"][3]["destination"] = 0x51
        variants.append(cfg)
        cfg = copy.deepcopy(self.cfg)
        cfg["rules"][1]["destination"] = 0x51
        variants.append(cfg)
        cfg = copy.deepcopy(self.cfg)
        cfg["rules"][0]["patches"] *= 2
        variants.append(cfg)
        for cfg in variants:
            with self.subTest(cfg=cfg), self.assertRaises(ConfigError):
                validate(cfg)

    def test_strict_input_validation(self):
        for field, value in (("speed_hz", True), ("speed_hz", 100000.5),
                             ("addresses", [0x50, 0x50]), ("addresses", [0x00]),
                             ("max_write_bytes", 0), ("read_block_fill", 256)):
            cfg = default_config()
            cfg["bus"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ConfigError):
                validate(cfg)
        for source in ('{"version": 1, "version": 1}', '[]', '{', 'x' * 65537):
            with self.subTest(source=source[:20]), self.assertRaises(ConfigError):
                loads(source)

    def test_unknown_keys_and_duplicate_names(self):
        self.cfg["rules"][0]["typo"] = True
        with self.assertRaises(ConfigError):
            validate(self.cfg)
        del self.cfg["rules"][0]["typo"]
        self.cfg["rules"][1]["name"] = self.cfg["rules"][0]["name"]
        with self.assertRaises(ConfigError):
            validate(self.cfg)

    def test_round_trip_and_no_mutation(self):
        before = copy.deepcopy(self.cfg)
        self.assertEqual(loads(dumps(self.cfg)), self.cfg)
        simulate(self.cfg, "write", 0x50, b"\x10\xaa")
        self.assertEqual(self.cfg, before)

    def test_atomic_save_and_utf8(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "設定.json"
            atomic_save(path, dumps(default_config()))
            atomic_save(path, dumps(self.cfg))
            self.assertEqual(loads(path.read_text(encoding="utf-8")), self.cfg)
            self.assertEqual(list(Path(temp).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
