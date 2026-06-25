"""WorkloadIdentityDriver protocol -- bind Kubernetes ServiceAccounts to cloud identities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class IdentityBinding:
    """Operator-facing binding snapshot returned by
    ``WorkloadIdentityDriver.describe_identity``.

    ``kind`` carries the binding flavour so the UI can render the right
    affordances per cloud. ``trust_policy_summary`` is a 1-line
    human-readable abstract — full trust JSON is too dense for the card,
    operators drill into the cloud console for that. ``last_used_at`` is
    None when the cloud doesn't record last-use (or we haven't fetched
    the credential report yet) — the UI renders that as "—" rather than
    erroring."""

    kind: str
    """``"irsa" | "workload_identity" | "federated" | "unknown"``."""

    role_arn_or_principal: str
    trust_policy_summary: str
    last_used_at: str | None = None


class WorkloadIdentityDriver(Protocol):
    """Protocol for binding Kubernetes ServiceAccounts to cloud IAM identities.

    The platform stores the identity role independent of cloud, so a re-bind
    across providers is a config change.

    Implementations: irsa (AWS OIDC trust), gke_workload_identity (GCP),
    aks_federated_credentials (Azure), projected_sa_token (vanilla k8s).
    """

    def bind_service_account(
        self,
        cluster: str,
        namespace: str,
        sa_name: str,
        identity_role: str,
    ) -> dict[str, str]: ...

    def create_identity_role(
        self,
        name: str,
        permissions: list[dict[str, Any]],
    ) -> str: ...

    def attach_policy(self, role: str, policy: str) -> None: ...

    def delete_identity_role(self, role: str) -> None: ...

    # ---- observability reads (default: not implemented) --------------
    #
    # See the same note on DnsDriver — additive, raises by default so
    # non-AWS drivers stay shape-compatible without forced stubs. AWS
    # overrides in ``aws/identity_irsa.py``.

    def describe_identity(
        self,
        app_slug: str,
    ) -> IdentityBinding | None:
        """Return the identity bound to the given app, or ``None`` when
        no binding exists yet. The default raises
        :class:`UnsupportedOperationError` so non-AWS drivers stay
        shape-compatible without forced stubs (#619). Resolvers map this
        to a user-facing "not supported on this cloud" message."""
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            f"identity.describe_identity({app_slug=}) not supported on this driver",
        )

    def list_owned_roles(self) -> list[str]:
        """Enumerate the identity-role names this driver owns on behalf of
        the platform (tagged ``astrolift.io/managed-by=platform``), for the
        orphan-detection scan (#995).

        Returns role *names* (not ARNs) so the reaper can diff them against
        the forward mapping ``{workload_identity_role_name(app)}`` for live
        apps — a role with no live owner is an orphan. Default raises
        :class:`UnsupportedOperationError` so non-implementing clouds report
        a "not supported" section rather than crashing the whole scan."""
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "identity.list_owned_roles not supported on this driver",
        )
