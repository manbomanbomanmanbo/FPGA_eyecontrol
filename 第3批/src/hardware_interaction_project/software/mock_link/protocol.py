"""Strict v1/v2 newline-delimited UTF-8 JSON, maximum 4096 bytes including LF."""
from dataclasses import dataclass
from typing import Optional
import json
import math
from pathlib import Path
import socket
import threading
import time
import uuid
from .calibration import (CalibrationError, FEATURE_MIN_Q16, FEATURE_MAX_Q16,
    MIN_DIMENSION, MAX_DIMENSION, validate_calibration, validate_sha256_id)

HOST = "127.0.0.1"
VERSION = 1
CALIBRATED_VERSION = 2
MAX_FRAME_BYTES = 4096
MAX_FRAME_ID = 0xFFFFFFFF
CALIBRATION = "synthetic_linear_v1"
WIDTH, HEIGHT = 1920, 1080
STATUSES = frozenset({"ok", "invalid_input", "computation_error", "driver_timeout", "driver_error", "server_busy"})

class LinkError(RuntimeError):
    pass

class ProtocolError(LinkError):
    pass

class LinkTimeout(LinkError, TimeoutError):
    pass

class DisconnectedError(LinkError):
    pass

class RequestInProgressError(LinkError):
    pass

@dataclass(frozen=True)
class LinkResult:
    session_id: str
    frame_id: int
    screen_x: Optional[int]
    screen_y: Optional[int]
    status: str
    timestamp: float


