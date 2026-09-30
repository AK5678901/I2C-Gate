"""Interactive transaction overview shared with the rule editor's field labels."""
import tkinter as tk
from tkinter import ttk


class TransactionDiagram(ttk.Frame):
    COLORS = {"address": "#dcecff", "data": "#e1f3e5", "ack": "#fff0cc", "boundary": "#eeeeee", "override": "#ffd6d6"}

    def __init__(self, parent, navigate):
        super().__init__(parent)
        self.navigate = navigate
        self.kind, self.address, self.previous_address = "write", "*", "*"
        self.nack = ""
        self.override = "auto"
        self.selected = None
        self.boxes = []
        self.canvas = tk.Canvas(self, height=100, background="#fafafa", highlightthickness=0)
        self.canvas.pack(fill="x")
        self.canvas.bind("<Configure>", lambda _: self.draw())
        ttk.Label(self, text="通常時の模式図。同じ記号の設定欄に対応します。箱のクリックで設定欄へ移動。",
                  wraplength=660).pack(anchor="w")
        ttk.Label(self, text="黄色：本来の送信元の応答を中継 ／ 赤色：条件一致時にI2C-Gateが応答を上書き",
                  foreground="#923333", wraplength=660).pack(anchor="w")

    def update_transaction(self, kind, address, previous_address, nack="", override="auto"):
        self.kind, self.address, self.previous_address, self.nack = kind, address, previous_address, nack
        self.override = override
        if kind != "write→read" and self.selected in ("previous_address", "previous_data"):
            self.selected = None
        self.canvas.configure(height=226 if kind == "write→read" else 119)
        self.draw()

    def highlight(self, key):
        self.selected = key
        self.draw()

    def activate(self, key):
        self.highlight(key)
        self.navigate(key)

    def draw(self):
        c = self.canvas
        c.delete("all")
        self.boxes = []
        width = max(c.winfo_width(), 640)
        combined = self.kind == "write→read"
        read = self.kind != "write"
        rows = [(False, True), (True, False)] if combined else [(read, False)]
        for row, (is_read, previous) in enumerate(rows):
            top = 22 + row * 107
            prefix = "W" if previous or not is_read else "R"
            role = "先行WRITE：条件のみ" if previous else "READ：デバイス → ホスト" if is_read else "WRITE：ホスト → デバイス"
            c.create_text(7, top - 11, text=role, anchor="w", fill="#333333", font=("Yu Gothic UI", 9))
            address_key = "previous_address" if previous else "address"
            data_key = "previous_data" if previous else "data"
            addr = self.previous_address if previous else self.address
            ack_key = None if previous else "ack"
            sender = "ホスト" if is_read else "デバイス"
            receiver = "デバイス" if is_read else "ホスト"
            address_ack = "ACK/NACK\n送信: デバイス\n→ホスト"
            data_ack = f"ACK/NACK\n送信: {sender}\n→{receiver}"
            address_color = data_color = "ack"
            if not previous and self.nack == "address":
                address_ack = "NACK指定\n本来: デバイス\nGate上書き\n→ホスト"
                address_color = "override"
            elif not previous and self.nack:
                data_ack = f"NACK [{self.nack}]\n本来: {sender}\nGate上書き\n→{receiver}"
                data_color = "override"
            elif not previous and self.override in ("force_ack", "match_nack", "write_block"):
                response = "ACK強制" if self.override == "force_ack" else "NACK強制"
                data_ack = f"{response}\n本来: {sender}\nGate上書き\n→{receiver}"
                data_color = "override"
            labels = [
                ("続き" if combined and row else "START", "boundary", None, 0.09),
                (f"[{prefix}-A] アドレス\n{addr or '*'} + {'R' if is_read else 'W'}", "address", address_key, 0.23),
                (("" if previous else "[C] ") + address_ack, address_color, ack_key, 0.15),
                (f"[{prefix}-B] データ\n[0] [1] … [n]", "data", data_key, 0.23),
                (("" if previous else "[C] ") + data_ack, data_color, ack_key, 0.17),
                ("Sr" if previous else "STOP", "boundary", None, 0.09),
            ]
            x = 6
            usable = width - 60
            for index, (label, color, key, fraction) in enumerate(labels):
                box_width = usable * fraction
                tag = f"box_{row}_{index}"
                chosen = key is not None and key == self.selected
                c.create_rectangle(x, top, x + box_width, top + 70, fill=self.COLORS[color],
                                   outline="#1769aa" if chosen else "#b42318" if color == "override" else "#8a939b",
                                   width=3 if chosen else 2 if color == "override" else 1, tags=tag)
                c.create_text(x + box_width / 2, top + 35, text=label, width=box_width - 4,
                              font=("Yu Gothic UI", 8), justify="center", tags=tag)
                self.boxes.append((key, label))
                if key:
                    c.tag_bind(tag, "<Button-1>", lambda _, k=key: self.activate(k))
                    c.tag_bind(tag, "<Enter>", lambda _: c.configure(cursor="hand2"))
                    c.tag_bind(tag, "<Leave>", lambda _: c.configure(cursor=""))
                if index < len(labels) - 1:
                    c.create_line(x + box_width + 1, top + 35, x + box_width + 9, top + 35, arrow="last", fill="#777777")
                x += box_width + 10
            if previous:
                c.create_text(width - 9, top + 80, text="Sr = Repeated START（STOPを挟まず下段へ）", anchor="e",
                              font=("Yu Gothic UI", 8), fill="#555555")
        c.create_text(7, int(c.cget("height")) - 7,
                      text="データ欄は複数バイトを省略表示。各バイトの後にACK/NACKがあります。",
                      anchor="w", font=("Yu Gothic UI", 8), fill="#555555")


class ScrollablePage(ttk.Frame):
    """Keep the editor usable on small screens after adding the overview."""
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.body = ttk.Frame(self.canvas, padding=10)
        window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.body.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

    def reveal(self, widget):
        self.update_idletasks()
        y = widget.winfo_rooty() - self.body.winfo_rooty()
        self.canvas.yview_moveto(max(0, y - 8) / max(1, self.body.winfo_height()))
