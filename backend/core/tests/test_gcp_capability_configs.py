from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.app_deploy import AppDeployError, _config_for_capability


def _cluster(**provider_overrides: object) -> SimpleNamespace:
    provider_config = {
        "project_id": "acme-prod",
        "region": "us-central1",
        "artifact_registry_location": "us",
        "artifact_registry_repo": "platform-images",
        "artifact_registry_immutable_tags": False,
        "artifact_registry_kms_key": "projects/p/locations/us/keyRings/r/cryptoKeys/ar",
        "secret_id_prefix": "smd",
        "secret_manager_kms_key": "projects/p/locations/us/keyRings/r/cryptoKeys/secrets",
        "managed_cert_name_prefix": "smd-cert",
        "ingress_variant": "gateway_api",
        "static_ip_name": "smd-global",
        "managed_cert_name": "smd-wildcard",
        "gateway_class": "gke-l7-regional-external-managed",
        "fcm_timeout_seconds": 17,
    }
    provider_config.update(provider_overrides)
    return SimpleNamespace(
        slug="gcp-prod",
        region="us-west1",
        provider_config=provider_config,
        auth_config={"fcm_access_token": "test-only-token"},
    )


def test_gcp_registry_config_uses_operator_controls() -> None:
    config = _config_for_capability("gcp", _cluster(), "registry")
    assert type(config).__name__ == "ArtifactRegistryConfig"
    assert config.project_id == "acme-prod"
    assert config.location == "us"
    assert config.repository_id == "platform-images"
    assert config.immutable_tags is False
    assert config.encryption_kms_key_name.endswith("/cryptoKeys/ar")


def test_gcp_secret_manager_config_matches_binding_resolver() -> None:
    config = _config_for_capability("gcp", _cluster(), "secrets")
    assert type(config).__name__ == "GCPSecretsConfig"
    assert config.project_id == "acme-prod"
    assert config.secret_id_prefix == "smd"
    assert config.kms_key_name.endswith("/cryptoKeys/secrets")


def test_gcp_identity_dns_and_tls_have_capability_specific_configs() -> None:
    identity = _config_for_capability("gcp", _cluster(), "identity")
    dns = _config_for_capability("gcp", _cluster(), "dns")
    tls = _config_for_capability("gcp", _cluster(), "tls")
    assert type(identity).__name__ == "GCPWIConfig"
    assert type(dns).__name__ == "CloudDNSConfig"
    assert type(tls).__name__ == "ManagedCertConfig"
    assert {identity.project_id, dns.project_id, tls.project_id} == {"acme-prod"}
    assert tls.cert_name_prefix == "smd-cert"


def test_gcp_ingress_config_preserves_provider_native_choices() -> None:
    config = _config_for_capability("gcp", _cluster(), "ingress")
    assert type(config).__name__ == "GCPIngressConfig"
    assert config.variant == "gateway_api"
    assert config.static_ip_name == "smd-global"
    assert config.managed_cert_name == "smd-wildcard"
    assert config.gateway_class == "gke-l7-regional-external-managed"


def test_gcp_notification_config_is_not_a_gke_config() -> None:
    config = _config_for_capability("gcp", _cluster(), "notification")
    assert type(config).__name__ == "FCMConfig"
    assert config.project_id == "acme-prod"
    assert config.access_token == "test-only-token"
    assert config.timeout_seconds == 17


def test_every_registered_non_cluster_gcp_capability_has_a_config() -> None:
    from gcp.plugin import PLUGIN

    for capability in sorted(set(PLUGIN.drivers) - {"cluster"}):
        config = _config_for_capability("gcp", _cluster(), capability)
        assert type(config).__name__ != "GKEConfig", capability


def test_gcp_capability_requires_project_id() -> None:
    cluster = _cluster(project_id="")
    with pytest.raises(AppDeployError, match="project_id"):
        _config_for_capability("gcp", cluster, "secrets")


def test_unknown_gcp_capability_fails_instead_of_using_gke_config() -> None:
    with pytest.raises(AppDeployError, match="no GCP config builder"):
        _config_for_capability("gcp", _cluster(), "made-up")


@pytest.mark.parametrize(
    ("kind", "variant"),
    [
        ("postgres", "cloudsql"),
        ("postgres", "alloydb"),
        ("mysql", "cloudsql"),
        ("mssql", "cloudsql_sqlserver"),
        ("redis", "memorystore"),
    ],
)
def test_gcp_managed_credentials_share_cluster_secret_prefix(
    kind: str,
    variant: str,
) -> None:
    from core.cluster_observability import managed_config_for

    config = managed_config_for("gcp", _cluster(), kind=kind, variant=variant)
    assert config.secret_id_prefix == "smd"


