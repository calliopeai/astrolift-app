from __future__ import annotations

from types import SimpleNamespace

import pytest
from k8s_native.plugin import PLUGIN

from astrolift_drivers.registry import PluginManifest, plugins
from core.cluster_observability import ClusterObservabilityError, managed_config_for


@pytest.fixture(autouse=True)
def _k8s_registry(monkeypatch):
    drivers = dict(PLUGIN.drivers)
    drivers.update(
        {
            f"managed:{kind}:{variant}": driver
            for (kind, variant), driver in PLUGIN.managed_service_drivers.items()
        }
    )
    monkeypatch.setattr(
        plugins,
        "_plugins",
        {
            "k8s_native": PluginManifest(
                plugin_id="k8s_native",
                display_name=PLUGIN.display_name,
                version="test",
                drivers=drivers,
            )
        },
    )


def _cluster(**provider_overrides):
    provider_config = {
        "cnpg_storage_class": "database-rwo",
        "redis_storage_class": "cache-rwo",
        "mysql_storage_class": "database-rwo",
        "mongodb_storage_class": "database-rwo",
        "kafka_storage_class": "stream-rwo",
        "nats_storage_class": "stream-rwo",
        "rabbitmq_storage_class": "queue-rwo",
        "knative_namespace": "functions-system",
        "gateway_api_namespace": "gateway-system",
        "gateway_api_class_name": "envoy-gateway",
        "knative_eventing_namespace": "eventing-system",
        "argo_workflows_namespace": "workflows-system",
        "kserve_namespace": "models-system",
        "seaweed_namespace": "storage-system",
        "seaweed_cluster_name": "shared-store",
        "seaweed_s3_endpoint": "https://objects.example.test",
        "seaweed_s3_region": "local-1",
        "s3_existing_allowed_endpoint_hosts": ["objects.partner.test"],
        "s3_existing_allowed_credential_path_prefixes": ["org/object-store"],
        "mssql_namespace": "database-system",
        "mssql_storage_class_name": "database-rwo",
        "mssql_credential_path_prefix": "managed/sqlserver",
        "opensearch_namespace": "search-system",
        "opensearch_storage_class_name": "search-rwo",
        "opensearch_credential_path_prefix": "managed/search",
        "nfs_storage_class_name": "nfs-rwx",
        "filesystem_pvc_storage_class_name": "standard-rwo",
        "rook_cephfs_storage_class_name": "rook-shared",
        "rook_cephfs_csi_driver": "rook-ceph.cephfs.csi.ceph.com",
    }
    provider_config.update(provider_overrides)
    return SimpleNamespace(
        slug="on-prem-prod",
        region="",
        provider_config=provider_config,
        auth_config={},
        provider_plugin=SimpleNamespace(slug="k8s_native"),
    )


def test_every_registered_k8s_managed_driver_has_live_config(monkeypatch) -> None:
    cluster_driver = object()
    secrets_backend = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability", lambda _cluster, _capability: secrets_backend
    )

    for kind, variant in PLUGIN.managed_service_drivers:
        config = managed_config_for("k8s_native", _cluster(), kind=kind, variant=variant)
        assert config.cluster_driver is cluster_driver, (kind, variant)
        if (kind, variant) in {
            ("object_store", "seaweedfs_operator"),
            ("object_store", "s3_compatible_existing"),
            ("mssql", "sqlserver_express"),
            ("search", "opensearch_operator"),
            ("vector_index", "opensearch_operator_vector"),
        }:
            assert config.secrets_backend is secrets_backend


def test_dynamic_filesystem_config_preserves_operator_defaults(monkeypatch) -> None:
    cluster_driver = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)

    generic = managed_config_for(
        "k8s_native",
        _cluster(filesystem_pvc_access_modes=["ReadWriteOncePod"]),
        kind="filesystem",
        variant="storage_class_pvc",
    )
    rook = managed_config_for(
        "k8s_native",
        _cluster(rook_cephfs_access_modes=["ReadWriteMany"]),
        kind="filesystem",
        variant="rook_cephfs",
    )

    assert generic.storage_class_name == "standard-rwo"
    assert generic.default_access_modes == ("ReadWriteOncePod",)
    assert generic.csi_driver == ""
    assert rook.storage_class_name == "rook-shared"
    assert rook.default_access_modes == ("ReadWriteMany",)
    assert rook.csi_driver == "rook-ceph.cephfs.csi.ceph.com"


def test_knative_config_preserves_install_security_policy(monkeypatch) -> None:
    cluster_driver = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)

    config = managed_config_for(
        "k8s_native",
        _cluster(
            knative_allow_public=True,
            knative_allow_tagged_images=True,
            knative_allow_unsafe_pod_spec=True,
            knative_default_port=9090,
            knative_default_timeout_seconds=600,
            knative_default_container_concurrency=50,
        ),
        kind="faas",
        variant="knative_service",
    )

    assert config.cluster_driver is cluster_driver
    assert config.namespace == "functions-system"
    assert config.allow_public is True
    assert config.allow_tagged_images is True
    assert config.allow_unsafe_pod_spec is True
    assert config.default_port == 9090
    assert config.default_timeout_seconds == 600
    assert config.default_container_concurrency == 50


def test_gateway_api_config_preserves_install_security_policy(monkeypatch) -> None:
    cluster_driver = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)

    config = managed_config_for(
        "k8s_native",
        _cluster(
            gateway_api_allow_class_override=True,
            gateway_api_allow_cross_namespace_routes=True,
            gateway_api_allow_cross_namespace_backends=True,
            gateway_api_allow_cross_namespace_certificates=True,
            gateway_api_allow_custom_backends=True,
            gateway_api_allow_extension_refs=True,
            gateway_api_allow_experimental_routes=True,
            gateway_api_allow_listener_sets=True,
        ),
        kind="api_gateway",
        variant="gateway_api",
    )

    assert config.cluster_driver is cluster_driver
    assert config.namespace == "gateway-system"
    assert config.gateway_class_name == "envoy-gateway"
    assert config.allow_class_override is True
    assert config.allow_cross_namespace_routes is True
    assert config.allow_cross_namespace_backends is True
    assert config.allow_cross_namespace_certificates is True
    assert config.allow_custom_backends is True
    assert config.allow_extension_refs is True
    assert config.allow_experimental_routes is True
    assert config.allow_listener_sets is True


