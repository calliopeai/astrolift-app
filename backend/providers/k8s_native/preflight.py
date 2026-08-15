"""K8s-native operator pre-flight checks (#74).

Operator-backed managed services (CNPG, Bitnami Redis, Percona MySQL,
Strimzi Kafka, RabbitMQ Cluster Operator, NATS, etc.) require their
operator to be installed in the target cluster before the platform
can apply tenant CRDs.

Pre-flight runs at provision time + at composition-delegation
register time. It looks at ClusterCapabilities + a per-operator
required-CRD list and returns a structured PreflightReport so the
control plane can fail fast (and tell the operator which Helm chart
to install) rather than apply a CRD that won't reconcile.

Hybrid variant delegation (#54 composition) also passes through
here: when an AWS plugin delegates ('postgres', 'cnpg') to the
k8s_native CNPGPostgresDriver, the AWS plugin's bind validator
calls preflight against the target cluster's capabilities — the
delegation only resolves if CNPG is actually installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _sdk.cluster_capabilities import ClusterCapabilities


@dataclass(frozen=True)
class OperatorRequirement:
    operator_id: str
    """Stable identifier (e.g. 'cnpg', 'strimzi', 'rabbitmq-cluster-operator')."""

    display_name: str
    required_crds: tuple[str, ...]
    """Fully-qualified CRD names the operator MUST install before
    we can apply tenant CRDs."""

    install_hint: str
    """Human / AI-readable instruction for getting the operator on.
    e.g. 'helm install cnpg ...' or 'kubectl apply -f ...'."""

    minimum_kubernetes_version: str = "1.27"


@dataclass(frozen=True)
class PreflightFailure:
    requirement_id: str
    code: str
    """missing_crd | missing_operator | k8s_too_old | unknown_variant"""

    message: str


@dataclass(frozen=True)
class PreflightReport:
    ok: bool
    variant_key: tuple[str, str]
    failures: list[PreflightFailure] = field(default_factory=list)
    install_hints: list[str] = field(default_factory=list)


# Per-(kind, variant) operator requirements. Drivers that target
# operators land their requirement here at the same time as the
# driver code.
REQUIREMENTS: dict[tuple[str, str], OperatorRequirement] = {
    ("postgres", "cnpg"): OperatorRequirement(
        operator_id="cnpg",
        display_name="CloudNativePG",
        required_crds=(
            "clusters.postgresql.cnpg.io",
            "backups.postgresql.cnpg.io",
        ),
        install_hint=("helm install cnpg cnpg/cloudnative-pg --namespace cnpg-system --create-namespace"),
    ),
    ("redis", "operator"): OperatorRequirement(
        operator_id="redis-operator",
        display_name="Bitnami Redis Operator",
        required_crds=(
            "redis.redis.redis.opstreelabs.in",
            "redisreplications.redis.redis.opstreelabs.in",
        ),
        install_hint=(
            "helm install redis-operator ot-helm/redis-operator --namespace redis-operator --create-namespace"
        ),
    ),
    ("mysql", "operator"): OperatorRequirement(
        operator_id="percona-xtradb-cluster",
        display_name="Percona Operator for MySQL",
        required_crds=(
            "perconaxtradbclusters.pxc.percona.com",
            "perconaxtradbclusterbackups.pxc.percona.com",
        ),
        install_hint=("helm install pxc-operator percona/pxc-operator --namespace pxc-operator --create-namespace"),
    ),
    ("document_db", "mongodb_operator"): OperatorRequirement(
        operator_id="psmdb-operator",
        display_name="Percona Server for MongoDB",
        required_crds=("perconaservermongodbs.psmdb.percona.com",),
        install_hint=(
            "helm install psmdb-operator percona/psmdb-operator --namespace psmdb-operator --create-namespace"
        ),
    ),
    ("event_stream", "kafka_strimzi"): OperatorRequirement(
        operator_id="strimzi-cluster-operator",
        display_name="Strimzi Kafka Operator",
        required_crds=(
            "kafkas.kafka.strimzi.io",
            "kafkanodepools.kafka.strimzi.io",
            "kafkausers.kafka.strimzi.io",
        ),
        install_hint=("helm install strimzi strimzi/strimzi-kafka-operator --namespace kafka --create-namespace"),
    ),
    ("event_stream", "nats"): OperatorRequirement(
        operator_id="nats-server",
        display_name="NATS",
        required_crds=(),  # NATS uses plain StatefulSets
        install_hint=(
            "Plain StatefulSet — no operator install needed; ensure the NATS image is reachable from the cluster"
        ),
    ),
    ("queue", "rabbitmq_operator"): OperatorRequirement(
        operator_id="rabbitmq-cluster-operator",
        display_name="RabbitMQ Cluster Operator",
        required_crds=("rabbitmqclusters.rabbitmq.com",),
        install_hint=(
            "kubectl apply -f https://github.com/rabbitmq/cluster"
            "-operator/releases/latest/download/cluster-operator.yml"
        ),
    ),
    ("filesystem", "nfs_csi"): OperatorRequirement(
        operator_id="nfs-csi",
        display_name="NFS CSI Driver",
        required_crds=(),  # CSI drivers don't ship CRDs
        install_hint=("helm install csi-driver-nfs csi-driver-nfs/csi-driver-nfs --namespace kube-system"),
    ),
    ("filesystem", "nfs_subdir_provisioner"): OperatorRequirement(
        operator_id="nfs-subdir-provisioner",
        display_name="NFS Subdir Provisioner",
        required_crds=(),
        install_hint=(
            "helm install nfs-subdir-provisioner "
            "nfs-subdir-external-provisioner/nfs-subdir-external-provisioner "
            "--namespace nfs-provisioner --create-namespace"
        ),
    ),
    ("filesystem", "storage_class_pvc"): OperatorRequirement(
        operator_id="storage-class",
        display_name="Kubernetes dynamic provisioning",
        required_crds=(),
        install_hint="Install a CSI provisioner and create the selected StorageClass",
    ),
    ("filesystem", "rook_cephfs"): OperatorRequirement(
        operator_id="rook-ceph-operator",
        display_name="Rook Ceph",
        required_crds=(
            "cephclusters.ceph.rook.io",
            "cephfilesystems.ceph.rook.io",
        ),
        install_hint=("Install the Rook Ceph operator, create a CephFilesystem, and create its CephFS StorageClass"),
    ),
}


def preflight(
    *,
    kind: str,
    variant: str,
    capabilities: ClusterCapabilities,
) -> PreflightReport:
    """Verify that the cluster has the operator + CRDs needed to
    provision (kind, variant)."""
    key = (kind, variant)
    requirement = REQUIREMENTS.get(key)
    if requirement is None:
        return PreflightReport(
            ok=False,
            variant_key=key,
            failures=[
                PreflightFailure(
                    requirement_id="",
                    code="unknown_variant",
                    message=(f"no preflight requirement registered for ({kind!r}, {variant!r})"),
                )
            ],
        )

    failures: list[PreflightFailure] = []
    hints: list[str] = []

    # k8s version check
    if capabilities.kubernetes_version and _version_too_old(
        actual=capabilities.kubernetes_version,
        minimum=requirement.minimum_kubernetes_version,
    ):
        failures.append(
            PreflightFailure(
                requirement_id=requirement.operator_id,
                code="k8s_too_old",
                message=(
                    f"cluster runs Kubernetes "
                    f"{capabilities.kubernetes_version!r}; operator "
                    f"{requirement.display_name} requires "
                    f"{requirement.minimum_kubernetes_version}+"
                ),
            )
        )

    # CRDs are typically the strongest signal an operator is up.
    # The probe in cluster_capabilities populates per-operator
    # bool flags (cnpg_installed, gateway_api_installed, etc.).
    # For broader CRD checks, a future iteration should expose
    # the raw CRD list from the probe; for now we use the
    # bool flags where they exist.
    if requirement.required_crds and not _operator_present(
        capabilities=capabilities,
        operator_id=requirement.operator_id,
    ):
        failures.append(
            PreflightFailure(
                requirement_id=requirement.operator_id,
                code="missing_operator",
                message=(
                    f"operator {requirement.display_name!r} not "
                    f"detected on cluster {capabilities.cluster_id!r}; "
                    f"install via: {requirement.install_hint}"
                ),
            )
        )
        hints.append(requirement.install_hint)

    return PreflightReport(
        ok=not failures,
        variant_key=key,
        failures=failures,
        install_hints=hints,
    )


def _operator_present(
    *,
    capabilities: ClusterCapabilities,
    operator_id: str,
) -> bool:
    """Map operator_id → ClusterCapabilities boolean flag.
    Future iterations expose raw CRD list and check that directly."""
    flag_map = {
        "cnpg": capabilities.cnpg_installed,
        # other operators don't have dedicated flags yet — assume
        # present if the operator_versions dict mentions them.
    }
    if operator_id in flag_map:
        return flag_map[operator_id]
    return operator_id in capabilities.operator_versions


def _version_too_old(*, actual: str, minimum: str) -> bool:
    """Compare 'major.minor' Kubernetes versions."""
    try:
        actual_t = tuple(int(p) for p in actual.split(".")[:2])
        minimum_t = tuple(int(p) for p in minimum.split(".")[:2])
    except ValueError:
        return False
    return actual_t < minimum_t
