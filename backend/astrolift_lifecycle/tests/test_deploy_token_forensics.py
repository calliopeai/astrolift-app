"""Tests for #425 deploy-token forensic stamping + Constance grace.

Three behaviours pinned here:

* ``touch_deploy_token`` writes the IP / UA / timestamp triple and
  refuses malformed IPs (so a CDN sneaking a bad ``X-Forwarded-For``
  doesn't crash the middleware).
* ``rotation_grace_seconds_from_constance`` honours the operator-set
  value when in-band and falls back / clamps when not.
* The deploy-token middleware sets ``request._deploy_token`` and
  stamps the forensic columns on a successful auth; returns 401 on
  a bad bearer; noops on no-bearer or wrong-prefix bearers.
"""

from __future__ import annotations

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from astrolift_lifecycle.deploy_tokens import (
    DEFAULT_GRACE_PERIOD_SECONDS,
    MAX_ROTATION_GRACE_SECONDS,
    MIN_ROTATION_GRACE_SECONDS,
    client_ip_from_request,
    issue_token,
    rotation_grace_seconds_from_constance,
    touch_deploy_token,
    user_agent_from_request,
)
from astrolift_lifecycle.middleware import (
    DeployTokenAuthMiddleware,
    get_request_deploy_token,
)
from astrolift_lifecycle.models import DeployToken

pytestmark = pytest.mark.django_db


# ---- touch_deploy_token ---------------------------------------------


def test_touch_writes_ip_ua_and_timestamp(app):
    row, _ = issue_token(app=app, name="ci")
    assert row.last_used_at is None
    assert row.last_used_ip is None
    assert row.last_used_agent == ""

    touch_deploy_token(row, ip="203.0.113.7", user_agent="GitHub-Actions/2.317")

    fresh = DeployToken.objects.get(pk=row.pk)
    assert fresh.last_used_at is not None
    assert fresh.last_used_ip == "203.0.113.7"
    assert fresh.last_used_agent == "GitHub-Actions/2.317"


def test_touch_rejects_malformed_ip_to_avoid_db_error(app):
    """``GenericIPAddressField`` raises on a non-IP string. The helper
    coerces invalid IPs to ``None`` so a spoofed XFF doesn't break the
    request path."""
    row, _ = issue_token(app=app, name="ci")
    touch_deploy_token(row, ip="not-an-ip", user_agent="x")
    fresh = DeployToken.objects.get(pk=row.pk)
    assert fresh.last_used_ip is None


def test_touch_truncates_oversize_user_agent(app):
    row, _ = issue_token(app=app, name="ci")
    long_ua = "X" * 1024
    touch_deploy_token(row, ip="198.51.100.1", user_agent=long_ua)
    fresh = DeployToken.objects.get(pk=row.pk)
    assert len(fresh.last_used_agent) == 512


def test_touch_ipv6_accepted(app):
    row, _ = issue_token(app=app, name="ci")
    touch_deploy_token(row, ip="2001:db8::1", user_agent="ipv6-client")
    fresh = DeployToken.objects.get(pk=row.pk)
    assert fresh.last_used_ip == "2001:db8::1"


# ---- client_ip_from_request / user_agent_from_request ----------------


def test_client_ip_prefers_leftmost_xff_hop():
    rf = RequestFactory()
    request = rf.get(
        "/",
        HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1, 10.0.0.2",
        REMOTE_ADDR="10.0.0.99",
    )
    assert client_ip_from_request(request) == "203.0.113.7"


def test_client_ip_falls_back_to_remote_addr_when_no_xff():
    rf = RequestFactory()
    request = rf.get("/", REMOTE_ADDR="198.51.100.42")
    assert client_ip_from_request(request) == "198.51.100.42"


def test_client_ip_returns_none_for_unparseable():
    rf = RequestFactory()
    request = rf.get("/", REMOTE_ADDR="garbage")
    assert client_ip_from_request(request) is None


