"""Tests for the ``provision_secrets_backend`` activity (#379).

The activity is the bring-into-management workflow's bridge between
``install_cluster_prereqs`` and ``apply_platform_rbac`` — it calls
``SecretsBackend.ensure_initialized()`` so the cluster's secrets store
is bootstrapped (CSI driver / KMS key / Vault auth) before any
platform workload starts trying to read secrets out of it.

The sync core is what the durable Temporal activity wraps, so we hit
it directly against a real Postgres + a recorder fake driver. The
workflow-integration test pins the activity's scheduling position
(after ``verify_reachability``, before ``apply_platform_rbac``) using
the same monkeypatched-``workflow.execute_activity`` recorder pattern
the decommission workflow tests use.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization
from astrolift_workflows.activities.cluster_management import (
    _provision_secrets_backend_sync,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Suppress the Profile -> OpenSearch indexing chain that fires on
    every Organization/User create."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


# ---- Fixtures + fake driver ---------------------------------------


class _RecorderSecretsBackend:
    """Fake SecretsBackend that records ``ensure_initialized`` calls.

    The activity only touches ``ensure_initialized`` so the other
    protocol methods stay un-implemented — exercising them would mean
    a different bug than the one this test guards against.
    """

    def __init__(self, *, raises: type[BaseException] | None = None, result: Any = None) -> None:
        self.raises = raises
        self.result = result if result is not None else {"installed": "csi-driver"}
        self.calls = 0

    def ensure_initialized(self) -> dict[str, Any]:
        self.calls += 1
        if self.raises is not None:
            raise self.raises("ensure_initialized not required for this backend")
        return self.result


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def provider_plugin():
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="k8s",
                slug="k8s_native",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return p


@pytest.fixture
def cluster(org, provider_plugin):
    return TenantCluster.objects.create(
        organization=org,
        slug=f"cluster-{uuid.uuid4().hex[:6]}",
        name="dev-cluster",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.REGISTERED.value,
    )


def _patch_driver_for_capability(monkeypatch, driver: Any) -> None:
    """Replace ``core.app_deploy.driver_for_capability`` with a stub
    that returns ``driver`` regardless of inputs.

    The activity imports inside the function body, so we patch the
    canonical module path the activity reaches into.
    """

    def _stub(cluster_arg, capability):
        assert capability == "secrets"
        return driver

    monkeypatch.setattr("core.app_deploy.driver_for_capability", _stub)


# ---- Happy path ---------------------------------------------------


def test_provision_secrets_backend_calls_ensure_initialized_and_stamps_timestamp(
    cluster,
    monkeypatch,
):
    driver = _RecorderSecretsBackend(result={"csi": "installed", "kms_key_id": "alias/x"})
    _patch_driver_for_capability(monkeypatch, driver)

    payload = _provision_secrets_backend_sync(cluster.pk)

    assert driver.calls == 1
    assert payload["ok"] is True
    assert payload["skipped"] is False
    assert payload["result"] == {"csi": "installed", "kms_key_id": "alias/x"}
    cluster.refresh_from_db()
    assert cluster.secrets_backend_provisioned_at is not None


# ---- Idempotent re-run --------------------------------------------


def test_provision_secrets_backend_idempotent_rerun_updates_timestamp(
    cluster,
    monkeypatch,
):
    """A second call should succeed cleanly. The driver decides what
    ``idempotent`` means under the hood; the activity itself just
    re-stamps the timestamp.
    """
    driver = _RecorderSecretsBackend()
    _patch_driver_for_capability(monkeypatch, driver)

    _provision_secrets_backend_sync(cluster.pk)
    cluster.refresh_from_db()
    first_stamp = cluster.secrets_backend_provisioned_at
    assert first_stamp is not None

    payload = _provision_secrets_backend_sync(cluster.pk)
    assert payload["ok"] is True
    assert payload["skipped"] is False
    assert driver.calls == 2
    cluster.refresh_from_db()
    # Re-run rewrites the timestamp; second stamp is >= first because
    # ``timezone.now()`` moves forward (or stays equal under clock
    # resolution).
    assert cluster.secrets_backend_provisioned_at is not None
    assert cluster.secrets_backend_provisioned_at >= first_stamp


# ---- NotImplementedError path -------------------------------------


def test_provision_secrets_backend_skips_when_driver_raises_not_implemented(
    cluster,
    monkeypatch,
):
    """AWS Secrets Manager (and any backend that needs no out-of-band
    setup) inherits the default ``NotImplementedError`` from the
    protocol. The activity catches it, returns ``skipped``, and leaves
    the timestamp null so the UI / status checks can show "not
    required".
    """
    driver = _RecorderSecretsBackend(raises=NotImplementedError)
    _patch_driver_for_capability(monkeypatch, driver)

    payload = _provision_secrets_backend_sync(cluster.pk)

    assert payload["ok"] is True
    assert payload["skipped"] is True
    assert "does not require initialization" in payload["reason"]
    cluster.refresh_from_db()
    assert cluster.secrets_backend_provisioned_at is None


def test_provision_secrets_backend_skips_when_plugin_has_no_secrets_driver(
    cluster,
    monkeypatch,
):
    """A provider plugin without a registered ``secrets`` driver is
    treated as an opt-out — the workflow continues with a skipped
    result rather than failing bring-into-management. Operators can
    wire a secrets driver in later without re-running cluster bring.
    """
    from core.app_deploy import AppDeployError

    def _stub(cluster_arg, capability):
        raise AppDeployError("no secrets driver")

    monkeypatch.setattr("core.app_deploy.driver_for_capability", _stub)

    payload = _provision_secrets_backend_sync(cluster.pk)

    assert payload["ok"] is True
    assert payload["skipped"] is True
    assert "no secrets driver" in payload["reason"]
    cluster.refresh_from_db()
    assert cluster.secrets_backend_provisioned_at is None


# ---- Workflow integration: scheduling position --------------------


def test_workflow_schedules_secrets_provisioning_between_reachability_and_rbac(
    cluster,
    monkeypatch,
):
    """The bring-into-management workflow must schedule
    ``provision_secrets_backend`` AFTER ``verify_reachability`` and
    BEFORE ``apply_platform_rbac``. We don't spin up a Temporal worker
    — the orchestrator is straight-line ``async`` code calling
    ``workflow.execute_activity``, so we patch the
    ``temporalio.workflow`` surface and run ``run`` against an
    in-process recorder. Same reasoning as the existing
    ``test_bring_cluster_into_management.py``: orchestration logic is
    sequential dispatch and a real worker fixture would require
    ``transaction=True`` (per the project's saved gotcha).
    """
    from temporalio import workflow as temporalio_workflow

    from astrolift_workflows.activities import (
        apply_platform_rbac,
        mark_managed,
        mark_managing,
        probe_capabilities,
        provision_secrets_backend,
        run_preflight_job,
        verify_reachability,
    )
    from astrolift_workflows.inputs import Actor, BringClusterIntoManagementInput
    from astrolift_workflows.workflows.bring_cluster_into_management import (
        BringClusterIntoManagementWorkflow,
    )

    schedule_log: list[str] = []

    async def _fake_execute_activity(activity_fn, *args, **kwargs):
        schedule_log.append(getattr(activity_fn, "__name__", str(activity_fn)))
        # apply_platform_rbac returns a list[str] of messages; nothing
        # else in this workflow consumes a non-None return value.
        if activity_fn is apply_platform_rbac:
            return ["applied"]
        if activity_fn is probe_capabilities:
            return {}
        if activity_fn is run_preflight_job:
            return "preflight ok"
        if activity_fn is provision_secrets_backend:
            return {"ok": True, "skipped": False}
        return None

    class _NullLogger:
        def warning(self, *args, **kwargs): ...
        def info(self, *args, **kwargs): ...
        def exception(self, *args, **kwargs): ...

    monkeypatch.setattr(temporalio_workflow, "execute_activity", _fake_execute_activity)
    monkeypatch.setattr(temporalio_workflow, "logger", _NullLogger())

    workflow_instance = BringClusterIntoManagementWorkflow()
    actor = Actor(kind="user", user_id=1, display="tester")
    input_payload = BringClusterIntoManagementInput(
        cluster_id=cluster.pk,
        actor=actor,
        force_preflight=True,
    )
    result = asyncio.new_event_loop().run_until_complete(
        workflow_instance.run(input_payload),
    )

    assert result.ok is True
    expected = [
        mark_managing.__name__,
        verify_reachability.__name__,
        provision_secrets_backend.__name__,
        apply_platform_rbac.__name__,
        probe_capabilities.__name__,
        run_preflight_job.__name__,
        mark_managed.__name__,
    ]
    assert schedule_log == expected


def test_workflow_skipped_preflight_still_schedules_secrets_provisioning(
    cluster,
    monkeypatch,
):
    """When ``force_preflight=False`` the workflow skips the preflight
    Job activity. The secrets-provisioning step must still run — it's
    a prerequisite for the platform RBAC apply, not a function of the
    Job phase.
    """
    from temporalio import workflow as temporalio_workflow

    from astrolift_workflows.activities import (
        apply_platform_rbac,
        probe_capabilities,
        provision_secrets_backend,
        run_preflight_job,
    )
    from astrolift_workflows.inputs import Actor, BringClusterIntoManagementInput
    from astrolift_workflows.workflows.bring_cluster_into_management import (
        BringClusterIntoManagementWorkflow,
    )

    schedule_log: list[str] = []

    async def _fake_execute_activity(activity_fn, *args, **kwargs):
        schedule_log.append(getattr(activity_fn, "__name__", str(activity_fn)))
        if activity_fn is apply_platform_rbac:
            return ["applied"]
        if activity_fn is probe_capabilities:
            return {}
        if activity_fn is provision_secrets_backend:
            return {"ok": True, "skipped": True, "reason": "not required"}
        return None

    class _NullLogger:
        def warning(self, *args, **kwargs): ...
        def info(self, *args, **kwargs): ...
        def exception(self, *args, **kwargs): ...

    monkeypatch.setattr(temporalio_workflow, "execute_activity", _fake_execute_activity)
    monkeypatch.setattr(temporalio_workflow, "logger", _NullLogger())

    workflow_instance = BringClusterIntoManagementWorkflow()
    actor = Actor(kind="user", user_id=1, display="tester")
    input_payload = BringClusterIntoManagementInput(
        cluster_id=cluster.pk,
        actor=actor,
        force_preflight=False,
    )
    result = asyncio.new_event_loop().run_until_complete(
        workflow_instance.run(input_payload),
    )

    assert result.ok is True
    assert provision_secrets_backend.__name__ in schedule_log
    # Preflight Job is the one that doesn't run on force_preflight=False
    assert run_preflight_job.__name__ not in schedule_log
    # Position: provision_secrets_backend lands before apply_platform_rbac
    assert schedule_log.index(provision_secrets_backend.__name__) < schedule_log.index(
        apply_platform_rbac.__name__
    )


def test_workflow_marks_error_when_provision_secrets_backend_raises(
    cluster,
    monkeypatch,
):
    """Activity failure inside the secrets-provisioning step must short
    -circuit the workflow into ``_fail`` -> ``mark_error`` with the
    activity name + exception in the message — same pattern every
    other step uses.
    """
    from temporalio import workflow as temporalio_workflow

    from astrolift_workflows.activities import (
        apply_platform_rbac,
        mark_error,
        provision_secrets_backend,
    )
    from astrolift_workflows.inputs import Actor, BringClusterIntoManagementInput
    from astrolift_workflows.workflows.bring_cluster_into_management import (
        BringClusterIntoManagementWorkflow,
    )

    schedule_log: list[str] = []
    mark_error_args: list[tuple[Any, ...]] = []

    async def _fake_execute_activity(activity_fn, *args, **kwargs):
        schedule_log.append(getattr(activity_fn, "__name__", str(activity_fn)))
        if activity_fn is provision_secrets_backend:
            raise RuntimeError("CSI driver helm install rejected: rate limited")
        if activity_fn is mark_error:
            mark_error_args.append(tuple(kwargs.get("args", args)))
            return None
        return None

    class _NullLogger:
        def warning(self, *args, **kwargs): ...
        def info(self, *args, **kwargs): ...
        def exception(self, *args, **kwargs): ...

    monkeypatch.setattr(temporalio_workflow, "execute_activity", _fake_execute_activity)
    monkeypatch.setattr(temporalio_workflow, "logger", _NullLogger())

    workflow_instance = BringClusterIntoManagementWorkflow()
    actor = Actor(kind="user", user_id=1, display="tester")
    input_payload = BringClusterIntoManagementInput(
        cluster_id=cluster.pk,
        actor=actor,
        force_preflight=False,
    )
    result = asyncio.new_event_loop().run_until_complete(
        workflow_instance.run(input_payload),
    )

    assert result.ok is False
    assert "provision_secrets_backend" in result.message
    assert "rate limited" in result.message
    # apply_platform_rbac must NOT have been scheduled — the failure
    # short-circuits before it.
    assert apply_platform_rbac.__name__ not in schedule_log
    assert mark_error.__name__ in schedule_log
