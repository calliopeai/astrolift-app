# ruff: noqa: F811  (pytest fixtures imported from test_brain_apps_2243)
"""Standby keys (#2649): the edge verifies brain tokens against a JWKS it
refetches only every few minutes and never for an unknown ``kid``, so a key
minted when a run spawns or an app deploys is refused until the next fetch.
Runs and deploys take a key that has been published on standby instead, and
another key goes on standby in its place. Real Postgres throughout; the run
side is in astrolift_agents/tests/test_run_brain_standby_keys_2649.py."""

from __future__ import annotations

import datetime as dt

import jwt
import pytest
from django.test import Client
from django.utils import timezone

from astrolift_registry import brain_tokens
from astrolift_registry.models import BrainTokenKey
from astrolift_registry.tests.test_brain_apps_2243 import (  # noqa: F401 (fixtures)
    _deploy,
    _env,
    _hosted,
    _materialize,
    _verify,
    world,
)

pytestmark = pytest.mark.django_db


def _standby():
    return list(BrainTokenKey.objects.filter(standby=True).order_by("created_at", "pk"))


def _aged(minutes):
    """Move every standby key ``minutes`` into the past, keeping their order."""
    base = timezone.now() - dt.timedelta(minutes=minutes)
    for offset, key in enumerate(BrainTokenKey.objects.filter(standby=True).order_by("pk")):
        BrainTokenKey.objects.filter(pk=key.pk).update(created_at=base + dt.timedelta(seconds=offset))


def _kid(token):
    return jwt.get_unverified_header(token)["kid"]


def test_the_jwks_publishes_the_install_key_and_the_standby_keys_once():
    first = Client().get("/api/brains/v1/jwks.json")
    assert first.status_code == 200
    kids = {key["kid"] for key in first.json()["keys"]}

    standby = _standby()
    assert len(standby) == brain_tokens.STANDBY_KEYS
    assert {key.kid for key in standby} | {brain_tokens.signing_key().kid} == kids
    assert all("d" not in key for key in first.json()["keys"]), "a private key was published"
    # The edge fetching again tops nothing up.
    assert {key["kid"] for key in Client().get("/api/brains/v1/jwks.json").json()["keys"]} == kids
    assert BrainTokenKey.objects.count() == brain_tokens.STANDBY_KEYS + 1


def test_a_deploy_signs_with_the_oldest_standby_key_and_another_takes_its_place(world, monkeypatch, caplog):
    app, env = _hosted(world)
    brain_tokens.ensure_standby()
    _aged(10)
    oldest, second = _standby()[:2]

    with caplog.at_level("WARNING", logger="astrolift_registry.brain_tokens"):
        token = _materialize(_deploy(app, env), monkeypatch)["BRAIN_TOKEN"]

    assert _kid(token) == oldest.kid
    oldest.refresh_from_db()
    assert not oldest.standby and oldest.binding is not None and oldest.deployment is not None
    assert _verify(token, str(world.medops_app.guid))["sub"] == f"app:{app.guid}"
    assert "may refuse" not in caplog.text  # ten minutes old: every edge holds it
    standby = _standby()
    assert len(standby) == brain_tokens.STANDBY_KEYS and oldest.pk not in {key.pk for key in standby}
    assert _kid(_materialize(_deploy(app, env), monkeypatch)["BRAIN_TOKEN"]) == second.kid


def test_with_nothing_on_standby_a_deploy_still_signs_and_says_the_edge_may_refuse(
    world, monkeypatch, caplog
):
    app, env = _hosted(world)
    assert not _standby()

    with caplog.at_level("WARNING", logger="astrolift_registry.brain_tokens"):
        token = _materialize(_deploy(app, env), monkeypatch)["BRAIN_TOKEN"]

    assert _verify(token, str(world.medops_app.guid))["sub"] == f"app:{app.guid}"
    assert _kid(token) in caplog.text and "may refuse" in caplog.text
    assert len(_standby()) == brain_tokens.STANDBY_KEYS


def test_a_redeploy_still_drops_the_key_before_last_and_never_reuses_one(world, monkeypatch):
    app, env = _hosted(world)
    brain_tokens.ensure_standby()
    _aged(10)
    audience = str(world.medops_app.guid)

    tokens = [_materialize(_deploy(app, env), monkeypatch)["BRAIN_TOKEN"] for _ in range(3)]

    kids = [_kid(token) for token in tokens]
    assert len(set(kids)) == 3
    with pytest.raises(jwt.InvalidKeyError):
        _verify(tokens[0], audience)
    assert not BrainTokenKey.objects.filter(kid=kids[0]).exists()
    _verify(tokens[1], audience)
    _verify(tokens[2], audience)
    assert not BrainTokenKey.objects.filter(kid__in=kids, standby=True).exists()


def test_the_install_key_never_is_or_retires_a_standby_key():
    brain_tokens.ensure_standby()
    _aged(60 * 24 * 40)
    install = brain_tokens.signing_key()
    assert not install.standby and install.pk not in {key.pk for key in _standby()}

    later = timezone.now() + dt.timedelta(days=31)
    rotated = brain_tokens.signing_key(now=later)

    assert rotated.kid != install.kid and not rotated.standby
    standby = _standby()
    assert len(standby) == brain_tokens.STANDBY_KEYS
    assert all(key.retired_at is None for key in standby)
    assert {key.kid for key in standby} <= {key["kid"] for key in brain_tokens.jwks(now=later)["keys"]}
