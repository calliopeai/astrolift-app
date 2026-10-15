"""Delivery channels on the GraphQL surface and in the schedule wrapper (#2244).

Org admins (``org.update``) configure channels; workflow editors
(``workflow.update``) pick them per Workflow. The credential is write-only.
On the worker, a fire posts its settled run to the channels, a failed run
notifies its owner, and a delivery that breaks never fails the run.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import httpx
import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core import mail

from astrolift_lifecycle.services.secrets import read_org_secret
from astrolift_workflows.schema.mutations import WorkflowsMutation
from astrolift_workflows.schema.queries import WorkflowsQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from workflows.models import DeliveryChannel, Workflow, WorkflowDefinition, WorkflowDelivery, WorkflowStage

pytestmark = pytest.mark.django_db

SLACK_URL = "https://hooks.slack.com/services/T000/B000/" + "s3cr3t" * 4


@pytest.fixture
def org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Delivery Org", slug=f"dlv-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def user():
    return get_user_model().objects.create_user(
        username=f"dlv-{uuid.uuid4().hex[:6]}", email="owner@example.com", password="pw"
    )


def _scheduled_workflow(org, owner):
    definition = WorkflowDefinition.objects.create(
        name="Digest",
        slug=f"digest-{uuid.uuid4().hex[:8]}",
        organization=org,
        model_label="",
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        states=[],
        transitions=[],
        is_enabled=True,
    )
    WorkflowStage.objects.create(
        definition=definition,
        slug=f"{definition.slug}-0",
        order=0,
        kind=WorkflowStage.StageKind.CHECKPOINT,
        role="snapshot",
    )
    return Workflow.objects.create(
        organization=org,
        definition=definition,
        name="Morning digest",
        slug=f"digest-{uuid.uuid4().hex[:6]}",
        trigger_kind=Workflow.TriggerKind.SCHEDULE,
        schedule_cron="0 7 * * *",
        created_by=owner,
    )


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=None))


def _as(org, user):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id))


# ---- configuration: org admins ----------------------------------------------


def test_an_org_admin_adds_a_channel_whose_credential_is_write_only(org, user, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    permission_resolver.grant(Permission.WORKFLOW_READ)
    with _as(org, user):
        res = WorkflowsMutation().create_workflow_delivery_channel(
            _info(user), name="Ops Slack", kind="slack_webhook", credential=SLACK_URL
        )
        listed = WorkflowsQuery().workflow_delivery_channels(_info(user))

    assert res.ok, res.errors
    channel = DeliveryChannel.objects.get(guid=res.channel.guid)
    assert read_org_secret(org, channel.secret_key) == SLACK_URL
    assert [(c.name, c.kind, c.has_credential) for c in listed] == [("Ops Slack", "slack_webhook", True)]
    assert SLACK_URL not in repr(res) and SLACK_URL not in repr(listed)
    assert all(
        SLACK_URL not in str(v)
        for v in DeliveryChannel.objects.filter(pk=channel.pk).values().first().values()
    )


def test_a_workflow_editor_cannot_configure_channels(org, user, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    with _as(org, user):
        res = WorkflowsMutation().create_workflow_delivery_channel(
            _info(user), name="Ops Slack", kind="slack_webhook", credential=SLACK_URL
        )
    assert not res.ok
    assert not DeliveryChannel.objects.filter(organization=org).exists()


def test_a_channel_url_off_the_vendor_hosts_is_refused(org, user, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    with _as(org, user):
        res = WorkflowsMutation().create_workflow_delivery_channel(
            _info(user), name="Sneaky", kind="teams_webhook", credential="https://169.254.169.254/latest"
        )
    assert not res.ok
    assert not DeliveryChannel.objects.filter(organization=org).exists()


def test_removing_a_channel_removes_its_credential(org, user, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    with _as(org, user):
        created = WorkflowsMutation().create_workflow_delivery_channel(
            _info(user), name="Ops Slack", kind="slack_webhook", credential=SLACK_URL
        )
        channel = DeliveryChannel.objects.get(guid=created.channel.guid)
        res = WorkflowsMutation().delete_workflow_delivery_channel(_info(user), channel_id=channel.guid)
    assert res.ok
    assert read_org_secret(org, channel.secret_key) is None
    assert not DeliveryChannel.objects.filter(pk=channel.pk).exists()


# ---- selection: workflow editors -------------------------------------------


def test_a_workflow_editor_picks_the_channels_of_their_own_org(org, user, permission_resolver):
    from astrolift_identity.models import Organization

    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    permission_resolver.grant(Permission.WORKFLOW_READ)
    wf = _scheduled_workflow(org, user)
    mine = DeliveryChannel.objects.create(
        organization=org, name="Ops mail", kind="email", email_recipients=["a@x.io"]
    )
    other_org = Organization.objects.create(name="Other", slug=f"other-{uuid.uuid4().hex[:6]}")
    theirs = DeliveryChannel.objects.create(
        organization=other_org, name="Theirs", kind="email", email_recipients=["b@x.io"]
    )

    with _as(org, user):
        foreign = WorkflowsMutation().set_workflow_delivery_channels(
            _info(user), workflow_id=wf.guid, channel_ids=[mine.guid, theirs.guid]
        )
        picked = WorkflowsMutation().set_workflow_delivery_channels(
            _info(user), workflow_id=wf.guid, channel_ids=[mine.guid]
        )
        selection = WorkflowsQuery().workflow_delivery_selection(_info(user), workflow_id=wf.guid)

    assert not foreign.ok
    assert picked.ok, picked.errors
    assert selection.channel_guids == [str(mine.guid)]
    assert list(wf.delivery_channels.all()) == [mine]


def test_picking_channels_needs_workflow_update(org, user, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    wf = _scheduled_workflow(org, user)
    channel = DeliveryChannel.objects.create(
        organization=org, name="Ops mail", kind="email", email_recipients=["a@x.io"]
    )
    with _as(org, user):
        res = WorkflowsMutation().set_workflow_delivery_channels(
            _info(user), workflow_id=wf.guid, channel_ids=[channel.guid]
        )
    assert not res.ok
    assert not wf.delivery_channels.exists()


# ---- the schedule wrapper on a real worker ----------------------------------


def _worker(env, *, activities):
    from temporalio.worker import Worker
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
    from temporalio.workflow import NondeterminismError

    from astrolift_workflows.workflows import (
        ConfiguredWorkflowScheduleWorkflow,
        WorkflowDefinitionRunWorkflow,
    )

    return Worker(
        env.client,
        task_queue="astrolift-test",
        workflows=[ConfiguredWorkflowScheduleWorkflow, WorkflowDefinitionRunWorkflow],
        activities=activities,
        workflow_runner=SandboxedWorkflowRunner(),
        workflow_failure_exception_types=[NondeterminismError],
    )


def _base_activities():
    from astrolift_workflows.activities.configured_workflow_schedule_fire import (
        create_scheduled_workflow_run,
        record_scheduled_workflow_start,
    )
    from astrolift_workflows.activities.workflow_stage_activities import (
        get_workflow_stages,
        mark_workflow_run,
        snapshot_checkpoint,
    )

    return [
        create_scheduled_workflow_run,
        record_scheduled_workflow_start,
        get_workflow_stages,
        snapshot_checkpoint,
        mark_workflow_run,
    ]


async def _fire(env, wf):
    from astrolift_workflows.workflows import ConfiguredWorkflowScheduleWorkflow

    handle = await env.client.start_workflow(
        ConfiguredWorkflowScheduleWorkflow.run,
        {"workflow_guid": str(wf.guid), "organization_id": wf.organization_id},
        id=f"workflow-{wf.guid}-run-{uuid.uuid4().hex[:6]}",
        task_queue="astrolift-test",
    )
    return await asyncio.wait_for(handle.result(), timeout=120)


@pytest.fixture
def slack_posts(monkeypatch):
    posts = []

    def fake_post(url, *, json=None, headers=None, timeout=None):
        posts.append({"url": url, "json": json})
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr("workflows.delivery.httpx.post", fake_post)
    return posts


@pytest.mark.django_db(transaction=True)
async def test_a_scheduled_run_posts_its_result_to_its_channel(org, user, temporal_env, slack_posts):
    from astrolift_lifecycle.services.secrets import write_org_secret
    from astrolift_workflows.activities.workflow_result_delivery import deliver_workflow_run_result

    @sync_to_async
    def arrange():
        wf = _scheduled_workflow(org, user)
        channel = DeliveryChannel.objects.create(organization=org, name="Ops", kind="slack_webhook")
        write_org_secret(org, channel.secret_key, SLACK_URL)
        wf.delivery_channels.set([channel])
        return wf

    wf = await arrange()
    async with _worker(temporal_env, activities=[*_base_activities(), deliver_workflow_run_result]):
        result = await _fire(temporal_env, wf)

    assert result["result"]["ok"] is True
    assert [p["url"] for p in slack_posts] == [SLACK_URL]
    assert slack_posts[0]["json"]["text"] == "Morning digest: completed"
    statuses = await sync_to_async(list)(
        WorkflowDelivery.objects.filter(workflow=wf).values_list("status", "run_outcome")
    )
    assert statuses == [("delivered", "completed")]


@pytest.mark.django_db(transaction=True)
async def test_a_broken_delivery_never_fails_the_run(org, user, temporal_env):
    from temporalio import activity
    from temporalio.exceptions import ApplicationError

    from astrolift_operations.models import WorkflowRun

    attempts = []

    @activity.defn(name="astrolift.configured_workflow.deliver_run_result")
    async def broken_delivery(request: dict) -> dict:
        attempts.append(request)
        raise ApplicationError("channel exploded", non_retryable=True)

    wf = await sync_to_async(_scheduled_workflow)(org, user)
    async with _worker(temporal_env, activities=[*_base_activities(), broken_delivery]):
        result = await _fire(temporal_env, wf)

    assert result["result"]["ok"] is True
    assert [a["outcome"] for a in attempts] == ["completed"]
    status = await sync_to_async(lambda: WorkflowRun.objects.get(pk=int(result["workflow_run_id"])).status)()
    assert status == "completed"


@pytest.mark.django_db(transaction=True)
async def test_a_failed_scheduled_run_notifies_its_owner(org, user, temporal_env, monkeypatch):
    from temporalio import activity
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import ApplicationError

    from astrolift_workflows.activities.workflow_result_delivery import deliver_workflow_run_result

    monkeypatch.setattr("astrolift_operations.notification_email.notifications_email_enabled", lambda: True)

    @activity.defn(name="astrolift.workflow_stage.get_workflow_stages")
    async def unplannable(params: dict) -> dict:
        raise ApplicationError("definition could not be planned", non_retryable=True)

    activities = [a for a in _base_activities() if a.__name__ != "get_workflow_stages"]
    wf = await sync_to_async(_scheduled_workflow)(org, user)
    async with _worker(temporal_env, activities=[*activities, unplannable, deliver_workflow_run_result]):
        with pytest.raises(WorkflowFailureError):
            await _fire(temporal_env, wf)

    assert [m.to for m in mail.outbox] == [["owner@example.com"]]
    assert mail.outbox[0].subject == "Morning digest: failed"
    records = await sync_to_async(list)(
        WorkflowDelivery.objects.filter(workflow=wf).values_list("purpose", "status", "run_outcome")
    )
    assert records == [("owner_notice", "delivered", "failed")]
