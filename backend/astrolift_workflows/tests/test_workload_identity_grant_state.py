"""The workload-identity reconcile persists per-assignment state (#1367).

PR #1444 made Azure grants real role assignments; a binding could still report
ready while one of those assignments was propagating or rejected. These cover
the seam that closes it: what the driver reports per grant has to land on the
binding that declared the grant, including on the path where the reconcile
raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest
from _sdk.identity import GrantAssignment

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import (
    ManagedService,
    WorkloadIdentityGrant,
    grant_state_for,
)
from astrolift_workflows.activities.workload_identity import (
    _ensure_workload_identity_sync,
)

pytestmark = pytest.mark.django_db

_SUB = "0000-sub"
_BLOB_ROLE = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"  # Storage Blob Data Contributor
_BLOB_SCOPE = (
    f"/subscriptions/{_SUB}/resourceGroups/rg/providers/Microsoft.Storage"
    "/storageAccounts/acct/blobServices/default/containers/data"
)
_REDIS_SCOPE = f"/subscriptions/{_SUB}/resourceGroups/rg/providers/Microsoft.Cache/Redis/cache"


@dataclass
class _Grant:
    resource: str
    actions: list[str]


@dataclass
class _Binding:
    iam_grants: list[_Grant]


@dataclass
class _FakeIdentityDriver:
    """Stands in for the Azure driver's reconcile outcome, not its plumbing."""

    outcomes: list[GrantAssignment] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    raise_after_reconcile: Exception | None = None

    def create_identity_role(self, name: str, permissions: list[dict[str, Any]]) -> str:
        if self.raise_after_reconcile is not None:
            raise self.raise_after_reconcile
        return f"/subscriptions/{_SUB}/.../userAssignedIdentities/{name}"

    def bind_service_account(self, cluster: str, namespace: str, sa: str, role: str) -> dict[str, str]:
        return {"azure.workload.identity/client-id": "client-1"}

    def grant_assignments(self) -> list[GrantAssignment]:
        return list(self.outcomes)

    def prune_refusals(self) -> list[str]:
        return list(self.refusals)


def _outcome(scope: str, state: str, *, reason: str = "") -> GrantAssignment:
    return GrantAssignment(
        role_definition_id=_BLOB_ROLE,
        role_name="Storage Blob Data Contributor",
        scope=scope,
        assignment_name="00000000-0000-0000-0000-000000000001",
        state=state,
        reason=reason,
    )


def _environment() -> tuple[RegisteredApp, AppEnvironment]:
    org = Organization.objects.create(name="Acme", slug="acme-wig")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-wig")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-wig")
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Azure",
                slug="azure",
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            ),
        ],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="aks-wig",
        name="AKS",
        provider_plugin=ProviderPlugin.objects.get(slug="azure"),
        endpoint="https://example.invalid",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="WIG App",
        slug="wig-app",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    return app, env


def _service(app: RegisteredApp, env: AppEnvironment, *, kind: str, name: str) -> ManagedService:
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=kind,
        variant="v1",
        name=name,
        backend_ref=f"{kind}/{name}",
        config={},
    )


def _run(app: RegisteredApp, env: AppEnvironment, driver: _FakeIdentityDriver, bindings: dict[str, Any]):
    """Run the reconcile with each service's binding taken from ``bindings``."""

    def binding_for(svc):
        return bindings.get(svc.name)

    with (
        patch(
            "astrolift_workflows.activities.managed_service_lifecycle._managed_binding_for",
            side_effect=binding_for,
        ),
        patch(
            "astrolift_workflows.activities.capability_deprovision._resolve_capability_driver",
            return_value=driver,
        ),
    ):
        return _ensure_workload_identity_sync(app.pk, env.pk)


def test_a_pending_assignment_is_recorded_against_the_binding_that_declared_it() -> None:
    app, env = _environment()
    store = _service(app, env, kind=ManagedService.Kind.OBJECT_STORE, name="blobs")
    driver = _FakeIdentityDriver(
        outcomes=[_outcome(_BLOB_SCOPE, "pending", reason="principal has not replicated")],
    )

    summary = _run(
        app,
        env,
        driver,
        {"blobs": _Binding([_Grant(_BLOB_SCOPE, ["Storage Blob Data Contributor"])])},
    )

    row = WorkloadIdentityGrant.objects.get(managed_service=store)
    assert row.state == WorkloadIdentityGrant.State.PENDING
    assert "replicated" in row.reason
    assert row.applied_at is None
    assert row.last_attempted_at is not None
    assert row.scope == _BLOB_SCOPE
    assert grant_state_for([row]) == "pending"
    assert summary["grants_pending"] == 1


def test_an_applied_assignment_clears_the_reason_and_stamps_applied_at() -> None:
    app, env = _environment()
    _service(app, env, kind=ManagedService.Kind.OBJECT_STORE, name="blobs")
    driver = _FakeIdentityDriver(outcomes=[_outcome(_BLOB_SCOPE, "applied")])

    _run(
        app,
        env,
        driver,
        {"blobs": _Binding([_Grant(_BLOB_SCOPE, ["Storage Blob Data Contributor"])])},
    )

    row = WorkloadIdentityGrant.objects.get()
    assert row.state == WorkloadIdentityGrant.State.APPLIED
    assert row.reason == ""
    assert row.applied_at is not None
    assert grant_state_for([row]) == "applied"


