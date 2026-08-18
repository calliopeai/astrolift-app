"""Tenant Temporal driver, with the control-plane isolation rule as the point (#1473)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk import UnsupportedOperationError
from _sdk.availability import MATRIX
from _sdk.binding_policy import LITERAL, OPERATOR_GENERATED_DRIVERS, SECRET_REF, violation
from _sdk.cluster_capabilities import ClusterCapabilities
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from k8s_native.managed._temporal_isolation import (
    ControlPlaneTemporal,
    TemporalIsolationError,
    addresses_collide,
    normalized_host_port,
)
from k8s_native.managed.workflow_temporal import (
    API_VERSION,
    CLIENT_KIND,
    CLUSTER_KIND,
    CNPG_API_VERSION,
    DRIVER_ID,
    NAMESPACE_KIND,
    REQUIRED_CRDS,
    TemporalConfig,
    TemporalWorkflowEngineDriver,
)
from k8s_native.plugin import PLUGIN
from k8s_native.preflight import preflight

CONTROL_PLANE = ControlPlaneTemporal(
    address="temporal-frontend.astrolift-system.svc.cluster.local:7233",
    namespaces=("default",),
    kubernetes_namespaces=("astrolift-system",),
)

CLUSTER_ID = "tenant-cluster-1"
NAMESPACE = "acme-web"
NAME = "web-prod-engine"
PERSISTENCE = "web-prod-engine-persistence"
CLIENT = "web-prod-engine-client"
POLICY = "web-prod-engine-temporal"
TENANT_NAMESPACE = "acme-web-prod"
OWNER = "ms-0001"
HANDLE = f"workflow_engine/{CLUSTER_ID}/{NAMESPACE}/{NAME}"


@dataclass
class _Result:
    ok: bool = True
    errors: list[str] = field(default_factory=list)

    def summary(self) -> list[str]:
        return list(self.errors)


class _Cluster:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.applied: list[tuple[str, str, list[dict[str, Any]]]] = []
        self.deleted: list[tuple[str, str, list[dict[str, Any]], str | None]] = []
        self.apply_result = _Result()
        self.delete_result = _Result()

    def seed(self, manifest: dict[str, Any]) -> None:
        metadata = manifest["metadata"]
        key = (f"{manifest['apiVersion']}/{manifest['kind']}", metadata["namespace"], metadata["name"])
        self.objects[key] = manifest

    def get_manifest(self, cluster_id, namespace, kind, name):
        del cluster_id
        return self.objects.get((kind, namespace, name))

    def list_manifests(self, cluster_id, namespace, kind):
        del cluster_id
        return [
            manifest
            for (stored_kind, stored_namespace, _name), manifest in self.objects.items()
            if stored_kind == kind and stored_namespace == namespace
        ]

    def apply_manifests(self, cluster_id, namespace, manifests):
        self.applied.append((cluster_id, namespace, manifests))
        if self.apply_result.ok:
            for manifest in manifests:
                self.seed({**manifest, "metadata": {**manifest["metadata"], "namespace": namespace}})
        return self.apply_result

    def delete_manifests(self, cluster_id, namespace, manifests, *, propagation_policy=None):
        self.deleted.append((cluster_id, namespace, manifests, propagation_policy))
        if self.delete_result.ok:
            for manifest in manifests:
                self.objects.pop(
                    (f"{manifest['apiVersion']}/{manifest['kind']}", namespace, manifest["metadata"]["name"]),
                    None,
                )
        return self.delete_result


def _driver(cluster: _Cluster | None = None, **overrides: Any) -> TemporalWorkflowEngineDriver:
    config = TemporalConfig(
        cluster_driver=cluster if cluster is not None else _Cluster(),
        control_plane=CONTROL_PLANE,
        **overrides,
    )
    return TemporalWorkflowEngineDriver(config=config)


def _spec(**overrides: Any) -> ProvisionSpec:
    values: dict[str, Any] = {
        "organization_id": "org-1",
        "organization_slug": "acme",
        "app_id": "app-1",
        "app_slug": "web",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": CLUSTER_ID,
        "service_handle_hint": "engine",
        "size": "small",
        "managed_service_id": OWNER,
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _labels(component: str, owner: str = OWNER, cluster_name: str = NAME) -> dict[str, str]:
    return {
        "app.kubernetes.io/managed-by": "astrolift",
        "astrolift.io/managed-service-id": owner,
        "astrolift.io/component": component,
        "astrolift.io/temporal-cluster": cluster_name,
    }


def _seed_provisioned(
    cluster: _Cluster,
    *,
    namespace: str = NAMESPACE,
    name: str = NAME,
    temporal_namespace: str = TENANT_NAMESPACE,
    owner: str = OWNER,
    issued: bool = True,
) -> None:
    cluster.seed(
        {
            "apiVersion": API_VERSION,
            "kind": CLUSTER_KIND,
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": _labels("temporal-server", owner, name),
                "annotations": {"astrolift.io/temporal-namespace": temporal_namespace},
            },
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
    )
    cluster.seed(
        {
            "apiVersion": API_VERSION,
            "kind": NAMESPACE_KIND,
            "metadata": {
                "name": temporal_namespace,
                "namespace": namespace,
                "labels": _labels("temporal-namespace", owner, name),
            },
            "spec": {"clusterRef": {"name": name}, "retentionPeriod": "72h"},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
    )
    client: dict[str, Any] = {
        "apiVersion": API_VERSION,
        "kind": CLIENT_KIND,
        "metadata": {
            "name": f"{name}-client",
            "namespace": namespace,
            "labels": _labels("temporal-client", owner, name),
        },
        "spec": {"clusterRef": {"name": name}},
    }
    if issued:
        client["status"] = {
            "secretRef": {"name": f"{name}-client-mtls"},
            "serverName": f"{name}-frontend.{namespace}.svc.cluster.local",
        }
    cluster.seed(client)
    cluster.seed(
        {
            "apiVersion": CNPG_API_VERSION,
            "kind": "Cluster",
            "metadata": {
                "name": f"{name}-persistence",
                "namespace": namespace,
                "labels": _labels("temporal-persistence", owner, name),
            },
        }
    )
    cluster.seed(
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": f"{name}-temporal",
                "namespace": namespace,
                "labels": _labels("temporal-network-policy", owner, name),
            },
        }
    )


def _by_kind(manifests: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f"{m['apiVersion']}/{m['kind']}": m for m in manifests}


# ---- address normalization -------------------------------------------------


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("temporal:7233", ("temporal", 7233)),
        ("temporal.astrolift-system.svc.cluster.local:7233", ("temporal.astrolift-system", 7233)),
        ("temporal.astrolift-system.svc:7233", ("temporal.astrolift-system", 7233)),
        ("dns:///Temporal.Astrolift-System:7233", ("temporal.astrolift-system", 7233)),
        ("temporal.astrolift-system", ("temporal.astrolift-system", 7233)),
    ],
)
def test_addresses_reduce_to_a_comparable_host(address: str, expected: tuple[str, int]) -> None:
    assert normalized_host_port(address) == expected


def test_the_same_frontend_written_three_ways_collides() -> None:
    """The control-plane address is operator-written; its spelling must not decide the answer."""
    fqdn = "temporal-frontend.astrolift-system.svc.cluster.local:7233"
    assert addresses_collide(fqdn, "temporal-frontend.astrolift-system:7233")
    assert addresses_collide(fqdn, "temporal-frontend.astrolift-system.svc:7233")
    # A bare label means "whatever namespace is asking", so it cannot be
    # cleared against a qualified host that starts with the same label.
    assert addresses_collide(fqdn, "temporal-frontend:7233")


def test_distinct_frontends_do_not_collide() -> None:
    assert not addresses_collide(
        "web-prod-engine-frontend.acme-web.svc.cluster.local:7233",
        "temporal-frontend.astrolift-system.svc.cluster.local:7233",
    )


# ---- provision -------------------------------------------------------------


def test_provision_renders_server_persistence_namespace_and_client() -> None:
    cluster = _Cluster()
    result = _driver(cluster).provision(_spec())

    assert result.ok is True, result.message
    assert result.handle == HANDLE
    cluster_id, namespace, manifests = cluster.applied[0]
    assert (cluster_id, namespace) == (CLUSTER_ID, NAMESPACE)
    rendered = _by_kind(manifests)
    assert set(rendered) == {
        f"{CNPG_API_VERSION}/Cluster",
        f"{API_VERSION}/{CLUSTER_KIND}",
        f"{API_VERSION}/{NAMESPACE_KIND}",
        f"{API_VERSION}/{CLIENT_KIND}",
        "networking.k8s.io/v1/NetworkPolicy",
    }

    server = rendered[f"{API_VERSION}/{CLUSTER_KIND}"]
    persistence = rendered[f"{CNPG_API_VERSION}/Cluster"]
    stores = server["spec"]["persistence"]
    connect = f"{PERSISTENCE}-rw.{NAMESPACE}.svc.cluster.local:5432"
    assert persistence["metadata"]["name"] == PERSISTENCE
    assert stores["defaultStore"]["sql"]["connectAddr"] == connect
    assert stores["visibilityStore"]["sql"]["connectAddr"] == connect
    assert stores["defaultStore"]["sql"]["databaseName"] != stores["visibilityStore"]["sql"]["databaseName"]
    assert stores["defaultStore"]["passwordSecretRef"] == {"name": f"{PERSISTENCE}-app", "key": "password"}
    assert server["spec"]["mTLS"]["frontend"]["enabled"] is True
    assert server["spec"]["ui"]["enabled"] is False
    assert server["spec"]["admintools"]["enabled"] is False
    assert server["metadata"]["annotations"]["astrolift.io/temporal-namespace"] == TENANT_NAMESPACE

    assert rendered[f"{API_VERSION}/{NAMESPACE_KIND}"]["metadata"]["name"] == TENANT_NAMESPACE
    assert rendered[f"{API_VERSION}/{NAMESPACE_KIND}"]["spec"]["clusterRef"] == {"name": NAME}
    assert rendered[f"{API_VERSION}/{CLIENT_KIND}"]["metadata"]["name"] == CLIENT
    assert rendered["networking.k8s.io/v1/NetworkPolicy"]["metadata"]["name"] == POLICY

    for manifest in manifests:
        labels = manifest["metadata"]["labels"]
        assert labels["app.kubernetes.io/managed-by"] == "astrolift"
        assert labels["astrolift.io/managed-service-id"] == OWNER
        assert labels["astrolift.io/temporal-cluster"] == NAME


def test_persistence_labels_reach_the_volumes_so_teardown_can_find_them() -> None:
    cluster = _Cluster()
    _driver(cluster).provision(_spec())
    persistence = _by_kind(cluster.applied[0][2])[f"{CNPG_API_VERSION}/Cluster"]

    inherited = persistence["spec"]["inheritedMetadata"]["labels"]
    assert inherited["astrolift.io/managed-service-id"] == OWNER
    assert inherited["astrolift.io/component"] == "temporal-persistence"


def test_provision_requires_an_owner_and_a_cluster() -> None:
    cluster = _Cluster()
    assert _driver(cluster).provision(_spec(managed_service_id="")).ok is False
    assert _driver(cluster).provision(_spec(tenant_cluster_id="")).ok is False
    assert cluster.applied == []


def test_provision_refuses_to_adopt_another_bindings_cluster() -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster, owner="ms-other")

    result = _driver(cluster).provision(_spec())

    assert result.ok is False
    assert result.errors == ["invalid_temporal_config"]
    assert cluster.applied == []


# ---- isolation: the negative cases -----------------------------------------


def test_provision_fails_closed_when_the_control_plane_is_unknown() -> None:
    """An install that has not declared its control-plane Temporal gets nothing.

    The driver cannot prove separation from an address it does not have, and
    the safe answer to "cannot prove" is a refusal rather than a default.
    """
    cluster = _Cluster()
    driver = TemporalWorkflowEngineDriver(config=TemporalConfig(cluster_driver=cluster))

    result = driver.provision(_spec())

    assert result.ok is False
    assert result.errors == ["temporal_isolation_violation"]
    assert "not configured" in result.message
    assert cluster.applied == []


def test_provision_refuses_a_control_plane_kubernetes_namespace() -> None:
    cluster = _Cluster()

    result = _driver(cluster).provision(_spec(organization_slug="astrolift", app_slug="system"))

    assert result.ok is False
    assert result.errors == ["temporal_isolation_violation"]
    assert "control-plane infrastructure" in result.message
    assert cluster.applied == []


def test_provision_refuses_a_control_plane_temporal_namespace() -> None:
    cluster = _Cluster()
    driver = TemporalWorkflowEngineDriver(
        config=TemporalConfig(
            cluster_driver=cluster,
            control_plane=ControlPlaneTemporal(
                address=CONTROL_PLANE.address,
                namespaces=("default", "Acme-Web-Prod"),
                kubernetes_namespaces=CONTROL_PLANE.kubernetes_namespaces,
            ),
        )
    )

    result = driver.provision(_spec())

    assert result.ok is False
    assert result.errors == ["temporal_isolation_violation"]
    assert TENANT_NAMESPACE in result.message
    assert cluster.applied == []


def test_provision_refuses_when_the_tenant_frontend_would_be_the_control_plane() -> None:
    """The control plane sharing the tenant's namespace is the collision that matters."""
    cluster = _Cluster()
    driver = TemporalWorkflowEngineDriver(
        config=TemporalConfig(
            cluster_driver=cluster,
            control_plane=ControlPlaneTemporal(
                address=f"{NAME}-frontend.{NAMESPACE}:7233",
                namespaces=("default",),
            ),
        )
    )

    result = driver.provision(_spec())

    assert result.ok is False
    assert result.errors == ["temporal_isolation_violation"]
    assert cluster.applied == []