def test_user_agent_truncated_to_column_width():
    rf = RequestFactory()
    request = rf.get("/", HTTP_USER_AGENT="X" * 600)
    assert len(user_agent_from_request(request)) == 512


# ---- rotation_grace_seconds_from_constance --------------------------


def test_grace_default_when_constance_unset():
    """Test bootstrap may not have the entry persisted yet — falls
    back to the in-process default."""
    assert rotation_grace_seconds_from_constance() == DEFAULT_GRACE_PERIOD_SECONDS


def test_grace_honours_operator_set_value(monkeypatch):
    from constance.test import override_config

    with override_config(DEPLOY_TOKEN_ROTATION_GRACE_SECONDS=2 * 60 * 60):
        assert rotation_grace_seconds_from_constance() == 2 * 60 * 60


def test_grace_clamps_below_minimum():
    from constance.test import override_config

    with override_config(DEPLOY_TOKEN_ROTATION_GRACE_SECONDS=5):
        assert rotation_grace_seconds_from_constance() == MIN_ROTATION_GRACE_SECONDS


def test_grace_clamps_above_maximum():
    from constance.test import override_config

    with override_config(DEPLOY_TOKEN_ROTATION_GRACE_SECONDS=999 * 24 * 60 * 60):
        assert rotation_grace_seconds_from_constance() == MAX_ROTATION_GRACE_SECONDS


# ---- DeployTokenAuthMiddleware --------------------------------------


def _ok(_request) -> HttpResponse:
    return HttpResponse("ok")


def test_middleware_noop_when_no_authorization_header(app):
    rf = RequestFactory()
    request = rf.get("/")
    mw = DeployTokenAuthMiddleware(_ok)
    resp = mw(request)
    assert resp.status_code == 200
    assert get_request_deploy_token(request) is None


def test_middleware_noop_when_bearer_is_wrong_prefix(app):
    rf = RequestFactory()
    request = rf.get(
        "/",
        HTTP_AUTHORIZATION="Bearer alft_at_someothertokentype",
    )
    mw = DeployTokenAuthMiddleware(_ok)
    resp = mw(request)
    assert resp.status_code == 200
    assert get_request_deploy_token(request) is None


def test_middleware_401_on_bad_deploy_token(app):
    rf = RequestFactory()
    request = rf.get(
        "/",
        HTTP_AUTHORIZATION="Bearer alft_dt_definitely-not-a-real-token",
    )
    mw = DeployTokenAuthMiddleware(_ok)
    resp = mw(request)
    assert resp.status_code == 401


def test_middleware_stamps_forensic_columns_on_success(app):
    row, plaintext = issue_token(app=app, name="ci")
    rf = RequestFactory()
    request = rf.get(
        "/",
        HTTP_AUTHORIZATION=f"Bearer {plaintext}",
        HTTP_X_FORWARDED_FOR="203.0.113.7",
        HTTP_USER_AGENT="GitHub-Actions/2.317",
    )
    mw = DeployTokenAuthMiddleware(_ok)
    resp = mw(request)
    assert resp.status_code == 200

    attached = get_request_deploy_token(request)
    assert attached is not None
    assert attached.pk == row.pk

    fresh = DeployToken.objects.get(pk=row.pk)
    assert fresh.last_used_ip == "203.0.113.7"
    assert fresh.last_used_agent == "GitHub-Actions/2.317"
    assert fresh.last_used_at is not None


def test_middleware_does_not_swap_request_user(app):
    """Deploy tokens are app-scoped credentials — they must not
    replace the session-resolved user. The middleware only attaches
    ``request._deploy_token`` so the deploy view can authorize on
    the token's own permission catalog."""
    from django.contrib.auth.models import AnonymousUser

    _row, plaintext = issue_token(app=app, name="ci")
    rf = RequestFactory()
    request = rf.get("/", HTTP_AUTHORIZATION=f"Bearer {plaintext}")
    request.user = AnonymousUser()

    mw = DeployTokenAuthMiddleware(_ok)
    mw(request)
    assert isinstance(request.user, AnonymousUser)
