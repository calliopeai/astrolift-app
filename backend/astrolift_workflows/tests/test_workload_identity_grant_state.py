"""The workload-identity reconcile persists per-assignment state (#1367).

PR #1444 made Azure grants real role assignments; a binding could still report
ready while one of those assignments was propagating or rejected. These cover
the seam that closes it: what became of each grant has to land on the binding
that declared the grant, including on the path where the reconcile raises.

Since #2279 the Azure reconcile runs through the production identity port, so
these drive it against a controlled ARM (``core.testing.azure_arm``) rather
than a stand-in driver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest
from _sdk.identity import GrantAssignment
from azure.identity_owned import AzureIdentityRefused, AzureOwnedIdentityError

from astrolift_clusters.models import ProviderPlugin
from astrolift_lifecycle.models import AppEnvironment
from astrolift_services.models import (
    AzureWorkloadIdentity,
    WorkloadIdentityGrant,
    grant_state_for,
)
from astrolift_workflows.activities.workload_identity import (
    _ensure_workload_identity_sync,
    _persist_grant_state,
)
from astrolift_workflows.tests.test_azure_workload_identity_2279 import (
    BLOB_CONTRIBUTOR,
    Binding,
    Grant,
    app_service,
    azure_app,
    blob_scope,
    contributor,
    controlled_arm,
    owner_tags,
    reconcile,
)
from core.app_deploy import workload_identity_role_name
from core.testing.azure_arm import ControlledARM

pytestmark = pytest.mark.django_db


@pytest.fixture
def arm(monkeypatch: Any) -> ControlledARM:
    return controlled_arm(monkeypatch)


_REDIS_SCOPE = (
    "/subscriptions/6a1f8a52-1d9d-4c43-9f84-0c2f43a1d3b1/resourceGroups/rg-platform"
    "/providers/Microsoft.Cache/Redis/cache"
)


def test_an_unconfirmed_assignment_is_pending_against_the_binding_that_declared_it(
    arm: ControlledARM,
) -> None:
    state = azure_app()
    store = app_service(state, "blobs")
    arm.failures[("PUT", "/roleAssignments/")] = 400

    with pytest.raises(AzureOwnedIdentityError):
        reconcile(state, {"blobs": contributor("data")})

    row = WorkloadIdentityGrant.objects.get(managed_service=store)
    assert row.state == WorkloadIdentityGrant.State.PENDING
    assert "did not confirm" in row.reason
    assert row.applied_at is None
    assert row.last_attempted_at is not None
    assert row.scope == blob_scope("data")
    assert grant_state_for([row]) == "pending"


def test_an_applied_assignment_clears_the_reason_and_stamps_applied_at(arm: ControlledARM) -> None:
    state = azure_app()
    app_service(state, "blobs")
    arm.failures[("PUT", "/roleAssignments/")] = 400
    with pytest.raises(AzureOwnedIdentityError):
        reconcile(state, {"blobs": contributor("data")})
    arm.failures.clear()

    summary = reconcile(state, {"blobs": contributor("data")})

    row = WorkloadIdentityGrant.objects.get()
    assert row.state == WorkloadIdentityGrant.State.APPLIED
    assert row.reason == ""
    assert row.applied_at is not None
    assert row.assignment_name in arm.roles
    assert grant_state_for([row]) == "applied"
    assert (summary["grants_pending"], summary["grants_failed"]) == (0, 0)


def test_a_control_plane_only_binding_records_nothing_and_is_not_pending_forever(arm: ControlledARM) -> None:
    # The twelve Azure drivers whose grants are all control-plane work reach the
    # pod as a projected Secret. They require no role assignment, so requiring
    # one would leave them unready for good.
    state = azure_app()
    app_service(state, "cache")

    reconcile(state, {"cache": Binding([Grant(_REDIS_SCOPE, ["Microsoft.Cache/Redis/listKeys/action"])])})

    assert WorkloadIdentityGrant.objects.count() == 0
    assert not arm.roles
    assert grant_state_for([]) == "not_required"


def test_an_assignment_nothing_was_reported_about_is_pending_not_applied() -> None:
    # Silence reading as success is the whole failure shape of this issue.
    state = azure_app()
    store = app_service(state, "blobs")
    permission = {"role_definition_id": BLOB_CONTRIBUTOR, "role_name": "Blob", "scope": blob_scope("data")}

    _persist_grant_state(
        environment=state.env,
        services=[store],
        declared_by_service=[[permission]],
        outcomes=[],
        plugin_slug="azure",
        identity_role_name="identity",
    )

    row = WorkloadIdentityGrant.objects.get()
    assert row.state == WorkloadIdentityGrant.State.PENDING
    assert "reported nothing" in row.reason


def test_a_refused_identity_records_failed_assignments_before_the_reconcile_raises(
    arm: ControlledARM,
) -> None:
    state = azure_app()
    app_service(state, "blobs")
    arm.seed_identity(workload_identity_role_name(state.app), tags={})

    with pytest.raises(AzureIdentityRefused):
        reconcile(state, {"blobs": contributor("data")})

    row = WorkloadIdentityGrant.objects.get()
    assert row.state == WorkloadIdentityGrant.State.FAILED
    assert "ownership markers" in row.reason
    assert grant_state_for([row]) == "failed"


def test_two_bindings_sharing_one_assignment_each_keep_a_row(arm: ControlledARM) -> None:
    # The identity is granted once; both bindings still depend on it, so
    # neither may report ready while it is unapplied.
    state = azure_app()
    app_service(state, "blobs")
    app_service(state, "lookups")

    reconcile(state, {"blobs": contributor("data"), "lookups": contributor("data")})

    assert len(arm.roles) == 1
    rows = WorkloadIdentityGrant.objects.all()
    assert {row.managed_service.name for row in rows} == {"blobs", "lookups"}
    assert {row.state for row in rows} == {"applied"}


def test_a_grant_the_binding_stopped_declaring_is_soft_deleted(arm: ControlledARM) -> None:
    state = azure_app()
    app_service(state, "blobs")
    reconcile(state, {"blobs": contributor("data")})

    reconcile(state, {"blobs": Binding([])})

    assert WorkloadIdentityGrant.objects.count() == 0
    dropped = WorkloadIdentityGrant.all_objects.get()
    assert dropped.deleted_at is not None
    assert not arm.roles


@dataclass
class _FakeIdentityDriver:
    """An AWS identity driver's reconcile outcome, not its plumbing."""

    outcomes: list[GrantAssignment] = field(default_factory=list)

    def create_identity_role(self, name: str, permissions: list[dict[str, Any]]) -> str:
        return f"arn:aws:iam::111122223333:role/{name}"

    def bind_service_account(self, cluster: str, namespace: str, sa: str, role: str) -> dict[str, str]:
        return {"eks.amazonaws.com/role-arn": f"arn:aws:iam::111122223333:role/{role}"}


