"""Explicit real-PYNQ entry point. Importing this module never loads hardware."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

# The board uses Python 3.6. Its dataclasses backport stays project-local.
if sys.version_info < (3, 7):
    vendor = Path(__file__).resolve().parents[1] / "vendor"
    if vendor.is_dir() and str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))

from pynq_gaze_driver import GazeMapDriver, PynqMMIOBackend, Register
from .mock_board_server import MockBoardServer
from .protocol import duration, integer, network_host

BIT_SHA256 = "5b7968a6d2d1679247a0b24b26732f52cb161b6a6e7990297084428e4171097d"
HWH_SHA256 = "43c75199e56e531ee524c1b5010403ab7ecf861f488dae7236776ca59470e856"
DEFAULT_LOCK_PATH = "/tmp/gaze_map_axi_owner.lock"


class HardwareLease:
    """Linux advisory lock shared with the board tests; not an MMIO access barrier."""
    def __init__(self, path=DEFAULT_LOCK_PATH):
        self.path = Path(path)
        self.stream = None
        self._fcntl = None

    def __enter__(self):
        import fcntl
        self._fcntl = fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise RuntimeError("gaze hardware is already owned: " + str(self.path)) from exc
        try:
            stream.seek(0)
            stream.truncate()
            stream.write(json.dumps({"pid": os.getpid(), "owner": "mock_link.board_server"}) + "\n")
            stream.flush()
        except BaseException:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
            raise
        self.stream = stream
        return self

    def __exit__(self, *args):
        try:
            self._fcntl.flock(self.stream.fileno(), self._fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifacts(bit_path, bit_sha256=BIT_SHA256, hwh_sha256=HWH_SHA256):
    bit_path = Path(bit_path).resolve()
    if bit_path.suffix.lower() != ".bit":
        raise ValueError("--bit must name a .bit file with a same-prefix .hwh")
    evidence = {}
    for kind, path, expected in (("bit", bit_path, bit_sha256),
                                 ("hwh", bit_path.with_suffix(".hwh"), hwh_sha256)):
        if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
            raise ValueError(kind + " expected SHA-256 must contain 64 hex characters")
        if not path.is_file():
            raise RuntimeError("Missing paired artifact: " + str(path))
        actual = _hash_file(path)
        if actual != expected.lower():
            raise RuntimeError(kind + " SHA-256 mismatch; hardware was not loaded")
        evidence[kind] = {"path": str(path), "sha256": actual}
    return evidence


def prepare_driver(bit_path, bit_sha256=BIT_SHA256, hwh_sha256=HWH_SHA256,
                   overlay_factory=None, backend_factory=None):
    """Caller must hold HardwareLease. Factories are for offline tests only."""
    evidence = verify_artifacts(bit_path, bit_sha256, hwh_sha256)
    if overlay_factory is None:
        from pynq import Overlay
        overlay_factory = Overlay
    overlay = overlay_factory(evidence["bit"]["path"], download=False)
    names = [name for name, info in overlay.ip_dict.items()
             if info.get("type") == "xilinx.com:user:gaze_map_axi:1.0"]
    if len(names) != 1:
        raise RuntimeError("Expected exactly one gaze_map_axi:1.0: " + repr(names))
    name = names[0]
    metadata = overlay.ip_dict[name]
    base, size = metadata.get("phys_addr"), metadata.get("addr_range")
    if type(base) is not int or base < 0 or base % 4 or type(size) is not int or size < 0x80:
        raise RuntimeError("Invalid gaze IP address metadata")
    overlay.download()
    if backend_factory is None:
        backend_factory = PynqMMIOBackend.from_overlay
    backend = backend_factory(overlay, name)
    driver = GazeMapDriver(backend)
    driver.reset()
    if backend.read(Register.STATE) != 0 or backend.read(Register.COMPLETE_COUNT) != 0:
        raise RuntimeError("ABORT did not clear initial STATE and COMPLETE_COUNT")
    evidence.update(ip_name=name, phys_addr=base, addr_range=size,
                    ip_id=backend.read(Register.IP_ID), abi_version=backend.read(Register.ABI_VERSION))
    return driver, evidence


def run_service(args, stop_event, lease_factory=HardwareLease,
                overlay_factory=None, backend_factory=None, server_factory=MockBoardServer):
    # The lock stays held through stop(), including all active driver handlers.
    with lease_factory(args.lock_path):
        driver, evidence = prepare_driver(args.bit, args.bit_sha256, args.hwh_sha256,
                                         overlay_factory, backend_factory)
        server = server_factory(host=args.bind_host, port=args.port, driver=driver,
            driver_timeout_s=args.driver_timeout, io_timeout_s=args.io_timeout, log_path=args.log)
        try:
            server.logger.write("hardware_ready", backend="pynq", **evidence)
            server.start()
            print("Real PYNQ server on {}:{}; synthetic_linear_v1; no physical mouse".format(
                server.host, server.port), flush=True)
            print(json.dumps(evidence, ensure_ascii=False), flush=True)
            until = None if args.serve_seconds is None else time.monotonic() + args.serve_seconds
            while not stop_event.is_set() and (until is None or time.monotonic() < until):
                stop_event.wait(0.05)
        finally:
            server.stop()


def main():
    parser = argparse.ArgumentParser(description="Explicit PYNQ hardware service; no Mock fallback")
    parser.add_argument("--bit", required=True)
    parser.add_argument("--bind-host", required=True, help="Explicit trusted-LAN interface address")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bit-sha256", default=BIT_SHA256)
    parser.add_argument("--hwh-sha256", default=HWH_SHA256)
    parser.add_argument("--lock-path", default=DEFAULT_LOCK_PATH)
    parser.add_argument("--driver-timeout", type=float, default=0.25)
    parser.add_argument("--io-timeout", type=float, default=5.0)
    parser.add_argument("--log", default=None)
    parser.add_argument("--serve-seconds", type=float, default=None)
    args = parser.parse_args()
    network_host(args.bind_host)
    integer(args.port, 1, 65535, "port")
    duration(args.driver_timeout, "driver_timeout")
    duration(args.io_timeout, "io_timeout")
    if args.serve_seconds is not None:
        duration(args.serve_seconds, "serve_seconds")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise RuntimeError("Run with the board's root Python/PYNQ environment; no automatic elevation")
    stop_event = threading.Event()
    previous = {}
    # SIGINT/SIGTERM request orderly shutdown. A second signal does not release
    # the lease while a native MMIO call is still inside a request thread.
    def request_stop(signum, frame):
        stop_event.set()
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, request_stop)
    try:
        run_service(args, stop_event)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    main()
