"""Projected SA token WorkloadIdentityDriver (#51 + #11).

Spec ref: spec 23-provider-plugin-k8s-native + _sdk/identity.py.

Vanilla k8s clusters don't have an external IAM (no IRSA / GKE WI
/ AAD federation). The driver returns an annotation map that
configures the SA's projected-token volume — apps read it from
``/var/run/secrets/tokens/<audience>`` and present it to whatever
service consumes JWTs (Vault, internal IdP, etc.).

This is intentionally minimal: no role creation, no policy
attachment. Identity is the cluster's own ServiceAccount + the
ESO/Vault auth flow downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.identity import WorkloadIdentityDriver


@dataclass(frozen=True)
class ProjectedSaTokenConfig:
    audience: str = "vault"
    """JWT audience the projected token carries. Downstream
    consumers (Vault, custom IdPs) verify this claim."""

    expiration_seconds: int = 3600
    """Token TTL. kubelet auto-rotates before expiry."""

    role_path: str = "astrolift-roles"
    """Logical 'role' namespace within the platform's role
    catalog. There's no real IAM here — these names are for
    operator-side bookkeeping + downstream policy lookup."""

    role_catalog: dict[str, list[dict[str, Any]]] = field(
        default_factory=dict,
    )
    """In-memory role catalog: name → policy statement list.
    Real production would persist this; for the vanilla-k8s
    driver it's just a mapping."""


class ProjectedSaTokenDriver(WorkloadIdentityDriver):
    def __init__(
        self,
        *,
        config: ProjectedSaTokenConfig | None = None,
    ) -> None:
        self._config = config or ProjectedSaTokenConfig()
        self._roles: dict[str, list[dict[str, Any]]] = dict(
            self._config.role_catalog,
        )

    @driver_op(cloud="k8s_native", driver="identity", audit=True, sensitive_kind="identity.bind")
    def bind_service_account(
        self,
        cluster: str,
        namespace: str,
        sa_name: str,
        identity_role: str,
    ) -> dict[str, str]:
        """Returns annotations the platform applies to the SA.

        The actual token projection happens at pod-spec time via
        a projected-volume mount; the annotations here let the
        manifest renderer know what audience/expiration to write.
        """
        if identity_role not in self._roles:
            raise KeyError(f"role {identity_role} not registered")
        return {
            "astrolift.io/identity-role": identity_role,
            "astrolift.io/token-audience": self._config.audience,
            "astrolift.io/token-expiration-seconds": str(
                self._config.expiration_seconds,
            ),
        }

    @driver_op(cloud="k8s_native", driver="identity", audit=True, sensitive_kind="identity.create_role")
    def create_identity_role(
        self,
        name: str,
        permissions: list[dict[str, Any]],
    ) -> str:
        """No-op-style: store in the in-memory catalog. Returns
        a logical 'arn' that's just the role path + name."""
        self._roles[name] = list(permissions)
        return f"{self._config.role_path}/{name}"

    @driver_op(cloud="k8s_native", driver="identity", audit=True, sensitive_kind="identity.attach_policy")
    def attach_policy(self, role: str, policy: str) -> None:
        if role not in self._roles:
            raise KeyError(f"role {role} not registered")
        self._roles[role].append({"managed_policy": policy})

    @driver_op(cloud="k8s_native", driver="identity", audit=True, sensitive_kind="identity.delete_role")
    def delete_identity_role(self, role: str) -> None:
        if role not in self._roles:
            raise KeyError(f"role {role} not registered")
        del self._roles[role]
