import unittest

from i2c_gate.conditions import from_match, to_match, from_changes, to_changes
from i2c_gate.config import ConfigError, default_config, validate


class ConditionsTests(unittest.TestCase):
    def test_existing_match_round_trip(self):
        for address in ("*", 0x50):
            cfg = default_config()
            cfg["rules"] = [{"name": "round trip", "enabled": True, "phase": "write",
                             "match": {"address": address, "payload": [
                                 {"offset": 2, "value": 0xA0, "mask": 0xF0}]},
                             "action": "modify", "destination": 0x52}]
            original = validate(cfg)
            cfg["rules"][0]["match"] = to_match(from_match(original["rules"][0]["match"]))
            self.assertEqual(validate(cfg), original)

    def test_no_conditions_means_any_registered_address(self):
        self.assertEqual(to_match([]), {"address": "*", "payload": []})

    def test_reject_invalid_rewrites(self):
        for changes in ({}, [None], [{"target": "unknown", "value": 1}],
                        [{"target": "data", "value": 1}],
                        [{"target": "address", "value": "0x52", "mask": 127}],
                        [{"target": "address", "value": "0x52"}] * 2):
            with self.subTest(changes=changes), self.assertRaises(ConfigError):
                to_changes(changes)

    def test_rewrite_validation_preserves_phase_and_action_restrictions(self):
        for phase, action, value in (("read_response", "modify", "0x52"),
                                     ("write", "block", "0x52"),
                                     ("write", "modify", "*"),
                                     ("write", "modify", "0x80")):
            cfg = default_config()
            cfg["rules"] = [{"name": "invalid", "enabled": True, "phase": phase,
                             "match": {"address": "*", "payload": []}, "action": action,
                             **to_changes([{"target": "address", "value": value}])}]
            with self.subTest(phase=phase, action=action, value=value), self.assertRaises(ConfigError):
                validate(cfg)

    def test_reject_ambiguous_or_malformed_conditions(self):
        for conditions in ({}, [None], [{"target": "unknown", "value": 1}],
                           [{"target": "data", "value": 1}],
                           [{"target": "address", "value": "0x50", "offset": 0}],
                           [{"target": "address", "value": "*"},
                            {"target": "address", "value": "0x50"}]):
            with self.subTest(conditions=conditions), self.assertRaises(ConfigError):
                to_match(conditions)
