# ruff: noqa: F811  (pytest fixtures imported from test_run_agent_brains_2432)
"""Unattended agent runs read semantic layers through the agent's own grants (#2449).

A semantic layer is shared with an agent like a team share (``shareBrain``
with ``agentSlug``, viewer only). A run nobody launched (a schedule, a loop, a
webhook, a workflow stage) reads the shared layers its agent also lists, as
the agent: bound at spawn, checked again on every refresh, and revoked on
unshare, archive and agent deletion. Runs a person launches are unchanged.
Builds on the #2432 rig: real Postgres and role bindings, recorded cluster
driver and Temporal client.
"""

from __future__ import annotations

import uuid

import pytest

from astrolift_agents.models import AgentTask
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.tests.test_run_agent_brains_2432 import (  # noqa: F401 (fixtures)
    _agent,
    _as,
    _binding,
    _brain,
    _container_env,
    _env,
    _job_and_secret,
    _published,
    _refresh,
    _run,
    _spawn,
    _sub,
    _task,
    _tool,
    _verify,
    _world,
    audit,
    driver,
    other,
    started,
    world,
)
from astrolift_identity.api_tokens import SCOPE_MCP_READ, SCOPE_MCP_WRITE, SCOPE_READ_APPS
from astrolift_identity.models import ApiToken
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry import brain_apps, brain_runs
from astrolift_registry.models import BrainAgentGrant, BrainRunBinding, BrainTokenKey
from astrolift_registry.schema.brain_types import BrainShareAccess
from astrolift_registry.schema.mutations import RegistryMutation
from astrolift_registry.schema.mutations.brains import ShareBrainInput, UnshareBrainInput
from astrolift_registry.schema.queries import RegistryQuery
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role, make_user

pytestmark = pytest.mark.django_db

ADMIN = [
    Permission.APP_READ,
    Permission.APP_UPDATE,
    Permission.APP_DEPLOY,
    Permission.AGENT_READ,
    Permission.AGENT_DISPATCH,
]


def _admin(world):
    user = make_user(f"adm-{world.org.slug}")
    bind_role(user, permissions=ADMIN, kind="ORG", scope_id=world.org.pk, slug=f"adm-{world.org.slug}")
    return user


def _share(world, admin, brain, agent_slug, access=BrainShareAccess.VIEWER):
    with _as(world, admin) as info:
        return RegistryMutation().share_brain(
            info, input=ShareBrainInput(slug=brain.slug, agent_slug=agent_slug, access=access)
        )


def _unshare(world, admin, brain, agent_slug):
    with _as(world, admin) as info:
        return RegistryMutation().unshare_brain(
            info, input=UnshareBrainInput(slug=brain.slug, agent_slug=agent_slug)
        )


def _unattended(world, agent, *, brains=(), allowed=None, trigger="schedule"):
    """A run nobody launched: dispatched, then stripped of its launcher, as
    a cron tick, a loop refill, a webhook or a workflow stage makes it."""
    task = _task(_run(world, agent))
    AgentTask.objects.filter(pk=task.pk).update(triggered_by_user=None, created_by=None, trigger_kind=trigger)
    agent.brains = list(brains)
    agent.brains_allowed = list(allowed) if allowed is not None else None
    agent.save(update_fields=["brains", "brains_allowed"])
    return AgentTask.objects.get(pk=task.pk)


def _spawned(world, driver, task):
    _spawn(world, task)
    job, secret = _job_and_secret(driver)
    return _container_env(job), secret