def test_alloydb_runtime_config_preserves_operator_controls() -> None:
    from core.cluster_observability import managed_config_for

    config = managed_config_for(
        "gcp",
        _cluster(
            alloydb_network="projects/123/global/networks/data",
            alloydb_allocated_ip_range="alloydb-private",
            alloydb_cluster_name_prefix="smd",
            alloydb_primary_instance_id="writer",
            alloydb_database_version="POSTGRES_18",
            alloydb_machine_type_default="c4a-highmem-4-lssd",
            alloydb_high_availability_default=False,
            alloydb_deletion_protection_default=False,
            alloydb_backup_retention_days=21,
            alloydb_secret_manager_prefix="managed/alloydb",
            alloydb_operation_timeout_seconds=900,
            alloydb_operation_poll_interval_seconds=2,
            alloydb_api_endpoint="https://alloydb.example.test/v1",
        ),
        kind="postgres",
        variant="alloydb",
    )
    assert config.network == "projects/123/global/networks/data"
    assert config.allocated_ip_range == "alloydb-private"
    assert config.cluster_name_prefix == "smd"
    assert config.primary_instance_id == "writer"
    assert config.database_version == "POSTGRES_18"
    assert config.machine_type_default == "c4a-highmem-4-lssd"
    assert config.high_availability_default is False
    assert config.deletion_protection_default is False
    assert config.backup_retention_days == 21
    assert config.secret_manager_prefix == "managed/alloydb"
    assert config.secret_id_prefix == "smd"
    assert config.operation_timeout_seconds == 900
    assert config.operation_poll_interval_seconds == 2
    assert config.api_endpoint == "https://alloydb.example.test/v1"


def test_pubsub_topic_runtime_config_preserves_operator_prefix() -> None:
    from core.cluster_observability import managed_config_for

    config = managed_config_for(
        "gcp",
        _cluster(pubsub_topic_prefix="smd-events"),
        kind="topic",
        variant="pubsub_topic",
    )
    assert type(config).__name__ == "PubSubTopicConfig"
    assert config.project_id == "acme-prod"
    assert config.topic_prefix == "smd-events"


def test_cloud_cdn_runtime_config_preserves_operator_controls() -> None:
    from core.cluster_observability import managed_config_for

    config = managed_config_for(
        "gcp",
        _cluster(
            cloud_cdn_name_prefix="smd-edge",
            cloud_cdn_deletion_protection_default=False,
            cloud_cdn_cache_mode_default="USE_ORIGIN_HEADERS",
            cloud_cdn_default_ttl_seconds=60,
            cloud_cdn_max_ttl_seconds=3600,
            cloud_cdn_client_ttl_seconds=30,
            cloud_cdn_serve_while_stale_seconds=120,
            cloud_cdn_invalidation_role="projects/acme-prod/roles/cdnInvalidator",
            cloud_cdn_api_endpoint="https://compute.example.test/compute/v1",
            cloud_cdn_operation_timeout_seconds=120,
            cloud_cdn_operation_poll_interval_seconds=0.5,
        ),
        kind="cdn",
        variant="cloud_cdn",
    )
    assert type(config).__name__ == "CloudCdnConfig"
    assert config.project_id == "acme-prod"
    assert config.name_prefix == "smd-edge"
    assert config.deletion_protection_default is False
    assert config.cache_mode_default == "USE_ORIGIN_HEADERS"
    assert config.default_ttl_seconds == 60
    assert config.max_ttl_seconds == 3600
    assert config.client_ttl_seconds == 30
    assert config.serve_while_stale_seconds == 120
    assert config.invalidation_role == "projects/acme-prod/roles/cdnInvalidator"
    assert config.api_endpoint == "https://compute.example.test/compute/v1"
    assert config.operation_timeout_seconds == 120
    assert config.poll_interval_seconds == 0.5


def test_bigquery_runtime_config_preserves_operator_controls() -> None:
    from core.cluster_observability import managed_config_for

    config = managed_config_for(
        "gcp",
        _cluster(
            bigquery_location="US",
            bigquery_dataset_prefix="smd_warehouse",
            bigquery_deletion_protection_default=False,
            bigquery_dataset_api_endpoint="https://bigquery.example.test/v2",
            bigquery_reservation_api_endpoint="https://reservations.example.test/v1",
        ),
        kind="warehouse",
        variant="bigquery",
    )
    assert type(config).__name__ == "BigQueryWarehouseConfig"
    assert config.project_id == "acme-prod"
    assert config.location == "US"
    assert config.dataset_prefix == "smd_warehouse"
    assert config.deletion_protection_default is False
    assert config.dataset_api_endpoint == "https://bigquery.example.test/v2"
    assert config.reservation_api_endpoint == "https://reservations.example.test/v1"


