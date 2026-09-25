from pathlib import Path
import json
import tkinter as tk
import unittest

from i2c_gate.config import loads
from i2c_gate.gui import App, RuleDialog
from i2c_gate.config import default_config
from i2c_gate.engine import simulate


class GuiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.app = App()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.app.withdraw()
        self.app.cfg = loads((Path(__file__).resolve().parents[1] / "examples" / "filters.json").read_text(encoding="utf-8"))
        self.app.refresh(0)

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.destroy()

    def test_rule_edit_and_table(self):
        dialog = RuleDialog(self.app, self.app.cfg, 0)
        dialog.withdraw()
        dialog.fields["name"].set("変更したルール")
        dialog.apply()
        self.assertEqual(dialog.result["rules"][0]["name"], "変更したルール")
        self.app.cfg = dialog.result
        self.app.changed(0)
        self.assertIn("変更したルール", self.app.tree.item("0", "values"))
        self.assertTrue(self.app.dirty)

    def test_priority_duplicate_toggle_delete(self):
        name = self.app.cfg["rules"][0]["name"]
        self.app.move(1)
        self.assertEqual(self.app.cfg["rules"][1]["name"], name)
        self.app.duplicate()
        self.assertEqual(len(self.app.cfg["rules"]), 7)
        self.app.toggle()
        self.assertFalse(self.app.cfg["rules"][2]["enabled"])
        self.app.delete()
        self.assertEqual(len(self.app.cfg["rules"]), 6)

    def test_address_and_data_conditions_control_destination(self):
        dialog = RuleDialog(self.app, default_config())
        dialog.withdraw()
        dialog.add_condition("address")
        dialog.add_change("address")
        dialog.apply()
        cfg = dialog.result
        self.assertEqual(simulate(cfg, "write", 0x50, b"\x10").destination, 0x52)
        self.assertEqual(simulate(cfg, "write", 0x50, b"\x20").destination, 0x52)

        dialog = RuleDialog(self.app, cfg, 0)
        dialog.withdraw()
        dialog.predicates.delete("1.0", "end")
        dialog.predicates.insert("1.0", json.dumps([
            {"target": "address", "value": "0x50"},
            {"target": "data", "offset": 0, "value": "0x10"}]))
        dialog.apply()
        cfg = dialog.result
        self.assertEqual(simulate(cfg, "write", 0x50, b"\x10").destination, 0x52)
        self.assertEqual(simulate(cfg, "write", 0x50, b"\x20").destination, 0x50)

    def test_read_request_address_condition(self):
        dialog = RuleDialog(self.app, default_config())
        dialog.withdraw()
        dialog.fields["phase"].set("read_request")
        dialog.add_change("address")
        dialog.add_condition("address")
        dialog.apply()
        self.assertEqual(simulate(dialog.result, "read", 0x50, b"\x10").destination, 0x52)

    def test_address_and_data_rewrite_round_trip_and_removal(self):
        dialog = RuleDialog(self.app, default_config())
        dialog.withdraw()
        dialog.add_condition("address")
        dialog.fields["action"].set("modify")
        dialog.patches.delete("1.0", "end")
        dialog.patches.insert("1.0", json.dumps([
            {"target": "address", "value": "0x52"},
            {"target": "data", "offset": 0, "value": "0x20"}]))
        dialog.apply()
        cfg = dialog.result
        result = simulate(cfg, "write", 0x50, b"\x10\xAA")
        self.assertEqual((result.destination, result.payload), (0x52, b"\x20\xAA"))
        dialog = RuleDialog(self.app, cfg, 0)
        dialog.withdraw()
        dialog.apply()
        self.assertEqual(dialog.result, cfg)
        dialog = RuleDialog(self.app, cfg, 0)
        dialog.withdraw()
        dialog.patches.delete("1.0", "end")
        dialog.patches.insert("1.0", '[{"target": "data", "offset": 0, "value": "0x20"}]')
        dialog.apply()
        result = simulate(dialog.result, "write", 0x50, b"\x10\xAA")
        self.assertEqual((result.destination, result.payload), (0x50, b"\x20\xAA"))

    def test_simulation_output(self):
        self.app.run_simulation()
        output = self.app.output.get("1.0", "end")
        self.assertIn("10 00 55", output)
        self.assertIn("modify", output)
        self.app.changed()
        self.assertIn("再度検証", self.app.output.get("1.0", "end"))
