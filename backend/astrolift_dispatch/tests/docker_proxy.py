"""Opt-in tests forward real Docker requests and can temporarily reject deletion."""

import http.client
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class DockerProxy:
    def __init__(self, socket_path: str = "/var/run/docker.sock"):
        self.block_deletes = False
        self.requests = []
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def forward(self):
                blocked = self.command == "DELETE" and proxy.block_deletes
                proxy.requests.append((self.command, self.path, blocked))
                if blocked:
                    content = json.dumps({"message": "disposable verification deletion fault"}).encode()
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
                connection = http.client.HTTPConnection("localhost", timeout=30)
                connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connection.sock.settimeout(30)
                connection.sock.connect(socket_path)
                try:
                    if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                        chunks = []
                        while size := int(self.rfile.readline().split(b";", 1)[0], 16):
                            chunks.append(self.rfile.read(size))
                            self.rfile.read(2)
                        while self.rfile.readline() != b"\r\n":
                            pass
                        body = b"".join(chunks)
                    else:
                        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    headers = {
                        key: value
                        for key, value in self.headers.items()
                        if key.lower() not in {"transfer-encoding", "content-length", "connection"}
                    }
                    headers["Content-Length"] = str(len(body))
                    connection.request(self.command, self.path, body=body, headers=headers)
                    response = connection.getresponse()
                    self.send_response(response.status)
                    for key, value in response.getheaders():
                        if key.lower() not in {"transfer-encoding", "connection"}:
                            self.send_header(key, value)
                    self.end_headers()
                    if self.command != "HEAD":
                        while content := response.read1(65536):
                            self.wfile.write(content)
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
                finally:
                    connection.close()

            do_HEAD = do_GET = do_POST = do_DELETE = forward

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.endpoint = f"tcp://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