def test_firestore_runtime_config_preserves_operator_controls() -> None:
    from core.cluster_observability import managed_config_for

    config = managed_config_for(
        "gcp",
        _cluster(
            firestore_location="nam5",
            firestore_database_name_prefix="smd-documents",
            firestore_deletion_protection_default=False,
            firestore_snapshot_bucket="gs://smd-firestore-exports",
            firestore_operation_timeout_seconds=600,
            firestore_operation_poll_interval_seconds=1,
            firestore_api_endpoint="https://firestore.example.test/v1",
        ),
        kind="document_db",
        variant="firestore_native",
    )
    assert type(config).__name__ == "FirestoreConfig"
    assert config.project_id == "acme-prod"
    assert config.location == "nam5"
    assert config.database_name_prefix == "smd-documents"
    assert config.deletion_protection_default is False
    assert config.snapshot_bucket == "gs://smd-firestore-exports"
    assert config.operation_timeout_seconds == 600
    assert config.poll_interval_seconds == 1
    assert config.api_endpoint == "https://firestore.example.test/v1"


def test_sqlserver_runtime_config_preserves_operator_controls() -> None:
    from core.cluster_observability import managed_config_for

    config = managed_config_for(
        "gcp",
        _cluster(
            cloudsql_private_network="projects/acme-prod/global/networks/data",
            cloudsql_instance_name_prefix="smd",
            cloudsql_sqlserver_engine_version="SQLSERVER_2025_ENTERPRISE",
            cloudsql_sqlserver_backup_retention_days=21,
            cloudsql_high_availability_default=True,
            cloudsql_deletion_protection_default=False,
            cloudsql_secret_manager_prefix="managed/cloudsql",
            cloudsql_api_endpoint="https://sql.example.test/v1",
            cloudsql_operation_timeout_seconds=1200,
            cloudsql_operation_poll_interval_seconds=2,
        ),
        kind="mssql",
        variant="cloudsql_sqlserver",
    )
    assert type(config).__name__ == "CloudSQLServerConfig"
    assert config.project_id == "acme-prod"
    assert config.region == "us-central1"
    assert config.private_network == "projects/acme-prod/global/networks/data"
    assert config.instance_name_prefix == "smd"
    assert config.engine_version == "SQLSERVER_2025_ENTERPRISE"
    assert config.backup_retention_days == 21
    assert config.high_availability_default is True
    assert config.deletion_protection_default is False
    assert config.secret_manager_prefix == "managed/cloudsql"
    assert config.secret_id_prefix == "smd"
    assert config.api_endpoint == "https://sql.example.test/v1"
    assert config.operation_timeout_seconds == 1200
    assert config.poll_interval_seconds == 2


def test_spanner_graph_runtime_config_preserves_operator_controls() -> None:
    from core.cluster_observability import managed_config_for

    config = managed_config_for(
        "gcp",
        _cluster(
            spanner_instance_name_prefix="smd-graph",
            spanner_shared_instance_id="shared-graph",
            spanner_instance_config="nam6",
            spanner_edition="ENTERPRISE_PLUS",
            spanner_processing_units=1000,
            spanner_automatic_backup_schedule=False,
            spanner_deletion_protection_default=False,
            spanner_backup_retention_days=90,
            spanner_api_endpoint="https://spanner.example.test/v1",
            spanner_operation_timeout_seconds=900,
            spanner_operation_poll_interval_seconds=1,
            spanner_adopt_existing_instance=True,
        ),
        kind="graph_db",
        variant="spanner_graph",
    )
    assert type(config).__name__ == "SpannerGraphConfig"
    assert config.project_id == "acme-prod"
    assert config.region == "us-central1"
    assert config.instance_name_prefix == "smd-graph"
    assert config.shared_instance_id == "shared-graph"
    assert config.instance_config == "nam6"
    assert config.edition == "ENTERPRISE_PLUS"
    assert config.processing_units == 1000
    assert config.automatic_backup_schedule is False
    assert config.deletion_protection_default is False
    assert config.backup_retention_days == 90
    assert config.api_endpoint == "https://spanner.example.test/v1"
    assert config.operation_timeout_seconds == 900
    assert config.poll_interval_seconds == 1
    assert config.adopt_existing_instance is True
