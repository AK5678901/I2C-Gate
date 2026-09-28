"""Translate the rule editor's unified conditions to the existing wire schema."""
from .config import ConfigError, obj


def from_match(match):
    conditions = []
    if match["address"] != "*":
        conditions.append({"target": "address", "value": f'0x{match["address"]:02X}'})
    conditions.extend({"target": "data", **predicate} for predicate in match["payload"])
    return conditions


def to_match(conditions):
    if not isinstance(conditions, list):
        raise ConfigError("一致条件: JSON配列を指定してください")
    match = {"address": "*", "payload": []}
    address_seen = False
    for index, condition in enumerate(conditions):
        path = f"一致条件[{index}]"
        obj(condition, ("target", "value"), ("offset", "mask"), path)
        if condition["target"] == "address":
            obj(condition, ("target", "value"), (), path)
            if address_seen:
                raise ConfigError("一致条件: I2Cアドレス条件は1つだけ指定してください")
            address_seen = True
            match["address"] = condition["value"]
        elif condition["target"] == "data":
            obj(condition, ("target", "offset", "value"), ("mask",), path)
            match["payload"].append({key: value for key, value in condition.items() if key != "target"})
        else:
            raise ConfigError(f"{path}: targetはaddress（I2Cアドレス）またはdata（データ）です")
    return match


def from_changes(rule):
    changes = []
    if "destination" in rule:
        changes.append({"target": "address", "value": f'0x{rule["destination"]:02X}'})
    changes.extend({"target": "data", **patch} for patch in rule.get("patches", []))
    return changes


def to_changes(changes):
    if not isinstance(changes, list):
        raise ConfigError("書き換え内容: JSON配列を指定してください")
    result = {"patches": []}
    for index, change in enumerate(changes):
        path = f"書き換え内容[{index}]"
        obj(change, ("target", "value"), ("offset", "mask", "operation"), path)
        if change["target"] == "address":
            obj(change, ("target", "value"), (), path)
            if "destination" in result:
                raise ConfigError("書き換え内容: I2Cアドレスは1つだけ指定してください")
            result["destination"] = change["value"]
        elif change["target"] == "data":
            obj(change, ("target", "offset", "value"), ("mask", "operation"), path)
            result["patches"].append({key: value for key, value in change.items() if key != "target"})
        else:
            raise ConfigError(f"{path}: targetはaddress（下流I2Cアドレス）またはdata（データ）です")
    return result
