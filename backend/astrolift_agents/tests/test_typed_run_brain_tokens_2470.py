# ruff: noqa: F811  (pytest fixtures imported from test_run_agent_brains_2432)
"""Agent runs on a brain that issues typed read tokens (#2470).

A run a person launches reads such a brain with the person's typed token:
their live teams in the brain's organization within the team of the
credential that launched the run, recorded on the run's binding. A run nobody
launched reads it as the typed agent, its Workload GUID and no teams. Brains
that do not issue typed tokens keep the legacy run tokens. Builds on the #2432
rig: real Postgres and role bindings, recorded cluster driver and Temporal client.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model

from astrolift_agents.schema.mutations import AgentsMutation, RunAstroliftAgentInput
from astrolift_agents.tests.test_run_agent_brains_2432 import (  # noqa: F401 (fixtures)
    _agent,
    _as,
    _binding,
    _brain,
    _env,
    _job_and_secret,
    _launched,
    _refresh,
    _run,
    _spawn,
    _sub,
    _task,
    _verify,
    audit,
    driver,
    started,
    world,
)
from astrolift_agents.tests.test_unattended_run_brains_2449 import _admin, _share, _spawned, _unattended
from astrolift_identity.api_tokens import SCOPE_READ_APPS, SCOPE_WRITE_APPS
from astrolift_identity.models import ApiToken, Member
from astrolift_registry.brain_tokens import TYPED_CLAIMS
from astrolift_registry.models import BrainRunBinding
from astrolift_registry.tests.test_brain_typed_tokens_2470 import typed

pytestmark = pytest.mark.django_db

LEGACY_RUN_CLAIMS = {"iss", "aud", "sub", "email", "scope", "iat", "exp", "jti", "agent_task"}


def _member_of_both_teams(world):
    Member.objects.create(user=world.user, scope_kind=Member.ScopeKind.ORG, scope_id=world.org.pk)
    for team in (world.medops, world.platform):
        Member.objects.create(user=world.user, scope_kind=Member.ScopeKind.TEAM, scope_id=team.pk)
    return sorted(str(team.guid) for team in (world.medops, world.platform))


def _operator(world):
    return get_user_model().objects.create(
        username=f"op-{world.org.slug}", email=f"op-{world.org.slug}@astrolift.test", is_superuser=True
    )


def test_a_launched_run_reads_a_typed_brain_with_the_launchers_typed_token(world, started, driver, audit):
    teams = _member_of_both_teams(world)
    kb = _brain(world, "kb")
    docs = _brain(world, "docs")
    typed(kb, _operator(world))
    task = _task(_run(world, _agent(world, brains=["kb"]), ["docs"]))
    row = _binding(task, "kb")
    assert (row.launch_ceiling, row.launch_team_guid) == (BrainRunBinding.LaunchCeiling.ORGANIZATION, None)

    _spawn(world, task)

    _job, secret = _job_and_secret(driver)
    claims = _verify(secret["stringData"]["BRAIN_KB_TOKEN"], kb)
    assert (claims["contract"], claims["sub_kind"], claims["org"], claims["teams"]) == (
        "1",
        "user",
        str(world.org.guid),
        teams,
    )
    assert (claims["sub"], claims["email"], claims["agent_task"]) == (
        _sub(world.user),
        world.user.email,
        str(task.guid),
    )
    assert set(claims) - TYPED_CLAIMS == LEGACY_RUN_CLAIMS
    # A brain that does not issue typed tokens keeps the legacy run token.
    assert set(_verify(secret["stringData"]["BRAIN_DOCS_TOKEN"], docs)) == LEGACY_RUN_CLAIMS
    issued = {entry.target_id: entry.extra for entry in audit if entry.action == "brain.token.issue"}
    assert issued["kb"]["contract"] == "1" and "contract" not in issued["docs"]


def test_a_refresh_issues_the_typed_token_again(world, started, driver):
    teams = _member_of_both_teams(world)
    kb = _brain(world, "kb")
    typed(kb, _operator(world))
    task, key = _launched(world, driver, brains=["kb"])

    body = _refresh(task, key).json()

    claims = _verify(body["brains"][0]["token"], kb)
    assert (claims["sub_kind"], claims["teams"], claims["agent_task"]) == ("user", teams, str(task.guid))


def test_a_run_launched_with_a_team_token_carries_that_team_only(world, started, driver):
    _member_of_both_teams(world)
    kb = _brain(world, "kb")
    typed(kb, _operator(world))
    agent = _agent(world, brains=["kb"])
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops,
        name="launch",
        token_hash=uuid.uuid4().hex,
        token_last_4="2470",
        scopes=[SCOPE_READ_APPS, SCOPE_WRITE_APPS],
    )
    with _as(world, token=token) as info:
        task = _task(
            AgentsMutation().run_astrolift_agent(info, input=RunAstroliftAgentInput(agent_slug=agent.slug))
        )
    row = _binding(task, "kb")
    assert (row.launch_ceiling, row.launch_team_guid) == (
        BrainRunBinding.LaunchCeiling.TEAM,
        world.medops.guid,
    )

    _spawn(world, task)

    _job, secret = _job_and_secret(driver)
    assert _verify(secret["stringData"]["BRAIN_KB_TOKEN"], kb)["teams"] == [str(world.medops.guid)]


def test_a_binding_from_before_launch_ceilings_carries_no_teams(world, started, driver):
    _member_of_both_teams(world)
    kb = _brain(world, "kb")
    typed(kb, _operator(world))
    task, key = _launched(world, driver, brains=["kb"])
    BrainRunBinding.objects.filter(agent_task=task).update(launch_ceiling="")

    claims = _verify(_refresh(task, key).json()["brains"][0]["token"], kb)

    assert (claims["sub_kind"], claims["email"], claims["teams"]) == ("user", world.user.email, [])


def test_an_unattended_run_reads_a_typed_brain_as_the_typed_agent(world, started, driver):
    _member_of_both_teams(world)
    admin = _admin(world)
    agent = _agent(world)
    kb = _brain(world, "kb")
    wiki = _brain(world, "wiki")
    for brain in (kb, wiki):
        assert _share(world, admin, brain, agent.slug).ok
    typed(kb, _operator(world))
    task = _unattended(world, agent, brains=["kb", "wiki"])

    _env_vars, secret = _spawned(world, driver, task)

    claims = _verify(secret["stringData"]["BRAIN_KB_TOKEN"], kb)
    assert {
        name: claims[name] for name in ("contract", "sub_kind", "org", "teams", "agent", "sub", "email")
    } == {
        "contract": "1",
        "sub_kind": "agent",
        "org": str(world.org.guid),
        "teams": [],
        "agent": str(agent.guid),
        "sub": f"agent:{agent.guid}",
        "email": "",
    }
    assert "app" not in claims and set(claims) - TYPED_CLAIMS == LEGACY_RUN_CLAIMS
    assert set(_verify(secret["stringData"]["BRAIN_WIKI_TOKEN"], wiki)) == LEGACY_RUN_CLAIMS
    assert _binding(task, "kb").launch_ceiling == ""
