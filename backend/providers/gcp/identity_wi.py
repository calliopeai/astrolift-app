"""GCP Workload Identity for GKE workloads.

The platform owns a Google service account per logical workload identity,
reconciles its project IAM roles, and authorizes the matching Kubernetes
ServiceAccount to impersonate it through GKE Workload Identity Federation.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.identity import WorkloadIdentityDriver
from gcp._errors import NotFoundError, map_api_error

_ACCOUNT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_ROLE_RE = re.compile(
    r"^(?:roles/[A-Za-z0-9_.]+|(?:projects|organizations)/[^/]+/roles/[A-Za-z0-9_.]+)$",
)


def service_account_id_for(name: str) -> str:
    """Return a deterministic GCP service-account id for a logical role.

    Kubernetes and AWS allow longer names than Google IAM's 6-30 character
    account-id limit. Preserve already-valid names; otherwise retain a readable
    prefix and add a hash so truncation and sanitization cannot collide.
    """
    normalized = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    if normalized and not normalized[0].isalpha():
        normalized = f"a-{normalized}"
    normalized = re.sub(r"-+", "-", normalized).rstrip("-")
    if normalized == name and _ACCOUNT_ID_RE.fullmatch(normalized):
        return normalized

    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    prefix = (normalized or "astrolift")[:19].rstrip("-") or "astrolift"
    candidate = f"{prefix}-{digest}"
    if not candidate[0].isalpha():
        candidate = f"a-{candidate}"[:30].rstrip("-")
    if len(candidate) < 6:
        candidate = f"{candidate}-{digest}"[:30].rstrip("-")
    return candidate


def service_account_email_for(name: str, project_id: str) -> str:
    return f"{service_account_id_for(name)}@{project_id}.iam.gserviceaccount.com"


class _ResourceManagerIAMClient:
    """Minimal Cloud Resource Manager IAM REST adapter.

    Resource Manager's generated library is not part of the GCP provider
    dependency set. Google Auth's AuthorizedSession is already required by
    every GCP deployment and keeps this control-plane path small and explicit.
    """

    def __init__(self, *, session: Any | None = None) -> None:
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            session = AuthorizedSession(credentials)
        self._session = session

    def get_project_iam_policy(self, *, project_id: str) -> dict[str, Any]:
        response = self._session.post(
            f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}:getIamPolicy",
            json={"options": {"requestedPolicyVersion": 3}},
            timeout=30,
        )
        response.raise_for_status()
        return dict(response.json())

    def set_project_iam_policy(
        self,
        *,
        project_id: str,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        response = self._session.post(
            f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}:setIamPolicy",
            json={"policy": policy, "updateMask": "bindings,etag,version"},
            timeout=30,
        )
        response.raise_for_status()
        return dict(response.json())


@dataclass(frozen=True)
class GCPWIConfig:
    project_id: str
    iam_client: Any | None = None
    project_iam_client: Any | None = None


class GCPWorkloadIdentityDriver(WorkloadIdentityDriver):
    def __init__(self, *, config: GCPWIConfig) -> None:
        self._config = config
        if config.iam_client is not None:
            self._iam = config.iam_client
        else:
            from google.cloud import iam_admin_v1

            self._iam = iam_admin_v1.IAMClient()
        self._project_iam = config.project_iam_client

    @driver_op(cloud="gcp", driver="identity", audit=True, sensitive_kind="identity.bind")
    def bind_service_account(
        self,
        cluster: str,
        namespace: str,
        sa_name: str,
        identity_role: str,
    ) -> dict[str, str]:
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
            changed = False
            if binding is None:
                policy.bindings.add(
                    role="roles/iam.workloadIdentityUser",
                    members=[member],
                )
                changed = True
            elif member not in binding.members:
                binding.members.append(member)
                changed = True
            if changed:
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
        roles = self._validated_roles(permissions)
        account_id = service_account_id_for(name)
        email = self._sa_email(name=name)
        try:
            sa = self._iam.create_service_account(
                name=f"projects/{self._config.project_id}",
                account_id=account_id,
                service_account={
                    "display_name": f"Astrolift {name}"[:100],
                    "description": "Astrolift workload identity service account",
                },
            )
            email = sa.email
        except Exception as exc:
            if type(exc).__name__ != "AlreadyExists":
                raise map_api_error(exc) from exc

        for role in roles:
            self._add_project_role(sa_email=email, role=role)
        return email

    @driver_op(cloud="gcp", driver="identity", audit=True, sensitive_kind="identity.attach_policy")
    def attach_policy(self, role: str, policy: str) -> None:
        validated = self._validated_roles([{"role": policy}])
        self._add_project_role(
            sa_email=self._sa_email(name=role),
            role=validated[0],
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
        return service_account_email_for(name, self._config.project_id)

    def _sa_resource(self, *, name: str) -> str:
        return f"projects/-/serviceAccounts/{self._sa_email(name=name)}"

    @staticmethod
    def _validated_roles(permissions: list[dict[str, Any]]) -> list[str]:
        roles: list[str] = []
        for permission in permissions:
            resource = permission.get("resource")
            if permission.get("role") == "roles/aiplatform.user" or (
                isinstance(resource, str) and "/endpoints/" in resource
            ):
                raise ValueError("Endpoint grants require owned resource-scoped GCP reconciliation")
            role = permission.get("role")
            if not isinstance(role, str) or not _ROLE_RE.fullmatch(role):
                raise ValueError(
                    "GCP workload-identity permissions must contain a valid "
                    f"predefined or custom IAM role; received {role!r}",
                )
            if role not in roles:
                roles.append(role)
        return roles

    def _add_project_role(self, *, sa_email: str, role: str) -> None:
        try:
            if self._project_iam is None:
                self._project_iam = _ResourceManagerIAMClient()
            policy = self._project_iam.get_project_iam_policy(
                project_id=self._config.project_id,
            )
            bindings = list(policy.get("bindings") or [])
            member = f"serviceAccount:{sa_email}"
            binding = next(
                (item for item in bindings if item.get("role") == role and not item.get("condition")),
                None,
            )
            if binding is not None and member in (binding.get("members") or []):
                return
            if binding is None:
                bindings.append({"role": role, "members": [member]})
            else:
                binding["members"] = [*(binding.get("members") or []), member]
            updated = dict(policy)
            updated["bindings"] = bindings
            updated["version"] = max(int(updated.get("version") or 1), 3)
            self._project_iam.set_project_iam_policy(
                project_id=self._config.project_id,
                policy=updated,
            )
        except Exception as exc:
            raise map_api_error(exc) from exc


__all__ = [
    "GCPWIConfig",
    "GCPWorkloadIdentityDriver",
    "service_account_email_for",
    "service_account_id_for",
]
