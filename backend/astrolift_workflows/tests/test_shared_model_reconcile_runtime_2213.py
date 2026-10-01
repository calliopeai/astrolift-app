"""Actual Temporal workflow + real ORM/activity/renderer seams for shared models."""

from copy import deepcopy
from uuid import uuid4

import pytest
from _sdk.k8s_naming import cluster_model_namespace, cluster_model_resource_name
from asgiref.sync import sync_to_async
from k8s_native.managed.shared_model_runtime import AUTH_REVISION

from astrolift_services.model_subscriptions import subscription_namespace, subscription_secret_name
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from astrolift_services.tests.test_shared_model_binding_runtime_2213 import (
    mark_model_observed,
    runtime_world,  # noqa: F401 -- real ORM fixture shared with destination checks
)
from astrolift_workflows.activities import shared_model_reconcile as reconcile
from astrolift_workflows.inputs import Actor, SharedModelReconcileInput
from astrolift_workflows.workflows.shared_model_reconcile import SharedModelReconcileWorkflow
from core.testing.temporal import temporal_worker

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def activity_runtime(runtime_world, monkeypatch):  # noqa: F811 -- imported pytest fixture
    w = runtime_world
    monkeypatch.setattr(reconcile, "resolved_driver", lambda service, cluster: (w.vllm, w.cfg))
    # Provider/operator infrastructure discovery is outside this controlled SDK boundary.
    monkeypatch.setattr(
        "astrolift_workflows.activities.managed_service_lifecycle._run_managed_service_preflight",
        lambda *args, **kwargs: None,
    )
    return w


def test_readiness_observation_is_required_before_app_activation(activity_runtime):
    w = activity_runtime
    with pytest.raises(ValueError, match="rollout"):
        reconcile._activate_sync(w.model.pk, 1)
    assert not w.driver.writes
    assert not reconcile._finish_sync(w.model.pk, 1)
    w.model.backend_ref = ""
    w.model.save()
    assert reconcile._apply_sync(w.model.pk, 1, "apply", False) == "applied"
    assert reconcile._observe_sync(w.model.pk, 1) == "pending"
    w.model.refresh_from_db()
    assert w.model.applied_subscription_revision == 0 and w.model.model_ready_observed_at is None
    assert not any(
        key[0] == "v1/Secret" and key[1] == subscription_namespace(w.envs[0]) for key in w.driver.objects
    )
    w.driver.converge = True
    assert reconcile._observe_sync(w.model.pk, 1) == "pending"
    assert reconcile._observe_sync(w.model.pk, 1) == "ready"
    assert reconcile._activate_sync(w.model.pk, 1)
    assert not reconcile._finish_sync(w.model.pk, 1)
    assert any(reconcile._finish_sync(w.model.pk, 1) for _ in range(3))
    w.model.refresh_from_db()
    assert w.model.status == ManagedService.Status.ACTIVE
    assert w.model.applied_config == w.model.config
    assert all(
        row.subscription_status == "active" and row.applied_revision == 1
        for row in ManagedServiceAttachment.objects.filter(pk__in=[r.pk for r in w.rows])
    )


@pytest.mark.parametrize(
    "change",
    [
        "revision",
        "cluster_identity",
        "provider_identity",
        "retired_environment",
        "retired_app",
        "foreign_environment_cluster",
    ],
)
def test_stale_or_retired_activation_has_no_destination_writes(activity_runtime, change):
    w = activity_runtime
    mark_model_observed(w)
    if change == "revision":
        w.model.subscription_revision = 2
        w.model.save()
    elif change == "cluster_identity":
        w.model.model_operation_cluster_guid = uuid4()
        w.model.save()
    elif change == "provider_identity":
        w.model.model_operation_provider_guid = uuid4()
        w.model.save()
    elif change == "retired_environment":
        w.envs[0].soft_delete()
    elif change == "retired_app":
        w.apps[0].soft_delete()
    else:
        from core.tests.utils.scope_world import make_cluster

        w.envs[0].tenant_cluster = make_cluster(w, "replaced-binding2213")
        w.envs[0].save()
    with pytest.raises(ValueError):
        reconcile._activate_sync(w.model.pk, 1)
    assert not w.driver.writes


def test_old_failure_cannot_overwrite_newer_intent(activity_runtime):
    w = activity_runtime
    w.model.subscription_revision = 2
    w.model.save()
    reconcile._failed_sync(w.model.pk, 1)
    w.model.refresh_from_db()
    assert w.model.status == "updating" and not w.model.status_error
    assert all(row.subscription_status == "pending" for row in w.model.attachments.all())


async def run_actual_workflow(env, w, action="apply"):
    from astrolift_workflows.worker import ACTIVITIES, WORKFLOWS

    activities = [
        reconcile.apply_shared_model,
        reconcile.observe_shared_model,
        reconcile.activate_shared_model_subscriptions,
        reconcile.finish_shared_model_reconcile,
        reconcile.fail_shared_model_reconcile,
    ]
    assert SharedModelReconcileWorkflow in WORKFLOWS
    assert set(activities).issubset(ACTIVITIES)
    queue = f"shared-model-2213-{uuid4().hex}"
    async with temporal_worker(
        env, task_queue=queue, workflows=[SharedModelReconcileWorkflow], activities=activities
    ):
        workflow_id = f"shared-model-2213-{uuid4().hex}"
        result = await env.client.execute_workflow(
            SharedModelReconcileWorkflow.run,
            SharedModelReconcileInput(
                w.model.pk, w.model.subscription_revision, Actor(kind="system"), action
            ),
            id=workflow_id,
            task_queue=queue,
        )
        w.temporal_history = (await env.client.get_workflow_handle(workflow_id).fetch_history()).to_json()
        w.temporal_histories = [*getattr(w, "temporal_histories", []), w.temporal_history]
        return result


