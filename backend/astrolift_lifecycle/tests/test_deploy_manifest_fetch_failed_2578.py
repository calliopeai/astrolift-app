"""A deploy whose manifest cannot be read, with nothing stored to deploy, fails (#2578).

#1553 refused that deploy by raising from workflow code, past the handler that
marks a deployment failed, which catches activity errors only. The deployment
stayed ``pending`` with no reason, ``app show`` had nothing to say and
``app deploy --wait`` timed out. The ``fetch_failed`` event that held the reason
was recorded at ``info``.

The stored manifest stands in for the repo's. An app registered with one
(``astro app register --manifest-raw``) whose workloads were never created
from it gets them at deploy and deploys it. When nothing can stand in, the
reason says that a resync from the repo is required.

The workflow tests run the real ``DeployAppWorkflow`` on the Temporal test
server with the real resync and ``mark_failed`` activities, against Postgres;
only the source host's reply is stubbed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer, Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import NondeterminismError

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.types import deployment_status_reason
from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_operations.models import Event
from astrolift_registry.services import manifest_sync
from astrolift_scm.models import SourceConnection
from astrolift_workflows.activities.app_lifecycle import (
    _resync_manifest_for_deploy_sync,
    mark_failed,
    resync_manifest_for_deploy,
)
from astrolift_workflows.inputs import Actor, DeployAppInput
from astrolift_workflows.tests.test_deploy_workflows_sandboxed_1758 import fake_activities
from astrolift_workflows.workflows.deploy_app import DeployAppWorkflow

TASK_QUEUE = "astrolift-test"
NOT_FOUND = "'astrolift.toml' not found on 'main' of 'example/app'"
LEGACY = (
    Path(__file__).parents[2]
    / "astrolift_workflows"
    / "tests"
    / "fixtures"
    / "legacy-deploy-app-resync-refusal-raises.json"
)

_STORED_TOML = """\
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080
"""

_UNPARSEABLE_TOML = """\
name = "hello"

