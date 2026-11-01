"""One Overlay and four real PL services, with the accepted interaction reused."""
import argparse
from pathlib import Path
import signal
import socket
import socketserver
import sys
import threading

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT / 'software' / 'vendor', ROOT / 'software',
             ROOT / 'stereo_fpga_stage4' / 'software',
             ROOT / 'interaction_fpga_stage6' / 'software', Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(path))

from stereo_fpga_stage4.board_server import prepare_drivers, StereoTCPServer
from mock_link.board_server import HardwareLease
from mock_link.mock_board_server import MockBoardServer
from interaction_stage6.board_server import InteractionServer
from interaction_stage6.driver import InteractionDriver
from gimbal_stage7.driver import GimbalDriver
from gimbal_stage7.tcp import receive, send, _word
from gimbal_stage7.protocol import ID, ABI, BASE, EVIDENCE


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        owner = self.server.owner
        self.request.settimeout(1200)
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if not owner.slot.acquire(False):
            return
        with owner.clients_lock:
            owner.clients.add(self.request)
        session, previous = None, 0
        try:
            owner.driver.reset()
            while not owner.stopping.is_set():
                request = receive(self.request)
                if type(request) is not dict:
                    raise ValueError('Invalid request')
                sid, tag, kind = request.get('session'), request.get('tag'), request.get('kind')
                if (type(sid) is not str or len(sid) != 32
                        or any(c not in '0123456789abcdef' for c in sid)):
                    raise ValueError('Invalid session')
                _word(tag)
                if session is None:
                    session = sid
                if sid != session or tag != previous + 1:
                    raise ValueError('Replay, gap or changed session')
                previous = tag
                result = {'session': session, 'tag': tag, 'evidence': EVIDENCE}
                if kind == 'ping' and set(request) == {'session', 'tag', 'kind'}:
                    result.update(id=ID, abi=ABI)
                elif kind == 'call' and set(request) == {'session', 'tag', 'kind', 'op', 'inputs'}:
                    op, values = request['op'], request['inputs']
                    if type(op) is not int or op not in (1, 2) or type(values) is not list or len(values) != 16:
                        raise ValueError('Invalid operation or word count')
                    for value in values:
                        _word(value)
                    result.update(owner.driver.compute(op, values, tag))
                else:
                    raise ValueError('Unknown request')
                send(self.request, result)
        except (OSError, RuntimeError, ValueError):
            pass
        finally:
            with owner.clients_lock:
                owner.clients.discard(self.request)
            owner.slot.release()


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = False


class GimbalServer(InteractionServer):
    def __init__(self, driver, host, port):
        self.driver = driver
        self.slot = threading.Lock()
        self.clients_lock = threading.Lock()
        self.clients = set()
        self.stopping = threading.Event()
        self.server = Server((host, port), Handler)
        self.server.owner = self
        self.thread = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bit', required=True)
    parser.add_argument('--bit-sha256', required=True)
    parser.add_argument('--hwh-sha256', required=True)
    parser.add_argument('--bind-host', default='192.168.2.99')
    parser.add_argument('--gaze-port', type=int, default=8765)
    parser.add_argument('--stereo-port', type=int, default=8766)
    parser.add_argument('--interaction-port', type=int, default=8767)
    parser.add_argument('--gimbal-port', type=int, default=8768)
    args = parser.parse_args()
    from pynq import Overlay, MMIO
    def overlay_factory(path, download=False):
        overlay = Overlay(path, download=download)
        for iptype, base in (('codex.org:user:interaction_axi:1.0', 0x43C20000),
                             ('codex.org:user:gimbal_axi:1.0', BASE)):
            names = [name for name, info in overlay.ip_dict.items() if info.get('type') == iptype]
            if len(names) != 1:
                raise RuntimeError('Expected one ' + iptype)
            info = overlay.ip_dict[names[0]]
            if info.get('phys_addr') != base or info.get('addr_range', 0) < 0x100:
                raise RuntimeError('Wrong IP address metadata: ' + iptype)
        return overlay
    stop = threading.Event()
    def handle_signal(signum, frame):
        stop.set()
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    with HardwareLease('/tmp/gaze_map_axi_owner.lock'):
        overlay, gaze, stereo, evidence = prepare_drivers(
            args.bit, args.bit_sha256, args.hwh_sha256, overlay_factory=overlay_factory)
        interaction = InteractionDriver(MMIO(0x43C20000, 0x10000))
        gimbal = GimbalDriver(MMIO(BASE, 0x10000))
        interaction.reset()
        gimbal.reset()
        services = []
        try:
            services.append(MockBoardServer(host=args.bind_host, port=args.gaze_port, driver=gaze))
            services.append(StereoTCPServer(host=args.bind_host, port=args.stereo_port, driver=stereo))
            services.append(InteractionServer(interaction, args.bind_host, args.interaction_port))
            services.append(GimbalServer(gimbal, args.bind_host, args.gimbal_port))
            for service in services:
                service.start()
            print('Hardware gaze/XYZ/interaction/gimbal calculation ready', flush=True)
            while not stop.wait(.1):
                pass
        finally:
            for service in reversed(services):
                service.stop()


if __name__ == '__main__':
    main()
