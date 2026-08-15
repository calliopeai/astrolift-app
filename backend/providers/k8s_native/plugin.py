"""Vanilla Kubernetes provider plugin manifest.

Targets kind, minikube, k3d, k3s, and any standards-compliant
Kubernetes cluster without cloud-managed services. Implements
the full driver set so a tenant cluster can be bound to this
plugin and run end-to-end without depending on AWS/GCP/Azure
SDKs.

Drivers shipped:
- K8sNativeClusterDriver (#48 + #6) — ClusterDriver
- K8sIngressDriver (#49 + #8) — IngressDriver, multi-variant
  (nginx / Gateway API / Traefik / Kong / Istio)
- ExternalDnsDriver (#50 + #9) — DnsDriver via DNSEndpoint CRDs
- CertManagerDriver (#50 + #9) — TlsDriver via Certificate CRDs
- VaultSecretsBackend (#51 + #10) — SecretsBackend via Vault KV v2
- ProjectedSaTokenDriver (#51 + #11) — WorkloadIdentityDriver
  with projected SA tokens
- OCIRegistryDriver (#52 + #12) — ImageRegistryDriver, generic
  OCI (Harbor / Zot / GHCR)
- CNPGPostgresDriver (#53) — Postgres via CloudNativePG operator
- RedisOperatorDriver (#53) — Redis via Bitnami operator
"""

from _sdk.base import ProviderPlugin
from k8s_native.cluster import K8sNativeClusterDriver
from k8s_native.dns_external import ExternalDnsDriver
from k8s_native.identity_projected import ProjectedSaTokenDriver
from k8s_native.ingress import K8sIngressDriver
from k8s_native.managed.api_gateway import GatewayAPIDriver
from k8s_native.managed.event_bus_knative import KnativeEventingDriver
from k8s_native.managed.event_stream_nats import NATSDriver
from k8s_native.managed.event_stream_strimzi import StrimziKafkaDriver
from k8s_native.managed.faas_knative import KnativeServiceDriver
from k8s_native.managed.filesystem_nfs import NFSDriver
from k8s_native.managed.filesystem_pvc import RookCephFSDriver, StorageClassPVCDriver
from k8s_native.managed.model_endpoint_kserve import KServeDriver
from k8s_native.managed.mongodb_operator import MongoDBOperatorDriver
from k8s_native.managed.mssql_express import DEFAULT_IMAGE as DEFAULT_MSSQL_IMAGE
from k8s_native.managed.mssql_express import SQLServerExpressDriver
from k8s_native.managed.mysql_operator import MySQLOperatorDriver
from k8s_native.managed.object_store_existing_s3 import ExistingS3ObjectStoreDriver
from k8s_native.managed.object_store_seaweedfs import SeaweedFSObjectStoreDriver
from k8s_native.managed.observability_kube_prometheus import KubePrometheusStackDriver
from k8s_native.managed.opensearch_operator import (
    DEFAULT_BOOTSTRAP_IMAGE as DEFAULT_OPENSEARCH_BOOTSTRAP_IMAGE,
)
from k8s_native.managed.opensearch_operator import DEFAULT_IMAGE as DEFAULT_OPENSEARCH_IMAGE
from k8s_native.managed.opensearch_operator import (
    OpenSearchSearchDriver,
    OpenSearchVectorDriver,
)
from k8s_native.managed.postgres_cnpg import CNPGPostgresDriver
from k8s_native.managed.queue_rabbitmq import RabbitMQOperatorDriver
from k8s_native.managed.redis_operator import RedisOperatorDriver
from k8s_native.managed.workflow_argo import ArgoWorkflowsDriver
from k8s_native.notification_otlp import WebhookSMTPNotificationDriver
from k8s_native.registry_oci import OCIRegistryDriver
from k8s_native.secrets_vault import VaultSecretsBackend
from k8s_native.tls_certmanager import CertManagerDriver

