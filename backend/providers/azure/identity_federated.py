"""Azure Workload Identity / Federated Credentials driver (#45).

AKS Workload Identity binds a k8s ServiceAccount to an Azure AD
Application via federated credentials. Driver flow:
- create_identity_role: creates the AAD Application + Service
  Principal pair (mirrors AWS IRSA where 'role' == IAM role)
- bind_service_account: adds a federated credential pointing the
  k8s SA's OIDC issuer at the AAD App + annotates the k8s SA with
  azure.workload.identity/client-id
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.azure_tags import serialize_azure_arm_tags
from _sdk.identity import WorkloadIdentityDriver
from azure._errors import NotFoundError, map_api_error


@dataclass(frozen=True)
class FederatedIdentityConfig:
    tenant_id: str
    subscription_id: str
    resource_group: str
    cluster_oidc_issuer: str
    """The AKS cluster's `oidcIssuerProfile.issuerUrl`. Required for
    federated credentials to validate the projected SA token."""

    msi_client: Any | None = None
    """ManagedServiceIdentityClient — injected for tests."""

    graph_client: Any | None = None
    """Microsoft Graph client for AAD Application CRUD. Injected
    for tests; production uses MSAL-backed graph SDK."""


class AzureFederatedIdentityDriver(WorkloadIdentityDriver):
    def __init__(self, *, config: FederatedIdentityConfig) -> None:
        self._config = config
        self._msi = config.msi_client
        self._graph = config.graph_client

    @driver_op(cloud="azure", driver="identity", audit=True, sensitive_kind="identity.bind")
    def bind_service_account(
        self,
        cluster: str,
        namespace: str,
        sa_name: str,
        identity_role: str,
    ) -> dict[str, str]:
        """Adds a federated credential and returns the SA annotation
        AKS Workload Identity expects."""
        if self._msi is None:
            raise RuntimeError(
                "msi_client required to bind federated credentials",
            )
        subject = f"system:serviceaccount:{namespace}:{sa_name}"
        try:
            self._msi.federated_identity_credentials.create_or_update(
                resource_group_name=self._config.resource_group,
                resource_name=identity_role,
                federated_identity_credential_resource_name=(f"astrolift-{namespace}-{sa_name}"),
                parameters={
                    "properties": {
                        "issuer": self._config.cluster_oidc_issuer,
                        "subject": subject,
                        "audiences": ["api://AzureADTokenExchange"],
                    },
                },
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                raise NotFoundError(
                    f"managed identity {identity_role} not found",
                ) from exc
            raise map_api_error(exc) from exc

        client_id = self._lookup_client_id(name=identity_role)
        return {
            "azure.workload.identity/client-id": client_id,
            "azure.workload.identity/tenant-id": self._config.tenant_id,
        }

    @driver_op(cloud="azure", driver="identity", audit=True, sensitive_kind="identity.create_role")
    def create_identity_role(
        self,
        name: str,
        permissions: list[dict[str, Any]],
    ) -> str:
        """Creates a User-Assigned Managed Identity. permissions
        list contains {role_definition_id} entries for RBAC role
        assignments at scope (resource group / subscription)."""
        if self._msi is None:
            raise RuntimeError(
                "msi_client required to create managed identity",
            )
        try:
            identity = self._msi.user_assigned_identities.create_or_update(
                resource_group_name=self._config.resource_group,
                resource_name=name,
                parameters={
                    "location": "global",
                    "tags": serialize_azure_arm_tags(
                        {"astrolift.io/managed-by": "platform"},
                    ),
                },
            )
        except Exception as exc:
            raise map_api_error(exc) from exc
        return getattr(identity, "client_id", "") or ""

    @driver_op(cloud="azure", driver="identity", audit=True, sensitive_kind="identity.attach_policy")
    def attach_policy(self, role: str, policy: str) -> None:
        """For Azure, 'policy' is a roleDefinitionId. The actual
        attachment is a Microsoft.Authorization/roleAssignments
        write at the appropriate scope; out of scope here without
        an authorization client. Operators wire this up via Bicep
        templates or terraform that the install workflow renders."""
        # No-op: scope + role-assignment management belongs in the
        # operator's deployment definition, not the runtime driver.
        return None

    @driver_op(cloud="azure", driver="identity", audit=True, sensitive_kind="identity.delete_role")
    def delete_identity_role(self, role: str) -> None:
        if self._msi is None:
            raise RuntimeError(
                "msi_client required to delete managed identity",
            )
        try:
            self._msi.user_assigned_identities.delete(
                resource_group_name=self._config.resource_group,
                resource_name=role,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                raise NotFoundError(f"role {role} not found") from exc
            raise map_api_error(exc) from exc

    def _lookup_client_id(self, *, name: str) -> str:
        if self._msi is None:
            return ""
        try:
            identity = self._msi.user_assigned_identities.get(
                resource_group_name=self._config.resource_group,
                resource_name=name,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                raise NotFoundError(
                    f"managed identity {name} not found",
                ) from exc
            raise map_api_error(exc) from exc
        return getattr(identity, "client_id", "") or ""
