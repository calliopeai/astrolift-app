"""Storage encryption policy + local-dev support (#28).

Every persistent-data managed-service variant (object_store, postgres,
mysql, redis, document_db, key_value, search, time_series, filesystem)
must encrypt at rest by default. The encryption mode varies by
variant:

- cloud-native KMS (AWS KMS, GCP CMEK, Azure Key Vault Keys) — default
  for cloud-managed variants where the operator can specify a
  customer-managed key
- LUKS-on-PV — for k8s_native operator-backed variants running on
  encrypted underlying volumes (operator-managed StorageClass)
- application-level (sealed secrets, age-encrypted backups) — for
  variants where neither cloud KMS nor PV encryption applies

For local-dev, the policy degrades to "encryption_at_rest=false +
warn" so dev clusters with kind/minikube don't silently get a
phantom encrypted-at-rest claim. The platform surfaces the warning
in the operator UI so dev → prod promotion fails the encryption
preflight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

EncryptionMode = Literal[
    "cloud_kms",
    "pv_luks",
    "application_level",
    "none",
]


@dataclass(frozen=True)
class EncryptionPolicy:
    mode: EncryptionMode
    """How encryption is enforced for this variant."""

    cmek_supported: bool
    """Customer-managed encryption key supported. When False the
    cloud's default service-managed key is used (still encrypted
    at rest, but not auditable to a customer-controlled key)."""

    cmek_required_for_compliance: tuple[str, ...] = ()
    """Compliance frameworks that require CMEK (e.g. 'pci', 'hipaa',
    'fedramp_high'). Operators in these frameworks fail bind without
    a CMEK reference."""

    in_transit_default: bool = True
    """TLS in transit on by default — disabling requires opt-out."""

    notes: str = ""


# Per-(plugin_id, kind, variant) policy. Where a variant supports
# CMEK, the driver's config_schema MUST expose a kms_key field.
POLICIES: dict[tuple[str, str, str], EncryptionPolicy] = {
    # AWS — every variant supports KMS CMEK
    ("aws", "object_store", "s3"): EncryptionPolicy(
        mode="cloud_kms",
        cmek_supported=True,
        cmek_required_for_compliance=("pci", "hipaa", "fedramp_high"),
    ),
    ("aws", "queue", "sqs"): EncryptionPolicy(
        mode="cloud_kms",
        cmek_supported=True,
    ),
    ("aws", "filesystem", "efs"): EncryptionPolicy(
        mode="cloud_kms",
        cmek_supported=True,
        cmek_required_for_compliance=("pci", "hipaa", "fedramp_high"),
        notes="EFS encryption is immutable after creation; the driver enables it by default.",
    ),
    # GCP
    ("gcp", "object_store", "gcs"): EncryptionPolicy(
        mode="cloud_kms",
        cmek_supported=True,
        cmek_required_for_compliance=("pci", "hipaa"),
    ),
    ("gcp", "queue", "pubsub"): EncryptionPolicy(
        mode="cloud_kms",
        cmek_supported=True,
    ),
    # Azure
    ("azure", "object_store", "blob"): EncryptionPolicy(
        mode="cloud_kms",
        cmek_supported=True,
        cmek_required_for_compliance=("pci", "hipaa"),
        notes=(
            "Azure-managed key by default; customer-managed key via Storage Account encryption settings + Key Vault."
        ),
    ),
    ("azure", "queue", "servicebus"): EncryptionPolicy(
        mode="cloud_kms",
        cmek_supported=True,
    ),
    # k8s_native operator-backed variants
    ("k8s_native", "postgres", "cnpg"): EncryptionPolicy(
        mode="pv_luks",
        cmek_supported=False,
        notes=(
            "PV-level encryption depends on the StorageClass "
            "having an encrypted underlying provisioner (e.g., "
            "ebs-csi with kmsKeyId, gce-pd with kmsKey). The "
            "operator does not run application-level encryption."
        ),
    ),
    ("k8s_native", "redis", "operator"): EncryptionPolicy(
        mode="pv_luks",
        cmek_supported=False,
    ),
    ("k8s_native", "mysql", "operator"): EncryptionPolicy(
        mode="pv_luks",
        cmek_supported=False,
    ),
    ("k8s_native", "document_db", "mongodb_operator"): EncryptionPolicy(
        mode="pv_luks",
        cmek_supported=False,
    ),
    ("k8s_native", "event_stream", "kafka_strimzi"): EncryptionPolicy(
        mode="pv_luks",
        cmek_supported=False,
    ),
    ("k8s_native", "event_stream", "nats"): EncryptionPolicy(
        mode="pv_luks",
        cmek_supported=False,
    ),
    ("k8s_native", "queue", "rabbitmq_operator"): EncryptionPolicy(
        mode="pv_luks",
        cmek_supported=False,
    ),
    ("k8s_native", "filesystem", "nfs_csi"): EncryptionPolicy(
        mode="pv_luks",
        cmek_supported=False,
        notes=(
            "Encryption depends on the NFS server's underlying "
            "filesystem (LUKS / ZFS native encryption) — Astrolift "
            "doesn't enforce, only documents."
        ),
    ),
}


@dataclass(frozen=True)
class EncryptionCheckResult:
    ok: bool
    mode: EncryptionMode
    cmek_used: bool
    warnings: list[str] = field(default_factory=list)
    """Soft issues — surfaces in the UI but doesn't block bind."""

    failures: list[str] = field(default_factory=list)
    """Hard issues that block bind (compliance violations)."""


