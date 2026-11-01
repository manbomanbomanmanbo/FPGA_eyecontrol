"""Exclusive ordered TCP access; no CPU gimbal math or fallback."""
import socket
import threading
import uuid
from interaction_stage6.tcp import send, receive, _word
from .protocol import ID, ABI, EVIDENCE


class TCPClient:
    def __init__(self, host='192.168.2.99', port=8768, timeout_s=1.0):
        self.host, self.port, self.timeout_s = host, port, timeout_s
        self.sock = None
        self.session = uuid.uuid4().hex
        self.tag, self.count = 0, 0
        self.failed = False
        self.lock = threading.RLock()

    def _request(self, kind, op=None, inputs=None):
        if self.tag == 0xffffffff:
            raise RuntimeError('Gimbal sequence exhausted')
        self.tag += 1
        request = {'session': self.session, 'tag': self.tag, 'kind': kind}
        if kind == 'call':
            request.update(op=op, inputs=inputs)
        send(self.sock, request)
        result = receive(self.sock)
        if (type(result) is not dict or result.get('session') != self.session
                or type(result.get('tag')) is not int or result['tag'] != self.tag
                or result.get('evidence') != EVIDENCE or result.get('error')):
            raise RuntimeError('Unmatched or non-hardware gimbal response')
        return result

    def connect(self):
        with self.lock:
            if self.sock is not None:
                return self
            if self.failed:
                raise RuntimeError('Start a fresh gimbal session after failure')
            self.sock = socket.create_connection((self.host, self.port), self.timeout_s)
            self.sock.settimeout(self.timeout_s)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                response = self._request('ping')
                if response.get('id') != ID or response.get('abi') != ABI:
                    raise ValueError('Gimbal hardware ID/ABI mismatch')
            except BaseException:
                self.failed = True
                self.close()
                raise
            return self

    def call(self, op, inputs=None):
        if type(op) is not int or op not in (1, 2):
            raise ValueError('Unknown gimbal operation')
        values = list(inputs or [])
        if len(values) > 16:
            raise ValueError('Too many gimbal inputs')
        values += [0] * (16 - len(values))
        values = [_word(value) for value in values]
        with self.lock:
            self.connect()
            try:
                result = self._request('call', op, values)
                if (type(result.get('output')) is not list or len(result['output']) != 16
                        or any(type(x) is not int or not 0 <= x <= 0xffffffff for x in result['output'])
                        or type(result.get('status')) is not int or result['status'] not in range(12)
                        or type(result.get('count')) is not int
                        or result['count'] != ((self.count + 1) & 0xffffffff)):
                    raise ValueError('Invalid or stale PL gimbal result')
                self.count = result['count']
                return result
            except BaseException:
                self.failed = True
                self.close()
                raise

    def close(self):
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.sock.close()
                self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *args):
        self.close()
