"""Shared TCP service; default CLI remains a loopback Mock, with driver injection."""
import argparse
import select
import socket
import socketserver
import threading
import time
from pynq_gaze_driver import GazeMapDriver, MockMMIOBackend, ComputationError, GazeTimeoutError
from .protocol import (HOST, WIDTH, HEIGHT, CALIBRATED_VERSION, LinkError, ProtocolError, FrameReader,
    JsonLogger, duration, integer, network_host, validate_request, response_for, send_frame)


class _TCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True

    def __init__(self, *args, **kwargs):
        self._workers = set()
        self._workers_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        # Explicit ownership is needed on Python 3.6: its ThreadingMixIn
        # server_close() does not join request threads.
        worker = threading.Thread(target=self._run_request,
            args=(request, client_address), name="gaze-link-request", daemon=False)
        with self._workers_lock:
            self._workers.add(worker)
        try:
            worker.start()
        except BaseException:
            with self._workers_lock:
                self._workers.discard(worker)
            self.shutdown_request(request)
            raise

    def _run_request(self, request, client_address):
        try:
            self.process_request_thread(request, client_address)
        finally:
            with self._workers_lock:
                self._workers.discard(threading.current_thread())

    def server_close(self):
        socketserver.TCPServer.server_close(self)
        while True:
            with self._workers_lock:
                workers = list(self._workers)
            if not workers:
                break
            for worker in workers:
                worker.join()


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        owner = self.server.owner
        with owner._clients_lock:
            owner._clients.add(self.request)
        reader = FrameReader()
        session = None
        binding = None
        previous = -1
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            while not owner._stopping.is_set():
                request = validate_request(reader.receive(self.request, time.monotonic() + owner.io_timeout_s))
                sid, fid = request["session_id"], request["frame_id"]
                current_binding = (request["protocol_version"],
                    request["calibration"]["calibration_id"] if request["protocol_version"] == CALIBRATED_VERSION else None)
                if session is None:
                    session = sid
                    binding = current_binding
                if sid != session or fid <= previous:
                    raise ProtocolError("session changed or frame_id is duplicate/out-of-order")
                if current_binding != binding:
                    raise ProtocolError("protocol version or calibration changed within a connection")
                if reader.buffer:
                    raise ProtocolError("pipelined requests are not allowed")
                previous = fid
                owner.logger.write("request", session_id=sid, frame_id=fid, message=request)
                response = owner._compute(request)
                if owner._stopping.is_set():
                    break
                # Before the response, extra data means a forbidden pipeline; EOF
                # means the old client is gone. Neither creates another request.
                if select.select([self.request], [], [], 0)[0]:
                    raise ProtocolError("pipelined request or client closed during computation")
                send_frame(self.request, response, time.monotonic() + owner.io_timeout_s)
                owner.logger.write("response", session_id=sid, frame_id=fid, message=response)
        except LinkError as exc:
            owner.logger.write("connection_closed", session_id=session,
                               frame_id=previous if previous >= 0 else None, reason=str(exc))
        finally:
            with owner._clients_lock:
                owner._clients.discard(self.request)


