"""Five accepted/new PL services; UART bytes and waveforms exist only in PL."""
import argparse
from pathlib import Path
import signal
import socket
import sys
import threading

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT / "software" / "vendor", ROOT / "software", ROOT / "stereo_fpga_stage4" / "software",
             ROOT / "interaction_fpga_stage6" / "software", ROOT / "gimbal_fpga_stage7" / "software",
             Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(path))

from stereo_fpga_stage4.board_server import prepare_drivers, StereoTCPServer
from mock_link.board_server import HardwareLease
from mock_link.mock_board_server import MockBoardServer
from interaction_stage6.board_server import InteractionServer, Server
from interaction_stage6.driver import InteractionDriver
from interaction_stage6.tcp import receive, send
from gimbal_stage7.driver import GimbalDriver
from gimbal_stage7.board_server import GimbalServer
from gimbal_uart_stage8.driver import UARTDriver
from gimbal_uart_stage8.protocol import ID, ABI, BASE, EVIDENCE, Control as C, word


class Handler:
    """Mixin body kept testable without importing pynq or opening hardware."""
    def handle(self):
        owner = self.server.owner
        self.request.settimeout(1200)
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if not owner.slot.acquire(False):
            return
        with owner.clients_lock:
            owner.clients.add(self.request)
        session, previous, owns_arm = None, 0, False
        try:
            # No reset/seed/arm/ACK, even if PL has an old terminal result.
            while not owner.stopping.is_set():
                request = receive(self.request)
                if type(request) is not dict:
                    raise ValueError("Invalid request")
                sid, tag, kind = request.get("session"), request.get("tag"), request.get("kind")
                if (type(sid) is not str or len(sid) != 32 or any(c not in "0123456789abcdef" for c in sid)):
                    raise ValueError("Invalid session")
                word(tag)
                if session is None:
                    session = sid
                if sid != session or tag != previous + 1:
                    raise ValueError("Replay, gap or changed session")
                previous = tag
                base = {"session", "tag", "kind"}
                if kind in ("ping", "query") and set(request) == base:
                    result = owner.driver.query()
                elif kind == "call" and set(request) == base | {"op", "inputs"}:
                    if not owns_arm:
                        raise RuntimeError("This session must explicitly ARM before motion")
                    values, _ = owner.driver.validate_call_request(request["op"], request["inputs"], tag)
                    # A valid explicit owner command renews, not idle connection/query.
                    owner.driver.control(C.HEARTBEAT, tag)
                    result = owner.driver.call(request["op"], values, tag)
                elif kind == "control" and set(request) in (base | {"control"}, base | {"control", "values"}):
                    command = C(word(request["control"]))
                    if command == C.HEARTBEAT and not owns_arm:
                        raise RuntimeError("Heartbeat does not own a prior ARM")
                    result = owner.driver.control(command, tag, request.get("values"))
                    if command == C.ARM:
                        owns_arm = bool(result["telemetry"]["flags"] & 1)
                    elif command in (C.STOP, C.DISARM):
                        owns_arm = False  # Hardware success first; exception still owns cleanup.
                elif kind == "configure" and set(request) == base | {"name", "value"}:
                    result = owner.driver.configure(request["name"], request["value"])
                elif kind == "inject" and set(request) == base | {"value"}:
                    result = owner.driver.inject_reply(request["value"])
                else:
                    raise ValueError("Unknown request")
                if not result["telemetry"]["flags"] & 1:
                    owns_arm = False
                send(self.request, {**result, "session": session, "tag": tag, "owns_arm": owns_arm})
        except (OSError, RuntimeError, ValueError):
            pass
        finally:
            if owns_arm:
                try:
                    owner.driver.control(C.DISARM, previous)
                except Exception:
                    pass  # Independent PL watchdog remains active if host fails.
            with owner.clients_lock:
                owner.clients.discard(self.request)
            owner.slot.release()


import socketserver
class SocketHandler(Handler, socketserver.BaseRequestHandler):
    pass


class UARTServer(InteractionServer):
    def __init__(self, driver, host="192.168.2.99", port=8769):
        self.driver = driver
        self.slot = threading.Lock()
        self.clients_lock = threading.Lock()
        self.clients = set()
        self.stopping = threading.Event()
        self.server = Server((host, port), SocketHandler)
        self.server.owner = self
        self.thread = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bit", required=True)
    parser.add_argument("--bit-sha256", required=True)
    parser.add_argument("--hwh-sha256", required=True)
    parser.add_argument("--bind-host", default="192.168.2.99")
    parser.add_argument("--gaze-port", type=int, default=8765)
    parser.add_argument("--stereo-port", type=int, default=8766)
    parser.add_argument("--interaction-port", type=int, default=8767)
    parser.add_argument("--gimbal-port", type=int, default=8768)
    parser.add_argument("--uart-port", "--gimbal-uart-port", type=int, default=8769)
    args = parser.parse_args()
    from pynq import Overlay, MMIO
    def overlay_factory(path, download=False):
        overlay = Overlay(path, download=download)
        for iptype, base in (("codex.org:user:interaction_axi:1.0", 0x43C20000),
                             ("codex.org:user:gimbal_axi:1.0", 0x43C30000),
                             ("codex.org:user:gimbal_uart_axi:1.0", BASE)):
            names = [name for name, info in overlay.ip_dict.items() if info.get("type") == iptype]
            if len(names) != 1:
                raise RuntimeError("Expected one " + iptype)
            info = overlay.ip_dict[names[0]]
            if info.get("phys_addr") != base or info.get("addr_range", 0) < (0x200 if base == BASE else 0x100):
                raise RuntimeError("Wrong IP register address metadata")
        return overlay
    sys.path.insert(0,str(ROOT))
    from hardware_interaction.board_health import HealthService
    stop = threading.Event()
    def handle_signal(signum, frame):
        stop.set()
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    with HardwareLease("/tmp/gaze_map_axi_owner.lock"):
        overlay, gaze, stereo, evidence = prepare_drivers(args.bit, args.bit_sha256, args.hwh_sha256,
                                                        overlay_factory=overlay_factory)
        interaction = InteractionDriver(MMIO(0x43C20000, 0x10000))
        calculator = GimbalDriver(MMIO(0x43C30000, 0x10000))
        uart = UARTDriver(MMIO(BASE, 0x10000))
        # Preserve the accepted four-service initialization, never reset UART.
        interaction.reset()
        calculator.reset()
        services = []
        try:
            services.extend((MockBoardServer(host=args.bind_host, port=args.gaze_port, driver=gaze),
                             StereoTCPServer(host=args.bind_host, port=args.stereo_port, driver=stereo),
                             InteractionServer(interaction, args.bind_host, args.interaction_port),
                             GimbalServer(calculator, args.bind_host, args.gimbal_port),
                             UARTServer(uart, args.bind_host, args.uart_port),
                             HealthService(overlay,args.bind_host,args.bit_sha256)))
            for service in services:
                service.start()
            print("Hardware gaze/XYZ/interaction/calculator/direct PL UART ready", flush=True)
            while not stop.wait(.1):
                pass
        finally:
            for service in reversed(services):
                service.stop()


if __name__ == "__main__":
    main()
