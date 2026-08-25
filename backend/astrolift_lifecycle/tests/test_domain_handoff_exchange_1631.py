"""The handoff exchange endpoint (#1631).

Every test that matters is a refusal, and they all check the **same**
response. An endpoint that distinguishes "no such token" from "expired"
from "wrong audience" tells an attacker which of those they got wrong, so
the uniformity is the security property rather than laziness.

The other property worth naming: this never 500s on a bad token. A 500 on
the auth path is itself an oracle, and the shapes that would raise --
malformed JSON, a missing field, a non-string token -- are exactly what a
prober sends.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from django.test import Client
from django.utils import timezone

from astrolift_lifecycle.models import CustomDomain
from astrolift_lifecycle.models.domain_handoff import DomainSessionHandoff
from core.domain_handoff import mint_domain_handoff

pytestmark = pytest.mark.django_db

URL = "/api/edge/v1/domain-handoff/exchange/"
SUBJECT = "auth0|deadbeef"


@pytest.fixture
def domain(app):
    return CustomDomain.objects.create(
        registered_app=app,
        hostname="shop.customer.com",
        is_active=True,
    )


def _post(body) -> tuple[int, dict]:
    raw = body if isinstance(body, (str, bytes)) else json.dumps(body)
    response = Client().post(URL, data=raw, content_type="application/json")
    try:
        return response.status_code, json.loads(response.content)
    except ValueError:
        return response.status_code, {}


def test_a_valid_token_returns_the_verified_identity(domain):
    token = mint_domain_handoff(custom_domain=domain, subject=SUBJECT, email="a@b.com")

    status, body = _post({"token": token, "hostname": "shop.customer.com"})

    assert status == 200
    assert body["ok"] is True
    assert body["subject"] == SUBJECT
    assert body["email"] == "a@b.com"


def test_a_get_is_not_allowed():
    """The exchange spends a token. A GET would let a prefetch or a crawler
    burn a user's grant."""
    assert Client().get(URL).status_code == 405


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"",
        "[]",
        '{"hostname": "shop.customer.com"}',
        '{"token": "x"}',
        '{"token": 42, "hostname": "shop.customer.com"}',
        '{"token": "   ", "hostname": "shop.customer.com"}',
        '{"token": "x", "hostname": ""}',
    ],
)
def test_every_malformed_request_is_refused_without_raising(body):
    """A 500 on the auth path is an oracle in itself, and these are the
    shapes a prober sends."""
    status, out = _post(body)

    assert status == 401
    assert out == {"ok": False, "error": "handoff_refused"}


def test_an_unknown_token_is_refused(domain):
    status, body = _post({"token": "nope", "hostname": "shop.customer.com"})

    assert status == 401
    assert body["error"] == "handoff_refused"


def test_a_replayed_token_is_refused(domain):
    token = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)
    _post({"token": token, "hostname": "shop.customer.com"})

    status, _ = _post({"token": token, "hostname": "shop.customer.com"})

    assert status == 401


def test_an_expired_token_is_refused(domain):
    token = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)
    DomainSessionHandoff.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))

    status, _ = _post({"token": token, "hostname": "shop.customer.com"})

    assert status == 401


def test_the_wrong_audience_is_refused_and_does_not_burn_the_token(domain):
    """Ordering, at the HTTP layer. If the exchange burned the token before
    checking the audience, anyone who could replay one at the wrong host
    would lock a user out of their own login."""
    token = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)

    assert _post({"token": token, "hostname": "attacker.example"})[0] == 401
    assert _post({"token": token, "hostname": "shop.customer.com"})[0] == 200


def test_every_refusal_is_byte_identical(domain):
    """The security property. Distinguishing them tells an attacker which
    of expiry, audience, replay or existence they got wrong."""
    token = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)
    expired = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)
    DomainSessionHandoff.objects.filter(token_hash__isnull=False).exclude(
        pk=DomainSessionHandoff.objects.order_by("pk").first().pk
    ).update(expires_at=timezone.now() - dt.timedelta(seconds=1))

    bodies = [
        _post({"token": "unknown", "hostname": "shop.customer.com"})[1],
        _post({"token": token, "hostname": "elsewhere.example"})[1],
        _post({"token": expired, "hostname": "shop.customer.com"})[1],
    ]

    assert all(b == bodies[0] for b in bodies)


def test_no_identity_leaks_on_a_refusal(domain):
    """The refusal body must not confirm who a token was for."""
    token = mint_domain_handoff(custom_domain=domain, subject=SUBJECT, email="a@b.com")

    _, body = _post({"token": token, "hostname": "elsewhere.example"})

    serialised = json.dumps(body)
    assert SUBJECT not in serialised
    assert "a@b.com" not in serialised
    assert "customer.com" not in serialised


def test_the_token_is_never_written_to_the_log(domain, caplog):
    """A log line outlives the token's 60 seconds, and the token is a
    credential right up until it is spent."""
    token = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)

    with caplog.at_level("DEBUG"):
        _post({"token": token, "hostname": "elsewhere.example"})

    assert token not in caplog.text


def test_a_deactivated_domain_is_refused(domain):
    """Deactivating a domain must stop it minting sessions immediately, not
    at the end of the TTL of tokens already issued."""
    token = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)
    CustomDomain.objects.filter(pk=domain.pk).update(is_active=False)

    assert _post({"token": token, "hostname": "shop.customer.com"})[0] == 401
