"""Explicit combined PYNQ entry: original gaze TCP plus independent stereo XYZ TCP.

Run this file directly on the board so Python 3.6's local dataclasses vendor is
available before the stereo package is imported. Importing never loads an Overlay.
"""

import argparse
import os
from pathlib import Path
import select
import signal
import socket
import socketserver
import sys
import threading
import time

# Direct-file entry works before package __init__ needs Python 3.6 dataclasses.
_stage_software = Path(__file__).resolve().parents[1]
_original_software = Path(__file__).resolve().parents[3] / "software"
for _path in (_original_software / "vendor", _original_software, _stage_software):
    if _path.is_dir() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from mock_link.board_server import HardwareLease, verify_artifacts
from mock_link.mock_board_server import MockBoardServer, _TCPServer
from mock_link.protocol import FrameReader, LinkError, duration, integer, network_host, send_frame
from pynq_gaze_driver import GazeMapDriver, Register as GazeRegister
from pynq_gaze_driver.pynq_backend import PynqMMIOBackend as GazeBackend
from stereo_fpga_stage4.backends import PynqMMIOBackend as StereoBackend
from stereo_fpga_stage4.driver import (
    ComputationError, RequestTimeout, StereoDriverError, StereoMMIODriver,
    Register as StereoRegister,
)
from stereo_fpga_stage4.oracle import RawStereoInput
from stereo_fpga_stage4.tcp import (
    HARDWARE_EVIDENCE, response_for, validate_request,
)

BIT_SHA256 = "74a1cfd2c20123f9fd395098ff12f29013c705429847cf38b68f2b3b8bdf7cf3"
HWH_SHA256 = "465095505427b8b532d60419c01ca5679140d08f17dbcaeba8c783d5e27943d4"
LOCK_PATH = "/tmp/gaze_map_axi_owner.lock"


class _StereoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        owner = self.server.owner
        reader = FrameReader()
        session, previous = None, -1
        with owner._clients_lock:
            owner._clients.add(self.request)
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            while not owner._stopping.is_set():
                request = validate_request(reader.receive(
                    self.request, time.monotonic() + owner.io_timeout_s))
                sid, fid = request["session_id"], request["frame_id"]
                if session is None:
                    session = sid
                if sid != session or fid <= previous:
                    from mock_link.protocol import ProtocolError
                    raise ProtocolError("Session changed or stereo frame replayed/out of order")
                if reader.buffer:
                    from mock_link.protocol import ProtocolError
                    raise ProtocolError("Pipelined stereo requests are forbidden")
                previous = fid
                result = owner._compute(request)
                if owner._stopping.is_set():
                    break
                # A disconnected or pipelining client cannot receive an old result.
                if select.select([self.request], [], [], 0)[0]:
                    break
                send_frame(self.request, result, time.monotonic() + owner.io_timeout_s)
        except (LinkError, OSError, ValueError):
            pass
        finally:
            with owner._clients_lock:
                owner._clients.discard(self.request)


class StereoTCPServer:
    """Service for an explicitly supplied PYNQ MMIO driver."""

    def __init__(self, *, driver, host="127.0.0.1", port=8766,
                 driver_timeout_s=0.25, io_timeout_s=5.0):
        if not isinstance(driver, StereoMMIODriver):
            raise TypeError("driver must be an explicitly supplied StereoMMIODriver")
        if driver.evidence != HARDWARE_EVIDENCE:
            raise ValueError("Driver backend must identify PYNQ MMIO hardware")
        self.driver = driver
        self.host = network_host(host)
        integer(port, 0, 65535, "port")
        self.driver_timeout_s = duration(driver_timeout_s, "driver_timeout_s")
        self.io_timeout_s = duration(io_timeout_s, "io_timeout_s")
        self._driver_lock = threading.Lock()
        self._failed = False
        self._stopping = threading.Event()
        self._clients_lock = threading.Lock()
        self._clients = set()
        self._server = _TCPServer((self.host, port), _StereoHandler)
        self._server.owner = self
        self.port = self._server.server_address[1]
        self._thread = None
        self._closed = False

    def _compute(self, request):
        validate_request(request)
        evidence = self.driver.evidence
        if not self._driver_lock.acquire(blocking=False):
            return response_for(request, "server_busy", evidence)
        try:
            if request["op"] == "recover":
                try:
                    self.driver.recover()
                    if self.driver.needs_recovery:
                        raise StereoDriverError("Recovery left driver unusable")
                    self._failed = False
                    return response_for(request, "recovered", evidence)
                except Exception:
                    self._failed = True
                    return response_for(request, "driver_error", evidence)
            if self._failed or self.driver.needs_recovery:
                return response_for(request, "recovery_required", evidence)
            try:
                raw = RawStereoInput(**request["input"])
                completed = self.driver.compute(
                    raw, request_tag=request["request_tag"],
                    timeout_s=min(self.driver_timeout_s, request["timeout_s"]))
                return response_for(request, "ok", evidence, completed=completed)
            except ComputationError as exc:
                return response_for(request, "computation_error", evidence, error=exc)
            except RequestTimeout:
                self._failed = True
                return response_for(request, "driver_timeout", evidence)
            except Exception:
                self._failed = True
                return response_for(request, "driver_error", evidence)
        finally:
            self._driver_lock.release()

    def start(self):
        if self._closed:
            raise RuntimeError("Create a fresh server after stop")
        if self._thread is not None:
            return self
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02},
            name="stereo-link-server", daemon=False)
        try:
            self._thread.start()
        except BaseException:
            self._thread = None
            self._server.server_close()
            self._closed = True
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
        # Includes non-daemon request handlers; lease stays held until all exit.
        self._server.server_close()
        self._closed = True

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.stop()


