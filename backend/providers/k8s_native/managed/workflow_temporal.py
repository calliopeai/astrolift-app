"""Tenant-facing Temporal implementation of the portable workflow-engine contract (#1473).

Step Functions, GCP Workflows and Argo Workflows are three vocabularies with
nothing in common, so the portable contract across them can only ever be their
intersection. Temporal provisioned in-cluster is portable by construction --
the same driver runs on every cloud -- and it is the one orchestration stack
Astrolift already operates, so its failure modes, upgrades and observability
are known rather than three separate research problems.

The instance this driver provisions is **not** the control-plane Temporal. It
is a separate server with its own persistence and its own namespace, created
per binding, and keeping it separate is the security property of this driver in
the way ownership verification is the security property of the Azure managed
drivers. ``_temporal_isolation`` holds that rule and every mutating and
credential-emitting entry point runs it before doing anything else.

Persistence is driver-managed, not a separate managed ``postgres`` binding
------------------------------------------------------------------------
Both were available and the split is not close:

* A ``postgres`` binding is *bindable*. Its whole purpose is to emit
  ``DATABASE_URL`` into a workload, which would hand the tenant direct SQL
  access to the history store of its own orchestrator -- every workflow input,
  every activity result, and the schema the Temporal server owns and migrates.
  Isolation is the point of this driver, so the store must not be addressable.
* A ``postgres`` binding is *independently deletable*. Two rows with two
  lifecycles produce exactly the orphan this ticket forbids: a store that
  outlives its server, or a server whose store is deleted underneath it.
  Driver-managed persistence is created and torn down inside one deprovision,
  under one set of ownership labels.
* Temporal's operator owns the schema. It runs create-database and
  setup-schema jobs against the store, which is not something a shared or
  operator-configured Postgres should have done to it behind its back.

So the driver emits its own CNPG ``Cluster`` alongside the ``TemporalCluster``,
carrying the same owner labels, and CNPG's reclaim policy governs the PVCs the
same way it does for the ``postgres/cnpg`` driver.

Credentials
-----------
Frontend mTLS is on, the operator issues the client certificate through
cert-manager, and the binding references the generated Secret rather than
inlining it (``_sdk/binding_policy``: the operator generates and rotates the
value, so the driver never learns it). That certificate is signed by *this*
cluster's CA, which is the second half of the isolation story: even a workload
that somehow learned the control-plane address cannot authenticate to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, cast

from _sdk import UnsupportedOperationError
from _sdk._telemetry import driver_op
from _sdk.k8s_naming import agent_namespace, app_namespace, dns_label
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    ManagedServiceDriver,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
)
from k8s_native.managed._handle import ParsedHandle
from k8s_native.managed._handle import pack as _pack_handle
from k8s_native.managed._handle import unpack as _unpack_handle
from k8s_native.managed._temporal_isolation import (
    DEFAULT_FRONTEND_PORT,
    ControlPlaneTemporal,
    TemporalIsolationError,
    assert_isolated,
)

KIND = "workflow_engine"
VARIANT = "temporal"
DRIVER_ID = "k8s_native/workflow_engine/temporal"

API_VERSION = "temporal.io/v1beta1"
CLUSTER_KIND = "TemporalCluster"
NAMESPACE_KIND = "TemporalNamespace"
CLIENT_KIND = "TemporalClusterClient"
CNPG_API_VERSION = "postgresql.cnpg.io/v1"
CNPG_KIND = "Cluster"
NETWORK_POLICY_API_VERSION = "networking.k8s.io/v1"
NETWORK_POLICY_KIND = "NetworkPolicy"

REQUIRED_CRDS = (
    "temporalclusters.temporal.io",
    "temporalnamespaces.temporal.io",
    "temporalclusterclients.temporal.io",
    "clusters.postgresql.cnpg.io",
    "certificates.cert-manager.io",
)

DEFAULT_SERVER_VERSION = "1.27.2"
DEFAULT_RETENTION_PERIOD = "72h"
DEFAULT_POSTGRES_IMAGE = "ghcr.io/cloudnative-pg/postgresql:16"

#: Temporal's SQL plugin name for PostgreSQL 12 and newer wire protocol.
_POSTGRES_PLUGIN = "postgres12"
_POSTGRES_PORT = 5432
_DEFAULT_STORE_DATABASE = "temporal"
_VISIBILITY_STORE_DATABASE = "temporal_visibility"

_OWNER = "app.kubernetes.io/managed-by"
_OWNER_ID = "astrolift.io/managed-service-id"
_COMPONENT = "astrolift.io/component"
_TEMPORAL_NAMESPACE_ANNOTATION = "astrolift.io/temporal-namespace"

_SERVER_COMPONENT = "temporal-server"
_PERSISTENCE_COMPONENT = "temporal-persistence"
_NAMESPACE_COMPONENT = "temporal-namespace"
_CLIENT_COMPONENT = "temporal-client"
_POLICY_COMPONENT = "temporal-network-policy"

#: Hours only, because Temporal rejects a namespace retention below one day and
#: an hour count is the one duration unit where that bound is obvious to read.
_RETENTION = re.compile(r"^([0-9]{1,5})h$")
_MIN_RETENTION_HOURS = 24
_MAX_RETENTION_HOURS = 8760

_CONFIG_FIELDS = frozenset({"retention_period"})

SIZE_TO_SPEC: dict[str, dict[str, Any]] = {
    "small": {
        "replicas": 1,
        "history_shards": 512,
        "resources": {
            "requests": {"cpu": "100m", "memory": "256Mi"},
            "limits": {"cpu": "1", "memory": "1Gi"},
        },
        "postgres_instances": 1,
        "postgres_storage": "10Gi",
    },
    "medium": {
        "replicas": 2,
        "history_shards": 512,
        "resources": {
            "requests": {"cpu": "500m", "memory": "1Gi"},
            "limits": {"cpu": "2", "memory": "4Gi"},
        },
        "postgres_instances": 2,
        "postgres_storage": "50Gi",
    },
    "large": {
        "replicas": 3,
        "history_shards": 1024,
        "resources": {
            "requests": {"cpu": "1", "memory": "2Gi"},
            "limits": {"cpu": "4", "memory": "8Gi"},
        },
        "postgres_instances": 3,
        "postgres_storage": "100Gi",
    },
    "xlarge": {
        "replicas": 5,
        "history_shards": 2048,
        "resources": {
            "requests": {"cpu": "2", "memory": "4Gi"},
            "limits": {"cpu": "8", "memory": "16Gi"},
        },
        "postgres_instances": 3,
        "postgres_storage": "500Gi",
    },
}


@dataclass(frozen=True)
class TemporalConfig:
    cluster_driver: Any = None
    control_plane: ControlPlaneTemporal = field(default_factory=ControlPlaneTemporal)
    server_version: str = DEFAULT_SERVER_VERSION
    postgres_image: str = DEFAULT_POSTGRES_IMAGE
    storage_class: str = ""


class TemporalWorkflowEngineDriver(ManagedServiceDriver):
    def __init__(self, *, config: TemporalConfig | None = None) -> None:
        self._config = config or TemporalConfig()

    # ---- lifecycle -------------------------------------------------

    @driver_op(
        cloud="k8s_native",
        driver="workflow_temporal",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            self._require_driver()
            if not spec.managed_service_id:
                raise ValueError("a tenant Temporal requires a managed_service_id for safe ownership")
            if not spec.tenant_cluster_id:
                raise ValueError("a tenant Temporal requires a tenant_cluster_id")
            namespace = app_namespace(
                organization_slug=spec.organization_slug,
                app_slug=spec.app_slug,
            )
            name = self._cluster_name(spec)
            temporal_namespace = self._temporal_namespace(spec)
            retention = self._retention(spec.config)
            self._assert_isolated(
                kubernetes_namespace=namespace,
                temporal_namespace=temporal_namespace,
                name=name,
            )
            self._assert_adoptable(
                cluster_id=spec.tenant_cluster_id,
                namespace=namespace,
                name=name,
                managed_service_id=spec.managed_service_id,
            )
            manifests = self._manifests(
                spec=spec,
                namespace=namespace,
                name=name,
                temporal_namespace=temporal_namespace,
                retention=retention,
            )
        except (TypeError, ValueError) as exc:
            return ProvisionResult(False, "", str(exc), [_error_code(exc)])

        handle = _pack_handle(
            kind=KIND,
            cluster_id=spec.tenant_cluster_id,
            namespace=namespace,
            name=name,
        )
        result = self._config.cluster_driver.apply_manifests(
            spec.tenant_cluster_id,
            namespace,
            manifests,
        )
        if not result.ok:
            return ProvisionResult(False, handle, "tenant Temporal resources were rejected", result.summary())
        return ProvisionResult(
            True,
            handle,
            f"tenant Temporal {namespace}/{name} applied; namespace {temporal_namespace} reconciles asynchronously",
        )

    @driver_op(cloud="k8s_native", driver="workflow_temporal")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            self._require_driver()
            parsed = self._parsed(spec.handle)
            cluster = self._cluster(parsed)
            if cluster is None:
                return UpdateResult(
                    False,
                    spec.handle,
                    "tenant Temporal does not exist",
                    ["resource_not_found"],
                    retryable=False,
                )
            owner = self._assert_owned(cluster, CLUSTER_KIND)
            if spec.managed_service_id and spec.managed_service_id != owner:
                raise ValueError(f"tenant Temporal {parsed.name} belongs to managed service {owner}")
            temporal_namespace = self._annotated_namespace(cluster)
            self._assert_isolated(
                kubernetes_namespace=parsed.namespace,
                temporal_namespace=temporal_namespace,
                name=parsed.name,
            )
            retention = self._retention(spec.config)
            manifest = self._namespace_manifest(
                namespace=parsed.namespace,
                cluster_name=parsed.name,
                temporal_namespace=temporal_namespace,
                retention=retention,
                labels=self._labels(owner, _NAMESPACE_COMPONENT, parsed.name),
            )
        except (TypeError, ValueError) as exc:
            return UpdateResult(False, spec.handle, str(exc), [_error_code(exc)], retryable=False)

        result = self._config.cluster_driver.apply_manifests(
            parsed.cluster_id,
            parsed.namespace,
            [manifest],
        )
        if not result.ok:
            return UpdateResult(False, spec.handle, "tenant Temporal namespace update was rejected", result.summary())
        return UpdateResult(True, spec.handle, f"Temporal namespace {temporal_namespace} retention set to {retention}")

    @driver_op(
        cloud="k8s_native",
        driver="workflow_temporal",
        audit=True,
        sensitive_kind="managed_service_deprovision",
    )
    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        del force_destroy
        try:
            self._require_driver()
            parsed = self._parsed(spec.handle)
            owned = self._owned_resources(parsed)
            if not owned:
                return DeprovisionResult(True, spec.handle, "tenant Temporal already absent")
            owner = self._sole_owner(owned)
            if spec.managed_service_id and spec.managed_service_id != owner:
                raise ValueError(f"tenant Temporal {parsed.name} belongs to managed service {owner}")
            self._assert_isolated(
                kubernetes_namespace=parsed.namespace,
                temporal_namespace=self._owned_namespace_name(owned),
                name=parsed.name,
            )
        except (TypeError, ValueError) as exc:
            return DeprovisionResult(False, spec.handle, str(exc), [_error_code(exc)], retryable=False)

        stubs = [_stub(api_version, kind, name, parsed.namespace) for api_version, kind, name, _ in owned]
        if delete_data:
            stubs.extend(self._persistence_claim_stubs(parsed, owner))
        result = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            stubs,
            propagation_policy="Background",
        )
        if not result.ok:
            return DeprovisionResult(False, spec.handle, "tenant Temporal deletion failed", result.summary())
        retention = "history volumes deleted" if delete_data else "history volumes retained by CNPG reclaim policy"
        return DeprovisionResult(True, spec.handle, f"tenant Temporal {parsed.name} deleted; {retention}")

    # ---- read-only -------------------------------------------------

    @driver_op(cloud="k8s_native", driver="workflow_temporal")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            self._require_driver()
            parsed = self._parsed(handle.handle)
            cluster = self._cluster(parsed)
            if cluster is None:
                return ServiceStatus(handle.handle, "deprovisioned", "tenant Temporal not found")
            self._assert_owned(cluster, CLUSTER_KIND)
            temporal_namespace = self._annotated_namespace(cluster)
            self._assert_isolated(
                kubernetes_namespace=parsed.namespace,
                temporal_namespace=temporal_namespace,
                name=parsed.name,
            )
        except (TypeError, ValueError) as exc:
            return ServiceStatus(handle.handle, "error", str(exc))
        if not _condition_true(cluster, "Ready"):
            return ServiceStatus(handle.handle, "provisioning", f"TemporalCluster {parsed.name} is not ready yet")
        tenant_namespace = self._get(parsed, f"{API_VERSION}/{NAMESPACE_KIND}", temporal_namespace)
        if tenant_namespace is None or not _condition_true(tenant_namespace, "Ready"):
            return ServiceStatus(
                handle.handle,
                "provisioning",
                f"Temporal namespace {temporal_namespace} is not ready yet",
            )
        return ServiceStatus(handle.handle, "available", f"tenant Temporal {parsed.name} available")

    @driver_op(cloud="k8s_native", driver="workflow_temporal")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        self._require_driver()
        parsed = self._parsed(handle.handle)
        cluster = self._cluster(parsed)
        if cluster is None:
            raise ValueError("tenant Temporal does not exist")
        self._assert_owned(cluster, CLUSTER_KIND)
        temporal_namespace = self._annotated_namespace(cluster)
        # The last gate before coordinates reach a workload: everything below
        # this line ends up in the pod's environment.
        self._assert_isolated(
            kubernetes_namespace=parsed.namespace,
            temporal_namespace=temporal_namespace,
            name=parsed.name,
        )
        client = self._get(parsed, f"{API_VERSION}/{CLIENT_KIND}", _client_name(parsed.name))
        if client is None:
            raise ValueError("tenant Temporal client certificate has not been requested")
        self._assert_owned(client, CLIENT_KIND)
        status = cast("dict[str, Any]", client.get("status") or {})
        secret_name = str((status.get("secretRef") or {}).get("name") or "")
        server_name = str(status.get("serverName") or "")
        if not secret_name or not server_name:
            raise ValueError("tenant Temporal client certificate is not issued yet")
        address = frontend_address(parsed.name, parsed.namespace)
        return Binding(
            env_vars={
                "WORKFLOW_ENGINE_ID": ValueRef(literal=parsed.name),
                "WORKFLOW_ENGINE_ARN": ValueRef(
                    literal=f"k8s://{parsed.cluster_id}/{parsed.namespace}/temporalcluster/{parsed.name}",
                ),
                "WORKFLOW_ENGINE_REGION": ValueRef(literal="kubernetes"),
                "WORKFLOW_ENGINE_TYPE": ValueRef(literal="TEMPORAL"),
                "TEMPORAL_ADDRESS": ValueRef(literal=address),
                "TEMPORAL_NAMESPACE": ValueRef(literal=temporal_namespace),
                "TEMPORAL_TLS_SERVER_NAME": ValueRef(literal=server_name),
                # cert-manager writes and rotates all three through the
                # operator, so the driver never holds the values.
                "TEMPORAL_TLS_CA_CERT": ValueRef(secret_ref=f"{secret_name}#ca.crt"),
                "TEMPORAL_TLS_CLIENT_CERT": ValueRef(secret_ref=f"{secret_name}#tls.crt"),
                "TEMPORAL_TLS_CLIENT_KEY": ValueRef(secret_ref=f"{secret_name}#tls.key"),
            },
            notes=(
                "Dedicated tenant Temporal. The client certificate is signed by this cluster's own CA and "
                "authenticates to this frontend only; Astrolift's control-plane Temporal is a different server "
                "and is never reachable with these credentials."
            ),
        )

    @driver_op(cloud="k8s_native", driver="workflow_temporal")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise UnsupportedOperationError(
            "Temporal has no point-in-time export: a consistent copy means quiescing the cluster and "
            "snapshotting its persistence, which is a cluster-level operation rather than a binding one"
        )

    @driver_op(cloud="k8s_native", driver="workflow_temporal")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            False,
            "",
            "tenant Temporal cannot be restored from a snapshot; provision a new cluster",
            ["not_implemented_in_driver"],
        )

    @driver_op(cloud="k8s_native", driver="workflow_temporal", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "retention_period": {
                    "type": "string",
                    "pattern": r"^[0-9]{1,5}h$",
                    "default": DEFAULT_RETENTION_PERIOD,
                    "description": (
                        f"Closed-workflow history retention, in whole hours "
                        f"({_MIN_RETENTION_HOURS}h-{_MAX_RETENTION_HOURS}h)."
                    ),
                },
            },
        }

    @driver_op(cloud="k8s_native", driver="workflow_temporal", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "WORKFLOW_ENGINE_ID": "TemporalCluster name",
                "WORKFLOW_ENGINE_ARN": "k8s:// locator for the TemporalCluster",
                "WORKFLOW_ENGINE_REGION": "kubernetes",
                "WORKFLOW_ENGINE_TYPE": "TEMPORAL",
                "TEMPORAL_ADDRESS": "host:port of this tenant's Temporal frontend",
                "TEMPORAL_NAMESPACE": "Temporal namespace this binding may address",
                "TEMPORAL_TLS_SERVER_NAME": "Server name to verify the frontend certificate against",
                "TEMPORAL_TLS_CA_CERT": "CA that signed the frontend certificate",
                "TEMPORAL_TLS_CLIENT_CERT": "Client certificate issued for this binding",
                "TEMPORAL_TLS_CLIENT_KEY": "Private key for the client certificate",
            }
        )

    def editable_fields(self) -> list[str]:
        """Retention is a live patch of the TemporalNamespace CR.

        Everything else -- history shard count above all, which Temporal
        cannot change on an existing cluster -- needs a reprovision.
        """
        return ["retention_period"]

    # ---- isolation + ownership -------------------------------------

    def _assert_isolated(self, *, kubernetes_namespace: str, temporal_namespace: str, name: str) -> None:
        assert_isolated(
            control_plane=self._config.control_plane,
            kubernetes_namespace=kubernetes_namespace,
            temporal_namespace=temporal_namespace,
            frontend_address=frontend_address(name, kubernetes_namespace),
        )

    def _assert_adoptable(
        self,
        *,
        cluster_id: str,
        namespace: str,
        name: str,
        managed_service_id: str,
    ) -> None:
        existing = self._config.cluster_driver.get_manifest(
            cluster_id,
            namespace,
            f"{API_VERSION}/{CLUSTER_KIND}",
            name,
        )
        if existing is None:
            return
        labels = _labels_of(existing)
        if labels.get(_OWNER) == "astrolift" and labels.get(_OWNER_ID) == managed_service_id:
            return
        raise ValueError(f"TemporalCluster {namespace}/{name} already exists and is not owned by this managed service")

    def _assert_owned(self, resource: dict[str, Any], kind: str) -> str:
        labels = _labels_of(resource)
        owner = str(labels.get(_OWNER_ID) or "")
        if labels.get(_OWNER) != "astrolift" or not owner:
            name = str((resource.get("metadata") or {}).get("name") or "")
            raise ValueError(f"{kind} {name} is not managed by Astrolift")
        return owner

    def _sole_owner(self, owned: list[tuple[str, str, str, str]]) -> str:
        owners = {owner for *_, owner in owned}
        if len(owners) != 1:
            raise ValueError(f"tenant Temporal resources carry conflicting owners: {sorted(owners)}")
        return owners.pop()

    # ---- cluster reads ---------------------------------------------

    def _require_driver(self) -> None:
        if self._config.cluster_driver is None:
            raise ValueError("a tenant Temporal requires a live cluster driver")

    @staticmethod
    def _parsed(handle: str) -> ParsedHandle:
        parsed = _unpack_handle(handle)
        if parsed.is_legacy:
            raise ValueError("legacy tenant Temporal handle has no cluster locator")
        return parsed

    def _get(self, parsed: ParsedHandle, kind: str, name: str) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._config.cluster_driver.get_manifest(parsed.cluster_id, parsed.namespace, kind, name),
        )

    def _cluster(self, parsed: ParsedHandle) -> dict[str, Any] | None:
        return self._get(parsed, f"{API_VERSION}/{CLUSTER_KIND}", parsed.name)

    @staticmethod
    def _annotated_namespace(cluster: dict[str, Any]) -> str:
        annotations = (cluster.get("metadata") or {}).get("annotations") or {}
        value = str(annotations.get(_TEMPORAL_NAMESPACE_ANNOTATION) or "")
        if not value:
            raise ValueError(
                "TemporalCluster does not record which Temporal namespace it serves; "
                "reprovision to restore the annotation rather than guessing one"
            )
        return value

    def _owned_resources(self, parsed: ParsedHandle) -> list[tuple[str, str, str, str]]:
        """Every object this binding owns, as ``(apiVersion, kind, name, owner)``.

        Listed rather than reconstructed from the handle so a partially applied
        provision leaves nothing behind: the handle carries the cluster name,
        not the names of the namespace CR, the client CR or the CNPG cluster.
        """
        found: list[tuple[str, str, str, str]] = []
        for api_version, kind in (
            (API_VERSION, NAMESPACE_KIND),
            (API_VERSION, CLIENT_KIND),
            (NETWORK_POLICY_API_VERSION, NETWORK_POLICY_KIND),
            (API_VERSION, CLUSTER_KIND),
            (CNPG_API_VERSION, CNPG_KIND),
        ):
            rows = self._config.cluster_driver.list_manifests(
                parsed.cluster_id,
                parsed.namespace,
                f"{api_version}/{kind}",
            )
            for row in rows or []:
                labels = _labels_of(row)
                owner = str(labels.get(_OWNER_ID) or "")
                if labels.get(_OWNER) != "astrolift" or not owner:
                    continue
                if labels.get("astrolift.io/temporal-cluster") != parsed.name:
                    continue
                found.append((api_version, kind, str((row.get("metadata") or {}).get("name") or ""), owner))
        return found

    @staticmethod
    def _owned_namespace_name(owned: list[tuple[str, str, str, str]]) -> str:
        for _, kind, name, _ in owned:
            if kind == NAMESPACE_KIND:
                return name
        # No TemporalNamespace left to check; the k8s-namespace and address
        # halves of the isolation rule still have to pass, so pass a value that
        # cannot match a control-plane namespace instead of skipping the check.
        return ""

    def _persistence_claim_stubs(self, parsed: ParsedHandle, owner: str) -> list[dict[str, Any]]:
        """PVC stubs for the history store, resolved by label.

        CNPG names claims after its own instance ordinals, which the handle
        does not carry, and ``inheritedMetadata`` on the Cluster stamps the
        binding's owner label onto each one.
        """
        claims = self._config.cluster_driver.list_manifests(
            parsed.cluster_id,
            parsed.namespace,
            "v1/PersistentVolumeClaim",
        )
        stubs: list[dict[str, Any]] = []
        for claim in claims or []:
            labels = _labels_of(claim)
            if labels.get(_OWNER_ID) != owner or labels.get(_COMPONENT) != _PERSISTENCE_COMPONENT:
                continue
            stubs.append(
                _stub(
                    "v1",
                    "PersistentVolumeClaim",
                    str((claim.get("metadata") or {}).get("name") or ""),
                    parsed.namespace,
                )
            )
        return stubs

    # ---- naming ----------------------------------------------------

    @staticmethod
    def _cluster_name(spec: ProvisionSpec) -> str:
        # The operator suffixes this name per service (``-frontend``,
        # ``-history``) and CNPG suffixes the persistence cluster again, so
        # leave room under the 63-character label limit.
        return dns_label(
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "temporal",
            max_length=40,
        )

    @staticmethod
    def _temporal_namespace(spec: ProvisionSpec) -> str:
        return dns_label(spec.organization_slug, spec.app_slug, spec.environment_name)

    # ---- config ----------------------------------------------------

    @staticmethod
    def _retention(raw: dict[str, Any] | None) -> str:
        cfg = dict(raw or {})
        unknown = sorted(set(cfg) - _CONFIG_FIELDS)
        if unknown:
            raise ValueError(f"unsupported tenant Temporal config keys: {', '.join(unknown)}")
        # An absent key means "default"; a present but empty one is an operator
        # clearing the field, and silently resetting their retention to the
        # default is the wrong answer to that.
        value = DEFAULT_RETENTION_PERIOD if "retention_period" not in cfg else str(cfg["retention_period"])
        match = _RETENTION.match(value)
        if match is None:
            raise ValueError(f"retention_period must be whole hours such as '72h'; got {value!r}")
        hours = int(match.group(1))
        if not _MIN_RETENTION_HOURS <= hours <= _MAX_RETENTION_HOURS:
            raise ValueError(
                f"retention_period must be between {_MIN_RETENTION_HOURS}h and {_MAX_RETENTION_HOURS}h; got {value!r}"
            )
        return value

    # ---- manifests -------------------------------------------------

    def _labels(self, owner: str, component: str, cluster_name: str = "") -> dict[str, str]:
        labels = {
            _OWNER: "astrolift",
            _OWNER_ID: owner,
            _COMPONENT: component,
        }
        if cluster_name:
            labels["astrolift.io/temporal-cluster"] = cluster_name
        return labels

    def _manifests(
        self,
        *,
        spec: ProvisionSpec,
        namespace: str,
        name: str,
        temporal_namespace: str,
        retention: str,
    ) -> list[dict[str, Any]]:
        size = SIZE_TO_SPEC.get(spec.size, SIZE_TO_SPEC["small"])
        owner = spec.managed_service_id
        persistence_name = _persistence_name(name)
        return [
            self._persistence_manifest(
                namespace=namespace,
                name=persistence_name,
                size=size,
                labels=self._labels(owner, _PERSISTENCE_COMPONENT, name),
            ),
            self._cluster_manifest(
                namespace=namespace,
                name=name,
                persistence_name=persistence_name,
                temporal_namespace=temporal_namespace,
                size=size,
                labels=self._labels(owner, _SERVER_COMPONENT, name),
            ),
            self._namespace_manifest(
                namespace=namespace,
                cluster_name=name,
                temporal_namespace=temporal_namespace,
                retention=retention,
                labels=self._labels(owner, _NAMESPACE_COMPONENT, name),
            ),
            self._client_manifest(
                namespace=namespace,
                cluster_name=name,
                labels=self._labels(owner, _CLIENT_COMPONENT, name),
            ),
            self._network_policy_manifest(
                namespace=namespace,
                cluster_name=name,
                organization_slug=spec.organization_slug,
                labels=self._labels(owner, _POLICY_COMPONENT, name),
            ),
        ]

    def _persistence_manifest(
        self,
        *,
        namespace: str,
        name: str,
        size: dict[str, Any],
        labels: dict[str, str],
    ) -> dict[str, Any]:
        manifest: dict[str, Any] = {
            "apiVersion": CNPG_API_VERSION,
            "kind": CNPG_KIND,
            "metadata": {"name": name, "namespace": namespace, "labels": labels},
            "spec": {
                "instances": size["postgres_instances"],
                "imageName": self._config.postgres_image,
                # Stamped onto the PVCs so teardown can find the history
                # volumes by owner rather than by CNPG's instance ordinals.
                "inheritedMetadata": {"labels": dict(labels)},
                "storage": {"size": size["postgres_storage"]},
                "bootstrap": {
                    "initdb": {
                        "database": _DEFAULT_STORE_DATABASE,
                        "owner": "app",
                        # The Temporal operator's schema jobs issue
                        # CREATE DATABASE for the visibility store, which the
                        # CNPG application role cannot do by default.
                        "postInitSQL": ['ALTER ROLE "app" CREATEDB;'],
                    },
                },
                "monitoring": {"enablePodMonitor": True},
            },
        }
        if self._config.storage_class:
            manifest["spec"]["storage"]["storageClass"] = self._config.storage_class
        return manifest

    def _cluster_manifest(
        self,
        *,
        namespace: str,
        name: str,
        persistence_name: str,
        temporal_namespace: str,
        size: dict[str, Any],
        labels: dict[str, str],
    ) -> dict[str, Any]:
        connect_addr = f"{persistence_name}-rw.{namespace}.svc.cluster.local:{_POSTGRES_PORT}"
        password_ref = {"name": f"{persistence_name}-app", "key": "password"}
        service = {"replicas": size["replicas"], "resources": size["resources"]}
        return {
            "apiVersion": API_VERSION,
            "kind": CLUSTER_KIND,
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "annotations": {_TEMPORAL_NAMESPACE_ANNOTATION: temporal_namespace},
            },
            "spec": {
                "version": self._config.server_version,
                "numHistoryShards": size["history_shards"],
                "jobTtlSecondsAfterFinished": 300,
                "persistence": {
                    "defaultStore": {
                        "sql": {
                            "user": "app",
                            "pluginName": _POSTGRES_PLUGIN,
                            "databaseName": _DEFAULT_STORE_DATABASE,
                            "connectAddr": connect_addr,
                            "connectProtocol": "tcp",
                        },
                        "passwordSecretRef": dict(password_ref),
                    },
                    "visibilityStore": {
                        "sql": {
                            "user": "app",
                            "pluginName": _POSTGRES_PLUGIN,
                            "databaseName": _VISIBILITY_STORE_DATABASE,
                            "connectAddr": connect_addr,
                            "connectProtocol": "tcp",
                        },
                        "passwordSecretRef": dict(password_ref),
                    },
                },
                "mTLS": {
                    "provider": "cert-manager",
                    "internode": {"enabled": True},
                    "frontend": {"enabled": True},
                },
                "services": {
                    "frontend": {**service, "port": DEFAULT_FRONTEND_PORT},
                    "history": dict(service),
                    "matching": dict(service),
                    "worker": dict(service),
                },
                # A Temporal Web console reaches every namespace on the server
                # it is attached to and ships no authentication of its own;
                # admintools is a shell against the persistence store.
                "ui": {"enabled": False},
                "admintools": {"enabled": False},
            },
        }

    @staticmethod
    def _namespace_manifest(
        *,
        namespace: str,
        cluster_name: str,
        temporal_namespace: str,
        retention: str,
        labels: dict[str, str],
    ) -> dict[str, Any]:
        return {
            "apiVersion": API_VERSION,
            "kind": NAMESPACE_KIND,
            "metadata": {"name": temporal_namespace, "namespace": namespace, "labels": labels},
            "spec": {
                "clusterRef": {"name": cluster_name},
                "retentionPeriod": retention,
            },
        }

    @staticmethod
    def _client_manifest(*, namespace: str, cluster_name: str, labels: dict[str, str]) -> dict[str, Any]:
        return {
            "apiVersion": API_VERSION,
            "kind": CLIENT_KIND,
            "metadata": {"name": _client_name(cluster_name), "namespace": namespace, "labels": labels},
            "spec": {"clusterRef": {"name": cluster_name}},
        }

    @staticmethod
    def _network_policy_manifest(
        *,
        namespace: str,
        cluster_name: str,
        organization_slug: str,
        labels: dict[str, str],
    ) -> dict[str, Any]:
        return {
            "apiVersion": NETWORK_POLICY_API_VERSION,
            "kind": NETWORK_POLICY_KIND,
            "metadata": {"name": _policy_name(cluster_name), "namespace": namespace, "labels": labels},
            "spec": {
                "podSelector": {"matchLabels": {"app.kubernetes.io/instance": cluster_name}},
                "policyTypes": ["Ingress"],
                "ingress": [
                    {
                        "from": [
                            {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": namespace}}},
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": agent_namespace(organization_slug),
                                    }
                                }
                            },
                        ],
                        "ports": [{"protocol": "TCP", "port": DEFAULT_FRONTEND_PORT}],
                    },
                    {
                        # Membership gossip and the internal history/matching
                        # RPCs, reachable only from this cluster's own pods.
                        "from": [{"podSelector": {"matchLabels": {"app.kubernetes.io/instance": cluster_name}}}],
                        "ports": [
                            {"protocol": "TCP", "port": 6933, "endPort": 6939},
                            {"protocol": "TCP", "port": 7234, "endPort": 7239},
                        ],
                    },
                ],
            },
        }


def frontend_address(cluster_name: str, namespace: str) -> str:
    return f"{cluster_name}-frontend.{namespace}.svc.cluster.local:{DEFAULT_FRONTEND_PORT}"


def _persistence_name(cluster_name: str) -> str:
    return dns_label(cluster_name, "persistence")


def _client_name(cluster_name: str) -> str:
    return dns_label(cluster_name, "client")


def _policy_name(cluster_name: str) -> str:
    return dns_label(cluster_name, "temporal")


def _labels_of(resource: dict[str, Any]) -> dict[str, str]:
    return dict((resource.get("metadata") or {}).get("labels") or {})


def _condition_true(resource: dict[str, Any], condition_type: str) -> bool:
    conditions = (resource.get("status") or {}).get("conditions") or []
    return any(
        str(condition.get("type")) == condition_type and str(condition.get("status")) == "True"
        for condition in conditions
    )


def _stub(api_version: str, kind: str, name: str, namespace: str) -> dict[str, Any]:
    return {
        "apiVersion": api_version,
        "kind": kind,
        "metadata": {"name": name, "namespace": namespace},
    }


def _error_code(exc: Exception) -> str:
    return "temporal_isolation_violation" if isinstance(exc, TemporalIsolationError) else "invalid_temporal_config"