def test_knative_eventing_config_preserves_install_security_policy(monkeypatch) -> None:
    cluster_driver = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)

    config = managed_config_for(
        "k8s_native",
        _cluster(
            knative_eventing_broker_class="Kafka",
            knative_eventing_broker_config={
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "name": "kafka-broker-config",
                "namespace": "knative-eventing",
            },
            knative_eventing_allow_class_override=True,
            knative_eventing_allow_config_override=True,
            knative_eventing_allow_external_subscribers=True,
            knative_eventing_allow_cross_namespace_subscribers=True,
            knative_eventing_allow_alpha_delivery_fields=True,
            knative_eventing_allowed_broker_classes=["Kafka"],
        ),
        kind="event_bus",
        variant="knative_eventing",
    )

    assert config.cluster_driver is cluster_driver
    assert config.namespace == "eventing-system"
    assert config.broker_class == "Kafka"
    assert config.broker_config == {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "name": "kafka-broker-config",
        "namespace": "knative-eventing",
    }
    assert config.allow_class_override is True
    assert config.allow_config_override is True
    assert config.allow_external_subscribers is True
    assert config.allow_cross_namespace_subscribers is True
    assert config.allow_alpha_delivery_fields is True
    assert config.allowed_broker_classes == ("Kafka",)


def test_argo_workflows_config_preserves_install_security_policy(monkeypatch) -> None:
    cluster_driver = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)

    config = managed_config_for(
        "k8s_native",
        _cluster(
            argo_workflows_server_url="https://argo.example.test",
            argo_workflows_watch_all_namespaces=False,
            argo_workflows_managed_namespaces=["workflows-system"],
            argo_workflows_service_account_name="workflow-runner",
            argo_workflows_allow_service_account_override=True,
            argo_workflows_allowed_service_accounts=["workflow-runner", "gpu-runner"],
            argo_workflows_allow_workflow_template_refs=True,
            argo_workflows_allow_cluster_template_refs=True,
            argo_workflows_trusted_template_uids={"cluster/shared": "uid-1"},
            argo_workflows_allow_resource_templates=True,
            argo_workflows_allow_executor_plugins=True,
            argo_workflows_allow_external_http_templates=True,
            argo_workflows_allow_host_access=True,
            argo_workflows_allow_privileged_pods=True,
            argo_workflows_allow_pod_spec_patch=True,
            argo_workflows_allow_tagged_images=True,
            argo_workflows_allowed_image_prefixes=["registry.example.test/"],
            argo_workflows_default_parallelism=5,
            argo_workflows_max_parallelism=25,
            argo_workflows_default_active_deadline_seconds=900,
            argo_workflows_max_active_deadline_seconds=7200,
            argo_workflows_default_ttl_seconds=3600,
            argo_workflows_max_ttl_seconds=86400,
        ),
        kind="workflow_engine",
        variant="argo_workflows",
    )

    assert config.cluster_driver is cluster_driver
    assert config.namespace == "workflows-system"
    assert config.argo_server_url == "https://argo.example.test"
    assert config.watch_all_namespaces is False
    assert config.managed_namespaces == ("workflows-system",)
    assert config.service_account_name == "workflow-runner"
    assert config.allow_service_account_override is True
    assert config.allowed_service_accounts == ("workflow-runner", "gpu-runner")
    assert config.allow_workflow_template_refs is True
    assert config.allow_cluster_template_refs is True
    assert config.trusted_workflow_template_uids == {"cluster/shared": "uid-1"}
    assert config.allow_resource_templates is True
    assert config.allow_executor_plugins is True
    assert config.allow_external_http_templates is True
    assert config.allow_host_access is True
    assert config.allow_privileged_pods is True
    assert config.allow_pod_spec_patch is True
    assert config.allow_tagged_images is True
    assert config.allowed_image_prefixes == ("registry.example.test/",)
    assert config.default_parallelism == 5
    assert config.max_parallelism == 25
    assert config.default_active_deadline_seconds == 900
    assert config.max_active_deadline_seconds == 7200
    assert config.default_ttl_seconds == 3600
    assert config.max_ttl_seconds == 86400


def test_kserve_config_preserves_install_security_policy(monkeypatch) -> None:
    cluster_driver = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)

    config = managed_config_for(
        "k8s_native",
        _cluster(
            kserve_default_deployment_mode="Knative",
            kserve_allowed_deployment_modes=["Standard", "Knative"],
            kserve_service_account_name="model-runner",
            kserve_allow_service_account_override=True,
            kserve_allowed_service_accounts=["model-runner", "gpu-runner"],
            kserve_allow_service_account_token=True,
            kserve_allow_public=True,
            kserve_allow_writable_storage=True,
            kserve_allow_custom_containers=True,
            kserve_allow_tagged_images=True,
            kserve_allowed_image_prefixes=["registry.example.test/models"],
            kserve_allowed_storage_uri_schemes=["s3", "https"],
            kserve_allow_external_storage_urls=True,
            kserve_allowed_external_storage_hosts=["models.example.test"],
            kserve_allow_external_logger_urls=True,
            kserve_allowed_external_logger_hosts=["logs.example.test"],
            kserve_allow_privileged_pods=True,
            kserve_allow_host_access=True,
            kserve_allow_local_model_cache=True,
            kserve_allowed_model_formats=["sklearn"],
            kserve_allowed_serving_runtimes=["sklearn-runtime"],
            kserve_allowed_autoscaler_classes=["hpa", "keda"],
            kserve_max_replicas=12,
        ),
        kind="model_endpoint",
        variant="kserve",
    )

    assert config.cluster_driver is cluster_driver
    assert config.namespace == "models-system"
    assert config.default_deployment_mode == "Knative"
    assert config.allowed_deployment_modes == ("Standard", "Knative")
    assert config.service_account_name == "model-runner"
    assert config.allow_service_account_override is True
    assert config.allowed_service_accounts == ("model-runner", "gpu-runner")
    assert config.allow_service_account_token is True
    assert config.allow_public is True
    assert config.allow_writable_storage is True
    assert config.allow_custom_containers is True
    assert config.allow_tagged_images is True
    assert config.allowed_image_prefixes == ("registry.example.test/models",)
    assert config.allowed_storage_uri_schemes == ("s3", "https")
    assert config.allow_external_storage_urls is True
    assert config.allowed_external_storage_hosts == ("models.example.test",)
    assert config.allow_external_logger_urls is True
    assert config.allowed_external_logger_hosts == ("logs.example.test",)
    assert config.allow_privileged_pods is True
    assert config.allow_host_access is True
    assert config.allow_local_model_cache is True
    assert config.allowed_model_formats == ("sklearn",)
    assert config.allowed_serving_runtimes == ("sklearn-runtime",)
    assert config.allowed_autoscaler_classes == ("hpa", "keda")
    assert config.max_replicas == 12


def test_unknown_k8s_managed_pair_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: object())

    with pytest.raises(ClusterObservabilityError, match="no Kubernetes managed-service config builder"):
        managed_config_for("k8s_native", _cluster(), kind="filesystem", variant="imaginary")


