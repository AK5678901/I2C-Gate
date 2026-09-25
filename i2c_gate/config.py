"""Strict, versioned configuration shared by the editor and simulator."""
import copy
import json

MAX_RULES = 64
MAX_PAYLOAD = 4096
MAX_JSON_BYTES = 65536
PHASES = ("write", "read_request", "read_response")
ACTIONS = ("modify", "block")


class ConfigError(ValueError):
    pass


def number(value, minimum, maximum, path):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ConfigError(f"{path}: 整数を指定してください")
    try:
        parsed = int(value, 16) if isinstance(value, str) and value.lower().startswith("0x") else int(value)
    except ValueError:
        raise ConfigError(f"{path}: 整数または0x付き16進数を指定してください") from None
    if not minimum <= parsed <= maximum:
        raise ConfigError(f"{path}: {minimum}〜{maximum}の範囲で指定してください")
    return parsed


def obj(value, required, optional, path):
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: オブジェクトが必要です")
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing or extra:
        raise ConfigError(f"{path}: 不足={sorted(missing)}, 未知の項目={sorted(extra)}")


def validate(raw):
    cfg = copy.deepcopy(raw)
    obj(cfg, ("version", "bus", "rules"), (), "config")
    cfg["version"] = number(cfg["version"], 1, 1, "version")
    bus = cfg["bus"]
    obj(bus, ("speed_hz", "stretch_timeout_us", "max_write_bytes", "read_block_fill"), ("addresses",), "bus")
    bus["speed_hz"] = number(bus["speed_hz"], 10000, 400000, "bus.speed_hz")
    bus["stretch_timeout_us"] = number(bus["stretch_timeout_us"], 1, 1000000, "bus.stretch_timeout_us")
    bus["max_write_bytes"] = number(bus["max_write_bytes"], 1, MAX_PAYLOAD, "bus.max_write_bytes")
    bus["read_block_fill"] = number(bus["read_block_fill"], 0, 255, "bus.read_block_fill")
    # Accept old files, but an address allowlist no longer limits forwarding.
    if "addresses" in bus:
        addresses = bus.pop("addresses")
        if not isinstance(addresses, list) or len(addresses) > 112:
            raise ConfigError("bus.addresses: 最大112個のアドレスを指定してください")
        addresses = [number(a, 0x08, 0x77, "bus.addresses") for a in addresses]
        if len(set(addresses)) != len(addresses):
            raise ConfigError("bus.addresses: アドレスが重複しています")
    if not isinstance(cfg["rules"], list) or len(cfg["rules"]) > MAX_RULES:
        raise ConfigError(f"rules: 最大{MAX_RULES}件です")
    names = set()
    for i, rule in enumerate(cfg["rules"]):
        p = f"rules[{i}]"
        obj(rule, ("name", "enabled", "phase", "match", "action"), ("destination", "patches"), p)
        if not isinstance(rule["name"], str) or not 1 <= len(rule["name"].strip()) <= 80:
            raise ConfigError(f"{p}.name: 1〜80文字の名前が必要です")
        if rule["name"] in names:
            raise ConfigError(f"{p}.name: 名前が重複しています")
        names.add(rule["name"])
        if not isinstance(rule["enabled"], bool):
            raise ConfigError(f"{p}.enabled: true/falseが必要です")
        if rule["phase"] not in PHASES or rule["action"] not in ACTIONS:
            raise ConfigError(f"{p}: phaseまたはactionが不正です")
        match = rule["match"]
        obj(match, ("address", "payload"), (), f"{p}.match")
        if match["address"] != "*":
            match["address"] = number(match["address"], 0x08, 0x77, f"{p}.match.address")
        if not isinstance(match["payload"], list) or len(match["payload"]) > 64:
            raise ConfigError(f"{p}.match.payload: 最大64条件です")
        if rule["phase"] == "read_request" and match["payload"]:
            raise ConfigError(f"{p}: READ開始前には応答ペイロードで判定できません")
        for j, predicate in enumerate(match["payload"]):
            q = f"{p}.match.payload[{j}]"
            obj(predicate, ("offset", "value"), ("mask",), q)
            predicate["offset"] = number(predicate["offset"], 0, MAX_PAYLOAD - 1, q + ".offset")
            predicate["value"] = number(predicate["value"], 0, 255, q + ".value")
            predicate["mask"] = number(predicate.get("mask", 255), 0, 255, q + ".mask")
        if "destination" in rule:
            rule["destination"] = number(rule["destination"], 0x08, 0x77, p + ".destination")
            if rule["phase"] == "read_response" or rule["action"] == "block":
                raise ConfigError(f"{p}: READ応答後／遮断時には宛先変更を指定できません")
        patches = rule.setdefault("patches", [])
        if not isinstance(patches, list) or len(patches) > 64:
            raise ConfigError(f"{p}.patches: 最大64件です")
        if rule["action"] == "modify":
            if not patches and "destination" not in rule:
                raise ConfigError(f"{p}: modifyにはアドレスまたはデータの書き換えが必要です")
            if patches and rule["phase"] == "read_request":
                raise ConfigError(f"{p}: READ開始前にはデータを書き換えられません")
        elif patches:
            raise ConfigError(f"{p}: patchesはmodifyでのみ指定できます")
        offsets = set()
        for j, patch in enumerate(patches):
            q = f"{p}.patches[{j}]"
            obj(patch, ("offset", "value"), ("mask",), q)
            patch["offset"] = number(patch["offset"], 0, MAX_PAYLOAD - 1, q + ".offset")
            patch["value"] = number(patch["value"], 0, 255, q + ".value")
            patch["mask"] = number(patch.get("mask", 255), 0, 255, q + ".mask")
            if patch["offset"] in offsets:
                raise ConfigError(f"{q}: 同一位置への重複した書き換えです")
            offsets.add(patch["offset"])
        if rule["phase"] == "read_response" and patches and match["payload"]:
            if max(p["offset"] for p in match["payload"]) > min(p["offset"] for p in patches):
                raise ConfigError(f"{p}: READでは後続バイトを条件に先行バイトを書き換えられません")
    return cfg


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f"JSONキーが重複しています: {key}")
        result[key] = value
    return result


def loads(source):
    if len(source.encode("utf-8")) > MAX_JSON_BYTES:
        raise ConfigError("設定ファイルは64 KiB以下にしてください")
    try:
        return validate(json.loads(source, object_pairs_hook=_unique_pairs))
    except (json.JSONDecodeError, RecursionError) as error:
        raise ConfigError(f"JSONの解析に失敗しました: {error}") from error


def dumps(cfg):
    result = json.dumps(validate(cfg), ensure_ascii=False, indent=2) + "\n"
    if len(result.encode("utf-8")) > MAX_JSON_BYTES:
        raise ConfigError("設定ファイルは64 KiB以下にしてください")
    return result


def default_config():
    return {"version": 1, "bus": {"speed_hz": 100000, "stretch_timeout_us": 25000,
            "max_write_bytes": 256, "read_block_fill": 255}, "rules": []}
