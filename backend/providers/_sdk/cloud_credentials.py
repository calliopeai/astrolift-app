"""Which cloud identity a driver authenticates as (#1422).

Every driver in this tree takes per-cluster config and then authenticates
with whatever identity the control-plane *process* happens to carry:
``boto3.client(...)`` walks botocore's default chain,
``google.auth.default()`` reads ADC, ``DefaultAzureCredential()`` reads the
environment. That makes the account a property of the deployment rather than
of the cluster row, and it is invisible at every call site.

The three clouds are not equally affected, and the difference decides how much
of this module each one needs:

* GCP and Azure address the resource container explicitly. ``project_id`` and
  ``subscription_id`` come off the cluster row and go into the request, so one
  ambient service account with IAM in two projects already drives two projects.
  What ambience costs them is only the *cross-boundary* case: a different
  Google org, a different Entra tenant.
* AWS has no such field. The account is not addressable in the request at all
  because it is a property of the credential. ``provider_config.account_id``
  exists, but drivers only interpolate it into ARN strings; nothing routes on
  it and nothing checks it against the identity actually in use. So a cluster
  can declare account B, be driven by credentials in account A, provision into
  A, and hand back ARNs that name B.

Hence :class:`CredentialMode`: ``AMBIENT`` is what the whole tree does today,
and the one explicit mode is AWS role assumption, because AWS is the one cloud
where an explicit credential is the only way to reach a second account.

**Nothing here ever carries credential material.** A role ARN, an external ID
and an account number are pointers; they are safe in ``provider_config``, a
plaintext JSON column. An access key or a client secret is not, and would be
exactly the leak ``_sdk.binding_policy`` forbids for managed-service bindings.
:func:`credential_from_config` enforces that with the same classifier the
binding rule uses, so the two paths cannot drift: a credential block naming a
key that ``classify_key`` calls credential-bearing is refused outright rather
than stored. Modes that genuinely need material (a GCP service-account key, an
Entra client secret) must first grow a secret-reference indirection, which is
why they are absent rather than half-supported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from _sdk.binding_policy import KeyClass, classify_key


class CloudCredentialError(ValueError):
    """A cluster's ``credential`` block is malformed or unsafe.

    Raised at config-build time, before any client is constructed, so a
    misdeclared credential fails the operation rather than silently falling
    back to the ambient identity. Falling back is the outcome this whole
    module exists to prevent: it looks like success and provisions into the
    wrong account.
    """


class CredentialMode(StrEnum):
    AMBIENT = "ambient"
    """The control-plane process's own identity: instance/task role, ADC,
    managed identity. Every driver's behaviour today."""

    AWS_ASSUME_ROLE = "aws_assume_role"
    """``sts:AssumeRole`` into ``role_arn`` before building any client. The
    control plane's own identity is still what authenticates the AssumeRole
    call, so the tenant account's trust policy remains the authority over
    whether this install may reach it."""


#: What ``declared_account`` means per cloud, and which ``provider_config``
#: key it is read from. AWS is the only one where the value is not otherwise
#: used to route a request, which is why it is the only one worth verifying
#: against the live caller identity.
_ACCOUNT_KEYS: dict[str, tuple[str, ...]] = {
    "aws": ("account_id",),
    "gcp": ("project_id", "gcp_project_id"),
    "azure": ("subscription_id",),
}

#: STS accepts ``[\w+=,.@-]{2,64}`` for a role session name. The session name
#: is what shows up in the tenant's CloudTrail, so it is worth spending on an
#: identifying string rather than a constant.
_SESSION_NAME_ALLOWED = re.compile(r"[^\w+=,.@-]")

_MAX_SESSION_NAME = 64


@dataclass(frozen=True, slots=True)
class CloudCredential:
    """The identity one driver operation should authenticate as."""

    cloud: str
    mode: CredentialMode = CredentialMode.AMBIENT

    role_arn: str = ""
    """AWS role to assume. Empty for every other mode."""

    external_id: str = ""
    """Optional ``sts:ExternalId``. Not a secret in the sense the binding rule
    means: it is a confused-deputy nonce that only has force in combination
    with the role's trust policy, and AWS documents it as shareable with the
    third party that uses it."""

    declared_account: str = ""
    """The account / project / subscription the cluster row says it is in.
    Advisory for GCP and Azure, where the request already names the container.
    Load-bearing for AWS, where it is the only thing a mismatch can be caught
    against."""

    session_name: str = "astrolift"
    """Role session name, surfaced in the tenant's audit log."""

    @property
    def is_ambient(self) -> bool:
        return self.mode is CredentialMode.AMBIENT


