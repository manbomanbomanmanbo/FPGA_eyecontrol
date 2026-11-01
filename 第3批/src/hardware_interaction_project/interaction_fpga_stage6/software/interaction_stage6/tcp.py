"""Exclusive, ordered TCP access to the PL interaction state machine."""
import json
import math
import socket
import struct
import threading
import time
import uuid

from .driver import ID, ABI, EVIDENCE


def _word(value):
    if type(value) is not int or not 0 <= value <= 0xffffffff:
        raise ValueError('Expected uint32 hardware word')
    return value


def send(sock, value):
    raw = json.dumps(value, allow_nan=False, separators=(',', ':')).encode('utf8')
    if len(raw) > 65536:
        raise ValueError('Frame exceeds protocol limit')
    sock.sendall(struct.pack('!I', len(raw)) + raw)


def receive(sock):
    def exact(length):
        blocks = []
        while length:
            block = sock.recv(length)
            if not block:
                raise ConnectionError('Interaction link closed')
            blocks.append(block)
            length -= len(block)
        return b''.join(blocks)
    length = struct.unpack('!I', exact(4))[0]
    if not 1 <= length <= 65536:
        raise ValueError('Invalid interaction frame length')
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate JSON key')
            value[key] = item
        return value
    return json.loads(exact(length).decode('utf8'), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


class TCPClient:
    def __init__(self, host='192.168.2.99', port=8767, timeout_s=1.0):
        self.host, self.port, self.timeout_s = host, port, timeout_s
        self.sock = None
        self.session = uuid.uuid4().hex
        self.tag = 0
        self.lock = threading.RLock()
        self.failed = False

    def connect(self):
        with self.lock:
            if self.sock is not None:
                return self
            if self.failed:
                raise RuntimeError('Start a fresh interaction session after failure')
            self.sock = socket.create_connection((self.host, self.port), self.timeout_s)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.settimeout(self.timeout_s)
            try:
                result = self._request('ping')
                if result.get('id') != ID or result.get('abi') != ABI:
                    raise ValueError('Unexpected hardware identity')
            except BaseException:
                self.close()
                self.failed = True
                raise
            return self

    def restart_session(self):
        """Reset healthy tracking state through a fresh exclusive PL session.

        The board resets its interaction IP when it accepts a new connection.
        Its previous handler may still own the slot after our socket closes;
        retry those connection closures within one bounded recovery window.
        An already failed session remains failed and requires a new process.
        """
        with self.lock:
            if self.failed:
                raise RuntimeError('Start a fresh interaction session after failure')
            self.close()
            self.session = uuid.uuid4().hex
            self.tag = 0
            deadline = time.monotonic() + 1.0
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('Interaction tracking restart timed out')
                    try:
                        attempt_timeout = min(self.timeout_s, remaining)
                        self.sock = socket.create_connection((self.host, self.port), attempt_timeout)
                        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError('Interaction tracking restart timed out')
                        self.sock.settimeout(min(self.timeout_s, remaining))
                        result = self._request('ping')
                        if result.get('id') != ID or result.get('abi') != ABI:
                            raise ValueError('Unexpected hardware identity')
                        self.sock.settimeout(self.timeout_s)
                        return self
                    except OSError:
                        self.close()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError('Interaction tracking restart timed out')
                        time.sleep(min(.02, remaining))
            except BaseException:
                self.failed = True
                self.close()
                raise

    def _request(self, kind, op=None, inputs=None):
        if self.tag == 0xffffffff:
            raise RuntimeError('Interaction sequence exhausted')
        self.tag += 1
        request = {'session': self.session, 'tag': self.tag, 'kind': kind}
        if kind == 'call':
            request.update(op=op, inputs=inputs)
        send(self.sock, request)
        result = receive(self.sock)
        if (type(result) is not dict or result.get('session') != self.session
                or result.get('tag') != self.tag or result.get('evidence') != EVIDENCE):
            raise ValueError('Unmatched or non-hardware interaction response')
        if result.get('error'):
            raise RuntimeError(result['error'])
        return result

    def call(self, op, inputs=None):
        if type(op) is not int or not 1 <= op <= 12:
            raise ValueError('Unknown interaction operation')
        values = list(inputs or [])
        if len(values) > 8:
            raise ValueError('Too many input words')
        values += [0] * (8-len(values))
        values = [_word(value) for value in values]
        with self.lock:
            self.connect()
            try:
                result = self._request('call', op, values)
                if (type(result.get('output')) is not list or len(result['output']) != 8
                        or any(type(x) is not int or not 0 <= x <= 0xffffffff for x in result['output'])
                        or type(result.get('count')) is not int
                        or type(result.get('status')) is not int):
                    raise ValueError('Invalid PL result format')
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
