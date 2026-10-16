"""GCP Workload Identity for GKE workloads.

The platform owns a Google service account per logical workload identity,
reconciles its project IAM roles, and authorizes the matching Kubernetes
ServiceAccount to impersonate it through GKE Workload Identity Federation.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from _sdk._telemetry import driver_op
from _sdk.identity import WorkloadIdentityDriver
from gcp._errors import NotFoundError, map_api_error

if TYPE_CHECKING:
    from collections.abc import Collection

_ACCOUNT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_ROLE_RE = re.compile(
    r"^(?:roles/[A-Za-z0-9_.]+|(?:projects|organizations)/[^/]+/roles/[A-Za-z0-9_.]+)$",
)
_LOG_VIEW_RE = re.compile(r"^projects/([^/]+)/locations/[^/]+/buckets/[^/]+/views/[^/]+$")
_VIEW_ACCESSOR = "roles/logging.viewAccessor"
#: Observability roles a binding once granted on the whole project (#2099). In
#: a project several orgs share they read every tenant's logs and metrics, so
#: one no binding still asks for is withdrawn from the identity on reconcile.
_PROJECT_WIDE_OBSERVABILITY_ROLES = (
    "roles/logging.viewer",
    "roles/logging.logWriter",
    "roles/monitoring.viewer",
    "roles/monitoring.metricWriter",
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

    def get_log_view_iam_policy(self, *, view: str) -> dict[str, Any]:
        response = self._session.post(
            f"https://logging.googleapis.com/v2/{view}:getIamPolicy",
            json={"options": {"requestedPolicyVersion": 3}},
            timeout=30,
        )
        response.raise_for_status()
        return dict(response.json())

    def set_log_view_iam_policy(self, *, view: str, policy: dict[str, Any]) -> dict[str, Any]:
        response = self._session.post(
            f"https://logging.googleapis.com/v2/{view}:setIamPolicy",
            json={"policy": policy},
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
        view_grants = self._log_view_grants(permissions)
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
        for view, role in view_grants:
            self._add_log_view_role(sa_email=email, view=view, role=role)
        self._withdraw_project_roles(
            sa_email=email,
            roles=[role for role in _PROJECT_WIDE_OBSERVABILITY_ROLES if role not in roles],
        )
        return email

    @driver_op(cloud="gcp", driver="identity", audit=True, sensitive_kind="identity.withdraw_log_views")
    def withdraw_log_view_grants(self, name: str, views: list[str]) -> list[str]:
        """Take the identity off the read grant on each of ``views`` (#2099).

        ``create_identity_role`` only adds view grants, so a detached Cloud
        Operations bundle stayed readable by the app it was detached from. The
        caller names the views of bundles the app is no longer bound to; only
        the unconditional ``roles/logging.viewAccessor`` binding this driver
        writes is touched, and a view that no longer exists has nothing left to
        withdraw. Returns the views the identity was taken off.
        """
        member = f"serviceAccount:{self._sa_email(name=name)}"
        withdrawn: list[str] = []
        for view in dict.fromkeys(views):
            if not self._log_view({"role": _VIEW_ACCESSOR, "resource": view}):
                raise ValueError(f"{view!r} is not a Cloud Logging view")
            client = self._project_iam_client()
            try:
                policy = client.get_log_view_iam_policy(view=view)
            except Exception as exc:
                if _not_found(exc):
                    continue
                raise map_api_error(exc) from exc
            updated = _without_member(policy, roles=(_VIEW_ACCESSOR,), member=member)
            if updated is None:
                continue
            try:
                client.set_log_view_iam_policy(view=view, policy=updated)
            except Exception as exc:
                raise map_api_error(exc) from exc
            withdrawn.append(view)
        return withdrawn

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

    def _validated_roles(self, permissions: list[dict[str, Any]]) -> list[str]:
        """The project-level roles; grants on a log view are applied on the view."""
        roles: list[str] = []
        for permission in permissions:
            resource = permission.get("resource")
            if permission.get("role") == "roles/aiplatform.user" or (
                isinstance(resource, str) and "/endpoints/" in resource
            ):
                raise ValueError("Endpoint grants require owned resource-scoped GCP reconciliation")
            if self._log_view(permission):
                continue
            role = permission.get("role")
            if not isinstance(role, str) or not _ROLE_RE.fullmatch(role):
                raise ValueError(
                    "GCP workload-identity permissions must contain a valid "
                    f"predefined or custom IAM role; received {role!r}",
                )
            if role == _VIEW_ACCESSOR:
                # On the project it reads every log view there, so every
                # tenant's logs in a shared project (#2099).
                raise ValueError(f"{_VIEW_ACCESSOR} is granted on a log view, never on the project")
            if role not in roles:
                roles.append(role)
        return roles

    def _log_view(self, permission: dict[str, Any]) -> str:
        """The log view ``permission`` is scoped to, or ``""`` for a project grant."""
        resource = permission.get("resource")
        match = _LOG_VIEW_RE.fullmatch(resource) if isinstance(resource, str) else None
        if match is None:
            return ""
        if match.group(1) != self._config.project_id:
            raise ValueError(f"log view {resource!r} is not in project {self._config.project_id!r}")
        if permission.get("role") != _VIEW_ACCESSOR:
            raise ValueError(f"a log view grants only {_VIEW_ACCESSOR}; received {permission.get('role')!r}")
        return str(resource)

    def _log_view_grants(self, permissions: list[dict[str, Any]]) -> list[tuple[str, str]]:
        grants: list[tuple[str, str]] = []
        for permission in permissions:
            view = self._log_view(permission)
            if view and (view, _VIEW_ACCESSOR) not in grants:
                grants.append((view, _VIEW_ACCESSOR))
        return grants

    def _project_iam_client(self) -> Any:
        if self._project_iam is None:
            self._project_iam = _ResourceManagerIAMClient()
        return self._project_iam

    def _add_log_view_role(self, *, sa_email: str, view: str, role: str) -> None:
        try:
            client = self._project_iam_client()
            policy = client.get_log_view_iam_policy(view=view)
            updated = _with_member(policy, role=role, member=f"serviceAccount:{sa_email}")
            if updated is not None:
                client.set_log_view_iam_policy(view=view, policy=updated)
        except Exception as exc:
            raise map_api_error(exc) from exc

    def _withdraw_project_roles(self, *, sa_email: str, roles: list[str]) -> None:
        """Remove the identity from unconditional project bindings of ``roles``.

        Idempotent: nothing is written when the identity holds none of them.
        """
        if not roles:
            return
        try:
            client = self._project_iam_client()
            policy = client.get_project_iam_policy(project_id=self._config.project_id)
            updated = _without_member(policy, roles=roles, member=f"serviceAccount:{sa_email}")
            if updated is not None:
                client.set_project_iam_policy(project_id=self._config.project_id, policy=updated)
        except Exception as exc:
            raise map_api_error(exc) from exc

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


def _with_member(policy: dict[str, Any], *, role: str, member: str) -> dict[str, Any] | None:
    """``policy`` with ``member`` in its unconditional ``role`` binding, or None when it already is."""
    bindings = [dict(item) for item in policy.get("bindings") or []]
    binding = next(
        (item for item in bindings if item.get("role") == role and not item.get("condition")),
        None,
    )
    if binding is not None and member in (binding.get("members") or []):
        return None
    if binding is None:
        bindings.append({"role": role, "members": [member]})
    else:
        binding["members"] = [*(binding.get("members") or []), member]
    updated = dict(policy)
    updated["bindings"] = bindings
    updated["version"] = max(int(updated.get("version") or 1), 3)
    return updated


def _without_member(policy: dict[str, Any], *, roles: Collection[str], member: str) -> dict[str, Any] | None:
    """``policy`` with ``member`` off its unconditional bindings of ``roles``, or None when it is on none."""
    bindings: list[dict[str, Any]] = []
    changed = False
    for item in policy.get("bindings") or []:
        members = list(item.get("members") or [])
        if item.get("role") in roles and not item.get("condition") and member in members:
            members.remove(member)
            changed = True
            if not members:
                continue
            item = {**item, "members": members}
        bindings.append(dict(item))
    if not changed:
        return None
    updated = dict(policy)
    updated["bindings"] = bindings
    updated["version"] = max(int(updated.get("version") or 1), 3)
    return updated


def _not_found(exc: Exception) -> bool:
    """A 404 from the REST adapter (``requests.HTTPError``) or a client library ``NotFound``."""
    response = getattr(exc, "response", None)
    return type(exc).__name__ == "NotFound" or getattr(response, "status_code", None) == 404


__all__ = [
    "GCPWIConfig",
    "GCPWorkloadIdentityDriver",
    "service_account_email_for",
    "service_account_id_for",
]
