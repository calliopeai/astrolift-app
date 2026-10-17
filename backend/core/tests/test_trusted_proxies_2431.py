"""Per-IP limits see the client behind the load balancer, never a forged address (#2431).

On ECS the backend's peer is the nginx sidecar on loopback, and nginx's peer
is the ALB. uvicorn trusted loopback only, so it took the client to be the
ALB node nginx appended to ``X-Forwarded-For``: everyone behind one balancer
node shared one sign-in budget (10 a minute) and one invitation-preview
budget (30 a minute).

The tests put that chain in front of ``config.asgi.application``, the
callable uvicorn serves, set up as an install that lists its balancer
subnets in ``ASTROLIFT_TRUSTED_PROXIES``: the peer is nginx and the header is
``<client>, <ALB node>``, as nginx forwards it. The last test serves it with
a real uvicorn, run as ``startup.py`` runs it.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import threading
import time
import urllib.error
import urllib.request
import uuid

import pytest
from asgiref.sync import async_to_sync
from django.core import signals
from django.core.exceptions import ImproperlyConfigured
from django.db import close_old_connections
from django.http import HttpResponse
from uvicorn.logging import AccessFormatter
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from auth1.sessions import Auth1SessionWorkflow
from config import asgi
from config.proxies import parse_trusted_proxies

NGINX = "127.0.0.1"
ALB_SUBNETS = "10.20.0.0/22,10.20.4.0/22"
ALB_NODE = "10.20.1.5"


@pytest.fixture(autouse=True)
def isolated(settings):
    settings.CACHES = {
        **settings.CACHES,
        "proxies_ratelimit": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": str(uuid.uuid4()),
        },
    }
    settings.RATELIMIT_USE_CACHE = "proxies_ratelimit"


@pytest.fixture
def behind_alb(monkeypatch):
    """``config.asgi.application`` as an install with ``ASTROLIFT_TRUSTED_PROXIES`` set runs it."""
    configured = ProxyHeadersMiddleware(None, trusted_hosts=list(parse_trusted_proxies(ALB_SUBNETS)))
    monkeypatch.setattr(asgi.application, "trusted_hosts", configured.trusted_hosts, raising=False)
    return asgi.application


@contextlib.contextmanager
def _keep_test_connection():
    """As Django's test client does: a request must not close the test's own connection."""
    signals.request_started.disconnect(close_old_connections)
    signals.request_finished.disconnect(close_old_connections)
    try:
        yield
    finally:
        signals.request_started.connect(close_old_connections)
        signals.request_finished.connect(close_old_connections)


@pytest.fixture
def idp(monkeypatch):
    """The IdP redirect, recording the client address and callback URL each sign-in started with."""
    seen: list[tuple[str, str]] = []

    def authorize_redirect(request, redirect_uri, **kwargs):
        seen.append((request.META["REMOTE_ADDR"], redirect_uri))
        return HttpResponse(status=302, headers={"Location": "https://idp.test/"})

    monkeypatch.setattr(Auth1SessionWorkflow._client.auth0, "authorize_redirect", authorize_redirect)
    return seen


def _call(app, path, *, method="GET", peer=NGINX, forwarded_for=None, proto=None, body=b"") -> int:
    headers = [(b"host", b"astro.example"), (b"content-type", b"application/json")]
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    if proto is not None:
        headers.append((b"x-forwarded-proto", proto.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": (peer, 51234),
        "server": ("10.20.8.9", 8000),
    }
    sent: list[dict] = []

    async def run():
        delivered = False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()  # the client stays connected

        async def send(message):
            sent.append(message)

        await app(scope, receive, send)

    # Sync code inside runs on this thread, in the test's transaction.
    with _keep_test_connection():
        async_to_sync(run)()
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _sign_in(app, client: str, **kwargs) -> int:
    return _call(app, "/app/auth1/login", forwarded_for=f"{client}, {ALB_NODE}", **kwargs)


def _preview(app, client: str) -> int:
    return _call(
        app,
        "/app/auth1/invitation-preview",
        method="POST",
        forwarded_for=f"{client}, {ALB_NODE}",
        body=json.dumps({"token": "alft_unknown"}).encode(),
    )


@pytest.mark.django_db
def test_clients_behind_one_balancer_get_their_own_sign_in_budget(behind_alb, idp):
    for _ in range(10):
        assert _sign_in(behind_alb, "203.0.113.10") == 302
    assert _sign_in(behind_alb, "203.0.113.10") == 429
    assert _sign_in(behind_alb, "203.0.113.11") == 302
    assert {address for address, _ in idp} == {"203.0.113.10", "203.0.113.11"}


@pytest.mark.django_db
def test_clients_behind_one_balancer_get_their_own_invitation_preview_budget(behind_alb):
    for _ in range(30):
        assert _preview(behind_alb, "203.0.113.20") == 200
    assert _preview(behind_alb, "203.0.113.20") == 429
    assert _preview(behind_alb, "203.0.113.21") == 200


@pytest.mark.django_db
def test_a_peer_that_is_not_a_trusted_proxy_cannot_choose_its_address(behind_alb, idp):
    for i in range(10):
        assert (
            _call(behind_alb, "/app/auth1/login", peer="198.51.100.7", forwarded_for=f"203.0.113.{i}") == 302
        )
    assert _call(behind_alb, "/app/auth1/login", peer="198.51.100.7", forwarded_for="203.0.113.99") == 429
    assert {address for address, _ in idp} == {"198.51.100.7"}


@pytest.mark.django_db
def test_addresses_a_client_puts_in_the_header_itself_are_ignored(behind_alb, idp):
    # The ALB appends the address it saw to whatever the client sent.
    for i in range(10):
        forged = f"6.6.6.{i}, 203.0.113.30, {ALB_NODE}"
        assert _call(behind_alb, "/app/auth1/login", forwarded_for=forged) == 302
    assert _call(behind_alb, "/app/auth1/login", forwarded_for=f"6.6.6.99, 203.0.113.30, {ALB_NODE}") == 429
    assert {address for address, _ in idp} == {"203.0.113.30"}


@pytest.mark.django_db
def test_the_scheme_comes_only_from_a_trusted_proxy(behind_alb, idp):
    # The IdP's callback URL, built from it, must stay https behind the ALB.
    _sign_in(behind_alb, "203.0.113.31", proto="https")
    _call(behind_alb, "/app/auth1/login", peer="198.51.100.8", proto="https")
    assert [uri.split("://")[0] for _, uri in idp] == ["https", "http"]


@pytest.mark.django_db
def test_without_the_balancer_subnets_every_client_is_the_balancer(idp):
    # This run sets no ASTROLIFT_TRUSTED_PROXIES: loopback (nginx) is trusted,
    # as uvicorn trusted it, and the ALB node is the first untrusted hop.
    assert _sign_in(asgi.application, "203.0.113.40") == 302
    assert idp == [(ALB_NODE, "http://astro.example/app/auth1/callback")]


@pytest.mark.parametrize(
    ("raw", "trusted"),
    [
        ("", ("127.0.0.1",)),
        (None, ("127.0.0.1",)),
        (" 10.20.0.0/22 , 10.20.4.0/22 ", ("127.0.0.1", "10.20.0.0/22", "10.20.4.0/22")),
        ("10.20.1.5,127.0.0.1,10.20.1.5", ("127.0.0.1", "10.20.1.5")),
        ("10.20.1.5/22", ("127.0.0.1", "10.20.0.0/22")),
        ("2600:1f14:abc::/56", ("127.0.0.1", "2600:1f14:abc::/56")),
    ],
)
def test_the_setting_lists_loopback_and_each_address_or_range(raw, trusted):
    assert parse_trusted_proxies(raw) == trusted


@pytest.mark.parametrize(
    "raw", ["*", "0.0.0.0/0", "::/0", "10.0.0.0/33", "alb.internal", "10.0.0.1 10.0.0.2"]
)
def test_a_setting_that_trusts_everyone_or_cannot_be_read_stops_the_app(raw):
    with pytest.raises(ImproperlyConfigured, match="ASTROLIFT_TRUSTED_PROXIES"):
        parse_trusted_proxies(raw)


def test_a_real_uvicorn_behind_nginx_limits_and_logs_each_client(behind_alb):
    import uvicorn

    stream = io.StringIO()
    capture = logging.StreamHandler(stream)
    capture.setFormatter(
        AccessFormatter(
            '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False
        )
    )
    access = logging.getLogger("uvicorn.access")
    access.addHandler(capture)
    # startup.py's flags: uvicorn's own proxy handling off, so config.asgi decides.
    server = uvicorn.Server(
        uvicorn.Config(
            behind_alb, host="127.0.0.1", port=0, proxy_headers=False, lifespan="off", log_config=None
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started:
            assert time.monotonic() < deadline and thread.is_alive(), "uvicorn did not start"
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]

        def post(path: str, client: str) -> int:
            # From loopback, as the sidecar connects; the header as nginx forwards it.
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                data=b"",
                method="POST",
                headers={"X-Forwarded-For": f"{client}, {ALB_NODE}"},
            )
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code

        # The retired relay answers 410 and allows 5 a minute per client.
        first = [post("/app/auth1/session?code=SECRETcode&state=s", "203.0.113.50") for _ in range(6)]
        second = post("/app/auth1/session", "203.0.113.51")
    finally:
        server.should_exit = True
        thread.join(10)
        access.removeHandler(capture)

    assert first == [410] * 5 + [429]
    assert second == 410
    lines = stream.getvalue()
    assert lines.count("203.0.113.50:0") == 6 and "203.0.113.51:0" in lines
    assert ALB_NODE not in lines and NGINX not in lines
    assert "SECRET" not in lines and "code=***REDACTED***&state=s" in lines