def test_k8s_operator_defaults_are_exposed_in_provider_schema() -> None:
    properties = PLUGIN.config_schema["properties"]
    expected = {
        "cnpg_operator_namespace",
        "cnpg_storage_class",
        "cnpg_backup_url",
        "redis_storage_class",
        "redis_persistent",
        "mysql_operator_brand",
        "mysql_storage_class",
        "mysql_namespace",
        "mysql_backup_url",
        "mongodb_storage_class",
        "mongodb_namespace",
        "mongodb_backup_url",
        "kafka_storage_class",
        "kafka_namespace",
        "nats_storage_class",
        "nats_namespace",
        "nats_enable_jetstream",
        "rabbitmq_storage_class",
        "rabbitmq_namespace",
        "knative_namespace",
        "knative_allow_public",
        "knative_allow_tagged_images",
        "knative_allow_unsafe_pod_spec",
        "knative_default_port",
        "knative_default_timeout_seconds",
        "knative_default_container_concurrency",
        "gateway_api_namespace",
        "gateway_api_class_name",
        "gateway_api_allow_class_override",
        "gateway_api_allow_cross_namespace_routes",
        "gateway_api_allow_cross_namespace_backends",
        "gateway_api_allow_cross_namespace_certificates",
        "gateway_api_allow_custom_backends",
        "gateway_api_allow_extension_refs",
        "gateway_api_allow_experimental_routes",
        "gateway_api_allow_listener_sets",
        "knative_eventing_namespace",
        "knative_eventing_broker_class",
        "knative_eventing_broker_config",
        "knative_eventing_allow_class_override",
        "knative_eventing_allow_config_override",
        "knative_eventing_allow_external_subscribers",
        "knative_eventing_allow_cross_namespace_subscribers",
        "knative_eventing_allow_alpha_delivery_fields",
        "knative_eventing_allowed_broker_classes",
        "argo_workflows_namespace",
        "argo_workflows_watch_all_namespaces",
        "argo_workflows_managed_namespaces",
        "argo_workflows_server_url",
        "argo_workflows_service_account_name",
        "argo_workflows_allow_service_account_override",
        "argo_workflows_allowed_service_accounts",
        "argo_workflows_allow_workflow_template_refs",
        "argo_workflows_allow_cluster_template_refs",
        "argo_workflows_allow_resource_templates",
        "argo_workflows_allow_executor_plugins",
        "argo_workflows_allow_external_http_templates",
        "argo_workflows_allow_host_access",
        "argo_workflows_allow_privileged_pods",
        "argo_workflows_allow_pod_spec_patch",
        "argo_workflows_allow_tagged_images",
        "argo_workflows_allowed_image_prefixes",
        "argo_workflows_default_parallelism",
        "argo_workflows_max_parallelism",
        "argo_workflows_default_active_deadline_seconds",
        "argo_workflows_max_active_deadline_seconds",
        "argo_workflows_default_ttl_seconds",
        "argo_workflows_max_ttl_seconds",
        "kserve_namespace",
        "kserve_default_deployment_mode",
        "kserve_allowed_deployment_modes",
        "kserve_service_account_name",
        "kserve_allow_service_account_override",
        "kserve_allowed_service_accounts",
        "kserve_allow_service_account_token",
        "kserve_allow_public",
        "kserve_allow_writable_storage",
        "kserve_allow_custom_containers",
        "kserve_allow_tagged_images",
        "kserve_allowed_image_prefixes",
        "kserve_allowed_storage_uri_schemes",
        "kserve_allow_external_storage_urls",
        "kserve_allowed_external_storage_hosts",
        "kserve_allow_external_logger_urls",
        "kserve_allowed_external_logger_hosts",
        "kserve_allow_privileged_pods",
        "kserve_allow_host_access",
        "kserve_allow_local_model_cache",
        "kserve_allowed_model_formats",
        "kserve_allowed_serving_runtimes",
        "kserve_allowed_autoscaler_classes",
        "kserve_max_replicas",
        "s3_existing_namespace",
        "s3_existing_allowed_endpoint_hosts",
        "s3_existing_allowed_credential_path_prefixes",
        "s3_existing_allow_insecure_http",
        "s3_existing_allow_skip_tls_verify",
        "s3_existing_allow_endpoint_paths",
        "seaweed_namespace",
        "seaweed_cluster_name",
        "seaweed_s3_endpoint",
        "seaweed_s3_scheme",
        "seaweed_s3_port",
        "seaweed_s3_region",
        "seaweed_credential_path_prefix",
        "seaweed_verify_crds",
        "seaweed_deletion_timeout_seconds",
        "mssql_namespace",
        "mssql_storage_class_name",
        "mssql_image",
        "mssql_credential_path_prefix",
        "mssql_allow_custom_images",
        "mssql_allow_load_balancer",
        "mssql_allow_network_policy_disable",
        "mssql_volume_snapshot_class",
        "mssql_allow_crash_consistent_snapshots",
        "mssql_deletion_timeout_seconds",
        "opensearch_namespace",
        "opensearch_storage_class_name",
        "opensearch_api_version",
        "opensearch_operator_namespace",
        "opensearch_version",
        "opensearch_image",
        "opensearch_bootstrap_image",
        "opensearch_credential_path_prefix",
        "opensearch_allow_custom_versions",
        "opensearch_allow_custom_images",
        "opensearch_allow_custom_bootstrap_images",
        "opensearch_allow_custom_plugins",
        "opensearch_allow_single_node",
        "opensearch_allow_network_policy_disable",
        "opensearch_http_tls_secret_name",
        "opensearch_http_tls_ca_secret_name",
        "opensearch_http_tls_admin_secret_name",
        "opensearch_http_tls_admin_dns",
        "opensearch_http_tls_verify",
        "opensearch_deletion_timeout_seconds",
        "nfs_storage_class_name",
        "nfs_server_address",
        "nfs_server_export",
        "nfs_namespace",
        "filesystem_pvc_storage_class_name",
        "filesystem_pvc_csi_driver",
        "filesystem_pvc_access_modes",
        "rook_cephfs_storage_class_name",
        "rook_cephfs_csi_driver",
        "rook_cephfs_access_modes",
    }

    assert expected <= set(properties)


def test_seaweedfs_config_preserves_shared_cluster_and_secret_backend(monkeypatch) -> None:
    cluster_driver = object()
    secrets_backend = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability", lambda _cluster, _capability: secrets_backend
    )

    config = managed_config_for(
        "k8s_native",
        _cluster(),
        kind="object_store",
        variant="seaweedfs_operator",
    )

    assert config.namespace == "storage-system"
    assert config.seaweed_name == "shared-store"
    assert config.endpoint == "https://objects.example.test"
    assert config.region == "local-1"
    assert config.cluster_driver is cluster_driver
    assert config.secrets_backend is secrets_backend


def test_existing_s3_config_preserves_adoption_policy_and_secret_backend(monkeypatch) -> None:
    cluster_driver = object()
    secrets_backend = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability", lambda _cluster, _capability: secrets_backend
    )

    config = managed_config_for(
        "k8s_native",
        _cluster(
            s3_existing_namespace="external-resources",
            s3_existing_allow_insecure_http=True,
            s3_existing_allow_skip_tls_verify=True,
            s3_existing_allow_endpoint_paths=True,
        ),
        kind="object_store",
        variant="s3_compatible_existing",
    )

    assert config.namespace == "external-resources"
    assert config.allowed_endpoint_hosts == ("objects.partner.test",)
    assert config.allowed_credential_path_prefixes == ("org/object-store",)
    assert config.allow_insecure_http is True
    assert config.allow_skip_tls_verify is True
    assert config.allow_endpoint_paths is True
    assert config.cluster_driver is cluster_driver
    assert config.secrets_backend is secrets_backend


