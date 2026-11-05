# ruff: noqa: F811  (pytest fixtures imported from test_run_agent_brains_2432)
"""A run signs its brain tokens with a key the edge already holds (#2649).

The edge refetches the brain JWKS only every few minutes and never for an
unknown ``kid``, so a key minted at spawn left a workflow stage's first brain
calls refused ("Jwks doesn't have key to match kid or alg from Jwt"). A run's
binding now takes the oldest standby key, published before the run started.
Builds on the #2432 and #2449 rigs: real Postgres and role bindings, recorded
cluster driver and Temporal client.
"""

from __future__ import annotations

import datetime as dt

import jwt
import pytest
from django.utils import timezone

from astrolift_agents.models import AgentTask
from astrolift_agents.tests.test_run_agent_brains_2432 import (  # noqa: F401 (fixtures)
    _agent,
    _binding,
    _brain,
    _env,
    _published,
    _refresh,
    _running,
    _verify,
    driver,
    started,
    world,
)
from astrolift_agents.tests.test_unattended_run_brains_2449 import _admin, _share, _spawned, _unattended
from astrolift_registry import brain_runs, brain_tokens
from astrolift_registry.models import BrainTokenKey

pytestmark = pytest.mark.django_db


def _standby():
    return list(BrainTokenKey.objects.filter(standby=True).order_by("created_at", "pk"))


def _on_standby_for(minutes):
    """Standby keys published ``minutes`` ago, as the edge's last fetch saw them."""
    brain_tokens.ensure_standby()
    base = timezone.now() - dt.timedelta(minutes=minutes)
    for offset, key in enumerate(BrainTokenKey.objects.filter(standby=True).order_by("pk")):
        BrainTokenKey.objects.filter(pk=key.pk).update(created_at=base + dt.timedelta(seconds=offset))
    return _standby()


def _stage(world, brains, *, admin=None, slug="triage"):
    """A workflow stage of an agent the brains are shared with: the factory's case."""
    admin = admin or _admin(world)
    agent = _agent(world, slug=slug)
    for brain in brains:
        assert _share(world, admin, brain, agent.slug).ok
    return _unattended(world, agent, brains=[brain.slug for brain in brains], trigger="parent")


def test_a_workflow_stage_signs_with_a_key_published_before_it_started(world, started, driver):
    kb = _brain(world, "kb")
    standby = _on_standby_for(10)
    held_by_the_edge = _published()
    task = _stage(world, [kb])

    _env_vars, secret = _spawned(world, driver, task)

    token = secret["stringData"]["BRAIN_KB_TOKEN"]
    kid = jwt.get_unverified_header(token)["kid"]
    assert kid == standby[0].kid and kid in held_by_the_edge
    key = BrainTokenKey.objects.get(kid=kid)
    assert not key.standby and key.run_binding_id == _binding(task, "kb").pk
    assert _verify(token, kb)["agent_task"] == str(task.guid)
    assert len(_standby()) == brain_tokens.STANDBY_KEYS


def test_each_brain_and_each_run_takes_its_own_key(world, started, driver):
    kb, docs = _brain(world, "kb"), _brain(world, "docs")
    standby = _on_standby_for(10)
    admin = _admin(world)
    first = _stage(world, [kb, docs], admin=admin, slug="groom")
    second = _stage(world, [kb], admin=admin, slug="review")

    _env_vars, first_secret = _spawned(world, driver, first)
    _env_vars, second_secret = _spawned(world, driver, second)

    kids = [
        jwt.get_unverified_header(secret["stringData"][name])["kid"]
        for secret, name in (
            (first_secret, "BRAIN_KB_TOKEN"),
            (first_secret, "BRAIN_DOCS_TOKEN"),
            (second_secret, "BRAIN_KB_TOKEN"),
        )
    ]
    assert kids == [key.kid for key in standby[:3]]


def test_a_refresh_signs_with_the_same_key(world, started, driver):
    kb = _brain(world, "kb")
    _on_standby_for(10)
    task = _stage(world, [kb])
    env, secret = _spawned(world, driver, task)
    kid = jwt.get_unverified_header(secret["stringData"]["BRAIN_KB_TOKEN"])["kid"]

    [entry] = _refresh(task, env["ASTROLIFT_CLUSTER_KEY"]["value"]).json()["brains"]

    assert jwt.get_unverified_header(entry["token"])["kid"] == kid
    assert len(_standby()) == brain_tokens.STANDBY_KEYS


def test_a_taken_key_leaves_with_its_run_and_never_returns_to_standby(world, started, driver):
    kb = _brain(world, "kb")
    _on_standby_for(10)
    task = _stage(world, [kb])
    _spawned(world, driver, task)
    kid = BrainTokenKey.objects.get(run_binding=_binding(task, "kb")).kid
    assert kid in _published()

    _running(task)
    task.transition_to(AgentTask.Status.COMPLETED)

    assert _binding(task, "kb").revoked_reason == brain_runs.ENDED
    assert kid not in _published()
    assert not BrainTokenKey.objects.filter(kid=kid).exists()
    assert kid not in {key.kid for key in _standby()}
