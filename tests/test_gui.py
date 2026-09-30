from pathlib import Path
import tkinter as tk
import unittest
from i2c_gate.config import default_config, loads
from i2c_gate.gui import App, RuleDialog
from i2c_gate.rule_editor import ADDRESS_NACK, DATA_NACK, AUTO, FORCE_ACK, MATCH_NACK, MATCH_BLOCK
from i2c_gate.engine import simulate, simulate_combined
from i2c_gate.wire import encode_config


class GuiTests(unittest.TestCase):
    def test_ack_senders_and_override_colors(self):
        dialog = self.dialog()
        def ack_box(row, index):
            tag = f"box_{row}_{index}"
            rectangle, text = dialog.diagram.canvas.find_withtag(tag)
            return (dialog.diagram.canvas.itemcget(rectangle, "fill"),
                    dialog.diagram.canvas.itemcget(text, "text"))
        normal = dialog.diagram.COLORS["ack"]
        override = dialog.diagram.COLORS["override"]
        for kind in ("write", "read", "write→read"):
            dialog.kind.set(kind)
            dialog.response.set(AUTO)
            row = 1 if kind == "write→read" else 0
            sender = "デバイス" if kind == "write" else "ホスト"
            self.assertEqual(ack_box(row, 2)[0], normal)
            self.assertIn("送信: デバイス", ack_box(row, 2)[1])
            self.assertEqual(ack_box(row, 4)[0], normal)
            self.assertIn(f"送信: {sender}", ack_box(row, 4)[1])
            dialog.response.set(ADDRESS_NACK)
            self.assertEqual(ack_box(row, 2)[0], override)
            self.assertIn("Gate上書き", ack_box(row, 2)[1])
            self.assertIn("→ホスト", ack_box(row, 2)[1])
            self.assertEqual(ack_box(row, 4)[0], normal)
            dialog.response.set(DATA_NACK)
            dialog.nack_at.set("3")
            self.assertEqual(ack_box(row, 2)[0], normal)
            self.assertEqual(ack_box(row, 4)[0], override)
            self.assertIn("NACK [3]", ack_box(row, 4)[1])
            self.assertIn(f"本来: {sender}", ack_box(row, 4)[1])
            self.assertIn("→ホスト" if kind == "write" else "→デバイス", ack_box(row, 4)[1])
            if kind == "write→read":
                for index in (2, 4):
                    self.assertEqual(ack_box(0, index)[0], normal)
                    self.assertIn("送信: デバイス", ack_box(0, index)[1])
            dialog.response.set(AUTO)
            self.assertEqual(ack_box(row, 4)[0], normal)
        dialog.kind.set("read")
        for mode in (FORCE_ACK, MATCH_NACK):
            dialog.response.set(mode)
            self.assertEqual(ack_box(0, 4)[0], override)
            self.assertIn("Gate上書き", ack_box(0, 4)[1])
        dialog.response.set(MATCH_BLOCK)
        self.assertEqual(ack_box(0, 4)[0], normal)  # READ fill substitution does not override ACK.

    def test_diagrams_follow_transaction_and_nack_settings(self):
        dialog = self.dialog()
        for kind, tokens in (("write", ("[W-A]", "[W-B]")),
                             ("write→read", ("[W-A]", "[W-B]", "[R-A]", "[R-B]", "Sr")),
                             ("read", ("[R-A]", "[R-B]"))):
            dialog.kind.set(kind)
            labels = " ".join(label for _, label in dialog.diagram.boxes)
            for token in tokens:
                self.assertIn(token, labels)
            self.assertEqual(labels.count("STOP"), 1)
            self.assertEqual(labels.count("START"), 1)
            if kind == "read":
                self.assertNotIn("[W-A]", labels)
        dialog.address.set("0x52")
        self.assertIn("0x52", " ".join(label for _, label in dialog.diagram.boxes))
        dialog.response.set(DATA_NACK)
        dialog.nack_at.set("3")
        self.assertIn("NACK [3]", " ".join(label for _, label in dialog.diagram.boxes))
        dialog.response.set(ADDRESS_NACK)
        self.assertIn("NACK指定", " ".join(label for _, label in dialog.diagram.boxes))

    def test_diagram_navigation_and_field_focus(self):
        self.app.deiconify()
        dialog = RuleDialog(self.app, default_config())
        dialog.kind.set("write→read")
        dialog.geometry("700x620")
        dialog.update()
        dialog.diagram.activate("ack")
        dialog.update()
        self.assertEqual(dialog.tabs.index(dialog.tabs.select()), 1)
        self.assertEqual(dialog.diagram.selected, "ack")
        dialog.diagram.activate("previous_data")
        dialog.update()
        self.assertEqual(dialog.tabs.index(dialog.tabs.select()), 0)
        self.assertEqual(dialog.diagram.selected, "previous_data")
        dialog.current_address_entry.event_generate("<FocusIn>")
        self.assertEqual(dialog.diagram.selected, "address")
        dialog.tabs.select(1)
        dialog.diagram.activate("data")
        dialog.update()
        self.assertEqual(dialog.tabs.index(dialog.tabs.select()), 1)
        self.assertEqual(dialog.diagram.selected, "data")
        self.assertIn("R-B", dialog.change_data_label.cget("text"))
        self.assertLessEqual(dialog.diagram.canvas.bbox("all")[2], dialog.diagram.canvas.winfo_width())
        dialog.destroy()

    def test_and_or_controls_save_and_reopen(self):
        dialog = self.dialog()
        dialog.changes.add({"offset": 0, "operation": "AND", "value": 0xF0})
        dialog.changes.add({"offset": 0, "operation": "OR", "value": 0x05})
        dialog.apply()
        cfg = dialog.result
        self.assertEqual(simulate(cfg, "write", 0x50, bytes([0xAB])).payload, bytes([0xA5]))
        self.assertNotIn("mask", cfg["rules"][0]["patches"][0])
        again = self.dialog(cfg, 0)
        self.assertEqual([row[1][2].get() for row in again.changes.rows], ["AND", "OR"])
        again.apply()
        self.assertEqual(again.result, cfg)

    def setUp(self):
        try:
            self.app = App()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.app.withdraw()
        self.app.cfg = loads((Path(__file__).resolve().parents[1] / "examples/filters.json").read_text(encoding="utf-8"))
        self.app.refresh(0)

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.destroy()

    def dialog(self, cfg=None, index=None):
        dialog = RuleDialog(self.app, cfg or default_config(), index)
        dialog.withdraw()
        return dialog

    def test_existing_rules_round_trip(self):
        for i in range(len(self.app.cfg["rules"])):
            dialog = self.dialog(self.app.cfg, i)
            dialog.apply()
            self.assertEqual(encode_config(dialog.result), encode_config(self.app.cfg), i)

    def test_rule_edit_and_table(self):
        dialog = self.dialog(self.app.cfg, 0)
        dialog.name.set("変更したルール")
        dialog.apply()
        self.app.cfg = dialog.result
        self.app.changed(0)
        self.assertIn("変更したルール", self.app.tree.item("0", "values"))
        self.assertIn("write", self.app.tree.item("0", "values"))
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

    def test_address_redirect_write_and_read(self):
        for kind in ("write", "read"):
            dialog = self.dialog()
            dialog.kind.set(kind)
            dialog.address.set("0x52")
            dialog.destination.set("0x50")
            dialog.apply()
            result = simulate(dialog.result, kind, 0x52, b"abc")
            self.assertEqual((result.destination, result.payload), (0x50, b"abc"))

    def test_combined_read_modify_and_nack(self):
        dialog = self.dialog()
        dialog.kind.set("write→read")
        dialog.write_address.set("0x52")
        dialog.write_terms.add({"offset": 0, "value": 16})
        dialog.address.set("0x52")
        dialog.changes.add({"offset": 0, "value": 90})
        dialog.response.set(DATA_NACK)
        dialog.nack_at.set("1")
        dialog.apply()
        cfg = dialog.result
        self.assertIsNotNone(cfg)
        _, result = simulate_combined(cfg, 0x52, bytes([16]), 0x52, b"abc")
        self.assertEqual((result.payload, result.device_acks), (bytes([90, 98, 255]), (True, False)))
        _, wrong = simulate_combined(cfg, 0x52, bytes([17]), 0x52, b"abc")
        self.assertEqual(wrong.payload, b"abc")
        self.assertEqual(simulate(cfg, "read", 0x52, b"abc").payload, b"abc")
        again = self.dialog(cfg, 0)
        self.assertEqual(again.kind.get(), "write→read")
        again.apply()
        self.assertEqual(again.result, cfg)
        self.app.cfg = cfg
        self.app.direction.set("write→read")
        self.app.address.set("0x52")
        self.app.write_address.set("0x52")
        self.app.write_bytes.set("10")
        self.app.run_simulation()
        self.assertIn("5A AA FF", self.app.output.get("1.0", "end"))
        self.assertIn("ACK NACK", self.app.output.get("1.0", "end"))

    def test_combined_layout_and_large_condition_list(self):
        self.app.deiconify()
        dialog = RuleDialog(self.app, default_config())
        dialog.kind.set("write→read")
        for i in range(64):
            dialog.write_terms.add({"offset": i, "value": i})
        dialog.geometry("700x620")
        dialog.update()
        self.assertTrue(dialog.previous_box.winfo_ismapped())
        self.assertEqual(len(dialog.write_terms.get()), 64)
        for tab in (0, 1):
            dialog.tabs.select(tab)
            dialog.update()
            self.assertLess(dialog.error.winfo_rooty() + dialog.error.winfo_height(),
                            dialog.winfo_rooty() + dialog.winfo_height())
        dialog.destroy()

    def test_write_modify_and_nack_at_exact_byte(self):
        dialog = self.dialog()
        dialog.address.set("0x50")
        dialog.changes.add({"offset": 0, "value": 90})
        dialog.response.set(DATA_NACK)
        dialog.nack_at.set("2")
        dialog.apply()
        result = simulate(dialog.result, "write", 0x50, b"abcd")
        self.assertEqual((result.payload, result.nack_offset), (b"Zb", 2))

    def test_address_nack_in_all_three_modes(self):
        for kind in ("write", "read", "write→read"):
            dialog = self.dialog()
            dialog.kind.set(kind)
            dialog.response.set(ADDRESS_NACK)
            dialog.apply()
            cfg = dialog.result
            if kind == "write→read":
                _, result = simulate_combined(cfg, 0x50, b"x", 0x50, b"abc")
            else:
                result = simulate(cfg, kind, 0x50, b"abc")
            self.assertEqual(result.nack_offset, -1)
            self.assertFalse(result.downstream_access)

    def test_errors_are_inline_without_changing_config(self):
        dialog = self.dialog()
        dialog.response.set(ADDRESS_NACK)
        dialog.terms.add({"offset": 0, "value": 16})
        dialog.apply()
        self.assertIsNone(dialog.result)
        self.assertIn("アドレスNACK", dialog.error.cget("text"))
        dialog.destroy()
        dialog = self.dialog()
        dialog.changes.add({"offset": 0, "value": 90})
        dialog.terms.add({"offset": 1, "value": 16})
        dialog.apply()
        self.assertIsNone(dialog.result)
        self.assertIn("後続バイト", dialog.error.cget("text"))

    def test_simulation_output(self):
        self.app.run_simulation()
        output = self.app.output.get("1.0", "end")
        self.assertIn("10 00 55", output)
        self.app.changed()
        self.assertIn("再度検証", self.app.output.get("1.0", "end"))
