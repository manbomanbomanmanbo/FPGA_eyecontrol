"""Serve a virtual canvas; bridge browser samples through the real HostClient TCP path."""
import argparse
from dataclasses import asdict
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from mock_link.host_client import HostClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--board-host', default='127.0.0.1')
    parser.add_argument('--board-port', type=int, default=8765)
    parser.add_argument('--backend-kind', choices=('mock', 'pynq'), default='mock',
                        help='display configuration only; does not authenticate the TCP peer')
    parser.add_argument('--log', default=None)
    args = parser.parse_args()
    client = HostClient(host=args.board_host, port=args.board_port,
                        timeout_s=0.3, result_ttl_s=0.5, log_path=args.log)
    guard = threading.Lock()
    mode = '真实 PYNQ 计算' if args.backend_kind == 'pynq' else 'Mock'
    page = (Path(__file__).with_name('index.html').read_text(encoding='utf-8')
            .replace('{{BACKEND_MODE}}', escape(mode))
            .replace('{{BOARD_ADDRESS}}', escape('{}:{}'.format(args.board_host, args.board_port)))
            .encode('utf-8'))
    javascript = Path(__file__).with_name('app.js').read_bytes()

    def snapshot():
        result = client.last_result
        return {'connected': client.connected, 'session_id': client.session_id,
                'board_host': args.board_host, 'board_port': args.board_port,
                'backend_kind': args.backend_kind,
                'configuration_only': True, 'input_kind': 'synthetic',
                'calibration_version': 'synthetic_linear_v1',
                'last_error': str(client.last_error or ''),
                'result': asdict(result) if result is not None else None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_json(self, value, status=200):
            content = json.dumps(value, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path in ('/', '/app.js'):
                body = page if self.path == '/' else javascript
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8' if self.path == '/' else 'application/javascript')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == '/api/status':
                with guard:
                    self.send_json(snapshot())
            else:
                self.send_error(404)

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 1024:
                    raise ValueError('Bad request length')
                payload = json.loads(self.rfile.read(length) or b'{}')
                # HostClient.disconnect is thread-safe and cancels an in-flight
                # socket operation. Cancellation must not wait behind that work.
                if self.path == '/api/disconnect':
                    client.disconnect()
                    self.send_json(snapshot())
                    return
                if not guard.acquire(blocking=False):
                    self.send_json({'error': 'Request already in progress; nothing queued', 'result': None}, 409)
                    return
                try:
                    try:
                        if self.path == '/api/connect':
                            client.connect()
                        elif self.path == '/api/sample':
                            if payload.get('session_id') != client.session_id or not client.connected:
                                raise ValueError('Stale browser session; sample rejected')
                            client.submit(payload['gx_q16'], payload['gy_q16'], valid=payload.get('valid', 1))
                        else:
                            raise ValueError('Unknown endpoint')
                        data = snapshot()
                    except Exception as exc:
                        if client.connected:
                            client.disconnect()
                        data = snapshot()
                        data['result'] = None
                        data['error'] = str(exc)
                finally:
                    guard.release()
                self.send_json(data)
            except Exception as exc:
                self.send_json({'error': str(exc), 'result': None}, 400)

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print('Virtual point viewer: http://127.0.0.1:{}/; configured backend={} at {}:{}; '
          'synthetic_linear_v1; peer identity is not verified'.format(
              args.port, args.backend_kind, args.board_host, args.board_port), flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        client.close()


if __name__ == '__main__':
    main()
