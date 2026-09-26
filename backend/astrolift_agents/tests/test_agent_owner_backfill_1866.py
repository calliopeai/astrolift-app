"""Migration 0037 backfills spec and box owners where one is provable (#1866).

Runs the migration's own function against the historical model state, as the
#1919 backfill test does, rather than a full ``migrate`` round trip.
"""

from __future__ import annotations

import importlib

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from astrolift_agents.models import AgentBox, AgentEnvironmentSpec
from astrolift_identity.models import Project, Team
from astrolift_registry.models import RegisteredApp, Workload
from core.tests.utils.scope_world import ScopeWorld

pytestmark = pytest.mark.django_db

NAME = "0037_backfill_agent_spec_and_box_owner"


def _backfill():
    migration = importlib.import_module(f"astrolift_agents.migrations.{NAME}")
    historical = MigrationExecutor(connection).loader.project_state(("astrolift_agents", NAME)).apps
    migration.backfill_owners(historical, None)


def _agent(app, slug):
    return Workload.objects.create(registered_app=app, name=slug, slug=slug, kind=Workload.Kind.AGENT)


def _spec(org, slug):
    return AgentEnvironmentSpec.objects.create(organization=org, name=slug, slug=slug, agent_type="claude")


def test_a_spec_with_one_canonical_agent_takes_that_agents_project():
    world = ScopeWorld("bf-1")
    _agent(world.medops_app, "triage")
    spec = _spec(world.org, "triage")

    _backfill()

    spec.refresh_from_db()
    assert (spec.team_id, spec.project_id) == (world.medops.pk, world.medops_project.pk)


def test_a_slug_two_apps_share_stays_org_shared():
    world = ScopeWorld("bf-2")
    _agent(world.medops_app, "triage")
    _agent(world.platform_app, "triage")
    spec = _spec(world.org, "triage")

    _backfill()

    spec.refresh_from_db()
    assert spec.team_id is None and spec.project_id is None


def test_a_spec_with_no_agent_or_a_dead_one_stays_org_shared():
    world = ScopeWorld("bf-3")
    dead = _agent(world.medops_app, "gone")
    dead.soft_delete()
    lonely = _spec(world.org, "lonely")
    gone = _spec(world.org, "gone")

    _backfill()

    for spec in (lonely, gone):
        spec.refresh_from_db()
        assert spec.team_id is None and spec.project_id is None


def test_an_app_without_a_live_project_gives_its_team_only():
    world = ScopeWorld("bf-4")
    RegisteredApp.objects.filter(pk=world.medops_app.pk).update(project=None)
    _agent(world.medops_app, "triage")
    spec = _spec(world.org, "triage")

    _backfill()

    spec.refresh_from_db()
    assert (spec.team_id, spec.project_id) == (world.medops.pk, None)


def test_an_agent_in_another_org_is_not_an_owner():
    home = ScopeWorld("bf-5a")
    other = ScopeWorld("bf-5b")
    _agent(other.medops_app, "triage")
    spec = _spec(home.org, "triage")

    _backfill()

    spec.refresh_from_db()
    assert spec.team_id is None


def test_a_spec_only_box_takes_its_specs_owner_and_an_agent_box_records_nothing():
    world = ScopeWorld("bf-6")
    agent = _agent(world.medops_app, "triage")
    spec = _spec(world.org, "triage")
    spec_box = AgentBox.objects.create(organization=world.org, name="s", slug="s", environment_spec=spec)
    agent_box = AgentBox.objects.create(
        organization=world.org, name="a", slug="a", agent_definition=agent, environment_spec=spec
    )

    _backfill()

    spec_box.refresh_from_db()
    agent_box.refresh_from_db()
    assert (spec_box.team_id, spec_box.project_id) == (world.medops.pk, world.medops_project.pk)
    assert agent_box.team_id is None and agent_box.project_id is None


def test_reruns_are_a_no_op_and_existing_owners_are_kept():
    world = ScopeWorld("bf-7")
    _agent(world.medops_app, "triage")
    spec = _spec(world.org, "triage")
    AgentEnvironmentSpec.objects.filter(pk=spec.pk).update(
        team=world.platform, project=world.platform_project
    )

    _backfill()
    _backfill()

    spec.refresh_from_db()
    assert (spec.team_id, spec.project_id) == (world.platform.pk, world.platform_project.pk)
    assert Team.objects.filter(organization=world.org).count() == 2
    assert Project.objects.filter(organization=world.org).count() == 2