PLUGIN = ProviderPlugin(
    id="k8s_native",
    display_name="Kubernetes (vanilla)",
    drivers={
        "cluster": K8sNativeClusterDriver,
        "ingress": K8sIngressDriver,
        "dns": ExternalDnsDriver,
        "tls": CertManagerDriver,
        "secrets": VaultSecretsBackend,
        "identity": ProjectedSaTokenDriver,
        "registry": OCIRegistryDriver,
        "notification": WebhookSMTPNotificationDriver,
    },
    managed_service_drivers={
        ("postgres", "cnpg"): CNPGPostgresDriver,
        ("redis", "operator"): RedisOperatorDriver,
        ("mysql", "operator"): MySQLOperatorDriver,
        ("document_db", "mongodb_operator"): MongoDBOperatorDriver,
        ("event_stream", "kafka_strimzi"): StrimziKafkaDriver,
        ("event_stream", "nats"): NATSDriver,
        ("queue", "rabbitmq_operator"): RabbitMQOperatorDriver,
        ("faas", "knative_service"): KnativeServiceDriver,
        ("api_gateway", "gateway_api"): GatewayAPIDriver,
        ("event_bus", "knative_eventing"): KnativeEventingDriver,
        ("workflow_engine", "argo_workflows"): ArgoWorkflowsDriver,
        ("model_endpoint", "kserve"): KServeDriver,
        ("observability", "kube_prometheus_stack"): KubePrometheusStackDriver,
        ("object_store", "s3_compatible_existing"): ExistingS3ObjectStoreDriver,
        ("object_store", "seaweedfs_operator"): SeaweedFSObjectStoreDriver,
        ("mssql", "sqlserver_express"): SQLServerExpressDriver,
        ("search", "opensearch_operator"): OpenSearchSearchDriver,
        ("vector_index", "opensearch_operator_vector"): OpenSearchVectorDriver,
        ("filesystem", "nfs_csi"): NFSDriver,
        ("filesystem", "storage_class_pvc"): StorageClassPVCDriver,
        ("filesystem", "rook_cephfs"): RookCephFSDriver,
    },
    config_schema={
        "type": "object",
        "properties": {
            "kubeconfig_path": {"type": "string"},
            "context": {"type": "string"},
            "in_cluster": {"type": "boolean", "default": False},
            "ingress_variant": {
                "type": "string",
                "enum": [
                    "nginx_ingress",
                    "gateway_api",
                    "traefik",
                    "kong",
                    "istio_gateway",
                ],
                "default": "nginx_ingress",
            },
            "cert_manager_issuer": {
                "type": "string",
                "default": "letsencrypt-prod",
            },
            "vault_address": {
                "type": "string",
                "description": (
                    "Vault address. Required when the Vault secrets driver is selected; "
                    "there is no implicit Kubernetes-Secret fallback."
                ),
            },
            "vault_kv_mount": {"type": "string", "default": "secret"},
            "vault_path_prefix": {"type": "string", "default": "astrolift"},
            "vault_namespace": {"type": "string"},
            "vault_auth_method": {
                "type": "string",
                "enum": ["token", "kubernetes"],
                "default": "token",
            },
            "vault_sa_role": {"type": "string"},
            "oci_registry_url": {
                "type": "string",
                "description": ("Generic OCI registry URL (Harbor/Zot/GHCR)."),
            },
            "cnpg_storage_class": {"type": "string"},
            "cnpg_backup_url": {"type": "string"},
            "cnpg_operator_namespace": {"type": "string", "default": "cnpg-system"},
            "redis_storage_class": {"type": "string"},
            "redis_persistent": {"type": "boolean", "default": True},
            "mysql_operator_brand": {
                "type": "string",
                "enum": ["percona", "oracle", "mariadb"],
                "default": "percona",
            },
            "mysql_storage_class": {"type": "string"},
            "mysql_namespace": {"type": "string"},
            "mysql_backup_url": {"type": "string"},
            "mongodb_storage_class": {"type": "string"},
            "mongodb_namespace": {"type": "string"},
            "mongodb_backup_url": {"type": "string"},
            "kafka_storage_class": {"type": "string"},
            "kafka_namespace": {"type": "string"},
            "nats_storage_class": {"type": "string"},
            "nats_namespace": {"type": "string"},
            "nats_enable_jetstream": {"type": "boolean", "default": True},
            "rabbitmq_storage_class": {"type": "string"},
            "rabbitmq_namespace": {"type": "string"},
            "knative_namespace": {"type": "string"},
            "knative_allow_public": {"type": "boolean", "default": False},
            "knative_allow_tagged_images": {"type": "boolean", "default": False},
            "knative_allow_unsafe_pod_spec": {"type": "boolean", "default": False},
            "knative_default_port": {
                "type": "integer",
                "minimum": 1,
                "maximum": 65535,
                "default": 8080,
            },
            "knative_default_timeout_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3600,
                "default": 300,
            },
            "knative_default_container_concurrency": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
            },
            "gateway_api_namespace": {"type": "string"},
            "gateway_api_class_name": {"type": "string"},
            "gateway_api_allow_class_override": {"type": "boolean", "default": False},
            "gateway_api_allow_cross_namespace_routes": {
                "type": "boolean",
                "default": False,
            },
            "gateway_api_allow_cross_namespace_backends": {
                "type": "boolean",
                "default": False,
            },
            "gateway_api_allow_cross_namespace_certificates": {
                "type": "boolean",
                "default": False,
            },
            "gateway_api_allow_custom_backends": {"type": "boolean", "default": False},
            "gateway_api_allow_extension_refs": {"type": "boolean", "default": False},
            "gateway_api_allow_experimental_routes": {
                "type": "boolean",
                "default": False,
            },
            "gateway_api_allow_listener_sets": {"type": "boolean", "default": False},
            "knative_eventing_namespace": {"type": "string"},
            "knative_eventing_broker_class": {
                "type": "string",
                "default": "MTChannelBasedBroker",
            },
            "knative_eventing_broker_config": {"type": "object"},
            "knative_eventing_allow_class_override": {
                "type": "boolean",
                "default": False,
            },
            "knative_eventing_allow_config_override": {
                "type": "boolean",
                "default": False,
            },
            "knative_eventing_allow_external_subscribers": {
                "type": "boolean",
                "default": False,
            },
            "knative_eventing_allow_cross_namespace_subscribers": {
                "type": "boolean",
                "default": False,
            },
            "knative_eventing_allow_alpha_delivery_fields": {
                "type": "boolean",
                "default": False,
            },
            "knative_eventing_allowed_broker_classes": {
                "type": "array",
                "items": {"type": "string"},
                "default": [
                    "MTChannelBasedBroker",
                    "ChannelBasedBroker",
                    "Kafka",
                    "RabbitMQBroker",
                ],
            },
            "argo_workflows_namespace": {
                "type": "string",
                "description": (
                    "Optional fixed managed namespace; otherwise each project uses its normal tenant namespace."
                ),
            },
            "argo_workflows_watch_all_namespaces": {
                "type": "boolean",
                "default": True,
                "description": "Whether the installed Argo controller watches every namespace.",
            },
            "argo_workflows_managed_namespaces": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Namespaces watched by a namespace-scoped or managed-namespace controller.",
            },
            "argo_workflows_server_url": {"type": "string"},
            "argo_workflows_service_account_name": {
                "type": "string",
                "default": "argo-workflow",
                "description": (
                    "Baseline workflow-pod ServiceAccount. Astrolift creates it with "
                    "workflowtaskresults create/patch RBAC when absent; a pre-existing "
                    "account remains operator-managed."
                ),
            },
            "argo_workflows_allow_service_account_override": {
                "type": "boolean",
                "default": False,
            },
            "argo_workflows_allowed_service_accounts": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "argo_workflows_allow_cluster_template_refs": {
                "type": "boolean",
                "default": False,
            },
            "argo_workflows_allow_workflow_template_refs": {
                "type": "boolean",
                "default": False,
            },
            "argo_workflows_trusted_template_uids": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "default": {},
                "description": (
                    "Exact UID pins for non-Astrolift WorkflowTemplate references, keyed by "
                    "'<namespace>/<name>' or 'cluster/<name>'."
                ),
            },
            "argo_workflows_allow_resource_templates": {
                "type": "boolean",
                "default": False,
            },
            "argo_workflows_allow_executor_plugins": {
                "type": "boolean",
                "default": False,
            },
            "argo_workflows_allow_external_http_templates": {
                "type": "boolean",
                "default": False,
            },
            "argo_workflows_allow_host_access": {
                "type": "boolean",
                "default": False,
            },
            "argo_workflows_allow_privileged_pods": {
                "type": "boolean",
                "default": False,
            },
            "argo_workflows_allow_pod_spec_patch": {
                "type": "boolean",
                "default": False,
            },
            "argo_workflows_allow_tagged_images": {
                "type": "boolean",
                "default": False,
            },
            "argo_workflows_allowed_image_prefixes": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "argo_workflows_default_parallelism": {
                "type": "integer",
                "minimum": 1,
                "default": 10,
            },
            "argo_workflows_max_parallelism": {
                "type": "integer",
                "minimum": 1,
                "default": 50,
            },
            "argo_workflows_default_active_deadline_seconds": {
                "type": "integer",
                "minimum": 1,
                "default": 3600,
            },
            "argo_workflows_max_active_deadline_seconds": {
                "type": "integer",
                "minimum": 1,
                "default": 86400,
            },
            "argo_workflows_default_ttl_seconds": {
                "type": "integer",
                "minimum": 0,
                "default": 86400,
            },
            "argo_workflows_max_ttl_seconds": {
                "type": "integer",
                "minimum": 0,
                "default": 604800,
            },
            "kserve_namespace": {
                "type": "string",
                "description": ("Optional fixed namespace; otherwise each model endpoint uses its project namespace."),
            },
            "kserve_default_deployment_mode": {
                "type": "string",
                "enum": ["Standard", "Knative", "ModelMesh"],
                "default": "Standard",
            },
            "kserve_allowed_deployment_modes": {
                "type": "array",
                "items": {"type": "string", "enum": ["Standard", "Knative", "ModelMesh"]},
                "default": ["Standard"],
            },
            "kserve_service_account_name": {"type": "string", "default": "kserve-model"},
            "kserve_allow_service_account_override": {"type": "boolean", "default": False},
            "kserve_allowed_service_accounts": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "kserve_allow_service_account_token": {"type": "boolean", "default": False},
            "kserve_allow_public": {"type": "boolean", "default": False},
            "kserve_allow_writable_storage": {"type": "boolean", "default": False},
            "kserve_allow_custom_containers": {"type": "boolean", "default": False},
            "kserve_allow_tagged_images": {"type": "boolean", "default": False},
            "kserve_allowed_image_prefixes": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "kserve_allowed_storage_uri_schemes": {
                "type": "array",
                "items": {"type": "string"},
                "default": ["s3", "gs", "hf", "pvc", "oci", "oci+native"],
            },
            "kserve_allow_external_storage_urls": {"type": "boolean", "default": False},
            "kserve_allowed_external_storage_hosts": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "kserve_allow_external_logger_urls": {"type": "boolean", "default": False},
            "kserve_allowed_external_logger_hosts": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "kserve_allow_privileged_pods": {"type": "boolean", "default": False},
            "kserve_allow_host_access": {"type": "boolean", "default": False},
            "kserve_allow_local_model_cache": {"type": "boolean", "default": False},
            "kserve_allowed_model_formats": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "kserve_allowed_serving_runtimes": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "kserve_allowed_autoscaler_classes": {
                "type": "array",
                "items": {"type": "string", "enum": ["hpa", "keda", "external", "none"]},
                "default": ["hpa", "none"],
            },
            "kserve_max_replicas": {"type": "integer", "minimum": 0, "default": 100},
            "kube_prometheus_namespace": {"type": "string", "default": "astrolift-system"},
            "kube_prometheus_prometheus_service_name": {
                "type": "string",
                "default": "astrolift-kube-prometheus-prometheus",
            },
            "kube_prometheus_alertmanager_service_name": {
                "type": "string",
                "default": "astrolift-kube-prometheus-alertmanager",
            },
            "kube_prometheus_grafana_service_name": {
                "type": "string",
                "default": "astrolift-kube-prometheus-stack-grafana",
            },
            "kube_prometheus_prometheus_url": {"type": "string"},
            "kube_prometheus_grafana_url": {"type": "string"},
            "kube_prometheus_verify_crds": {"type": "boolean", "default": True},
            "kube_prometheus_verify_services": {"type": "boolean", "default": True},
            "kube_prometheus_verify_selection": {"type": "boolean", "default": True},
            "kube_prometheus_allow_workload_prometheus_access": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Trusted-operator opt-in that exposes the shared, cluster-wide Prometheus query endpoint to "
                    "workload bindings whose namespace the cluster operator also labels "
                    "astrolift.io/trusted-observability-access=true. Alertmanager is never exposed."
                ),
            },
            "kube_prometheus_allow_cross_namespace": {"type": "boolean", "default": False},
            "kube_prometheus_allowed_target_namespaces": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "kube_prometheus_allow_custom_rules": {"type": "boolean", "default": False},
            "kube_prometheus_allow_custom_dashboards": {"type": "boolean", "default": False},
            "kube_prometheus_allow_honor_labels": {"type": "boolean", "default": False},
            "kube_prometheus_min_scrape_interval_seconds": {
                "type": "integer",
                "minimum": 1,
                "default": 15,
            },
            "kube_prometheus_max_monitors": {"type": "integer", "minimum": 0, "default": 20},
            "kube_prometheus_max_endpoints_per_monitor": {
                "type": "integer",
                "minimum": 1,
                "default": 10,
            },
            "kube_prometheus_max_samples_per_scrape": {
                "type": "integer",
                "minimum": 1,
                "default": 50000,
            },
            "kube_prometheus_max_targets_per_monitor": {
                "type": "integer",
                "minimum": 1,
                "default": 100,
            },
            "kube_prometheus_max_rule_groups": {"type": "integer", "minimum": 0, "default": 20},
            "kube_prometheus_max_rules": {"type": "integer", "minimum": 0, "default": 100},
            "kube_prometheus_max_dashboards": {"type": "integer", "minimum": 0, "default": 10},
            "kube_prometheus_max_dashboard_bytes": {
                "type": "integer",
                "minimum": 1024,
                "default": 512000,
            },
            "s3_existing_namespace": {
                "type": "string",
                "description": "Optional fixed namespace for external S3 adoption records.",
            },
            "s3_existing_allowed_endpoint_hosts": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Endpoint host or DNS-suffix allowlist; cluster Service DNS must also be explicit.",
            },
            "s3_existing_allowed_credential_path_prefixes": {
                "type": "array",
                "items": {"type": "string"},
                "default": ["managed/object_store/{organization}"],
            },
            "s3_existing_allow_insecure_http": {"type": "boolean", "default": False},
            "s3_existing_allow_skip_tls_verify": {"type": "boolean", "default": False},
            "s3_existing_allow_endpoint_paths": {"type": "boolean", "default": False},
            "seaweed_namespace": {
                "type": "string",
                "default": "astrolift-storage",
                "description": "Namespace of the shared operator-managed Seaweed cluster.",
            },
            "seaweed_cluster_name": {
                "type": "string",
                "default": "astrolift-object-store",
            },
            "seaweed_s3_endpoint": {
                "type": "string",
                "description": "Explicit S3 gateway URL; defaults to the in-cluster Service FQDN.",
            },
            "seaweed_s3_scheme": {
                "type": "string",
                "enum": ["http", "https"],
                "default": "http",
            },
            "seaweed_s3_port": {
                "type": "integer",
                "minimum": 1,
                "maximum": 65535,
                "default": 8333,
            },
            "seaweed_s3_region": {"type": "string", "default": "us-east-1"},
            "seaweed_credential_path_prefix": {
                "type": "string",
                "default": "managed/object_store",
            },
            "seaweed_verify_crds": {"type": "boolean", "default": True},
            "seaweed_deletion_timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "default": 120,
            },
            "mssql_namespace": {"type": "string"},
            "mssql_storage_class_name": {"type": "string"},
            "mssql_image": {
                "type": "string",
                "default": DEFAULT_MSSQL_IMAGE,
                "description": "Pinned Microsoft SQL Server 2025 Express container image.",
            },
            "mssql_credential_path_prefix": {
                "type": "string",
                "default": "managed/mssql",
            },
            "mssql_allow_custom_images": {"type": "boolean", "default": False},
            "mssql_allow_load_balancer": {"type": "boolean", "default": False},
            "mssql_allow_network_policy_disable": {"type": "boolean", "default": False},
            "mssql_volume_snapshot_class": {"type": "string"},
            "mssql_allow_crash_consistent_snapshots": {
                "type": "boolean",
                "default": False,
            },
            "mssql_deletion_timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "default": 120,
            },
            "opensearch_namespace": {"type": "string"},
            "opensearch_storage_class_name": {"type": "string"},
            "opensearch_api_version": {
                "type": "string",
                "enum": ["opensearch.org/v1"],
                "default": "opensearch.org/v1",
            },
            "opensearch_operator_namespace": {
                "type": "string",
                "default": "opensearch-operator-system",
            },
            "opensearch_version": {"type": "string", "default": "3.8.0"},
            "opensearch_image": {
                "type": "string",
                "default": DEFAULT_OPENSEARCH_IMAGE,
            },
            "opensearch_bootstrap_image": {
                "type": "string",
                "default": DEFAULT_OPENSEARCH_BOOTSTRAP_IMAGE,
            },
            "opensearch_credential_path_prefix": {
                "type": "string",
                "default": "managed/opensearch",
            },
            "opensearch_allow_custom_versions": {"type": "boolean", "default": False},
            "opensearch_allow_custom_images": {"type": "boolean", "default": False},
            "opensearch_allow_custom_bootstrap_images": {
                "type": "boolean",
                "default": False,
            },
            "opensearch_allow_custom_plugins": {"type": "boolean", "default": False},
            "opensearch_allow_single_node": {"type": "boolean", "default": False},
            "opensearch_allow_network_policy_disable": {
                "type": "boolean",
                "default": False,
            },
            "opensearch_http_tls_secret_name": {"type": "string"},
            "opensearch_http_tls_ca_secret_name": {"type": "string"},
            "opensearch_http_tls_admin_secret_name": {"type": "string"},
            "opensearch_http_tls_admin_dns": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
            },
            "opensearch_http_tls_verify": {"type": "boolean", "default": False},
            "opensearch_deletion_timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "default": 180,
            },
            "nfs_storage_class_name": {"type": "string"},
            "nfs_server_address": {"type": "string"},
            "nfs_server_export": {"type": "string", "default": "/export"},
            "nfs_namespace": {"type": "string"},
            "filesystem_pvc_storage_class_name": {
                "type": "string",
                "description": "Default StorageClass for generic consumer-local PVCs.",
            },
            "filesystem_pvc_csi_driver": {
                "type": "string",
                "description": "Optional expected provisioner; enables fail-closed CSI verification.",
            },
            "filesystem_pvc_access_modes": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "uniqueItems": True,
                "items": {
                    "enum": [
                        "ReadOnlyMany",
                        "ReadWriteMany",
                        "ReadWriteOnce",
                        "ReadWriteOncePod",
                    ],
                },
                "default": ["ReadWriteOnce"],
            },
            "rook_cephfs_storage_class_name": {
                "type": "string",
                "default": "rook-cephfs",
            },
            "rook_cephfs_csi_driver": {
                "type": "string",
                "default": "rook-ceph.cephfs.csi.ceph.com",
            },
            "rook_cephfs_access_modes": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "uniqueItems": True,
                "items": {
                    "enum": [
                        "ReadOnlyMany",
                        "ReadWriteMany",
                        "ReadWriteOnce",
                        "ReadWriteOncePod",
                    ],
                },
                "default": ["ReadWriteMany"],
            },
        },
    },
)