async def test_real_temporal_provisions_observes_activates_and_revokes_only_one_app(
    temporal_env, activity_runtime, recwarn
):
    w = activity_runtime
    w.model.backend_ref = ""
    await sync_to_async(w.model.save)()
    w.driver.converge = True
    result = await run_actual_workflow(temporal_env, w)
    assert result.ok, result.message
    await sync_to_async(w.model.refresh_from_db)()
    assert w.model.status == "active" and w.model.applied_subscription_revision == 1
    ns = cluster_model_namespace(
        organization_id=str(w.org.guid), cluster_id=str(w.cluster.guid), managed_service_id=str(w.model.guid)
    )
    name = cluster_model_resource_name(str(w.model.guid))
    model_resource = w.driver.objects[("apps/v1/Deployment", ns, name)]
    assert model_resource["metadata"]["annotations"][AUTH_REVISION] == "1"
    other_secret = deepcopy(
        w.driver.objects[
            ("v1/Secret", subscription_namespace(w.envs[1]), subscription_secret_name(w.rows[1]))
        ]
    )
    other_key = deepcopy(w.secrets.data[w.rows[1].credential_ref.partition("#")[0]])

    def revoke():
        w.model.subscription_revision = 2
        w.model.status = "updating"
        w.model.save()
        w.rows[0].desired_enabled = False
        w.rows[0].desired_revision = 2
        w.rows[0].subscription_status = "revoking"
        w.rows[0].save()

    await sync_to_async(revoke)()
    result = await run_actual_workflow(temporal_env, w)
    assert result.ok, result.message
    await sync_to_async(w.rows[0].refresh_from_db)()
    assert w.rows[0].subscription_status == "revoked" and w.rows[0].applied_revision == 2
    assert w.rows[0].credential_ref.partition("#")[0] in w.secrets.deleted
    assert w.secrets.data[w.rows[1].credential_ref.partition("#")[0]] == other_key
    # Other credentials survive; only conditional refresh metadata may change.
    actual_other = w.driver.objects[
        ("v1/Secret", subscription_namespace(w.envs[1]), subscription_secret_name(w.rows[1]))
    ]
    assert actual_other["stringData"] == other_secret["stringData"]
    assert (
        "v1/Secret",
        subscription_namespace(w.envs[0]),
        subscription_secret_name(w.rows[0]),
    ) not in w.driver.objects
    import json

    snapshot = json.loads(
        next(
            resource["stringData"]["keys.json"]
            for (kind, namespace, _), resource in w.driver.objects.items()
            if kind == "v1/Secret" and namespace == ns
        )
    )
    assert snapshot["subscription_keys"] == [other_key["api_key"]]
    assert snapshot["revision"] == 2
    for history in w.temporal_histories:
        assert snapshot["operator_key"] not in history
        assert "1" * 64 not in history and other_key["api_key"] not in history
    import gc

    gc.collect()
    assert not [warning for warning in recwarn if "was never awaited" in str(warning.message)]


@pytest.mark.parametrize("mode", ["refused_apply", "missing_readiness", "replaced_app"])
async def test_real_temporal_failure_never_marks_subscription_active(temporal_env, activity_runtime, mode):
    w = activity_runtime
    w.model.backend_ref = ""
    await sync_to_async(w.model.save)()
    w.driver.converge = mode == "replaced_app"
    if mode == "refused_apply":
        w.driver.rejected = True
    elif mode == "replaced_app":
        target = w.driver.objects[("apps/v1/Deployment", subscription_namespace(w.envs[0]), "api")]
        target["metadata"]["labels"]["astrolift.dev/environment"] = "foreign"
    result = await run_actual_workflow(temporal_env, w)
    assert result.ok is False
    await sync_to_async(w.model.refresh_from_db)()
    assert w.model.status == "failed" and "retry or operator" in w.model.status_error
    statuses = await sync_to_async(
        lambda: list(w.model.attachments.values_list("subscription_status", flat=True))
    )()
    assert statuses == ["failed", "failed"]


@pytest.mark.parametrize("observation", ["generation", "provider", "recorded_target"])
def test_unconfirmed_rollout_observation_refuses_activation_and_finish_before_transport(
    activity_runtime, monkeypatch, observation
):
    w = activity_runtime
    mark_model_observed(w)
    if observation == "generation":
        w.model.model_ready_generation = 0
    elif observation == "provider":
        w.model.model_ready_provider_guid = uuid4()
    else:
        w.model.model_ready_backend_ref = w.model.backend_ref + "-stale-target"
    w.model.save()

    def unexpected_transport(*args):
        pytest.fail("unconfirmed rollout must be refused before provider resolution or credential reads")

    monkeypatch.setattr(reconcile, "resolved_driver", unexpected_transport)
    with pytest.raises(ValueError, match="rollout is not confirmed"):
        reconcile._activate_sync(w.model.pk, 1)
    assert reconcile._finish_sync(w.model.pk, 1) is False
    assert not w.driver.writes and not w.secrets.reads and not w.secrets.deleted
    assert all(
        row.subscription_status == "pending" and row.applied_revision == 0
        for row in ManagedServiceAttachment.objects.filter(pk__in=[row.pk for row in w.rows])
    )
    w.model.refresh_from_db()
    assert w.model.status == "updating" and w.model.applied_subscription_revision == 1
