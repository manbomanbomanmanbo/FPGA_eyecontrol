"""Explicit TCP transport for stereo_xyz_axi; no connection or hardware on import."""

import select
import socket
import threading
import time
import uuid
from dataclasses import asdict

from mock_link.protocol import (
    FrameReader, LinkError, LinkTimeout, ProtocolError as WireError,
    duration, integer, network_host, send_frame,
)
from .driver import (
    ABI_VERSION, IP_ID, CompletedResult, ComputationError, ConcurrentOwnerError,
    ProtocolError, RecoveryRequired, RequestTimeout, StereoDriverError,
)
from .oracle import MAX32, MIN32, MASK32, RawStereoInput, Status

PROTOCOL = "stereo_xyz_axi"
VERSION = 1
HARDWARE_EVIDENCE = "pynq_mmio_result"
INPUT_FIELDS = frozenset((
    "u", "v", "d", "fx", "fy", "cx", "cy", "baseline",
    "width", "height", "zmin", "zmax", "input_valid",
))
STATUSES = frozenset((
    "ok", "recovered", "computation_error", "server_busy",
    "recovery_required", "driver_timeout", "driver_error",
))
REQUEST_KEYS = frozenset((
    "protocol", "version", "session_id", "frame_id", "op",
    "request_tag", "timeout_s", "input",
))
RESPONSE_KEYS = frozenset((
    "protocol", "version", "session_id", "frame_id", "op", "request_tag",
    "status", "x", "y", "z", "kernel_status", "tag", "count",
    "evidence", "ip_id", "abi",
))


def _keys(value, expected, name):
    if type(value) is not dict or set(value) != set(expected):
        raise WireError("{} has unexpected/missing fields".format(name))


def _identity(message):
    if message["protocol"] != PROTOCOL:
        raise WireError("Wrong stereo protocol")
    integer(message["version"], VERSION, VERSION, "version")
    sid = message["session_id"]
    try:
        if type(sid) is not str or str(uuid.UUID(sid)) != sid:
            raise ValueError()
    except (ValueError, AttributeError):
        raise WireError("session_id must be a canonical UUID")
    integer(message["frame_id"], 0, MASK32, "frame_id")
    integer(message["request_tag"], 0, MASK32, "request_tag")
    if message["op"] not in ("compute", "recover"):
        raise WireError("Unknown stereo operation")


def validate_request(message):
    _keys(message, REQUEST_KEYS, "request")
    _identity(message)
    seconds = message["timeout_s"]
    if type(seconds) not in (int, float) or not 0 < seconds <= 60:
        raise WireError("timeout_s must be finite and within 0..60 seconds")
    if message["op"] == "recover":
        if message["input"] is not None:
            raise WireError("recover has no geometry input")
    else:
        _keys(message["input"], INPUT_FIELDS, "input")
        try:
            RawStereoInput(**message["input"])
        except (TypeError, ValueError) as exc:
            raise WireError("Invalid raw stereo input: " + str(exc)) from exc
    return message


def validate_response(message):
    _keys(message, RESPONSE_KEYS, "response")
    _identity(message)
    integer(message["ip_id"], IP_ID, IP_ID, "ip_id")
    integer(message["abi"], ABI_VERSION, ABI_VERSION, "abi")
    if message["evidence"] != HARDWARE_EVIDENCE:
        raise WireError("Stereo responses must identify a PYNQ MMIO result")
    status = message["status"]
    if type(status) is not str or status not in STATUSES:
        raise WireError("Unknown stereo response status")
    if status == "ok":
        if message["op"] != "compute":
            raise WireError("Only compute returns coordinates")
        for name in ("x", "y", "z"):
            integer(message[name], MIN32, MAX32, name)
        integer(message["kernel_status"], 0, 0, "kernel_status")
        integer(message["tag"], 0, MASK32, "tag")
        integer(message["count"], 0, MASK32, "count")
        if message["tag"] != message["request_tag"]:
            raise WireError("Result tag does not match request")
    else:
        if any(message[name] is not None for name in ("x", "y", "z")):
            raise WireError("Error/recovery responses must have null XYZ")
        if status == "computation_error":
            if message["op"] != "compute":
                raise WireError("Kernel rejection requires compute")
            integer(message["kernel_status"], 1, int(Status.OUTPUT_OVERFLOW), "kernel_status")
            integer(message["tag"], 0, MASK32, "tag")
            integer(message["count"], 0, MASK32, "count")
            if message["tag"] != message["request_tag"]:
                raise WireError("Rejected result tag does not match request")
        elif any(message[name] is not None for name in ("kernel_status", "tag", "count")):
            raise WireError("Uncompleted operation cannot expose a kernel result")
        if status == "recovered" and message["op"] != "recover":
            raise WireError("recovered requires recover")
    return message


