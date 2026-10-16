"""Detaching a Cloud Operations bundle takes its log view grant back from the app (#2099).

A GCP identity's view grants are written one view at a time and were only ever
added, so an app detached from a bundle went on reading its logs. The identity
reconcile now withdraws the grant on every bundle view the app was bound to and
no live binding reads, and the detach starts that reconcile once it commits.

Real app, environment, service and attachment rows; the real Cloud Operations
and GCP workload identity drivers over the provider tests' fake clients.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest
from _sdk.managed_service import ServiceHandle
from gcp.identity_wi import GCPWIConfig, GCPWorkloadIdentityDriver, service_account_email_for
from temporalio import activity
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import NondeterminismError

from astrolift_clusters.models import ProviderPlugin
from astrolift_graphql import GUID
from astrolift_lifecycle.models import AppEnvironment
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from astrolift_services.schema.mutations import DetachProjectManagedServiceInput, ServicesMutation
from astrolift_workflows.activities.managed_service_lifecycle import _sync_binding_rows
from astrolift_workflows.activities.workload_identity import _ensure_workload_identity_sync
from astrolift_workflows.inputs import Actor, ReconcileWorkloadIdentityInput
from astrolift_workflows.workflows.reconcile_workload_identity import ReconcileWorkloadIdentityWorkflow
from core.app_deploy import workload_identity_role_name
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from providers.tests.gcp.test_bundle_log_view_2099 import FakeIAMWithViews
from providers.tests.gcp.test_identity_wi import FakeIAMClient, _AlreadyExists
from providers.tests.gcp.test_managed_observability_cloud_operations import (
    FakeCloudOperationsClient,
    _driver,
    _spec,
)

_VIEW_ACCESSOR = "roles/logging.viewAccessor"


@pytest.fixture
def world(app, env, cluster, monkeypatch):
    gcp, _ = ProviderPlugin.objects.get_or_create(
        slug="gcp", defaults={"name": "GCP", "capabilities_manifest": {}, "config_schema": {}}
    )
    cluster.provider_plugin = gcp
    cluster.provider_config = {"project_id": "acme-prod"}
    cluster.save()
    logging_client = FakeCloudOperationsClient()
    project_iam = FakeIAMWithViews()
    _AlreadyExists.__name__ = "AlreadyExists"
    identity = GCPWorkloadIdentityDriver(
        config=GCPWIConfig(
            project_id="acme-prod", iam_client=FakeIAMClient(), project_iam_client=project_iam
        ),
    )
    resolved = []

    def resolve(target, capability):
        resolved.append((target.pk, capability))
        return identity

    def binding(svc):
        driver, _, _ = _driver(logging=logging_client)
        return driver.binding(
            ServiceHandle(svc.backend_ref, managed_service_id=str(svc.guid)), dict(svc.config)
        )

    monkeypatch.setattr(
        "astrolift_workflows.activities.capability_deprovision._resolve_capability_driver", resolve
    )
    monkeypatch.setattr(
        "astrolift_workflows.activities.managed_service_lifecycle._managed_binding_for", binding
    )
    member = f"serviceAccount:{service_account_email_for(workload_identity_role_name(app), 'acme-prod')}"
    return SimpleNamespace(
        app=app,
        env=env,
        cluster=cluster,
        logging=logging_client,
        iam=project_iam,
        member=member,
        resolved=resolved,
    )


def _provision(world, svc):
    """Provision the bundle as the platform does and record its binding rows."""
    driver, _, _ = _driver(logging=world.logging)
    result = driver.provision(
        replace(_spec({"deletion_protection": False, **svc.config}), managed_service_id=str(svc.guid))
    )
    assert result.ok, result.message
    svc.backend_ref = result.handle
    svc.save(update_fields=["backend_ref"])
    _sync_binding_rows(svc)
    bucket = svc.bindings.get(env_key="GCP_LOG_BUCKET").env_value_ref
    return f"{bucket}/views/_AllLogs"


def _shared_bundle(world, *environments, name="shared-ops"):
    svc = ManagedService.objects.create(
        project=world.app.project,
        tenant_cluster=world.cluster,
        kind=ManagedService.Kind.OBSERVABILITY,
        variant="cloud_operations",
        name=name,
        status=ManagedService.Status.ACTIVE,
    )
    attachments = [
        ManagedServiceAttachment.objects.create(managed_service=svc, app_environment=environment)
        for environment in environments
    ]
    return svc, _provision(world, svc), attachments


def _readers(world, view):
    policy = world.iam.views.get(view, {"bindings": []})
    return {
        member
        for binding in policy["bindings"]
        if binding["role"] == _VIEW_ACCESSOR and not binding.get("condition")
        for member in binding["members"]
    }


@pytest.mark.django_db
def test_detaching_a_project_bundle_takes_its_view_back_from_the_app(world):
    _, view, (attachment,) = _shared_bundle(world, world.env)
    _ensure_workload_identity_sync(world.app.pk, world.env.pk)
    assert _readers(world, view) == {world.member}

    attachment.soft_delete()
    result = _ensure_workload_identity_sync(world.app.pk, world.env.pk)

    assert result["skipped"] is True
    assert result["withdrawn_log_views"] == [view]
    assert _readers(world, view) == set()


@pytest.mark.django_db
def test_a_view_another_environment_still_reads_is_kept_until_it_detaches_too(world):
    preview = AppEnvironment.objects.create(
        registered_app=world.app, tenant_cluster=world.cluster, name="preview", k8s_namespace="hello-preview"
    )
    _, view, (prod_attachment, preview_attachment) = _shared_bundle(world, world.env, preview)
    _ensure_workload_identity_sync(world.app.pk, world.env.pk)

    preview_attachment.soft_delete()
    after_preview = _ensure_workload_identity_sync(world.app.pk, preview.pk)
    assert after_preview["withdrawn_log_views"] == []
    assert _readers(world, view) == {world.member}

    prod_attachment.soft_delete()
    after_prod = _ensure_workload_identity_sync(world.app.pk, world.env.pk)
    assert after_prod["withdrawn_log_views"] == [view]
    assert _readers(world, view) == set()


@pytest.mark.django_db
def test_a_bundle_switched_to_write_only_takes_its_view_back_while_still_bound(world):
    svc = ManagedService.objects.create(
        registered_app=world.app,
        app_environment=world.env,
        kind=ManagedService.Kind.OBSERVABILITY,
        variant="cloud_operations",
        name="ops",
        status=ManagedService.Status.ACTIVE,
    )
    view = _provision(world, svc)
    _ensure_workload_identity_sync(world.app.pk, world.env.pk)
    assert _readers(world, view) == {world.member}

    svc.config = {"access_mode": "write"}
    svc.save(update_fields=["config"])
    result = _ensure_workload_identity_sync(world.app.pk, world.env.pk)

    assert "skipped" not in result
    assert result["withdrawn_log_views"] == [view]
    assert _readers(world, view) == set()


@pytest.mark.django_db
def test_a_deleted_bundles_view_is_withdrawn_and_a_vanished_view_is_no_error(world):
    svc = ManagedService.objects.create(
        registered_app=world.app,
        app_environment=world.env,
        kind=ManagedService.Kind.OBSERVABILITY,
        variant="cloud_operations",
        name="ops",
        status=ManagedService.Status.ACTIVE,
    )
    view = _provision(world, svc)
    _ensure_workload_identity_sync(world.app.pk, world.env.pk)

    # Deleted with its data retained: the bucket and its view outlive the row.
    svc.soft_delete()
    assert _ensure_workload_identity_sync(world.app.pk, world.env.pk)["withdrawn_log_views"] == [view]
    assert _readers(world, view) == set()

    # Deleted with its data: the view is gone, and there is nothing to withdraw.
    world.iam.view_errors[view] = 404
    assert _ensure_workload_identity_sync(world.app.pk, world.env.pk)["withdrawn_log_views"] == []


@pytest.mark.django_db
def test_withdrawal_leaves_every_other_reader_of_the_view(world):
    _, view, (attachment,) = _shared_bundle(world, world.env)
    _ensure_workload_identity_sync(world.app.pk, world.env.pk)
    other = "serviceAccount:other-app@acme-prod.iam.gserviceaccount.com"
    world.iam.views[view]["bindings"].append({"role": _VIEW_ACCESSOR, "members": [other]})

    attachment.soft_delete()
    _ensure_workload_identity_sync(world.app.pk, world.env.pk)

    assert _readers(world, view) == {other}


@pytest.mark.django_db
def test_an_app_never_bound_to_a_bundle_makes_no_identity_call(world):
    result = _ensure_workload_identity_sync(world.app.pk, world.env.pk)

    assert result == {
        "skipped": True,
        "reason": "no managed services",
        "registered_app_id": world.app.pk,
        "withdrawn_log_views": [],
    }
    assert world.resolved == []


# ----- the detach starts the reconcile ------------------------------------


def _info():
    request = SimpleNamespace(user=SimpleNamespace(pk=None, email="operator@example.com"), META={})
    return SimpleNamespace(context=SimpleNamespace(request=request, user=request.user))


def _detach(world, attachment):
    with tenant_context(TenantContext(organization_id=world.app.organization_id)):
        return ServicesMutation().detach_project_managed_service(
            _info(), input=DetachProjectManagedServiceInput(attachment_id=GUID(str(attachment.guid)))
        )


@pytest.mark.django_db
def test_detaching_a_bundle_reconciles_the_apps_identity_once_it_commits(
    world, permission_resolver, monkeypatch, django_capture_on_commit_callbacks
):
    _, _, (attachment,) = _shared_bundle(world, world.env)
    starts = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow",
        lambda name, *, args, workflow_id: starts.append((name, args, workflow_id)),
    )
    permission_resolver.grant(Permission.PROJECT_UPDATE)

    with django_capture_on_commit_callbacks() as callbacks:
        result = _detach(world, attachment)
        # Nothing starts before the commit: the reconcile would read the
        # attachment as live and keep the grant.
        assert starts == []
    for callback in callbacks:
        callback()

    assert result.ok, result.errors
    assert starts == [
        (
            "ReconcileWorkloadIdentityWorkflow",
            [
                ReconcileWorkloadIdentityInput(
                    registered_app_id=world.app.pk,
                    app_environment_id=world.env.pk,
                    actor=Actor(kind="user", user_id=None, display="operator@example.com"),
                )
            ],
            f"ReconcileWorkloadIdentityWorkflow-{world.env.guid}",
        )
    ]


@pytest.mark.django_db
def test_detaching_any_other_service_starts_no_reconcile(
    world, permission_resolver, monkeypatch, django_capture_on_commit_callbacks
):
    redis = ManagedService.objects.create(
        project=world.app.project, tenant_cluster=world.cluster, kind=ManagedService.Kind.REDIS, name="cache"
    )
    attachment = ManagedServiceAttachment.objects.create(managed_service=redis, app_environment=world.env)
    starts = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow", lambda *args, **kwargs: starts.append(args)
    )
    permission_resolver.grant(Permission.PROJECT_UPDATE)

    with django_capture_on_commit_callbacks(execute=True):
        result = _detach(world, attachment)

    assert result.ok, result.errors
    assert starts == []


@pytest.mark.django_db
def test_a_reconcile_that_cannot_start_is_logged_and_the_detach_stands(
    world, permission_resolver, monkeypatch, django_capture_on_commit_callbacks, caplog
):
    _, _, (attachment,) = _shared_bundle(world, world.env)

    def unavailable(*args, **kwargs):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", unavailable)
    permission_resolver.grant(Permission.PROJECT_UPDATE)
    caplog.set_level(logging.ERROR, logger="astrolift_services.schema.mutations.managed_services")

    with django_capture_on_commit_callbacks(execute=True):
        result = _detach(world, attachment)

    assert result.ok, result.errors
    attachment.refresh_from_db()
    assert attachment.deleted_at is not None
    [record] = [r for r in caplog.records if r.name == "astrolift_services.schema.mutations.managed_services"]
    assert record.levelno == logging.ERROR
    assert (
        record.getMessage()
        == f"could not start the workload identity reconcile after a detach from {world.env.guid}"
    )
    assert "temporal unavailable" in str(record.exc_info[1])


async def test_the_reconcile_workflow_runs_the_identity_activity_on_the_sandboxed_runner(temporal_env):
    calls = []

    @activity.defn(name="astrolift.workload_identity.ensure")
    async def ensure_workload_identity(registered_app_id: int, app_environment_id: int) -> dict:
        calls.append((registered_app_id, app_environment_id))
        return {"withdrawn_log_views": ["v"]}

    worker = Worker(
        temporal_env.client,
        task_queue="astrolift-test",
        workflows=[ReconcileWorkloadIdentityWorkflow],
        activities=[ensure_workload_identity],
        workflow_runner=SandboxedWorkflowRunner(),
        workflow_failure_exception_types=[NondeterminismError],
    )
    async with worker:
        result = await temporal_env.client.execute_workflow(
            ReconcileWorkloadIdentityWorkflow.run,
            ReconcileWorkloadIdentityInput(
                registered_app_id=7, app_environment_id=9, actor=Actor(kind="system")
            ),
            id="reconcile-identity-2099",
            task_queue="astrolift-test",
        )

    assert calls == [(7, 9)]
    assert result == {"withdrawn_log_views": ["v"]}
