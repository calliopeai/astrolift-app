"""Azure Workload Identity / Federated Credentials driver (#45).

AKS Workload Identity binds a k8s ServiceAccount to a User-Assigned Managed
Identity via federated credentials. Driver flow:
- create_identity_role: creates the UAMI (mirrors AWS IRSA where 'role' == IAM
  role) and reconciles its Azure RBAC role assignments against the grants the
  app's managed services declare
- bind_service_account: adds a federated credential pointing the k8s SA's OIDC
  issuer at the UAMI + annotates the k8s SA with
  azure.workload.identity/client-id

Authorization (#1367): a federated credential proves *who* the pod is; only a
``Microsoft.Authorization/roleAssignments`` write says what it may touch. Every
assignment this driver creates is named with a UUIDv5 derived from
``(scope, roleDefinitionId, principalId)``, which makes creation idempotent and
makes ownership decidable later: an assignment whose name equals that
derivation is ours, anything else belongs to the operator and is never touched.

Assignment *state* (#1367 follow-up): every attempt records a
:class:`GrantAssignment` before it can raise, so the control plane can persist
what actually happened per grant rather than inferring it from one all-or-
nothing exception. AAD principal replication is the normal reason an
assignment is not there yet, and it is reported as ``pending`` — a fresh
identity is settling, not broken — while anything ARM actually rejected is
``failed`` with the reason attached.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.azure_tags import serialize_azure_arm_tags
from _sdk.identity import (
    GRANT_APPLIED,
    GRANT_FAILED,
    GRANT_PENDING,
    GrantAssignment,
    WorkloadIdentityDriver,
)
from azure._errors import NotFoundError, ProviderError, map_api_error
from azure.role_catalog import (
    AZURE_BUILTIN_ROLE_IDS,
    AzureGrantContractError,
    resolve_grant_action,
    role_definition_resource_id,
    subscription_of,
    validate_arm_scope,
)

log = logging.getLogger("providers.azure.identity_federated")

_ASSIGNMENT_NAME_NAMESPACE = uuid.NAMESPACE_URL

# AAD replicates a freshly created UAMI's service principal asynchronously, so
# ARM rejects an assignment naming it until the directory catches up. That is
# eventual consistency, not a misconfiguration: the same call succeeds moments
# later without anything changing.
_REPLICATION_LAG_MARKERS = (
    "principalnotfound",
    "does not exist in the directory",
)

_SETTLE_POLL_SECONDS = 5.0

# Stamped on every assignment we create. Not an ownership proof on its own
# (descriptions are operator-writable); it exists so a human reading the portal
# knows what made the assignment, and so a foreign assignment that happens to
# collide with our derived name is recognisable as foreign.
_ASSIGNMENT_DESCRIPTION = "astrolift.io/managed-by=platform workload-identity grant"


class AzureRoleAssignmentError(ProviderError):
    """A workload-identity role assignment could not be reconciled."""


@dataclass(frozen=True)
class AzureManagedIdentity:
    """The stable handles a UAMI exposes.

    ``resource_id`` is the ARM identity resource ID and the value
    :meth:`AzureFederatedIdentityDriver.create_identity_role` returns —
    the Azure analogue of the IAM role ARN AWS returns. ``principal_id`` is
    the service-principal object ID every role assignment binds to;
    ``client_id`` is what the ServiceAccount annotation carries.
    """

    name: str
    resource_id: str
    client_id: str
    principal_id: str
    tenant_id: str


@dataclass(frozen=True)
class FederatedIdentityConfig:
    tenant_id: str
    subscription_id: str
    resource_group: str
    cluster_oidc_issuer: str
    """The AKS cluster's `oidcIssuerProfile.issuerUrl`. Required for
    federated credentials to validate the projected SA token."""

    location: str = "eastus"

    msi_client: Any | None = None
    """ManagedServiceIdentityClient — injected for tests; production builds a
    real one lazily."""

    graph_client: Any | None = None
    """Microsoft Graph client for AAD Application CRUD. Injected
    for tests; production uses MSAL-backed graph SDK."""

    authz_client: Any | None = None
    """AuthorizationManagementClient — injected for tests; production builds a
    real one lazily. Required to grant the workload any data-plane access."""

    grant_settle_seconds: float = 60.0
    """How long to keep retrying an assignment ARM refuses because the
    identity's service principal has not replicated yet. Bounded: past it the
    assignment is reported ``pending`` rather than ``failed``, because nothing
    is wrong with it — the directory is still catching up. Tests set 0 so no
    test ever sleeps."""


class AzureFederatedIdentityDriver(WorkloadIdentityDriver):
    def __init__(self, *, config: FederatedIdentityConfig) -> None:
        self._config = config
        self._msi = config.msi_client
        self._graph = config.graph_client
        self._authz = config.authz_client
        self._grant_assignments: list[GrantAssignment] = []
        self._prune_refusals: list[str] = []

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

        identity = self.describe_identity_role(identity_role)
        return {
            "azure.workload.identity/client-id": identity.client_id,
            "azure.workload.identity/tenant-id": identity.tenant_id,
        }

    @driver_op(cloud="azure", driver="identity", audit=True, sensitive_kind="identity.create_role")
    def create_identity_role(
        self,
        name: str,
        permissions: list[dict[str, Any]],
    ) -> str:
        """Create-or-update the UAMI and reconcile its role assignments.

        ``permissions`` carries the Azure shape the workload-identity activity
        produces: ``{"role_definition_id", "role_name", "scope"}`` per desired
        assignment. Returns the UAMI's ARM resource ID — the stable handle a
        later assignment or an out-of-band audit needs. ``client_id`` and
        ``principal_id`` come from :meth:`describe_identity_role`.
        """
        if self._msi is None:
            raise RuntimeError(
                "msi_client required to create managed identity",
            )
        desired = _desired_assignments(permissions)
        try:
            identity = self._msi.user_assigned_identities.create_or_update(
                resource_group_name=self._config.resource_group,
                resource_name=name,
                parameters={
                    "location": self._config.location,
                    "tags": serialize_azure_arm_tags(
                        {"astrolift.io/managed-by": "platform"},
                    ),
                },
            )
        except Exception as exc:
            raise map_api_error(exc) from exc

        record = self._identity_record(name=name, identity=identity)
        self._reconcile_role_assignments(identity=record, desired=desired)
        return record.resource_id

    @driver_op(cloud="azure", driver="identity", audit=True, sensitive_kind="identity.attach_policy")
    def attach_policy(self, role: str, policy: str) -> None:
        """Grant the UAMI one built-in role at the driver's resource group.

        Mirrors the GCP driver, where ``attach_policy`` adds a single role at
        the project scope. Resource-scoped grants come through
        :meth:`create_identity_role`, which owns the full desired set and can
        therefore also remove what is no longer declared; this entry point only
        adds.
        """
        resolved = resolve_grant_action(policy)
        guid = getattr(resolved, "role_definition_guid", None)
        if guid is None:
            raise AzureGrantContractError(
                f"attach_policy needs an assignable built-in role; {policy!r} is "
                "classified as control-plane work and grants the workload nothing",
            )
        scope = f"/subscriptions/{self._config.subscription_id}/resourceGroups/{self._config.resource_group}"
        identity = self.describe_identity_role(role)
        outcome = self._ensure_role_assignment(
            identity=identity,
            role_definition_guid=guid,
            role_name=getattr(resolved, "role_name", ""),
            scope=validate_arm_scope(scope),
        )
        self._grant_assignments = [outcome]
        if outcome.state != GRANT_APPLIED:
            # No persistence hangs off this entry point, so an unconfirmed
            # grant here would be lost rather than merely unapplied. The
            # caller retries; the reason says whether that is worth doing.
            raise AzureRoleAssignmentError(outcome.reason)

    @driver_op(cloud="azure", driver="identity", audit=True, sensitive_kind="identity.delete_role")
    def delete_identity_role(self, role: str) -> None:
        if self._msi is None:
            raise RuntimeError(
                "msi_client required to delete managed identity",
            )
        identity = self.describe_identity_role(role)
        # Strip our grants before the principal disappears: a deleted principal
        # leaves orphaned assignments behind that no later reconcile can match.
        self._reconcile_role_assignments(identity=identity, desired={})
        try:
            self._msi.user_assigned_identities.delete(
                resource_group_name=self._config.resource_group,
                resource_name=role,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                raise NotFoundError(f"role {role} not found") from exc
            raise map_api_error(exc) from exc

    @driver_op(cloud="azure", driver="identity")
    def describe_identity_role(self, name: str) -> AzureManagedIdentity:
        """Read the UAMI's stable handles (resource ID, principal, client)."""
        if self._msi is None:
            raise RuntimeError("msi_client required to read managed identity")
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
        return self._identity_record(name=name, identity=identity)

    # ---- role assignments -------------------------------------------------

    def grant_assignments(self) -> list[GrantAssignment]:
        """Per-assignment state left by the last reconcile (#1367).

        Populated even when the reconcile raises: the control plane needs to
        record *which* grant failed and why, and an exception carries only
        the first one.
        """
        return list(self._grant_assignments)

    def prune_refusals(self) -> list[str]:
        """Assignments the last reconcile refused to prune, with the reason.

        Fail-closed removal is right, but a refusal that only ever happens in
        silence is the same shape of problem as a binding that reports ready
        while unauthorized: nothing complains and the operator never learns
        that a grant they thought was revoked is still in place.
        """
        return list(self._prune_refusals)

    def _reconcile_role_assignments(
        self,
        *,
        identity: AzureManagedIdentity,
        desired: dict[tuple[str, str], str],
    ) -> None:
        """Converge the UAMI's assignments on ``desired``.

        Creates first so a re-scoped grant never leaves the workload briefly
        unauthorized, then removes the assignments we own that are no longer
        declared. Removal is the dangerous direction: an assignment is only
        removable when its name equals the UUIDv5 we derive from its own
        ``(scope, roleDefinitionId, principalId)`` *and* its description is
        ours. Anything else is treated as an operator's and left alone.

        Every attempt is recorded before anything raises. A rejected grant
        still fails the reconcile loudly — that is what #1444 bought — but it
        fails with all of the outcomes already banked, so the caller can
        persist which grant is broken instead of only that one was.
        """
        self._grant_assignments = []
        self._prune_refusals = []
        if desired and not identity.principal_id:
            raise AzureRoleAssignmentError(
                f"managed identity {identity.name} exposes no principalId; refusing to "
                "report a configured binding with no authorization behind it",
            )
        outcomes = [
            self._ensure_role_assignment(
                identity=identity,
                role_definition_guid=guid,
                role_name=role_name,
                scope=scope,
            )
            for (guid, scope), role_name in sorted(desired.items())
        ]
        self._grant_assignments = outcomes
        if identity.principal_id:
            self._prune_role_assignments(identity=identity, desired=set(desired))
        failures = [outcome for outcome in outcomes if outcome.state == GRANT_FAILED]
        if failures:
            raise AzureRoleAssignmentError("; ".join(outcome.reason for outcome in failures))

    def _ensure_role_assignment(
        self,
        *,
        identity: AzureManagedIdentity,
        role_definition_guid: str,
        role_name: str,
        scope: str,
    ) -> GrantAssignment:
        """Apply one grant and report what became of it.

        Never raises: a rejected grant is an outcome the caller has to be able
        to record. ``_reconcile_role_assignments`` turns the failures back
        into one exception once every outcome is banked.
        """
        assignment_name = ""
        if identity.principal_id:
            assignment_name = role_assignment_name(
                principal_id=identity.principal_id,
                role_definition_guid=role_definition_guid,
                scope=scope,
            )

        def outcome(state: str, reason: str = "") -> GrantAssignment:
            return GrantAssignment(
                role_definition_id=role_definition_guid,
                role_name=role_name,
                scope=scope,
                assignment_name=assignment_name,
                state=state,
                reason=reason,
            )

        if not identity.principal_id:
            return outcome(
                GRANT_FAILED,
                f"managed identity {identity.name} exposes no principalId; refusing to "
                "report a configured binding with no authorization behind it",
            )
        if subscription_of(scope).lower() != self._config.subscription_id.lower():
            return outcome(
                GRANT_FAILED,
                f"grant scope {scope!r} is outside the identity's subscription "
                f"{self._config.subscription_id!r}; the declaring driver must emit a "
                "real ARM resource ID for the subscription it provisions into",
            )

        deadline = time.monotonic() + max(self._config.grant_settle_seconds, 0.0)
        while True:
            try:
                self._write_role_assignment(
                    identity=identity,
                    role_definition_guid=role_definition_guid,
                    scope=scope,
                    assignment_name=assignment_name,
                )
            except Exception as exc:
                if not _is_replication_lag(exc):
                    return outcome(GRANT_FAILED, f"{type(exc).__name__}: {exc}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return outcome(
                        GRANT_PENDING,
                        "the identity's service principal has not replicated to the "
                        f"directory yet; ARM answered: {exc}",
                    )
                time.sleep(min(_SETTLE_POLL_SECONDS, remaining))
                continue
            return outcome(GRANT_APPLIED)

    def _write_role_assignment(
        self,
        *,
        identity: AzureManagedIdentity,
        role_definition_guid: str,
        scope: str,
        assignment_name: str,
    ) -> None:
        authz = self._authorization_client()
        role_definition_id = role_definition_resource_id(
            subscription_id=self._config.subscription_id,
            role_definition_guid=role_definition_guid,
        )
        try:
            authz.role_assignments.create(
                scope=scope,
                role_assignment_name=assignment_name,
                parameters={
                    "properties": {
                        "roleDefinitionId": role_definition_id,
                        "principalId": identity.principal_id,
                        "principalType": "ServicePrincipal",
                        "description": _ASSIGNMENT_DESCRIPTION,
                    },
                },
            )
        except Exception as exc:
            if type(exc).__name__ != "ResourceExistsError":
                raise map_api_error(exc) from exc
            # The name is derived from (scope, role, principal), so an existing
            # assignment under it is the same grant — unless someone else took
            # the name for a different grant, which must not pass as ours.
            self._resolve_existing_assignment(
                authz=authz,
                scope=scope,
                assignment_name=assignment_name,
                identity=identity,
                role_definition_guid=role_definition_guid,
                cause=exc,
            )

    def _prune_role_assignments(
        self,
        *,
        identity: AzureManagedIdentity,
        desired: set[tuple[str, str]],
    ) -> None:
        authz = self._authorization_client()
        try:
            existing = list(
                authz.role_assignments.list_for_subscription(
                    filter=f"principalId eq '{identity.principal_id}'",
                ),
            )
        except Exception as exc:
            # Never delete on a partial or failed view of the world.
            raise map_api_error(exc) from exc

        for assignment in existing:
            props = _assignment_properties(assignment)
            scope = str(getattr(props, "scope", "") or "")
            role_definition_id = str(getattr(props, "role_definition_id", "") or "")
            principal_id = str(getattr(props, "principal_id", "") or "")
            guid = role_definition_id.rsplit("/", 1)[-1].lower()
            if principal_id != identity.principal_id or not scope or not guid:
                continue
            if (guid, scope.rstrip("/")) in desired:
                continue
            name = str(getattr(assignment, "name", "") or "")
            if not _is_ours(
                assignment,
                principal_id=principal_id,
                role_definition_guid=guid,
                scope=scope,
            ):
                self._record_prune_refusal(
                    assignment=assignment,
                    name=name,
                    principal_id=principal_id,
                    role_definition_guid=guid,
                    scope=scope,
                )
                continue
            try:
                authz.role_assignments.delete(
                    scope=scope,
                    role_assignment_name=str(getattr(assignment, "name", "")),
                )
            except Exception as exc:
                if type(exc).__name__ == "ResourceNotFoundError":
                    continue
                raise map_api_error(exc) from exc

    def _record_prune_refusal(
        self,
        *,
        assignment: Any,
        name: str,
        principal_id: str,
        role_definition_guid: str,
        scope: str,
    ) -> None:
        """Make a refused prune audible.

        An operator's own assignment carries a random name and shows up here
        on every reconcile; that is expected and only worth a debug line. An
        assignment sitting under the exact name we would derive but *without*
        our marker is the interesting one — the grant looks revoked from the
        platform's side and is still in force in the subscription.
        """
        expected = role_assignment_name(
            principal_id=principal_id,
            role_definition_guid=role_definition_guid,
            scope=scope,
        )
        if name != expected:
            log.debug(
                "leaving unmanaged role assignment %s (role %s at %s) on principal %s",
                name,
                role_definition_guid,
                scope,
                principal_id,
            )
            return
        refusal = (
            f"role assignment {name} (role {role_definition_guid} at {scope}) carries this "
            f"platform's derived name but not its marker "
            f"{str(getattr(_assignment_properties(assignment), 'description', '') or '')!r}; "
            "left in place, so the grant is still in force despite no longer being declared"
        )
        self._prune_refusals.append(refusal)
        log.warning("refusing to prune role assignment: %s", refusal)

    def _resolve_existing_assignment(
        self,
        *,
        authz: Any,
        scope: str,
        assignment_name: str,
        identity: AzureManagedIdentity,
        role_definition_guid: str,
        cause: Exception,
    ) -> None:
        """Decide what an existing-assignment conflict actually means.

        Azure answers 409 in two different situations. Our derived name may be
        taken — the idempotent retry, unless the assignment under it expresses
        a different grant. Or the operator already granted this exact
        ``(principal, role, scope)`` under a name of their own, in which case
        the workload is authorized and re-deriving the name would conflict on
        every future reconcile. That assignment stays theirs: pruning only
        removes names it can re-derive, so accepting it here never gives us a
        licence to delete it later.
        """
        try:
            existing = authz.role_assignments.get(
                scope=scope,
                role_assignment_name=assignment_name,
            )
        except Exception as exc:
            if type(exc).__name__ != "ResourceNotFoundError":
                raise map_api_error(exc) from exc
            if self._grant_already_assigned(
                authz=authz,
                identity=identity,
                role_definition_guid=role_definition_guid,
                scope=scope,
            ):
                return
            raise map_api_error(cause) from cause
        props = _assignment_properties(existing)
        principal_id = str(getattr(props, "principal_id", "") or "")
        guid = str(getattr(props, "role_definition_id", "") or "").rsplit("/", 1)[-1].lower()
        if principal_id != identity.principal_id or guid != role_definition_guid:
            raise AzureRoleAssignmentError(
                f"role assignment {assignment_name} at {scope} already exists but binds "
                f"principal {principal_id!r} to role {guid!r}; refusing to treat another "
                "party's assignment as this workload's grant",
            )

    def _grant_already_assigned(
        self,
        *,
        authz: Any,
        identity: AzureManagedIdentity,
        role_definition_guid: str,
        scope: str,
    ) -> bool:
        """Is this exact grant already on the principal under another name?"""
        try:
            existing = list(
                authz.role_assignments.list_for_subscription(
                    filter=f"principalId eq '{identity.principal_id}'",
                ),
            )
        except Exception:
            # An unreadable listing proves nothing; let the caller surface the
            # original conflict rather than assume the grant is in place.
            return False
        target = scope.rstrip("/").lower()
        for assignment in existing:
            props = _assignment_properties(assignment)
            if str(getattr(props, "principal_id", "") or "") != identity.principal_id:
                continue
            guid = str(getattr(props, "role_definition_id", "") or "").rsplit("/", 1)[-1].lower()
            found = str(getattr(props, "scope", "") or "").rstrip("/").lower()
            if guid == role_definition_guid.lower() and found == target:
                return True
        return False

    def _authorization_client(self) -> Any:
        if self._authz is None:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.authorization import AuthorizationManagementClient

            self._authz = AuthorizationManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=self._config.subscription_id,
            )
        return self._authz

    def _identity_record(self, *, name: str, identity: Any) -> AzureManagedIdentity:
        resource_id = str(getattr(identity, "id", "") or "") or (
            f"/subscriptions/{self._config.subscription_id}"
            f"/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{name}"
        )
        return AzureManagedIdentity(
            name=name,
            resource_id=resource_id,
            client_id=str(getattr(identity, "client_id", "") or ""),
            principal_id=str(getattr(identity, "principal_id", "") or ""),
            tenant_id=str(getattr(identity, "tenant_id", "") or "") or self._config.tenant_id,
        )