def test_binding_refuses_a_handle_pointing_at_the_control_plane() -> None:
    """Ownership labels do not answer this question, so the isolation rule must.

    The control-plane Temporal is deployed by Astrolift, so it plausibly
    carries the ownership labels; what a corrupted or crafted handle must never
    buy is a workload environment aimed at it.
    """
    cluster = _Cluster()
    _seed_provisioned(cluster, namespace="astrolift-system", name="temporal", temporal_namespace="default")
    driver = _driver(cluster)

    with pytest.raises(TemporalIsolationError, match="control-plane infrastructure"):
        driver.binding(ServiceHandle(handle="workflow_engine/tenant-cluster-1/astrolift-system/temporal"))


def test_binding_refuses_a_cluster_annotated_with_a_control_plane_namespace() -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster, temporal_namespace="default")
    driver = _driver(cluster)

    with pytest.raises(TemporalIsolationError, match="control-plane namespace"):
        driver.binding(ServiceHandle(handle=HANDLE))


def test_deprovision_refuses_a_handle_pointing_at_the_control_plane() -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster, namespace="astrolift-system", name="temporal", temporal_namespace="default")
    driver = _driver(cluster)

    result = driver.deprovision(
        DeprovisionSpec(handle="workflow_engine/tenant-cluster-1/astrolift-system/temporal"),
    )

    assert result.ok is False
    assert result.retryable is False
    assert result.errors == ["temporal_isolation_violation"]
    assert cluster.deleted == []


