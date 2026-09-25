"""Binary USB envelope; user-facing configuration remains JSON."""
import struct
import zlib
from .config import validate

MAGIC = b"I2CG"
MAX_BINARY = 65536


def encode_config(raw):
    cfg = validate(raw)
    bus = cfg["bus"]
    result = bytearray(struct.pack("<BIIHBBB", 1, bus["speed_hz"], bus["stretch_timeout_us"],
                                   bus["max_write_bytes"], bus["read_block_fill"],
                                   112, len(cfg["rules"])))
    # Keep the v1 wire layout, enabling every supported address on older firmware too.
    result.extend(range(0x08, 0x78))
    for rule in cfg["rules"]:
        # Firmware uses rule indices for diagnostics; names stay in the JSON/GUI.
        result.extend(struct.pack("<BBBBBBB", int(rule["enabled"]),
            ("write", "read_request", "read_response").index(rule["phase"]),
            {"modify": 1, "block": 2}[rule["action"]],
            255 if rule["match"]["address"] == "*" else rule["match"]["address"],
            rule.get("destination", 255), len(rule["match"]["payload"]), len(rule["patches"])))
        for item in rule["match"]["payload"] + rule["patches"]:
            result.extend(struct.pack("<HBB", item["offset"], item["value"], item["mask"]))
    if len(result) > MAX_BINARY:
        raise ValueError("USB設定データが上限を超えています")
    return bytes(result)


def frame(raw):
    payload = encode_config(raw)
    return MAGIC + struct.pack("<II", len(payload), zlib.crc32(payload)) + payload


def upload(port, cfg, serial_factory=None):
    if serial_factory is None:
        try:
            import serial
        except ImportError:
            raise RuntimeError("USB転送には py -m pip install -r requirements.txt を実行してください") from None
        serial_factory = serial.Serial
    packet = frame(cfg)
    with serial_factory(port, baudrate=115200, timeout=5, write_timeout=5) as link:
        link.reset_input_buffer()
        link.write(b"HELLO\n")
        if link.readline().strip() != b"I2C-GATE 1 GPIO-EXPERIMENTAL":
            raise RuntimeError("対応するI2C-Gateファームウェアの応答がありません")
        if link.write(packet) != len(packet):
            raise RuntimeError("USB送信が途中で終了しました。反映状態は未確認です")
        link.flush()
        reply = link.readline().strip()
        expected = f"OK {zlib.crc32(packet[12:]):08X}".encode("ascii")
        if reply != expected:
            raise RuntimeError(f"設定反映を確認できません: {reply.decode('ascii', errors='replace') or 'タイムアウト'}")
    return "Pico RAMへ設定を反映しました。電源断で消去されます。"