# ---- assignment naming + shape helpers -------------------------------------


def role_assignment_name(
    *,
    principal_id: str,
    role_definition_guid: str,
    scope: str,
) -> str:
    """Derive the assignment's UUID name from the grant it expresses.

    Deterministic so concurrent reconciles converge on one assignment instead
    of racing, and so ownership stays decidable without a side table: recompute
    the name from an assignment's own fields and compare.
    """
    payload = f"{scope.rstrip('/')}|{role_definition_guid.lower()}|{principal_id}"
    return str(uuid.uuid5(_ASSIGNMENT_NAME_NAMESPACE, payload))


def _desired_assignments(
    permissions: list[dict[str, Any]],
) -> dict[tuple[str, str], str]:
    """Validate the Azure permission shape into ``{(guid, scope): role_name}``."""
    desired: dict[tuple[str, str], str] = {}
    for permission in permissions or []:
        if not isinstance(permission, dict):
            raise AzureGrantContractError(
                f"Azure workload-identity permissions must be mappings; got {permission!r}",
            )
        guid = str(permission.get("role_definition_id", "") or "").lower()
        scope = str(permission.get("scope", "") or "")
        if guid not in AZURE_BUILTIN_ROLE_IDS.values():
            raise AzureGrantContractError(
                f"Azure workload-identity permission names role definition {guid!r}, "
                "which is not in the vetted built-in catalog",
            )
        desired[(guid, validate_arm_scope(scope))] = str(permission.get("role_name", "") or guid)
    return desired