[[workloads]]
name = "web"
kind = "not-a-kind"
"""

NOTHING_STORED = "there is no stored manifest to deploy instead, so a resync from the repo is required"


@pytest.fixture
def unreadable_repo(monkeypatch):
    """The source host answers 404 for the manifest, as it did in the issue.

    The repo itself is visible, so the probe that explains an invisible repo
    (#2600) is stubbed to say so. Without the stub it decrypts the test's PAT,
    which was never given a credential, and the deploy fails with InvalidToken.
    """
    monkeypatch.setattr(manifest_sync, "_default_fetch", lambda connection, repo, path, ref: None)
    from astrolift_scm.providers import github

    monkeypatch.setattr(github, "repo_visible", lambda connection, repo_full_name: True)


def _source(app) -> None:
    SourceConnection.objects.create(
        organization=app.organization,
        kind=SourceConnection.Kind.GITHUB_PAT,
        display_name="Example PAT",
        account_login="example",
    )
    app.source_repo = "example/app"
    app.default_branch = "main"
    app.save()


def _sourced_deploy(app, env, *, stored_manifest: str = "") -> Deployment:
    _source(app)
    if stored_manifest:
        normalized = normalize(parse_raw(stored_manifest), defaults=NormalizationDefaults())
        persist_manifest(app, normalized, raw_text=stored_manifest)
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
    )


def _stored_without_workloads(app, text: str) -> None:
    """What registration leaves when it keeps an inline manifest but creates
    no workloads from it."""
    app.manifest_raw = text
    app.manifest_bootstrap_status = "parse_failed"
    app.manifest_bootstrap_error = "ValueError"
    app.save()


def _live_workloads(app) -> list[str]:
    return sorted(app.workloads.filter(deleted_at__isnull=True).values_list("slug", flat=True))


@activity.defn(name="validate_deployment_identity_origin")
async def _admitted(input: DeployAppInput) -> None:
    return None


async def _run_deploy(temporal_env, deployment: Deployment, activities: list):
    worker = Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[DeployAppWorkflow],
        activities=activities,
        workflow_runner=SandboxedWorkflowRunner(),
        workflow_failure_exception_types=[NondeterminismError],
    )
    async with worker:
        handle = await temporal_env.client.start_workflow(
            DeployAppWorkflow.run,
            DeployAppInput(
                registered_app_id=deployment.registered_app_id,
                app_environment_id=deployment.app_environment_id,
                deployment_id=deployment.pk,
                image_tags={},
                trigger_kind="manual",
                actor=Actor(kind="system"),
            ),
            id=f"deploy-2578-{deployment.pk}",
            task_queue=TASK_QUEUE,
        )
        result = await asyncio.wait_for(handle.result(), timeout=120)
        history = await handle.fetch_history()
    return result, history


def _scheduled(history: WorkflowHistory) -> list[str]:
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    ]


async def _replay(history: WorkflowHistory) -> None:
    await Replayer(workflows=[DeployAppWorkflow], workflow_runner=SandboxedWorkflowRunner()).replay_workflow(
        history
    )


@pytest.mark.django_db(transaction=True)
async def test_an_unreadable_manifest_with_nothing_stored_fails_the_deploy_and_says_why(
    app, env, temporal_env, unreadable_repo
):
    deployment = await sync_to_async(_sourced_deploy)(app, env)

    result, history = await _run_deploy(
        temporal_env, deployment, [_admitted, resync_manifest_for_deploy, mark_failed]
    )

    assert result.ok is False
    assert NOT_FOUND in result.message
    assert _scheduled(history) == [
        "validate_deployment_identity_origin",
        "astrolift.app.resync_manifest_for_deploy",
        "astrolift.deploy.mark_failed",
    ]
    row = await sync_to_async(Deployment.objects.get)(pk=deployment.pk)
    assert row.status == Deployment.Status.FAILED
    assert row.failed_at is not None
    assert row.manifest_resync_status == "fetch_failed"
    assert row.manifest_resync_error == NOT_FOUND
    assert row.aborted_reason.endswith(f"{NOT_FOUND}; {NOTHING_STORED}")
    assert await sync_to_async(deployment_status_reason)(row) == row.aborted_reason
    await _replay(history)


@pytest.mark.django_db(transaction=True)
async def test_an_unreadable_manifest_still_deploys_the_stored_workloads(
    app, env, temporal_env, unreadable_repo
):
    """An app with workloads stored at registration deploys them, as before."""
    deployment = await sync_to_async(_sourced_deploy)(app, env, stored_manifest=_STORED_TOML)
    calls: list[str] = []
    fakes = [
        a for a in fake_activities(calls) if a.__name__ not in ("resync_manifest_for_deploy", "mark_failed")
    ]

    result, history = await _run_deploy(
        temporal_env, deployment, [*fakes, resync_manifest_for_deploy, mark_failed]
    )

    assert result.ok is True, result.message
    assert "astrolift.deploy.mark_failed" not in _scheduled(history)
    assert {"render_manifests", "apply_manifests", "mark_running"} <= set(calls)
    row = await sync_to_async(Deployment.objects.select_related("registered_app").get)(pk=deployment.pk)
    assert row.manifest_resync_status == "fetch_failed"
    assert row.manifest_resync_error == NOT_FOUND
    assert row.aborted_reason == ""
    assert row.registered_app.manifest_raw == _STORED_TOML


@pytest.mark.django_db(transaction=True)
async def test_an_unreadable_manifest_deploys_the_stored_one_whose_workloads_were_never_created(
    app, env, temporal_env, unreadable_repo
):
    """The render reads the stored manifest, so it deploys, and the app gets
    the workloads registration would have created from it."""
    await sync_to_async(_stored_without_workloads)(app, _STORED_TOML)
    deployment = await sync_to_async(_sourced_deploy)(app, env)
    calls: list[str] = []
    fakes = [
        a for a in fake_activities(calls) if a.__name__ not in ("resync_manifest_for_deploy", "mark_failed")
    ]

    result, history = await _run_deploy(
        temporal_env, deployment, [*fakes, resync_manifest_for_deploy, mark_failed]
    )

    assert result.ok is True, result.message
    assert "astrolift.deploy.mark_failed" not in _scheduled(history)
    assert {"render_manifests", "apply_manifests", "mark_running"} <= set(calls)
    row = await sync_to_async(Deployment.objects.select_related("registered_app").get)(pk=deployment.pk)
    assert row.manifest_resync_status == "fetch_failed"
    assert row.manifest_resync_error == NOT_FOUND
    assert row.aborted_reason == ""
    stored = row.registered_app
    assert stored.manifest_raw == _STORED_TOML
    assert await sync_to_async(_live_workloads)(stored) == ["web"]
    assert (stored.manifest_bootstrap_status, stored.manifest_bootstrap_error) == ("applied", "")
    await _replay(history)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("stored", "why"),
    [
        (_UNPARSEABLE_TOML, "the stored manifest does not parse ("),
        ('name = "hello"\n', "the stored manifest declares no workloads"),
    ],
)
def test_a_stored_manifest_that_cannot_stand_in_says_a_resync_is_required(
    app, env, unreadable_repo, stored, why
):
    _stored_without_workloads(app, stored)
    deployment = _sourced_deploy(app, env)

    outcome = _resync_manifest_for_deploy_sync(deployment.pk)

    assert (outcome["status"], outcome["workload_count"]) == ("fetch_failed", 0)
    assert outcome["error"].startswith(f"{NOT_FOUND}; {why}")
    assert outcome["error"].endswith(", so a resync from the repo is required")
    assert _live_workloads(app) == []
    deployment.refresh_from_db()
    assert deployment.manifest_resync_error == NOT_FOUND


@pytest.mark.django_db
def test_a_fetch_failed_resync_is_recorded_as_a_warning(app, unreadable_repo):
    _source(app)

    result = manifest_sync.resync_app_manifest_from_repo(app)

    assert result.status == "fetch_failed"
    events = Event.objects.filter(event_type="app.manifest_resync.fetch_failed", registered_app=app)
    assert list(events.values_list("severity", flat=True)) == [Event.Severity.WARN]


async def test_a_deploy_refused_before_the_fix_still_replays():
    """Captured from the workflow at 95af20c4 on the Temporal test server: the
    resync refused with no workloads and the run failed with no further
    activity. A worker on this code must replay it as it ran."""
    history = WorkflowHistory.from_json("legacy-deploy-app-resync-refusal", LEGACY.read_text())
    assert _scheduled(history) == [
        "validate_deployment_identity_origin",
        "astrolift.app.resync_manifest_for_deploy",
    ]
    assert history.events[-1].event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED

    await _replay(history)
