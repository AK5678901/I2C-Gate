"""Form-based transaction rule editor; JSON remains a persistence format."""
import copy
import tkinter as tk
from tkinter import ttk

from .config import ConfigError, number, validate
from .patches import editor_operations

KINDS = ("write", "write→read", "read")
AUTO = "自動（相手の応答に追従）"
ADDRESS_NACK = "アドレスでNACK"
DATA_NACK = "指定データバイトでNACK"
MATCH_BLOCK = "条件成立時に遮断（既存ルール）"
FORCE_ACK = "下流へACKを返す（既存ルール）"
MATCH_NACK = "条件成立時に下流へNACK（既存ルール）"


def transaction_kind(rule):
    return "write" if rule["phase"] == "write" else "write→read" if "write" in rule["match"] else "read"


def response_label(rule):
    if rule["action"] == "block":
        return ADDRESS_NACK if rule["phase"] == "read_request" or not rule["match"]["payload"] and rule["phase"] == "write" else MATCH_BLOCK
    if "nack_at" in rule:
        return f'{DATA_NACK}（位置 {rule["nack_at"]}）'
    return {"host": AUTO, "ack": FORCE_ACK, "nack": MATCH_NACK}[rule.get("ack", "host")]


class ByteTable(ttk.Frame):
    """Editable byte rows with an always-visible add button and a scrollable body."""
    def __init__(self, parent, terms=(), changes=False):
        super().__init__(parent)
        self.changes = changes
        self.rows = []
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="位置（0始まり）     設定値（0x付き16進数）     " + ("演算" if changes else "マスク")).pack(side="left")
        ttk.Button(toolbar, text="＋ バイトを追加", command=self.add).pack(side="right")
        area = ttk.Frame(self)
        area.pack(fill="both", expand=True)
        canvas = tk.Canvas(area, height=105, highlightthickness=0)
        scroll = ttk.Scrollbar(area, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.body = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=self.body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        self.body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        for term in terms:
            self.add(term)

    def add(self, term=None):
        if self.changes and term is not None and "operation" not in term:
            for operation in editor_operations([term]):
                self.add(operation)
            return
        term = term or ({"offset": 0, "value": 255, "operation": "AND"} if self.changes
                        else {"offset": 0, "value": 0, "mask": 255})
        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=2)
        values = [tk.StringVar(value=str(term["offset"])),
                  tk.StringVar(value=f'0x{term["value"]:02X}'),
                  tk.StringVar(value=term["operation"] if self.changes else f'0x{term.get("mask", 255):02X}')]
        entry = (row, values)
        self.rows.append(entry)
        for index, var in enumerate(values):
            if self.changes and index == 2:
                ttk.Combobox(row, textvariable=var, values=("AND", "OR"), state="readonly", width=15).pack(side="left", padx=3)
            else:
                ttk.Entry(row, textvariable=var, width=17).pack(side="left", padx=3)
        def remove():
            self.rows.remove(entry)
            row.destroy()
        ttk.Button(row, text="削除", command=remove).pack(side="left", padx=4)

    def get(self):
        if self.changes:
            return [{"offset": number(v[0].get().strip(), 0, 4095, "バイト位置"),
                     "value": number(v[1].get().strip(), 0, 255, "設定値"), "operation": v[2].get()}
                    for _, v in self.rows]
        return [{"offset": number(v[0].get().strip(), 0, 4095, "バイト位置"),
                 "value": number(v[1].get().strip(), 0, 255, "値"),
                 "mask": number(v[2].get().strip(), 0, 255, "マスク")}
                for _, v in self.rows]


