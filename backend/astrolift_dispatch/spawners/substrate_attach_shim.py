"""In-box attach shim for the substrate backend (#1853 spike, do not merge).

Never imported by Astrolift. SubstrateSpawner ships this file into the box as
a payload argument and runs it next to the keep-alive, standard library only.

Substrate reaches an actor only over HTTP through atenet-router, and a K8s
exec into the worker pod lands outside the gVisor sandbox. So the box serves
its own attach: /readyz (the template's wakeupProbe), /exec (run one command,
return its output) and /attach (a WebSocket bridged to a pty running
``tmux new-session -A -s astrolift``, the argv ``astro exec`` uses today).
Anything that can reach atenet-router can reach this, so the router must be
fenced to the Astrolift relay.
"""

import base64
import hashlib
import json
import os
import pty
import shlex
import struct
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
ATTACH_ARGV = ["tmux", "new-session", "-A", "-s", "astrolift"]
# Non-root boxes: the shim stays root (port 80) and drops to uid 42042 for
# everything it runs on the agent's behalf.
RUN_AS = shlex.split(os.environ.get("BOX_RUN_AS", ""))


def ws_send(sock, payload: bytes, opcode: int = 0x2) -> None:
    header = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header += bytes([n])
    elif n < 65536:
        header += bytes([126]) + struct.pack("!H", n)
    else:
        header += bytes([127]) + struct.pack("!Q", n)
    sock.sendall(header + payload)


def ws_recv(rfile):
    head = rfile.read(2)
    if len(head) < 2:
        return None, b""
    opcode = head[0] & 0x0F
    n = head[1] & 0x7F
    if n == 126:
        n = struct.unpack("!H", rfile.read(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", rfile.read(8))[0]
    mask = rfile.read(4) if head[1] & 0x80 else b"\0\0\0\0"
    data = bytearray(rfile.read(n))
    for i in range(len(data)):
        data[i] ^= mask[i % 4]
    return opcode, bytes(data)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _reply(self, code: int, body: bytes, ctype: str = "text/plain") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/readyz":
            self._reply(200, b"ok")
        elif self.path == "/attach" and self.headers.get("Upgrade", "").lower() == "websocket":
            self._attach()
        else:
            self._reply(404, b"not found")

    def do_POST(self):
        if self.path != "/exec":
            self._reply(404, b"not found")
            return
        cmd = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        proc = subprocess.run(RUN_AS + ["/bin/bash", "-c", cmd], capture_output=True, text=True, timeout=30)
        body = json.dumps({"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr}).encode()
        self._reply(200, body, "application/json")

    def _attach(self):
        key = self.headers["Sec-WebSocket-Key"]
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()
        pid, fd = pty.fork()
        if pid == 0:
            os.environ["TERM"] = "xterm-256color"
            argv = RUN_AS + ATTACH_ARGV
            os.execvp(argv[0], argv)
        sock = self.connection

        def pump_in():
            while True:
                opcode, data = ws_recv(self.rfile)
                if opcode is None or opcode == 0x8:
                    break
                if opcode in (0x1, 0x2):
                    os.write(fd, data)
            try:
                os.kill(pid, 15)
            except OSError:
                pass

        threading.Thread(target=pump_in, daemon=True).start()
        try:
            while True:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                ws_send(sock, chunk)
        except OSError:
            pass
        finally:
            try:
                ws_send(sock, b"", 0x8)
            except OSError:
                pass
            os.waitpid(pid, 0)
        self.close_connection = True


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("BOX_PORT", "80"))), Handler).serve_forever()
