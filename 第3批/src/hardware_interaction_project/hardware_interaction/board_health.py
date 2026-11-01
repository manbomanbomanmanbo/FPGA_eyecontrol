"""Board lifecycle lease. Observation decisions remain in interaction PL."""
import json
import os
import signal
import socket
import socketserver
import threading
import time

class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        owner=self.server.owner
        with owner.lock:
            owner.clients.add(self.request);owner.last_seen=time.monotonic()
        try:
            self.request.settimeout(15)
            self.request.sendall((json.dumps(owner.info)+'\n').encode())
            stream=self.request.makefile('rb')
            try:
                while not owner.stopping.is_set():
                    line=stream.readline(128)
                    if line!=b'ping\n':break
                    with owner.lock:owner.last_seen=time.monotonic()
                    self.request.sendall(b'pong\n')
            finally:stream.close()
        except OSError:pass
        finally:
            with owner.lock:
                owner.clients.discard(self.request);owner.last_seen=time.monotonic()

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True

class HealthService:
    def __init__(self,overlay,host,bit_sha256,port=8770):
        self.info={'backend':'PL','project':'hardware_interaction','abi':3,'bit_sha256':bit_sha256}
        self.lock=threading.Lock();self.clients=set();self.last_seen=time.monotonic()
        self.stopping=threading.Event();self.server=Server((host,port),Handler)
        self.server.owner=self
    def start(self):
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        def monitor():
            while not self.stopping.wait(2):
                with self.lock:abandoned=not self.clients and time.monotonic()-self.last_seen>30
                if abandoned:os.kill(os.getpid(),signal.SIGTERM);return
        threading.Thread(target=monitor,daemon=True).start()
    def stop(self):
        self.stopping.set()
        with self.lock:
            for s in list(self.clients):
                try:s.shutdown(socket.SHUT_RDWR)
                except OSError:pass
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
