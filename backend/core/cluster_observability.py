"""Resolver-facing entry for runtime pod observability (#299).

Builds a ``ClusterAuth`` payload from a ``TenantCluster`` row,
looks up the right provider plugin's ``ClusterDriver``, and
dispatches ``list_pods`` / ``stream_logs`` through it. The
kubernetes-client SDK lives in the provider package now — the
lifecycle module no longer imports it directly.

Why this lives in ``core`` instead of ``astrolift_drivers``: the
function shape (``list_app_pods(*, cluster, namespace, app_slug)``)
is the resolver contract — it predates the protocol move and the
existing tests assert against it. Moving the file under
``astrolift_drivers`` would force a bigger test-rename churn for
no upside.

Pluggable test backends:
The k8s_native driver (and its EKS/GKE/AKS subclasses) take
``pod_backend`` and ``log_backend`` constructor kwargs that swap
the live kubernetes-client path for a deterministic fake. Tests
go through :func:`set_pod_backend_for_tests` /
:func:`set_log_backend_for_tests` here so the resolver-facing
contract stays narrow.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from astrolift_drivers.registry import DriverNotFound, plugins

if TYPE_CHECKING:
    from astrolift_clusters.models import TenantCluster

logger = logging.getLogger(__name__)


class ClusterObservabilityError(Exception):
    """A cluster row can't be turned into a usable driver — either
    the plugin isn't loaded, the driver doesn't implement the
    observability methods, or the auth payload is malformed. The
    resolver layer maps this to an empty UI state."""


# ---- Test-injectable backends -------------------------------------
#
# Same pattern the old core.k8s.{pods,logs} modules used: tests
# install a fake, run their assertion, and reset on teardown. The
# real driver path is the default; tests opt in.
#
# Type-imported lazily so importing this module doesn't pull in
# astrolift-providers (which the schema-export command runs without).

_POD_BACKEND_OVERRIDE: Any = None
_LOG_BACKEND_OVERRIDE: Any = None
_EVENTS_BACKEND_OVERRIDE: Any = None


def set_pod_backend_for_tests(backend: Any) -> None:
    """Tests use this to install a deterministic pod backend.

    Backend interface: ``backend.list_pods(*, auth, namespace,
    app_slug) -> list[PodInfo]``. Reset with
    :func:`reset_pod_backend_for_tests`.
    """
    global _POD_BACKEND_OVERRIDE
    _POD_BACKEND_OVERRIDE = backend


def reset_pod_backend_for_tests() -> None:
    global _POD_BACKEND_OVERRIDE
    _POD_BACKEND_OVERRIDE = None


def set_log_backend_for_tests(backend: Any) -> None:
    """Tests use this to install a deterministic log backend.

    Backend interface: ``backend.stream(*, auth, namespace, ...) ->
    AsyncIterator[PodLogLine]``. Reset with
    :func:`reset_log_backend_for_tests`.
    """
    global _LOG_BACKEND_OVERRIDE
    _LOG_BACKEND_OVERRIDE = backend


def reset_log_backend_for_tests() -> None:
    global _LOG_BACKEND_OVERRIDE
    _LOG_BACKEND_OVERRIDE = None


def set_events_backend_for_tests(backend: Any) -> None:
    """Tests use this to install a deterministic events backend (#666).

    Backend interface: ``backend.list_events(ctx, *, namespaces, event_type,
    limit) -> list[ClusterEvent]`` — the driver protocol's shape.  Reset
    with :func:`reset_events_backend_for_tests`.
    """
    global _EVENTS_BACKEND_OVERRIDE
    _EVENTS_BACKEND_OVERRIDE = backend


def reset_events_backend_for_tests() -> None:
    global _EVENTS_BACKEND_OVERRIDE
    _EVENTS_BACKEND_OVERRIDE = None


# ---- Public resolver API ------------------------------------------


def namespace_for_app(app: Any) -> str:
    """Per-app namespace, mirroring what the manifest renderer uses.

    Resolution order:
      1. ``app.k8s_namespace`` (explicit override on the row)
      2. ``f"{org_slug}-{app_slug}"`` (renderer default)
    """
    explicit = (getattr(app, "k8s_namespace", "") or "").strip()
    if explicit:
        return explicit
    org_slug = getattr(getattr(app, "organization", None), "slug", "") or ""
    return f"{org_slug}-{app.slug}"


def _auth_for_cluster(cluster: TenantCluster) -> Any:
    """Build a ``ClusterAuth`` payload from a ``TenantCluster`` row.

    The provider package owns the dataclass shape; we import it
    lazily so the schema-export command (which doesn't have the
    provider plugins installed) still works."""
    from _sdk.cluster import ClusterAuth

    return ClusterAuth(
        slug=cluster.slug,
        auth_method=cluster.auth_method,
        auth_config=cluster.auth_config or {},
        endpoint=cluster.endpoint or "",
        ca_cert=cluster.ca_cert or "",
        namespace_prefix=cluster.default_namespace_prefix or "",
    )


class _OverrideDriver:
    """Test-only driver — bypasses provider-plugin lookup and
    dispatches list_pods / stream_logs / list_events straight to the
    installed test backends. Lets the test fixtures use any
    ``TenantCluster.provider_plugin`` row (even one not in the
    plugin registry) without forcing the real driver path."""

    def __init__(self, pod_backend: Any, log_backend: Any, events_backend: Any = None) -> None:
        self._pod_backend = pod_backend
        self._log_backend = log_backend
        self._events_backend = events_backend

    def list_pods(self, *, auth: Any, namespace: str, app_slug: str, task_id: str = "") -> list[Any]:
        if self._pod_backend is None:
            raise ClusterObservabilityError(
                "list_pods called without a pod backend override; set_pod_backend_for_tests was not called",
            )
        kwargs: dict[str, Any] = {"auth": auth, "namespace": namespace, "app_slug": app_slug}
        # Forward task_id only when set so app-path test backends (whose
        # list_pods predates the kwarg) keep working unchanged (#891).
        if task_id:
            kwargs["task_id"] = task_id
        return self._pod_backend.list_pods(**kwargs)

    def stream_logs(
        self,
        *,
        auth: Any,
        namespace: str,
        pod_name: str,
        container: str | None,
        tail_lines: int,
        follow: bool,
    ) -> Any:
        if self._log_backend is None:
            raise ClusterObservabilityError(
                "stream_logs called without a log backend override; set_log_backend_for_tests was not called",
            )
        return self._log_backend.stream(
            auth=auth,
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            tail_lines=tail_lines,
            follow=follow,
        )

    def list_events(
        self,
        ctx: Any,
        *,
        namespaces: list[str] | None = None,
        event_type: str | None = "Warning",
        limit: int = 50,
    ) -> list[Any]:
        if self._events_backend is None:
            raise ClusterObservabilityError(
                "list_events called without an events backend override; set_events_backend_for_tests was not called",
            )
        return self._events_backend.list_events(
            ctx,
            namespaces=namespaces,
            event_type=event_type,
            limit=limit,
        )


def _driver_for_cluster(cluster: TenantCluster) -> Any:
    """Look up the ``ClusterDriver`` class for ``cluster.provider_plugin``
    and instantiate it with the right test backend overrides (if any).

    The driver classes ship config-required constructors (EKSConfig,
    GKEConfig, AKSConfig, K8sNativeConfig) but the observability path
    doesn't use that config — it lands in ``auth`` instead. We pass
    a sentinel placeholder so the constructor doesn't refuse to
    build, and the pluggable backends do the real work.

    When a test backend is installed we skip the registry lookup
    entirely — the test fixtures use synthetic ``provider_plugin``
    rows that aren't loaded as Python plugins, so the registry path
    would error on every test. The override driver dispatches
    straight to the test backend.
    """
    if (
        _POD_BACKEND_OVERRIDE is not None
        or _LOG_BACKEND_OVERRIDE is not None
        or _EVENTS_BACKEND_OVERRIDE is not None
    ):
        return _OverrideDriver(
            pod_backend=_POD_BACKEND_OVERRIDE,
            log_backend=_LOG_BACKEND_OVERRIDE,
            events_backend=_EVENTS_BACKEND_OVERRIDE,
        )

    plugin_slug = cluster.provider_plugin.slug
    try:
        driver_cls = plugins.get(plugin_slug, "cluster")
    except DriverNotFound as exc:
        raise ClusterObservabilityError(
            f"cluster {cluster.slug}: provider plugin {plugin_slug!r} does not register a 'cluster' driver",
        ) from exc

    cfg = _config_for(plugin_slug, cluster)
    try:
        return driver_cls(config=cfg)
    except TypeError as exc:
        # Constructor signature didn't accept config= or kwargs. This
        # signals a plugin mismatch — surface as observability error
        # rather than blowing up the resolver.
        raise ClusterObservabilityError(
            f"cluster {cluster.slug}: driver {plugin_slug!r} constructor rejected config payload: {exc}",
        ) from exc


def _config_for(plugin_slug: str, cluster: TenantCluster) -> Any:
    """Per-plugin config dataclass instantiation.

    Each driver has a tiny ``*Config`` dataclass keyed by cloud
    identifiers (region/project/subscription). For the observability
    path we just need *something* that doesn't crash the constructor;
    the row's ``provider_config`` holds whatever the operator pinned
    so use that, falling back to empty strings.
    """
    pc = cluster.provider_config or {}
    if plugin_slug == "k8s_native":
        from k8s_native.cluster import K8sNativeConfig

        return K8sNativeConfig(
            kubeconfig_path=str(pc.get("kubeconfig_path", "")),
            context=str(pc.get("context", "")),
            in_cluster=bool(pc.get("in_cluster", False)),
        )
    if plugin_slug == "aws":
        from aws.cluster_eks import EKSConfig

        # cluster_name resolution order:
        #   1. provider_config["cluster_name"] (operator-pinned at plugin configure)
        #   2. auth_config["cluster_name"]     (set by bootstrap_components / IRSA path)
        #   3. cluster.slug                    (last-resort fallback)
        ac = cluster.auth_config or {}
        return EKSConfig(
            region=str(pc.get("region", ac.get("region", cluster.region or ""))),
            cluster_name=str(pc.get("cluster_name", ac.get("cluster_name", cluster.slug))),
        )
    if plugin_slug == "gcp":
        from gcp.cluster_gke import GKEConfig

        return GKEConfig(
            project_id=str(pc.get("project_id", "")),
            location=str(pc.get("location", cluster.region or "")),
            cluster_name=str(pc.get("cluster_name", cluster.slug)),
        )
    if plugin_slug == "azure":
        from azure.cluster_aks import AKSConfig

        return AKSConfig(
            subscription_id=str(pc.get("subscription_id", "")),
            resource_group=str(pc.get("resource_group", "")),
            cluster_name=str(pc.get("cluster_name", cluster.slug)),
        )
    raise ClusterObservabilityError(
        f"cluster {cluster.slug}: no config builder wired for plugin {plugin_slug!r}",
    )


def _gcp_managed_config_for(
    cluster: TenantCluster,
    *,
    kind: str,
    variant: str,
    provider_config: dict[str, Any],
    auth_config: dict[str, Any],
    region: str,
) -> Any:
    """Build configs for every executable GCP managed-service driver.

    Registered placeholder drivers remain intentionally unavailable here.  A
    catalogue row must not become lifecycle-addressable until its driver can
    actually provision a cloud resource.
    """

    pc = provider_config
    ac = auth_config
    project_id = str(
        pc.get("project_id")
        or pc.get("gcp_project_id")
        or ac.get("project_id")
        or ac.get("gcp_project_id")
        or ""
    )
    if not project_id:
        raise ClusterObservabilityError(
            f"cluster {cluster.slug}: GCP managed service {kind!r} requires provider_config.project_id",
        )

    pair = (kind, variant)
    if pair == ("faas", "cloud_functions_gen2") or (kind == "faas" and not variant):
        from gcp.managed.faas_cloud_functions import CloudFunctionsConfig

        return CloudFunctionsConfig(
            project_id=project_id,
            region=str(pc.get("cloud_functions_region") or region),
            function_name_prefix=str(pc.get("cloud_functions_name_prefix", "astrolift")),
            api_endpoint=str(
                pc.get("cloud_functions_api_endpoint", "https://cloudfunctions.googleapis.com/v2"),
            ),
            deletion_protection_default=bool(
                pc.get("cloud_functions_deletion_protection_default", True),
            ),
            operation_timeout_seconds=float(
                pc.get("cloud_functions_operation_timeout_seconds", 1800),
            ),
            poll_interval_seconds=float(
                pc.get("cloud_functions_operation_poll_interval_seconds", 5),
            ),
        )

    if pair == ("event_bus", "eventarc") or (kind == "event_bus" and not variant):
        from gcp.managed.event_bus_eventarc import EventarcConfig

        return EventarcConfig(
            project_id=project_id,
            location=str(pc.get("eventarc_location") or region),
            message_bus_id=str(pc.get("eventarc_message_bus_id", "astrolift")),
            api_endpoint=str(
                pc.get("eventarc_api_endpoint", "https://eventarc.googleapis.com/v1"),
            ),
            publishing_endpoint=str(
                pc.get(
                    "eventarc_publishing_endpoint",
                    "https://eventarcpublishing.googleapis.com/v1",
                ),
            ),
            deletion_protection_default=bool(
                pc.get("eventarc_deletion_protection_default", True),
            ),
            operation_timeout_seconds=float(
                pc.get("eventarc_operation_timeout_seconds", 900),
            ),
            poll_interval_seconds=float(
                pc.get("eventarc_operation_poll_interval_seconds", 5),
            ),
        )

    if pair == ("object_store", "gcs") or (kind == "object_store" and not variant):
        from gcp.managed.object_store_gcs import GCSConfig

        return GCSConfig(
            project_id=project_id,
            bucket_name_prefix=str(pc.get("bucket_name_prefix", "astrolift")),
            versioning_enabled=bool(pc.get("gcs_versioning_enabled", True)),
            uniform_bucket_level_access=bool(
                pc.get("gcs_uniform_bucket_level_access", True),
            ),
            location=str(pc.get("gcs_location", region or "US")),
            storage_class=str(pc.get("gcs_storage_class", "STANDARD")),
        )

    if pair == ("queue", "pubsub") or (kind == "queue" and not variant):
        from gcp.managed.queue_pubsub import PubSubConfig

        return PubSubConfig(
            project_id=project_id,
            topic_prefix=str(pc.get("pubsub_topic_prefix", "astrolift")),
        )

    if pair == ("topic", "pubsub_topic") or (kind == "topic" and not variant):
        from gcp.managed.topic_pubsub import PubSubTopicConfig

        return PubSubTopicConfig(
            project_id=project_id,
            topic_prefix=str(pc.get("pubsub_topic_prefix", "astrolift")),
        )

    if pair == ("warehouse", "bigquery") or (kind == "warehouse" and not variant):
        from gcp.managed.warehouse_bigquery import BigQueryWarehouseConfig

        return BigQueryWarehouseConfig(
            project_id=project_id,
            location=str(pc.get("bigquery_location") or region),
            dataset_prefix=str(pc.get("bigquery_dataset_prefix", "astrolift")),
            deletion_protection_default=bool(
                pc.get("bigquery_deletion_protection_default", True),
            ),
            dataset_api_endpoint=str(
                pc.get("bigquery_dataset_api_endpoint", "https://bigquery.googleapis.com/bigquery/v2"),
            ),
            reservation_api_endpoint=str(
                pc.get(
                    "bigquery_reservation_api_endpoint",
                    "https://bigqueryreservation.googleapis.com/v1",
                ),
            ),
        )

    if pair == ("document_db", "firestore_native") or (kind == "document_db" and not variant):
        from gcp.managed.document_firestore import FirestoreConfig

        return FirestoreConfig(
            project_id=project_id,
            location=str(pc.get("firestore_location") or region),
            database_name_prefix=str(
                pc.get("firestore_database_name_prefix", "astrolift"),
            ),
            deletion_protection_default=bool(
                pc.get("firestore_deletion_protection_default", True),
            ),
            api_endpoint=str(
                pc.get("firestore_api_endpoint", "https://firestore.googleapis.com/v1"),
            ),
            snapshot_bucket=str(pc.get("firestore_snapshot_bucket", "")),
            operation_timeout_seconds=float(
                pc.get("firestore_operation_timeout_seconds", 900),
            ),
            poll_interval_seconds=float(
                pc.get("firestore_operation_poll_interval_seconds", 2),
            ),
        )

    if pair == ("graph_db", "spanner_graph") or (kind == "graph_db" and not variant):
        from gcp.managed.graph_spanner import SpannerGraphConfig

        return SpannerGraphConfig(
            project_id=project_id,
            region=region,
            instance_name_prefix=str(pc.get("spanner_instance_name_prefix", "astrolift")),
            shared_instance_id=str(pc.get("spanner_shared_instance_id", "")),
            instance_config=str(pc.get("spanner_instance_config", "")),
            edition=str(pc.get("spanner_edition", "ENTERPRISE")),
            processing_units=int(pc.get("spanner_processing_units", 100)),
            automatic_backup_schedule=bool(pc.get("spanner_automatic_backup_schedule", True)),
            deletion_protection_default=bool(
                pc.get("spanner_deletion_protection_default", True),
            ),
            backup_retention_days=int(pc.get("spanner_backup_retention_days", 30)),
            api_endpoint=str(
                pc.get("spanner_api_endpoint", "https://spanner.googleapis.com/v1"),
            ),
            operation_timeout_seconds=float(
                pc.get("spanner_operation_timeout_seconds", 1800),
            ),
            poll_interval_seconds=float(
                pc.get("spanner_operation_poll_interval_seconds", 2),
            ),
            adopt_existing_instance=bool(pc.get("spanner_adopt_existing_instance", False)),
        )

    if pair == ("postgres", "cloudsql") or (kind == "postgres" and not variant):
        from gcp.managed.postgres_cloudsql import CloudSQLConfig

        return CloudSQLConfig(
            project_id=project_id,
            region=region,
            private_network=_optional_string(pc.get("cloudsql_private_network")),
            instance_name_prefix=str(pc.get("cloudsql_instance_name_prefix", "astrolift")),
            engine_version=str(pc.get("cloudsql_postgres_engine_version", "POSTGRES_16")),
            backup_retention_days=int(pc.get("cloudsql_backup_retention_days", 7)),
            high_availability_default=bool(pc.get("cloudsql_high_availability_default", False)),
            deletion_protection_default=bool(pc.get("cloudsql_deletion_protection_default", True)),
            secret_manager_prefix=str(pc.get("cloudsql_secret_manager_prefix", "astrolift/cloudsql")),
            secret_id_prefix=str(pc.get("secret_id_prefix", "astrolift")),
        )

    if pair == ("postgres", "alloydb"):
        from gcp.managed.postgres_alloydb import AlloyDBConfig

        return AlloyDBConfig(
            project_id=project_id,
            region=region,
            network=_optional_string(pc.get("alloydb_network")),
            allocated_ip_range=_optional_string(pc.get("alloydb_allocated_ip_range")),
            cluster_name_prefix=str(pc.get("alloydb_cluster_name_prefix", "astrolift")),
            primary_instance_id=str(pc.get("alloydb_primary_instance_id", "primary")),
            database_version=str(pc.get("alloydb_database_version", "POSTGRES_16")),
            machine_type_default=str(pc.get("alloydb_machine_type_default", "n2-highmem-2")),
            high_availability_default=bool(pc.get("alloydb_high_availability_default", True)),
            deletion_protection_default=bool(
                pc.get("alloydb_deletion_protection_default", True),
            ),
            backup_retention_days=int(pc.get("alloydb_backup_retention_days", 14)),
            secret_manager_prefix=str(
                pc.get("alloydb_secret_manager_prefix", "astrolift/alloydb"),
            ),
            secret_id_prefix=str(pc.get("secret_id_prefix", "astrolift")),
            operation_timeout_seconds=float(pc.get("alloydb_operation_timeout_seconds", 1200)),
            operation_poll_interval_seconds=float(
                pc.get("alloydb_operation_poll_interval_seconds", 5),
            ),
            api_endpoint=str(
                pc.get("alloydb_api_endpoint", "https://alloydb.googleapis.com/v1"),
            ),
        )

    if pair == ("mysql", "cloudsql") or (kind == "mysql" and not variant):
        from gcp.managed.mysql_cloudsql import CloudSQLMySQLConfig

        return CloudSQLMySQLConfig(
            project_id=project_id,
            region=region,
            private_network=_optional_string(pc.get("cloudsql_private_network")),
            instance_name_prefix=str(pc.get("cloudsql_instance_name_prefix", "astrolift")),
            engine_version=str(pc.get("cloudsql_mysql_engine_version", "MYSQL_8_0")),
            backup_retention_days=int(pc.get("cloudsql_backup_retention_days", 7)),
            high_availability_default=bool(pc.get("cloudsql_high_availability_default", False)),
            deletion_protection_default=bool(pc.get("cloudsql_deletion_protection_default", True)),
            secret_manager_prefix=str(pc.get("cloudsql_secret_manager_prefix", "astrolift/cloudsql")),
            secret_id_prefix=str(pc.get("secret_id_prefix", "astrolift")),
        )

    if pair == ("mssql", "cloudsql_sqlserver") or (kind == "mssql" and not variant):
        from gcp.managed.mssql_cloudsql import CloudSQLServerConfig

        return CloudSQLServerConfig(
            project_id=project_id,
            region=region,
            private_network=_optional_string(pc.get("cloudsql_private_network")),
            instance_name_prefix=str(pc.get("cloudsql_instance_name_prefix", "astrolift")),
            engine_version=str(
                pc.get("cloudsql_sqlserver_engine_version", "SQLSERVER_2025_EXPRESS"),
            ),
            backup_retention_days=int(pc.get("cloudsql_sqlserver_backup_retention_days", 7)),
            high_availability_default=bool(pc.get("cloudsql_high_availability_default", False)),
            deletion_protection_default=bool(pc.get("cloudsql_deletion_protection_default", True)),
            secret_manager_prefix=str(pc.get("cloudsql_secret_manager_prefix", "astrolift/cloudsql")),
            secret_id_prefix=str(pc.get("secret_id_prefix", "astrolift")),
            api_endpoint=str(pc.get("cloudsql_api_endpoint", "https://sqladmin.googleapis.com/v1")),
            operation_timeout_seconds=float(pc.get("cloudsql_operation_timeout_seconds", 1800)),
            poll_interval_seconds=float(pc.get("cloudsql_operation_poll_interval_seconds", 3)),
        )

    if pair == ("redis", "memorystore") or (kind == "redis" and not variant):
        from gcp.managed.redis_memorystore import MemorystoreConfig

        return MemorystoreConfig(
            project_id=project_id,
            region=region,
            authorized_network=_optional_string(pc.get("memorystore_authorized_network")),
            instance_name_prefix=str(pc.get("memorystore_instance_name_prefix", "astrolift")),
            redis_version=str(pc.get("memorystore_redis_version", "REDIS_7_2")),
            tier_default=str(pc.get("memorystore_tier_default", "BASIC")),
            transit_encryption_default=bool(pc.get("memorystore_transit_encryption_default", True)),
            auth_enabled_default=bool(pc.get("memorystore_auth_enabled_default", True)),
            secret_manager_prefix=str(
                pc.get("memorystore_secret_manager_prefix", "astrolift/memorystore"),
            ),
            secret_id_prefix=str(pc.get("secret_id_prefix", "astrolift")),
        )

    if pair == ("redis", "memorystore_valkey"):
        from gcp.managed.redis_memorystore_valkey import MemorystoreValkeyConfig

        return MemorystoreValkeyConfig(
            project_id=project_id,
            region=region,
            network=str(pc.get("memorystore_valkey_network", "")),
            instance_name_prefix=str(pc.get("memorystore_valkey_instance_name_prefix", "astrolift")),
            engine_version=str(pc.get("memorystore_valkey_engine_version", "VALKEY_9_0")),
            node_type=str(pc.get("memorystore_valkey_node_type", "HIGHMEM_MEDIUM")),
            mode=str(pc.get("memorystore_valkey_mode", "CLUSTER")),
            shard_count=int(pc.get("memorystore_valkey_shard_count", 1)),
            replica_count=int(pc.get("memorystore_valkey_replica_count", 1)),
            authorization_mode=str(pc.get("memorystore_valkey_authorization_mode", "IAM_AUTH")),
            token_auth_user=str(pc.get("memorystore_valkey_token_auth_user", "default")),
            token_auth_rotation_generation=int(
                pc.get("memorystore_valkey_token_auth_rotation_generation", 1)
            ),
            token_auth_retire_generation=int(pc.get("memorystore_valkey_token_auth_retire_generation", 0)),
            transit_encryption_default=bool(
                pc.get("memorystore_valkey_transit_encryption_default", True),
            ),
            persistence_mode=str(pc.get("memorystore_valkey_persistence_mode", "RDB")),
            automated_backup_default=bool(
                pc.get("memorystore_valkey_automated_backup_default", True),
            ),
            backup_retention_days=int(pc.get("memorystore_valkey_backup_retention_days", 35)),
            deletion_protection_default=bool(
                pc.get("memorystore_valkey_deletion_protection_default", True),
            ),
            kms_key=str(pc.get("memorystore_valkey_kms_key", "")),
            server_ca_mode=str(pc.get("memorystore_valkey_server_ca_mode", "")),
            server_ca_pool=str(pc.get("memorystore_valkey_server_ca_pool", "")),
            secret_manager_prefix=str(
                pc.get("memorystore_valkey_secret_manager_prefix", "astrolift/memorystore-valkey")
            ),
            secret_id_prefix=str(pc.get("secret_id_prefix", "astrolift")),
            allow_preview_features=bool(
                pc.get("memorystore_valkey_allow_preview_features", False),
            ),
            api_endpoint=str(
                pc.get("memorystore_valkey_api_endpoint", "https://memorystore.googleapis.com/v1"),
            ),
            operation_timeout_seconds=float(
                pc.get("memorystore_valkey_operation_timeout_seconds", 1800),
            ),
            poll_interval_seconds=float(pc.get("memorystore_valkey_poll_interval_seconds", 3)),
            adopt_existing_instance=bool(
                pc.get("memorystore_valkey_adopt_existing_instance", False),
            ),
        )

    if pair == ("kv_store", "bigtable") or (kind == "kv_store" and not variant):
        from gcp.managed.bigtable import BigtableConfig

        return BigtableConfig(
            project_id=project_id,
            region=region,
            instance_name_prefix=str(pc.get("bigtable_instance_name_prefix", "astrolift")),
            storage_type_default=str(pc.get("bigtable_storage_type_default", "SSD")),
            cluster_count_default=int(pc.get("bigtable_cluster_count_default", 1)),
            column_family_default=str(pc.get("bigtable_column_family_default", "cf1")),
        )

    if pair == ("vector_index", "vertex_matching_engine") or (kind == "vector_index" and not variant):
        from gcp.managed.vector_vertex import VertexMatchingEngineConfig

        return VertexMatchingEngineConfig(
            project_id=project_id,
            region=region,
            name_prefix=str(pc.get("vertex_matching_engine_name_prefix", "astrolift-vec")),
            embedding_dimension_default=int(
                pc.get("vertex_matching_engine_embedding_dimension_default", 768),
            ),
            distance_measure_default=str(
                pc.get("vertex_matching_engine_distance_measure_default", "DOT_PRODUCT_DISTANCE"),
            ),
            algorithm_default=str(
                pc.get("vertex_matching_engine_algorithm_default", "BRUTE_FORCE"),
            ),
            shard_bucket_prefix=str(
                pc.get("vertex_matching_engine_shard_bucket_prefix", "astrolift-vec-shards"),
            ),
            public_endpoint_enabled_default=bool(
                pc.get("vertex_matching_engine_public_endpoint_enabled_default", False),
            ),
        )

    if pair == ("time_series", "gcp_managed_prometheus") or (kind == "time_series" and not variant):
        from gcp.managed.timeseries_managed_prometheus import GCPManagedPrometheusConfig

        return GCPManagedPrometheusConfig(
            project_id=project_id,
            region=region,
            workspace_name_prefix=str(
                pc.get("managed_prometheus_workspace_name_prefix", "astrolift-tsdb"),
            ),
            retention_months_default=int(
                pc.get("managed_prometheus_retention_months_default", 24),
            ),
            rule_group_check_enabled=bool(
                pc.get("managed_prometheus_rule_group_check_enabled", True),
            ),
        )

    if pair == ("model_endpoint", "vertex_ai") or (kind == "model_endpoint" and not variant):
        from gcp.managed.model_endpoint_vertex import VertexAIEndpointConfig

        return VertexAIEndpointConfig(
            project_id=project_id,
            region=region,
            name_prefix=str(pc.get("vertex_ai_endpoint_name_prefix", "astrolift-model")),
            default_model_artifact=str(
                pc.get(
                    "vertex_ai_default_model_artifact",
                    "publishers/google/models/text-bison",
                ),
            ),
            public_endpoint_enabled_default=bool(
                pc.get("vertex_ai_public_endpoint_enabled_default", False),
            ),
            default_traffic_percentage=int(pc.get("vertex_ai_default_traffic_percentage", 100)),
        )

    if pair == ("encryption_key", "cloud_kms") or (kind == "encryption_key" and not variant):
        from gcp.managed.encryption_cloud_kms import CloudKMSConfig

        return CloudKMSConfig(
            project_id=project_id,
            location=str(pc.get("cloud_kms_location", pc.get("kms_location", region or "global"))),
            key_ring_name_prefix=str(
                pc.get("cloud_kms_key_ring_name_prefix", "astrolift"),
            ),
            key_name_prefix=str(pc.get("cloud_kms_key_name_prefix", "astrolift")),
            deletion_protection_default=bool(
                pc.get("cloud_kms_deletion_protection_default", True),
            ),
            rotation_period_default=str(
                pc.get("cloud_kms_rotation_period_default", "7776000s"),
            ),
            destroy_scheduled_duration_default=str(
                pc.get("cloud_kms_destroy_scheduled_duration_default", "2592000s"),
            ),
            api_endpoint=str(
                pc.get("cloud_kms_api_endpoint", "https://cloudkms.googleapis.com/v1"),
            ),
        )

    if pair in {
        ("search", "gcp_elastic_cloud"),
        ("email", "gcp_thirdparty"),
    }:
        raise ClusterObservabilityError(
            f"cluster {cluster.slug}: GCP managed service {kind!r}/{variant!r} is a planned "
            "placeholder and cannot be provisioned",
        )
    raise ClusterObservabilityError(
        f"cluster {cluster.slug}: no GCP managed-service config builder for "
        f"kind={kind!r}, variant={variant!r}",
    )


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def managed_config_for(
    plugin_slug: str,
    cluster: TenantCluster,
    *,
    kind: str,
    variant: str = "",
) -> Any:
    """Build a managed-service DRIVER config from the cluster's install
    settings (#1002).

    Distinct from :func:`_config_for`, which builds the *cluster* driver
    config (EKSConfig …). Managed-service drivers (RDS, ElastiCache, S3)
    take their own ``*Config`` dataclass. Every field is read from the
    cluster's ``provider_config`` (the install-time settings bundle) with
    the driver default as the fallback, so an operator can pin anything
    (sizing, engine, retention, encryption, prefixes) for a locked-down
    install. VPC-bound kinds (RDS/Redis) resolve their subnet group + SG
    from ``provider_config`` if pinned, else the platform discovers the
    cluster VPC and creates them itself — never out-of-band terraform.
    """
    pc = cluster.provider_config or {}
    ac = cluster.auth_config or {}
    region = str(pc.get("region", ac.get("region", cluster.region or "")))

    if plugin_slug == "gcp":
        return _gcp_managed_config_for(
            cluster,
            kind=kind,
            variant=variant,
            provider_config=pc,
            auth_config=ac,
            region=region,
        )

    if plugin_slug != "aws":
        raise ClusterObservabilityError(
            f"cluster {cluster.slug}: managed-service config not wired for "
            f"plugin {plugin_slug!r} (kind={kind!r})",
        )

    if kind == "object_store":
        from aws.managed.object_store_s3 import S3Config

        return S3Config(
            region=region,
            bucket_name_prefix=str(pc.get("bucket_name_prefix", "astrolift")),
            versioning_enabled=bool(pc.get("versioning_enabled", True)),
            public_access_blocked=bool(pc.get("public_access_blocked", True)),
        )

    if kind == "kv_store":
        from aws.managed.dynamodb import DynamoDBConfig

        return DynamoDBConfig(
            region=region,
            table_name_prefix=str(pc.get("table_name_prefix", "astrolift")),
            billing_mode_default=str(pc.get("dynamodb_billing_mode", "PAY_PER_REQUEST")),
            deletion_protection_default=bool(pc.get("deletion_protection_default", True)),
            point_in_time_recovery_default=bool(pc.get("point_in_time_recovery_default", True)),
        )

    if kind == "cdn":
        from aws.managed.cdn_cloudfront import CloudFrontConfig

        return CloudFrontConfig(
            region="us-east-1",
            comment_prefix=str(pc.get("cdn_comment_prefix", "astrolift")),
            price_class=str(pc.get("cloudfront_price_class", "PriceClass_100")),
        )

    if kind == "faas":
        from aws.managed.faas_lambda import LambdaConfig

        return LambdaConfig(
            region=region,
            role_path_prefix=str(pc.get("faas_role_path_prefix", "/")),
            default_architecture=str(pc.get("faas_default_architecture", "arm64")),
            log_retention_days=int(pc.get("faas_log_retention_days", 14)),
        )

    if kind == "api_gateway":
        from aws.managed.api_gateway_http import ApiGatewayHttpConfig

        return ApiGatewayHttpConfig(
            region=region,
            account_id=str(pc.get("account_id", ac.get("account_id", ""))),
            deletion_protection_default=bool(
                pc.get("api_gateway_deletion_protection_default", False),
            ),
        )

    if kind == "encryption_key":
        from aws.managed.encryption_kms import KMSConfig

        return KMSConfig(
            region=region,
            alias_name_prefix=str(pc.get("kms_alias_name_prefix", "alias/astrolift")),
            deletion_protection_default=bool(
                pc.get("kms_deletion_protection_default", True),
            ),
            pending_window_days_default=int(
                pc.get("kms_pending_window_days_default", 30),
            ),
        )

    if kind == "observability":
        from aws.managed.observability_cloudwatch import CloudWatchConfig

        return CloudWatchConfig(
            region=region,
            account_id=str(pc.get("account_id", ac.get("account_id", ""))),
            log_group_prefix=str(pc.get("cloudwatch_log_group_prefix", "/astrolift")),
            retention_days_default=int(pc.get("cloudwatch_retention_days_default", 30)),
            log_group_class_default=str(pc.get("cloudwatch_log_group_class_default", "STANDARD")),
            deletion_protection_default=bool(
                pc.get("cloudwatch_deletion_protection_default", True),
            ),
            dashboard_enabled_default=bool(
                pc.get("cloudwatch_dashboard_enabled_default", True),
            ),
        )

    if kind == "workflow_engine":
        from aws.managed.workflow_step_functions import StepFunctionsConfig

        return StepFunctionsConfig(
            region=region,
            state_machine_name_prefix=str(
                pc.get("step_functions_name_prefix", "astrolift"),
            ),
            deletion_protection_default=bool(
                pc.get("step_functions_deletion_protection_default", True),
            ),
        )

    if kind == "private_endpoint":
        from aws.managed._networking import discover_vpc
        from aws.managed.private_endpoint_vpc import VpcEndpointConfig

        vpc_id = str(pc.get("vpc_endpoint_vpc_id") or pc.get("vpc_id") or "")
        subnet_ids = list(pc.get("vpc_endpoint_subnet_ids") or [])
        if not vpc_id:
            import boto3

            vpc_id, discovered_subnets, _ = discover_vpc(
                cluster,
                region=region,
                ec2=boto3.client("ec2", region_name=region),
                eks=boto3.client("eks", region_name=region),
            )
            if not subnet_ids:
                subnet_ids = discovered_subnets
        return VpcEndpointConfig(
            region=region,
            vpc_id=vpc_id,
            subnet_ids=subnet_ids,
            security_group_ids=list(pc.get("vpc_endpoint_security_group_ids") or []),
            route_table_ids=list(pc.get("vpc_endpoint_route_table_ids") or []),
            private_dns_enabled_default=bool(
                pc.get("vpc_endpoint_private_dns_enabled_default", False),
            ),
            deletion_protection_default=bool(
                pc.get("vpc_endpoint_deletion_protection_default", True),
            ),
        )

    if kind == "database_proxy":
        from aws.managed._networking import ensure_db_proxy_networking
        from aws.managed.rds_proxy import RDSProxyConfig

        subnet_ids, security_group_ids = ensure_db_proxy_networking(
            cluster,
            region=region,
        )
        return RDSProxyConfig(
            region=region,
            vpc_subnet_ids=subnet_ids,
            vpc_security_group_ids=security_group_ids,
            role_arn=str(pc.get("db_proxy_role_arn", "")),
            proxy_name_prefix=str(pc.get("db_proxy_name_prefix", "astrolift")),
            idle_client_timeout=int(pc.get("db_proxy_idle_client_timeout", 1800)),
            require_tls_default=bool(pc.get("db_proxy_require_tls", True)),
        )

    if kind in ("postgres", "mysql", "mssql"):
        from aws.managed._networking import ensure_db_networking

        port = {"postgres": 5432, "mysql": 3306, "mssql": 1433}[kind]
        subnet_group, sg_ids = ensure_db_networking(
            cluster,
            region=region,
            port=port,
            service="rds",
        )
        if variant.startswith("aurora_"):
            from aws.managed.aurora import AuroraConfig

            engine = "aurora-postgresql" if kind == "postgres" else "aurora-mysql"
            return AuroraConfig(
                region=region,
                db_subnet_group=subnet_group,
                security_group_ids=sg_ids,
                engine=engine,
                serverless_v2=variant.endswith("_serverless_v2"),
                cluster_name_prefix=str(pc.get("instance_name_prefix", "astrolift")),
                engine_version=str(
                    pc.get("postgres_engine_version" if kind == "postgres" else "mysql_engine_version", ""),
                ),
                backup_retention_days=int(pc.get("backup_retention_days", 7)),
                deletion_protection_default=bool(
                    pc.get("deletion_protection_default", True),
                ),
                secrets_manager_prefix=str(
                    pc.get("managed_service_secrets_prefix", "astrolift/managed"),
                ),
            )
        if kind == "mssql":
            from aws.managed.mssql_rds import RDSSqlServerConfig

            engines = {
                "rds_sqlserver_express": "sqlserver-ex",
                "rds_sqlserver_web": "sqlserver-web",
                "rds_sqlserver_standard": "sqlserver-se",
                "rds_sqlserver_enterprise": "sqlserver-ee",
            }
            try:
                engine = engines[variant]
            except KeyError as exc:
                raise ClusterObservabilityError(
                    f"cluster {cluster.slug}: unknown AWS mssql variant {variant!r}",
                ) from exc
            return RDSSqlServerConfig(
                region=region,
                db_subnet_group=subnet_group,
                security_group_ids=sg_ids,
                engine=engine,
                instance_name_prefix=str(pc.get("instance_name_prefix", "astrolift")),
                engine_version=str(pc.get("mssql_engine_version", "")),
                backup_retention_days=int(pc.get("backup_retention_days", 7)),
                multi_az_default=bool(pc.get("multi_az_default", False)),
                deletion_protection_default=bool(
                    pc.get("deletion_protection_default", True),
                ),
                secrets_manager_prefix=str(
                    pc.get("managed_service_secrets_prefix", "astrolift/managed"),
                ),
            )
        if kind == "mysql":
            from aws.managed.mysql_rds import RDSMySQLConfig

            return RDSMySQLConfig(
                region=region,
                db_subnet_group=subnet_group,
                security_group_ids=sg_ids,
                instance_name_prefix=str(pc.get("instance_name_prefix", "astrolift")),
                engine_version=str(pc.get("mysql_engine_version", "")),
                backup_retention_days=int(pc.get("backup_retention_days", 7)),
                multi_az_default=bool(pc.get("multi_az_default", False)),
                deletion_protection_default=bool(
                    pc.get("deletion_protection_default", True),
                ),
            )
        from aws.managed.postgres_rds import RDSConfig

        return RDSConfig(
            region=region,
            db_subnet_group=subnet_group,
            security_group_ids=sg_ids,
            instance_name_prefix=str(pc.get("instance_name_prefix", "astrolift")),
            engine_version=str(pc.get("postgres_engine_version", "")),
            backup_retention_days=int(pc.get("backup_retention_days", 7)),
            multi_az_default=bool(pc.get("multi_az_default", False)),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
        )

    if kind in ("redis", "cache") and variant.startswith("elasticache_serverless_"):
        from aws.managed._networking import ensure_serverless_cache_networking
        from aws.managed.elasticache_serverless import ElastiCacheServerlessConfig

        engines = {
            "elasticache_serverless_valkey": "valkey",
            "elasticache_serverless_redis": "redis",
            "elasticache_serverless_memcached": "memcached",
        }
        try:
            engine = engines[variant]
        except KeyError as exc:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: unknown AWS serverless cache variant {variant!r}",
            ) from exc
        subnet_ids, sg_ids = ensure_serverless_cache_networking(
            cluster,
            region=region,
        )
        return ElastiCacheServerlessConfig(
            region=region,
            subnet_ids=subnet_ids,
            security_group_ids=sg_ids,
            engine=engine,
            cache_name_prefix=str(pc.get("instance_name_prefix", "astrolift")),
            snapshot_retention_days=int(pc.get("snapshot_retention_days", 7)),
            kms_key_arn=str(pc.get("kms_key_id", "")),
            network_type=str(pc.get("serverless_cache_network_type", "ipv4")),
            secrets_manager_prefix=str(
                pc.get("managed_service_secrets_prefix", "astrolift/managed"),
            ),
            auth_mode_default=str(pc.get("serverless_cache_auth_mode", "password")),
        )

    if kind == "cache" and variant == "elasticache_memcached":
        from aws.managed._networking import ensure_db_networking
        from aws.managed.memcached_elasticache import ElastiCacheMemcachedConfig

        subnet_group, sg_ids = ensure_db_networking(
            cluster,
            region=region,
            port=11211,
            service="elasticache",
        )
        return ElastiCacheMemcachedConfig(
            region=region,
            cache_subnet_group=subnet_group,
            security_group_ids=sg_ids,
            cluster_name_prefix=str(pc.get("instance_name_prefix", "astrolift")),
            engine_version=str(pc.get("memcached_engine_version", "")),
            transit_encryption_default=bool(pc.get("transit_encryption_default", True)),
        )

    if kind == "redis" and variant == "memorydb":
        from aws.managed._networking import ensure_memorydb_networking
        from aws.managed.memorydb import MemoryDBConfig

        subnet_group, sg_ids = ensure_memorydb_networking(
            cluster,
            region=region,
        )
        return MemoryDBConfig(
            region=region,
            subnet_group=subnet_group,
            security_group_ids=sg_ids,
            engine=str(pc.get("memorydb_engine", "valkey")),
            cluster_name_prefix=str(pc.get("instance_name_prefix", "astrolift")),
            engine_version=str(pc.get("memorydb_engine_version", "")),
            snapshot_retention_days=int(pc.get("snapshot_retention_days", 7)),
            kms_key_arn=str(pc.get("kms_key_id", "")),
            tls_enabled_default=bool(pc.get("transit_encryption_default", True)),
            secrets_manager_prefix=str(
                pc.get("managed_service_secrets_prefix", "astrolift/managed"),
            ),
            auth_mode_default=str(pc.get("memorydb_auth_mode", "password")),
        )

    if kind == "redis":
        from aws.managed._networking import ensure_db_networking
        from aws.managed.redis_elasticache import ElastiCacheConfig

        subnet_group, sg_ids = ensure_db_networking(
            cluster,
            region=region,
            port=6379,
            service="elasticache",
        )
        return ElastiCacheConfig(
            region=region,
            cache_subnet_group=subnet_group,
            security_group_ids=sg_ids,
            engine="valkey" if variant == "elasticache_valkey" else "redis",
            replication_group_prefix=str(pc.get("instance_name_prefix", "astrolift")),
            engine_version=str(pc.get("redis_engine_version", "")),
            transit_encryption_default=bool(pc.get("transit_encryption_default", True)),
            at_rest_encryption_default=bool(pc.get("at_rest_encryption_default", True)),
            snapshot_retention_days=int(pc.get("snapshot_retention_days", 7)),
        )

    if kind == "queue":
        from aws.managed.queue_sqs import SQSConfig

        return SQSConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
            queue_name_prefix=str(pc.get("queue_name_prefix", "astrolift")),
            default_visibility_timeout_seconds=int(
                pc.get("sqs_default_visibility_timeout_seconds", 30),
            ),
            default_message_retention_seconds=int(
                pc.get("sqs_default_message_retention_seconds", 4 * 86400),
            ),
            fifo_default=bool(pc.get("sqs_fifo_default", False)),
            kms_key_id=str(pc.get("sqs_kms_key_id") or pc.get("kms_key_id") or ""),
            sqs_managed_sse_default=bool(pc.get("sqs_managed_sse_default", True)),
        )

    if kind == "topic" and variant.startswith("sns_"):
        from aws.managed.topic_sns import SNSConfig

        return SNSConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
            topic_name_prefix=str(pc.get("sns_topic_name_prefix", "astrolift")),
            kms_key_id=str(pc.get("sns_kms_key_id") or pc.get("kms_key_id") or ""),
            tracing_config_default=str(pc.get("sns_tracing_config_default", "PassThrough")),
        )

    if kind == "sms" and variant == "sns_sms":
        from aws.managed.sms_sns import SNSSmsConfig

        return SNSSmsConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
            topic_name_prefix=str(pc.get("sns_sms_topic_name_prefix", "astrolift-sms")),
            kms_key_id=str(pc.get("sns_sms_kms_key_id") or pc.get("sns_kms_key_id") or ""),
            sender_id_default=str(pc.get("sns_sms_sender_id_default", "")),
            sms_type_default=str(pc.get("sns_sms_type_default", "Transactional")),
            deletion_protection_default=bool(pc.get("deletion_protection_default", True)),
            tracing_config_default=str(pc.get("sns_tracing_config_default", "PassThrough")),
        )

    if kind == "event_bus" and variant == "eventbridge":
        from aws.managed.event_bus_eventbridge import EventBridgeConfig

        return EventBridgeConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
            event_bus_name_prefix=str(pc.get("eventbridge_name_prefix", "astrolift")),
            kms_key_id=str(pc.get("eventbridge_kms_key_id") or pc.get("kms_key_id") or ""),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
        )

    if kind == "stream" and variant == "kinesis":
        from aws.managed.stream_kinesis import KinesisConfig

        return KinesisConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
            stream_name_prefix=str(pc.get("kinesis_stream_name_prefix", "astrolift")),
            kms_key_id=str(pc.get("kinesis_kms_key_id") or pc.get("kms_key_id") or ""),
            stream_mode_default=str(pc.get("kinesis_stream_mode_default", "ON_DEMAND")),
            retention_hours_default=int(pc.get("kinesis_retention_hours_default", 24)),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
        )

    if kind == "stream" and variant == "firehose":
        from aws.managed.stream_firehose import FirehoseConfig

        return FirehoseConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
            delivery_stream_name_prefix=str(
                pc.get("firehose_delivery_stream_name_prefix", "astrolift"),
            ),
            kms_key_id=str(pc.get("firehose_kms_key_id") or pc.get("kms_key_id") or ""),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
            poll_delay_seconds=float(pc.get("firehose_poll_delay_seconds", 5)),
            max_poll_attempts=int(pc.get("firehose_max_poll_attempts", 60)),
        )

    if kind == "event_stream" and variant in {"msk", "msk_serverless"}:
        from aws.managed._networking import ensure_msk_networking
        from aws.managed.event_stream_msk import MSKConfig

        subnet_ids, security_group_ids = ensure_msk_networking(
            cluster,
            region=region,
        )
        return MSKConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
            cluster_name_prefix=str(pc.get("msk_cluster_name_prefix", "astrolift")),
            subnet_ids=tuple(subnet_ids),
            security_group_ids=tuple(security_group_ids),
            kms_key_arn=str(pc.get("msk_kms_key_arn") or pc.get("kms_key_id") or ""),
            kafka_version_default=str(pc.get("msk_kafka_version", "")),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
            poll_delay_seconds=float(pc.get("msk_poll_delay_seconds", 15)),
            max_poll_attempts=int(pc.get("msk_max_poll_attempts", 80)),
        )

    if kind == "mq" and variant in {"amazon_mq_rabbitmq", "amazon_mq_activemq"}:
        from aws.managed._networking import ensure_mq_networking
        from aws.managed.mq_amazon import AmazonMQConfig

        subnet_ids, security_group_ids = ensure_mq_networking(
            cluster,
            region=region,
        )
        engine_key = "rabbitmq" if variant == "amazon_mq_rabbitmq" else "activemq"
        return AmazonMQConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
            broker_name_prefix=str(pc.get("amazon_mq_broker_name_prefix", "astrolift")),
            subnet_ids=tuple(subnet_ids),
            security_group_ids=tuple(security_group_ids),
            kms_key_id=str(pc.get("amazon_mq_kms_key_id") or pc.get("kms_key_id") or ""),
            engine_version_default=str(
                pc.get(f"amazon_mq_{engine_key}_engine_version") or pc.get("amazon_mq_engine_version") or ""
            ),
            secrets_manager_prefix=str(
                pc.get("amazon_mq_secrets_manager_prefix", "astrolift/mq"),
            ),
            admin_username=str(pc.get("amazon_mq_admin_username", "astrolift")),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
            poll_delay_seconds=float(pc.get("amazon_mq_poll_delay_seconds", 10)),
            max_poll_attempts=int(pc.get("amazon_mq_max_poll_attempts", 90)),
        )

    if kind == "filesystem" and variant == "efs":
        from aws.managed._networking import ensure_efs_networking
        from aws.managed.filesystem_efs import EFSConfig

        subnet_ids, security_group_ids = ensure_efs_networking(
            cluster,
            region=region,
        )
        return EFSConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
            subnet_ids=tuple(subnet_ids),
            security_group_ids=tuple(security_group_ids),
            kms_key_id=str(pc.get("efs_kms_key_id") or pc.get("kms_key_id") or ""),
            creation_token_prefix=str(pc.get("efs_creation_token_prefix", "astrolift")),
            deletion_protection_default=bool(pc.get("deletion_protection_default", True)),
            poll_delay_seconds=float(pc.get("efs_poll_delay_seconds", 5)),
            max_poll_attempts=int(pc.get("efs_max_poll_attempts", 120)),
        )

    if kind == "filesystem" and variant in {"fsx_lustre", "fsx_openzfs", "fsx_windows"}:
        from aws.managed._networking import ensure_fsx_networking
        from aws.managed.filesystem_fsx import FSxConfig

        subnet_ids, security_group_ids = ensure_fsx_networking(
            cluster,
            region=region,
            variant=variant,
        )
        suffix = variant.removeprefix("fsx_")
        return FSxConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
            subnet_ids=tuple(subnet_ids),
            security_group_ids=tuple(security_group_ids),
            kms_key_id=str(
                pc.get(f"fsx_{suffix}_kms_key_id") or pc.get("fsx_kms_key_id") or pc.get("kms_key_id") or ""
            ),
            client_token_prefix=str(pc.get("fsx_client_token_prefix", "astrolift")),
            deletion_protection_default=bool(pc.get("deletion_protection_default", True)),
            poll_delay_seconds=float(pc.get("fsx_poll_delay_seconds", 10)),
            max_poll_attempts=int(pc.get("fsx_max_poll_attempts", 120)),
        )

    if kind in ("search", "vector_index") and variant.startswith("opensearch_serverless"):
        from aws.managed._networking import ensure_opensearch_serverless_networking
        from aws.managed.opensearch_serverless import OpenSearchServerlessConfig

        public_access = bool(pc.get("opensearch_serverless_public_access", False))
        vpc_endpoint_ids = (
            []
            if public_access
            else ensure_opensearch_serverless_networking(
                cluster,
                region=region,
            )
        )
        return OpenSearchServerlessConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
            collection_type="VECTORSEARCH" if kind == "vector_index" else "SEARCH",
            collection_name_prefix=str(
                pc.get("opensearch_serverless_collection_prefix", "astrolift"),
            ),
            vpc_endpoint_ids=vpc_endpoint_ids,
            public_access_default=public_access,
            kms_key_arn=str(pc.get("kms_key_id", "")),
            standby_replicas_default=str(
                pc.get("opensearch_serverless_standby_replicas", "DISABLED"),
            ),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
            data_access_principals=list(
                pc.get("opensearch_serverless_data_access_principals") or [],
            ),
            source_services=list(pc.get("opensearch_serverless_source_services") or []),
        )

    if kind == "document_db" and variant.startswith("documentdb"):
        from aws.managed._networking import ensure_documentdb_networking
        from aws.managed.documentdb import DocumentDBConfig

        subnet_group, security_group_ids = ensure_documentdb_networking(
            cluster,
            region=region,
        )
        return DocumentDBConfig(
            region=region,
            db_subnet_group=subnet_group,
            security_group_ids=security_group_ids,
            cluster_name_prefix=str(pc.get("documentdb_cluster_name_prefix", "astrolift")),
            engine_version=str(pc.get("documentdb_engine_version", "5.0.0")),
            serverless_v2=variant == "documentdb_serverless_v2",
            backup_retention_days=int(pc.get("documentdb_backup_retention_days", 7)),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
            secrets_manager_prefix=str(
                pc.get("documentdb_secrets_manager_prefix", "astrolift/documentdb"),
            ),
            master_username=str(pc.get("documentdb_master_username", "astrolift")),
        )

    if kind == "graph_db" and variant.startswith("neptune"):
        from aws.managed._networking import ensure_neptune_networking
        from aws.managed.neptune import NeptuneConfig

        subnet_group, security_group_ids = ensure_neptune_networking(
            cluster,
            region=region,
        )
        return NeptuneConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
            db_subnet_group=subnet_group,
            security_group_ids=security_group_ids,
            cluster_name_prefix=str(pc.get("neptune_cluster_name_prefix", "astrolift")),
            engine_version=str(pc.get("neptune_engine_version", "")),
            serverless_v2=variant == "neptune_serverless",
            backup_retention_days=int(pc.get("neptune_backup_retention_days", 7)),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
            iam_auth_default=bool(pc.get("neptune_iam_auth_default", True)),
        )

    if kind == "warehouse" and variant.startswith("redshift"):
        from aws.managed._networking import ensure_redshift_networking

        serverless = variant == "redshift_serverless"
        subnet_group, subnet_ids, security_group_ids = ensure_redshift_networking(
            cluster,
            region=region,
            serverless=serverless,
        )
        if serverless:
            from aws.managed.redshift_serverless import RedshiftServerlessConfig

            return RedshiftServerlessConfig(
                region=region,
                account_id=str(pc.get("account_id", "")),
                subnet_ids=subnet_ids,
                security_group_ids=security_group_ids,
                name_prefix=str(pc.get("redshift_name_prefix", "astrolift")),
                base_capacity_default=int(pc.get("redshift_serverless_base_capacity", 8)),
                snapshot_retention_days=int(pc.get("redshift_snapshot_retention_days", 30)),
                deletion_protection_default=bool(
                    pc.get("deletion_protection_default", True),
                ),
                manage_admin_password_default=True,
                admin_username=str(pc.get("redshift_admin_username", "astrolift")),
            )

        from aws.managed.redshift import RedshiftConfig

        return RedshiftConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
            cluster_subnet_group=subnet_group,
            security_group_ids=security_group_ids,
            cluster_name_prefix=str(pc.get("redshift_name_prefix", "astrolift")),
            node_type_default=str(pc.get("redshift_node_type_default", "ra3.xlplus")),
            automated_snapshot_retention_days=int(
                pc.get("redshift_automated_snapshot_retention_days", 7),
            ),
            manual_snapshot_retention_days=int(
                pc.get("redshift_snapshot_retention_days", 30),
            ),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
            manage_admin_password_default=True,
            master_username=str(pc.get("redshift_admin_username", "astrolift")),
        )

    if kind == "wide_column" and variant == "keyspaces":
        from aws.managed._networking import ensure_keyspaces_networking
        from aws.managed.keyspaces import KeyspacesConfig

        public_endpoint = bool(pc.get("keyspaces_public_endpoint", False))
        return KeyspacesConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
            name_prefix=str(pc.get("keyspaces_name_prefix", "astrolift")),
            throughput_mode_default=str(
                pc.get("keyspaces_throughput_mode_default", "PAY_PER_REQUEST"),
            ),
            point_in_time_recovery_default=bool(
                pc.get("keyspaces_point_in_time_recovery_default", True),
            ),
            vpc_endpoint_id=(
                ""
                if public_endpoint
                else ensure_keyspaces_networking(
                    cluster,
                    region=region,
                )
            ),
        )

    if kind == "search":
        from aws.managed.search_opensearch import OpenSearchSearchConfig

        return OpenSearchSearchConfig(
            region=region,
            domain_name_prefix=str(pc.get("search_domain_name_prefix", "astrolift")),
            engine_version=str(pc.get("opensearch_engine_version", "OpenSearch_2.11")),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
        )

    if kind == "vector_index":
        from aws.managed.vector_opensearch import OpenSearchVectorConfig

        return OpenSearchVectorConfig(
            region=region,
            domain_name_prefix=str(pc.get("vector_domain_name_prefix", "astrolift-vec")),
            engine_version=str(pc.get("opensearch_engine_version", "OpenSearch_2.11")),
            instance_count_default=int(pc.get("vector_instance_count_default", 1)),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
        )

    if kind == "email":
        from aws.managed.email_ses import SESEmailConfig

        return SESEmailConfig(
            region=region,
            identity_prefix=str(pc.get("ses_identity_prefix", "astrolift")),
            base_domain=str(pc.get("base_domain", "")),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
        )

    if kind == "model_endpoint":
        from aws.managed.model_endpoint_bedrock import AmazonBedrockConfig

        return AmazonBedrockConfig(
            region=region,
            default_model_id=str(
                pc.get("bedrock_default_model_id", "anthropic.claude-3-haiku-20240307-v1:0"),
            ),
            invocation_log_retention_days=int(
                pc.get("bedrock_invocation_log_retention_days", 30),
            ),
        )

    if kind == "time_series":
        from aws.managed.timeseries_timestream import TimestreamConfig

        return TimestreamConfig(
            region=region,
            database_name_prefix=str(pc.get("database_name_prefix", "astrolift")),
            table_name_default=str(pc.get("timestream_table_name_default", "metrics")),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
            kms_key_id=str(pc.get("kms_key_id", "")),
        )

    raise ClusterObservabilityError(
        f"cluster {cluster.slug}: no managed-service config builder for "
        f"kind={kind!r} (plugin={plugin_slug!r})",
    )


def list_app_pods(
    *,
    cluster: TenantCluster,
    namespace: str,
    app_slug: str,
    task_id: str = "",
) -> list[Any]:
    """Resolver-facing entry. Returns a list of ``PodInfo`` (from
    the provider SDK). Resolver layer is responsible for catching
    :class:`ClusterObservabilityError` and rendering the empty UI.

    ``task_id`` (#891), when set, discovers an agent task pod by its
    ``astrolift.dev/task-id`` label rather than the ``app_slug`` label."""
    driver = _driver_for_cluster(cluster)
    auth = _auth_for_cluster(cluster)
    return driver.list_pods(auth=auth, namespace=namespace, app_slug=app_slug, task_id=task_id)


def list_app_pod_warning_events(
    *,
    cluster: TenantCluster,
    namespace: str,
    limit: int = 100,
) -> list[Any]:
    """Resolver-facing entry — recent Warning events in ``namespace`` (#666).

    Returns a list of ``ClusterEvent`` dataclasses (provider SDK shape);
    the caller is responsible for the pod-name → most-recent-event join.
    The driver layer fetches all Warning events in the namespace so we
    can answer 'what went wrong on this pod' for any pod the namespace
    surfaced, without N round-trips.

    Errors raise :class:`ClusterObservabilityError`; the resolver
    swallows + renders the workloads list without inline event chips
    rather than 502ing the page.
    """
    # The `list_events` driver method takes a ClusterContext (not auth).
    # We lazy-import the context builder from core.cluster_management to
    # avoid a hard dep on that module's TenantCluster import order.
    from core.cluster_management import _context_for_cluster

    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    try:
        return driver.list_events(
            ctx,
            namespaces=[namespace],
            event_type="Warning",
            limit=limit,
        )
    except AttributeError as exc:
        # The test-override driver (_OverrideDriver) doesn't ship
        # list_events; that's intentional for unit tests that don't
        # exercise the event path.  Surface as observability-empty
        # rather than crashing.
        raise ClusterObservabilityError(
            f"driver for cluster {cluster.slug!r} does not implement list_events",
        ) from exc


def stream_app_logs(
    *,
    cluster: TenantCluster,
    namespace: str,
    pod_name: str,
    container: str | None,
    tail_lines: int = 100,
    follow: bool = True,
) -> AsyncIterator[Any]:
    """Resolver-facing entry for the log subscription. Returns an
    async iterator that yields ``PodLogLine`` (from the provider
    SDK). The subscription layer wraps this with an explicit
    ``aclose()`` finally — see :mod:`astrolift_lifecycle.schema.subscriptions`.
    """
    driver = _driver_for_cluster(cluster)
    auth = _auth_for_cluster(cluster)
    return driver.stream_logs(
        auth=auth,
        namespace=namespace,
        pod_name=pod_name,
        container=container,
        tail_lines=tail_lines,
        follow=follow,
    )


# Upper bound on a one-shot agent-task log read. Generous enough to drain a
# real tail over a slow link, short enough that a stuck stream can never wedge
# the GraphQL worker (#1013).
_TASK_LOG_READ_TIMEOUT_SECONDS = 15.0


async def fetch_task_pod_logs(
    *,
    cluster: TenantCluster,
    namespace: str,
    task_guid: str,
    pod_name_hint: str = "",
    tail: int = 200,
) -> list[str]:
    """Operator-facing one-shot log read for an agent-task pod.

    Resolves the task's pod in ``namespace`` and returns up to ``tail``
    of its most recent log message lines (no timestamps, no follow).
    Built on the same driver plumbing the app-log surface uses —
    :func:`list_app_pods` for discovery and :func:`stream_app_logs`
    (``follow=False``) for the byte stream — so it inherits the
    test-injectable pod/log backends.

    Pod discovery order:

    1. ``list_app_pods`` keyed on ``task_guid`` as a ``task_id``
       selector. The K8s Job spawner labels each agent pod
       ``astrolift.dev/task-id=<task.guid>``, and the live pod backend
       turns a non-empty ``task_id`` into an
       ``astrolift.dev/task-id=<guid>`` label query, so the agent pod is
       resolved exactly on a real cluster (#891).
    2. ``pod_name_hint`` — the dispatcher records the spawned Job name
       on ``AgentTask.pod_name``; passed through as a last-resort pod
       name so a single-pod Job whose pod name equals the Job name (or
       a future exact pod name) still streams.

    Returns ``[]`` — never raises — when the cluster can't be turned
    into a usable driver, no pod is found, or the stream yields nothing
    yet. The resolver layer surfaces the empty list as "no logs yet"
    rather than a 500.
    """
    from asgiref.sync import sync_to_async

    tail = max(0, int(tail))
    if tail == 0:
        return []

    def _discover() -> str:
        try:
            pods = list_app_pods(
                cluster=cluster,
                namespace=namespace,
                app_slug=task_guid,
                task_id=task_guid,
            )
        except ClusterObservabilityError:
            return ""
        except Exception:
            logger.exception("fetch_task_pod_logs: pod discovery failed for task %s", task_guid)
            return ""
        for pod in pods:
            name = getattr(pod, "name", "") or ""
            if name:
                return name
        return ""

    pod_name = await sync_to_async(_discover)()
    if not pod_name:
        pod_name = (pod_name_hint or "").strip()
    if not pod_name:
        return []

    try:
        inner = stream_app_logs(
            cluster=cluster,
            namespace=namespace,
            pod_name=pod_name,
            container=None,
            tail_lines=tail,
            follow=False,
        )
    except ClusterObservabilityError:
        return []
    except Exception:
        logger.exception("fetch_task_pod_logs: failed to open log stream for pod %s", pod_name)
        return []

    lines: list[str] = []

    async def _collect() -> None:
        async for line in inner:
            message = getattr(line, "message", "")
            lines.append(message if isinstance(message, str) else str(message))
            if len(lines) >= tail:
                break

    try:
        # Hard bound: a one-shot operator log read must never block the
        # GraphQL worker, regardless of driver behaviour (see #1013). The
        # follow=False path now terminates on EOF, but the timeout is the
        # backstop for any future driver that doesn't.
        await asyncio.wait_for(_collect(), timeout=_TASK_LOG_READ_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning(
            "fetch_task_pod_logs: read exceeded %ss for pod %s; returning partial tail",
            _TASK_LOG_READ_TIMEOUT_SECONDS,
            pod_name,
        )
    except Exception:
        # A mid-stream failure still returns whatever we collected —
        # partial logs beat a hard error on an operator-facing read.
        logger.exception("fetch_task_pod_logs: stream raised for pod %s", pod_name)
    finally:
        # Explicit aclose so the kubelet socket releases even when we
        # break out early after hitting the tail cap — async for does
        # not aclose its iterator on a break.
        try:
            await inner.aclose()
        except Exception:
            logger.exception("fetch_task_pod_logs: aclose failed for pod %s", pod_name)

    return lines


# ---- Multi-pod fan-out (#482) ------------------------------------
#
# Fan N per-pod streams into one merged async generator. Each line
# is tagged with its source pod so the consumer can identify which
# replica produced it.
#
# Implementation notes:
#   * One ``asyncio.Task`` per pod drains its stream into a shared
#     ``asyncio.Queue``. The yield-loop reads from the queue and
#     forwards to the caller.
#   * Pod-discovery refresh runs every ``refresh_interval_seconds``
#     so replicas that come up mid-stream get auto-subscribed; the
#     refresh is cooperative — we only ADD streams, never replace
#     existing ones, so a flapping pod doesn't get its tail
#     re-fetched on every cycle.
#   * Per-pod errors are isolated: a bad pod's task records the
#     error and exits; the other pods keep streaming. This matches
#     the resolver-layer contract that one broken replica should
#     not kill the whole tail.
#   * Cancellation: when the consumer drops, the outer generator's
#     ``aclose()`` cancels every child task and drains the queue.
#     Each child task wraps its inner ``async for`` in a
#     try/finally with ``aclose()`` so the kubelet socket releases
#     even when the task is cancelled mid-yield.

_REFRESH_INTERVAL_SECONDS_DEFAULT = 10.0
_MAX_PODS_DEFAULT = 50


async def stream_app_logs_multi(
    *,
    cluster: TenantCluster,
    namespace: str,
    app_slug: str,
    workload_slug: str | None = None,
    container: str | None = None,
    tail_lines: int = 100,
    follow: bool = True,
    refresh_interval_seconds: float = _REFRESH_INTERVAL_SECONDS_DEFAULT,
    max_pods: int = _MAX_PODS_DEFAULT,
) -> AsyncIterator[Any]:
    """Stream log lines from every pod of ``app_slug`` (filtered to
    ``workload_slug`` when given) as one merged async iterator.

    Each yielded line is a ``PodLogLine`` carrying ``pod_name`` so the
    UI can group / colorize per replica. New pods that appear during
    the subscription are auto-subscribed every
    ``refresh_interval_seconds`` (default 10s). ``max_pods`` caps the
    fan-out so a runaway scale-up can't exhaust the connection pool.

    ``follow=False`` runs a one-shot tail of every current pod and
    completes after the last per-pod stream drains — useful for a
    snapshot view without a long-lived WebSocket.

    Errors from a single pod's stream are logged and the pod's task
    exits silently; sibling pods keep streaming. Cancellation of the
    outer generator tears down every child task so kubelet sockets
    release back to the pool.
    """
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1024)
    sentinel = object()
    # Track pods we've already subscribed to so the refresh loop only
    # opens streams for new replicas — re-subscribing an existing pod
    # would double-emit the tail-replay.
    subscribed: set[str] = set()
    children: dict[str, asyncio.Task] = {}

    async def _pump_one(pod_name: str) -> None:
        """Drain one pod's stream into the shared queue."""
        try:
            inner = stream_app_logs(
                cluster=cluster,
                namespace=namespace,
                pod_name=pod_name,
                container=container,
                tail_lines=tail_lines,
                follow=follow,
            )
        except Exception:
            logger.exception(
                "stream_app_logs_multi: failed to open stream for pod %s",
                pod_name,
            )
            return
        try:
            async for line in inner:
                await queue.put(line)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "stream_app_logs_multi: pod %s stream raised; tearing down its task",
                pod_name,
            )
        finally:
            try:
                await inner.aclose()
            except Exception:
                logger.exception(
                    "stream_app_logs_multi: aclose failed for pod %s",
                    pod_name,
                )

    def _discover_pods() -> list[str]:
        try:
            pods = list_app_pods(
                cluster=cluster,
                namespace=namespace,
                app_slug=app_slug,
            )
        except Exception:
            logger.exception(
                "stream_app_logs_multi: pod discovery failed for app %s",
                app_slug,
            )
            return []
        names: list[str] = []
        for pod in pods:
            name = getattr(pod, "name", "")
            if not name:
                continue
            if workload_slug:
                pod_workload = getattr(pod, "workload", "") or ""
                if pod_workload != workload_slug:
                    continue
            names.append(name)
            if len(names) >= max_pods:
                break
        return names

    async def _refresh_loop() -> None:
        """Periodically discover new pods + subscribe them."""
        while True:
            await asyncio.sleep(refresh_interval_seconds)
            new_pods = [p for p in _discover_pods() if p not in subscribed]
            for pod_name in new_pods:
                subscribed.add(pod_name)
                children[pod_name] = asyncio.create_task(_pump_one(pod_name))

    # Open initial subscriptions synchronously so the first lines
    # arrive promptly.
    initial_pods = _discover_pods()
    if not initial_pods:
        # No pods to tail — yield nothing and finish; the subscription
        # layer surfaces an empty stream rather than an error.
        return
    for pod_name in initial_pods:
        subscribed.add(pod_name)
        children[pod_name] = asyncio.create_task(_pump_one(pod_name))

    refresh_task: asyncio.Task | None = None
    if follow:
        refresh_task = asyncio.create_task(_refresh_loop())

    async def _finalize_when_done() -> None:
        """In non-follow mode, sentinel the queue once every child
        finishes so the consumer loop terminates."""
        await asyncio.gather(*children.values(), return_exceptions=True)
        await queue.put(sentinel)

    completion_task: asyncio.Task | None = None
    if not follow:
        completion_task = asyncio.create_task(_finalize_when_done())

    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                return
            yield item
    finally:
        # Tear down every child task + drain the queue so producer
        # tasks awaiting ``queue.put`` don't block forever.
        if refresh_task is not None:
            refresh_task.cancel()
        if completion_task is not None:
            completion_task.cancel()
        for task in children.values():
            task.cancel()
        # Wait for cancellation to propagate so per-pod finally
        # blocks (aclose) get a chance to run. Swallow the
        # CancelledError each task raises.
        await asyncio.gather(
            *children.values(),
            *(t for t in (refresh_task, completion_task) if t is not None),
            return_exceptions=True,
        )
