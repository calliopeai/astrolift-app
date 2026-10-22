"""Production Azure workload identity, end to end against PostgreSQL (#2279).

Before #2279 the identity capability built ``FederatedIdentityConfig`` without
an MSI client, so every create, bind and delete refused in production, and the
render emitted no ServiceAccount for Azure at all. These run the production
path: the capability's config and factory, the real MSI and Authorization SDK
clients, the platform's records. Only the HTTP transport and the token are
controlled (``core.testing.azure_arm``).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from azure.identity_owned import AzureIdentityRefused, AzureOwnedIdentityError

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import RegisteredApp
from astrolift_services.azure_workload_identity import AzureWorkloadIdentityError, render_identity
from astrolift_services.models import (
    AzureWorkloadIdentity,
    ManagedService,
    ManagedServiceAttachment,
    ManagedServiceBinding,
    WorkloadIdentityGrant,
)
from astrolift_workflows.activities.capability_deprovision import (
    CapabilityDeprovisionError,
    _deprovision_identity_role_sync,
)
from astrolift_workflows.activities.workload_identity import _ensure_workload_identity_sync
from core.app_deploy import namespace_for_environment, workload_identity_role_name
from core.testing.azure_arm import ControlledARM, install

pytestmark = pytest.mark.django_db

SUBSCRIPTION = "6a1f8a52-1d9d-4c43-9f84-0c2f43a1d3b1"
TENANT = "3c7d1d52-8a8b-4f3a-a1c4-6d2c6c1e7b20"
RESOURCE_GROUP = "rg-platform"
ISSUER = f"https://eastus.oic.prod-aks.azure.com/{TENANT}/9a1b2c3d-0000-4000-8000-000000000001/"
MOVED_ISSUER = f"https://eastus.oic.prod-aks.azure.com/{TENANT}/9a1b2c3d-0000-4000-8000-000000000002/"
AUDIENCE = "api://AzureADTokenExchange"
BLOB_CONTRIBUTOR = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
BLOB_READER = "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"


def blob_scope(container: str) -> str:
    return (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.Storage"
        f"/storageAccounts/acct/blobServices/default/containers/{container}"
    )


@dataclass
class Grant:
    resource: str
    actions: list[str]


@dataclass
class Binding:
    iam_grants: list[Grant]


def contributor(container: str) -> Binding:
    return Binding([Grant(blob_scope(container), ["Storage Blob Data Contributor"])])


def azure_app(suffix: str = "wi") -> SimpleNamespace:
    org = Organization.objects.create(name=f"Acme {suffix}", slug=f"acme-{suffix}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{suffix}")
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Azure",
                slug="azure",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            ),
        ],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"aks-{suffix}",
        name="AKS",
        provider_plugin=ProviderPlugin.objects.get(slug="azure"),
        endpoint="https://aks.example.invalid",
        provider_config={
            "subscription_id": SUBSCRIPTION,
            "tenant_id": TENANT,
            "resource_group": RESOURCE_GROUP,
            "location": "eastus",
            "cluster_name": "aks-1",
            "cluster_oidc_issuer": ISSUER,
        },
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Web",
        slug=f"web-{suffix}",
        provisioning_status="ready",
        default_tenant_cluster=cluster,
    )
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    return SimpleNamespace(org=org, project=project, cluster=cluster, app=app, env=env)


def app_service(state: SimpleNamespace, name: str, env: AppEnvironment | None = None) -> ManagedService:
    return ManagedService.objects.create(
        registered_app=state.app,
        app_environment=env or state.env,
        kind=ManagedService.Kind.OBJECT_STORE,
        variant="v1",
        name=name,
        backend_ref=f"object_store/{name}",
        config={},
    )


def reconcile(
    state: SimpleNamespace, bindings: dict[str, Binding], env: AppEnvironment | None = None
) -> dict:
    with patch(
        "astrolift_workflows.activities.managed_service_lifecycle._managed_binding_for",
        side_effect=lambda service: bindings.get(service.name),
    ):
        return _ensure_workload_identity_sync(state.app.pk, (env or state.env).pk)


def owner_tags(state: SimpleNamespace) -> dict[str, str]:
    return {
        "astrolift-managed-by": "platform",
        "astrolift-organization-id": str(state.org.guid),
        "astrolift-app-id": str(state.app.guid),
        "astrolift-cluster-id": str(state.cluster.guid),
    }


def subject(state: SimpleNamespace, env: AppEnvironment) -> str:
    return f"system:serviceaccount:{namespace_for_environment(env)}:{workload_identity_role_name(state.app)}"


def federations(arm: ControlledARM, state: SimpleNamespace) -> dict[str, tuple[str, str, list[str]]]:
    rows = arm.fics.get(workload_identity_role_name(state.app), {})
    return {
        name: (row["properties"]["issuer"], row["properties"]["subject"], row["properties"]["audiences"])
        for name, row in rows.items()
    }


def assignments(arm: ControlledARM) -> set[tuple[str, str, str]]:
    return {
        (
            row["properties"]["principalId"],
            row["properties"]["roleDefinitionId"].rsplit("/", 1)[-1],
            row["properties"]["scope"],
        )
        for row in arm.roles.values()
    }


def controlled_arm(monkeypatch: Any) -> ControlledARM:
    """The cluster's subscription, routed through for the production factory."""
    arm = ControlledARM(
        subscription_id=SUBSCRIPTION,
        tenant_id=TENANT,
        resource_group=RESOURCE_GROUP,
        cluster_name="aks-1",
        issuer=ISSUER,
    )
    install(monkeypatch, arm)
    return arm


