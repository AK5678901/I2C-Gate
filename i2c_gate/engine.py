"""Offline byte callback reference model. This does not perform I2C transfers."""
from dataclasses import dataclass
from typing import Optional
from .config import MAX_PAYLOAD, number, validate


@dataclass(frozen=True)
class WriteContext:
    address: int
    data: bytes


@dataclass
class Result:
    action: Optional[str]
    destination: int
    payload: bytes
    rules: tuple
    downstream_access: bool
    explanation: str
    nack_offset: Optional[int] = None  # -1 = address; otherwise zero-based data offset
    device_acks: tuple = ()  # True = ACK, False = NACK


def _terms_match(terms, data):
    return all(p["offset"] < len(data) and
               (data[p["offset"]] & p["mask"]) == (p["value"] & p["mask"]) for p in terms)


def _match(cfg, phase, address, data, write=None, at_address=False):
    for rule in cfg["rules"]:
        match = rule["match"]
        if not rule["enabled"] or rule["phase"] != phase:
            continue
        if match["address"] != "*" and match["address"] != address:
            continue
        if at_address and rule["action"] != "block" and "destination" not in rule:
            continue
        previous = match.get("write")
        if previous is not None:
            if write is None or (previous["address"] != "*" and previous["address"] != write.address):
                continue
            if not _terms_match(previous["payload"], write.data):
                continue
        if _terms_match(match["payload"], data):
            return rule
    return None


def _byte(rule, offset, value):
    if rule and rule["action"] == "modify":
        for patch in rule["patches"]:
            if patch["offset"] == offset:
                if patch.get("operation") == "AND":
                    value &= patch["value"]
                elif patch.get("operation") == "OR":
                    value |= patch["value"]
                else:
                    value = (value & (255 ^ patch["mask"])) | (patch["value"] & patch["mask"])
    return value


def simulate(raw_config, direction, address, data, *, write_context=None,
             host_acks=None, device_address_ack=True, device_write_acks=None):
    """Simulate one segment, including optional physical ACK/NACK responses.

    data is the original host WRITE or device READ data. host_acks and
    device_write_acks contain booleans (True = ACK). Default host NACK is last.
    write_context must belong to the same repeated-START chain, never a prior STOP.
    """
    cfg = validate(raw_config)
    address = number(address, 0x08, 0x77, "address")
    if direction not in ("write", "read"):
        raise ValueError("directionはwrite/readを指定してください")
    if not isinstance(data, bytes) or len(data) > MAX_PAYLOAD:
        raise ValueError(f"ペイロードは最大{MAX_PAYLOAD}バイトのbytesが必要です")
    for acks in (host_acks, device_write_acks):
        if acks is not None and (len(acks) != len(data) or any(type(a) is not bool for a in acks)):
            raise ValueError("ACK配列はデータ長と同じ長さのbool配列が必要です")
    request = _match(cfg, "write" if direction == "write" else "read_request",
                     address, b"", write_context, True)
    names = [request["name"]] if request else []
    dest = request.get("destination", address) if request else address
    overall = request["action"] if request else None
    if request and request["action"] == "block":
        return Result("block", dest, b"", tuple(names), False, "アドレスNACK: 下流へアドレスを送信しない", -1)
    if not device_address_ack:
        return Result(overall, dest, b"", tuple(names), True, "下流アドレスNACKをホストへ中継", -1)
    output, device_acks = bytearray(), []
    blocked = ended = False
    nack_offset = None
    for offset, value in enumerate(data):
        if direction == "write" and offset >= cfg["bus"]["max_write_bytes"]:
            overall, nack_offset = "block", offset
            break
        rule = None if ended else _match(cfg, "write" if direction == "write" else "read_response",
                                         address, data[:offset + 1], write_context)
        if rule:
            if rule["name"] not in names:
                names.append(rule["name"])
            if rule["action"] == "block":
                blocked = True
                overall = "block"
            elif overall != "block":
                # A future patch matches now, but has not yet changed a byte.
                if "destination" in rule or rule.get("ack", "host") != "host" or any(
                        p["offset"] == offset for p in rule["patches"]):
                    overall = "modify"
        if direction == "write":
            if blocked or (rule and rule.get("ack") == "nack" and offset >= rule.get("nack_at", 0)):
                nack_offset = offset
                break
            output.append(_byte(rule, offset, value))
            if device_write_acks is not None and not device_write_acks[offset]:
                nack_offset = offset
                break
        else:
            output.append(cfg["bus"]["read_block_fill"] if blocked or ended else _byte(rule, offset, value))
            host_ack = host_acks[offset] if host_acks is not None else offset < len(data) - 1
            if not ended:
                mode = rule.get("ack", "host") if rule else "host"
                if rule and offset < rule.get("nack_at", 0):
                    mode = "host"
                device_ack = mode == "ack" or (mode == "host" and host_ack)
                if offset + 1 == MAX_PAYLOAD:
                    device_ack = False
                device_acks.append(device_ack)
                ended = not device_ack
            if not host_ack:
                break
    explanation = ("バイトごとに判定し下流ACK/NACKを中継。遮断位置より前の送信は取消不可。"
                   if direction == "write" else
                   "バイトごとに判定してホストへ返却。下流NACK後の追加要求には代替値を返す。")
    return Result(overall, dest, bytes(output), tuple(names), True, explanation, nack_offset, tuple(device_acks))


def simulate_combined(raw_config, write_address, write_data, read_address, read_data, **read_options):
    """WRITE -> repeated START -> READ; preserve original received WRITE bytes."""
    written = simulate(raw_config, "write", write_address, write_data)
    context = None
    if written.nack_offset != -1:
        count = len(write_data) if written.nack_offset is None else min(
            written.nack_offset + 1, validate(raw_config)["bus"]["max_write_bytes"])
        context = WriteContext(number(write_address, 0x08, 0x77, "address"), write_data[:count])
    return written, simulate(raw_config, "read", read_address, read_data,
                             write_context=context, **read_options)