def test_mssql_config_preserves_install_policy_and_secret_backend(monkeypatch) -> None:
    cluster_driver = object()
    secrets_backend = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability", lambda _cluster, _capability: secrets_backend
    )

    config = managed_config_for(
        "k8s_native",
        _cluster(
            mssql_allow_load_balancer=True,
            mssql_allow_network_policy_disable=True,
            mssql_volume_snapshot_class="database-snapshots",
        ),
        kind="mssql",
        variant="sqlserver_express",
    )

    assert config.namespace == "database-system"
    assert config.storage_class_name == "database-rwo"
    assert config.credential_path_prefix == "managed/sqlserver"
    assert config.allow_load_balancer is True
    assert config.allow_network_policy_disable is True
    assert config.volume_snapshot_class == "database-snapshots"
    assert config.cluster_driver is cluster_driver
    assert config.secrets_backend is secrets_backend


@pytest.mark.parametrize(
    ("kind", "variant"),
    [
        ("search", "opensearch_operator"),
        ("vector_index", "opensearch_operator_vector"),
    ],
)
def test_opensearch_config_preserves_install_policy_and_secret_backend(
    monkeypatch,
    kind,
    variant,
) -> None:
    cluster_driver = object()
    secrets_backend = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability", lambda _cluster, _capability: secrets_backend
    )

    config = managed_config_for(
        "k8s_native",
        _cluster(
            opensearch_allow_single_node=True,
            opensearch_allow_custom_plugins=True,
            opensearch_operator_namespace="search-operator",
        ),
        kind=kind,
        variant=variant,
    )

    assert config.namespace == "search-system"
    assert config.storage_class_name == "search-rwo"
    assert config.credential_path_prefix == "managed/search"
    assert config.operator_namespace == "search-operator"
    assert config.allow_single_node is True
    assert config.allow_custom_plugins is True
    assert config.cluster_driver is cluster_driver
    assert config.secrets_backend is secrets_backend


def test_dynamic_filesystems_are_executable_preview_catalog_entries() -> None:
    from astrolift_services.managed_service_catalog import list_catalog

    rows = {row.variant: row for row in list_catalog("k8s_native") if row.kind == "filesystem"}

    for variant in ("storage_class_pvc", "rook_cephfs"):
        row = rows[variant]
        assert row.available is True
        assert row.status == "preview"
        assert row.config_schema["required"] == ["storage_class_name"]
        assert "FILESYSTEM_TLS" not in row.binding_envs


def test_k8s_object_store_catalog_distinguishes_executable_and_planned_variants() -> None:
    from astrolift_services.managed_service_catalog import list_catalog

    rows = {row.variant: row for row in list_catalog("k8s_native") if row.kind == "object_store"}

    assert rows["seaweedfs_operator"].available is True
    assert rows["seaweedfs_operator"].status == "preview"
    assert rows["seaweedfs_operator"].is_default_for_kind is True
    assert rows["s3_compatible_existing"].available is True
    assert rows["s3_compatible_existing"].status == "preview"
    assert rows["s3_compatible_existing"].is_default_for_kind is False
    assert rows["minio_operator"].available is False
    assert rows["minio_operator"].status == "deprecated"
    assert "retired" in rows["minio_operator"].unavailable_reason
    assert rows["minio_aistor_operator"].available is False


def test_sqlserver_express_is_an_executable_preview_catalog_entry() -> None:
    from astrolift_services.managed_service_catalog import list_catalog

    rows = {row.variant: row for row in list_catalog("k8s_native") if row.kind == "mssql"}

    row = rows["sqlserver_express"]
    assert row.available is True
    assert row.status == "preview"
    assert set(row.binding_envs) == {
        "MSSQL_HOST",
        "MSSQL_PORT",
        "MSSQL_DB",
        "MSSQL_USER",
        "MSSQL_PASSWORD",
        "MSSQL_ENCRYPT",
        "MSSQL_TRUST_SERVER_CERTIFICATE",
        "DATABASE_URL",
    }


def test_opensearch_variants_are_executable_preview_catalog_entries() -> None:
    from astrolift_services.managed_service_catalog import list_catalog

    rows = {
        (row.kind, row.variant): row
        for row in list_catalog("k8s_native")
        if row.kind in {"search", "vector_index"}
    }

    search = rows[("search", "opensearch_operator")]
    vector = rows[("vector_index", "opensearch_operator_vector")]
    assert search.available is True
    assert search.status == "preview"
    assert "SEARCH_TLS_VERIFY" in search.binding_envs
    assert vector.available is True
    assert vector.status == "preview"
    assert "VECTOR_USERNAME" in vector.binding_envs
    assert "VECTOR_PASSWORD" in vector.binding_envs
from __future__ import annotations

from types import SimpleNamespace

import pytest
from k8s_native.plugin import PLUGIN

from astrolift_drivers.registry import PluginManifest, plugins
from core.cluster_observability import ClusterObservabilityError, managed_config_for


@pytest.fixture(autouse=True)
def _k8s_registry(monkeypatch):
    drivers = dict(PLUGIN.drivers)
    drivers.update(
        {
            f"managed:{kind}:{variant}": driver
            for (kind, variant), driver in PLUGIN.managed_service_drivers.items()
        }
    )
    monkeypatch.setattr(
        plugins,
        "_plugins",
        {
            "k8s_native": PluginManifest(
                plugin_id="k8s_native",
                display_name=PLUGIN.display_name,
                version="test",
                drivers=drivers,
            )
        },
    )


