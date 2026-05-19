"""Tests for the mobile universal-link / app-link well-known endpoints (#541).

Both endpoints MUST:

* Return 200 to an unauthenticated client (iOS / Android verifiers fetch
  these as anonymous; auth gating breaks deep-link verification).
* Serve ``Content-Type: application/json``.
* Carry a public cache header (max-age=3600).
* Reflect ``MOBILE_TEAM_ID`` / ``MOBILE_ANDROID_SHA256`` from settings
  so ops can patch the manifest via env var without a code deploy.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client, override_settings


@pytest.fixture
def anon_client():
    """Fresh Django test client with no logged-in user."""
    return Client()


# ---- apple-app-site-association --------------------------------------


def test_aasa_returns_200_anonymously(anon_client):
    resp = anon_client.get("/.well-known/apple-app-site-association")
    assert resp.status_code == 200


def test_aasa_content_type_is_application_json(anon_client):
    resp = anon_client.get("/.well-known/apple-app-site-association")
    # Apple's verifier rejects anything that isn't application/json.
    # Allow optional charset suffix.
    assert resp["Content-Type"].split(";")[0].strip() == "application/json"


def test_aasa_body_is_valid_json_with_applinks(anon_client):
    resp = anon_client.get("/.well-known/apple-app-site-association")
    payload = json.loads(resp.content)
    assert "applinks" in payload
    assert payload["applinks"]["apps"] == []
    details = payload["applinks"]["details"]
    assert isinstance(details, list) and len(details) == 1
    entry = details[0]
    assert "appID" in entry
    assert "paths" in entry
    assert isinstance(entry["paths"], list) and len(entry["paths"]) > 0
    # The handful of paths the mobile app claims today must all be
    # advertised; if one drops out the OS won't intercept that route.
    for required in ("/approve/*", "/sessions/*", "/connect"):
        assert required in entry["paths"]


def test_aasa_appid_uses_settings_team_id(anon_client):
    with override_settings(MOBILE_TEAM_ID="ABCDE12345"):
        resp = anon_client.get("/.well-known/apple-app-site-association")
    payload = json.loads(resp.content)
    assert payload["applinks"]["details"][0]["appID"] == "ABCDE12345.dev.astrolift.mobile"


def test_aasa_carries_public_cache_header(anon_client):
    resp = anon_client.get("/.well-known/apple-app-site-association")
    cache = resp["Cache-Control"]
    assert "public" in cache
    assert "max-age=3600" in cache


def test_aasa_rejects_post(anon_client):
    resp = anon_client.post("/.well-known/apple-app-site-association")
    assert resp.status_code == 405


# ---- assetlinks.json -------------------------------------------------


def test_assetlinks_returns_200_anonymously(anon_client):
    resp = anon_client.get("/.well-known/assetlinks.json")
    assert resp.status_code == 200


def test_assetlinks_content_type_is_application_json(anon_client):
    resp = anon_client.get("/.well-known/assetlinks.json")
    assert resp["Content-Type"].split(";")[0].strip() == "application/json"


def test_assetlinks_body_is_valid_json_array(anon_client):
    resp = anon_client.get("/.well-known/assetlinks.json")
    payload = json.loads(resp.content)
    assert isinstance(payload, list) and len(payload) == 1
    entry = payload[0]
    assert entry["relation"] == ["delegate_permission/common.handle_all_urls"]
    target = entry["target"]
    assert target["namespace"] == "android_app"
    assert target["package_name"] == "dev.astrolift.mobile"
    assert isinstance(target["sha256_cert_fingerprints"], list)
    assert len(target["sha256_cert_fingerprints"]) >= 1


def test_assetlinks_supports_multiple_fingerprints(anon_client):
    # Production installs ship both an upload key + a Play app-signing
    # key — the setting accepts a comma-separated list.
    with override_settings(MOBILE_ANDROID_SHA256="AA:BB:CC, DD:EE:FF"):
        resp = anon_client.get("/.well-known/assetlinks.json")
    payload = json.loads(resp.content)
    fps = payload[0]["target"]["sha256_cert_fingerprints"]
    assert fps == ["AA:BB:CC", "DD:EE:FF"]


def test_assetlinks_carries_public_cache_header(anon_client):
    resp = anon_client.get("/.well-known/assetlinks.json")
    cache = resp["Cache-Control"]
    assert "public" in cache
    assert "max-age=3600" in cache


def test_assetlinks_rejects_post(anon_client):
    resp = anon_client.post("/.well-known/assetlinks.json")
    assert resp.status_code == 405
