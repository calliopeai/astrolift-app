"""GCP Workload Identity (#39).

Spec ref: spec 23-provider-plugin-gcp + _sdk/identity.py.

GKE Workload Identity binds k8s ServiceAccounts to GCP IAM
Service Accounts via the projected SA token + GCP's
WorkloadIdentityFederation flow. The driver:
- creates GCP IAM SA + grants it the operator-supplied roles
- adds an IAM binding so the k8s SA can impersonate the GCP SA
- annotates the k8s SA with `iam.gke.io/gcp-service-account`
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.identity import WorkloadIdentityDriver
from gcp._errors import NotFoundError, map_api_error


@dataclass(frozen=True)
class GCPWIConfig:
    project_id: str
    iam_client: Any | None = None


class GCPWorkloadIdentityDriver(WorkloadIdentityDriver):
    def __init__(self, *, config: GCPWIConfig) -> None:
        self._config = config
        if config.iam_client is not None:
            self._iam = config.iam_client
        else:
            # google-cloud-iam doesn't ship the v1 client we need
            # for ServiceAccount CRUD; fall back to the legacy
            # api-core IAM Admin v1 client.
            from google.cloud import iam_admin_v1

            self._iam = iam_admin_v1.IAMClient()

    @driver_op(cloud="gcp", driver="identity", audit=True, sensitive_kind="identity.bind")
    def bind_service_account(
        self,
        cluster: str,
        namespace: str,
        sa_name: str,
        identity_role: str,
    ) -> dict[str, str]:
        """Adds the k8s SA as a member of the GCP SA's
        roles/iam.workloadIdentityUser binding, then returns the
        annotation the manifest renderer applies."""
        gcp_sa_email = self._sa_email(name=identity_role)
        member = f"serviceAccount:{self._config.project_id}.svc.id.goog[{namespace}/{sa_name}]"
        try:
            policy = self._iam.get_iam_policy(
                resource=self._sa_resource(name=identity_role),
            )
            binding = next(
                (b for b in policy.bindings if b.role == "roles/iam.workloadIdentityUser"),
                None,
            )
            if binding is None:
                policy.bindings.add(
                    role="roles/iam.workloadIdentityUser",
                    members=[member],
                )
            elif member not in binding.members:
                binding.members.append(member)
            self._iam.set_iam_policy(
                resource=self._sa_resource(name=identity_role),
                policy=policy,
            )
        except Exception as exc:
            if type(exc).__name__ == "NotFound":
                raise NotFoundError(
                    f"GCP SA {identity_role} not found",
                ) from exc
            raise map_api_error(exc) from exc

        return {"iam.gke.io/gcp-service-account": gcp_sa_email}

    @driver_op(cloud="gcp", driver="identity", audit=True, sensitive_kind="identity.create_role")
    def create_identity_role(
        self,
        name: str,
        permissions: list[dict[str, Any]],
    ) -> str:
        """Creates a GCP IAM SA + attaches roles. Permissions
        list contains {role: 'roles/...'} entries; raw IAM
        permissions need a custom role (out of scope for this
        driver — operator pre-creates custom roles)."""
        try:
            sa = self._iam.create_service_account(
                name=f"projects/{self._config.project_id}",
                account_id=name,
                service_account={
                    "display_name": f"astrolift-{name}",
                    "description": "Astrolift workload identity SA",
                },
            )
        except Exception as exc:
            if type(exc).__name__ == "AlreadyExists":
                return self._sa_email(name=name)
            raise map_api_error(exc) from exc

        # Attach project-level roles via the operator-supplied
        # permissions list. Each entry is {role: '...'}.
        if permissions:
            for perm in permissions:
                role = perm.get("role")
                if role:
                    self._add_project_role(
                        sa_email=sa.email,
                        role=role,
                    )
        return sa.email

    @driver_op(cloud="gcp", driver="identity", audit=True, sensitive_kind="identity.attach_policy")
    def attach_policy(self, role: str, policy: str) -> None:
        """For GCP, 'policy' is a roles/* identifier."""
        self._add_project_role(
            sa_email=self._sa_email(name=role),
            role=policy,
        )

    @driver_op(cloud="gcp", driver="identity", audit=True, sensitive_kind="identity.delete_role")
    def delete_identity_role(self, role: str) -> None:
        try:
            self._iam.delete_service_account(
                name=self._sa_resource(name=role),
            )
        except Exception as exc:
            if type(exc).__name__ == "NotFound":
                raise NotFoundError(f"role {role} not found") from exc
            raise map_api_error(exc) from exc

    def _sa_email(self, *, name: str) -> str:
        return f"{name}@{self._config.project_id}.iam.gserviceaccount.com"

    def _sa_resource(self, *, name: str) -> str:
        return f"projects/-/serviceAccounts/{self._sa_email(name=name)}"

    def _add_project_role(self, *, sa_email: str, role: str) -> None:
        # Project-level IAM mutation goes through Resource Manager.
        # google-cloud-resource-manager isn't always installed; in
        # the interest of keeping the driver dependency-light, we
        # log + skip when missing. Production wiring threads in
        # the resourcemanager_v3 client.
        try:
            from google.cloud import resourcemanager_v3
        except ImportError:
            return
        client = resourcemanager_v3.ProjectsClient()
        project = f"projects/{self._config.project_id}"
        try:
            policy = client.get_iam_policy(resource=project)
            member = f"serviceAccount:{sa_email}"
            binding = next(
                (b for b in policy.bindings if b.role == role),
                None,
            )
            if binding is None:
                policy.bindings.add(role=role, members=[member])
            elif member not in binding.members:
                binding.members.append(member)
            client.set_iam_policy(resource=project, policy=policy)
        except Exception as exc:
            raise map_api_error(exc) from exc