def test_an_unattended_run_reads_exactly_the_granted_brains_the_agent_lists(world, started, driver, audit):
    admin = _admin(world)
    agent = _agent(world)
    kb, docs, hr, wiki, dark = (_brain(world, s) for s in ("kb", "docs", "hr", "wiki", "dark"))
    AppEnvironment.objects.filter(registered_app=dark).update(url="")
    for brain in (kb, docs, hr, dark):
        assert _share(world, admin, brain, agent.slug).ok
    # kb is the agent's own, docs in its allow-list; hr is shared but not
    # listed, wiki listed but not shared, dark shared and listed but unserved.
    task = _unattended(world, agent, brains=["kb", "wiki", "dark"], allowed=["docs"])

    env, secret = _spawned(world, driver, task)

    rows = list(BrainRunBinding.objects.filter(agent_task=task).order_by("pk"))
    assert [(row.slug, row.source) for row in rows] == [("kb", "grant"), ("docs", "grant")]
    for slug, brain in (("KB", kb), ("DOCS", docs)):
        claims = _verify(secret["stringData"][f"BRAIN_{slug}_TOKEN"], brain)
        # The agent, never a person.
        assert (claims["sub"], claims["email"]) == (f"agent:{agent.guid}", "")
        assert claims["agent_task"] == str(task.guid)
    assert "BRAIN_HR_TOKEN" not in env and "BRAIN_WIKI_TOKEN" not in env
    issued = [row for row in audit if row.action == "brain.token.issue"]
    assert sorted(row.target_id for row in issued) == ["docs", "kb"]
    assert all(row.actor_user_id is None and row.extra["agent"] == agent.slug for row in issued)
    with _as(world, admin) as info:
        detail = AgentsQuery().agent_task(info, id=str(task.guid))
    assert [(b.slug, b.source) for b in detail.brains] == [("kb", "grant"), ("docs", "grant")]


@pytest.mark.parametrize("trigger", ["webhook", "parent"])
def test_every_unattended_trigger_reads_through_the_grant(world, started, driver, trigger):
    admin = _admin(world)
    agent = _agent(world)
    kb = _brain(world, "kb")
    assert _share(world, admin, kb, agent.slug).ok
    task = _unattended(world, agent, brains=["kb"], trigger=trigger)

    _spawned(world, driver, task)

    assert _binding(task, "kb").source == "grant"


def test_a_launched_run_reads_as_its_launcher_not_the_grant(world, started, driver):
    admin = _admin(world)
    kb = _brain(world, "kb")
    agent = _agent(world, brains=["kb"])
    assert _share(world, admin, kb, agent.slug).ok

    task = _task(_run(world, agent))
    _env, secret = _spawned(world, driver, task)

    assert _binding(task, "kb").source == "default"
    assert _verify(secret["stringData"]["BRAIN_KB_TOKEN"], kb)["sub"] == _sub(world.user)


def test_an_agent_is_shared_only_within_its_organization(world, other, started, driver):
    admin = _admin(world)
    kb = _brain(world, "kb")
    theirs = _agent(other, slug="theirs")

    refused = _share(world, admin, kb, theirs.slug)
    assert (refused.errors[0].code, refused.errors[0].field) == ("NOT_FOUND", "agentSlug")

    # A grant forged across organizations is never read.
    foreign = _brain(other, "foreign")
    mine = _agent(world)
    BrainAgentGrant.objects.create(organization=other.org, brain=foreign, agent=mine)
    task = _unattended(world, mine, brains=["foreign"])
    _spawn(world, task)
    assert not BrainRunBinding.objects.filter(agent_task=task).exists()


def test_an_agent_reads_and_never_deploys(world):
    admin = _admin(world)
    kb = _brain(world, "kb")
    agent = _agent(world)

    deployer = _share(world, admin, kb, agent.slug, access=BrainShareAccess.DEPLOYER)
    assert (deployer.errors[0].code, deployer.errors[0].field) == ("VALIDATION", "access")
    with _as(world, admin) as info:
        both = RegistryMutation().share_brain(
            info,
            input=ShareBrainInput(
                slug=kb.slug,
                team_slug=world.platform.slug,
                agent_slug=agent.slug,
                access=BrainShareAccess.VIEWER,
            ),
        )
        neither = RegistryMutation().unshare_brain(info, input=UnshareBrainInput(slug=kb.slug))
    assert both.errors[0].code == neither.errors[0].code == "VALIDATION"
    assert not BrainAgentGrant.objects.exists()


