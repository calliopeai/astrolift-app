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
from core.cluster_credentials import (
    CREDENTIAL_REFUSALS,
    assert_credential_supported,
    credential_for_cluster,
    stamp_credential,
)

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
    from _sdk.k8s_naming import app_namespace

    org_slug = getattr(getattr(app, "organization", None), "slug", "") or ""
    return app_namespace(organization_slug=org_slug, app_slug=str(app.slug))


def namespace_for_environment(env: Any) -> str:
    """The namespace one environment's objects live in (#1922).

    Its own ``AppEnvironment.k8s_namespace`` when it has one (a preview, or
    an environment that shares a cluster with another environment of the
    app), else the app namespace. Mirrors
    ``core.app_deploy.namespace_for_environment``, which the deploy path
    writes through, so a read looks where the deploy wrote.
    """
    recorded = (getattr(env, "k8s_namespace", "") or "").strip()
    if recorded:
        return recorded
    return namespace_for_app(env.registered_app)


def namespace_for_app_environment(app: Any, environment_name: str | None) -> str:
    """The namespace of ``app``'s environment named ``environment_name``, or
    the app namespace when none is named or no live one has that name.

    For the reads that take an environment by name: the app namespace is
    where every environment rendered before #1922, and where the primary
    environment still does.
    """
    if environment_name:
        from astrolift_lifecycle.models import AppEnvironment

        recorded = (
            AppEnvironment.objects.filter(
                registered_app=app,
                name=environment_name,
                deleted_at__isnull=True,
            )
            .values_list("k8s_namespace", flat=True)
            .first()
        )
        if recorded:
            return str(recorded)
    return namespace_for_app(app)


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

    def list_pods(
        self,
        *,
        auth: Any,
        namespace: str,
        app_slug: str,
        task_id: str = "",
        job_name: str = "",
    ) -> list[Any]:
        if self._pod_backend is None:
            raise ClusterObservabilityError(
                "list_pods called without a pod backend override; set_pod_backend_for_tests was not called",
            )
        kwargs: dict[str, Any] = {"auth": auth, "namespace": namespace, "app_slug": app_slug}
        # Forward task_id/job_name only when set so app-path test backends
        # (whose list_pods predates the kwarg) keep working unchanged (#891, #1712).
        if task_id:
            kwargs["task_id"] = task_id
        if job_name:
            kwargs["job_name"] = job_name
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
        assert_credential_supported(cluster, capability="cluster")
    except CREDENTIAL_REFUSALS as exc:
        raise ClusterObservabilityError(str(exc)) from exc
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
    """The cluster driver's config, carrying the cluster's identity.

    Stamped on the way out for the same reason ``managed_config_for``
    does it: the EKS driver builds eks/sts/ec2 clients from this config,
    so leaving it ambient would reach the control plane's own account
    while every ARN in the row named the tenant's (#1422).
    """
    return stamp_credential(_config_for_uncredentialed(plugin_slug, cluster), cluster)


