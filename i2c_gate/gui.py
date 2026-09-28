"""Windows-compatible Tk editor with USB upload and offline filter validation."""
import copy
import json
import os
from pathlib import Path
import tempfile
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import ConfigError, default_config, dumps, loads, number, validate
from .conditions import from_match
from .patches import editor_operations
from .rule_editor import RuleDialog, transaction_kind, response_label
from .engine import simulate, simulate_combined
from .wire import upload


def atomic_save(path, content):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = default_config()
        self.path = None
        self.dirty = False
        self.usb_queue = queue.Queue()
        self.usb_busy = False
        self.geometry("1100x740")
        self.minsize(900, 640)
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        self.option_add("*Font", "{Yu Gothic UI} 10")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.make_ui()
        self.refresh()
        self.usb_poll = self.after(100, self.poll_usb)
        self.bind("<Control-s>", lambda _: self.save())
        self.bind("<Control-o>", lambda _: self.open_file())
        self.bind("<Control-n>", lambda _: self.new_file())

    def make_ui(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="I2C-Gate", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        ttk.Label(root, text="WRITE・WRITE→READ・READのフィルタ設定とオフライン検証").pack(anchor="w", pady=(0, 12))
        bar = ttk.Frame(root)
        bar.pack(fill="x")
        for label, callback in (("新規", self.new_file), ("開く…", self.open_file), ("保存", self.save),
                                ("名前を付けて保存…", lambda: self.save(True)), ("設定全体を編集…", self.edit_json)):
            ttk.Button(bar, text=label, command=callback).pack(side="left", padx=(0, 6))
        usb = ttk.Frame(root)
        usb.pack(fill="x", pady=(10, 0))
        ttk.Label(usb, text="Pico USBポート").pack(side="left")
        self.port = tk.StringVar()
        self.port_list = ttk.Combobox(usb, textvariable=self.port, width=14)
        self.port_list.pack(side="left", padx=8)
        ttk.Button(usb, text="ポート一覧更新", command=self.scan_ports).pack(side="left")
        self.send_button = ttk.Button(usb, text="現在の設定をPicoへ送信", command=self.send_config)
        self.send_button.pack(side="left", padx=8)
        self.usb_status = ttk.Label(root, text="未接続。Picoへの反映は送信ボタンで行います。設定はRAMに保持します。")
        self.usb_status.pack(anchor="w", pady=(6, 0))
        self.info = ttk.Label(root)
        self.info.pack(anchor="w", pady=10)
        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True)
        rules_tab = ttk.Frame(tabs, padding=10)
        test_tab = ttk.Frame(tabs, padding=12)
        tabs.add(rules_tab, text="フィルタルール")
        tabs.add(test_tab, text="オフライン検証")
        ttk.Label(rules_tab, text="上から順に評価し、各タイミングで最初に一致したルールを適用します。").pack(anchor="w", pady=(0, 8))
        list_frame = ttk.Frame(rules_tab)
        list_frame.pack(fill="both", expand=True)
        columns = ("enabled", "name", "phase", "address", "conditions", "action", "destination")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse")
        for col, title, width in zip(columns,
                                    ("有効", "名前", "通信の種類", "アドレス条件", "条件 / 書換", "ACK/NACK", "下流宛先"),
                                    (45, 180, 105, 90, 95, 240, 80)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=40)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _: self.edit_rule())
        self.tree.bind("<<TreeviewSelect>>", self.show_rule)
        actions = ttk.Frame(rules_tab)
        actions.pack(fill="x", pady=8)
        for label, callback in (("追加", lambda: self.edit_rule(True)), ("編集", self.edit_rule),
                                ("複製", self.duplicate), ("削除", self.delete), ("有効/無効", self.toggle),
                                ("↑ 上へ", lambda: self.move(-1)), ("↓ 下へ", lambda: self.move(1))):
            ttk.Button(actions, text=label, command=callback).pack(side="left", padx=(0, 5))
        self.detail = tk.Text(rules_tab, height=8, wrap="word", state="disabled")
        self.detail.pack(fill="x")
        ttk.Label(test_tab, text="実際のI2C通信は行いません。READでは、デバイスが返す想定データを入力してください。\n"
                                "各バイト時点の受信済みデータで判定します。ACKや400 kHzのタイミングは実機検証が必要です。",
                  wraplength=900).pack(anchor="w", pady=(0, 15))
        row = ttk.Frame(test_tab)
        row.pack(fill="x")
        self.direction = tk.StringVar(value="write")
        self.address = tk.StringVar(value="0x50")
        ttk.Label(row, text="方向").pack(side="left")
        ttk.Combobox(row, textvariable=self.direction, values=("write", "write→read", "read"), state="readonly", width=14).pack(side="left", padx=8)
        ttk.Label(row, text="上流アドレス").pack(side="left")
        ttk.Entry(row, textvariable=self.address, width=12).pack(side="left", padx=8)
        ttk.Label(test_tab, text="データ（16進数、例: 10 AA 55）").pack(anchor="w", pady=(15, 5))
        self.data_input = tk.Text(test_tab, height=5, wrap="word")
        self.data_input.pack(fill="x")
        self.data_input.insert("1.0", "10 AA 55")
        previous_row = ttk.Frame(test_tab)
        previous_row.pack(fill="x", pady=8)
        ttk.Label(previous_row, text="write→read の先行WRITE：アドレス").pack(side="left")
        self.write_address = tk.StringVar(value="0x50")
        self.write_bytes = tk.StringVar(value="10")
        ttk.Entry(previous_row, textvariable=self.write_address, width=8).pack(side="left", padx=5)
        ttk.Label(previous_row, text="WRITEデータ（16進）").pack(side="left")
        ttk.Entry(previous_row, textvariable=self.write_bytes, width=24).pack(side="left", padx=5)
        ttk.Button(test_tab, text="現在のルールで検証", command=self.run_simulation).pack(anchor="w", pady=12)
        self.output = tk.Text(test_tab, height=12, state="disabled", wrap="word")
        self.output.pack(fill="both", expand=True)
        self.status = ttk.Label(root, text="")
        self.status.pack(anchor="w", pady=(10, 0))

    def selected(self):
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def refresh(self, selection=None):
        self.title(f'I2C-Gate — {self.path.name if self.path else "未保存"}{" *" if self.dirty else ""}')
        self.tree.delete(*self.tree.get_children())
        for index, rule in enumerate(self.cfg["rules"]):
            address = rule["match"]["address"]
            self.tree.insert("", "end", iid=str(index), values=("✓" if rule["enabled"] else "—", rule["name"],
                transaction_kind(rule), "*" if address == "*" else f"0x{address:02X}",
                f'{len(from_match(rule["match"]))} / {len(rule.get("patches", []))}', response_label(rule),
                f'0x{rule["destination"]:02X}' if "destination" in rule else "維持"))
        if selection is not None and 0 <= selection < len(self.cfg["rules"]):
            self.tree.selection_set(str(selection))
            self.tree.see(str(selection))
        bus = self.cfg["bus"]
        self.info.configure(text=f'設定: {bus["speed_hz"] / 1000:g} kHz  /  ストレッチ上限 {bus["stretch_timeout_us"]} µs  /  '
                                 f'WRITE上限 {bus["max_write_bytes"]} B  /  既定: 全アドレス通過（0x08〜0x77）', wraplength=1000)
        self.status.configure(text=f'{len(self.cfg["rules"])} ルール  |  {self.path or "ファイル未選択"}  |  '
                                   f'{"未保存の変更あり" if self.dirty else "変更なし"}')
        self.show_rule()

    @staticmethod
    def set_text(widget, content):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def show_rule(self, _event=None):
        index = self.selected()
        if index is None:
            self.set_text(self.detail, "ルールを選択すると条件と書き換え内容を表示します。")
            return
        rule = self.cfg["rules"][index]
        def terms_text(terms):
            return "、".join(
                f'データ[{p["offset"]}] {p["operation"]} 0x{p["value"]:02X}' if "operation" in p else
                f'データ[{p["offset"]}] = 0x{p["value"]:02X}（マスク0x{p["mask"]:02X}）' for p in terms) or "なし"
        text = f'{transaction_kind(rule)} / {rule["name"]}\n'
        if "write" in rule["match"]:
            prev = rule["match"]["write"]
            text += f'先行WRITE: アドレス {RuleDialog.address_text(prev["address"])}、{terms_text(prev["payload"])}\n'
        text += f'一致条件: アドレス {RuleDialog.address_text(rule["match"]["address"])}、{terms_text(rule["match"]["payload"])}\n'
        text += f'ACK/NACK: {response_label(rule)}\n'
        text += f'下流宛先: {RuleDialog.address_text(rule["destination"]) if "destination" in rule else "維持"}\n'
        text += f'データ書き換え: {terms_text(editor_operations(rule.get("patches", [])))}'
        self.set_text(self.detail, text)

    def changed(self, selection=None):
        self.dirty = True
        self.refresh(selection)
        self.set_text(self.output, "設定が変更されました。再度検証してください。")

    def edit_rule(self, add=False):
        index = None if add else self.selected()
        if not add and index is None:
            return
        dialog = RuleDialog(self, self.cfg, index)
        self.wait_window(dialog)
        if dialog.result is not None:
            self.cfg = dialog.result
            self.changed(len(self.cfg["rules"]) - 1 if add else index)

    def duplicate(self):
        index = self.selected()
        if index is None:
            return
        candidate = copy.deepcopy(self.cfg)
        rule = copy.deepcopy(candidate["rules"][index])
        names = {r["name"] for r in candidate["rules"]}
        count = 1
        base = rule["name"][:65]
        while f"{base} (copy {count})" in names:
            count += 1
        rule["name"] = f"{base} (copy {count})"
        candidate["rules"].insert(index + 1, rule)
        try:
            self.cfg = validate(candidate)
        except ConfigError as error:
            messagebox.showerror("複製できません", str(error), parent=self)
            return
        self.changed(index + 1)

    def delete(self):
        index = self.selected()
        if index is not None:
            del self.cfg["rules"][index]
            self.changed(min(index, len(self.cfg["rules"]) - 1))

    def toggle(self):
        index = self.selected()
        if index is not None:
            self.cfg["rules"][index]["enabled"] = not self.cfg["rules"][index]["enabled"]
            self.changed(index)

    def move(self, delta):
        index = self.selected()
        if index is not None and 0 <= index + delta < len(self.cfg["rules"]):
            rules = self.cfg["rules"]
            rules[index], rules[index + delta] = rules[index + delta], rules[index]
            self.changed(index + delta)

    def discard_or_save(self):
        if not self.dirty:
            return True
        response = messagebox.askyesnocancel("未保存の変更", "現在の変更を保存しますか？", parent=self)
        return False if response is None else self.save() if response else True

    def new_file(self):
        if self.discard_or_save():
            self.cfg, self.path, self.dirty = default_config(), None, False
            self.refresh()
            self.set_text(self.output, "")

    def open_file(self):
        if not self.discard_or_save():
            return
        path = filedialog.askopenfilename(filetypes=[("JSON設定", "*.json")], parent=self)
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig") as source:
                cfg = loads(source.read(65537))
        except (OSError, ValueError) as error:
            messagebox.showerror("読み込みに失敗しました", str(error), parent=self)
            return
        self.cfg, self.path, self.dirty = cfg, Path(path), False
        self.refresh()
        self.set_text(self.output, "")

    def save(self, save_as=False):
        path = self.path
        if path is None or save_as:
            chosen = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON設定", "*.json")], parent=self)
            if not chosen:
                return False
            path = Path(chosen)
        try:
            atomic_save(path, dumps(self.cfg))
        except (OSError, ValueError) as error:
            messagebox.showerror("保存に失敗しました", str(error), parent=self)
            return False
        self.path, self.dirty = path, False
        self.refresh(self.selected())
        return True

    def edit_json(self):
        dialog = tk.Toplevel(self)
        dialog.title("バス設定・ルール全体をJSONで編集")
        dialog.geometry("780x640")
        dialog.transient(self)
        dialog.grab_set()
        ttk.Label(dialog, text="バス速度は仮設定です。設定値の検証は、実機でその速度に対応することを保証しません。",
                  wraplength=740, padding=10).pack(fill="x")
        editor = tk.Text(dialog, undo=True, wrap="none")
        editor.pack(fill="both", expand=True, padx=10)
        editor.insert("1.0", dumps(self.cfg))

        def apply():
            try:
                cfg = loads(editor.get("1.0", "end"))
            except ValueError as error:
                messagebox.showerror("設定エラー", str(error), parent=dialog)
                return
            self.cfg = cfg
            self.changed()
            dialog.destroy()

        ttk.Button(dialog, text="検証して適用", command=apply).pack(pady=10)

    def run_simulation(self):
        try:
            address = number(self.address.get().strip(), 0x08, 0x77, "address")
            data = bytes.fromhex(self.data_input.get("1.0", "end").strip())
            if self.direction.get() == "write→read":
                _, result = simulate_combined(self.cfg, self.write_address.get().strip(),
                                              bytes.fromhex(self.write_bytes.get()), address, data)
            else:
                result = simulate(self.cfg, self.direction.get(), address, data)
            text = f'動作: {result.action or "変更なし（既定動作）"}\n一致ルール: {" → ".join(result.rules) or "なし（既定動作）"}\n'
            text += f'下流宛先: 0x{result.destination:02X}\n下流アクセス: {"あり" if result.downstream_access else "なし"}\n'
            if result.nack_offset is not None:
                text += f'ホストへNACK: {"アドレス" if result.nack_offset == -1 else f"データ[{result.nack_offset}]"}\n'
            if result.device_acks:
                text += '下流への応答: ' + ' '.join('ACK' if a else 'NACK' for a in result.device_acks) + '\n'
            text += f'出力 ({len(result.payload)} B): {result.payload.hex(" ").upper() or "（なし）"}\n\n{result.explanation}'
            self.set_text(self.output, text)
        except ValueError as error:
            messagebox.showerror("検証できません", str(error), parent=self)

    def close(self):
        if self.usb_busy:
            messagebox.showinfo("USB送信中", "設定反映の結果が返るまでお待ちください。", parent=self)
            return
        if self.discard_or_save():
            self.destroy()

    def destroy(self):
        if getattr(self, "usb_poll", None) is not None:
            self.after_cancel(self.usb_poll)
            self.usb_poll = None
        super().destroy()

    def scan_ports(self):
        try:
            from serial.tools import list_ports
            ports = [item.device for item in list_ports.comports()]
            self.port_list.configure(values=ports)
            if not self.port.get() and ports:
                self.port.set(ports[0])
            self.usb_status.configure(text=f"{len(ports)}個のシリアルポートを検出。Picoのポートを選択してください。")
        except ImportError:
            messagebox.showerror("USB依存パッケージ", "py -m pip install -r requirements.txt を実行してください。", parent=self)

    def send_config(self):
        if self.usb_busy:
            return
        port = self.port.get().strip()
        if not port:
            messagebox.showerror("ポート未指定", "PicoのCOMポートを選択してください。", parent=self)
            return
        snapshot = copy.deepcopy(self.cfg)
        self.usb_busy = True
        self.send_button.configure(state="disabled")
        self.usb_status.configure(text="USB送信中… 送信開始時の設定を反映します。")

        def work():
            try:
                self.usb_queue.put((True, upload(port, snapshot), snapshot))
            except Exception as error:
                self.usb_queue.put((False, str(error), snapshot))

        threading.Thread(target=work, daemon=True).start()

    def poll_usb(self):
        try:
            ok, message, snapshot = self.usb_queue.get_nowait()
            self.usb_busy = False
            self.send_button.configure(state="normal")
            if ok and snapshot != self.cfg:
                message += " 現在の編集内容は送信内容と異なります。"
            self.usb_status.configure(text=("成功: " if ok else "失敗: ") + message)
        except queue.Empty:
            pass
        self.usb_poll = self.after(100, self.poll_usb)


def main():
    App().mainloop()