def test_unsharing_revokes_mid_run_on_the_next_refresh(world, started, driver, audit):
    admin = _admin(world)
    agent = _agent(world)
    kb, docs = _brain(world, "kb"), _brain(world, "docs")
    for brain in (kb, docs):
        assert _share(world, admin, brain, agent.slug).ok
    task = _unattended(world, agent, brains=["kb", "docs"])
    env, _secret = _spawned(world, driver, task)
    key = env["ASTROLIFT_CLUSTER_KEY"]["value"]
    kid = BrainTokenKey.objects.get(run_binding=_binding(task, "docs")).kid
    assert [entry["slug"] for entry in _refresh(task, key).json()["brains"]] == ["kb", "docs"]

    unshared = _unshare(world, admin, docs, agent.slug)

    assert unshared.ok, unshared.errors
    assert kid not in _published()
    body = _refresh(task, key).json()
    assert [entry["slug"] for entry in body["brains"]] == ["kb"]
    assert _verify(body["brains"][0]["token"], kb)["sub"] == f"agent:{agent.guid}"
    [lost] = body["revoked"]
    assert (lost["slug"], lost["reason"]) == ("docs", brain_runs.UNSHARED)
    share_rows = [row for row in audit if row.action in ("brain.share", "brain.unshare")]
    assert [(row.action, row.extra["agent"], row.extra.get("access")) for row in share_rows] == [
        ("brain.share", agent.slug, "viewer"),
        ("brain.share", agent.slug, "viewer"),
        ("brain.unshare", agent.slug, None),
    ]


def test_deleting_the_agent_or_archiving_the_brain_revokes(world, started, driver):
    admin = _admin(world)
    agent = _agent(world)
    kb, docs = _brain(world, "kb"), _brain(world, "docs")
    for brain in (kb, docs):
        assert _share(world, admin, brain, agent.slug).ok
    task = _unattended(world, agent, brains=["kb", "docs"])
    env, _secret = _spawned(world, driver, task)
    key = env["ASTROLIFT_CLUSTER_KEY"]["value"]

    brain_apps.revoke_app(docs.pk)
    assert _binding(task, "docs").revoked_reason == brain_runs.BRAIN_GONE

    kid = BrainTokenKey.objects.get(run_binding=_binding(task, "kb")).kid
    agent.soft_delete()
    assert kid not in _published()  # at once, before any refresh
    body = _refresh(task, key).json()
    assert body["brains"] == []
    assert {(r["slug"], r["reason"]) for r in body["revoked"]} == {
        ("docs", brain_runs.BRAIN_GONE),
        ("kb", brain_runs.UNSHARED),
    }


def test_graphql_and_mcp_list_an_agents_grants(world, other, started):
    admin = _admin(world)
    agent = _agent(world)
    kb = _brain(world, "kb")
    hidden = _brain(world, "hidden", team=world.platform)
    for brain in (kb, hidden):
        assert _share(world, admin, brain, agent.slug).ok

    with _as(world, admin) as info:
        detail = AgentsQuery().agent(info, org_id=str(world.org.guid), slug=agent.slug)
        brain = RegistryQuery().astrolift_brain(info, slug=kb.slug)
    assert [g.brain_slug for g in detail.brain_grants] == ["hidden", "kb"]
    assert [(s.agent, s.app) for s in brain.agent_shares] == [(agent.slug, agent.registered_app.slug)]

    # The launcher reads medops brains only: the platform team's stays hidden.
    with _as(world) as info:
        mine = AgentsQuery().agent(info, org_id=str(world.org.guid), slug=agent.slug)
    assert [g.brain_slug for g in mine.brain_grants] == ["kb"]

    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="mcp",
        token_hash=uuid.uuid4().hex,
        token_last_4="2449",
        scopes=[SCOPE_MCP_READ, SCOPE_MCP_WRITE, SCOPE_READ_APPS],
    )
    got = _tool(world, token, "astrolift_get_agent", agent_slug=agent.slug)["structuredContent"]
    assert [g["slug"] for g in got["run"]["brain_grants"]] == ["kb"]
