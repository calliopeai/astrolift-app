"""WorkloadIdentityDriver protocol -- bind Kubernetes ServiceAccounts to cloud identities."""

from __future__ import annotations

from typing import Any, Protocol


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
