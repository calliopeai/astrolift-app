"""Piped exec gets stdin EOF over the v5 subprotocol (astrolift#145).

`echo hello | astro exec ... -- cat` printed hello and hung: the relay
forwarded the CLI's stdin_eof, but the session had no way to half-close
the remote stdin. v5.channel.k8s.io adds a binary [255, channel] close
message; the session sends it when the API server negotiated v5 and stays
a no-op against a v4-only server.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from k8s_native import observability
from k8s_native.observability import InteractiveExecSession, _negotiated_protocol


class _Sock:
    def __init__(self, protocol):
        headers = {"sec-websocket-protocol": protocol} if protocol else {}
        self.handshake_response = SimpleNamespace(headers=headers)
        self.sent = []

    def send(self, data, opcode):
        self.sent.append((data, opcode))


class _Resp:
    def __init__(self, protocol):
        self.sock = _Sock(protocol)

    def is_open(self):
        return False

    def read_channel(self, _channel):
        return ""


def _session(protocol):
    return InteractiveExecSession(_Resp(protocol))


def test_v5_close_stdin_sends_the_binary_close_frame():
    from websocket import ABNF

    session = _session("v5.channel.k8s.io")
    asyncio.run(session.close_stdin())

    assert session._resp.sock.sent == [(bytes([255, 0]), ABNF.OPCODE_BINARY)]


def test_v4_server_gets_no_close_frame():
    session = _session("v4.channel.k8s.io")
    asyncio.run(session.close_stdin())

    assert session._resp.sock.sent == []


def test_negotiated_protocol_reads_the_handshake_and_tolerates_anything():
    assert _negotiated_protocol(_Resp("v5.channel.k8s.io")) == "v5.channel.k8s.io"
    assert _negotiated_protocol(_Resp(None)) == ""
    assert _negotiated_protocol(SimpleNamespace()) == ""


def test_exec_offers_v5_then_v4(monkeypatch):
    captured = {}

    class _Api:
        def set_default_header(self, name, value):
            captured[name] = value

    monkeypatch.setattr(observability, "build_api_client", lambda _auth: _Api())
    import kubernetes.client
    import kubernetes.stream

    monkeypatch.setattr(
        kubernetes.client, "CoreV1Api", lambda api: SimpleNamespace(connect_get_namespaced_pod_exec=None)
    )
    monkeypatch.setattr(kubernetes.stream, "stream", lambda *a, **k: _Resp("v5.channel.k8s.io"))

    observability.open_interactive_exec(
        auth=None, namespace="ns", pod_name="p", container="c", command=["cat"], tty=False
    )

    assert captured == {"sec-websocket-protocol": "v5.channel.k8s.io,v4.channel.k8s.io"}