def session_name_for(cluster_slug: str) -> str:
    """Build an STS-legal role session name that identifies the cluster.

    Worth the few lines because this string is the tenant's only handle on
    *which* of our clusters made a call when they read their own CloudTrail.
    """
    candidate = f"astrolift-{cluster_slug}".strip("-")
    sanitized = _SESSION_NAME_ALLOWED.sub("-", candidate) or "astrolift"
    return sanitized[:_MAX_SESSION_NAME]


def credential_from_config(
    *,
    cloud: str,
    provider_config: dict[str, Any] | None,
    auth_config: dict[str, Any] | None = None,
    cluster_slug: str = "",
) -> CloudCredential:
    """Read a cluster's declared credential, or report the ambient default.

    Takes plain dicts rather than a ``TenantCluster`` so the rule stays in the
    provider SDK, where the drivers that must honour it live, and stays
    testable without Django.
    """
    pc = provider_config or {}
    ac = auth_config or {}
    declared_account = _declared_account(cloud, pc, ac)
    session_name = session_name_for(cluster_slug) if cluster_slug else "astrolift"

    block = pc.get("credential", ac.get("credential"))
    if block is None:
        return CloudCredential(
            cloud=cloud,
            mode=CredentialMode.AMBIENT,
            declared_account=declared_account,
            session_name=session_name,
        )
    if not isinstance(block, dict):
        raise CloudCredentialError(
            f"{cloud}: provider_config.credential must be an object, got {type(block).__name__}",
        )

    _reject_inline_secrets(cloud, block)

    raw_mode = str(block.get("mode", "") or "").strip()
    try:
        mode = CredentialMode(raw_mode)
    except ValueError as exc:
        supported = ", ".join(sorted(m.value for m in CredentialMode))
        raise CloudCredentialError(
            f"{cloud}: unsupported credential mode {raw_mode!r}; supported modes are {supported}",
        ) from exc

    if mode is CredentialMode.AMBIENT:
        return CloudCredential(
            cloud=cloud,
            mode=mode,
            declared_account=declared_account,
            session_name=session_name,
        )

    if cloud != "aws":
        raise CloudCredentialError(
            f"{cloud}: credential mode {mode.value!r} is AWS-only",
        )

    role_arn = str(block.get("role_arn", "") or "").strip()
    if not role_arn.startswith("arn:") or ":iam:" not in role_arn or ":role/" not in role_arn:
        raise CloudCredentialError(
            f"aws: credential mode {mode.value!r} requires role_arn to be an IAM role ARN, got {role_arn!r}",
        )
    return CloudCredential(
        cloud=cloud,
        mode=mode,
        role_arn=role_arn,
        external_id=str(block.get("external_id", "") or "").strip(),
        declared_account=declared_account or _account_from_role_arn(role_arn),
        session_name=session_name,
    )


def _declared_account(cloud: str, provider_config: dict[str, Any], auth_config: dict[str, Any]) -> str:
    for key in _ACCOUNT_KEYS.get(cloud, ()):
        value = provider_config.get(key) or auth_config.get(key)
        if value:
            return str(value)
    return ""


def _account_from_role_arn(role_arn: str) -> str:
    parts = role_arn.split(":")
    return parts[4] if len(parts) > 5 else ""


def _reject_inline_secrets(cloud: str, block: dict[str, Any]) -> None:
    """Refuse a credential block that names a credential-bearing key.

    Classified by name with the binding rule's own classifier rather than by
    an allowlist of known-bad keys, so a future ``client_secret`` or
    ``service_account_key`` is caught the day someone tries it rather than the
    day someone remembers to extend a list. The refusal is unconditional: no
    mode defined here needs material, so there is no legitimate value to
    admit, and the alternative — accepting a secret reference — is a whole
    resolution path that should be designed rather than back-doored.
    """
    offenders = sorted(key for key in block if classify_key(str(key)) is KeyClass.CREDENTIAL)
    if not offenders:
        return
    raise CloudCredentialError(
        f"{cloud}: provider_config.credential may not carry credential material "
        f"({', '.join(offenders)}). It is a plaintext JSON column; a literal there "
        f"cannot be unwritten. Declare a role to assume instead.",
    )