def test_a_non_azure_cluster_records_no_grants(arm: ControlledARM) -> None:
    # AWS folds the grants into the role's own inline policy in the same call,
    # so there is no second object whose state could differ.
    state = azure_app()
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            ),
        ],
        ignore_conflicts=True,
    )
    state.cluster.provider_plugin = ProviderPlugin.objects.get(slug="aws")
    state.cluster.auth_config = {"cluster_oidc_issuer": "https://oidc.example.invalid/id"}
    state.cluster.save(update_fields=["provider_plugin", "auth_config", "updated_at", "version"])
    app_service(state, "blobs")

    with (
        patch(
            "astrolift_workflows.activities.managed_service_lifecycle._managed_binding_for",
            return_value=Binding([Grant("arn:aws:s3:::bucket/*", ["s3:GetObject"])]),
        ),
        patch(
            "astrolift_workflows.activities.capability_deprovision._resolve_capability_driver",
            return_value=_FakeIdentityDriver(),
        ),
    ):
        _ensure_workload_identity_sync(state.app.pk, state.env.pk)

    assert WorkloadIdentityGrant.objects.count() == 0
    assert not AzureWorkloadIdentity.objects.exists() and not arm.calls


@pytest.mark.parametrize("reconcile_failed", [False, True])
def test_union_assignment_outcomes_stay_on_each_environment_owner(
    arm: ControlledARM, reconcile_failed
) -> None:
    state = azure_app()
    preview = AppEnvironment.objects.create(
        registered_app=state.app,
        tenant_cluster=state.cluster,
        name="preview-pr-3",
        k8s_namespace="web-wi-pr-3",
    )
    production = app_service(state, "production-blobs")
    branch = app_service(state, "preview-blobs", env=preview)
    bindings = {production.name: contributor("data"), branch.name: contributor("preview")}
    if reconcile_failed:
        arm.failures[("PUT", "/roleAssignments/")] = 400
        with pytest.raises(AzureOwnedIdentityError):
            reconcile(state, bindings, env=preview)
    else:
        result = reconcile(state, bindings, env=preview)
        assert result["grants"] == 2
    rows = {row.managed_service_id: row for row in WorkloadIdentityGrant.objects.all()}
    assert set(rows) == {production.pk, branch.pk}
    assert rows[production.pk].app_environment_id == state.env.pk
    assert rows[production.pk].scope == blob_scope("data")
    assert rows[branch.pk].app_environment_id == preview.pk
    assert rows[branch.pk].scope == blob_scope("preview")
    expected = "pending" if reconcile_failed else "applied"
    assert all(row.state == expected for row in rows.values())
    assert arm.identities[workload_identity_role_name(state.app)]["tags"] == owner_tags(state)


def test_an_azure_cluster_never_reaches_the_legacy_identity_driver(arm: ControlledARM) -> None:
    state = azure_app()
    app_service(state, "blobs")

    with patch(
        "astrolift_workflows.activities.capability_deprovision._resolve_capability_driver",
        side_effect=AssertionError("legacy driver"),
    ):
        reconcile(state, {"blobs": contributor("data")})

    assert arm.roles