class MockBoardServer:
    def __init__(self, *, host=HOST, port=8765, driver=None, driver_timeout_s=0.25,
                  io_timeout_s=5.0, log_path=None):
        self.host = network_host(host)
        integer(port, 0, 65535, "port")
        self.driver_timeout_s = duration(driver_timeout_s, "driver_timeout_s")
        self.io_timeout_s = duration(io_timeout_s, "io_timeout_s")
        self.driver = driver if driver is not None else GazeMapDriver(MockMMIOBackend())
        self._driver_lock = threading.Lock()
        self._driver_failed = False
        self._clients_lock = threading.Lock()
        self._clients = set()
        self._stopping = threading.Event()
        self.logger = JsonLogger("server", log_path)
        self._server = _TCPServer((self.host, port), _Handler)
        self._server.owner = self
        self.port = self._server.server_address[1]
        self._thread = None
        self._closed = False

    def _compute(self, request):
        f = request["features"]
        if f["valid"] == 0:
            return response_for(request, "invalid_input")
        # Nonblocking across ALL connections: no backlog survives a reconnect.
        if not self._driver_lock.acquire(blocking=False):
            return response_for(request, "server_busy")
        try:
            if self._driver_failed:
                return response_for(request, "driver_error")
            try:
                if request["protocol_version"] == CALIBRATED_VERSION:
                    profile = request["calibration"]
                    a, b = profile["a_q16"], profile["b_q16"]
                    width, height = profile["width"], profile["height"]
                    calibration_id = profile["calibration_id"]
                else:
                    a, b = [WIDTH << 16, 0, 0, 0], [0, HEIGHT << 16, 0, 0]
                    width, height, calibration_id = WIDTH, HEIGHT, None
                result = self.driver.compute(gx_q16=f["gx_q16"], gy_q16=f["gy_q16"],
                    a=a, b=b, width=width, height=height, valid=f["valid"], tag=request["frame_id"],
                    timeout_s=self.driver_timeout_s)
                if result.tag != request["frame_id"] or result.status != 0:
                    raise ProtocolError("driver result does not match request")
                self.logger.write("driver_result", session_id=request["session_id"],
                    frame_id=request["frame_id"], tag=result.tag,
                    complete_count=result.complete_count, x=result.x, y=result.y, status=result.status,
                    calibration_id=calibration_id, width=width, height=height)
                return response_for(request, "ok", result.x, result.y)
            except Exception as exc:
                if isinstance(exc, ComputationError):
                    status = "computation_error"
                elif isinstance(exc, GazeTimeoutError):
                    status = "driver_timeout"
                else:
                    status = "driver_error"
                self.logger.write("driver_failure", session_id=request["session_id"],
                    frame_id=request["frame_id"], error_type=type(exc).__name__, reason=str(exc))
                try:
                    self.driver.reset()  # Explicit ABORT, under the same per-driver lock.
                except Exception as reset_error:
                    self._driver_failed = True
                    self.logger.write("recovery_failed", session_id=request["session_id"],
                        frame_id=request["frame_id"], error_type=type(reset_error).__name__)
                return response_for(request, status)
        finally:
            self._driver_lock.release()

    def start(self):
        if self._closed:
            raise RuntimeError("Create a new server instance after stop")
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._server.serve_forever,
            kwargs={"poll_interval": 0.02}, name="gaze-link-server", daemon=False)
        try:
            self._thread.start()
        except BaseException:
            # shutdown() waits for serve_forever(); it must not be called when
            # the listener thread never started.
            self._thread = None
            self._server.server_close()
            self._closed = True
            raise
        try:
            self.logger.write("started", host=self.host, port=self.port,
                supported_protocol_versions=[1, 2], legacy_calibration="synthetic_linear_v1")
        except BaseException:
            self.stop()
            raise
        return self

    def stop(self):
        if self._closed:
            return
        self._stopping.set()
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join()
        with self._clients_lock:
            clients = list(self._clients)
        for sock in clients:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        # Do not report stopped or release a hardware lease while a request still
        # owns the driver. A blocked native MMIO call can also block this join.
        self._server.server_close()
        self._closed = True
        self.logger.write("stopped", host=self.host, port=self.port)

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.stop()


def main():
    parser = argparse.ArgumentParser(description="Mock driver only; loopback by default; synthetic calibration; no PYNQ")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log", default=None)
    parser.add_argument("--serve-seconds", type=float, default=None, help="optional bounded local smoke run")
    args = parser.parse_args()
    if args.serve_seconds is not None:
        duration(args.serve_seconds, "serve_seconds")
    with MockBoardServer(host=args.host, port=args.port, log_path=args.log) as server:
        print(f"Mock server on {server.host}:{server.port}; synthetic_linear_v1; NOT FPGA performance", flush=True)
        try:
            until = None if args.serve_seconds is None else time.monotonic() + args.serve_seconds
            while until is None or time.monotonic() < until:
                time.sleep(0.02)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