def _cluster(**provider_overrides):
    provider_config = {
        "cnpg_storage_class": "database-rwo",
        "redis_storage_class": "cache-rwo",
        "mysql_storage_class": "database-rwo",
        "mongodb_storage_class": "database-rwo",
        "kafka_storage_class": "stream-rwo",
        "nats_storage_class": "stream-rwo",
        "rabbitmq_storage_class": "queue-rwo",
        "knative_namespace": "functions-system",
        "gateway_api_namespace": "gateway-system",
        "gateway_api_class_name": "envoy-gateway",
        "knative_eventing_namespace": "eventing-system",
        "argo_workflows_namespace": "workflows-system",
        "kserve_namespace": "models-system",
        "kube_prometheus_namespace": "observability-system",
        "kube_prometheus_prometheus_url": "https://prometheus.example.test",
        "kube_prometheus_grafana_url": "https://grafana.example.test",
        "kube_prometheus_allowed_target_namespaces": ["shared-exporters"],
        "seaweed_namespace": "storage-system",
        "seaweed_cluster_name": "shared-store",
        "seaweed_s3_endpoint": "https://objects.example.test",
        "seaweed_s3_region": "local-1",
        "s3_existing_allowed_endpoint_hosts": ["objects.partner.test"],
        "s3_existing_allowed_credential_path_prefixes": ["org/object-store"],
        "mssql_namespace": "database-system",
        "mssql_storage_class_name": "database-rwo",
        "mssql_credential_path_prefix": "managed/sqlserver",
        "opensearch_namespace": "search-system",
        "opensearch_storage_class_name": "search-rwo",
        "opensearch_credential_path_prefix": "managed/search",
        "nfs_storage_class_name": "nfs-rwx",
        "filesystem_pvc_storage_class_name": "standard-rwo",
        "rook_cephfs_storage_class_name": "rook-shared",
        "rook_cephfs_csi_driver": "rook-ceph.cephfs.csi.ceph.com",
    }
    provider_config.update(provider_overrides)
    return SimpleNamespace(
        slug="on-prem-prod",
        region="",
        provider_config=provider_config,
        auth_config={},
        provider_plugin=SimpleNamespace(slug="k8s_native"),
    )


def test_every_registered_k8s_managed_driver_has_live_config(monkeypatch) -> None:
    cluster_driver = object()
    secrets_backend = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability", lambda _cluster, _capability: secrets_backend
    )

    for kind, variant in PLUGIN.managed_service_drivers:
        config = managed_config_for("k8s_native", _cluster(), kind=kind, variant=variant)
        assert config.cluster_driver is cluster_driver, (kind, variant)
        if (kind, variant) in {
            ("object_store", "seaweedfs_operator"),
            ("object_store", "s3_compatible_existing"),
            ("mssql", "sqlserver_express"),
            ("search", "opensearch_operator"),
            ("vector_index", "opensearch_operator_vector"),
        }:
            assert config.secrets_backend is secrets_backend


def test_dynamic_filesystem_config_preserves_operator_defaults(monkeypatch) -> None:
    cluster_driver = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)

    generic = managed_config_for(
        "k8s_native",
        _cluster(filesystem_pvc_access_modes=["ReadWriteOncePod"]),
        kind="filesystem",
        variant="storage_class_pvc",
    )
    rook = managed_config_for(
        "k8s_native",
        _cluster(rook_cephfs_access_modes=["ReadWriteMany"]),
        kind="filesystem",
        variant="rook_cephfs",
    )

    assert generic.storage_class_name == "standard-rwo"
    assert generic.default_access_modes == ("ReadWriteOncePod",)
    assert generic.csi_driver == ""
    assert rook.storage_class_name == "rook-shared"
    assert rook.default_access_modes == ("ReadWriteMany",)
    assert rook.csi_driver == "rook-ceph.cephfs.csi.ceph.com"


def test_knative_config_preserves_install_security_policy(monkeypatch) -> None:
    cluster_driver = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)

    config = managed_config_for(
        "k8s_native",
        _cluster(
            knative_allow_public=True,
            knative_allow_tagged_images=True,
            knative_allow_unsafe_pod_spec=True,
            knative_default_port=9090,
            knative_default_timeout_seconds=600,
            knative_default_container_concurrency=50,
        ),
        kind="faas",
        variant="knative_service",
    )

    assert config.cluster_driver is cluster_driver
    assert config.namespace == "functions-system"
    assert config.allow_public is True
    assert config.allow_tagged_images is True
    assert config.allow_unsafe_pod_spec is True
    assert config.default_port == 9090
    assert config.default_timeout_seconds == 600
    assert config.default_container_concurrency == 50


def test_gateway_api_config_preserves_install_security_policy(monkeypatch) -> None:
    cluster_driver = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)

    config = managed_config_for(
        "k8s_native",
        _cluster(
            gateway_api_allow_class_override=True,
            gateway_api_allow_cross_namespace_routes=True,
            gateway_api_allow_cross_namespace_backends=True,
            gateway_api_allow_cross_namespace_certificates=True,
            gateway_api_allow_custom_backends=True,
            gateway_api_allow_extension_refs=True,
            gateway_api_allow_experimental_routes=True,
            gateway_api_allow_listener_sets=True,
        ),
        kind="api_gateway",
        variant="gateway_api",
    )

    assert config.cluster_driver is cluster_driver
    assert config.namespace == "gateway-system"
    assert config.gateway_class_name == "envoy-gateway"
    assert config.allow_class_override is True
    assert config.allow_cross_namespace_routes is True
    assert config.allow_cross_namespace_backends is True
    assert config.allow_cross_namespace_certificates is True
    assert config.allow_custom_backends is True
    assert config.allow_extension_refs is True
    assert config.allow_experimental_routes is True
    assert config.allow_listener_sets is True


def test_knative_eventing_config_preserves_install_security_policy(monkeypatch) -> None:
    cluster_driver = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)

    config = managed_config_for(
        "k8s_native",
        _cluster(
            knative_eventing_broker_class="Kafka",
            knative_eventing_broker_config={
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "name": "kafka-broker-config",
                "namespace": "knative-eventing",
            },
            knative_eventing_allow_class_override=True,
            knative_eventing_allow_config_override=True,
            knative_eventing_allow_external_subscribers=True,
            knative_eventing_allow_cross_namespace_subscribers=True,
            knative_eventing_allow_alpha_delivery_fields=True,
            knative_eventing_allowed_broker_classes=["Kafka"],
        ),
        kind="event_bus",
        variant="knative_eventing",
    )

    assert config.cluster_driver is cluster_driver
    assert config.namespace == "eventing-system"
    assert config.broker_class == "Kafka"
    assert config.broker_config == {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "name": "kafka-broker-config",
        "namespace": "knative-eventing",
    }
    assert config.allow_class_override is True
    assert config.allow_config_override is True
    assert config.allow_external_subscribers is True
    assert config.allow_cross_namespace_subscribers is True
    assert config.allow_alpha_delivery_fields is True
    assert config.allowed_broker_classes == ("Kafka",)


