"""Triggered workflow instances and runs record the org that owns them (#1984).

``trigger_workflow_instance`` (SCM push routing, the app-bound SCM receiver,
the inbound webhook's stage-less fallback, direct launches) created its
``WorkflowInstance`` and the ``WorkflowRun`` behind it with no org, and so did
the definition schedule. Since #1965 an org-less row belongs to no tenant, so
the org that owns the trigger could not read, transition or control its own
runs. An org-less run also resolved its agents and picked its dispatcher
across every org.

Each path now records the owner org: the webhook's (the app's, for an
app-bound webhook that predates the org column), else the definition's.
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import Client
from graphql import GraphQLError

from astrolift_agents.models import WorkflowWebhook
from astrolift_agents.services import workflow_triggers
from astrolift_agents.services.workflow_triggers import (
    ScmEvent,
    create_webhook_workflow_trigger,
    route_scm_push_to_workflow_webhooks,
    trigger_workflow_instance,
)
from astrolift_identity.models import Organization, Role, RoleBinding
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.schema.queries import WorkflowsQuery
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, make_info
from workflows.models import WorkflowDefinition, WorkflowInstance
from workflows.schema.mutations import Mutation as LegacyMutation
from workflows.schema.queries import Query as LegacyQuery

pytestmark = pytest.mark.django_db

User = get_user_model()
RESYNC = importlib.import_module("astrolift_identity.migrations.0034_resync_system_roles_stock_catalogue")

_STATES = [
    {"name": "pending", "label": "Pending", "is_initial": True, "is_final": False},
    {"name": "done", "label": "Done", "is_initial": False, "is_final": True},
]


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture(autouse=True)
def _plain_http(settings):
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "DebugToolbar" not in m]
    settings.DEBUG = False
    settings.ASTROLIFT_TEMPORAL_ENABLED = False


@pytest.fixture
def world():
    w = ScopeWorld("tr1984")
    w.beta = Organization.objects.create(name="Beta", slug="beta-tr1984")
    RESYNC.upsert_system_roles(django_apps, None)
    return w


def _definition(org, slug):
    return WorkflowDefinition.objects.create(
        organization=org,
        name=slug,
        slug=slug,
        model_label="workflows.workflowdefinition",
        states=_STATES,
        transitions=[{"from_state": "pending", "to_state": "done", "label": "Complete"}],
        is_enabled=True,
    )


def _admin(org, name):
    user = User.objects.create(username=f"{name}-tr1984", email=f"{name}-tr1984@acme.test")
    role = Role.objects.get(slug="org_admin", is_system=True, organization=None)
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.pk)
    return user


def _as(org, user):
    return tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk))


def _assert_owned_by(world, instance, owner):
    """The instance and its run carry ``owner``; that org reads the run and
    reads and moves the instance, and the other org gets not-found."""
    instance.refresh_from_db()
    assert instance.organization_id == owner.pk
    run = WorkflowRun.objects.get(workflow_id=instance.temporal_workflow_id)
    assert run.organization_id == owner.pk

    other = world.beta if owner == world.org else world.org
    stranger = _admin(other, f"stranger-{instance.pk}")
    with _as(other, stranger):
        info = make_info(stranger)
        assert LegacyQuery().workflow_instance(info, str(instance.pk)) is None
        assert WorkflowsQuery().workflow_execution(info, str(run.pk)) is None
        with pytest.raises(GraphQLError, match="not found"):
            LegacyMutation().transition_workflow(info, str(instance.pk), "done")

    admin = _admin(owner, f"owner-{instance.pk}")
    with _as(owner, admin):
        info = make_info(admin)
        assert LegacyQuery().workflow_instance(info, str(instance.pk)) == instance
        assert WorkflowsQuery().workflow_execution(info, str(run.pk)) is not None
        assert LegacyMutation().transition_workflow(info, str(instance.pk), "done").ok


def test_a_direct_launch_belongs_to_the_definitions_org(world):
    instance = trigger_workflow_instance(_definition(world.org, "direct-flow"), {"k": "v"})

    _assert_owned_by(world, instance, world.org)


def test_a_template_launch_belongs_to_the_org_that_triggered_it(world):
    """A platform template has no org, so the trigger's org owns the run."""
    template = _definition(None, "template-flow-tr1984")

    instance = trigger_workflow_instance(template, {}, trigger_kind="webhook", organization_id=world.org.pk)

    _assert_owned_by(world, instance, world.org)


def test_the_inbound_webhook_fallback_records_the_webhooks_org(world):
    """A stage-less definition goes through the legacy instance path; a
    template-backed webhook still belongs to the webhook's org."""
    template = _definition(None, "hooked-template-tr1984")
    hook = create_webhook_workflow_trigger(template, organization=world.org)

    response = Client().post(
        hook["endpoint"],
        data=json.dumps({"event": "ping"}),
        content_type="application/json",
        HTTP_X_ASTROLIFT_SIGNATURE=hook["signing_secret"],
    )

    assert response.status_code == 200, response.content
    _assert_owned_by(world, WorkflowInstance.objects.get(workflow=template), world.org)


def test_scm_push_routing_records_the_webhooks_org(world):
    definition = _definition(world.beta, "beta-push-flow")
    WorkflowWebhook.objects.create(
        workflow_definition=definition,
        organization=world.beta,
        slug="beta-push-hook-tr1984",
        secret_hash="x",
        input_mapping={},
        enabled=True,
    )

    (instance,) = route_scm_push_to_workflow_webhooks(
        ScmEvent(
            organization_id=world.beta.pk,
            repo_full_name="beta/repo",
            branch="main",
            head_sha="f" * 40,
            event_kind="push",
        )
    )

    _assert_owned_by(world, instance, world.beta)


def test_an_app_bound_scm_webhook_without_an_org_belongs_to_its_app(world):
    from astrolift_scm.webhook_views import _dispatch_workflow_webhooks

    definition = _definition(world.org, "app-push-flow")
    WorkflowWebhook.objects.create(
        workflow_definition=definition,
        registered_app=world.medops_app,
        slug="app-push-hook-tr1984",
        secret_hash="x",
        input_mapping={},
        enabled=True,
    )

    assert _dispatch_workflow_webhooks("push", world.medops_app, {"ref": "refs/heads/main"}) == 1

    _assert_owned_by(world, WorkflowInstance.objects.get(workflow=definition), world.org)


def test_a_definition_schedule_run_belongs_to_the_definitions_org(world, monkeypatch):
    created = []

    async def _create_schedule(schedule_id, schedule):
        created.append(schedule)

    async def _client():
        return SimpleNamespace(create_schedule=_create_schedule)

    monkeypatch.setattr("astrolift_workflows.client._get_client_async", _client)
    definition = _definition(world.org, "scheduled-flow")

    workflow_triggers._create_temporal_schedule(
        schedule_id="astrolift-sched-tr1984",
        definition=definition,
        cron_expression="0 9 * * *",
        timezone_name="UTC",
        input_template={},
        enabled=True,
    )

    assert len(created) == 1
    run = WorkflowRun.objects.get(workflow_definition=definition)
    assert run.organization_id == world.org.pk
    admin = _admin(world.org, "sched-owner")
    with _as(world.org, admin):
        assert WorkflowsQuery().workflow_execution(make_info(admin), str(run.pk)) is not None
