"""Synchronous explicit-connect client. Poll last_result before drawing a virtual point."""
import argparse
import json
import select
import socket
import threading
import time
import uuid
from .calibration import validate_calibration
from .protocol import (HOST, VERSION, CALIBRATED_VERSION, CALIBRATION, MAX_FRAME_ID, LinkError, ProtocolError,
    LinkTimeout, DisconnectedError, RequestInProgressError, LinkResult, FrameReader,
    JsonLogger, duration, integer, network_host, validate_request, validate_response, send_frame)


class HostClient:
    def __init__(self, *, host=HOST, port=8765, timeout_s=1.0, result_ttl_s=0.5, log_path=None,
                 calibration=None):
        self.host = network_host(host)
        self.port = integer(port, 1, 65535, "port")
        self.timeout_s = duration(timeout_s, "timeout_s")
        self.result_ttl_s = duration(result_ttl_s, "result_ttl_s")
        self._calibration = None if calibration is None else validate_calibration(calibration)
        self._protocol_version = VERSION if self._calibration is None else CALIBRATED_VERSION
        self._sock = None
        self._reader = FrameReader()
        self._session_id = None
        self._next_frame_id = 0
        self._pending = None
        self._last_result = None
        self._result_at = 0.0
        self._status = "offline"
        self.last_error = None
        self._state_lock = threading.RLock()
        self._request_lock = threading.Lock()
        self.logger = JsonLogger("client", log_path)

    @property
    def calibration(self):
        """Independent copy; create a new client to change calibration."""
        return None if self._calibration is None else validate_calibration(self._calibration)

    @property
    def session_id(self):
        with self._state_lock:
            return self._session_id

    @property
    def next_frame_id(self):
        with self._state_lock:
            return self._next_frame_id

    def _close_locked(self, reason):
        sock, self._sock = self._sock, None
        self._last_result = None
        self._pending = None
        self._reader = FrameReader()
        self._status = "offline"
        self.last_error = reason
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self.logger.write("disconnected", session_id=self._session_id, reason=reason)

    def disconnect(self):
        with self._state_lock:
            self._close_locked("explicit disconnect")

    close = disconnect

    def connect(self):
        if not self._request_lock.acquire(blocking=False):
            raise RequestInProgressError("Finish/cancel the in-flight request before reconnecting")
        try:
            with self._state_lock:
                self._close_locked("explicit reconnect; old requests discarded")
                try:
                    sock = socket.create_connection((self.host, self.port), timeout=self.timeout_s)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError as exc:
                    self.last_error = "connect failed"
                    raise DisconnectedError("board server is unavailable; explicit connect required") from exc
                self._sock = sock
                self._session_id = str(uuid.uuid4())
                self._next_frame_id = 0
                self._status = "connected"
                self.last_error = None
                self.logger.write("connected", session_id=self._session_id, host=self.host, port=self.port)
                return self._session_id
        finally:
            self._request_lock.release()

    def _refresh_locked(self):
        if self._sock is not None and self._pending is None:
            try:
                readable, _, _ = select.select([self._sock], [], [], 0)
                if self._reader.buffer or readable:
                    self._close_locked("unsolicited/duplicate response or peer closed while idle")
            except (OSError, ValueError):
                self._close_locked("idle connection failed")
        if self._last_result is not None and time.monotonic() - self._result_at >= self.result_ttl_s:
            old = self._last_result
            self._last_result = None
            self._status = "expired" if self._sock else "offline"
            self.logger.write("result_expired", session_id=old.session_id, frame_id=old.frame_id)

    @property
    def connected(self):
        with self._state_lock:
            self._refresh_locked()
            return self._sock is not None

    @property
    def status(self):
        with self._state_lock:
            self._refresh_locked()
            return self._status

    @property
    def last_result(self):
        """Returns None after error, disconnect or local monotonic TTL expiration.

        Consumers must query this property before each draw; a previously returned
        immutable LinkResult is only a historical record, not a live coordinate.
        """
        with self._state_lock:
            self._refresh_locked()
            return self._last_result

    def submit(self, gx_q16, gy_q16, *, valid=1, timestamp=None):
        if not self._request_lock.acquire(blocking=False):
            raise RequestInProgressError("One request in flight; concurrent requests are not queued")
        sock = None
        try:
            deadline = time.monotonic() + self.timeout_s
            with self._state_lock:
                self._refresh_locked()
                if self._sock is None:
                    raise DisconnectedError("explicit connect required")
                if self._next_frame_id > MAX_FRAME_ID:
                    self._close_locked("frame_id exhausted; connect for a new session")
                    raise ProtocolError("uint32 frame_id exhausted; no wrap/replay allowed")
                sid, fid = self._session_id, self._next_frame_id
                message = {"protocol_version": self._protocol_version, "session_id": sid,
                    "frame_id": fid, "timestamp": time.time() if timestamp is None else timestamp,
                    "features": {"gx_q16": gx_q16, "gy_q16": gy_q16, "valid": valid}}
                if self._calibration is None:
                    message["calibration_version"] = CALIBRATION
                else:
                    message["calibration"] = self.calibration
                validate_request(message)
                self._next_frame_id += 1
                sock, reader = self._sock, self._reader
                self._pending = (sid, fid)
                self._last_result = None
                self._status = "waiting"
            self.logger.write("request", session_id=sid, frame_id=fid, message=message)
            send_frame(sock, message, deadline)
            response = validate_response(reader.receive(sock, deadline))
            if (response["session_id"], response["frame_id"]) != (sid, fid):
                raise ProtocolError("old session, duplicate, out-of-order or nonpending response")
            if response["protocol_version"] != self._protocol_version:
                raise ProtocolError("response protocol_version does not match pending request")
            if self._calibration is not None:
                if any(response[key] != self._calibration[key] for key in ("calibration_id", "width", "height")):
                    raise ProtocolError("response calibration_id or screen dimensions do not match pending request")
            if time.monotonic() >= deadline:
                raise LinkTimeout("response arrived after total deadline")
            if reader.buffer:
                raise ProtocolError("unsolicited extra response after current response")
            result = LinkResult(sid, fid, response["screen_x"], response["screen_y"],
                                response["status"], float(message["timestamp"]))
            with self._state_lock:
                if self._sock is not sock or self._pending != (sid, fid):
                    raise DisconnectedError("request was cancelled; response discarded")
                self._pending = None
                self._status = result.status
                self._last_result = result if result.status == "ok" else None
                self._result_at = time.monotonic()
                self.last_error = None if result.status == "ok" else result.status
            self.logger.write("response", session_id=sid, frame_id=fid, message=response)
            return result
        except (LinkError, OSError) as exc:
            with self._state_lock:
                pending = self._pending
                self.logger.write("rejected", session_id=self._session_id,
                    frame_id=pending[1] if pending else None, reason=str(exc))
                self._close_locked(str(exc))
            if isinstance(exc, LinkError):
                raise
            raise DisconnectedError("board connection failed") from exc
        finally:
            self._request_lock.release()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Board TCP client; synthetic calibration; no physical mouse")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--frames", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--log", default=None)
    args = parser.parse_args()
    if not 1 <= args.frames <= 100000:
        parser.error("--frames must be 1..100000")
    with HostClient(host=args.host, port=args.port, timeout_s=args.timeout, log_path=args.log) as client:
        for i in range(args.frames):
            value = round(65536 * i / max(1, args.frames - 1))
            result = client.submit(value, value)
            print(json.dumps(result.__dict__, ensure_ascii=False))


if __name__ == "__main__":
    main()
