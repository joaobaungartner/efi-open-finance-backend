"""PostgreSQL wire proxy for cutting the connection at a specific COMMIT."""

import select
import socket
import socketserver
from contextlib import contextmanager
from struct import unpack
from threading import Event, Thread


class CommitProxy(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def __init__(self, target):
        super().__init__(("127.0.0.1", 0), _Connection)
        self.target = target
        self.cut = None
        self.observed = Event()
        self.error = None

    @contextmanager
    def cut_commit(self, when):
        self.observed.clear()
        self.cut = when
        try:
            yield
        finally:
            self.cut = None
            assert self.error is None, self.error
            assert self.observed.wait(2), f"COMMIT não observado: {when}"


class _Connection(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            with socket.create_connection(self.server.target, timeout=5) as upstream:
                self._relay(upstream)
        except Exception as exc:
            self.server.error = exc

    def _relay(self, upstream):
        downstream = self.request
        buffers = {downstream: bytearray(), upstream: bytearray()}
        startup = True
        commit_in_flight = False
        while True:
            ready, _, _ = select.select([downstream, upstream], [], [], 15)
            if not ready:
                return
            for origin in ready:
                chunk = origin.recv(65536)
                if not chunk:
                    return
                buffer = buffers[origin]
                buffer.extend(chunk)
                target = upstream if origin is downstream else downstream
                while True:
                    untagged = startup and origin is downstream
                    offset = 0 if untagged else 1
                    if len(buffer) < offset + 4:
                        break
                    size = offset + unpack("!I", buffer[offset : offset + 4])[0]
                    if not offset + 4 <= size <= 4 * 1024 * 1024:
                        raise ValueError("Tamanho de frame PostgreSQL inválido")
                    if len(buffer) < size:
                        break
                    frame = bytes(buffer[:size])
                    del buffer[:size]
                    if untagged:
                        if frame[4:8] != b"\x00\x03\x00\x00":
                            raise ValueError("Esperado startup PostgreSQL sem SSL/GSS")
                        startup = False
                    elif origin is downstream and frame[0:1] == b"Q":
                        if frame[5:].rstrip(b"\x00").strip().upper() == b"COMMIT":
                            if self.server.cut == "before":
                                self.server.observed.set()
                                return
                            commit_in_flight = self.server.cut == "after"
                    elif (
                        origin is upstream
                        and commit_in_flight
                        and frame[0:1] == b"C"
                        and frame[5:] == b"COMMIT\x00"
                    ):
                        self.server.observed.set()
                        return
                    target.sendall(frame)


@contextmanager
def commit_proxy(target):
    with CommitProxy(target) as proxy:
        thread = Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            yield proxy
        finally:
            proxy.shutdown()
            thread.join()