def policy_for(
    *,
    plugin_id: str,
    kind: str,
    variant: str,
) -> EncryptionPolicy | None:
    return POLICIES.get((plugin_id, kind, variant))


def check_encryption(
    *,
    plugin_id: str,
    kind: str,
    variant: str,
    cmek_key: str | None = None,
    compliance_frameworks: list[str] | None = None,
    is_local_dev: bool = False,
) -> EncryptionCheckResult:
    """Verify the encryption policy for a (plugin, kind, variant)
    against an optional CMEK reference + compliance frameworks.

    Local-dev mode (kind / minikube clusters) downgrades to a
    warning rather than failure so devs can iterate without the
    full KMS plumbing — but the warning surfaces explicitly so
    promotion to staging/prod fails the encryption preflight."""
    policy = policy_for(
        plugin_id=plugin_id,
        kind=kind,
        variant=variant,
    )
    if policy is None:
        return EncryptionCheckResult(
            ok=False,
            mode="none",
            cmek_used=False,
            failures=[
                f"no encryption policy registered for "
                f"({plugin_id!r}, {kind!r}, {variant!r}) — "
                f"add one in _sdk/storage_encryption.py before bind",
            ],
        )

    warnings: list[str] = []
    failures: list[str] = []
    cmek_used = bool(cmek_key)
    frameworks = compliance_frameworks or []

    # Compliance: CMEK required by framework but not provided
    required_by = [f for f in policy.cmek_required_for_compliance if f in frameworks]
    if required_by and not cmek_used:
        msg = (
            f"compliance framework(s) {required_by} require CMEK "
            f"on ({plugin_id}, {kind}, {variant}) but no "
            f"customer-managed key was supplied"
        )
        if is_local_dev:
            warnings.append(
                msg + " (local-dev: warning only; will fail in staging/prod)",
            )
        else:
            failures.append(msg)

    # Mode==none is only acceptable when explicitly local_dev
    if policy.mode == "none":
        msg = f"({plugin_id}, {kind}, {variant}) policy mode='none' — no encryption at rest"
        if is_local_dev:
            warnings.append(msg)
        else:
            failures.append(msg)

    # CMEK supplied for a variant that doesn't support it
    if cmek_key and not policy.cmek_supported:
        warnings.append(
            f"({plugin_id}, {kind}, {variant}) doesn't support CMEK; the supplied key will be ignored",
        )

    return EncryptionCheckResult(
        ok=not failures,
        mode=policy.mode,
        cmek_used=cmek_used and policy.cmek_supported,
        warnings=warnings,
        failures=failures,
    )
