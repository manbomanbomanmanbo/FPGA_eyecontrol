"""Exclusive ordered numeric/control transport with persistent PL snapshots."""
import socket
import threading
import uuid
from interaction_stage6.tcp import send, receive
from .protocol import ID, ABI, EVIDENCE, Control as C, word


def validate_snapshot(result):
    if (result.get("id") != ID or result.get("abi") != ABI or result.get("evidence") != EVIDENCE
            or type(result.get("count")) is not int or not 0 <= result["count"] <= 0xffffffff
            or type(result.get("state")) is not int or not 0 <= result["state"] <= 15 or type(result.get("status")) is not int
            or result["status"] not in range(21) or type(result.get("result_tag")) is not int
            or not 0 <= result["result_tag"] <= 0xffffffff
            or type(result.get("output")) is not list or len(result["output"]) != 16
            or any(type(value) is not int or not 0 <= value <= 0xffffffff for value in result["output"])):
        raise RuntimeError("Malformed/nonhardware PL result")
    telemetry = result.get("telemetry")
    if (type(telemetry) is not dict or type(telemetry.get("epoch")) is not int or not 0 <= telemetry["epoch"] <= 0xffffffff
            or type(telemetry.get("flags")) is not int or telemetry.get("mode") not in (0, 1)
            or type(telemetry.get("current_steps")) is not list or len(telemetry["current_steps"]) != 2
            or any(type(value) is not int or not -(1 << 31) <= value < (1 << 31) for value in telemetry["current_steps"])
            or type(telemetry.get("estimated_angles_q22")) is not list or len(telemetry["estimated_angles_q22"]) != 2
            or any(type(value) is not int or not -(1 << 31) <= value < (1 << 31) for value in telemetry["estimated_angles_q22"])
            or type(telemetry.get("monitor_bytes")) is not list or len(telemetry["monitor_bytes"]) > 64
            or any(type(value) is not int or not 0 <= value <= 255 for value in telemetry["monitor_bytes"])
            or (telemetry["mode"] == 1 and telemetry["flags"] & 4)):
        raise RuntimeError("Malformed PL telemetry or test mode has external output")
    for field in ("flags", "interface_profile", "watchdog_age_cycles", "tx_frames", "tx_bytes", "rx_bytes", "rx_framing_errors",
                  "rx_invalid_packets", "last_reply", "valid_replies", "last_stop_status", "monitor_count", "monitor_framing_errors"):
        word(telemetry.get(field))
    for field in ("last_frame_start_cycles", "last_frame_end_cycles", "previous_frame_end_cycles", "last_frame_gap_cycles"):
        if type(telemetry.get(field)) is not int or not 0 <= telemetry[field] < (1 << 64):
            raise RuntimeError("Malformed PL 64-bit cycle telemetry")
    stream_fields = ("tx_frames", "tx_bytes", "rx_bytes", "rx_framing_errors", "rx_invalid_packets",
                     "last_reply", "valid_replies", "monitor_count", "monitor_framing_errors")
    frame_fields = ("last_frame_start_cycles", "last_frame_end_cycles", "previous_frame_end_cycles", "last_frame_gap_cycles")
    begin, end = telemetry.get("telemetry_snapshot_begin"), telemetry.get("telemetry_snapshot_end")
    if (type(begin) is not dict or type(end) is not dict
            or set(begin) != set(stream_fields + frame_fields) or set(end) != set(begin)
            or type(telemetry.get("telemetry_snapshot_stable")) is not bool
            or type(telemetry.get("monitor_snapshot_stable")) is not bool):
        raise RuntimeError("Missing PL stream snapshot boundaries")
    for boundary in (begin, end):
        for field in stream_fields:
            word(boundary[field])
        for field in frame_fields:
            if type(boundary[field]) is not int or not 0 <= boundary[field] < (1 << 64):
                raise RuntimeError("Malformed PL frame snapshot boundary")
    word(telemetry.get("monitor_snapshot_end_count"))
    monitor_stable = (begin["monitor_count"] == end["monitor_count"]
                      and begin["monitor_framing_errors"] == end["monitor_framing_errors"])
    if (any(telemetry[field] != begin[field] for field in stream_fields + frame_fields)
            or telemetry["telemetry_snapshot_stable"] != (begin == end)
            or telemetry["monitor_snapshot_stable"] != monitor_stable
            or telemetry["monitor_snapshot_end_count"] != end["monitor_count"]):
        raise RuntimeError("Inconsistent PL stream snapshot boundaries")
    return result