@pytest.fixture
def arm(monkeypatch: Any) -> ControlledARM:
    return controlled_arm(monkeypatch)


def test_first_reconcile_creates_records_federates_and_grants_the_identity(arm: ControlledARM) -> None:
    state = azure_app()
    store = app_service(state, "blobs")

    summary = reconcile(state, {"blobs": contributor("data")})

    name = workload_identity_role_name(state.app)
    created = arm.identities[name]
    assert created["tags"] == owner_tags(state) and created["location"] == "eastus"
    record = AzureWorkloadIdentity.objects.get()
    assert (record.organization_id, record.registered_app_id, record.tenant_cluster_id) == (
        state.org.pk,
        state.app.pk,
        state.cluster.pk,
    )
    assert (record.resource_id, record.client_id, record.principal_id, record.tenant_id, record.issuer) == (
        arm.identity_path(name),
        created["properties"]["clientId"],
        created["properties"]["principalId"],
        TENANT,
        ISSUER,
    )
    assert list(federations(arm, state).values()) == [(ISSUER, subject(state, state.env), [AUDIENCE])]
    assert assignments(arm) == {(record.principal_id, BLOB_CONTRIBUTOR, blob_scope("data"))}
    [assignment] = arm.roles.values()
    assert assignment["properties"]["principalType"] == "ServicePrincipal"
    grant = WorkloadIdentityGrant.objects.get(managed_service=store)
    assert (grant.state, grant.reason, grant.assignment_name) == ("applied", "", assignment["name"])
    assert summary["annotation"] == {
        "azure.workload.identity/client-id": record.client_id,
        "azure.workload.identity/tenant-id": TENANT,
    }
    assert summary["role_arn"] == record.resource_id
    assert summary["propagation_verified"] is False


def test_a_second_reconcile_of_the_same_union_writes_nothing(arm: ControlledARM) -> None:
    state = azure_app()
    app_service(state, "blobs")
    reconcile(state, {"blobs": contributor("data")})
    writes = len(arm.writes)

    reconcile(state, {"blobs": contributor("data")})

    assert len(arm.writes) == writes
    assert AzureWorkloadIdentity.objects.count() == 1


@pytest.mark.parametrize("tags", ["untagged", "another_app"])
def test_a_same_name_identity_the_app_does_not_own_is_refused_and_never_written(
    arm: ControlledARM, tags
) -> None:
    state = azure_app()
    app_service(state, "blobs")
    foreign = owner_tags(state) | {"astrolift-app-id": "018f42f0-4420-7000-8000-000000000099"}
    arm.seed_identity(workload_identity_role_name(state.app), tags={} if tags == "untagged" else foreign)

    with pytest.raises(AzureIdentityRefused, match="without this app's ownership markers"):
        reconcile(state, {"blobs": contributor("data")})

    assert not arm.writes
    assert not AzureWorkloadIdentity.objects.exists()
    grant = WorkloadIdentityGrant.objects.get()
    assert grant.state == "failed" and "ownership markers" in grant.reason


