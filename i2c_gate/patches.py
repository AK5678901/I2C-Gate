"""AND/OR edits and lossless conversion to the firmware's bit masks."""


def editor_operations(patches):
    result = []
    for patch in patches:
        if "operation" in patch:
            result.append(dict(patch))
        else:
            # Preserve legacy replacement semantics as AND, then OR.
            mask = patch.get("mask", 255)
            result.append({"offset": patch["offset"], "operation": "AND", "value": 255 ^ mask})
            result.append({"offset": patch["offset"], "operation": "OR", "value": patch["value"] & mask})
    return result


def lower_patches(patches):
    """Compose edits per byte; output uses the existing firmware wire format."""
    values = {}
    for patch in patches:
        offset = patch["offset"]
        keep, bits = values.get(offset, (255, 0))
        if patch.get("operation") == "AND":
            keep &= patch["value"]
            bits &= patch["value"]
        elif patch.get("operation") == "OR":
            keep &= 255 ^ patch["value"]
            bits |= patch["value"]
        else:
            mask = patch.get("mask", 255)
            keep &= 255 ^ mask
            bits = (bits & (255 ^ mask)) | (patch["value"] & mask)
        values[offset] = keep, bits
    return [{"offset": offset, "value": bits, "mask": 255 ^ keep}
            for offset, (keep, bits) in values.items()]