def _config_for_uncredentialed(plugin_slug: str, cluster: TenantCluster) -> Any:
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
            allowed_service_accounts=tuple(
                str(value) for value in pc.get("cloud_functions_allowed_service_accounts") or []
            ),
        )

    if pair == ("api_gateway", "api_gateway") or (kind == "api_gateway" and not variant):
        from gcp.managed.api_gateway import APIGatewayConfig

        return APIGatewayConfig(
            project_id=project_id,
            region=str(pc.get("api_gateway_region") or region),
            api_id_prefix=str(pc.get("api_gateway_api_id_prefix", "astrolift")),
            gateway_id_prefix=str(pc.get("api_gateway_gateway_id_prefix", "astrolift")),
            config_id_prefix=str(pc.get("api_gateway_config_id_prefix", "cfg")),
            api_endpoint=str(
                pc.get("api_gateway_api_endpoint", "https://apigateway.googleapis.com/v1"),
            ),
            deletion_protection_default=bool(
                pc.get("api_gateway_deletion_protection_default", True),
            ),
            operation_timeout_seconds=float(
                pc.get("api_gateway_operation_timeout_seconds", 1800),
            ),
            poll_interval_seconds=float(
                pc.get("api_gateway_operation_poll_interval_seconds", 5),
            ),
            allowed_service_accounts=tuple(
                str(value) for value in pc.get("api_gateway_allowed_service_accounts") or []
            ),
        )

    if pair == ("event_stream", "managed_kafka") or (kind == "event_stream" and not variant):
        from gcp.managed.event_stream_managed_kafka import ManagedKafkaConfig

        return ManagedKafkaConfig(
            project_id=project_id,
            location=str(pc.get("managed_kafka_location") or region),
            cluster_id_prefix=str(pc.get("managed_kafka_cluster_id_prefix", "astrolift")),
            subnet_names=tuple(str(item) for item in pc.get("managed_kafka_subnet_names", ())),
            api_endpoint=str(
                pc.get("managed_kafka_api_endpoint", "https://managedkafka.googleapis.com/v1"),
            ),
            deletion_protection_default=bool(
                pc.get("managed_kafka_deletion_protection_default", True),
            ),
            operation_timeout_seconds=float(
                pc.get("managed_kafka_operation_timeout_seconds", 1800),
            ),
            poll_interval_seconds=float(
                pc.get("managed_kafka_operation_poll_interval_seconds", 5),
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
            allowed_service_accounts=tuple(
                str(value) for value in pc.get("eventarc_allowed_service_accounts") or []
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

    if pair == ("filesystem", "filestore") or (kind == "filesystem" and not variant):
        from gcp.managed.filesystem_filestore import FilestoreConfig

        return FilestoreConfig(
            project_id=project_id,
            location=str(pc.get("filestore_location") or region),
            network=str(pc.get("filestore_network") or pc.get("network") or ac.get("network") or ""),
            instance_name_prefix=str(
                pc.get("filestore_instance_name_prefix", "astrolift"),
            ),
            share_name_default=str(pc.get("filestore_share_name_default", "data")),
            tier_default=str(pc.get("filestore_tier_default", "REGIONAL")),
            protocol_default=str(pc.get("filestore_protocol_default", "NFS_V3")),
            connect_mode_default=str(
                pc.get("filestore_connect_mode_default", "PRIVATE_SERVICE_CONNECT"),
            ),
            reserved_ip_range=str(pc.get("filestore_reserved_ip_range", "")),
            psc_endpoint_project=str(pc.get("filestore_psc_endpoint_project", "")),
            kms_key_name=str(
                pc.get("filestore_kms_key_name") or pc.get("kms_key") or "",
            ),
            deletion_protection_default=bool(
                pc.get("filestore_deletion_protection_default", True),
            ),
            backup_location=str(pc.get("filestore_backup_location", "")),
            backup_kms_key=str(pc.get("filestore_backup_kms_key", "")),
            api_endpoint=str(
                pc.get("filestore_api_endpoint", "https://file.googleapis.com/v1"),
            ),
            operation_timeout_seconds=float(
                pc.get("filestore_operation_timeout_seconds", 3600),
            ),
            poll_interval_seconds=float(
                pc.get("filestore_operation_poll_interval_seconds", 5),
            ),
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
            allowed_service_accounts=tuple(
                str(value) for value in pc.get("pubsub_allowed_service_accounts") or []
            ),
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
            allowed_connections=tuple(str(value) for value in pc.get("bigquery_allowed_connections") or []),
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
        )

    if pair == ("workflow_engine", "workflows") or (kind == "workflow_engine" and not variant):
        from gcp.managed.workflow_workflows import WorkflowsConfig

        return WorkflowsConfig(
            project_id=project_id,
            region=region,
            workflow_name_prefix=str(pc.get("workflows_name_prefix", "astrolift")),
            deletion_protection_default=bool(
                pc.get("workflows_deletion_protection_default", True),
            ),
            call_log_level_default=str(
                pc.get("workflows_call_log_level_default", "LOG_ERRORS_ONLY"),
            ),
            execution_history_level_default=str(
                pc.get(
                    "workflows_execution_history_level_default",
                    "EXECUTION_HISTORY_BASIC",
                ),
            ),
            api_endpoint=str(
                pc.get("workflows_api_endpoint", "https://workflows.googleapis.com/v1"),
            ),
            executions_api_endpoint=str(
                pc.get(
                    "workflow_executions_api_endpoint",
                    "https://workflowexecutions.googleapis.com/v1",
                ),
            ),
            operation_timeout_seconds=float(
                pc.get("workflows_operation_timeout_seconds", 900),
            ),
            poll_interval_seconds=float(
                pc.get("workflows_operation_poll_interval_seconds", 2),
            ),
            allowed_service_accounts=tuple(
                str(value) for value in pc.get("workflows_allowed_service_accounts") or []
            ),
        )

    if pair == ("observability", "cloud_operations") or (kind == "observability" and not variant):
        from gcp.managed.observability_cloud_operations import CloudOperationsConfig

        return CloudOperationsConfig(
            project_id=project_id,
            location=str(pc.get("cloud_operations_location") or "global"),
            name_prefix=str(
                pc.get("cloud_operations_name_prefix", "astrolift-observability"),
            ),
            retention_days_default=int(
                pc.get("cloud_operations_retention_days_default", 30),
            ),
            deletion_protection_default=bool(
                pc.get("cloud_operations_deletion_protection_default", True),
            ),
            secret_id_prefix=str(pc.get("secret_id_prefix", "astrolift")),
            logging_api_endpoint=str(
                pc.get(
                    "cloud_operations_logging_api_endpoint",
                    "https://logging.googleapis.com",
                ),
            ),
            monitoring_api_endpoint=str(
                pc.get(
                    "cloud_operations_monitoring_api_endpoint",
                    "https://monitoring.googleapis.com",
                ),
            ),
            request_timeout_seconds=float(
                pc.get("cloud_operations_request_timeout_seconds", 30),
            ),
            operation_timeout_seconds=float(
                pc.get("cloud_operations_operation_timeout_seconds", 900),
            ),
            operation_poll_interval_seconds=float(
                pc.get("cloud_operations_operation_poll_interval_seconds", 2),
            ),
            allowed_writer_identities=tuple(
                str(value) for value in pc.get("cloud_operations_allowed_writer_identities") or []
            ),
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

    if pair == ("cdn", "cloud_cdn") or (kind == "cdn" and not variant):
        from gcp.managed.cdn_cloud import CloudCdnConfig

        return CloudCdnConfig(
            project_id=project_id,
            name_prefix=str(pc.get("cloud_cdn_name_prefix", "astrolift")),
            deletion_protection_default=bool(
                pc.get("cloud_cdn_deletion_protection_default", True),
            ),
            cache_mode_default=str(
                pc.get("cloud_cdn_cache_mode_default", "CACHE_ALL_STATIC"),
            ),
            default_ttl_seconds=int(pc.get("cloud_cdn_default_ttl_seconds", 3600)),
            max_ttl_seconds=int(pc.get("cloud_cdn_max_ttl_seconds", 86400)),
            client_ttl_seconds=int(pc.get("cloud_cdn_client_ttl_seconds", 3600)),
            serve_while_stale_seconds=int(
                pc.get("cloud_cdn_serve_while_stale_seconds", 86400),
            ),
            invalidation_role=str(
                pc.get("cloud_cdn_invalidation_role", "roles/compute.loadBalancerAdmin"),
            ),
            api_endpoint=str(
                pc.get(
                    "cloud_cdn_api_endpoint",
                    "https://compute.googleapis.com/compute/v1",
                ),
            ),
            operation_timeout_seconds=float(
                pc.get("cloud_cdn_operation_timeout_seconds", 900),
            ),
            poll_interval_seconds=float(
                pc.get("cloud_cdn_operation_poll_interval_seconds", 2),
            ),
        )

    if pair == ("private_endpoint", "private_service_connect") or (
        kind == "private_endpoint" and not variant
    ):
        from gcp.managed.private_endpoint_psc import PrivateServiceConnectConfig

        return PrivateServiceConnectConfig(
            project_id=project_id,
            region=region,
            network=str(pc.get("private_service_connect_network", "")),
            subnetwork=str(pc.get("private_service_connect_subnetwork", "")),
            name_prefix=str(pc.get("private_service_connect_name_prefix", "astrolift")),
            labels={
                str(key): str(value)
                for key, value in dict(
                    pc.get("private_service_connect_labels") or {},
                ).items()
            },
            deletion_protection_default=bool(
                pc.get("private_service_connect_deletion_protection_default", True),
            ),
            api_endpoint=str(
                pc.get(
                    "private_service_connect_api_endpoint",
                    "https://compute.googleapis.com/compute/v1",
                ),
            ),
            operation_timeout_seconds=float(
                pc.get("private_service_connect_operation_timeout_seconds", 900),
            ),
            poll_interval_seconds=float(
                pc.get("private_service_connect_operation_poll_interval_seconds", 2),
            ),
        )

    if pair == ("email", "smtp") or (kind == "email" and not variant):
        from gcp.managed.email_smtp import SMTPRelayConfig

        required = {
            "smtp_host": str(pc.get("smtp_host") or ""),
            "smtp_username_secret_ref": str(pc.get("smtp_username_secret_ref") or ""),
            "smtp_password_secret_ref": str(pc.get("smtp_password_secret_ref") or ""),
            "smtp_default_from_address": str(pc.get("smtp_default_from_address") or ""),
        }
        missing = sorted(key for key, value in required.items() if not value)
        if missing:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: GCP email/smtp requires provider_config."
                + ", provider_config.".join(missing),
            )
        return SMTPRelayConfig(
            host=required["smtp_host"],
            username_secret_ref=required["smtp_username_secret_ref"],
            password_secret_ref=required["smtp_password_secret_ref"],
            default_from_address=required["smtp_default_from_address"],
            port=int(pc.get("smtp_port", 587)),
            tls_mode=str(pc.get("smtp_tls_mode", "starttls")),
            allowed_sender_domains=tuple(
                str(domain) for domain in (pc.get("smtp_allowed_sender_domains") or ()) if str(domain).strip()
            ),
            region=str(pc.get("smtp_region", "")),
        )

    if pair == ("search", "gcp_elastic_cloud"):
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


def _k8s_managed_config_for(
    cluster: TenantCluster,
    *,
    kind: str,
    variant: str,
    provider_config: dict[str, Any],
) -> Any:
    """Build live in-cluster driver configs with the cluster mutator attached."""

    cluster_driver = _driver_for_cluster(cluster)
    pc = provider_config
    pair = (kind, variant)

    if pair == ("postgres", "cnpg"):
        from k8s_native.managed.postgres_cnpg import CNPGConfig

        return CNPGConfig(
            cluster_driver=cluster_driver,
            operator_namespace=str(pc.get("cnpg_operator_namespace", "cnpg-system")),
            storage_class=str(pc.get("cnpg_storage_class", "")),
            backup_object_store_url=str(pc.get("cnpg_backup_url", "")),
        )
    if pair == ("redis", "operator"):
        from k8s_native.managed.redis_operator import RedisOperatorConfig

        return RedisOperatorConfig(
            cluster_driver=cluster_driver,
            storage_class=str(pc.get("redis_storage_class", "")),
            persistent=bool(pc.get("redis_persistent", True)),
        )
    if pair == ("cache", "memcached"):
        from k8s_native.managed.cache_memcached import DEFAULT_IMAGE, MemcachedConfig

        return MemcachedConfig(
            cluster_driver=cluster_driver,
            image=str(pc.get("memcached_image") or DEFAULT_IMAGE),
        )
    if pair == ("mysql", "operator"):
        from k8s_native.managed.mysql_operator import MySQLOperatorConfig

        return MySQLOperatorConfig(
            operator_brand=str(pc.get("mysql_operator_brand", "percona")),
            storage_class=_optional_string(pc.get("mysql_storage_class")),
            namespace=_optional_string(pc.get("mysql_namespace")),
            backup_url=_optional_string(pc.get("mysql_backup_url")),
            cluster_driver=cluster_driver,
        )
    if pair == ("document_db", "mongodb_operator"):
        from k8s_native.managed.mongodb_operator import MongoDBOperatorConfig

        return MongoDBOperatorConfig(
            storage_class=_optional_string(pc.get("mongodb_storage_class")),
            namespace=_optional_string(pc.get("mongodb_namespace")),
            backup_url=_optional_string(pc.get("mongodb_backup_url")),
            cluster_driver=cluster_driver,
        )
    if pair == ("event_stream", "kafka_strimzi"):
        from k8s_native.managed.event_stream_strimzi import StrimziKafkaConfig

        return StrimziKafkaConfig(
            storage_class=_optional_string(pc.get("kafka_storage_class")),
            namespace=_optional_string(pc.get("kafka_namespace")),
            cluster_driver=cluster_driver,
        )
    if pair == ("event_stream", "nats"):
        from k8s_native.managed.event_stream_nats import NATSConfig

        return NATSConfig(
            storage_class=_optional_string(pc.get("nats_storage_class")),
            namespace=_optional_string(pc.get("nats_namespace")),
            enable_jetstream=bool(pc.get("nats_enable_jetstream", True)),
            cluster_driver=cluster_driver,
        )
    if pair == ("queue", "rabbitmq_operator"):
        from k8s_native.managed.queue_rabbitmq import RabbitMQOperatorConfig

        return RabbitMQOperatorConfig(
            storage_class=_optional_string(pc.get("rabbitmq_storage_class")),
            namespace=_optional_string(pc.get("rabbitmq_namespace")),
            cluster_driver=cluster_driver,
        )
    if pair == ("faas", "knative_service"):
        from k8s_native.managed.faas_knative import KnativeServiceConfig

        return KnativeServiceConfig(
            cluster_driver=cluster_driver,
            namespace=_optional_string(pc.get("knative_namespace")),
            allow_public=bool(pc.get("knative_allow_public", False)),
            allow_tagged_images=bool(pc.get("knative_allow_tagged_images", False)),
            allow_unsafe_pod_spec=bool(
                pc.get("knative_allow_unsafe_pod_spec", False),
            ),
            default_port=int(pc.get("knative_default_port", 8080)),
            default_timeout_seconds=int(
                pc.get("knative_default_timeout_seconds", 300),
            ),
            default_container_concurrency=int(
                pc.get("knative_default_container_concurrency", 0),
            ),
            allowed_service_accounts=tuple(
                str(value) for value in pc.get("knative_allowed_service_accounts") or []
            ),
        )
    if pair == ("api_gateway", "gateway_api"):
        from k8s_native.managed.api_gateway import GatewayAPIConfig

        return GatewayAPIConfig(
            cluster_driver=cluster_driver,
            namespace=_optional_string(pc.get("gateway_api_namespace")),
            gateway_class_name=str(pc.get("gateway_api_class_name", "")),
            allow_class_override=bool(pc.get("gateway_api_allow_class_override", False)),
            allow_cross_namespace_routes=bool(
                pc.get("gateway_api_allow_cross_namespace_routes", False),
            ),
            allow_cross_namespace_backends=bool(
                pc.get("gateway_api_allow_cross_namespace_backends", False),
            ),
            allow_cross_namespace_certificates=bool(
                pc.get("gateway_api_allow_cross_namespace_certificates", False),
            ),
            allow_custom_backends=bool(pc.get("gateway_api_allow_custom_backends", False)),
            allow_extension_refs=bool(pc.get("gateway_api_allow_extension_refs", False)),
            allow_experimental_routes=bool(
                pc.get("gateway_api_allow_experimental_routes", False),
            ),
            allow_listener_sets=bool(pc.get("gateway_api_allow_listener_sets", False)),
        )
    if pair == ("event_bus", "knative_eventing"):
        from k8s_native.managed.event_bus_knative import KnativeEventingConfig

        allowed_classes = pc.get(
            "knative_eventing_allowed_broker_classes",
            [
                "MTChannelBasedBroker",
                "ChannelBasedBroker",
                "Kafka",
                "RabbitMQBroker",
            ],
        )
        return KnativeEventingConfig(
            cluster_driver=cluster_driver,
            namespace=_optional_string(pc.get("knative_eventing_namespace")),
            broker_class=str(
                pc.get("knative_eventing_broker_class", "MTChannelBasedBroker"),
            ),
            broker_config=pc.get("knative_eventing_broker_config"),
            allow_class_override=bool(
                pc.get("knative_eventing_allow_class_override", False),
            ),
            allow_config_override=bool(
                pc.get("knative_eventing_allow_config_override", False),
            ),
            allow_external_subscribers=bool(
                pc.get("knative_eventing_allow_external_subscribers", False),
            ),
            allow_cross_namespace_subscribers=bool(
                pc.get(
                    "knative_eventing_allow_cross_namespace_subscribers",
                    False,
                ),
            ),
            allow_alpha_delivery_fields=bool(
                pc.get("knative_eventing_allow_alpha_delivery_fields", False),
            ),
            allowed_broker_classes=tuple(str(value) for value in allowed_classes),
        )
    if pair == ("workflow_engine", "argo_workflows"):
        from k8s_native.managed.workflow_argo import ArgoWorkflowsConfig

        return ArgoWorkflowsConfig(
            cluster_driver=cluster_driver,
            namespace=_optional_string(pc.get("argo_workflows_namespace")),
            watch_all_namespaces=bool(
                pc.get("argo_workflows_watch_all_namespaces", True),
            ),
            managed_namespaces=tuple(str(value) for value in pc.get("argo_workflows_managed_namespaces", [])),
            argo_server_url=str(pc.get("argo_workflows_server_url", "")),
            service_account_name=str(
                pc.get("argo_workflows_service_account_name", "argo-workflow"),
            ),
            allow_service_account_override=bool(
                pc.get("argo_workflows_allow_service_account_override", False),
            ),
            allowed_service_accounts=tuple(
                str(value) for value in pc.get("argo_workflows_allowed_service_accounts", [])
            ),
            allow_workflow_template_refs=bool(
                pc.get("argo_workflows_allow_workflow_template_refs", False),
            ),
            allow_cluster_template_refs=bool(
                pc.get("argo_workflows_allow_cluster_template_refs", False),
            ),
            trusted_workflow_template_uids={
                str(key): str(value)
                for key, value in dict(
                    pc.get("argo_workflows_trusted_template_uids", {}),
                ).items()
            },
            allow_resource_templates=bool(
                pc.get("argo_workflows_allow_resource_templates", False),
            ),
            allow_executor_plugins=bool(
                pc.get("argo_workflows_allow_executor_plugins", False),
            ),
            allow_external_http_templates=bool(
                pc.get("argo_workflows_allow_external_http_templates", False),
            ),
            allow_host_access=bool(
                pc.get("argo_workflows_allow_host_access", False),
            ),
            allow_privileged_pods=bool(
                pc.get("argo_workflows_allow_privileged_pods", False),
            ),
            allow_pod_spec_patch=bool(
                pc.get("argo_workflows_allow_pod_spec_patch", False),
            ),
            allow_tagged_images=bool(
                pc.get("argo_workflows_allow_tagged_images", False),
            ),
            allowed_image_prefixes=tuple(
                str(value) for value in pc.get("argo_workflows_allowed_image_prefixes", [])
            ),
            default_parallelism=int(
                pc.get("argo_workflows_default_parallelism", 10),
            ),
            max_parallelism=int(pc.get("argo_workflows_max_parallelism", 50)),
            default_active_deadline_seconds=int(
                pc.get("argo_workflows_default_active_deadline_seconds", 3600),
            ),
            max_active_deadline_seconds=int(
                pc.get("argo_workflows_max_active_deadline_seconds", 86400),
            ),
            default_ttl_seconds=int(
                pc.get("argo_workflows_default_ttl_seconds", 86400),
            ),
            max_ttl_seconds=int(
                pc.get("argo_workflows_max_ttl_seconds", 604800),
            ),
        )
    if pair == ("workflow_engine", "temporal"):
        from django.conf import settings
        from k8s_native.managed._temporal_isolation import ControlPlaneTemporal
        from k8s_native.managed.workflow_temporal import (
            DEFAULT_POSTGRES_IMAGE,
            DEFAULT_SERVER_VERSION,
            TemporalConfig,
        )

        # The coordinates the driver must never collide with are read from the
        # settings the control plane's own Temporal client uses, not from
        # provider config an operator fills in by hand. A hand-copied address
        # can go stale; this one cannot be wrong without the control plane
        # itself being pointed somewhere else.
        return TemporalConfig(
            cluster_driver=cluster_driver,
            control_plane=ControlPlaneTemporal(
                address=str(settings.TEMPORAL_ADDRESS),
                namespaces=(str(settings.TEMPORAL_NAMESPACE),),
                kubernetes_namespaces=tuple(
                    str(value) for value in pc.get("temporal_control_plane_kubernetes_namespaces", [])
                ),
            ),
            server_version=str(pc.get("temporal_server_version", DEFAULT_SERVER_VERSION)),
            postgres_image=str(pc.get("temporal_postgres_image", DEFAULT_POSTGRES_IMAGE)),
            storage_class=str(pc.get("temporal_storage_class", "")),
        )
    if pair == ("model_endpoint", "vllm"):
        from k8s_native.managed.model_endpoint_vllm import VLLMConfig

        from core.app_deploy import AppDeployError, driver_for_capability

        try:
            secrets_backend = driver_for_capability(cluster, "secrets")
        except AppDeployError as exc:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: cannot resolve the secrets backend required by vLLM: {exc}",
            ) from exc
        try:
            from constance import config as constance_config

            frontend_default = str(getattr(constance_config, "VLLM_FRONTEND_DEFAULT", "rust") or "rust")
        except Exception:  # noqa: BLE001 - constance unavailable (tests, early boot)
            frontend_default = "rust"
        return VLLMConfig(
            cluster_driver=cluster_driver,
            secrets_backend=secrets_backend,
            image=str(pc.get("vllm_image", "")),
            storage_class=str(pc.get("vllm_storage_class", "")),
            credential_path_prefix=str(pc.get("vllm_credential_path_prefix", "managed/vllm")),
            frontend_default=frontend_default,
            cluster_frontend=str(pc.get("vllm_frontend", "")),
            model_defaults=dict(pc.get("vllm_model_defaults") or {}),
            metrics=dict(pc.get("vllm_metrics") or {}),
            agent_test=dict(pc.get("vllm_agent_test") or {}),
            shared_runtimes=dict(pc.get("vllm_shared_runtimes") or {}),
        )
    if pair == ("model_endpoint", "kserve"):
        from k8s_native.managed.model_endpoint_kserve import KServeConfig

        return KServeConfig(
            cluster_driver=cluster_driver,
            namespace=_optional_string(pc.get("kserve_namespace")),
            default_deployment_mode=str(
                pc.get("kserve_default_deployment_mode", "Standard"),
            ),
            allowed_deployment_modes=tuple(
                str(value) for value in pc.get("kserve_allowed_deployment_modes", ["Standard"])
            ),
            service_account_name=str(pc.get("kserve_service_account_name", "kserve-model")),
            allow_service_account_override=bool(
                pc.get("kserve_allow_service_account_override", False),
            ),
            allowed_service_accounts=tuple(
                str(value) for value in pc.get("kserve_allowed_service_accounts", [])
            ),
            allow_service_account_token=bool(
                pc.get("kserve_allow_service_account_token", False),
            ),
            allow_public=bool(pc.get("kserve_allow_public", False)),
            allow_writable_storage=bool(pc.get("kserve_allow_writable_storage", False)),
            allow_custom_containers=bool(pc.get("kserve_allow_custom_containers", False)),
            allow_tagged_images=bool(pc.get("kserve_allow_tagged_images", False)),
            allowed_image_prefixes=tuple(str(value) for value in pc.get("kserve_allowed_image_prefixes", [])),
            allowed_storage_uri_schemes=tuple(
                str(value)
                for value in pc.get(
                    "kserve_allowed_storage_uri_schemes",
                    ["s3", "gs", "hf", "pvc", "oci", "oci+native"],
                )
            ),
            allow_external_storage_urls=bool(
                pc.get("kserve_allow_external_storage_urls", False),
            ),
            allowed_external_storage_hosts=tuple(
                str(value) for value in pc.get("kserve_allowed_external_storage_hosts", [])
            ),
            allow_external_logger_urls=bool(
                pc.get("kserve_allow_external_logger_urls", False),
            ),
            allowed_external_logger_hosts=tuple(
                str(value) for value in pc.get("kserve_allowed_external_logger_hosts", [])
            ),
            allow_privileged_pods=bool(pc.get("kserve_allow_privileged_pods", False)),
            allow_host_access=bool(pc.get("kserve_allow_host_access", False)),
            allow_local_model_cache=bool(pc.get("kserve_allow_local_model_cache", False)),
            allowed_model_formats=tuple(str(value) for value in pc.get("kserve_allowed_model_formats", [])),
            allowed_serving_runtimes=tuple(
                str(value) for value in pc.get("kserve_allowed_serving_runtimes", [])
            ),
            allowed_autoscaler_classes=tuple(
                str(value) for value in pc.get("kserve_allowed_autoscaler_classes", ["hpa", "none"])
            ),
            max_replicas=int(pc.get("kserve_max_replicas", 100)),
        )
    if pair == ("observability", "kube_prometheus_stack"):
        from k8s_native.managed.observability_kube_prometheus import KubePrometheusConfig

        return KubePrometheusConfig(
            cluster_driver=cluster_driver,
            monitoring_namespace=str(pc.get("kube_prometheus_namespace", "astrolift-system")),
            prometheus_service_name=str(
                pc.get(
                    "kube_prometheus_prometheus_service_name",
                    "astrolift-kube-prometheus-prometheus",
                )
            ),
            alertmanager_service_name=str(
                pc.get(
                    "kube_prometheus_alertmanager_service_name",
                    "astrolift-kube-prometheus-alertmanager",
                )
            ),
            grafana_service_name=str(
                pc.get(
                    "kube_prometheus_grafana_service_name",
                    "astrolift-kube-prometheus-stack-grafana",
                )
            ),
            prometheus_url=str(pc.get("kube_prometheus_prometheus_url", "")),
            grafana_url=str(pc.get("kube_prometheus_grafana_url", "")),
            verify_crds=bool(pc.get("kube_prometheus_verify_crds", True)),
            verify_services=bool(pc.get("kube_prometheus_verify_services", True)),
            verify_selection=bool(pc.get("kube_prometheus_verify_selection", True)),
            allow_workload_prometheus_access=bool(
                pc.get("kube_prometheus_allow_workload_prometheus_access", False),
            ),
            allow_cross_namespace=bool(pc.get("kube_prometheus_allow_cross_namespace", False)),
            allowed_target_namespaces=tuple(
                str(value) for value in pc.get("kube_prometheus_allowed_target_namespaces", [])
            ),
            allow_custom_rules=bool(pc.get("kube_prometheus_allow_custom_rules", False)),
            allow_custom_dashboards=bool(
                pc.get("kube_prometheus_allow_custom_dashboards", False),
            ),
            allow_honor_labels=bool(pc.get("kube_prometheus_allow_honor_labels", False)),
            min_scrape_interval_seconds=int(
                pc.get("kube_prometheus_min_scrape_interval_seconds", 15),
            ),
            max_monitors=int(pc.get("kube_prometheus_max_monitors", 20)),
            max_endpoints_per_monitor=int(
                pc.get("kube_prometheus_max_endpoints_per_monitor", 10),
            ),
            max_samples_per_scrape=int(
                pc.get("kube_prometheus_max_samples_per_scrape", 50_000),
            ),
            max_targets_per_monitor=int(
                pc.get("kube_prometheus_max_targets_per_monitor", 100),
            ),
            max_rule_groups=int(pc.get("kube_prometheus_max_rule_groups", 20)),
            max_rules=int(pc.get("kube_prometheus_max_rules", 100)),
            max_dashboards=int(pc.get("kube_prometheus_max_dashboards", 10)),
            max_dashboard_bytes=int(pc.get("kube_prometheus_max_dashboard_bytes", 512_000)),
        )
    if pair == ("object_store", "s3_compatible_existing"):
        from k8s_native.managed.object_store_existing_s3 import ExistingS3Config

        from core.app_deploy import AppDeployError, driver_for_capability

        try:
            secrets_backend = driver_for_capability(cluster, "secrets")
        except AppDeployError as exc:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: cannot resolve the secrets backend required by existing S3 adoption: {exc}",
            ) from exc
        return ExistingS3Config(
            cluster_driver=cluster_driver,
            secrets_backend=secrets_backend,
            namespace=_optional_string(pc.get("s3_existing_namespace")),
            allowed_endpoint_hosts=tuple(
                str(value) for value in pc.get("s3_existing_allowed_endpoint_hosts", [])
            ),
            allowed_credential_path_prefixes=tuple(
                str(value)
                for value in pc.get(
                    "s3_existing_allowed_credential_path_prefixes",
                    ["managed/object_store/{organization}"],
                )
            ),
            allow_insecure_http=bool(pc.get("s3_existing_allow_insecure_http", False)),
            allow_skip_tls_verify=bool(
                pc.get("s3_existing_allow_skip_tls_verify", False),
            ),
            allow_endpoint_paths=bool(pc.get("s3_existing_allow_endpoint_paths", False)),
        )
    if pair == ("object_store", "seaweedfs_operator"):
        from k8s_native.managed.object_store_seaweedfs import SeaweedFSObjectStoreConfig

        from core.app_deploy import AppDeployError, driver_for_capability

        try:
            secrets_backend = driver_for_capability(cluster, "secrets")
        except AppDeployError as exc:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: cannot resolve the secrets backend required by SeaweedFS: {exc}",
            ) from exc
        return SeaweedFSObjectStoreConfig(
            namespace=str(pc.get("seaweed_namespace", "astrolift-storage")),
            seaweed_name=str(pc.get("seaweed_cluster_name", "astrolift-object-store")),
            endpoint=str(pc.get("seaweed_s3_endpoint", "")),
            endpoint_scheme=str(pc.get("seaweed_s3_scheme", "http")),
            endpoint_port=int(pc.get("seaweed_s3_port", 8333)),
            region=str(pc.get("seaweed_s3_region", "us-east-1")),
            credential_path_prefix=str(
                pc.get("seaweed_credential_path_prefix", "managed/object_store"),
            ),
            verify_crds=bool(pc.get("seaweed_verify_crds", True)),
            deletion_timeout_seconds=float(
                pc.get("seaweed_deletion_timeout_seconds", 120),
            ),
            cluster_driver=cluster_driver,
            secrets_backend=secrets_backend,
        )
    if pair == ("mssql", "sqlserver_express"):
        from k8s_native.managed.mssql_express import DEFAULT_IMAGE, SQLServerExpressConfig

        from core.app_deploy import AppDeployError, driver_for_capability

        try:
            secrets_backend = driver_for_capability(cluster, "secrets")
        except AppDeployError as exc:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: cannot resolve the secrets backend required by SQL Server Express: {exc}",
            ) from exc
        return SQLServerExpressConfig(
            cluster_driver=cluster_driver,
            secrets_backend=secrets_backend,
            namespace=_optional_string(pc.get("mssql_namespace")),
            storage_class_name=str(pc.get("mssql_storage_class_name", "")),
            image=str(pc.get("mssql_image", DEFAULT_IMAGE)),
            credential_path_prefix=str(pc.get("mssql_credential_path_prefix", "managed/mssql")),
            allow_custom_images=bool(pc.get("mssql_allow_custom_images", False)),
            allow_load_balancer=bool(pc.get("mssql_allow_load_balancer", False)),
            allow_network_policy_disable=bool(
                pc.get("mssql_allow_network_policy_disable", False),
            ),
            volume_snapshot_class=str(pc.get("mssql_volume_snapshot_class", "")),
            allow_crash_consistent_snapshots=bool(
                pc.get("mssql_allow_crash_consistent_snapshots", False),
            ),
            deletion_timeout_seconds=float(pc.get("mssql_deletion_timeout_seconds", 120)),
        )
    if pair in {
        ("search", "opensearch_operator"),
        ("vector_index", "opensearch_operator_vector"),
    }:
        from k8s_native.managed.opensearch_operator import (
            CURRENT_API_VERSION,
            DEFAULT_BOOTSTRAP_IMAGE,
            DEFAULT_IMAGE,
            DEFAULT_VERSION,
            OpenSearchOperatorConfig,
        )

        from core.app_deploy import AppDeployError, driver_for_capability

        try:
            secrets_backend = driver_for_capability(cluster, "secrets")
        except AppDeployError as exc:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: cannot resolve the secrets backend required by OpenSearch: {exc}",
            ) from exc
        return OpenSearchOperatorConfig(
            cluster_driver=cluster_driver,
            secrets_backend=secrets_backend,
            namespace=_optional_string(pc.get("opensearch_namespace")),
            storage_class_name=str(pc.get("opensearch_storage_class_name", "")),
            api_version=str(pc.get("opensearch_api_version", CURRENT_API_VERSION)),
            operator_namespace=str(pc.get("opensearch_operator_namespace", "opensearch-operator-system")),
            version=str(pc.get("opensearch_version", DEFAULT_VERSION)),
            image=str(pc.get("opensearch_image", DEFAULT_IMAGE)),
            bootstrap_image=str(pc.get("opensearch_bootstrap_image", DEFAULT_BOOTSTRAP_IMAGE)),
            credential_path_prefix=str(pc.get("opensearch_credential_path_prefix", "managed/opensearch")),
            allow_custom_versions=bool(pc.get("opensearch_allow_custom_versions", False)),
            allow_custom_images=bool(pc.get("opensearch_allow_custom_images", False)),
            allow_custom_bootstrap_images=bool(pc.get("opensearch_allow_custom_bootstrap_images", False)),
            allow_custom_plugins=bool(pc.get("opensearch_allow_custom_plugins", False)),
            allow_single_node=bool(pc.get("opensearch_allow_single_node", False)),
            allow_network_policy_disable=bool(pc.get("opensearch_allow_network_policy_disable", False)),
            http_tls_secret_name=str(pc.get("opensearch_http_tls_secret_name", "")),
            http_tls_ca_secret_name=str(pc.get("opensearch_http_tls_ca_secret_name", "")),
            http_tls_admin_secret_name=str(pc.get("opensearch_http_tls_admin_secret_name", "")),
            http_tls_admin_dns=tuple(str(value) for value in pc.get("opensearch_http_tls_admin_dns", [])),
            http_tls_verify=bool(pc.get("opensearch_http_tls_verify", False)),
            deletion_timeout_seconds=float(pc.get("opensearch_deletion_timeout_seconds", 180)),
        )
    if pair in {
        ("filesystem", "nfs_csi"),
        ("filesystem", "nfs_subdir_provisioner"),
    }:
        from k8s_native.managed.filesystem_nfs import NFSConfig

        return NFSConfig(
            storage_class_name=str(
                pc.get("nfs_storage_class_name", pc.get("filesystem_storage_class_name", "")),
            ),
            server_address=str(pc.get("nfs_server_address", "")),
            server_export=str(pc.get("nfs_server_export", "/export")),
            namespace=_optional_string(pc.get("nfs_namespace")),
            cluster_driver=cluster_driver,
        )
    if pair in {
        ("filesystem", "storage_class_pvc"),
        ("filesystem", "rook_cephfs"),
    }:
        from k8s_native.managed.filesystem_pvc import PVCConfig

        if pair[1] == "rook_cephfs":
            default_class = "rook-cephfs"
            default_csi = "rook-ceph.cephfs.csi.ceph.com"
            default_modes = ("ReadWriteMany",)
            prefix = "rook_cephfs"
        else:
            default_class = ""
            default_csi = ""
            default_modes = ("ReadWriteOnce",)
            prefix = "filesystem_pvc"
        raw_modes = pc.get(f"{prefix}_access_modes", default_modes)
        if isinstance(raw_modes, str):
            raw_modes = [raw_modes]
        return PVCConfig(
            storage_class_name=str(pc.get(f"{prefix}_storage_class_name", default_class)),
            cluster_driver=cluster_driver,
            csi_driver=str(pc.get(f"{prefix}_csi_driver", default_csi)),
            default_access_modes=tuple(str(value) for value in raw_modes),
        )
    raise ClusterObservabilityError(
        f"cluster {cluster.slug}: no Kubernetes managed-service config builder for "
        f"kind={kind!r}, variant={variant!r}",
    )