def response_for(request, status, evidence, completed=None, error=None):
    message = {key: request[key] for key in (
        "protocol", "version", "session_id", "frame_id", "op", "request_tag",
    )}
    message.update(status=status, x=None, y=None, z=None, kernel_status=None,
                   tag=None, count=None, evidence=evidence, ip_id=IP_ID, abi=ABI_VERSION)
    if completed is not None:
        message.update(x=completed.x, y=completed.y, z=completed.z,
                       kernel_status=int(completed.status),
                       tag=completed.tag, count=completed.count)
    if error is not None:
        message.update(kernel_status=int(error.status), tag=error.tag, count=error.count)
    return validate_response(message)


class StereoTCPClient:
    """One request at a time, explicit connect/recover, and no software XYZ fallback.

    Only PYNQ MMIO evidence is accepted.
    A TCP response authenticates protocol identity, not the physical camera.
    """

    def __init__(self, host="192.168.2.99", port=8766, *, timeout_s=0.25):
        self.host = network_host(host)
        self.port = integer(port, 1, 65535, "port")
        self.timeout_s = duration(timeout_s, "timeout_s")
        if self.timeout_s > 60:
            raise ValueError("timeout_s cannot exceed 60 seconds")
        self.expected_evidence = HARDWARE_EVIDENCE
        self._evidence = "unverified_remote_backend"
        self._sock = None
        self._reader = FrameReader()
        self._session_id = None
        self._next_frame_id = 0
        self._next_tag = 1
        self._pending = None
        self._needs_recovery = False
        self._state_lock = threading.RLock()
        self._request_lock = threading.Lock()

    @property
    def evidence(self):
        with self._state_lock:
            return self._evidence

    @property
    def needs_recovery(self):
        with self._state_lock:
            return self._needs_recovery

    @property
    def connected(self):
        with self._state_lock:
            if self._sock is not None and self._pending is None:
                try:
                    if self._reader.buffer or select.select([self._sock], [], [], 0)[0]:
                        self._needs_recovery = True
                        self._close_locked()
                except (OSError, ValueError):
                    self._needs_recovery = True
                    self._close_locked()
            return self._sock is not None

    def _close_locked(self):
        sock, self._sock = self._sock, None
        self._reader = FrameReader()
        self._pending = None
        self._evidence = "unverified_remote_backend"
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def close(self):
        with self._state_lock:
            if self._pending is not None:
                self._needs_recovery = True
            self._close_locked()

    disconnect = close

    def _connect_locked(self):
        self._close_locked()
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout_s)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as exc:
            raise StereoDriverError("Stereo board service is unavailable") from exc
        self._sock = sock
        self._reader = FrameReader()
        self._session_id = str(uuid.uuid4())
        self._next_frame_id = 0
        return self._session_id

    def connect(self):
        if not self._request_lock.acquire(blocking=False):
            raise ConcurrentOwnerError("Cannot reconnect during a stereo request")
        try:
            with self._state_lock:
                return self._connect_locked()
        finally:
            self._request_lock.release()

    def _exchange(self, op, point, tag, seconds):
        """Caller owns _request_lock; state lock is released during network I/O."""
        deadline = time.monotonic() + seconds
        with self._state_lock:
            if self._sock is None:
                raise StereoDriverError("Explicit connect() is required")
            if self._next_frame_id > MASK32:
                self._needs_recovery = True
                self._close_locked()
                raise ProtocolError("Frame ID exhausted; reconnect and recover")
            request = dict(protocol=PROTOCOL, version=VERSION,
                           session_id=self._session_id, frame_id=self._next_frame_id,
                           op=op, request_tag=tag, timeout_s=seconds,
                           input=None if point is None else asdict(point))
            validate_request(request)
            self._next_frame_id += 1
            sock, reader = self._sock, self._reader
            pending = (request["session_id"], request["frame_id"], tag, op)
            self._pending = pending
        try:
            send_frame(sock, request, deadline)
            result = validate_response(reader.receive(sock, deadline))
            if tuple(result[key] for key in ("session_id", "frame_id", "request_tag", "op")) != pending:
                raise WireError("Response does not match this pending request")
            if result["evidence"] != self.expected_evidence:
                raise WireError("Backend evidence does not match the explicitly selected mode")
            if reader.buffer:
                raise WireError("Unsolicited/pipelined stereo response")
            if time.monotonic() >= deadline:
                raise LinkTimeout("Stereo response arrived after deadline")
            with self._state_lock:
                if self._sock is not sock or self._pending != pending:
                    raise WireError("Cancelled stereo request; result discarded")
                self._pending = None
                self._evidence = result["evidence"]
            return result
        except (LinkError, OSError, ValueError) as exc:
            with self._state_lock:
                self._needs_recovery = True
                self._close_locked()
            if isinstance(exc, (LinkTimeout, socket.timeout)):
                raise RequestTimeout("Stereo TCP deadline expired; recover before reuse") from exc
            raise ProtocolError("Stereo TCP request failed; recover before reuse") from exc

    def compute(self, point, *, request_tag=None, timeout_s=None):
        if not isinstance(point, RawStereoInput):
            raise TypeError("point must be RawStereoInput")
        seconds = self.timeout_s if timeout_s is None else duration(timeout_s, "timeout_s")
        if seconds > 60:
            raise ValueError("timeout_s cannot exceed 60 seconds")
        if not self._request_lock.acquire(blocking=False):
            raise ConcurrentOwnerError("Stereo request already in progress")
        try:
            with self._state_lock:
                if self._needs_recovery:
                    raise RecoveryRequired("Explicit recover() is required after the prior failure")
                tag = self._next_tag if request_tag is None else integer(request_tag, 0, MASK32, "request_tag")
            result = self._exchange("compute", point, tag, seconds)
            status = result["status"]
            if status in ("ok", "computation_error"):
                self._next_tag = (tag + 1) & MASK32
                if status == "computation_error":
                    raise ComputationError(result["kernel_status"], result["tag"], result["count"])
                return CompletedResult(result["x"], result["y"], result["z"], Status.OK,
                                       result["tag"], result["count"], result["evidence"])
            if status == "driver_timeout":
                with self._state_lock:
                    self._needs_recovery = True
                    self._close_locked()
                raise RequestTimeout("Stereo MMIO timed out; explicit recover() required")
            if status == "server_busy":
                raise ConcurrentOwnerError("Stereo hardware already has an active request")
            with self._state_lock:
                self._needs_recovery = True
            raise RecoveryRequired("Stereo service reported " + status)
        finally:
            self._request_lock.release()

    def recover(self):
        """Explicitly reconnect if needed, then ABORT/discard the remote old result."""
        if not self._request_lock.acquire(blocking=False):
            raise ConcurrentOwnerError("Cannot recover during a stereo request")
        try:
            with self._state_lock:
                self._needs_recovery = True
                if self._sock is None:
                    self._connect_locked()
                tag = self._next_tag
            result = self._exchange("recover", None, tag, self.timeout_s)
            if result["status"] != "recovered":
                raise RecoveryRequired("Remote recovery failed: " + result["status"])
            with self._state_lock:
                self._needs_recovery = False
        finally:
            self._request_lock.release()
