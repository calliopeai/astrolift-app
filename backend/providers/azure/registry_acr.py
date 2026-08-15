"""Azure Container Registry ImageRegistryDriver (#46).

ACR is name-scoped per registry (e.g. acmeprod.azurecr.io). A single
ACR registry hosts many repository paths. The driver:
- references a pre-created registry by name
- relies on the registry's repository auto-creation (push creates path)
- emits AKS-friendly attached-identity pull (no Secret needed when
  cluster is attached via `az aks update --attach-acr`)
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from _sdk import UnsupportedOperationError
from _sdk._telemetry import driver_op
from _sdk.azure_tags import serialize_azure_arm_tags
from _sdk.registry import CiPushRole, ImageRegistryDriver, Repo, SecretSpec, Tag
from azure._errors import NotFoundError, map_api_error

# Built-in Azure RBAC role `AcrPush` (ARM-fixed GUID); referenced by ID
# rather than display name because the ID is stable across tenants while
# display names can drift.
_ACR_PUSH_ROLE_DEFINITION_ID = "8311e382-0749-4cb8-b61a-304f252e45ec"

# GitHub Actions' OIDC issuer — embedded verbatim in every Federated
# Identity Credential the driver creates.
_GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"

# The audience Azure's STS expects on the GitHub OIDC token at
# AssumeRoleWithWebIdentity time. Fixed string per Azure AD docs.
_AZURE_AD_TOKEN_EXCHANGE_AUDIENCE = "api://AzureADTokenExchange"


@dataclass(frozen=True)
class ACRConfig:
    subscription_id: str
    resource_group: str
    registry_name: str
    """ACR registry name (without the .azurecr.io suffix)."""

    location: str = "eastus"
    sku: str = "Standard"
    admin_enabled: bool = False
    """Admin auth is brittle; prefer Workload Identity / managed
    identity attachment. Default off."""

    immutable_tags: bool = True
    client: Any | None = None
    """ContainerRegistryManagementClient — injected for tests."""

    tenant_id: str | None = None
    """Azure AD tenant the FIC binds against. Required for
    ``ensure_ci_push_role``; embedded verbatim in the returned
    ``role_ref`` so the CI workflow can pass it to
    ``azure/login@v2`` without a separate lookup. Optional on the
    config so non-CI driver flows don't need it set."""

    msi_client: Any | None = None
    """ManagedServiceIdentityClient — injected for tests; production
    builds a real one lazily inside ``ensure_ci_push_role``."""

    authz_client: Any | None = None
    """AuthorizationManagementClient — injected for tests; used to
    create the AcrPush role assignment scoped to this registry."""


