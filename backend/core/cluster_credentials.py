"""Reading a cluster's declared cloud credential, and refusing to ignore it (#1422).

``_sdk.cloud_credentials`` defines what a credential declaration looks like.
This module is the Django-side half: it reads one off a ``TenantCluster`` row
and, more importantly, stops the rest of the control plane from silently
disregarding it.

That second job is the whole reason this lands before any driver is migrated.
Credentials are ambient today at roughly 240 client-construction sites across
three clouds, and they cannot all move in one change to a security-sensitive
path. But a partial migration has a failure mode worse than the original
constraint: an operator declares a role for account B, the log path honours it,
the RDS driver does not, and a database appears in account A while the console
says B. "Wrong account, no error" is not an outcome to ship toward.

So declaring a credential is opt-in and fail-closed. Every funnel that builds
a driver config asks :func:`assert_credential_supported` first, and anything
not yet migrated refuses the operation with a message naming the capability.
Migrating a driver is then a two-line change here plus the driver's own work,
and at no point is there a path that quietly uses the wrong identity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from _sdk.cloud_credentials import CloudCredential

    from astrolift_clusters.models import TenantCluster


class ClusterCredentialUnsupported(RuntimeError):
    """The cluster declares an explicit credential and this code path cannot
    honour it yet.

    Distinct from an invalid declaration: the row is fine, the caller is
    simply not migrated. The operator's options are to remove the declaration
    or to wait for the capability, so the message names which capability
    refused.
    """


class ClusterCredentialInvalid(ValueError):
    """The cluster's ``credential`` block is malformed or carries material.

    The provider SDK raises its own ``CloudCredentialError`` for this;
    translating it here keeps every ``core`` caller free of a provider import,
    which the schema-export command depends on — it loads these modules
    without the plugin SDK installed.
    """


#: The two ways a credential declaration stops an operation. Grouped so a
#: funnel can translate both into its own error type in one clause.
CREDENTIAL_REFUSALS: tuple[type[Exception], ...] = (
    ClusterCredentialUnsupported,
    ClusterCredentialInvalid,
)


#: Capabilities whose driver construction threads the credential all the way
#: to the cloud client. Everything absent from this set still authenticates
#: ambiently and therefore refuses to run against a cluster that declares a
#: credential. Grow it one migrated driver at a time.
CREDENTIAL_AWARE_CAPABILITIES: frozenset[str] = frozenset({"log_query"})


def credential_for_cluster(cluster: TenantCluster) -> CloudCredential:
    """The identity operations against ``cluster`` should authenticate as.

    Ambient for every cluster that does not declare otherwise, which is every
    cluster that exists today.
    """
    from _sdk.cloud_credentials import CloudCredentialError, credential_from_config

    slug = _slug_for(cluster)
    try:
        return credential_from_config(
            cloud=_cloud_for(cluster),
            provider_config=_as_dict(getattr(cluster, "provider_config", None)),
            auth_config=_as_dict(getattr(cluster, "auth_config", None)),
            cluster_slug=slug,
        )
    except CloudCredentialError as exc:
        raise ClusterCredentialInvalid(f"cluster {slug}: {exc}") from exc


def assert_credential_supported(cluster: TenantCluster, *, capability: str) -> None:
    """Refuse the operation when ``cluster`` declares a credential this
    capability would ignore.

    Also the parse point for the declaration itself, so a malformed or unsafe
    credential block fails here rather than at some later cloud call.
    """
    credential = credential_for_cluster(cluster)
    if credential.is_ambient or capability in CREDENTIAL_AWARE_CAPABILITIES:
        return
    raise ClusterCredentialUnsupported(
        f"cluster {_slug_for(cluster)}: declares credential mode {credential.mode.value!r}, but the "
        f"{capability!r} path still authenticates with the control plane's own identity. "
        f"Running it would act on the wrong account without saying so.",
    )


def _cloud_for(cluster: TenantCluster) -> str:
    """The cluster's own cloud, empty when the row has no plugin bound yet.

    Mirrors ``_context_for_cluster``: the FK is non-null in the schema but
    unset on a row that has not been saved. Empty is the fail-closed answer —
    no cloud claims a credential declaration, so any declaration on such a row
    is refused rather than matched to a driver.
    """
    plugin = getattr(cluster, "provider_plugin", None)
    return str(getattr(plugin, "slug", "") or "")


def _slug_for(cluster: TenantCluster) -> str:
    """Only ever cosmetic here — it names the cluster in the refusal message
    and in the STS role session name. Read defensively so the guard itself
    cannot be the thing that fails on a partially built row."""
    return str(getattr(cluster, "slug", "") or "")


def _as_dict(value: Any) -> dict[str, Any]:
    """JSONField columns are dicts in practice, but a hand-edited row or an
    older migration can leave a null or a list, and this runs before any
    driver would have caught it. An absent column reads the same as an empty
    one: no declaration, so ambient."""
    return value if isinstance(value, dict) else {}