def _is_replication_lag(exc: BaseException) -> bool:
    """Is this ARM rejection just the directory not having caught up yet?

    Checked across the cause chain because ``map_api_error`` re-raises a typed
    provider error whose text is the SDK's, and the SDK's own ``error.code``
    only exists on the original.
    """
    current: BaseException | None = exc
    while current is not None:
        code = str(getattr(getattr(current, "error", None), "code", "") or "")
        haystack = f"{code} {current}".lower()
        if any(marker in haystack for marker in _REPLICATION_LAG_MARKERS):
            return True
        current = current.__cause__
    return False


def _assignment_properties(assignment: Any) -> Any:
    """Azure SDK models flatten properties onto the object; fakes may nest."""
    return getattr(assignment, "properties", None) or assignment


def _is_ours(
    assignment: Any,
    *,
    principal_id: str,
    role_definition_guid: str,
    scope: str,
) -> bool:
    """Fail-closed ownership test for a role assignment.

    Ours only when the name is exactly the UUIDv5 we would derive for this
    grant and the description is the marker we stamp. An operator's assignment
    carries a random name, so it can never satisfy the first condition; a
    foreign assignment that somehow does still fails the second.
    """
    name = str(getattr(assignment, "name", "") or "")
    if not name:
        return False
    expected = role_assignment_name(
        principal_id=principal_id,
        role_definition_guid=role_definition_guid,
        scope=scope,
    )
    if name != expected:
        return False
    props = _assignment_properties(assignment)
    return str(getattr(props, "description", "") or "") == _ASSIGNMENT_DESCRIPTION