def prepare_drivers(bit_path, bit_sha256=BIT_SHA256, hwh_sha256=HWH_SHA256,
                    overlay_factory=None, gaze_backend_factory=None,
                    stereo_backend_factory=None):
    """Caller holds HardwareLease before loading the paired FPGA artifacts."""
    evidence = verify_artifacts(bit_path, bit_sha256, hwh_sha256)
    if overlay_factory is None:
        from pynq import Overlay
        overlay_factory = Overlay
    overlay = overlay_factory(evidence["bit"]["path"], download=False)
    selections = {}
    for key, vlnv, expected_base in (
        ("gaze", "xilinx.com:user:gaze_map_axi:1.0", 0x43C00000),
        ("stereo", "codex.org:user:stereo_xyz_axi:1.0", 0x43C10000),
    ):
        names = [name for name, info in overlay.ip_dict.items() if info.get("type") == vlnv]
        if len(names) != 1:
            raise RuntimeError("Expected exactly one {} IP".format(key))
        name, info = names[0], overlay.ip_dict[names[0]]
        base, size = info.get("phys_addr"), info.get("addr_range")
        if type(base) is not int or base != expected_base or type(size) is not int or size < 0x80:
            raise RuntimeError("Unexpected {} IP address metadata".format(key))
        selections[key] = name
    overlay.download()  # Exactly one download, after both IPs and artifacts checked.
    gaze_backend_factory = GazeBackend.from_overlay if gaze_backend_factory is None else gaze_backend_factory
    stereo_backend_factory = StereoBackend.from_overlay if stereo_backend_factory is None else stereo_backend_factory
    gaze_backend = gaze_backend_factory(overlay, selections["gaze"])
    stereo_backend = stereo_backend_factory(overlay, selections["stereo"])
    gaze = GazeMapDriver(gaze_backend)
    stereo = StereoMMIODriver(stereo_backend)
    gaze.reset()
    stereo.recover()
    if (gaze_backend.read(GazeRegister.STATE) != 0 or
            gaze_backend.read(GazeRegister.COMPLETE_COUNT) != 0):
        raise RuntimeError("Gaze IP is not clear after the fresh Overlay download")
    if (stereo_backend.read(StereoRegister.STATE) != 0 or
            stereo_backend.read(StereoRegister.COUNT) != 0):
        raise RuntimeError("Stereo IP is not clear after the fresh Overlay download")
    evidence.update(ip_names=selections)
    return overlay, gaze, stereo, evidence


def run_service(args, stop_event, *, lease_factory=HardwareLease,
                overlay_factory=None, gaze_backend_factory=None,
                stereo_backend_factory=None):
    with lease_factory(args.lock_path):
        overlay, gaze, stereo, evidence = prepare_drivers(
            args.bit, args.bit_sha256, args.hwh_sha256, overlay_factory,
            gaze_backend_factory, stereo_backend_factory)
        # Existing gaze service and v1/v2 behavior are reused without source changes.
        gaze_server, stereo_server = None, None
        try:
            gaze_server = MockBoardServer(host=args.bind_host, port=args.gaze_port,
                                         driver=gaze, driver_timeout_s=args.driver_timeout,
                                         io_timeout_s=args.io_timeout)
            stereo_server = StereoTCPServer(host=args.bind_host, port=args.stereo_port,
                                           driver=stereo, driver_timeout_s=args.driver_timeout,
                                           io_timeout_s=args.io_timeout)
            gaze_server.start()
            stereo_server.start()
            print("Combined service ready ({}): gaze {}:{}, stereo {}:{}".format(
                stereo.evidence, args.bind_host, gaze_server.port,
                args.bind_host, stereo_server.port), flush=True)
            while not stop_event.is_set():
                stop_event.wait(0.05)
        finally:
            try:
                if stereo_server is not None:
                    stereo_server.stop()
            finally:
                if gaze_server is not None:
                    gaze_server.stop()
            # overlay stays referenced until both listeners and MMIO handlers exit.


def main():
    parser = argparse.ArgumentParser(description="Explicit combined PYNQ gaze/stereo service")
    parser.add_argument("--bit", required=True)
    parser.add_argument("--bind-host", required=True)
    parser.add_argument("--gaze-port", type=int, default=8765)
    parser.add_argument("--stereo-port", type=int, default=8766)
    parser.add_argument("--bit-sha256", default=BIT_SHA256)
    parser.add_argument("--hwh-sha256", default=HWH_SHA256)
    parser.add_argument("--lock-path", default=LOCK_PATH)
    parser.add_argument("--driver-timeout", type=float, default=0.25)
    parser.add_argument("--io-timeout", type=float, default=5.0)
    args = parser.parse_args()
    network_host(args.bind_host)
    integer(args.gaze_port, 1, 65535, "gaze_port")
    integer(args.stereo_port, 1, 65535, "stereo_port")
    if args.gaze_port == args.stereo_port:
        raise ValueError("Gaze and stereo need distinct ports")
    duration(args.driver_timeout, "driver_timeout")
    duration(args.io_timeout, "io_timeout")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise RuntimeError("Use the board's root Python/PYNQ environment")
    stop_event = threading.Event()
    previous = {}
    def stop(signum, frame):
        stop_event.set()
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, stop)
    try:
        run_service(args, stop_event)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    main()