def test_this_owners_unrecorded_identity_is_observed_and_recorded_not_recreated(arm: ControlledARM) -> None:
    # The create succeeded once but its record never committed.
    state = azure_app()
    app_service(state, "blobs")
    seeded = arm.seed_identity(workload_identity_role_name(state.app), tags=owner_tags(state))

    reconcile(state, {"blobs": contributor("data")})

    assert all(path != seeded["id"] for _, path, _ in arm.writes)
    assert AzureWorkloadIdentity.objects.get().principal_id == seeded["properties"]["principalId"]


def test_a_recorded_identity_deleted_in_azure_is_refused_not_recreated(arm: ControlledARM) -> None:
    state = azure_app()
    app_service(state, "blobs")
    reconcile(state, {"blobs": contributor("data")})
    arm.identities.clear()
    writes = len(arm.writes)

    with pytest.raises(AzureIdentityRefused, match="no longer exists"):
        reconcile(state, {"blobs": contributor("data")})

    assert len(arm.writes) == writes and not arm.identities
    assert AzureWorkloadIdentity.objects.count() == 1
    assert WorkloadIdentityGrant.objects.get().state == "failed"


def test_a_recorded_identity_replaced_under_its_name_is_refused(arm: ControlledARM) -> None:
    state = azure_app()
    app_service(state, "blobs")
    reconcile(state, {"blobs": contributor("data")})
    name = workload_identity_role_name(state.app)
    arm.identities[name]["properties"]["principalId"] = "018f42f0-4420-7000-8000-000000000098"
    writes = len(arm.writes)

    with pytest.raises(AzureIdentityRefused, match="was replaced"):
        reconcile(state, {"blobs": contributor("data")})

    assert len(arm.writes) == writes


def test_a_changed_issuer_is_refused_before_any_native_request(arm: ControlledARM) -> None:
    state = azure_app()
    app_service(state, "blobs")
    reconcile(state, {"blobs": contributor("data")})
    state.cluster.provider_config = {**state.cluster.provider_config, "cluster_oidc_issuer": MOVED_ISSUER}
    state.cluster.save(update_fields=["provider_config", "updated_at", "version"])
    arm.cluster["properties"]["oidcIssuerProfile"]["issuerURL"] = MOVED_ISSUER
    requests = len(arm.calls)

    with pytest.raises(AzureWorkloadIdentityError, match="does not move a federation to a new issuer"):
        reconcile(state, {"blobs": contributor("data")})

    assert len(arm.calls) == requests
    assert list(federations(arm, state).values())[0][0] == ISSUER
    grant = WorkloadIdentityGrant.objects.get()
    assert grant.state == "failed" and MOVED_ISSUER in grant.reason


def test_a_configured_issuer_the_cluster_does_not_sign_with_creates_nothing(arm: ControlledARM) -> None:
    state = azure_app()
    app_service(state, "blobs")
    arm.cluster["properties"]["oidcIssuerProfile"]["issuerURL"] = MOVED_ISSUER

    with pytest.raises(AzureIdentityRefused, match="signs with OIDC issuer"):
        reconcile(state, {"blobs": contributor("data")})

    assert not arm.writes and not AzureWorkloadIdentity.objects.exists()


def test_detaching_one_binding_removes_only_its_grant_and_keeps_every_other(arm: ControlledARM) -> None:
    state = azure_app()
    app_service(state, "blobs")
    shared = ManagedService.objects.create(
        project=state.project,
        tenant_cluster=state.cluster,
        kind=ManagedService.Kind.OBJECT_STORE,
        variant="v1",
        name="reports",
        backend_ref="object_store/reports",
        config={},
    )
    attachment = ManagedServiceAttachment.objects.create(managed_service=shared, app_environment=state.env)
    bindings = {
        "blobs": contributor("data"),
        "reports": Binding([Grant(blob_scope("reports"), ["Storage Blob Data Reader"])]),
    }
    reconcile(state, bindings)
    record = AzureWorkloadIdentity.objects.get()
    name = workload_identity_role_name(state.app)
    arm.seed_federation(
        name, "operator-ci", issuer="https://ci.example/", subject="repo:acme/web", audience=AUDIENCE
    )
    operator = arm.seed_assignment(
        scope=blob_scope("reports"),
        principal_id=record.principal_id,
        role_definition_guid=BLOB_READER,
        description="granted by the data team",
    )

    attachment.soft_delete()
    reconcile(state, bindings)

    assert assignments(arm) == {
        (record.principal_id, BLOB_CONTRIBUTOR, blob_scope("data")),
        (record.principal_id, BLOB_READER, blob_scope("reports")),
    }
    assert operator in arm.roles, "the operator's own grant of the same role is never touched"
    assert "operator-ci" in arm.fics[name]
    rows = {row.managed_service.name: row for row in WorkloadIdentityGrant.objects.all()}
    assert set(rows) == {"blobs"} and rows["blobs"].state == "applied"
    assert WorkloadIdentityGrant.all_objects.get(managed_service=shared).deleted_at is not None


