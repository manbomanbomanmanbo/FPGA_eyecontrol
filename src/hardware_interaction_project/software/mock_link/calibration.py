"""Strict versioned Q16.16 calibration profiles shared by PC and board.

The fixed basis is [gx, gy, gx*gy, 1]. The feature-contract hash binds the
PC-side preprocessing definition; the board interprets only these integers.
"""
import hashlib
import json
import math
import re

Q16 = 65536
FEATURE_MIN_Q16 = -16384
FEATURE_MAX_Q16 = 81920
COEFFICIENT_LIMIT_Q16 = 1966080000
MIN_DIMENSION = 2
MAX_DIMENSION = 8192
PROFILE_VERSION = 1
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_BODY_KEYS = frozenset({"profile_version", "feature_contract_id", "width", "height", "a_q16", "b_q16"})


class CalibrationError(ValueError):
    pass


def canonical_json_bytes(value):
    """Canonical UTF-8 used by both profile and feature-contract hashes."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError) as exc:
        raise CalibrationError("value cannot be encoded as canonical JSON") from exc


def sha256_id(value):
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def validate_sha256_id(value, name="hash"):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise CalibrationError("{} must be sha256: followed by 64 lowercase hex digits".format(name))
    return value


def q16_from_float(value):
    """Round half away from zero; reject overflow, never saturate.

    This encodes a signed int32. Feature and coefficient limits are narrower
    and are checked separately by the corresponding profile/request validator.
    """
    if type(value) not in (int, float) or not -32768 <= value <= 32768 or not math.isfinite(value):
        raise CalibrationError("Q16 value must be a finite number representable as signed int32")
    magnitude = int(math.floor(abs(value) * Q16 + 0.5))
    result = -magnitude if value < 0 else magnitude
    if not -(1 << 31) <= result <= (1 << 31) - 1:
        raise CalibrationError("rounded Q16 value overflows signed int32")
    return result


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise CalibrationError("{} must be integer {}..{}".format(name, low, high))
    return value


def _validated_body(body):
    if type(body) is not dict or set(body) != _BODY_KEYS:
        raise CalibrationError("calibration body requires exactly {}".format(sorted(_BODY_KEYS)))
    result = {
        "profile_version": _integer(body["profile_version"], PROFILE_VERSION, PROFILE_VERSION, "profile_version"),
        "feature_contract_id": validate_sha256_id(body["feature_contract_id"], "feature_contract_id"),
        "width": _integer(body["width"], MIN_DIMENSION, MAX_DIMENSION, "width"),
        "height": _integer(body["height"], MIN_DIMENSION, MAX_DIMENSION, "height"),
    }
    for key in ("a_q16", "b_q16"):
        values = body[key]
        if type(values) is not list or len(values) != 4:
            raise CalibrationError("{} must be a four-integer list for [gx, gy, gx*gy, 1]".format(key))
        result[key] = [_integer(v, -COEFFICIENT_LIMIT_Q16, COEFFICIENT_LIMIT_Q16, key) for v in values]
    return result


def build_profile(feature_contract_id, width, height, a_q16, b_q16):
    """Return a validated fresh wire dictionary including its canonical hash."""
    body = _validated_body({"profile_version": PROFILE_VERSION,
        "feature_contract_id": feature_contract_id, "width": width, "height": height,
        "a_q16": list(a_q16) if type(a_q16) in (list, tuple) else a_q16,
        "b_q16": list(b_q16) if type(b_q16) in (list, tuple) else b_q16})
    result = dict(body)
    result["calibration_id"] = sha256_id(body)
    return result


def validate_calibration(profile):
    """Validate the full profile/hash and return an independent deep copy."""
    if type(profile) is not dict or set(profile) != _BODY_KEYS | {"calibration_id"}:
        raise CalibrationError("calibration requires exactly {}".format(sorted(_BODY_KEYS | {"calibration_id"})))
    claimed = validate_sha256_id(profile["calibration_id"], "calibration_id")
    body = _validated_body({key: profile[key] for key in _BODY_KEYS})
    if sha256_id(body) != claimed:
        raise CalibrationError("calibration_id does not match the canonical profile hash")
    body["calibration_id"] = claimed
    return body