class RuleDialog(tk.Toplevel):
    def __init__(self, parent, config, index=None):
        super().__init__(parent)
        self.title("フィルタ設定")
        self.config_data = copy.deepcopy(config)
        self.index, self.result = index, None
        self.transient(parent)
        self.grab_set()
        self.geometry("820x730")
        self.minsize(700, 620)
        item = config["rules"][index] if index is not None else {
            "name": "新しいルール", "enabled": True, "phase": "write", "action": "modify",
            "match": {"address": "*", "payload": []}, "patches": []}
        self.original = item
        self.kind = tk.StringVar(value=transaction_kind(item))
        self.name = tk.StringVar(value=item["name"])
        self.enabled = tk.BooleanVar(value=item["enabled"])
        self.address = tk.StringVar(value=self.address_text(item["match"]["address"]))
        previous = item["match"].get("write", {"address": "*", "payload": []})
        self.write_address = tk.StringVar(value=self.address_text(previous["address"]))
        self.destination = tk.StringVar(value=self.address_text(item["destination"]) if "destination" in item else "")
        mode = response_label(item)
        self.response = tk.StringVar(value=DATA_NACK if "nack_at" in item else mode)
        self.nack_at = tk.StringVar(value=str(item.get("nack_at", 0)))

        box = ttk.Frame(self, padding=14)
        box.pack(fill="both", expand=True)
        head = ttk.Frame(box)
        head.pack(fill="x")
        ttk.Label(head, text="通信の種類").pack(side="left")
        ttk.Combobox(head, textvariable=self.kind, values=KINDS, state="readonly", width=16).pack(side="left", padx=8)
        ttk.Checkbutton(head, text="有効", variable=self.enabled).pack(side="right")
        names = ttk.Frame(box)
        names.pack(fill="x", pady=10)
        ttk.Label(names, text="ルール名").pack(side="left")
        ttk.Entry(names, textvariable=self.name).pack(side="left", fill="x", expand=True, padx=8)
        self.hint = ttk.Label(box, wraplength=740)
        self.hint.pack(fill="x", pady=(0, 10))
        self.tabs = ttk.Notebook(box)
        self.tabs.pack(fill="both", expand=True)
        conditions = ttk.Frame(self.tabs, padding=10)
        actions = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(conditions, text="1. 一致条件")
        self.tabs.add(actions, text="2. ACK/NACK・書き換え")

        self.previous_box = ttk.LabelFrame(conditions, text="先行WRITEの一致条件", padding=8)
        self.previous_box.pack(fill="both", expand=True, pady=(0, 8))
        self.address_entry(self.previous_box, "WRITEアドレス", self.write_address)
        self.write_terms = ByteTable(self.previous_box, previous["payload"])
        self.write_terms.pack(fill="both", expand=True)
        self.current_box = ttk.LabelFrame(conditions, text="一致条件", padding=8)
        self.current_box.pack(fill="both", expand=True)
        self.address_entry(self.current_box, "I2Cアドレス", self.address)
        self.terms = ByteTable(self.current_box, item["match"]["payload"])
        self.terms.pack(fill="both", expand=True)
        ttk.Label(conditions, text="アドレスは7ビット（0x08〜0x77）、* は任意。R/Wは通信の種類で指定します。\n"
                  "データ条件は全行AND。行がなければデータ条件なし。値・マスクは0x付きで入力。",
                  wraplength=730).pack(fill="x", pady=8)

        response_box = ttk.LabelFrame(actions, text="ACK/NACK", padding=10)
        response_box.pack(fill="x")
        modes = [AUTO, ADDRESS_NACK, DATA_NACK]
        if mode not in modes and "nack_at" not in item:
            modes.append(mode)
        ttk.Combobox(response_box, textvariable=self.response, values=modes, state="readonly", width=45).pack(anchor="w")
        nack_row = ttk.Frame(response_box)
        nack_row.pack(fill="x", pady=8)
        ttk.Label(nack_row, text="NACKするデータ位置（0始まり）").pack(side="left")
        self.nack_entry = ttk.Entry(nack_row, textvariable=self.nack_at, width=10)
        self.nack_entry.pack(side="left", padx=8)
        self.ack_hint = ttk.Label(response_box, wraplength=700)
        self.ack_hint.pack(fill="x")
        change_box = ttk.LabelFrame(actions, text="書き換え内容", padding=8)
        change_box.pack(fill="both", expand=True, pady=10)
        self.address_entry(change_box, "変更後の下流アドレス（空欄なら維持）", self.destination)
        self.changes = ByteTable(change_box, item.get("patches", []), changes=True)
        self.changes.pack(fill="both", expand=True)
        ttk.Label(actions, text="書き換えは 元データ AND/OR 設定値。同じ位置の複数行は上から順に適用します。\n"
                  "例：AND 0xF0で下位4ビットを消去、OR 0x05でビット0・2をセット。\n"
                  "READのアドレス変更とデータ変更は別ルールで指定してください。",
                  wraplength=730).pack(fill="x")
        self.error = ttk.Label(box, foreground="#b00020", wraplength=740)
        self.error.pack(fill="x", pady=6)
        buttons = ttk.Frame(box)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="キャンセル", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="適用", command=self.apply).pack(side="right", padx=8)
        self.kind.trace_add("write", lambda *_: self.update_mode())
        self.response.trace_add("write", lambda *_: self.update_mode())
        self.update_mode()
        self.bind("<Escape>", lambda _: self.destroy())

    @staticmethod
    def address_text(address):
        return "*" if address == "*" else f"0x{address:02X}"

    @staticmethod
    def address_entry(parent, label, variable):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text=label).pack(side="left")
        ttk.Entry(row, textvariable=variable, width=12).pack(side="left", padx=8)

    def update_mode(self):
        combined = self.kind.get() == "write→read"
        if combined:
            self.previous_box.pack(fill="both", expand=True, before=self.current_box, pady=(0, 8))
        else:
            self.previous_box.pack_forget()
        read = self.kind.get() != "write"
        self.current_box.configure(text="READの一致条件" if read else "WRITEの一致条件")
        self.hint.configure(text=("先行WRITEが一致した同じRepeated START区間のREADに適用します。STOPで条件を解除します。"
                                 if combined else "受信したWRITEに適用します。" if not read else
                                 "READに適用します。直前のWRITEの有無は問いません。"))
        self.ack_hint.configure(text=("アドレスNACKはホストへ返します。データNACKは下流デバイスへ返し、\n"
                                      "その後のホストの追加READには代替値を返します。自動はホストACK/NACKに追従。"
                                      if read else "ホストへ返す応答です。自動は下流デバイスのACK/NACKを中継します。\n"
                                      "指定位置のデータは下流へ転送せずNACKします。先行バイトは取り消せません。"))
        self.nack_entry.configure(state="normal" if self.response.get() == DATA_NACK else "disabled")

    def build_rule(self):
        kind, response = self.kind.get(), self.response.get()
        if kind not in KINDS:
            raise ConfigError("通信の種類を選択してください")
        terms, patches = self.terms.get(), self.changes.get()
        dest = self.destination.get().strip()
        address_stage = response == ADDRESS_NACK or bool(dest)
        phase = "write" if kind == "write" else "read_request" if address_stage else "read_response"
        if response == ADDRESS_NACK and (terms or patches or dest):
            raise ConfigError("アドレスNACKには現在の通信のデータ条件・書き換えを指定できません。先行WRITE条件は指定できます。")
        if kind != "write" and dest and (terms or patches or response != AUTO):
            raise ConfigError("READアドレス変更とデータ条件・変更・NACKは別ルールで設定してください。")
        rule = {"name": self.name.get().strip(), "enabled": self.enabled.get(), "phase": phase,
                "match": {"address": self.address.get().strip() or "*", "payload": terms},
                "action": "block" if response in (ADDRESS_NACK, MATCH_BLOCK) else "modify", "patches": patches}
        if kind == "write→read":
            rule["match"]["write"] = {"address": self.write_address.get().strip() or "*", "payload": self.write_terms.get()}
        if dest:
            rule["destination"] = dest
        if response == DATA_NACK:
            rule.update(ack="nack", nack_at=number(self.nack_at.get().strip(), 0, 4095, "NACK位置"))
        elif response in (FORCE_ACK, MATCH_NACK):
            rule["ack"] = "ack" if response == FORCE_ACK else "nack"
        elif self.original.get("ack") == "host" and phase == "read_response":
            rule["ack"] = "host"
        return rule

    def apply(self):
        try:
            rule = self.build_rule()
            candidate = copy.deepcopy(self.config_data)
            if self.index is None:
                candidate["rules"].append(rule)
            else:
                candidate["rules"][self.index] = rule
            self.result = validate(candidate)
        except (ValueError, TypeError) as error:
            self.error.configure(text=str(error))
            return
        self.destroy()