def test_an_environment_whose_last_binding_was_detached_withdraws_its_federation(arm: ControlledARM) -> None:
    state = azure_app()
    staging = AppEnvironment.objects.create(
        registered_app=state.app, name="staging", tenant_cluster=state.cluster, k8s_namespace="web-wi-staging"
    )
    app_service(state, "blobs")
    staged = app_service(state, "staged", env=staging)
    bindings = {"blobs": contributor("data"), "staged": contributor("staging")}
    reconcile(state, bindings)
    assert {row[1] for row in federations(arm, state).values()} == {
        subject(state, state.env),
        subject(state, staging),
    }

    staged.soft_delete()
    summary = reconcile(state, bindings, env=staging)

    assert {row[1] for row in federations(arm, state).values()} == {subject(state, state.env)}
    principal = AzureWorkloadIdentity.objects.get().principal_id
    assert assignments(arm) == {(principal, BLOB_CONTRIBUTOR, blob_scope("data"))}
    assert summary["federations"] and summary["grants"] == 1


def test_an_unconfirmed_reconcile_is_pending_and_a_retry_applies_it(arm: ControlledARM) -> None:
    state = azure_app()
    app_service(state, "blobs")
    arm.failures[("PUT", "/roleAssignments/")] = 403

    with pytest.raises(AzureOwnedIdentityError, match="not confirmed"):
        reconcile(state, {"blobs": contributor("data")})

    grant = WorkloadIdentityGrant.objects.get()
    assert grant.state == "pending" and "did not confirm" in grant.reason
    assert AzureWorkloadIdentity.objects.count() == 1, "the identity stays recorded for the retry"

    arm.failures.clear()
    reconcile(state, {"blobs": contributor("data")})
    grant.refresh_from_db()
    assert (grant.state, grant.reason) == ("applied", "")
    assert len(arm.identities) == 1, "the retry reuses the recorded identity"


def test_render_annotates_the_service_account_and_labels_pods_from_the_record(arm: ControlledARM) -> None:
    from core.app_deploy import render_resources_for_deployment

    state = azure_app()
    store = app_service(state, "blobs")
    ManagedServiceBinding.objects.create(
        managed_service=store, env_key="BLOB_URL", env_value_ref="https://x", is_secret=False
    )
    state.app.manifest_raw = (
        'name = "web"\n\n[[workloads]]\nname = "web"\nkind = "deployment"\n\n'
        '  [[workloads.containers]]\n  name = "web"\n  is_primary = true\n  port = 8080\n'
    )
    state.app.save(update_fields=["manifest_raw"])
    deployment = Deployment.objects.create(
        registered_app=state.app,
        app_environment=state.env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
    )
    sa_name = workload_identity_role_name(state.app)

    before = render_resources_for_deployment(deployment)
    assert not [r for r in before if r["kind"] == "ServiceAccount"], "no identity is guessed before it exists"

    reconcile(state, {"blobs": contributor("data")})
    record = AzureWorkloadIdentity.objects.get()
    resources = render_resources_for_deployment(Deployment.objects.get(pk=deployment.pk))

    [account] = [r for r in resources if r["kind"] == "ServiceAccount"]
    assert account["metadata"]["name"] == sa_name
    assert account["metadata"]["annotations"] == {
        "azure.workload.identity/client-id": record.client_id,
        "azure.workload.identity/tenant-id": TENANT,
    }
    [web] = [r for r in resources if r["kind"] == "Deployment"]
    template = web["spec"]["template"]
    assert template["spec"]["serviceAccountName"] == sa_name
    assert template["metadata"]["labels"]["azure.workload.identity/use"] == "true"