class ACRDriver(ImageRegistryDriver):
    def __init__(self, *, config: ACRConfig) -> None:
        self._config = config
        if config.client is not None:
            self._client = config.client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.containerregistry import (
                ContainerRegistryManagementClient,
            )

            self._client = ContainerRegistryManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )
        # MSI + Authorization clients are only needed by
        # ``ensure_ci_push_role``; created lazily to avoid forcing
        # operators to install the extra packages when their plugin
        # config wires CI push another way.
        self._msi = config.msi_client
        self._authz = config.authz_client

    @property
    def login_server(self) -> str:
        return f"{self._config.registry_name}.azurecr.io"

    @driver_op(cloud="azure", driver="registry")
    def ensure_repo(self, name: str) -> Repo:
        """ACR registries auto-create repositories on first push, so
        ensure_repo verifies the registry exists + returns the
        per-app URI. The registry itself is operator-provisioned via
        the Astrolift install workflow."""
        try:
            self._client.registries.get(
                resource_group_name=self._config.resource_group,
                registry_name=self._config.registry_name,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                raise NotFoundError(
                    f"ACR registry {self._config.registry_name} not found in {self._config.resource_group}",
                ) from exc
            raise map_api_error(exc) from exc

        return Repo(
            name=name,
            uri=f"{self.login_server}/{name}",
        )

    @driver_op(cloud="azure", driver="registry", audit=True, sensitive_kind="registry.delete")
    def delete_repo(self, name: str, *, archive: bool = True) -> None:
        """ACR delete-repository is a per-repo operation via the data
        plane (ContainerRegistryClient, not the management client).
        archive=True is a no-op (path stays; access via RBAC)."""
        if archive:
            return
        # #614 -- previously raised bare NotImplementedError; upgraded so
        # the resolver layer can map to a "not supported on this backend"
        # response distinct from a partial-stub bug.
        raise UnsupportedOperationError(
            f"registry.delete_repo({name=}, archive=False) not supported "
            "on Azure ACR via this driver -- requires data-plane "
            "ContainerRegistryClient; out of scope",
        )

    @driver_op(cloud="azure", driver="registry", audit=True, sensitive_kind="registry.get_pull_secret")
    def get_pull_secret(self, cluster: str, namespace: str) -> SecretSpec:
        """For AKS clusters attached via `az aks update --attach-acr`,
        pull auth flows through the kubelet's managed identity — no
        Secret needed. This returns a marker Secret so callers wired
        for the imagePullSecrets pattern stay consistent across clouds."""
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": "astrolift-acr-credentials",
                "namespace": namespace,
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/credential-source": "acr",
                },
                "annotations": {
                    "astrolift.io/note": (
                        "AKS clusters use --attach-acr managed-"
                        "identity for pull auth — no Secret needed. "
                        "This Secret is a marker; non-AKS pulls "
                        "require operator-supplied SP credentials."
                    ),
                },
            },
            "type": "kubernetes.io/dockerconfigjson",
            "data": {
                ".dockerconfigjson": base64.b64encode(
                    json.dumps({"auths": {}}).encode(),
                ).decode(),
            },
        }

    @driver_op(cloud="azure", driver="registry")
    def push(self, local_image: str, repo: str, tag: str) -> str:
        if not local_image:
            raise ValueError("local_image is required")
        ensured = self.ensure_repo(repo)
        return f"{ensured.uri}:{tag}"

    @driver_op(cloud="azure", driver="registry")
    def list_tags(self, repo: str) -> list[Tag]:
        # ACR's list_tags requires the data-plane client; mirroring
        # AWS pattern, expose minimal metadata.
        try:
            response = self._client.list_tags(
                resource_group_name=self._config.resource_group,
                registry_name=self._config.registry_name,
                repository=repo,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                raise NotFoundError(f"repo {repo} not found") from exc
            raise map_api_error(exc) from exc

        out: list[Tag] = []
        for item in response:
            out.append(
                Tag(
                    name=getattr(item, "name", "") or "",
                    digest=getattr(item, "digest", "") or "",
                    pushed_at=getattr(item, "last_updated", None),
                )
            )
        return out

    @driver_op(cloud="azure", driver="registry", audit=True, sensitive_kind="registry.create_ci_push_role")
    def ensure_ci_push_role(
        self,
        *,
        repo: str,
        scm_provider: str,
        scm_repo_full_name: str,
    ) -> CiPushRole:
        """Provision (or refresh) a User-Assigned Managed Identity that
        GitHub Actions can assume via OIDC + push to this ACR registry.

        Three Azure resources land idempotently:

        1. A User-Assigned Managed Identity named
           ``astrolift-<sanitized-repo>-acr-push``.
        2. An ``AcrPush`` role assignment scoping that MI to the
           registry resource (skipped if an equivalent assignment
           already exists).
        3. A Federated Identity Credential on the MI naming GitHub as
           the trusted OIDC issuer + the SCM repo as the trusted
           subject (``repo:<owner>/<name>:*``).

        Returns a ``CiPushRole`` whose ``role_ref`` is a JSON blob with
        ``client_id`` / ``tenant_id`` / ``subscription_id`` —
        ``azure/login@v2`` consumes those three fields directly, no
        separate lookup needed from the CI workflow.

        Pre-req: an operator-supplied ``tenant_id`` on the config (or
        the MI returns a populated ``tenant_id`` we can fall back to).
        """
        if scm_provider != "github":
            raise UnsupportedOperationError(
                f"ACRDriver only supports scm_provider='github' today; got {scm_provider!r}",
            )
        if "/" not in scm_repo_full_name:
            raise ValueError(
                f"scm_repo_full_name must be 'owner/repo'; got {scm_repo_full_name!r}",
            )

        if self._msi is None:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.msi import ManagedServiceIdentityClient

            self._msi = ManagedServiceIdentityClient(
                credential=DefaultAzureCredential(),
                subscription_id=self._config.subscription_id,
            )
        if self._authz is None:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.authorization import AuthorizationManagementClient

            self._authz = AuthorizationManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=self._config.subscription_id,
            )

        mi_name = _mi_name_for_repo(repo)
        registry_scope = (
            f"/subscriptions/{self._config.subscription_id}"
            f"/resourceGroups/{self._config.resource_group}"
            "/providers/Microsoft.ContainerRegistry/registries"
            f"/{self._config.registry_name}"
        )
        role_definition_id = (
            f"/subscriptions/{self._config.subscription_id}"
            f"/providers/Microsoft.Authorization/roleDefinitions"
            f"/{_ACR_PUSH_ROLE_DEFINITION_ID}"
        )

        # 1) Create-or-get the Managed Identity. ``create_or_update``
        # is idempotent on the Azure side — a second call on the same
        # name returns the existing identity with its principal_id +
        # client_id populated.
        try:
            identity = self._msi.user_assigned_identities.create_or_update(
                resource_group_name=self._config.resource_group,
                resource_name=mi_name,
                parameters={
                    "location": self._config.location,
                    "tags": serialize_azure_arm_tags(
                        {
                            "astrolift.io/managed-by": "platform",
                            "astrolift.io/scm-repo": scm_repo_full_name,
                            "astrolift.io/registry-repo": repo,
                        },
                    ),
                },
            )
        except Exception as exc:
            raise map_api_error(exc) from exc

        principal_id = getattr(identity, "principal_id", "") or ""
        client_id = getattr(identity, "client_id", "") or ""
        tenant_id = self._config.tenant_id or getattr(identity, "tenant_id", "") or ""
        if not tenant_id:
            raise ValueError(
                "ACRConfig.tenant_id must be set (or the MI must return a tenant_id) to construct a CI push role ref",
            )

        # 2) Idempotent AcrPush role assignment. Azure's role-assignment
        # API rejects duplicates with RoleAssignmentExists; rather than
        # catching that we list-then-skip so the assignment lifecycle
        # stays observable (e.g. a future "rotate" path).
        if not self._role_assignment_exists(
            scope=registry_scope,
            principal_id=principal_id,
            role_definition_id=role_definition_id,
        ):
            assignment_name = _role_assignment_name(
                principal_id=principal_id,
                role_definition_id=role_definition_id,
                scope=registry_scope,
            )
            try:
                self._authz.role_assignments.create(
                    scope=registry_scope,
                    role_assignment_name=assignment_name,
                    parameters={
                        "properties": {
                            "roleDefinitionId": role_definition_id,
                            "principalId": principal_id,
                            "principalType": "ServicePrincipal",
                        },
                    },
                )
            except Exception as exc:
                # Race against a concurrent driver run: another caller
                # may have created the assignment between our list +
                # our create. Treat "already exists" as success rather
                # than failing the whole CI bootstrap.
                if type(exc).__name__ != "ResourceExistsError":
                    raise map_api_error(exc) from exc

        # 3) Federated Identity Credential. ``create_or_update`` is
        # idempotent — the FIC name is fixed (``github-push``) so a
        # second call refreshes the issuer/subject/audience in place
        # when the SCM repo or scope changes.
        try:
            self._msi.federated_identity_credentials.create_or_update(
                resource_group_name=self._config.resource_group,
                resource_name=mi_name,
                federated_identity_credential_resource_name="github-push",
                parameters={
                    "properties": {
                        "issuer": _GITHUB_OIDC_ISSUER,
                        "subject": f"repo:{scm_repo_full_name}:*",
                        "audiences": [_AZURE_AD_TOKEN_EXCHANGE_AUDIENCE],
                    },
                },
            )
        except Exception as exc:
            raise map_api_error(exc) from exc

        return CiPushRole(
            role_ref=json.dumps(
                {
                    "client_id": client_id,
                    "tenant_id": tenant_id,
                    "subscription_id": self._config.subscription_id,
                },
                sort_keys=True,
            ),
            scm_provider="github",
        )

    def _role_assignment_exists(
        self,
        *,
        scope: str,
        principal_id: str,
        role_definition_id: str,
    ) -> bool:
        """List existing assignments at the registry scope + check for
        a match on principal + role-definition. Returns False on
        list-errors so the caller's create path runs (Azure will
        reject the duplicate with ResourceExistsError if our list was
        stale — that branch is handled at the call site)."""
        if self._authz is None:
            return False
        try:
            existing = self._authz.role_assignments.list_for_scope(scope=scope)
        except Exception:
            return False
        for assignment in existing:
            props = getattr(assignment, "properties", None) or assignment
            assigned_principal = getattr(props, "principal_id", None)
            assigned_role = getattr(props, "role_definition_id", None)
            if assigned_principal == principal_id and assigned_role == role_definition_id:
                return True
        return False


# ---- module-private helpers ------------------------------------------------


def _mi_name_for_repo(repo: str) -> str:
    """Build the per-repo Managed Identity name.

    Azure MI names accept ``0-9 A-Z a-z _ -`` only (128 char max). Repo
    slugs in the platform may contain ``/`` (e.g. ``acme/api``) which
    must be normalized — we replace any non-conforming character with
    ``-`` then truncate to fit the ``astrolift-<repo[:20]>-acr-push``
    template the spec mandates."""
    safe = re.sub(r"[^0-9A-Za-z-]", "-", repo)[:20]
    return f"astrolift-{safe}-acr-push"[:128]


def _role_assignment_name(
    *,
    principal_id: str,
    role_definition_id: str,
    scope: str,
) -> str:
    """Generate a deterministic UUID-shaped name for the role
    assignment so concurrent driver runs converge on the same name +
    Azure's idempotency on the resource name kicks in."""
    namespace = uuid.NAMESPACE_URL
    payload = f"{scope}|{role_definition_id}|{principal_id}"
    return str(uuid.uuid5(namespace, payload))
