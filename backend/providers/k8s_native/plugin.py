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
from k8s_native.managed.event_stream_nats import NATSDriver
from k8s_native.managed.event_stream_strimzi import StrimziKafkaDriver
from k8s_native.managed.filesystem_nfs import NFSDriver
from k8s_native.managed.filesystem_pvc import RookCephFSDriver, StorageClassPVCDriver
from k8s_native.managed.mongodb_operator import MongoDBOperatorDriver
from k8s_native.managed.mysql_operator import MySQLOperatorDriver
from k8s_native.managed.postgres_cnpg import CNPGPostgresDriver
from k8s_native.managed.queue_rabbitmq import RabbitMQOperatorDriver
from k8s_native.managed.redis_operator import RedisOperatorDriver
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
                    "Vault address. Required when 'secrets' driver is "
                    "selected. Empty falls back to k8s Secrets via the "
                    "External Secrets Operator (separate ticket)."
                ),
            },
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