def duration(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return float(value)


def network_host(value):
    if not isinstance(value, str) or not value or any(c.isspace() for c in value) or "\x00" in value:
        raise ValueError("host must be a nonempty hostname or IP address without whitespace")
    return value


_monotonic_ns = getattr(time, "monotonic_ns", None)


def monotonic_ns_compat():
    # Python 3.6 has monotonic(), but not monotonic_ns(). This is a logging
    # representation, not a promise of nanosecond precision or FPGA latency.
    return _monotonic_ns() if _monotonic_ns is not None else int(time.monotonic() * 1000000000)


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ProtocolError(f"{name} must be integer {low}..{high}")
    return value


def _keys(value, expected, name):
    if type(value) is not dict or set(value) != set(expected):
        raise ProtocolError(f"{name} requires exactly {sorted(expected)}")


def _identity(message):
    integer(message["protocol_version"], VERSION, CALIBRATED_VERSION, "protocol_version")
    sid = message["session_id"]
    try:
        if type(sid) is not str or str(uuid.UUID(sid)) != sid:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ProtocolError("session_id must be a canonical UUID") from None
    integer(message["frame_id"], 0, MAX_FRAME_ID, "frame_id")


def validate_request(message):
    if type(message) is not dict:
        raise ProtocolError("request must be an object")
    version = integer(message.get("protocol_version"), VERSION, CALIBRATED_VERSION, "protocol_version")
    profile_key = "calibration_version" if version == VERSION else "calibration"
    _keys(message, {"protocol_version", "session_id", "frame_id", "timestamp", "features", profile_key}, "request")
    _identity(message)
    ts = message["timestamp"]
    # Bound Unix seconds to year 9999; avoids float overflow on huge JSON integers.
    if type(ts) not in (float, int) or not 0 <= ts <= 253402300799 or not math.isfinite(ts):
        raise ProtocolError("timestamp must be finite Unix seconds in 0..253402300799")
    if version == VERSION:
        if message["calibration_version"] != CALIBRATION:
            raise ProtocolError("unsupported calibration_version")
    else:
        try:
            validate_calibration(message["calibration"])
        except CalibrationError as exc:
            raise ProtocolError(str(exc)) from exc
    f = message["features"]
    _keys(f, {"gx_q16", "gy_q16", "valid"}, "features")
    integer(f["gx_q16"], -(1 << 31), (1 << 31) - 1, "gx_q16")
    integer(f["gy_q16"], -(1 << 31), (1 << 31) - 1, "gy_q16")
    integer(f["valid"], 0, 1, "valid")
    if version == CALIBRATED_VERSION and f["valid"] == 1:
        integer(f["gx_q16"], FEATURE_MIN_Q16, FEATURE_MAX_Q16, "gx_q16")
        integer(f["gy_q16"], FEATURE_MIN_Q16, FEATURE_MAX_Q16, "gy_q16")
    return message


def validate_response(message):
    if type(message) is not dict:
        raise ProtocolError("response must be an object")
    version = integer(message.get("protocol_version"), VERSION, CALIBRATED_VERSION, "protocol_version")
    expected = {"protocol_version", "session_id", "frame_id", "screen_x", "screen_y", "status"}
    if version == CALIBRATED_VERSION:
        expected |= {"calibration_id", "width", "height"}
    _keys(message, expected, "response")
    _identity(message)
    width, height = WIDTH, HEIGHT
    if version == CALIBRATED_VERSION:
        try:
            validate_sha256_id(message["calibration_id"], "calibration_id")
        except CalibrationError as exc:
            raise ProtocolError(str(exc)) from exc
        width = integer(message["width"], MIN_DIMENSION, MAX_DIMENSION, "width")
        height = integer(message["height"], MIN_DIMENSION, MAX_DIMENSION, "height")
    if type(message["status"]) is not str or message["status"] not in STATUSES:
        raise ProtocolError("unknown response status")
    if message["status"] == "ok":
        integer(message["screen_x"], 0, width - 1, "screen_x")
        integer(message["screen_y"], 0, height - 1, "screen_y")
    elif message["screen_x"] is not None or message["screen_y"] is not None:
        raise ProtocolError("error response must have null coordinates")
    return message


def response_for(request, status, x=None, y=None):
    response = {"protocol_version": request["protocol_version"], "session_id": request["session_id"],
        "frame_id": request["frame_id"], "screen_x": x, "screen_y": y, "status": status}
    if request["protocol_version"] == CALIBRATED_VERSION:
        profile = request["calibration"]
        response.update({key: profile[key] for key in ("calibration_id", "width", "height")})
    return validate_response(response)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ProtocolError(f"nonfinite JSON number: {value}")


def decode_frame(data):
    if not data.endswith(b"\n") or len(data) > MAX_FRAME_BYTES:
        raise ProtocolError("frame must end with LF and fit 4096 bytes including LF")
    try:
        return json.loads(data[:-1].decode("utf-8", errors="strict"),
                          object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc


def encode_frame(message):
    try:
        data = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
    except (ValueError, TypeError, RecursionError) as exc:
        raise ProtocolError(f"unencodable JSON: {exc}") from exc
    if len(data) > MAX_FRAME_BYTES:
        raise ProtocolError("frame exceeds 4096 bytes including LF")
    return data


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise LinkTimeout("total monotonic deadline expired")
    return seconds


def send_frame(sock, message, deadline):
    sock.settimeout(remaining(deadline))
    try:
        sock.sendall(encode_frame(message))
    except socket.timeout as exc:
        raise LinkTimeout("send deadline expired") from exc
    except OSError as exc:
        raise DisconnectedError("connection failed while sending") from exc
    remaining(deadline)


class FrameReader:
    def __init__(self):
        self.buffer = bytearray()

    def receive(self, sock, deadline):
        while True:
            remaining(deadline)
            pos = self.buffer.find(b"\n")
            if pos >= 0:
                frame = bytes(self.buffer[:pos + 1])
                del self.buffer[:pos + 1]
                return decode_frame(frame)
            if len(self.buffer) >= MAX_FRAME_BYTES:
                raise ProtocolError("frame exceeds 4096 bytes without LF")
            sock.settimeout(remaining(deadline))
            try:
                block = sock.recv(MAX_FRAME_BYTES - len(self.buffer))
            except socket.timeout as exc:
                raise LinkTimeout("receive deadline expired") from exc
            except OSError as exc:
                raise DisconnectedError("connection failed while receiving") from exc
            if not block:
                raise DisconnectedError("peer closed connection; incomplete frame discarded")
            self.buffer.extend(block)


class JsonLogger:
    def __init__(self, role, path=None):
        self.role = role
        self.path = Path(path) if path is not None else None
        self.lock = threading.Lock()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event, *, session_id=None, frame_id=None, **details):
        if self.path is None:
            return
        record = {"timestamp": time.time(), "monotonic_ns": monotonic_ns_compat(), "role": self.role,
                  "event": event, "session_id": session_id, "frame_id": frame_id, **details}
        line = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        with self.lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line)