class TCPClient:
    def __init__(self, host="192.168.2.99", port=8769, timeout_s=2):
        self.host, self.port, self.timeout_s = host, port, timeout_s
        self.session = uuid.uuid4().hex
        self.tag, self.count, self.epoch = 0, None, None
        self.sock, self.failed, self.owns_arm = None, False, False
        self.lock = threading.RLock()
        self.last_result = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None

    def _request(self, kind, **payload):
        if self.tag == 0xffffffff:
            raise RuntimeError("Session sequence exhausted")
        self.tag += 1
        send(self.sock, {"session": self.session, "tag": self.tag, "kind": kind, **payload})
        result = receive(self.sock)
        if (type(result) is not dict or result.get("session") != self.session or result.get("tag") != self.tag
                or result.get("error")):
            raise RuntimeError("Unmatched or failed stage8 response: " + repr(result))
        validate_snapshot(result)
        self.last_result, self.count, self.epoch = result, result["count"], result["telemetry"]["epoch"]
        if not result["telemetry"]["flags"] & 1:
            self.owns_arm = False
        return result

    def connect(self):
        with self.lock:
            if self.sock is not None:
                return self
            if self.failed:
                raise RuntimeError("Create a fresh session after link failure; no retransmit")
            self.sock = socket.create_connection((self.host, self.port), self.timeout_s)
            self.sock.settimeout(self.timeout_s)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                self._request("ping")  # Persistent count/epoch snapshot, never zero assumptions.
            except BaseException:
                self.failed = True
                self._drop_socket()
                raise
            return self

    def _invoke(self, kind, **payload):
        with self.lock:
            self.connect()
            try:
                return self._request(kind, **payload)
            except BaseException:
                self.failed = True
                self.owns_arm = False
                self._heartbeat_stop.set()
                self._drop_socket()  # Server owner disconnect requests DISARM; PL watchdog independent.
                raise

    def query(self):
        return self._invoke("query")

    def call(self, op, inputs):
        if type(op) is not int or op not in (1, 2) or type(inputs) is not list or len(inputs) != 16:
            raise ValueError("Expected POINT/ANGLE with 16 numeric words")
        values = [word(value) for value in inputs]
        with self.lock:
            before = self.query()
            result = self._invoke("call", op=op, inputs=values)
            if result["count"] != ((before["count"] + 1) & 0xffffffff) or result["result_tag"] != result["tag"]:
                self.failed = True
                self.owns_arm = False
                self._heartbeat_stop.set()
                self._drop_socket()
                raise RuntimeError("Unexpected persistent PL completion count/tag")
            return result

    def control(self, command, values=None):
        if type(command) is not int and not isinstance(command, C):
            raise ValueError("Management control must be an integer opcode")
        command = C(command)
        if command == C.START:
            raise ValueError("Use call for motion START")
        payload = {"control": int(command)}
        if values is not None:
            payload["values"] = [word(value) for value in values]
        with self.lock:
            # A renewal may have queued behind STOP/DISARM. Recheck ownership
            # under the same lock as the request, before consuming a tag or IO.
            if command == C.HEARTBEAT and not self.owns_arm:
                raise RuntimeError("This session did not explicitly ARM")
            result = self._invoke("control", **payload)
            if command == C.ARM:
                if result.get("owns_arm") is not True:
                    self.failed = True
                    self._drop_socket()
                    raise RuntimeError("Server did not establish this ARM owner")
                self.owns_arm = True
                self._start_heartbeat()
            elif command in (C.STOP, C.DISARM):
                self.owns_arm = False
            return result

    def ack(self):
        return self.control(C.ACK)

    def seed(self, pan_steps, tilt_steps):
        if any(type(value) is not int or not -9102 <= value <= 9102 for value in (pan_steps, tilt_steps)):
            raise ValueError("Explicit seed must be integer steps in [-9102,9102]")
        return self.control(C.SEED, [pan_steps & 0xffffffff, tilt_steps & 0xffffffff])

    def arm(self):
        return self.control(C.ARM)

    def disarm(self):
        return self.control(C.DISARM)

    def stop(self):
        return self.control(C.STOP)

    def configure(self, name, value):
        return self._invoke("configure", name=name, value=word(value))

    def inject_reply(self, value):
        return self._invoke("inject", value=word(value))

    def heartbeat(self):
        return self.control(C.HEARTBEAT)

    def _start_heartbeat(self):
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_stop.clear()
        def renew():
            while not self._heartbeat_stop.wait(.2):
                with self.lock:
                    if self._heartbeat_stop.is_set():
                        return
                    # Keep the worker idle after DISARM so an explicit later
                    # ARM can reuse it, without sending a stale renewal.
                    if not self.owns_arm:
                        continue
                    try:
                        self.heartbeat()
                    except Exception:
                        return
        self._heartbeat_thread = threading.Thread(target=renew, daemon=True, name="stage8-owner-heartbeat")
        self._heartbeat_thread.start()

    def _drop_socket(self):
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
            self.sock = None

    def close(self):
        self._heartbeat_stop.set()
        with self.lock:
            if self.sock is not None and self.owns_arm and not self.failed:
                try:
                    self._request("control", control=int(C.DISARM))
                except Exception:
                    self.failed = True
            self.owns_arm = False
            self._drop_socket()
        if self._heartbeat_thread and self._heartbeat_thread is not threading.current_thread():
            self._heartbeat_thread.join(timeout=1.2)

    def __enter__(self):
        return self.connect()

    def __exit__(self, *args):
        self.close()