def _azure_managed_config_for(
    cluster: TenantCluster,
    *,
    kind: str,
    variant: str,
    provider_config: dict[str, Any],
    auth_config: dict[str, Any],
    region: str,
) -> Any:
    """Build configs for every executable Azure managed-service driver.

    Azure's provider package has shipped managed-service drivers for several
    releases, but the lifecycle resolver historically rejected every Azure
    request before constructing one.  Keep all install-scoped controls here so
    provisioning, update, status, binding, snapshot, and teardown instantiate
    the same driver configuration.
    """

    pc = provider_config
    ac = auth_config
    subscription_id = str(pc.get("subscription_id") or ac.get("subscription_id") or "")
    resource_group = str(pc.get("resource_group") or ac.get("resource_group") or "")
    location = str(pc.get("location") or region or "eastus")
    if not subscription_id:
        raise ClusterObservabilityError(
            f"cluster {cluster.slug}: Azure managed service {kind!r} requires "
            "provider_config.subscription_id",
        )
    if not resource_group:
        raise ClusterObservabilityError(
            f"cluster {cluster.slug}: Azure managed service {kind!r} requires provider_config.resource_group",
        )

    pair = (kind, variant)
    storage_account = str(pc.get("storage_account") or "")
    servicebus_namespace = str(pc.get("servicebus_namespace") or "")
    vault_url = str(pc.get("vault_url") or pc.get("keyvault_url") or "")

    if pair == ("object_store", "blob"):
        from azure.managed.object_store_blob import BlobStorageConfig

        if not storage_account:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure object_store/blob requires provider_config.storage_account",
            )
        # Both are needed to scope the container's role assignment. The
        # driver used to emit a literal `SUB_ID`/`RG` placeholder because
        # its config had nothing to build a real id from (#1470), so a
        # binding reported ready and its grant failed at assignment.
        if not subscription_id or not resource_group:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure object_store/blob requires "
                "provider_config.subscription_id and provider_config.resource_group "
                "to scope its Storage Blob Data Contributor assignment",
            )
        return BlobStorageConfig(
            storage_account=storage_account,
            subscription_id=subscription_id,
            resource_group=resource_group,
            container_name_prefix=str(pc.get("blob_container_name_prefix", "astrolift")),
            versioning_enabled=bool(pc.get("blob_versioning_enabled", True)),
        )

    if pair == ("object_store", "azure_blob") or (kind == "object_store" and not variant):
        from azure.managed.object_store_blob import AzureBlobConfig

        if not storage_account:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure object_store/azure_blob requires "
                "provider_config.storage_account",
            )
        return AzureBlobConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            storage_account=storage_account,
            container_name_prefix=str(pc.get("blob_container_name_prefix", "astrolift")),
            versioning_enabled=bool(pc.get("blob_versioning_enabled", True)),
        )

    if pair == ("queue", "servicebus"):
        from azure.managed.queue_servicebus import ServiceBusConfig

        if not servicebus_namespace:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure queue/servicebus requires "
                "provider_config.servicebus_namespace",
            )
        return ServiceBusConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            namespace_name=servicebus_namespace,
            queue_name_prefix=str(pc.get("servicebus_queue_name_prefix", "astrolift")),
            max_size_in_megabytes=int(pc.get("servicebus_max_size_in_megabytes", 1024)),
            enable_partitioning=bool(pc.get("servicebus_enable_partitioning", False)),
        )

    servicebus_topic_pairs = {
        ("queue", "azure_servicebus"),
        ("topic", "service_bus_topic"),
    }
    if pair in servicebus_topic_pairs or (kind in {"queue", "topic"} and not variant):
        from azure.managed.queue_servicebus import AzureServiceBusConfig

        if not servicebus_namespace:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure {kind}/{variant or ('service_bus_topic' if kind == 'topic' else 'azure_servicebus')} requires "
                "provider_config.servicebus_namespace",
            )
        return AzureServiceBusConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            namespace_name=servicebus_namespace,
            topic_name_prefix=str(pc.get("servicebus_topic_name_prefix", "astrolift")),
            handle_kind="topic" if kind == "topic" else "queue",
            location=str(pc.get("servicebus_location", location)),
            default_message_ttl=str(pc.get("servicebus_default_message_ttl", "P14D")),
            max_size_in_megabytes=int(pc.get("servicebus_max_size_in_megabytes", 1024)),
            enable_partitioning=bool(pc.get("servicebus_enable_partitioning", False)),
            dead_lettering_on_message_expiration=bool(
                pc.get("servicebus_dead_lettering_on_message_expiration", True),
            ),
            max_delivery_count=int(pc.get("servicebus_max_delivery_count", 10)),
            lock_duration=str(pc.get("servicebus_lock_duration", "PT30S")),
        )

    if pair == ("event_bus", "event_grid") or (kind == "event_bus" and not variant):
        from azure.managed.event_grid import AzureEventGridConfig

        return AzureEventGridConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=location,
            topic_name_prefix=str(pc.get("eventgrid_topic_name_prefix", "astrolift-eg")),
            default_input_schema=str(pc.get("eventgrid_default_input_schema", "CloudEventSchemaV1_0")),
            public_network_access_default=str(
                pc.get("eventgrid_public_network_access_default", "Enabled"),
            ),
            allowed_identity_resource_ids=tuple(
                str(value) for value in pc.get("eventgrid_allowed_identity_resource_ids") or []
            ),
        )

    event_hubs_pairs = {
        ("stream", "event_hubs"),
        ("event_stream", "event_hubs_kafka"),
    }
    if pair in event_hubs_pairs or (kind in {"stream", "event_stream"} and not variant):
        from azure.managed.event_hubs import AzureEventHubsConfig

        resolved_variant = variant or ("event_hubs_kafka" if kind == "event_stream" else "event_hubs")
        return AzureEventHubsConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            variant=resolved_variant,
            location=location,
            namespace_name_prefix=str(pc.get("eventhubs_namespace_name_prefix", "astrolift-eh")),
            event_hub_name_prefix=str(pc.get("eventhubs_event_hub_name_prefix", "astrolift")),
            default_sku=str(pc.get("eventhubs_default_sku", "Standard")),
            default_capacity=int(pc.get("eventhubs_default_capacity", 1)),
            default_consumer_group=str(pc.get("eventhubs_default_consumer_group", "astrolift")),
            public_network_access_default=str(
                pc.get("eventhubs_public_network_access_default", "Enabled"),
            ),
            allowed_identity_resource_ids=tuple(
                str(value) for value in pc.get("eventhubs_allowed_identity_resource_ids") or []
            ),
        )

    if pair == ("filesystem", "azure_files") or (kind == "filesystem" and not variant):
        from azure.managed.filesystem_files import AzureFilesConfig

        return AzureFilesConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=location,
            name_prefix=str(pc.get("files_name_prefix", "astrolift-files")),
            default_storage_gib=int(pc.get("files_default_storage_gib", 32)),
            default_redundancy=str(pc.get("files_default_redundancy", "Local")),
            default_root_squash=str(pc.get("files_default_root_squash", "RootSquash")),
            encryption_in_transit_required_default=bool(
                pc.get("files_encryption_in_transit_required_default", True),
            ),
            allowed_subnet_ids=tuple(str(value) for value in pc.get("files_allowed_subnet_ids", []) or []),
            deletion_protection_default=bool(pc.get("files_deletion_protection_default", True)),
        )

    if pair == ("filesystem", "azure_files_classic"):
        from azure.managed.filesystem_files_classic import AzureFilesClassicConfig

        return AzureFilesClassicConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=location,
            account_name_prefix=str(pc.get("files_classic_account_name_prefix", "astroliftfs")),
            share_name_prefix=str(pc.get("files_classic_share_name_prefix", "astrolift-files")),
            default_protocol=str(pc.get("files_classic_default_protocol", "SMB")),
            default_sku=str(pc.get("files_classic_default_sku", "Standard_LRS")),
            default_quota_gib=int(pc.get("files_classic_default_quota_gib", 100)),
            default_access_tier=str(pc.get("files_classic_default_access_tier", "TransactionOptimized")),
            default_root_squash=str(pc.get("files_default_root_squash", "RootSquash")),
            encryption_in_transit_required_default=bool(
                pc.get("files_encryption_in_transit_required_default", True),
            ),
            allowed_subnet_ids=tuple(str(value) for value in pc.get("files_allowed_subnet_ids", []) or []),
            allow_public_access_default=bool(pc.get("files_classic_allow_public_access_default", False)),
            soft_delete_retention_days=int(pc.get("files_classic_soft_delete_retention_days", 14)),
            deletion_protection_default=bool(pc.get("files_deletion_protection_default", True)),
            keyvault_url=vault_url,
            secret_name_prefix=str(pc.get("files_classic_secret_name_prefix", "astrolift-files")),
        )
    if pair == ("event_bus", "event_grid_namespace"):
        from azure.managed.event_grid_namespace import AzureEventGridNamespaceConfig

        if not vault_url:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure event_bus/event_grid_namespace requires "
                "provider_config.keyvault_url",
            )
        return AzureEventGridNamespaceConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            keyvault_url=vault_url,
            location=location,
            namespace_name_prefix=str(pc.get("eventgrid_namespace_name_prefix", "astrolift-egns")),
            topic_name_prefix=str(pc.get("eventgrid_namespace_topic_name_prefix", "events")),
            secret_name_prefix=str(
                pc.get("eventgrid_namespace_secret_name_prefix", "event-grid-namespace"),
            ),
            default_capacity=int(pc.get("eventgrid_namespace_default_capacity", 1)),
            allowed_identity_resource_ids=tuple(
                str(value) for value in pc.get("eventgrid_namespace_allowed_identity_resource_ids") or []
            ),
        )

    if pair == ("postgres", "azure_pg_flex") or (kind == "postgres" and not variant):
        from azure.managed.postgres_flexible import AzurePostgresConfig

        return AzurePostgresConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=location,
            server_name_prefix=str(pc.get("postgres_server_name_prefix", "astrolift")),
            engine_version=str(pc.get("postgres_engine_version", "16")),
            backup_retention_days=int(pc.get("postgres_backup_retention_days", 7)),
            high_availability_default=str(pc.get("postgres_high_availability_default", "Disabled")),
            deletion_protection_default=bool(pc.get("postgres_deletion_protection_default", True)),
            keyvault_url=vault_url,
            secret_name_prefix=str(pc.get("postgres_secret_name_prefix", "astrolift-pg")),
        )

    if pair == ("mysql", "azure_mysql_flex") or (kind == "mysql" and not variant):
        from azure.managed.mysql_flexible import AzureMySQLConfig

        return AzureMySQLConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=location,
            server_name_prefix=str(pc.get("mysql_server_name_prefix", "astrolift")),
            engine_version=str(pc.get("mysql_engine_version", "8.0.21")),
            backup_retention_days=int(pc.get("mysql_backup_retention_days", 7)),
            high_availability_default=str(pc.get("mysql_high_availability_default", "Disabled")),
            deletion_protection_default=bool(pc.get("mysql_deletion_protection_default", True)),
            keyvault_url=vault_url,
            secret_name_prefix=str(pc.get("mysql_secret_name_prefix", "astrolift-mysql")),
        )

    if pair == ("redis", "azure_cache_redis") or (kind == "redis" and not variant):
        from azure.managed.cache_redis import AzureCacheRedisConfig

        return AzureCacheRedisConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=location,
            cache_name_prefix=str(pc.get("redis_cache_name_prefix", "astrolift")),
            default_sku=str(pc.get("redis_default_sku", "Standard_C1")),
            minimum_tls_version_default=str(pc.get("redis_minimum_tls_version_default", "1.2")),
            enable_non_ssl_port_default=bool(pc.get("redis_enable_non_ssl_port_default", False)),
            keyvault_url=vault_url,
            secret_name_prefix=str(pc.get("redis_secret_name_prefix", "astrolift-redis")),
            backup_container_uri=str(pc.get("redis_backup_container_uri", "")),
            backup_storage_subscription_id=str(
                pc.get("redis_backup_storage_subscription_id", ""),
            ),
        )

    if pair == ("kv_store", "cosmos") or (kind == "kv_store" and not variant):
        from azure.managed.cosmos import AzureCosmosConfig

        return AzureCosmosConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=location,
            account_name_prefix=str(pc.get("cosmos_account_name_prefix", "astrolift")),
            database_name_default=str(pc.get("cosmos_database_name_default", "astrolift")),
            default_api_kind=str(pc.get("cosmos_default_api_kind", "MongoDB")),
            backup_policy_default=str(pc.get("cosmos_backup_policy_default", "Continuous")),
            keyvault_url=vault_url,
            secret_name_prefix=str(pc.get("cosmos_secret_name_prefix", "astrolift-cosmos")),
        )

    cosmos_api_pairs = {
        ("document_db", "cosmos_nosql"),
        ("document_db", "cosmos_mongodb"),
        ("graph_db", "cosmos_gremlin"),
        ("wide_column", "cosmos_cassandra"),
        ("kv_store", "cosmos_table"),
    }
    default_cosmos_api_variants = {
        "document_db": "cosmos_nosql",
        "graph_db": "cosmos_gremlin",
        "wide_column": "cosmos_cassandra",
    }
    if pair in cosmos_api_pairs or (kind in default_cosmos_api_variants and not variant):
        from azure.managed.cosmos_api import AzureCosmosApiConfig

        if not vault_url:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure {kind}/{variant or default_cosmos_api_variants[kind]} "
                "requires provider_config.vault_url",
            )
        resolved_variant = variant or default_cosmos_api_variants[kind]
        return AzureCosmosApiConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            variant=resolved_variant,
            location=location,
            account_name_prefix=str(
                pc.get(
                    "cosmos_api_account_name_prefix",
                    pc.get("cosmos_account_name_prefix", "astrolift-cosmos"),
                ),
            ),
            database_name_default=str(
                pc.get(
                    "cosmos_api_database_name_default",
                    pc.get("cosmos_database_name_default", "astrolift"),
                ),
            ),
            backup_policy_default=str(
                pc.get(
                    "cosmos_api_backup_policy_default",
                    pc.get("cosmos_backup_policy_default", "Continuous"),
                ),
            ),
            continuous_backup_tier_default=str(
                pc.get("cosmos_api_continuous_backup_tier_default", "Continuous30Days"),
            ),
            public_network_access_default=str(
                pc.get("cosmos_api_public_network_access_default", "Enabled"),
            ),
            consistency_level_default=str(
                pc.get("cosmos_api_consistency_level_default", "Session"),
            ),
            keyvault_url=vault_url,
            secret_name_prefix=str(
                pc.get("cosmos_api_secret_name_prefix", "astrolift-cosmos-api"),
            ),
            allowed_identity_resource_ids=tuple(
                str(value) for value in pc.get("cosmos_api_allowed_identity_resource_ids") or []
            ),
        )

    if pair == ("search", "azure_ai_search_fulltext") or (kind == "search" and not variant):
        from azure.managed.search_aisearch import AzureAISearchConfig

        return AzureAISearchConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=location,
            service_name_prefix=str(pc.get("ai_search_service_name_prefix", "astrolift")),
            default_sku=str(pc.get("ai_search_default_sku", "basic")),
            replica_count_default=int(pc.get("ai_search_replica_count_default", 1)),
            partition_count_default=int(pc.get("ai_search_partition_count_default", 1)),
            public_network_access_default=str(
                pc.get("ai_search_public_network_access_default", "enabled"),
            ),
            keyvault_url=vault_url,
            secret_name_prefix=str(pc.get("ai_search_secret_name_prefix", "astrolift-search")),
        )

    if pair == ("vector_index", "azure_ai_search_vector") or (kind == "vector_index" and not variant):
        from azure.managed.vector_search import AzureAISearchVectorConfig

        return AzureAISearchVectorConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=location,
            service_name_prefix=str(pc.get("ai_search_vector_service_name_prefix", "astrolift-vec")),
            default_sku=str(pc.get("ai_search_vector_default_sku", "basic")),
            embedding_dimension_default=int(pc.get("ai_search_embedding_dimension_default", 1536)),
            vector_search_profile_default=str(
                pc.get("ai_search_vector_profile_default", "default-profile"),
            ),
            algorithm_default=str(pc.get("ai_search_vector_algorithm_default", "hnsw")),
            keyvault_url=vault_url,
            secret_name_prefix=str(pc.get("ai_search_vector_secret_name_prefix", "astrolift-aisearch")),
        )

    if pair == ("time_series", "azure_monitor_prometheus") or (kind == "time_series" and not variant):
        from azure.managed.timeseries_monitor import AzureMonitorPrometheusConfig

        return AzureMonitorPrometheusConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=location,
            workspace_name_prefix=str(pc.get("monitor_workspace_name_prefix", "astrolift-tsdb")),
            create_linked_log_analytics_default=bool(
                pc.get("monitor_create_linked_log_analytics_default", True),
            ),
            public_network_access_default=str(pc.get("monitor_public_network_access_default", "Enabled")),
        )

    if pair == ("email", "azure_acs") or (kind == "email" and not variant):
        from azure.managed.email_acs import AzureCommunicationEmailConfig

        communication_resource_id = str(pc.get("acs_communication_resource_id") or "")
        if not communication_resource_id:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure email/azure_acs requires "
                "provider_config.acs_communication_resource_id",
            )
        return AzureCommunicationEmailConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=str(pc.get("acs_email_location", "global")),
            email_service_name=str(pc.get("acs_email_service_name", "astrolift-email")),
            communication_resource_id=communication_resource_id,
            default_domain_management=str(pc.get("acs_email_domain_management", "AzureManaged")),
            keyvault_url=vault_url,
            secret_name_prefix=str(pc.get("acs_email_secret_name_prefix", "astrolift-acs-email")),
            delete_data_default=bool(pc.get("acs_email_delete_data_default", False)),
        )

    if pair == ("model_endpoint", "azure_openai") or (kind == "model_endpoint" and not variant):
        from azure.managed.model_endpoint_aoai import AzureOpenAIConfig

        account_name = str(pc.get("azure_openai_account_name") or pc.get("openai_account_name") or "")
        if not account_name:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure model_endpoint/azure_openai requires "
                "provider_config.azure_openai_account_name",
            )
        return AzureOpenAIConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            account_name=account_name,
            location=location,
            deployment_name_prefix=str(pc.get("azure_openai_deployment_name_prefix", "astrolift")),
            api_version=str(pc.get("azure_openai_api_version", "2024-02-15-preview")),
            keyvault_url=vault_url,
            secret_name_prefix=str(pc.get("azure_openai_secret_name_prefix", "astrolift-aoai")),
        )

    if pair == ("model_endpoint", "azure_foundry"):
        from azure.managed.model_endpoint_foundry import AzureFoundryConfig

        account_name = str(pc.get("azure_foundry_account_name") or "")
        if not account_name:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure model_endpoint/azure_foundry requires "
                "provider_config.azure_foundry_account_name",
            )
        return AzureFoundryConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            account_name=account_name,
            location=location,
            deployment_name_prefix=str(pc.get("azure_openai_deployment_name_prefix", "astrolift")),
            api_version=str(pc.get("azure_foundry_api_version", "2024-05-01-preview")),
            keyvault_url=vault_url,
        )

    if pair == ("faas", "azure_functions") or (kind == "faas" and not variant):
        from azure.managed.faas_functions import AzureFunctionsConfig

        return AzureFunctionsConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=location,
            function_name_prefix=str(pc.get("faas_function_name_prefix", "astrolift")),
            default_plan_resource_id=str(pc.get("faas_default_plan_resource_id", "")),
            default_identity_resource_id=str(
                pc.get("faas_default_identity_resource_id", ""),
            ),
            allowed_plan_resource_ids=tuple(
                str(value) for value in pc.get("faas_allowed_plan_resource_ids", [])
            ),
            allowed_identity_resource_ids=tuple(
                str(value) for value in pc.get("faas_allowed_identity_resource_ids", [])
            ),
            allowed_storage_resource_ids=tuple(
                str(value) for value in pc.get("faas_allowed_storage_resource_ids", [])
            ),
            allowed_registry_resource_ids=tuple(
                str(value) for value in pc.get("faas_allowed_registry_resource_ids", [])
            ),
            allowed_subnet_resource_ids=tuple(
                str(value) for value in pc.get("faas_allowed_subnet_resource_ids", [])
            ),
            allow_public_network=bool(pc.get("faas_allow_public_network", False)),
            deletion_protection_default=bool(
                pc.get("faas_deletion_protection_default", True),
            ),
            storage_blob_endpoint_suffix=str(
                pc.get("faas_storage_blob_endpoint_suffix", "blob.core.windows.net"),
            ),
            registry_login_server_suffix=str(
                pc.get("faas_registry_login_server_suffix", "azurecr.io"),
            ),
            site_api_version=str(pc.get("faas_site_api_version", "2024-11-01")),
            identity_api_version=str(
                pc.get("faas_identity_api_version", "2023-01-31"),
            ),
            storage_api_version=str(pc.get("faas_storage_api_version", "2023-05-01")),
            registry_api_version=str(pc.get("faas_registry_api_version", "2023-07-01")),
            authorization_api_version=str(
                pc.get("faas_authorization_api_version", "2022-04-01"),
            ),
            operation_timeout_seconds=float(
                pc.get("faas_operation_timeout_seconds", 900),
            ),
            poll_interval_seconds=float(pc.get("faas_poll_interval_seconds", 3)),
            max_instances=int(pc.get("faas_max_instances", 100)),
        )

    if pair in {
        ("mssql", "azure_sql_database"),
        ("mssql", "azure_sql_serverless"),
        ("mssql", "azure_sql_hyperscale"),
    } or (kind == "mssql" and not variant):
        from azure.managed.mssql_sql import AzureSQLDatabaseConfig

        selected_variant = variant or "azure_sql_database"
        subnet_id = str(pc.get("mssql_virtual_network_subnet_id") or "")
        if not subnet_id:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure mssql/{selected_variant} requires "
                "provider_config.mssql_virtual_network_subnet_id",
            )
        public_network_access = str(pc.get("mssql_public_network_access_default", "Enabled"))
        if public_network_access != "Enabled":
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure mssql/{selected_variant} uses VNet service-endpoint "
                "selected-network mode; public network Disabled requires a Private Endpoint driver",
            )
        return AzureSQLDatabaseConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            variant=selected_variant,
            location=location,
            server_name_prefix=str(pc.get("mssql_server_name_prefix", "astrolift-sql")),
            database_name_prefix=str(pc.get("mssql_database_name_prefix", "astrolift")),
            administrator_login=str(pc.get("mssql_administrator_login", "astrolift")),
            keyvault_url=vault_url,
            secret_name_prefix=str(pc.get("mssql_secret_name_prefix", "astrolift-mssql")),
            virtual_network_subnet_id=subnet_id,
            virtual_network_rule_name=str(pc.get("mssql_virtual_network_rule_name", "astrolift-aks")),
            ignore_missing_vnet_service_endpoint=bool(
                pc.get("mssql_ignore_missing_vnet_service_endpoint", False),
            ),
            public_network_access_default=public_network_access,
            minimal_tls_version_default=str(pc.get("mssql_minimal_tls_version_default", "1.2")),
            backup_retention_days_default=int(pc.get("mssql_backup_retention_days_default", 7)),
            backup_storage_redundancy_default=str(
                pc.get("mssql_backup_storage_redundancy_default", "Geo"),
            ),
            auto_pause_delay_minutes_default=int(
                pc.get("mssql_serverless_auto_pause_delay_minutes_default", 60),
            ),
            min_capacity_default=float(pc.get("mssql_serverless_min_capacity_default", 0.5)),
        )

    if pair == ("mssql", "azure_sql_managed_instance"):
        from azure.managed.mssql_sql import AzureSQLManagedInstanceConfig

        subnet_id = str(pc.get("mssql_managed_instance_subnet_id") or "")
        if not subnet_id:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure mssql/azure_sql_managed_instance requires "
                "provider_config.mssql_managed_instance_subnet_id",
            )
        return AzureSQLManagedInstanceConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            subnet_id=subnet_id,
            location=location,
            instance_name_prefix=str(pc.get("mssql_managed_instance_name_prefix", "astrolift-mi")),
            database_name_prefix=str(pc.get("mssql_database_name_prefix", "astrolift")),
            administrator_login=str(pc.get("mssql_administrator_login", "astrolift")),
            keyvault_url=vault_url,
            secret_name_prefix=str(
                pc.get("mssql_managed_instance_secret_name_prefix", "astrolift-mssql-mi"),
            ),
            license_type_default=str(
                pc.get("mssql_managed_instance_license_type_default", "LicenseIncluded"),
            ),
            minimal_tls_version_default=str(pc.get("mssql_minimal_tls_version_default", "1.2")),
            public_data_endpoint_enabled_default=bool(
                pc.get("mssql_managed_instance_public_data_endpoint_enabled_default", False),
            ),
            backup_retention_days_default=int(pc.get("mssql_backup_retention_days_default", 7)),
        )

    if pair == ("redis", "azure_managed_redis"):
        from azure.managed.managed_redis import AzureManagedRedisConfig

        return AzureManagedRedisConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=location,
            cluster_name_prefix=str(pc.get("managed_redis_cluster_name_prefix", "astrolift-amr")),
            database_name=str(pc.get("managed_redis_database_name", "default")),
            default_sku=str(pc.get("managed_redis_default_sku", "Balanced_B3")),
            high_availability_default=str(
                pc.get("managed_redis_high_availability_default", "Enabled"),
            ),
            public_network_access_default=str(
                pc.get("managed_redis_public_network_access_default", "Enabled"),
            ),
            clustering_policy_default=str(
                pc.get("managed_redis_clustering_policy_default", "OSSCluster"),
            ),
            eviction_policy_default=str(
                pc.get("managed_redis_eviction_policy_default", "AllKeysLRU"),
            ),
            keyvault_url=vault_url,
            secret_name_prefix=str(pc.get("managed_redis_secret_name_prefix", "astrolift-amr")),
            allowed_identity_resource_ids=tuple(
                str(value) for value in pc.get("managed_redis_allowed_identity_resource_ids") or []
            ),
        )

    if pair == ("api_gateway", "api_management") or (kind == "api_gateway" and not variant):
        from azure.managed.api_management import AzureAPIMConfig

        publisher_email = str(pc.get("apim_publisher_email") or "")
        if not publisher_email:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure api_gateway/api_management requires "
                "provider_config.apim_publisher_email",
            )
        return AzureAPIMConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=location,
            publisher_email=publisher_email,
            publisher_name=str(pc.get("apim_publisher_name", "Astrolift")),
            service_name_prefix=str(pc.get("apim_service_name_prefix", "astrolift")),
            allowed_skus=tuple(
                str(value)
                for value in pc.get(
                    "apim_allowed_skus",
                    ["Developer", "Basic", "Standard", "Premium"],
                )
            ),
            max_capacity=int(pc.get("apim_max_capacity", 4)),
            allowed_policy_kinds=tuple(
                str(value) for value in pc.get("apim_allowed_policy_kinds", ["backend", "cors"])
            ),
            allowed_backend_host_suffixes=tuple(
                str(value)
                for value in pc.get(
                    "apim_allowed_backend_host_suffixes",
                    [".azurecontainerapps.io", ".azurewebsites.net"],
                )
            ),
            allowed_backend_identity_resources=tuple(
                str(value) for value in pc.get("apim_allowed_backend_identity_resources", [])
            ),
            allowed_user_assigned_identity_ids=tuple(
                str(value) for value in pc.get("apim_allowed_user_assigned_identity_ids", [])
            ),
            allowed_subnet_ids=tuple(str(value) for value in pc.get("apim_allowed_subnet_ids", [])),
            allowed_custom_domain_suffixes=tuple(
                str(value) for value in pc.get("apim_allowed_custom_domain_suffixes", [])
            ),
            allowed_key_vault_secret_prefixes=tuple(
                str(value) for value in pc.get("apim_allowed_key_vault_secret_prefixes", [])
            ),
            allow_internal_network=bool(pc.get("apim_allow_internal_network", False)),
            allow_custom_domains=bool(pc.get("apim_allow_custom_domains", False)),
            allow_subscriptions=bool(pc.get("apim_allow_subscriptions", False)),
            allow_child_pruning=bool(pc.get("apim_allow_child_pruning", False)),
            deletion_protection_default=bool(
                pc.get("apim_deletion_protection_default", True),
            ),
            max_apis=int(pc.get("apim_max_apis", 50)),
            max_routes_per_api=int(pc.get("apim_max_routes_per_api", 100)),
            max_backends=int(pc.get("apim_max_backends", 50)),
            max_subscriptions=int(pc.get("apim_max_subscriptions", 25)),
            api_endpoint=str(
                pc.get("apim_api_endpoint", "https://management.azure.com"),
            ),
            request_timeout_seconds=float(pc.get("apim_request_timeout_seconds", 30)),
            operation_timeout_seconds=float(
                pc.get("apim_operation_timeout_seconds", 3600),
            ),
            poll_interval_seconds=float(pc.get("apim_poll_interval_seconds", 5)),
        )

    if pair == ("private_endpoint", "private_link") or (kind == "private_endpoint" and not variant):
        from azure.managed.private_endpoint import AzurePrivateEndpointConfig

        allowed_subnet_ids = tuple(
            str(value) for value in pc.get("private_link_allowed_subnet_ids", []) or []
        )
        allowed_service_prefixes = tuple(
            str(value) for value in pc.get("private_link_allowed_service_id_prefixes", []) or []
        )
        if not allowed_subnet_ids:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure private_endpoint/private_link requires "
                "provider_config.private_link_allowed_subnet_ids",
            )
        if not allowed_service_prefixes:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure private_endpoint/private_link requires "
                "provider_config.private_link_allowed_service_id_prefixes",
            )
        return AzurePrivateEndpointConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=location,
            name_prefix=str(pc.get("private_link_name_prefix", "astrolift-pe")),
            default_subnet_id=str(pc.get("private_link_default_subnet_id") or ""),
            allowed_subnet_ids=allowed_subnet_ids,
            allowed_service_id_prefixes=allowed_service_prefixes,
            allowed_private_dns_zone_id_prefixes=tuple(
                str(value) for value in pc.get("private_link_allowed_private_dns_zone_id_prefixes", []) or []
            ),
            allow_manual_approval=bool(pc.get("private_link_allow_manual_approval", False)),
            max_group_ids=int(pc.get("private_link_max_group_ids", 8)),
            max_private_dns_zones=int(pc.get("private_link_max_private_dns_zones", 8)),
            deletion_protection_default=bool(
                pc.get("private_link_deletion_protection_default", True),
            ),
        )

    if pair == ("encryption_key", "key_vault_key") or (kind == "encryption_key" and not variant):
        from azure.managed.encryption_key_vault import AzureKeyVaultKeyConfig

        if not vault_url:
            raise ClusterObservabilityError(
                f"cluster {cluster.slug}: Azure encryption_key/key_vault_key requires provider_config.vault_url",
            )
        return AzureKeyVaultKeyConfig(
            subscription_id=subscription_id,
            resource_group=resource_group,
            vault_url=vault_url,
            key_name_prefix=str(pc.get("key_vault_key_name_prefix", "astrolift")),
            deletion_protection_default=bool(
                pc.get("key_vault_key_deletion_protection_default", True),
            ),
            purge_on_delete_default=bool(pc.get("key_vault_key_purge_on_delete_default", False)),
            rotation_period_default=str(pc.get("key_vault_key_rotation_period_default", "P90D")),
            api_version=str(pc.get("key_vault_key_api_version", "7.4")),
            request_timeout_seconds=float(pc.get("key_vault_key_request_timeout_seconds", 30)),
        )

    raise ClusterObservabilityError(
        f"cluster {cluster.slug}: no Azure managed-service config builder for "
        f"kind={kind!r}, variant={variant!r}",
    )


