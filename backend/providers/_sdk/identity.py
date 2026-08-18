"""WorkloadIdentityDriver protocol -- bind Kubernetes ServiceAccounts to cloud identities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

GRANT_PENDING = "pending"
"""The assignment is declared and attempted but not confirmed by the cloud.

Normal for the seconds an ARM role assignment takes to settle; not an error.
"""

GRANT_APPLIED = "applied"
"""The cloud confirmed the assignment exists and expresses this grant."""

GRANT_FAILED = "failed"
"""The attempt was rejected. ``reason`` carries the cloud's answer."""


@dataclass(frozen=True)
class GrantAssignment:
    """One authorization the workload identity must hold, and its state.

    A federated credential proves who a pod is; a grant assignment is the
    separate object that says what it may touch, and it can lag or fail on
    its own. Reporting a binding ready while one of these is unapplied is
    the failure #1367 exists to end, so every assignment carries its own
    state and, when it is not applied, the reason.
    """

    role_definition_id: str
    role_name: str
    scope: str
    assignment_name: str
    """Provider-side stable name of the assignment; empty when the attempt
    never reached the point of deriving one."""

    state: str
    """``pending`` | ``applied`` | ``failed``."""

    reason: str = ""


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

    def grant_assignments(self) -> list[GrantAssignment]:
        """Per-assignment state left by the last ``create_identity_role``.

        Empty by default, and that is the honest answer on AWS and GCP: the
        grants there *are* the identity's own policy document / role list,
        written by the same call that creates the identity, so there is no
        second object that can be pending or fail on its own. Azure writes
        one ``Microsoft.Authorization/roleAssignments`` per grant and
        overrides this so the control plane can persist their state (#1367).
        """
        return []

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