def test_status_reports_an_isolation_violation_rather_than_a_health_answer() -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster, temporal_namespace="default")

    status = _driver(cluster).status(ServiceHandle(handle=HANDLE))

    assert status.state == "error"
    assert "control-plane namespace" in status.message


# ---- binding ---------------------------------------------------------------


def test_binding_emits_this_tenants_coordinates_and_never_the_control_plane() -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster)

    binding = _driver(cluster).binding(ServiceHandle(handle=HANDLE))
    env = binding.env_vars

    assert env["TEMPORAL_ADDRESS"].literal == f"{NAME}-frontend.{NAMESPACE}.svc.cluster.local:7233"
    assert env["TEMPORAL_NAMESPACE"].literal == TENANT_NAMESPACE
    assert env["WORKFLOW_ENGINE_TYPE"].literal == "TEMPORAL"
    assert env["WORKFLOW_ENGINE_ARN"].literal == f"k8s://{CLUSTER_ID}/{NAMESPACE}/temporalcluster/{NAME}"

    emitted = [ref.literal or ref.secret_ref or "" for ref in env.values()]
    assert not any(addresses_collide(value, CONTROL_PLANE.address) for value in emitted if "-frontend." in value)
    assert CONTROL_PLANE.namespaces[0] not in emitted