def test_argo_workflows_config_preserves_install_security_policy(monkeypatch) -> None:
    cluster_driver = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)

    config = managed_config_for(
        "k8s_native",
        _cluster(
            argo_workflows_server_url="https://argo.example.test",
            argo_workflows_watch_all_namespaces=False,
            argo_workflows_managed_namespaces=["workflows-system"],
            argo_workflows_service_account_name="workflow-runner",
            argo_workflows_allow_service_account_override=True,
            argo_workflows_allowed_service_accounts=["workflow-runner", "gpu-runner"],
            argo_workflows_allow_workflow_template_refs=True,
            argo_workflows_allow_cluster_template_refs=True,
            argo_workflows_trusted_template_uids={"cluster/shared": "uid-1"},
            argo_workflows_allow_resource_templates=True,
            argo_workflows_allow_executor_plugins=True,
            argo_workflows_allow_external_http_templates=True,
            argo_workflows_allow_host_access=True,
            argo_workflows_allow_privileged_pods=True,
            argo_workflows_allow_pod_spec_patch=True,
            argo_workflows_allow_tagged_images=True,
            argo_workflows_allowed_image_prefixes=["registry.example.test/"],
            argo_workflows_default_parallelism=5,
            argo_workflows_max_parallelism=25,
            argo_workflows_default_active_deadline_seconds=900,
            argo_workflows_max_active_deadline_seconds=7200,
            argo_workflows_default_ttl_seconds=3600,
            argo_workflows_max_ttl_seconds=86400,
        ),
        kind="workflow_engine",
        variant="argo_workflows",
    )

    assert config.cluster_driver is cluster_driver
    assert config.namespace == "workflows-system"
    assert config.argo_server_url == "https://argo.example.test"
    assert config.watch_all_namespaces is False
    assert config.managed_namespaces == ("workflows-system",)
    assert config.service_account_name == "workflow-runner"
    assert config.allow_service_account_override is True
    assert config.allowed_service_accounts == ("workflow-runner", "gpu-runner")
    assert config.allow_workflow_template_refs is True
    assert config.allow_cluster_template_refs is True
    assert config.trusted_workflow_template_uids == {"cluster/shared": "uid-1"}
    assert config.allow_resource_templates is True
    assert config.allow_executor_plugins is True
    assert config.allow_external_http_templates is True
    assert config.allow_host_access is True
    assert config.allow_privileged_pods is True
    assert config.allow_pod_spec_patch is True
    assert config.allow_tagged_images is True
    assert config.allowed_image_prefixes == ("registry.example.test/",)
    assert config.default_parallelism == 5
    assert config.max_parallelism == 25
    assert config.default_active_deadline_seconds == 900
    assert config.max_active_deadline_seconds == 7200
    assert config.default_ttl_seconds == 3600
    assert config.max_ttl_seconds == 86400


def test_kserve_config_preserves_install_security_policy(monkeypatch) -> None:
    cluster_driver = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)

    config = managed_config_for(
        "k8s_native",
        _cluster(
            kserve_default_deployment_mode="Knative",
            kserve_allowed_deployment_modes=["Standard", "Knative"],
            kserve_service_account_name="model-runner",
            kserve_allow_service_account_override=True,
            kserve_allowed_service_accounts=["model-runner", "gpu-runner"],
            kserve_allow_service_account_token=True,
            kserve_allow_public=True,
            kserve_allow_writable_storage=True,
            kserve_allow_custom_containers=True,
            kserve_allow_tagged_images=True,
            kserve_allowed_image_prefixes=["registry.example.test/models"],
            kserve_allowed_storage_uri_schemes=["s3", "https"],
            kserve_allow_external_storage_urls=True,
            kserve_allowed_external_storage_hosts=["models.example.test"],
            kserve_allow_external_logger_urls=True,
            kserve_allowed_external_logger_hosts=["logs.example.test"],
            kserve_allow_privileged_pods=True,
            kserve_allow_host_access=True,
            kserve_allow_local_model_cache=True,
            kserve_allowed_model_formats=["sklearn"],
            kserve_allowed_serving_runtimes=["sklearn-runtime"],
            kserve_allowed_autoscaler_classes=["hpa", "keda"],
            kserve_max_replicas=12,
        ),
        kind="model_endpoint",
        variant="kserve",
    )

    assert config.cluster_driver is cluster_driver
    assert config.namespace == "models-system"
    assert config.default_deployment_mode == "Knative"
    assert config.allowed_deployment_modes == ("Standard", "Knative")
    assert config.service_account_name == "model-runner"
    assert config.allow_service_account_override is True
    assert config.allowed_service_accounts == ("model-runner", "gpu-runner")
    assert config.allow_service_account_token is True
    assert config.allow_public is True
    assert config.allow_writable_storage is True
    assert config.allow_custom_containers is True
    assert config.allow_tagged_images is True
    assert config.allowed_image_prefixes == ("registry.example.test/models",)
    assert config.allowed_storage_uri_schemes == ("s3", "https")
    assert config.allow_external_storage_urls is True
    assert config.allowed_external_storage_hosts == ("models.example.test",)
    assert config.allow_external_logger_urls is True
    assert config.allowed_external_logger_hosts == ("logs.example.test",)
    assert config.allow_privileged_pods is True
    assert config.allow_host_access is True
    assert config.allow_local_model_cache is True
    assert config.allowed_model_formats == ("sklearn",)
    assert config.allowed_serving_runtimes == ("sklearn-runtime",)
    assert config.allowed_autoscaler_classes == ("hpa", "keda")
    assert config.max_replicas == 12


def test_unknown_k8s_managed_pair_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: object())

    with pytest.raises(ClusterObservabilityError, match="no Kubernetes managed-service config builder"):
        managed_config_for("k8s_native", _cluster(), kind="filesystem", variant="imaginary")