def test_a_control_plane_only_binding_records_nothing_and_is_not_pending_forever() -> None:
    # The twelve Azure drivers whose grants are all control-plane work reach the
    # pod as a projected Secret. They require no role assignment, so requiring
    # one would leave them unready for good.
    app, env = _environment()
    _service(app, env, kind=ManagedService.Kind.REDIS, name="cache")
    driver = _FakeIdentityDriver()

    _run(
        app,
        env,
        driver,
        {"cache": _Binding([_Grant(_REDIS_SCOPE, ["Microsoft.Cache/Redis/listKeys/action"])])},
    )

    assert WorkloadIdentityGrant.objects.count() == 0
    assert grant_state_for([]) == "not_required"


def test_an_assignment_the_driver_reported_nothing_about_is_pending_not_applied() -> None:
    # Silence reading as success is the whole failure shape of this issue.
    app, env = _environment()
    _service(app, env, kind=ManagedService.Kind.OBJECT_STORE, name="blobs")
    driver = _FakeIdentityDriver(outcomes=[])

    _run(
        app,
        env,
        driver,
        {"blobs": _Binding([_Grant(_BLOB_SCOPE, ["Storage Blob Data Contributor"])])},
    )

    row = WorkloadIdentityGrant.objects.get()
    assert row.state == WorkloadIdentityGrant.State.PENDING
    assert "reported nothing" in row.reason


def test_a_failed_assignment_is_persisted_before_the_reconcile_raises() -> None:
    app, env = _environment()
    _service(app, env, kind=ManagedService.Kind.OBJECT_STORE, name="blobs")
    driver = _FakeIdentityDriver(
        outcomes=[_outcome(_BLOB_SCOPE, "failed", reason="scope outside the subscription")],
        raise_after_reconcile=RuntimeError("scope outside the subscription"),
    )

    with pytest.raises(RuntimeError):
        _run(
            app,
            env,
            driver,
            {"blobs": _Binding([_Grant(_BLOB_SCOPE, ["Storage Blob Data Contributor"])])},
        )

    row = WorkloadIdentityGrant.objects.get()
    assert row.state == WorkloadIdentityGrant.State.FAILED
    assert row.reason == "scope outside the subscription"
    assert grant_state_for([row]) == "failed"


def test_two_bindings_sharing_one_assignment_each_keep_a_row() -> None:
    # The driver is handed the grant once; both bindings still depend on it, so
    # neither may report ready while it is unapplied.
    app, env = _environment()
    _service(app, env, kind=ManagedService.Kind.OBJECT_STORE, name="blobs")
    _service(app, env, kind=ManagedService.Kind.KV_STORE, name="lookups")
    driver = _FakeIdentityDriver(outcomes=[_outcome(_BLOB_SCOPE, "pending", reason="settling")])
    grant = _Binding([_Grant(_BLOB_SCOPE, ["Storage Blob Data Contributor"])])

    _run(app, env, driver, {"blobs": grant, "lookups": grant})

    owners = {row.managed_service.name for row in WorkloadIdentityGrant.objects.all()}
    assert owners == {"blobs", "lookups"}


def test_a_grant_the_binding_stopped_declaring_is_soft_deleted() -> None:
    app, env = _environment()
    _service(app, env, kind=ManagedService.Kind.OBJECT_STORE, name="blobs")
    applied = _FakeIdentityDriver(outcomes=[_outcome(_BLOB_SCOPE, "applied")])
    _run(
        app,
        env,
        applied,
        {"blobs": _Binding([_Grant(_BLOB_SCOPE, ["Storage Blob Data Contributor"])])},
    )

    _run(app, env, _FakeIdentityDriver(), {"blobs": _Binding([])})

    assert WorkloadIdentityGrant.objects.count() == 0
    dropped = WorkloadIdentityGrant.all_objects.get()
    assert dropped.deleted_at is not None


def test_a_non_azure_cluster_records_no_grants() -> None:
    # AWS folds the grants into the role's own inline policy in the same call,
    # so there is no second object whose state could differ.
    app, env = _environment()
    cluster = env.tenant_cluster
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws",
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            ),
        ],
        ignore_conflicts=True,
    )
    cluster.provider_plugin = ProviderPlugin.objects.get(slug="aws")
    cluster.auth_config = {"cluster_oidc_issuer": "https://oidc.example.invalid/id"}
    cluster.save(update_fields=["provider_plugin", "auth_config", "updated_at", "version"])
    _service(app, env, kind=ManagedService.Kind.OBJECT_STORE, name="blobs")

    _run(
        app,
        env,
        _FakeIdentityDriver(),
        {"blobs": _Binding([_Grant("arn:aws:s3:::bucket/*", ["s3:GetObject"])])},
    )

    assert WorkloadIdentityGrant.objects.count() == 0
