"""Dispatcher registration must never become an unauthenticated bootstrap."""

from __future__ import annotations

import json

import pytest
from django.test import RequestFactory, override_settings

from astrolift_dispatch.views import register
from astrolift_identity.models import Organization

pytestmark = pytest.mark.django_db


def _request(*, token: str = ""):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return RequestFactory().post(
        "/api/dispatch/v1/register/",
        data=json.dumps(
            {
                "name": "primary",
                "endpoint": "https://dispatch.example",
                "org_slug": "acme",
            }
        ),
        content_type="application/json",
        **headers,
    )


@override_settings(DISPATCHER_BOOTSTRAP_TOKEN="")
def test_register_fails_closed_when_bootstrap_token_is_unconfigured():
    response = register(_request())

    assert response.status_code == 503
    assert b"not configured" in response.content


@override_settings(DISPATCHER_BOOTSTRAP_TOKEN="bootstrap-secret")
def test_register_rejects_wrong_bootstrap_token():
    response = register(_request(token="wrong"))

    assert response.status_code == 401


@override_settings(DISPATCHER_BOOTSTRAP_TOKEN="bootstrap-secret")
def test_register_accepts_configured_bootstrap_token():
    Organization.objects.create(name="Acme", slug="acme")

    response = register(_request(token="bootstrap-secret"))

    assert response.status_code == 201
    body = json.loads(response.content)
    assert body["dispatcher_id"]
    assert body["api_key"]