def test_k8s_operator_defaults_are_exposed_in_provider_schema() -> None:
    properties = PLUGIN.config_schema["properties"]
    expected = {
        "cnpg_operator_namespace",
        "cnpg_storage_class",
        "cnpg_backup_url",
        "redis_storage_class",
        "redis_persistent",
        "mysql_operator_brand",
        "mysql_storage_class",
        "mysql_namespace",
        "mysql_backup_url",
        "mongodb_storage_class",
        "mongodb_namespace",
        "mongodb_backup_url",
        "kafka_storage_class",
        "kafka_namespace",
        "nats_storage_class",
        "nats_namespace",
        "nats_enable_jetstream",
        "rabbitmq_storage_class",
        "rabbitmq_namespace",
        "knative_namespace",
        "knative_allow_public",
        "knative_allow_tagged_images",
        "knative_allow_unsafe_pod_spec",
        "knative_default_port",
        "knative_default_timeout_seconds",
        "knative_default_container_concurrency",
        "gateway_api_namespace",
        "gateway_api_class_name",
        "gateway_api_allow_class_override",
        "gateway_api_allow_cross_namespace_routes",
        "gateway_api_allow_cross_namespace_backends",
        "gateway_api_allow_cross_namespace_certificates",
        "gateway_api_allow_custom_backends",
        "gateway_api_allow_extension_refs",
        "gateway_api_allow_experimental_routes",
        "gateway_api_allow_listener_sets",
        "knative_eventing_namespace",
        "knative_eventing_broker_class",
        "knative_eventing_broker_config",
        "knative_eventing_allow_class_override",
        "knative_eventing_allow_config_override",
        "knative_eventing_allow_external_subscribers",
        "knative_eventing_allow_cross_namespace_subscribers",
        "knative_eventing_allow_alpha_delivery_fields",
        "knative_eventing_allowed_broker_classes",
        "argo_workflows_namespace",
        "argo_workflows_watch_all_namespaces",
        "argo_workflows_managed_namespaces",
        "argo_workflows_server_url",
        "argo_workflows_service_account_name",
        "argo_workflows_allow_service_account_override",
        "argo_workflows_allowed_service_accounts",
        "argo_workflows_allow_workflow_template_refs",
        "argo_workflows_allow_cluster_template_refs",
        "argo_workflows_allow_resource_templates",
        "argo_workflows_allow_executor_plugins",
        "argo_workflows_allow_external_http_templates",
        "argo_workflows_allow_host_access",
        "argo_workflows_allow_privileged_pods",
        "argo_workflows_allow_pod_spec_patch",
        "argo_workflows_allow_tagged_images",
        "argo_workflows_allowed_image_prefixes",
        "argo_workflows_default_parallelism",
        "argo_workflows_max_parallelism",
        "argo_workflows_default_active_deadline_seconds",
        "argo_workflows_max_active_deadline_seconds",
        "argo_workflows_default_ttl_seconds",
        "argo_workflows_max_ttl_seconds",
        "kserve_namespace",
        "kserve_default_deployment_mode",
        "kserve_allowed_deployment_modes",
        "kserve_service_account_name",
        "kserve_allow_service_account_override",
        "kserve_allowed_service_accounts",
        "kserve_allow_service_account_token",
        "kserve_allow_public",
        "kserve_allow_writable_storage",
        "kserve_allow_custom_containers",
        "kserve_allow_tagged_images",
        "kserve_allowed_image_prefixes",
        "kserve_allowed_storage_uri_schemes",
        "kserve_allow_external_storage_urls",
        "kserve_allowed_external_storage_hosts",
        "kserve_allow_external_logger_urls",
        "kserve_allowed_external_logger_hosts",
        "kserve_allow_privileged_pods",
        "kserve_allow_host_access",
        "kserve_allow_local_model_cache",
        "kserve_allowed_model_formats",
        "kserve_allowed_serving_runtimes",
        "kserve_allowed_autoscaler_classes",
        "kserve_max_replicas",
        "kube_prometheus_namespace",
        "kube_prometheus_prometheus_service_name",
        "kube_prometheus_alertmanager_service_name",
        "kube_prometheus_grafana_service_name",
        "kube_prometheus_prometheus_url",
        "kube_prometheus_grafana_url",
        "kube_prometheus_verify_crds",
        "kube_prometheus_verify_services",
        "kube_prometheus_verify_selection",
        "kube_prometheus_allow_workload_prometheus_access",
        "kube_prometheus_allow_cross_namespace",
        "kube_prometheus_allowed_target_namespaces",
        "kube_prometheus_allow_custom_rules",
        "kube_prometheus_allow_custom_dashboards",
        "kube_prometheus_allow_honor_labels",
        "kube_prometheus_min_scrape_interval_seconds",
        "kube_prometheus_max_monitors",
        "kube_prometheus_max_endpoints_per_monitor",
        "kube_prometheus_max_samples_per_scrape",
        "kube_prometheus_max_targets_per_monitor",
        "kube_prometheus_max_rule_groups",
        "kube_prometheus_max_rules",
        "kube_prometheus_max_dashboards",
        "kube_prometheus_max_dashboard_bytes",
        "s3_existing_namespace",
        "s3_existing_allowed_endpoint_hosts",
        "s3_existing_allowed_credential_path_prefixes",
        "s3_existing_allow_insecure_http",
        "s3_existing_allow_skip_tls_verify",
        "s3_existing_allow_endpoint_paths",
        "seaweed_namespace",
        "seaweed_cluster_name",
        "seaweed_s3_endpoint",
        "seaweed_s3_scheme",
        "seaweed_s3_port",
        "seaweed_s3_region",
        "seaweed_credential_path_prefix",
        "seaweed_verify_crds",
        "seaweed_deletion_timeout_seconds",
        "mssql_namespace",
        "mssql_storage_class_name",
        "mssql_image",
        "mssql_credential_path_prefix",
        "mssql_allow_custom_images",
        "mssql_allow_load_balancer",
        "mssql_allow_network_policy_disable",
        "mssql_volume_snapshot_class",
        "mssql_allow_crash_consistent_snapshots",
        "mssql_deletion_timeout_seconds",
        "opensearch_namespace",
        "opensearch_storage_class_name",
        "opensearch_api_version",
        "opensearch_operator_namespace",
        "opensearch_version",
        "opensearch_image",
        "opensearch_bootstrap_image",
        "opensearch_credential_path_prefix",
        "opensearch_allow_custom_versions",
        "opensearch_allow_custom_images",
        "opensearch_allow_custom_bootstrap_images",
        "opensearch_allow_custom_plugins",
        "opensearch_allow_single_node",
        "opensearch_allow_network_policy_disable",
        "opensearch_http_tls_secret_name",
        "opensearch_http_tls_ca_secret_name",
        "opensearch_http_tls_admin_secret_name",
        "opensearch_http_tls_admin_dns",
        "opensearch_http_tls_verify",
        "opensearch_deletion_timeout_seconds",
        "nfs_storage_class_name",
        "nfs_server_address",
        "nfs_server_export",
        "nfs_namespace",
        "filesystem_pvc_storage_class_name",
        "filesystem_pvc_csi_driver",
        "filesystem_pvc_access_modes",
        "rook_cephfs_storage_class_name",
        "rook_cephfs_csi_driver",
        "rook_cephfs_access_modes",
    }

    assert expected <= set(properties)


def test_seaweedfs_config_preserves_shared_cluster_and_secret_backend(monkeypatch) -> None:
    cluster_driver = object()
    secrets_backend = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability", lambda _cluster, _capability: secrets_backend
    )

    config = managed_config_for(
        "k8s_native",
        _cluster(),
        kind="object_store",
        variant="seaweedfs_operator",
    )

    assert config.namespace == "storage-system"
    assert config.seaweed_name == "shared-store"
    assert config.endpoint == "https://objects.example.test"
    assert config.region == "local-1"
    assert config.cluster_driver is cluster_driver
    assert config.secrets_backend is secrets_backend


def test_existing_s3_config_preserves_adoption_policy_and_secret_backend(monkeypatch) -> None:
    cluster_driver = object()
    secrets_backend = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability", lambda _cluster, _capability: secrets_backend
    )

    config = managed_config_for(
        "k8s_native",
        _cluster(
            s3_existing_namespace="external-resources",
            s3_existing_allow_insecure_http=True,
            s3_existing_allow_skip_tls_verify=True,
            s3_existing_allow_endpoint_paths=True,
        ),
        kind="object_store",
        variant="s3_compatible_existing",
    )

    assert config.namespace == "external-resources"
    assert config.allowed_endpoint_hosts == ("objects.partner.test",)
    assert config.allowed_credential_path_prefixes == ("org/object-store",)
    assert config.allow_insecure_http is True
    assert config.allow_skip_tls_verify is True
    assert config.allow_endpoint_paths is True
    assert config.cluster_driver is cluster_driver
    assert config.secrets_backend is secrets_backend