def test_render_reads_only_a_record_of_the_apps_own_organization(arm: ControlledARM) -> None:
    state = azure_app()
    other = azure_app("other")
    AzureWorkloadIdentity.objects.create(
        organization=other.org,
        registered_app=state.app,
        tenant_cluster=state.cluster,
        tenant_id=TENANT,
        subscription_id=SUBSCRIPTION,
        resource_group=RESOURCE_GROUP,
        identity_name="x",
        resource_id="x",
        client_id="018f42f0-4420-7000-8000-000000000097",
        principal_id="018f42f0-4420-7000-8000-000000000096",
        issuer=ISSUER,
    )

    assert render_identity(state.app, state.cluster) == ({}, {})


def test_deprovision_deletes_the_recorded_identity_and_its_grants(arm: ControlledARM) -> None:
    state = azure_app()
    app_service(state, "blobs")
    reconcile(state, {"blobs": contributor("data")})
    record = AzureWorkloadIdentity.objects.get()

    summary = _deprovision_identity_role_sync(registered_app_id=state.app.pk)

    assert not arm.identities and not arm.roles
    assert ("DELETE", record.resource_id) in [(method, path) for method, path, _ in arm.writes]
    assert summary["roles_deleted"] == [record.resource_id]
    assert not AzureWorkloadIdentity.objects.exists()
    assert AzureWorkloadIdentity.all_objects.get().deleted_at is not None


def test_deprovision_keeps_an_identity_carrying_an_operators_federation(arm: ControlledARM) -> None:
    state = azure_app()
    app_service(state, "blobs")
    reconcile(state, {"blobs": contributor("data")})
    name = workload_identity_role_name(state.app)
    arm.seed_federation(
        name, "operator-ci", issuer="https://ci.example/", subject="repo:acme/web", audience=AUDIENCE
    )

    with pytest.raises(CapabilityDeprovisionError, match="operator-ci"):
        _deprovision_identity_role_sync(registered_app_id=state.app.pk)

    assert name in arm.identities and set(arm.fics[name]) == {"operator-ci"}
    assert not arm.roles, "the platform's own grants are still removed"
    assert AzureWorkloadIdentity.objects.count() == 1, "kept so a later removal can finish"


def test_after_an_issuer_change_removal_then_deploy_federates_a_new_identity(arm: ControlledARM) -> None:
    # The recovery the changed-issuer refusal names.
    state = azure_app()
    app_service(state, "blobs")
    reconcile(state, {"blobs": contributor("data")})
    old = AzureWorkloadIdentity.objects.get()
    state.cluster.provider_config = {**state.cluster.provider_config, "cluster_oidc_issuer": MOVED_ISSUER}
    state.cluster.save(update_fields=["provider_config", "updated_at", "version"])
    arm.cluster["properties"]["oidcIssuerProfile"]["issuerURL"] = MOVED_ISSUER

    _deprovision_identity_role_sync(registered_app_id=state.app.pk)
    reconcile(state, {"blobs": contributor("data")})

    new = AzureWorkloadIdentity.objects.get()
    assert new.pk != old.pk and new.principal_id != old.principal_id and new.issuer == MOVED_ISSUER
    assert list(federations(arm, state).values()) == [(MOVED_ISSUER, subject(state, state.env), [AUDIENCE])]
    assert assignments(arm) == {(new.principal_id, BLOB_CONTRIBUTOR, blob_scope("data"))}


def test_deprovision_of_an_azure_app_that_never_recorded_an_identity_calls_nothing(
    arm: ControlledARM,
) -> None:
    state = azure_app()

    summary = _deprovision_identity_role_sync(registered_app_id=state.app.pk)

    assert not arm.calls
    assert summary["roles_deleted"] == [] and summary["roles_skipped"] == []


@pytest.mark.parametrize("change", ["cluster_resource_group", "app_deleted"])
def test_a_change_under_the_reconcile_stops_it_before_the_next_request(
    arm: ControlledARM, change: str
) -> None:
    # The checkpoint re-reads the cluster, the app and the record around every
    # native request, so nothing is written on facts that stopped being true.
    from django.utils import timezone

    state = azure_app()
    app_service(state, "blobs")
    name = workload_identity_role_name(state.app)
    changed = []

    def hook(method: str, path: str) -> None:
        if not changed and path.casefold().endswith("/roleassignments"):
            changed.append(path)
            if change == "app_deleted":
                RegisteredApp.objects.filter(pk=state.app.pk).update(deleted_at=timezone.now())
            else:
                config = {**state.cluster.provider_config, "resource_group": "rg-elsewhere"}
                TenantCluster.objects.filter(pk=state.cluster.pk).update(provider_config=config)

    arm.hook = hook
    with pytest.raises(AzureOwnedIdentityError, match="not confirmed"):
        reconcile(state, {"blobs": contributor("data")})

    assert changed, "the hook fired during the role inventory"
    assert not arm.fics.get(name) and not arm.roles
    assert WorkloadIdentityGrant.objects.get().state == "pending"