def test_binding_references_the_operator_issued_certificate_instead_of_inlining_it() -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster)

    env = _driver(cluster).binding(ServiceHandle(handle=HANDLE)).env_vars

    for key in ("TEMPORAL_TLS_CLIENT_CERT", "TEMPORAL_TLS_CLIENT_KEY", "TEMPORAL_TLS_CA_CERT"):
        assert env[key].literal is None
        assert env[key].secret_ref.startswith(f"{NAME}-client-mtls#")
        assert violation(key, DRIVER_ID, SECRET_REF) is None
    assert violation("TEMPORAL_TLS_CLIENT_KEY", DRIVER_ID, LITERAL) is not None
    assert DRIVER_ID in OPERATOR_GENERATED_DRIVERS


def test_binding_waits_for_the_certificate_rather_than_emitting_a_half_binding() -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster, issued=False)

    with pytest.raises(ValueError, match="not issued yet"):
        _driver(cluster).binding(ServiceHandle(handle=HANDLE))


def test_binding_refuses_a_cluster_astrolift_does_not_own() -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster)
    key = (f"{API_VERSION}/{CLUSTER_KIND}", NAMESPACE, NAME)
    cluster.objects[key]["metadata"]["labels"] = {"app.kubernetes.io/managed-by": "someone-else"}

    with pytest.raises(ValueError, match="not managed by Astrolift"):
        _driver(cluster).binding(ServiceHandle(handle=HANDLE))