def test_kube_prometheus_config_preserves_install_policy(monkeypatch) -> None:
    cluster_driver = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)

    config = managed_config_for(
        "k8s_native",
        _cluster(
            kube_prometheus_allow_cross_namespace=True,
            kube_prometheus_allow_custom_rules=True,
            kube_prometheus_allow_custom_dashboards=True,
            kube_prometheus_allow_honor_labels=True,
            kube_prometheus_allow_workload_prometheus_access=True,
            kube_prometheus_min_scrape_interval_seconds=5,
            kube_prometheus_max_monitors=12,
            kube_prometheus_max_samples_per_scrape=25000,
            kube_prometheus_max_targets_per_monitor=50,
        ),
        kind="observability",
        variant="kube_prometheus_stack",
    )

    assert config.monitoring_namespace == "observability-system"
    assert config.prometheus_url == "https://prometheus.example.test"
    assert config.grafana_url == "https://grafana.example.test"
    assert config.allowed_target_namespaces == ("shared-exporters",)
    assert config.allow_cross_namespace is True
    assert config.allow_custom_rules is True
    assert config.allow_custom_dashboards is True
    assert config.allow_honor_labels is True
    assert config.allow_workload_prometheus_access is True
    assert config.min_scrape_interval_seconds == 5
    assert config.max_monitors == 12
    assert config.max_samples_per_scrape == 25000
    assert config.max_targets_per_monitor == 50
    assert config.cluster_driver is cluster_driver


def test_mssql_config_preserves_install_policy_and_secret_backend(monkeypatch) -> None:
    cluster_driver = object()
    secrets_backend = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability", lambda _cluster, _capability: secrets_backend
    )

    config = managed_config_for(
        "k8s_native",
        _cluster(
            mssql_allow_load_balancer=True,
            mssql_allow_network_policy_disable=True,
            mssql_volume_snapshot_class="database-snapshots",
        ),
        kind="mssql",
        variant="sqlserver_express",
    )

    assert config.namespace == "database-system"
    assert config.storage_class_name == "database-rwo"
    assert config.credential_path_prefix == "managed/sqlserver"
    assert config.allow_load_balancer is True
    assert config.allow_network_policy_disable is True
    assert config.volume_snapshot_class == "database-snapshots"
    assert config.cluster_driver is cluster_driver
    assert config.secrets_backend is secrets_backend


@pytest.mark.parametrize(
    ("kind", "variant"),
    [
        ("search", "opensearch_operator"),
        ("vector_index", "opensearch_operator_vector"),
    ],
)
def test_opensearch_config_preserves_install_policy_and_secret_backend(
    monkeypatch,
    kind,
    variant,
) -> None:
    cluster_driver = object()
    secrets_backend = object()
    monkeypatch.setattr("core.cluster_observability._driver_for_cluster", lambda _cluster: cluster_driver)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability", lambda _cluster, _capability: secrets_backend
    )

    config = managed_config_for(
        "k8s_native",
        _cluster(
            opensearch_allow_single_node=True,
            opensearch_allow_custom_plugins=True,
            opensearch_operator_namespace="search-operator",
        ),
        kind=kind,
        variant=variant,
    )

    assert config.namespace == "search-system"
    assert config.storage_class_name == "search-rwo"
    assert config.credential_path_prefix == "managed/search"
    assert config.operator_namespace == "search-operator"
    assert config.allow_single_node is True
    assert config.allow_custom_plugins is True
    assert config.cluster_driver is cluster_driver
    assert config.secrets_backend is secrets_backend


def test_dynamic_filesystems_are_executable_preview_catalog_entries() -> None:
    from astrolift_services.managed_service_catalog import list_catalog

    rows = {row.variant: row for row in list_catalog("k8s_native") if row.kind == "filesystem"}

    for variant in ("storage_class_pvc", "rook_cephfs"):
        row = rows[variant]
        assert row.available is True
        assert row.status == "preview"
        assert row.config_schema["required"] == ["storage_class_name"]
        assert "FILESYSTEM_TLS" not in row.binding_envs


def test_k8s_object_store_catalog_distinguishes_executable_and_planned_variants() -> None:
    from astrolift_services.managed_service_catalog import list_catalog

    rows = {row.variant: row for row in list_catalog("k8s_native") if row.kind == "object_store"}

    assert rows["seaweedfs_operator"].available is True
    assert rows["seaweedfs_operator"].status == "preview"
    assert rows["seaweedfs_operator"].is_default_for_kind is True
    assert rows["s3_compatible_existing"].available is True
    assert rows["s3_compatible_existing"].status == "preview"
    assert rows["s3_compatible_existing"].is_default_for_kind is False
    assert rows["minio_operator"].available is False
    assert rows["minio_operator"].status == "deprecated"
    assert "retired" in rows["minio_operator"].unavailable_reason
    assert rows["minio_aistor_operator"].available is False


def test_kube_prometheus_is_an_executable_preview_catalog_entry() -> None:
    from astrolift_services.managed_service_catalog import list_catalog

    rows = {row.variant: row for row in list_catalog("k8s_native") if row.kind == "observability"}

    row = rows["kube_prometheus_stack"]
    assert row.available is True
    assert row.status == "preview"
    assert row.is_default_for_kind is True
    assert set(row.binding_envs) == {
        "OBSERVABILITY_PROVIDER",
        "METRICS_ENDPOINT",
        "DASHBOARD_URL",
        "PROMETHEUS_URL",
        "GRAFANA_URL",
        "OBSERVABILITY_NAMESPACE",
        "OBSERVABILITY_BUNDLE",
    }


def test_sqlserver_express_is_an_executable_preview_catalog_entry() -> None:
    from astrolift_services.managed_service_catalog import list_catalog

    rows = {row.variant: row for row in list_catalog("k8s_native") if row.kind == "mssql"}

    row = rows["sqlserver_express"]
    assert row.available is True
    assert row.status == "preview"
    assert set(row.binding_envs) == {
        "MSSQL_HOST",
        "MSSQL_PORT",
        "MSSQL_DB",
        "MSSQL_USER",
        "MSSQL_PASSWORD",
        "MSSQL_ENCRYPT",
        "MSSQL_TRUST_SERVER_CERTIFICATE",
        "DATABASE_URL",
    }


def test_opensearch_variants_are_executable_preview_catalog_entries() -> None:
    from astrolift_services.managed_service_catalog import list_catalog

    rows = {
        (row.kind, row.variant): row
        for row in list_catalog("k8s_native")
        if row.kind in {"search", "vector_index"}
    }

    search = rows[("search", "opensearch_operator")]
    vector = rows[("vector_index", "opensearch_operator_vector")]
    assert search.available is True
    assert search.status == "preview"
    assert "SEARCH_TLS_VERIFY" in search.binding_envs
    assert vector.available is True
    assert vector.status == "preview"
    assert "VECTOR_USERNAME" in vector.binding_envs
    assert "VECTOR_PASSWORD" in vector.binding_envs