def managed_config_for(
    plugin_slug: str,
    cluster: TenantCluster,
    *,
    kind: str,
    variant: str = "",
) -> Any:
    """Build a managed-service driver config, carrying the cluster's identity.

    Thin wrapper over :func:`_managed_config_uncredentialed`, which builds the
    config itself. The credential is stamped on afterwards rather than passed
    into each of the forty-odd constructors, so a newly added kind cannot be
    the one that forgets it.

    The stamping rule itself is :func:`~core.cluster_credentials.stamp_credential`,
    shared with the other two config funnels so they cannot come to disagree
    about which configs get one.
    """
    cfg = _managed_config_uncredentialed(plugin_slug, cluster, kind=kind, variant=variant)
    return stamp_credential(cfg, cluster)


def _managed_config_uncredentialed(
    plugin_slug: str,
    cluster: TenantCluster,
    *,
    kind: str,
    variant: str = "",
) -> Any:
    """Build a managed-service DRIVER config from the cluster's install
    settings (#1002).

    ``plugin_slug`` is the plugin the *driver* came from, which is not always
    the cluster's own: an in-cluster variant booked on an EKS/GKE/AKS cluster
    resolves out of ``k8s_native`` (#1484), and it needs the ``k8s_native``
    branch below. That branch attaches whatever ``ClusterDriver`` the cluster
    actually has, so an in-cluster service on AKS drives AKS. Callers get the
    right slug from ``resolve_managed_driver(...).plugin_slug``; passing
    ``cluster.provider_plugin.slug`` blindly would hand a Memcached driver an
    Azure config.

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
    try:
        assert_credential_supported(cluster, capability=f"managed:{kind}")
    except CREDENTIAL_REFUSALS as exc:
        raise ClusterObservabilityError(str(exc)) from exc

    pc = cluster.provider_config or {}
    ac = cluster.auth_config or {}
    region = str(pc.get("region", ac.get("region", cluster.region or "")))

    # The identity the VPC discovery below must run as. The networking
    # helpers build their own ec2/eks/rds clients, so leaving them ambient
    # would put the subnet group and security group in the control plane's
    # account while the database itself went to the cluster's (#1422).
    _cred = credential_for_cluster(cluster)

    # Every AWS driver builds ARNs by interpolating `account_id`. Prefer the
    # account the cluster was *proved* to be in when it was brought into
    # management over the one the operator declared (#1422) — the declaration
    # is unverified, and an ARN naming the wrong account fails much later
    # with nothing pointing back at the cluster row. Overlaid here rather
    # than at each of the seventeen driver configs that read it, so the two
    # cannot disagree.
    verified_account = str(getattr(cluster, "cloud_account_id", "") or "")
    if verified_account and pc.get("account_id") != verified_account:
        pc = {**pc, "account_id": verified_account}

    if plugin_slug == "gcp":
        return _gcp_managed_config_for(
            cluster,
            kind=kind,
            variant=variant,
            provider_config=pc,
            auth_config=ac,
            region=region,
        )

    if plugin_slug == "k8s_native":
        return _k8s_managed_config_for(
            cluster,
            kind=kind,
            variant=variant,
            provider_config=pc,
        )

    if plugin_slug == "azure":
        return _azure_managed_config_for(
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
            account_id=str(pc.get("account_id", "")),
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
            account_id=str(pc.get("account_id", "")),
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
            from aws.session import aws_client

            vpc_id, discovered_subnets, _ = discover_vpc(
                cluster,
                region=region,
                ec2=aws_client("ec2", region=region, credential=_cred),
                eks=aws_client("eks", region=region, credential=_cred),
            )
            if not subnet_ids:
                subnet_ids = discovered_subnets
        return VpcEndpointConfig(
            region=region,
            account_id=str(pc.get("account_id", "")),
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
            credential=_cred,
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
            credential=_cred,
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
                allowed_option_groups=tuple(
                    str(value) for value in pc.get("mssql_allowed_option_groups") or []
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
            credential=_cred,
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
            credential=_cred,
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
            credential=_cred,
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
            credential=_cred,
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
            credential=_cred,
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
            credential=_cred,
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
            credential=_cred,
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
            credential=_cred,
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
                credential=_cred,
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
            credential=_cred,
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
            credential=_cred,
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
            credential=_cred,
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
                    credential=_cred,
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

        from core.install_restrictions import reason as withheld_reason

        return SESEmailConfig(
            region=region,
            identity_prefix=str(pc.get("ses_identity_prefix", "astrolift")),
            base_domain=str(pc.get("base_domain", "")),
            deletion_protection_default=bool(
                pc.get("deletion_protection_default", True),
            ),
            # The SES driver writes its verification records into Route53
            # itself; on an install that withholds DNS it writes none and
            # says why the identity stays pending (calliope-installer#447).
            dns_withheld_reason=withheld_reason("dns"),
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
    job_name: str = "",
) -> list[Any]:
    """Resolver-facing entry. Returns a list of ``PodInfo`` (from
    the provider SDK). Resolver layer is responsible for catching
    :class:`ClusterObservabilityError` and rendering the empty UI.

    ``task_id`` (#891), when set, discovers an agent task pod by its
    ``astrolift.dev/task-id`` label rather than the ``app_slug`` label.
    ``job_name`` (#1712), when set and ``task_id`` is not, discovers a
    Job's pod by Kubernetes' own ``job-name`` label instead — the only
    selector that can resolve a Job's real (suffixed) pod name from just
    the Job's frozen name."""
    driver = _driver_for_cluster(cluster)
    auth = _auth_for_cluster(cluster)
    return driver.list_pods(
        auth=auth,
        namespace=namespace,
        app_slug=app_slug,
        task_id=task_id,
        job_name=job_name,
    )


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


async def stream_app_logs(
    *,
    cluster: TenantCluster,
    namespace: str,
    pod_name: str,
    container: str | None,
    tail_lines: int = 100,
    follow: bool = True,
    validate=None,
    validate_pod=None,
) -> AsyncIterator[Any]:
    """Open ORM-backed provider setup off the loop, then stream asynchronously."""
    from asgiref.sync import sync_to_async

    def _open() -> AsyncIterator[Any]:
        if validate is not None:
            validate()
        if validate_pod is not None:
            validate_pod(pod_name)
        driver = _driver_for_cluster(cluster)
        return driver.stream_logs(
            auth=_auth_for_cluster(cluster),
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            tail_lines=tail_lines,
            follow=follow,
        )

    inner = await sync_to_async(_open, thread_sensitive=True)()
    try:
        async for line in inner:
            yield line
    finally:
        await inner.aclose()


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
    structured: bool = False,
) -> list[Any]:
    """Operator-facing one-shot log read for an agent-task pod.

    Resolves the task's pod in ``namespace`` and returns up to ``tail``
    of its most recent log message lines (no follow). ``structured=True``
    preserves provider ``PodLogLine`` records, including timestamp and stream;
    the default retains the legacy message-string contract.
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
    2. ``pod_name_hint`` — the dispatcher records the spawned Job's name
       on ``AgentTask.pod_name``, never a real pod name: Kubernetes always
       appends a generated suffix to a Job's pod, so the Job name itself
       can never be read as a pod's logs directly (#1712). Re-queried as a
       ``job_name`` selector instead, which resolves through Kubernetes'
       own ``job-name`` label — the one label the Job controller stamps on
       a pod regardless of anything this platform sets.

    Returns ``[]`` — never raises — when the cluster can't be turned
    into a usable driver, no pod is found, or the stream yields nothing
    yet. The resolver layer surfaces the empty list as "no logs yet"
    rather than a 500.

    Each discovery attempt is bound by ``_TASK_LOG_READ_TIMEOUT_SECONDS``,
    the same ceiling the log stream itself uses -- ``list_namespaced_pod``'s
    own ``timeout_seconds`` covers the read, but not connection setup or a
    hung DNS/TCP handshake, so a slow apiserver could otherwise wedge the
    GraphQL worker on a synchronous call it can't cancel.
    """
    from asgiref.sync import sync_to_async

    tail = max(0, int(tail))
    if tail == 0:
        return []

    def _discover(*, task_id: str = "", job_name: str = "") -> str:
        try:
            pods = list_app_pods(
                cluster=cluster,
                namespace=namespace,
                app_slug=task_guid,
                task_id=task_id,
                job_name=job_name,
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

    async def _discover_bounded(*, task_id: str = "", job_name: str = "") -> str:
        try:
            return await asyncio.wait_for(
                sync_to_async(_discover)(task_id=task_id, job_name=job_name),
                timeout=_TASK_LOG_READ_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "fetch_task_pod_logs: discovery exceeded %ss for task %s (task_id=%r job_name=%r)",
                _TASK_LOG_READ_TIMEOUT_SECONDS,
                task_guid,
                task_id,
                job_name,
            )
            return ""

    pod_name = await _discover_bounded(task_id=task_guid)
    hint = (pod_name_hint or "").strip()
    if not pod_name and hint:
        pod_name = await _discover_bounded(job_name=hint)
    if not pod_name:
        return []

    return await fetch_pod_log_tail(
        cluster=cluster,
        namespace=namespace,
        pod_name=pod_name,
        tail=tail,
        structured=structured,
    )


async def fetch_pod_log_tail(
    *,
    cluster: TenantCluster,
    namespace: str,
    pod_name: str,
    tail: int = 200,
    structured: bool = False,
) -> list[Any]:
    """One-shot read of the last ``tail`` message lines from ``pod_name``.

    The read half of :func:`fetch_task_pod_logs`, split out because
    callers that already know their pod (the agent-box reaper, which is
    holding the pod it just found dead) should not have to re-run task
    discovery to reach it.

    Never raises: every failure — unusable driver, refused stream,
    mid-stream error, a driver that will not terminate — degrades to
    the lines collected so far. A log read is diagnostics, and
    diagnostics failing must not become the caller's problem.
    """
    tail = max(0, int(tail))
    if tail == 0:
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
        logger.exception("fetch_pod_log_tail: failed to open log stream for pod %s", pod_name)
        return []

    lines: list[Any] = []

    async def _collect() -> None:
        async for line in inner:
            message = getattr(line, "message", "")
            lines.append(line if structured else message if isinstance(message, str) else str(message))
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
            "fetch_pod_log_tail: read exceeded %ss for pod %s; returning partial tail",
            _TASK_LOG_READ_TIMEOUT_SECONDS,
            pod_name,
        )
    except Exception:
        # A mid-stream failure still returns whatever we collected —
        # partial logs beat a hard error on an operator-facing read.
        logger.exception("fetch_pod_log_tail: stream raised for pod %s", pod_name)
    finally:
        # Explicit aclose so the kubelet socket releases even when we
        # break out early after hitting the tail cap — async for does
        # not aclose its iterator on a break.
        try:
            await inner.aclose()
        except Exception:
            logger.exception("fetch_pod_log_tail: aclose failed for pod %s", pod_name)

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
    validate=None,
    validate_pod=None,
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
    from asgiref.sync import sync_to_async

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
                validate=validate,
                validate_pod=validate_pod,
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
        except Exception as exc:
            if validate is not None:
                from graphql import GraphQLError

                await queue.put(
                    exc
                    if isinstance(exc, GraphQLError)
                    else ClusterObservabilityError("Preview log backend unavailable")
                )
                return
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
        if validate is not None:
            validate()
        try:
            pods = list_app_pods(
                cluster=cluster,
                namespace=namespace,
                app_slug=app_slug,
            )
        except Exception:
            if validate is not None:
                raise ClusterObservabilityError("Preview log backend unavailable") from None
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
            try:
                discovered = await sync_to_async(_discover_pods, thread_sensitive=True)()
            except Exception as exc:
                if validate is None:
                    raise
                await queue.put(exc)
                return
            new_pods = [p for p in discovered if p not in subscribed]
            for pod_name in new_pods:
                subscribed.add(pod_name)
                children[pod_name] = asyncio.create_task(_pump_one(pod_name))

    initial_pods = await sync_to_async(_discover_pods, thread_sensitive=True)()
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
            if validate is not None and isinstance(item, Exception):
                raise item
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
