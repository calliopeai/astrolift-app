"""A forged X-Forwarded-For never reaches audit, sessions, ABAC or rate limits (#2440).

``config.asgi`` resolves the client from the trusted proxies' header into
``REMOTE_ADDR`` (#2431). Every reader of the client address takes it from
there. Before, audit rows, session tracking, API and deploy token forensics,
form submissions and service audit rows took the left-most header entry,
which the client writes, and ABAC took the right-most, the load balancer.

The request each reader sees is built by the chain an install runs: the
client, the ALB, nginx on loopback, then ``config.asgi.application``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from django.core.handlers.asgi import ASGIRequest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from astrolift_forms.schema.mutations import _extract_source_ip
from astrolift_identity import abac, sessions
from astrolift_identity.api_tokens import client_ip_from_request
from astrolift_identity.models import DeviceFlowSession
from astrolift_lifecycle import deploy_tokens
from astrolift_services.schema.mutations.helpers import _client_ip as _services_client_ip
from config.proxies import client_address, parse_trusted_proxies
from core.schema.audit import _get_client_ip
from core.tests.test_trusted_proxies_2431 import (  # noqa: F401
    ALB_NODE,
    ALB_SUBNETS,
    NGINX,
    _call,
    behind_alb,
    isolated,
)

CLIENT = "203.0.113.70"
FORGED = "6.6.6.6"


def _request_as_the_app_sees_it(*, peer: str, forwarded_for: str) -> ASGIRequest:
    """The Django request behind an install's ``config.asgi`` proxy middleware."""
    seen: list[ASGIRequest] = []

    async def app(scope, receive, send):
        seen.append(ASGIRequest(scope, None))

    proxied = ProxyHeadersMiddleware(app, trusted_hosts=list(parse_trusted_proxies(ALB_SUBNETS)))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"astro.example"), (b"x-forwarded-for", forwarded_for.encode())],
        "client": (peer, 51234),
        "server": ("10.20.8.9", 8000),
    }
    asyncio.run(proxied(scope, None, None))
    return seen[0]


READERS = {
    "audit": _get_client_ip,
    "sessions": sessions._client_ip,
    "abac": abac.policy_client_ip,
    "api_token": client_ip_from_request,
    "deploy_token": deploy_tokens.client_ip_from_request,
    "forms": _extract_source_ip,
    "services": lambda request: _services_client_ip(
        SimpleNamespace(context=SimpleNamespace(request=request))
    ),
}


@pytest.mark.parametrize("reader", READERS.values(), ids=READERS.keys())
def test_every_reader_sees_the_client_the_balancer_saw_not_the_forged_entry(reader):
    request = _request_as_the_app_sees_it(peer=NGINX, forwarded_for=f"{FORGED}, {CLIENT}, {ALB_NODE}")
    assert request.META["HTTP_X_FORWARDED_FOR"].startswith(FORGED)
    assert reader(request) == CLIENT


@pytest.mark.parametrize("reader", READERS.values(), ids=READERS.keys())
def test_an_untrusted_peer_is_its_own_address_whatever_it_claims(reader):
    request = _request_as_the_app_sees_it(peer="198.51.100.7", forwarded_for=f"{FORGED}, {CLIENT}")
    assert reader(request) == "198.51.100.7"


def test_a_malformed_address_is_none():
    assert client_address(SimpleNamespace(META={"REMOTE_ADDR": "not-an-ip"})) is None
    assert client_address(SimpleNamespace(META={})) is None
    assert client_address(None) is None


@pytest.mark.django_db
def test_a_device_sign_in_records_and_limits_the_real_client(behind_alb):  # noqa: F811
    # POST /api/cli/v1/auth/start: the session's client_ip and the 10/m limit.
    for i in range(10):
        assert (
            _call(
                behind_alb,
                "/api/cli/v1/auth/start",
                method="POST",
                forwarded_for=f"6.6.6.{i}, {CLIENT}, {ALB_NODE}",
                body=b"{}",
            )
            == 200
        )
    assert (
        _call(
            behind_alb,
            "/api/cli/v1/auth/start",
            method="POST",
            forwarded_for=f"6.6.6.99, {CLIENT}, {ALB_NODE}",
            body=b"{}",
        )
        == 429
    )
    assert set(DeviceFlowSession.all_objects.values_list("client_ip", flat=True)) == {CLIENT}
