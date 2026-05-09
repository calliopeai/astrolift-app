r"""
Workload identity policy: roles, SA binding, IAM annotations (#8, spec 12 §5.1, §5.3, §5.4).

Pure-Python policy. The WorkloadIdentityRole CRUD mutations and
the deploy-time SA-annotation activity consult this module for:

* **Policy derivation** — given an app's bound managed services,
  emit the high-level (resource, actions) policy statements that
  the cloud-side IAM role needs.
* **ServiceAccount annotation rendering** — per-cloud annotations
  for IRSA (AWS), Workload Identity (GCP), and AAD Pod Identity
  (Azure). Each cloud has its own conventions; this module
  centralizes them so drivers stay symmetric.
* **Cross-account delegation chain** — validates the assume-role
  chain spec 12 §5.4 describes (cluster-IRSA-role → workload-role
  in another account).
* **SA name conventions** — \`<app-slug>\` in the app's namespace,
  per spec §5.3.

Driver code lives in ``astrolift_drivers/workload_identity/``;
this module is the shape contract drivers consume.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from enum import Enum


class WorkloadIdentityError(ValueError):
    pass


# ---- managed-service → policy derivation ---------------------------


class PolicyResourceKind(str, Enum):
    """Spec 12 §5.1: high-level resource categories. Each cloud
    driver maps these to provider-specific ARNs/URIs/scopes."""

    OBJECT_STORE = "object_store"
    """S3 bucket, GCS bucket, Azure Blob container."""

    QUEUE = "queue"
    """SQS queue, Pub/Sub subscription, Service Bus queue."""

    SECRET = "secret"
    """Secrets Manager / Secret Manager / Key Vault."""

    DATABASE = "database"
    """RDS / Cloud SQL / Azure SQL — usually IAM-auth-only."""

    PUBSUB_TOPIC = "pubsub_topic"
    """SNS / Pub/Sub topic / Service Bus topic. Publish-only;
    subscribers go through ``QUEUE``."""


# Action vocabularies are deliberately small per resource kind so
# drivers translate to provider verbs deterministically. New kinds
# extend; existing kinds don't grow without a code review.
_ALLOWED_ACTIONS_BY_KIND: dict[PolicyResourceKind, frozenset[str]] = {
    PolicyResourceKind.OBJECT_STORE: frozenset({
        "read", "write", "list", "delete",
    }),
    PolicyResourceKind.QUEUE: frozenset({
        "send", "receive", "delete", "purge",
    }),
    PolicyResourceKind.SECRET: frozenset({
        "read", "write",
    }),
    PolicyResourceKind.DATABASE: frozenset({
        "connect",
    }),
    PolicyResourceKind.PUBSUB_TOPIC: frozenset({
        "publish",
    }),
}


@dataclasses.dataclass(frozen=True, slots=True)
class PolicyStatement:
    """One policy line — high-level, pre-driver."""

    kind: PolicyResourceKind
    resource_id: str
    """Provider-neutral identifier (e.g. ``s3:my-bucket``,
    ``sqs:my-queue``). Drivers parse this into ARN/URI."""

    actions: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.resource_id:
            raise WorkloadIdentityError(
                f"resource_id is required for {self.kind.value} statement"
            )
        if not self.actions:
            raise WorkloadIdentityError(
                f"{self.kind.value}:{self.resource_id} has no actions"
            )
        allowed = _ALLOWED_ACTIONS_BY_KIND[self.kind]
        bad = set(self.actions) - allowed
        if bad:
            raise WorkloadIdentityError(
                f"{self.kind.value}:{self.resource_id} has unsupported "
                f"actions: {sorted(bad)} (allowed: {sorted(allowed)})"
            )


# Default action sets per managed-service kind. Tenant apps can
# narrow via manifest; this is the safe-default broadest-needed.
_DEFAULT_ACTIONS_BY_SERVICE: dict[str, tuple[PolicyResourceKind, tuple[str, ...]]] = {
    "object_store": (PolicyResourceKind.OBJECT_STORE, ("read", "write", "list")),
    "queue": (PolicyResourceKind.QUEUE, ("send", "receive", "delete")),
    "secret": (PolicyResourceKind.SECRET, ("read",)),
    "database": (PolicyResourceKind.DATABASE, ("connect",)),
    "pubsub_topic": (PolicyResourceKind.PUBSUB_TOPIC, ("publish",)),
}


@dataclasses.dataclass(frozen=True, slots=True)
class ServiceBinding:
    """One bound managed service from the app's manifest."""

    service_kind: str
    """Logical kind: object_store, queue, secret, database,
    pubsub_topic. Maps to one of the policy resource kinds."""

    resource_id: str
    """The cloud resource identifier. Driver picks the ARN/URI
    out of this."""

    custom_actions: tuple[str, ...] = ()
    """Override default actions. Empty tuple = use defaults."""


def derive_policies(
    *,
    bindings: Sequence[ServiceBinding],
) -> tuple[PolicyStatement, ...]:
    """Spec §5.1: roll the app's bound managed services up into
    policy statements for the IAM role. Default action sets per
    kind; manifest overrides via ``custom_actions``."""
    out: list[PolicyStatement] = []
    for b in bindings:
        if b.service_kind not in _DEFAULT_ACTIONS_BY_SERVICE:
            raise WorkloadIdentityError(
                f"unknown service kind {b.service_kind!r}; known: "
                f"{sorted(_DEFAULT_ACTIONS_BY_SERVICE.keys())}"
            )
        kind, default_actions = _DEFAULT_ACTIONS_BY_SERVICE[b.service_kind]
        actions = b.custom_actions or default_actions
        out.append(PolicyStatement(
            kind=kind, resource_id=b.resource_id, actions=actions,
        ))
    return tuple(out)