def test_a_subscription_id_that_is_not_a_lowercase_guid_is_refused_before_any_request(
    arm: ControlledARM,
) -> None:
    state = azure_app()
    app_service(state, "blobs")
    state.cluster.provider_config = {**state.cluster.provider_config, "subscription_id": SUBSCRIPTION.upper()}
    state.cluster.save(update_fields=["provider_config", "updated_at", "version"])

    with pytest.raises(AzureWorkloadIdentityError, match="subscription_id as a lowercase GUID"):
        reconcile(state, {"blobs": contributor("data")})

    assert not arm.calls


def test_more_namespaces_than_one_identity_federates_is_refused_before_any_request(
    arm: ControlledARM,
) -> None:
    from astrolift_services.azure_workload_identity import MAX_FEDERATIONS, ensure_identity

    state = azure_app()
    namespaces = [f"web-wi-{n}" for n in range(MAX_FEDERATIONS + 1)]

    with pytest.raises(AzureWorkloadIdentityError, match="at most 20 federated credentials"):
        ensure_identity(state.app, state.env, state.cluster, namespaces=namespaces, permissions=[])

    assert not arm.calls


def test_the_app_doctor_reads_the_recorded_identity_on_azure(arm: ControlledARM) -> None:
    # The doctor computed the pod identity from cluster config, which cannot
    # name an Azure client ID: every Azure app read as broken.
    from astrolift_registry.services.app_doctor import CHECK_IDENTITY, run_app_doctor

    state = azure_app()
    app_service(state, "blobs")

    def identity_check() -> Any:
        report = run_app_doctor(RegisteredApp.objects.get(pk=state.app.pk))
        return next(check for check in report.checks if check.key == CHECK_IDENTITY)

    assert identity_check().status == "skip"
    reconcile(state, {"blobs": contributor("data")})
    assert identity_check().status == "pass"


def test_a_credential_the_cluster_cannot_use_is_a_failed_refusal_with_no_request(arm: ControlledARM) -> None:
    from core.cluster_credentials import ClusterCredentialInvalid

    state = azure_app()
    app_service(state, "blobs")
    state.cluster.provider_config = {
        **state.cluster.provider_config,
        "credential": {"mode": "aws_assume_role", "role_arn": "arn:aws:iam::111122223333:role/x"},
    }
    state.cluster.save(update_fields=["provider_config", "updated_at", "version"])

    with pytest.raises(ClusterCredentialInvalid, match="AWS-only"):
        reconcile(state, {"blobs": contributor("data")})

    grant = WorkloadIdentityGrant.objects.get()
    assert grant.state == "failed" and "AWS-only" in grant.reason
    assert not arm.calls


def test_deprovision_names_an_identity_on_a_cluster_it_can_no_longer_reach_once(arm: ControlledARM) -> None:
    from django.utils import timezone

    state = azure_app()
    gone = TenantCluster.objects.create(
        organization=state.org,
        slug="aks-gone",
        name="AKS gone",
        provider_plugin=state.cluster.provider_plugin,
        endpoint="https://gone.example.invalid",
        provider_config=state.cluster.provider_config,
    )
    stranded = AzureWorkloadIdentity.objects.create(
        organization=state.org,
        registered_app=state.app,
        tenant_cluster=gone,
        tenant_id=TENANT,
        subscription_id=SUBSCRIPTION,
        resource_group=RESOURCE_GROUP,
        identity_name="astrolift-stranded",
        resource_id=arm.identity_path("astrolift-stranded"),
        client_id="018f42f0-4420-7000-8000-000000000095",
        principal_id="018f42f0-4420-7000-8000-000000000094",
        issuer=ISSUER,
    )
    TenantCluster.objects.filter(pk=gone.pk).update(deleted_at=timezone.now())

    with pytest.raises(CapabilityDeprovisionError, match="astrolift-stranded.*delete it in Azure"):
        _deprovision_identity_role_sync(registered_app_id=state.app.pk)

    assert AzureWorkloadIdentity.all_objects.get(pk=stranded.pk).deleted_at is not None
    assert _deprovision_identity_role_sync(registered_app_id=state.app.pk)["roles_deleted"] == []
    assert not arm.calls