# ---- teardown --------------------------------------------------------------


def test_deprovision_removes_every_owned_object_including_ones_the_handle_cannot_name() -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster)

    result = _driver(cluster).deprovision(DeprovisionSpec(handle=HANDLE, managed_service_id=OWNER))

    assert result.ok is True, result.message
    deleted = {(m["kind"], m["metadata"]["name"]) for _, _, manifests, _ in cluster.deleted for m in manifests}
    assert deleted == {
        (CLUSTER_KIND, NAME),
        (NAMESPACE_KIND, TENANT_NAMESPACE),
        (CLIENT_KIND, CLIENT),
        ("Cluster", PERSISTENCE),
        ("NetworkPolicy", POLICY),
    }
    assert cluster.objects == {}


def test_deprovision_leaves_another_bindings_temporal_alone() -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster)

    result = _driver(cluster).deprovision(DeprovisionSpec(handle=HANDLE, managed_service_id="ms-other"))

    assert result.ok is False
    assert result.retryable is False
    assert cluster.deleted == []


def test_deprovision_with_delete_data_removes_the_history_volumes_by_owner() -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster)
    for ordinal, owner in ((1, OWNER), (2, OWNER), (1, "ms-other")):
        cluster.seed(
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {
                    "name": f"claim-{owner}-{ordinal}",
                    "namespace": NAMESPACE,
                    "labels": _labels("temporal-persistence", owner),
                },
            }
        )

    _driver(cluster).deprovision(DeprovisionSpec(handle=HANDLE), delete_data=True)

    claims = {
        m["metadata"]["name"]
        for _, _, manifests, _ in cluster.deleted
        for m in manifests
        if m["kind"] == "PersistentVolumeClaim"
    }
    assert claims == {f"claim-{OWNER}-1", f"claim-{OWNER}-2"}