# ---- ServiceAccount conventions ------------------------------------


_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
"""K8s SA names follow RFC 1123 label rules. Same as DNS labels."""


def serviceaccount_name_for_app(*, app_slug: str) -> str:
    """Spec §5.3: SA name == app slug. Single-source-of-truth so
    GitOps + manifest renderer + RBAC bindings agree."""
    if not app_slug:
        raise WorkloadIdentityError("app_slug is required")
    if not _SLUG_RE.match(app_slug):
        raise WorkloadIdentityError(
            f"app_slug {app_slug!r} is not a valid k8s SA name "
            "(RFC 1123: lowercase alphanumeric or '-', must start "
            "and end with alphanumeric, max 63 chars)"
        )
    return app_slug


# ---- IAM annotation rendering --------------------------------------


class CloudKind(str, Enum):
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"


def aws_irsa_annotations(*, role_arn: str) -> dict[str, str]:
    """AWS IRSA: SA carries the role ARN annotation; the EKS
    pod-identity webhook injects AWS_ROLE_ARN env."""
    if not role_arn.startswith("arn:aws:iam::") or ":role/" not in role_arn:
        raise WorkloadIdentityError(
            f"AWS role ARN {role_arn!r} doesn't look like an IAM role "
            "(expected 'arn:aws:iam::<account>:role/<name>')"
        )
    return {"eks.amazonaws.com/role-arn": role_arn}


def gcp_workload_identity_annotations(
    *, gcp_sa_email: str,
) -> dict[str, str]:
    """GCP Workload Identity: SA carries the GCP SA email
    annotation. Trust binding (KSA → GSA) is configured on the
    GCP-side IAM policy, not here."""
    if "@" not in gcp_sa_email or "." not in gcp_sa_email.split("@")[-1]:
        raise WorkloadIdentityError(
            f"GCP SA email {gcp_sa_email!r} doesn't look valid "
            "(expected '<name>@<project>.iam.gserviceaccount.com')"
        )
    return {"iam.gke.io/gcp-service-account": gcp_sa_email}


def azure_workload_identity_annotations(
    *,
    client_id: str,
    tenant_id: str = "",
) -> dict[str, str]:
    """Azure Workload Identity: SA carries the AAD client ID
    annotation (and optional tenant ID for cross-tenant scenarios).
    Trust binding is configured on the AAD app's federated
    credentials, not here."""
    if not _is_uuid(client_id):
        raise WorkloadIdentityError(
            f"Azure client_id {client_id!r} is not a valid UUID"
        )
    out = {"azure.workload.identity/client-id": client_id}
    if tenant_id:
        if not _is_uuid(tenant_id):
            raise WorkloadIdentityError(
                f"Azure tenant_id {tenant_id!r} is not a valid UUID"
            )
        out["azure.workload.identity/tenant-id"] = tenant_id
    return out


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _is_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value))


# ---- annotation rotation -------------------------------------------


def annotations_changed(
    *,
    old: dict[str, str],
    new: dict[str, str],
) -> bool:
    """Spec §5.3: rotate SA annotations when underlying cloud role
    changes. Caller diffs to know whether to re-apply.

    Comparison is exact (set of keys + values). Adding a key,
    removing a key, or value drift all return True.
    """
    return old != new


# ---- cross-account delegation (spec §5.4) --------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class DelegationStep:
    """One hop in the assume-role chain."""

    cloud: CloudKind
    role_id: str
    """Provider-specific role identifier (ARN / SA email / app
    object ID)."""

    external_id: str = ""
    r"""Optional confused-deputy guard. AWS: \`sts:ExternalId\`."""


def validate_delegation_chain(
    *,
    chain: Sequence[DelegationStep],
) -> None:
    """Spec §5.4: cross-account workload identity is a chain of
    assume-role hops. Validate:

    1. At least 1 hop (no chain = no delegation).
    2. Max 3 hops (otherwise audit becomes intractable; AWS
       enforces a 5-hop hard cap, but 3 is plenty for legit use
       cases and refuses spaghetti).
    3. All hops in the same cloud (cross-CLOUD identity is a
       different mechanism — workforce federation, not workload).
    4. All role_ids non-empty.
    """
    if not chain:
        raise WorkloadIdentityError(
            "delegation chain cannot be empty (omit the chain "
            "rather than passing [])"
        )
    if len(chain) > 3:
        raise WorkloadIdentityError(
            f"delegation chain has {len(chain)} hops; max 3 "
            "(audit becomes intractable, and legit cross-account "
            "patterns rarely need more than 2)"
        )

    first_cloud = chain[0].cloud
    for i, step in enumerate(chain):
        if step.cloud != first_cloud:
            raise WorkloadIdentityError(
                f"delegation chain mixes clouds at hop {i} "
                f"({step.cloud.value}, expected {first_cloud.value}); "
                "cross-cloud workload identity isn't supported"
            )
        if not step.role_id:
            raise WorkloadIdentityError(
                f"delegation chain hop {i} missing role_id"
            )


# ---- whole-role validation -----------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class BoundServiceAccount:
    """Spec §5.1: WorkloadIdentityRole.bound_serviceaccount."""

    cluster_id: int
    namespace: str
    sa_name: str

    def __post_init__(self) -> None:
        if not self.namespace:
            raise WorkloadIdentityError("namespace is required")
        if not self.sa_name:
            raise WorkloadIdentityError("sa_name is required")
        if not _SLUG_RE.match(self.sa_name):
            raise WorkloadIdentityError(
                f"sa_name {self.sa_name!r} not RFC 1123 valid"
            )
        if not _SLUG_RE.match(self.namespace):
            raise WorkloadIdentityError(
                f"namespace {self.namespace!r} not RFC 1123 valid"
            )