def test_deprovision_removes_the_identity_left_on_a_cluster_the_app_migrated_off(arm: ControlledARM) -> None:
    # A migration moves the environment's binding to its target cluster and
    # leaves the identity recorded on the source. Removal must still reach it,
    # or deregister fails on that record for good and the identity keeps its grants.
    from astrolift_workflows.activities.migration import _switch_app_env_binding_sync

    state = azure_app()
    app_service(state, "blobs")
    reconcile(state, {"blobs": contributor("data")})
    record = AzureWorkloadIdentity.objects.get()
    target = TenantCluster.objects.create(
        organization=state.org,
        slug="aks-target",
        name="AKS target",
        provider_plugin=state.cluster.provider_plugin,
        endpoint="https://target.example.invalid",
        provider_config={
            **state.cluster.provider_config,
            "resource_group": "rg-target",
            "cluster_name": "aks-2",
        },
    )
    _switch_app_env_binding_sync(state.env.pk, target.pk)

    summary = _deprovision_identity_role_sync(registered_app_id=state.app.pk)

    assert not arm.identities and not arm.roles
    assert summary["roles_deleted"] == [record.resource_id]
    assert not AzureWorkloadIdentity.objects.exists()


@pytest.mark.parametrize("tags", ["owned", "foreign"])
def test_removal_gets_past_a_replaced_identity_and_never_touches_the_replacement(
    arm: ControlledARM, tags: str
) -> None:
    # The refusal for a replaced identity says: remove the app's workload
    # identity, then deploy. The recorded principal is gone, so removal is done
    # with it; the identity holding the name now is not the platform's to strip.
    state = azure_app()
    app_service(state, "blobs")
    reconcile(state, {"blobs": contributor("data")})
    record = AzureWorkloadIdentity.objects.get()
    name = workload_identity_role_name(state.app)
    # Deleted in Azure, federations with it, and another identity made under the name.
    del arm.identities[name]
    arm.fics.pop(name)
    replacement = arm.seed_identity(name, tags=owner_tags(state) if tags == "owned" else {"team": "data"})
    arm.seed_federation(
        name, "data-ci", issuer="https://ci.example/", subject="repo:data/x", audience=AUDIENCE
    )
    writes = len(arm.writes)

    summary = _deprovision_identity_role_sync(registered_app_id=state.app.pk)

    assert len(arm.writes) == writes, "nothing on the replacement is written"
    assert arm.identities[name] is replacement and set(arm.fics[name]) == {"data-ci"}
    [report] = summary["azure_identities"]
    assert report["replaced"] and report["absent"] and not report["deleted"]
    assert summary["roles_skipped"] == [record.resource_id]
    assert not AzureWorkloadIdentity.objects.exists()
    if tags == "owned":
        reconcile(state, {"blobs": contributor("data")})
        assert AzureWorkloadIdentity.objects.get().principal_id == replacement["properties"]["principalId"]
    else:
        with pytest.raises(AzureIdentityRefused, match="without this app's ownership markers"):
            reconcile(state, {"blobs": contributor("data")})


def test_a_grant_outside_the_clusters_subscription_fails_before_any_request(arm: ControlledARM) -> None:
    # #1367 recorded a grant scoped to another subscription as failed with that
    # reason. It must not read as an unconfirmed reconcile, pending for good,
    # after the identity was already created.
    state = azure_app()
    store = app_service(state, "blobs")
    elsewhere = blob_scope("data").replace(SUBSCRIPTION, "0b9e3c1a-5d2f-4e6a-8b7c-9d0e1f2a3b4c")

    with pytest.raises(AzureWorkloadIdentityError, match="outside"):
        reconcile(state, {"blobs": Binding([Grant(elsewhere, ["Storage Blob Data Contributor"])])})

    assert not arm.calls
    assert not AzureWorkloadIdentity.objects.exists()
    grant = WorkloadIdentityGrant.objects.get(managed_service=store)
    assert grant.state == "failed" and elsewhere in grant.reason