def test_deprovision_retains_the_history_volumes_by_default() -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster)
    cluster.seed(
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": "claim-1",
                "namespace": NAMESPACE,
                "labels": _labels("temporal-persistence"),
            },
        }
    )

    result = _driver(cluster).deprovision(DeprovisionSpec(handle=HANDLE))

    kinds = {m["kind"] for _, _, manifests, _ in cluster.deleted for m in manifests}
    assert "PersistentVolumeClaim" not in kinds
    assert "retained" in result.message


def test_deprovision_is_idempotent_when_nothing_is_left() -> None:
    cluster = _Cluster()

    result = _driver(cluster).deprovision(DeprovisionSpec(handle=HANDLE))

    assert result.ok is True
    assert cluster.deleted == []


# ---- update ----------------------------------------------------------------


def test_update_patches_retention_on_the_tenant_namespace() -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster)

    result = _driver(cluster).update(UpdateSpec(handle=HANDLE, config={"retention_period": "168h"}))

    assert result.ok is True, result.message
    applied = cluster.applied[-1][2]
    assert len(applied) == 1
    assert applied[0]["kind"] == NAMESPACE_KIND
    assert applied[0]["spec"]["retentionPeriod"] == "168h"
    assert applied[0]["metadata"]["labels"]["astrolift.io/temporal-cluster"] == NAME


@pytest.mark.parametrize("retention", ["1h", "23h", "9000h", "3d", "72", ""])
def test_update_rejects_a_retention_temporal_would_not_accept(retention: str) -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster)

    result = _driver(cluster).update(UpdateSpec(handle=HANDLE, config={"retention_period": retention}))

    assert result.ok is False
    assert result.retryable is False
    assert cluster.applied == []


def test_update_rejects_unknown_config_keys() -> None:
    cluster = _Cluster()
    _seed_provisioned(cluster)

    result = _driver(cluster).update(UpdateSpec(handle=HANDLE, config={"num_history_shards": 4096}))

    assert result.ok is False
    assert "num_history_shards" in result.message
    assert cluster.applied == []


def test_editable_fields_are_only_what_update_can_apply() -> None:
    assert _driver().editable_fields() == ["retention_period"]


def test_snapshot_is_refused_rather_than_faked() -> None:
    with pytest.raises(UnsupportedOperationError):
        _driver().snapshot(ServiceHandle(handle=HANDLE))


# ---- registration ----------------------------------------------------------


def test_driver_is_registered_and_described_consistently() -> None:
    assert PLUGIN.managed_service_drivers[("workflow_engine", "temporal")] is TemporalWorkflowEngineDriver
    entry = next(
        row
        for row in MATRIX.managed_services
        if (row.plugin_id, row.kind, row.variant) == ("k8s_native", "workflow_engine", "temporal")
    )
    assert set(entry.binding_envs) == set(_driver().binding_schema().env_vars)


def test_preflight_requires_the_operator_cnpg_and_cert_manager() -> None:
    def _caps(crds: tuple[str, ...]) -> ClusterCapabilities:
        return ClusterCapabilities(
            cluster_id=CLUSTER_ID,
            kubernetes_version="1.31",
            installed_crds=frozenset(crds),
            crd_inventory_probed=True,
        )

    without_cnpg = tuple(crd for crd in REQUIRED_CRDS if crd != "clusters.postgresql.cnpg.io")
    partial = preflight(kind="workflow_engine", variant="temporal", capabilities=_caps(without_cnpg))
    assert partial.ok is False
    assert [failure.code for failure in partial.failures] == ["missing_crd"]
    assert "clusters.postgresql.cnpg.io" in partial.failures[0].message

    present = preflight(kind="workflow_engine", variant="temporal", capabilities=_caps(REQUIRED_CRDS))
    assert present.ok is True
