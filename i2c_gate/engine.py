"""Offline reference model. This does not perform I2C transfers."""
from dataclasses import dataclass
from typing import Optional
from .config import MAX_PAYLOAD, number, validate


@dataclass
class Result:
    action: Optional[str]
    destination: int
    payload: bytes
    rules: tuple
    downstream_access: bool
    explanation: str


def _evaluate(cfg, phase, address, data, streaming=False):
    for rule in cfg["rules"]:
        match = rule["match"]
        if not rule["enabled"] or rule["phase"] != phase:
            continue
        if match["address"] != "*" and match["address"] != address:
            continue
        if any(p["offset"] >= len(data) or (data[p["offset"]] & p["mask"]) != (p["value"] & p["mask"])
               for p in match["payload"]):
            continue
        # A short packet cannot satisfy a modification requiring absent bytes.
        if not streaming and any(p["offset"] >= len(data) for p in rule["patches"]):
            continue
        output = bytearray(data)
        for patch in rule["patches"]:
            pos, mask = patch["offset"], patch["mask"]
            if streaming and pos != len(data) - 1:
                continue
            output[pos] = (output[pos] & (255 ^ mask)) | (patch["value"] & mask)
        return rule["action"], rule.get("destination", address), bytes(output), (rule["name"],)
    return None, address, data, ()


def simulate(raw_config, direction, address, data):
    cfg = validate(raw_config)
    address = number(address, 0x08, 0x77, "address")
    if direction not in ("write", "read"):
        raise ValueError("directionはwrite/readを指定してください")
    if not isinstance(data, bytes) or len(data) > MAX_PAYLOAD:
        raise ValueError(f"ペイロードは最大{MAX_PAYLOAD}バイトのbytesが必要です")
    if direction == "write":
        if len(data) > cfg["bus"]["max_write_bytes"]:
            return Result("block", address, b"", (), False, "WRITEバッファ上限超過: 転送しない")
        action, dest, output, names = _evaluate(cfg, "write", address, data)
        return Result(action, dest, b"" if action == "block" else output, names, action != "block",
                      "WRITEをバッファ受信後に破棄（上流ACKの取消不可）" if action == "block" else "下流へ送信するデータ")
    action, dest, _, names = _evaluate(cfg, "read_request", address, b"")
    if action == "block":
        return Result("block", dest, b"", names, False, "READアドレスNACK: 下流READなし")
    output = bytearray()
    response_names = []
    overall = action
    blocked = False
    for offset in range(len(data)):
        if blocked:
            output.append(cfg["bus"]["read_block_fill"])
            continue
        action, _, current, matched = _evaluate(cfg, "read_response", address, data[:offset + 1], streaming=True)
        for name in matched:
            if name not in response_names:
                response_names.append(name)
        if action == "block":
            blocked = True
            overall = "block"
            output.append(cfg["bus"]["read_block_fill"])
        else:
            if action == "modify":
                overall = "modify"
            output.append(current[-1])
    return Result(overall, dest, bytes(output), names + tuple(response_names), True,
                  "条件成立バイト以降を代替値へ。先行データは返却済み。下流READの副作用は残る。"
                  if blocked else "バイトごとに判定してホストへ返すデータ。ACK/NACKに応じて下流READを継続／終了。")
