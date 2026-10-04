"""Existing-UAMI reconciliation through fixed native ARM clients (#2279).

This internal port is deliberately separate from the legacy injected driver.
Its caller supplies a previously recorded identity and a fresh admission/source
checkpoint. It cannot create/delete a UAMI or certify AKS/token propagation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from azure.identity_federated import AzureManagedIdentity
from azure.role_catalog import AZURE_BUILTIN_ROLE_IDS, subscription_of, validate_arm_scope

if TYPE_CHECKING:
    from collections.abc import Callable

    from azure.core.credentials import AccessToken, TokenCredential
    from azure.core.pipeline.transport import HttpTransport

_AUDIENCE = "api://AzureADTokenExchange"
_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{2,127}\Z")
_KSA = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
_GROUP = re.compile(r"[a-zA-Z0-9_().-]{1,90}\Z")
_MAX_BYTES = 2 * 1024 * 1024


class AzureOwnedIdentityError(ValueError):
    """A bounded native observation or effect could not be confirmed."""


def _guid(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise AzureOwnedIdentityError("A canonical nonzero identity GUID is required.") from None
    if parsed.int == 0 or str(parsed) != value:
        raise AzureOwnedIdentityError("A canonical nonzero identity GUID is required.")
    return value


@dataclass(frozen=True, slots=True)
class AzureIdentityContext:
    organization_id: str
    app_id: str
    environment_id: str
    cluster_id: str
    tenant_id: str
    subscription_id: str
    resource_group: str
    identity_name: str
    resource_id: str
    client_id: str
    principal_id: str
    issuer: str

    def __post_init__(self) -> None:
        for value in (
            self.organization_id,
            self.app_id,
            self.environment_id,
            self.cluster_id,
            self.tenant_id,
            self.subscription_id,
            self.client_id,
            self.principal_id,
        ):
            _guid(value)
        if not _GROUP.fullmatch(self.resource_group) or self.resource_group.endswith("."):
            raise AzureOwnedIdentityError("An exact resource group is required.")
        if not _NAME.fullmatch(self.identity_name):
            raise AzureOwnedIdentityError("An exact managed identity name is required.")
        expected = (
            f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{self.identity_name}"
        )
        if self.resource_id != expected:
            raise AzureOwnedIdentityError("The recorded managed identity placement is inconsistent.")
        issuer = urlsplit(self.issuer)
        if (
            not 1 <= len(self.issuer) <= 512
            or issuer.scheme != "https"
            or not issuer.hostname
            or issuer.username
            or issuer.password
            or issuer.port not in (None, 443)
            or issuer.query
            or issuer.fragment
            or "%" in self.issuer
            or any(ord(c) < 33 for c in self.issuer)
        ):
            raise AzureOwnedIdentityError("An exact HTTPS issuer declaration is required.")

    @property
    def owner_key(self) -> str:
        # The app identity is shared by its environments. Federation still binds
        # each exact namespace/KSA; role reconciliation receives the complete union.
        return f"{self.organization_id}|{self.app_id}|{self.cluster_id}"


@dataclass(frozen=True, slots=True)
class AzureFederation:
    namespace: str
    service_account: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.namespace, str)
            or len(self.namespace) > 63
            or not _KSA.fullmatch(self.namespace)
            or "." in self.namespace
            or not isinstance(self.service_account, str)
            or not _KSA.fullmatch(self.service_account)
            or ".." in self.service_account
        ):
            raise AzureOwnedIdentityError("An exact Kubernetes namespace and service account are required.")

    @property
    def subject(self) -> str:
        return f"system:serviceaccount:{self.namespace}:{self.service_account}"


@dataclass(frozen=True, slots=True)
class AzureIdentityGrant:
    role_definition_id: str
    scope: str

    def __post_init__(self) -> None:
        if self.role_definition_id not in AZURE_BUILTIN_ROLE_IDS.values():
            raise AzureOwnedIdentityError("Only reviewed built-in role definitions are supported.")
        if validate_arm_scope(self.scope) != self.scope or len(self.scope) > 1024:
            raise AzureOwnedIdentityError("An exact ARM grant scope is required.")


@dataclass(frozen=True, slots=True)
class AzureIdentityReconcileResult:
    identity: AzureManagedIdentity
    annotations: tuple[tuple[str, str], ...]
    federation_names: tuple[str, ...]
    assignment_names: tuple[str, ...]
    configuration_observed: bool = True
    workload_ready: bool = False
    propagation_verified: bool = False


def federation_name(context: AzureIdentityContext, subject: str) -> str:
    owner = hashlib.sha256(context.owner_key.encode()).hexdigest()[:32]
    source = hashlib.sha256(f"{context.issuer}|{subject}|{_AUDIENCE}".encode()).hexdigest()[:32]
    return f"astrolift-{owner}-{source}"


def assignment_name(context: AzureIdentityContext, grant: AzureIdentityGrant) -> str:
    return str(
        uuid5(NAMESPACE_URL, f"{context.owner_key}|{context.principal_id}|{grant.scope}|{grant.role_definition_id}")
    )


def _description(context: AzureIdentityContext) -> str:
    return f"astrolift.io/owned-workload-identity={context.owner_key}"


@dataclass(slots=True)
class _Budget:
    bytes_read: int = 0
    requests: int = 0
    allowed: set[tuple[str, str]] = field(default_factory=set)


class _TenantCredential:
    """Do not let ARM challenges widen the admitted credential tenant/scope."""

    def __init__(self, credential: TokenCredential, tenant_id: str) -> None:
        self._credential = credential
        self._tenant_id = tenant_id

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        if scopes != ("https://management.azure.com/.default",) or kwargs.get("tenant_id") not in (
            None,
            self._tenant_id,
        ):
            raise AzureOwnedIdentityError("Native identity credential scope or tenant changed.")
        kwargs["tenant_id"] = self._tenant_id
        return self._credential.get_token(*scopes, **kwargs)


def _transport(delegate: HttpTransport[Any, Any], budget: _Budget, checkpoint: Callable[[], None]) -> Any:
    from azure.core.pipeline.transport import HttpTransport

    class FixedARMTransport(HttpTransport[Any, Any]):
        def open(self) -> None:
            delegate.open()

        def close(self) -> None:
            delegate.close()

        def __enter__(self) -> FixedARMTransport:
            self.open()
            return self

        def __exit__(self, *args: Any) -> None:
            self.close()

        def send(self, request: Any, **kwargs: Any) -> Any:
            checkpoint()
            url = urlsplit(request.url)
            path = unquote(url.path)
            query = parse_qs(url.query, keep_blank_values=True)
            if (
                url.scheme != "https"
                or url.netloc != "management.azure.com"
                or url.fragment
                or (request.method, path.casefold()) not in budget.allowed
                or any(x in (".", "..") for x in path.split("/"))
                or not set(query) <= {"api-version", "$filter", "$top", "$skiptoken", "skiptoken"}
                or len(request.url) > 4096
                or budget.requests >= 512
            ):
                raise AzureOwnedIdentityError("Native identity transport refused an unreviewed request.")
            budget.requests += 1
            kwargs.update(connection_timeout=5, read_timeout=10)
            response = delegate.send(request, **kwargs)
            # A caller can lose admission/source authority while native I/O is
            # blocked. Never admit even the final observation under old facts.
            checkpoint()
            # No redirect/retry follows an untrusted native response.
            if 300 <= response.status_code < 400:
                raise AzureOwnedIdentityError("Native identity redirects are unsupported.")
            body = response.body()
            budget.bytes_read += len(body)
            if budget.bytes_read > _MAX_BYTES:
                raise AzureOwnedIdentityError("Native identity response bounds were exceeded.")
            return response

    return FixedARMTransport()


class AzureOwnedIdentityDriver:
    """Reconcile a recorded existing identity, never infer one from a name.

    ``checkpoint`` must revalidate current caller admission and the exact owner,
    placement, credential, issuer and complete desired union. It is required at
    every native request, including continuation reads. It supplies no remote CAS.
    """

    def __init__(
        self,
        context: AzureIdentityContext,
        *,
        checkpoint: Callable[[], None],
        credential: TokenCredential | None = None,
        transport: HttpTransport[Any, Any] | None = None,
    ) -> None:
        from azure.core.pipeline.transport import RequestsTransport
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.authorization import AuthorizationManagementClient
        from azure.mgmt.msi import ManagedServiceIdentityClient

        if not callable(checkpoint):
            raise AzureOwnedIdentityError("Fresh admission and source validation are required.")
        self.context = context
        self._checkpoint = checkpoint
        self._lock = Lock()
        self._budget = _Budget()
        self._credential = _TenantCredential(
            credential
            if credential is not None
            else DefaultAzureCredential(
                authority="https://login.microsoftonline.com",
                additionally_allowed_tenants=[],
                exclude_interactive_browser_credential=True,
            ),
            context.tenant_id,
        )
        self._transport = _transport(
            transport if transport is not None else RequestsTransport(), self._budget, checkpoint
        )
        common = dict(
            base_url="https://management.azure.com",
            transport=self._transport,
            retry_total=0,
            redirect_max=0,
            logging_enable=False,
            credential_scopes=["https://management.azure.com/.default"],
        )
        self._msi = ManagedServiceIdentityClient(
            self._credential,
            context.subscription_id,
            api_version="2024-11-30",
            **common,
        )
        self._authz = AuthorizationManagementClient(self._credential, context.subscription_id, **common)

    def close(self) -> None:
        # The declared SDK close methods have no annotations in MSI7/authz4.
        for client in (self._msi, self._authz):
            close: Callable[[], None] = client.close
            close()

    def _reset(self) -> None:
        self._budget.bytes_read = self._budget.requests = 0
        self._budget.allowed = {
            ("GET", self.context.resource_id.casefold()),
            ("GET", f"/subscriptions/{self.context.subscription_id}"),
            ("GET", (self.context.resource_id + "/federatedIdentityCredentials").casefold()),
            ("GET", f"/subscriptions/{self.context.subscription_id}/providers/microsoft.authorization/roleassignments"),
        }

    def observe(self) -> AzureManagedIdentity:
        if not self._lock.acquire(blocking=False):
            raise AzureOwnedIdentityError("Concurrent use of one native identity port is unsupported.")
        try:
            self._reset()
            return self._observe()
        except Exception:
            raise AzureOwnedIdentityError("Current managed identity ownership could not be verified.") from None
        finally:
            self._lock.release()

    def _observe(self) -> AzureManagedIdentity:
        from azure.core.pipeline.transport import HttpRequest

        c = self.context
        response = self._msi._client.send_request(
            HttpRequest(
                "GET",
                f"https://management.azure.com/subscriptions/{c.subscription_id}?api-version=2022-12-01",
            )
        )
        if response.status_code != 200:
            raise AzureOwnedIdentityError("Current subscription admission could not be verified.")
        subscription = json.loads(response.body())
        if (
            subscription.get("subscriptionId") != c.subscription_id
            or subscription.get("id", "").casefold() != f"/subscriptions/{c.subscription_id}"
            or subscription.get("tenantId") != c.tenant_id
            or subscription.get("state") != "Enabled"
        ):
            raise AzureOwnedIdentityError("The current subscription identity or tenant changed.")
        row = self._msi.user_assigned_identities.get(c.resource_group, c.identity_name)
        if (
            getattr(row, "id", None) != c.resource_id
            or getattr(row, "name", None) != c.identity_name
            or getattr(row, "client_id", None) != c.client_id
            or getattr(row, "principal_id", None) != c.principal_id
            or getattr(row, "tenant_id", None) != c.tenant_id
        ):
            raise AzureOwnedIdentityError("The recorded managed identity was replaced or is incomplete.")
        tags = getattr(row, "tags", None)
        required = {
            "managedby": "platform",
            "organizationid": c.organization_id,
            "appid": c.app_id,
            "clusterid": c.cluster_id,
        }
        if not isinstance(tags, dict) or len(tags) > 50:
            raise AzureOwnedIdentityError("Current identity owner markers are unavailable.")
        for suffix, expected in required.items():
            matches = [
                value
                for key, value in tags.items()
                if re.sub("[^a-z0-9]", "", key.lower()) in {"astrolift" + suffix, "astroliftio" + suffix}
            ]
            if not matches or any(value != expected for value in matches):
                raise AzureOwnedIdentityError("Current identity owner markers conflict or are foreign.")
        return AzureManagedIdentity(c.identity_name, c.resource_id, c.client_id, c.principal_id, c.tenant_id)

    @staticmethod
    def _inventory(paged: Any, maximum: int) -> list[Any]:
        rows: list[Any] = []
        pages = paged.by_page()
        for _ in range(4):
            try:
                page = next(pages)
            except StopIteration:
                return rows
            for row in page:
                rows.append(row)
                if len(rows) > maximum:
                    raise AzureOwnedIdentityError("Native identity inventory exceeds the supported bound.")
            if not pages.continuation_token:
                return rows
        raise AzureOwnedIdentityError("Native identity inventory is incomplete.")

    def reconcile(
        self,
        *,
        federations: tuple[AzureFederation, ...],
        grants: tuple[AzureIdentityGrant, ...],
    ) -> AzureIdentityReconcileResult:
        if not self._lock.acquire(blocking=False):
            raise AzureOwnedIdentityError("Concurrent use of one native identity port is unsupported.")
        try:
            self._reset()
            return self._reconcile(federations, grants)
        except Exception:
            # SDK errors can contain tokens, custom tags or native response bodies.
            raise AzureOwnedIdentityError(
                "Owned identity reconciliation was not confirmed; re-observe before retry."
            ) from None
        finally:
            self._lock.release()

    def _reconcile(
        self,
        federations: tuple[AzureFederation, ...],
        grants: tuple[AzureIdentityGrant, ...],
    ) -> AzureIdentityReconcileResult:
        c = self.context
        if len(federations) > 20 or len(grants) > 64:
            raise AzureOwnedIdentityError("The desired identity union exceeds its supported bound.")
        desired_fic = {federation_name(c, f.subject): (c.issuer, f.subject, (_AUDIENCE,)) for f in federations}
        desired_roles = {assignment_name(c, g): g for g in grants}
        if len(desired_fic) != len(federations) or len(desired_roles) != len(grants):
            raise AzureOwnedIdentityError("Duplicate desired identity entries are unsupported.")
        if any(subscription_of(g.scope).lower() != c.subscription_id for g in grants):
            raise AzureOwnedIdentityError("The desired grant is outside the recorded subscription.")
        identity = self._observe()
        fic_rows = self._inventory(self._msi.federated_identity_credentials.list(c.resource_group, c.identity_name), 20)
        role_rows = self._inventory(
            self._authz.role_assignments.list_for_subscription(
                filter=f"principalId eq '{c.principal_id}'",
            ),
            128,
        )
        fics: dict[str, Any] = {}
        roles: dict[str, Any] = {}
        prefix = "astrolift-" + hashlib.sha256(c.owner_key.encode()).hexdigest()[:32] + "-"
        for row in fic_rows:
            name = getattr(row, "name", None)
            signature = (
                getattr(row, "issuer", None),
                getattr(row, "subject", None),
                tuple(getattr(row, "audiences", None) or []),
            )
            if (
                not isinstance(name, str)
                or not _NAME.fullmatch(name)
                or name in fics
                or getattr(row, "id", None) != c.resource_id + "/federatedIdentityCredentials/" + name
            ):
                raise AzureOwnedIdentityError("A federation inventory identity is invalid.")
            if (
                not isinstance(signature[0], str)
                or not 1 <= len(signature[0]) <= 512
                or not isinstance(signature[1], str)
                or not 1 <= len(signature[1]) <= 512
                or len(signature[2]) != 1
                or not isinstance(signature[2][0], str)
                or not 1 <= len(signature[2][0]) <= 512
            ):
                raise AzureOwnedIdentityError("A federation inventory exceeds its native field bounds.")
            if name.startswith(prefix) and (
                signature[0] != c.issuer
                or not isinstance(signature[1], str)
                or not signature[1].startswith("system:serviceaccount:")
                or signature[2] != (_AUDIENCE,)
                or federation_name(c, signature[1]) != name
            ):
                raise AzureOwnedIdentityError("An owned federation signature changed.")
            fics[name] = row
            self._budget.allowed.add(("GET", str(row.id).casefold()))
        if len(fic_rows) + len(set(desired_fic) - set(fics)) > 20:
            raise AzureOwnedIdentityError("Federation capacity is insufficient without removing foreign entries.")
        for row in role_rows:
            name = getattr(row, "name", None)
            scope = getattr(row, "scope", None)
            if (
                not isinstance(name, str)
                or name in roles
                or not isinstance(scope, str)
                or validate_arm_scope(scope) != scope
                or subscription_of(scope).lower() != c.subscription_id
                or getattr(row, "id", "").casefold()
                != (scope + "/providers/Microsoft.Authorization/roleAssignments/" + name).casefold()
                or getattr(row, "principal_id", None) != c.principal_id
            ):
                raise AzureOwnedIdentityError("A role inventory identity is invalid.")
            _guid(name)
            role_id = getattr(row, "role_definition_id", "")
            guid = role_id.rsplit("/", 1)[-1]
            expected_role_id = (
                f"/subscriptions/{c.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/{guid}"
            )
            if role_id.casefold() != expected_role_id.casefold():
                raise AzureOwnedIdentityError("A role definition identity is invalid.")
            # Foreign custom roles are preserved. Our marker is only supported
            # with a reviewed built-in role and the complete derived signature.
            if getattr(row, "description", None) == _description(c):
                grant = AzureIdentityGrant(guid, scope)
                if assignment_name(c, grant) != name:
                    raise AzureOwnedIdentityError("An owned role signature changed.")
            if name in desired_roles and not self._role_matches(row, desired_roles[name]):
                raise AzureOwnedIdentityError("A desired role name is occupied by a foreign assignment.")
            roles[name] = row
            self._budget.allowed.add(("GET", str(row.id).casefold()))
        self._observe()
        for name, signature in desired_fic.items():
            path = c.resource_id + "/federatedIdentityCredentials/" + name
            self._budget.allowed.add(("GET", path.casefold()))
            if name in fics:
                continue
            self._observe()
            current = self._absent_or_current(
                self._msi.federated_identity_credentials.get, c.resource_group, c.identity_name, name
            )
            if current is not None:
                if (current.id, current.issuer, current.subject, tuple(current.audiences or [])) != (path, *signature):
                    raise AzureOwnedIdentityError("A federation appeared with an unreviewed signature.")
                continue
            self._observe()
            self._budget.allowed.add(("PUT", path.casefold()))
            self._msi.federated_identity_credentials.create_or_update(
                c.resource_group,
                c.identity_name,
                name,
                {"properties": {"issuer": signature[0], "subject": signature[1], "audiences": list(signature[2])}},
            )
            self._observe()
        for name, grant in desired_roles.items():
            path = grant.scope + "/providers/Microsoft.Authorization/roleAssignments/" + name
            self._budget.allowed.add(("GET", path.casefold()))
            if name in roles:
                continue
            self._observe()
            current = self._absent_or_current(self._authz.role_assignments.get, grant.scope, name)
            if current is not None:
                if current.id != path or not self._role_matches(current, grant):
                    raise AzureOwnedIdentityError("An assignment appeared with an unreviewed signature.")
                continue
            self._observe()
            self._budget.allowed.add(("PUT", path.casefold()))
            self._authz.role_assignments.create(
                grant.scope,
                name,
                {
                    "properties": {
                        "roleDefinitionId": (
                            f"/subscriptions/{c.subscription_id}"
                            f"/providers/Microsoft.Authorization/roleDefinitions/{grant.role_definition_id}"
                        ),
                        "principalId": c.principal_id,
                        "principalType": "ServicePrincipal",
                        "description": _description(c),
                    }
                },
            )
            self._observe()
        for name, row in fics.items():
            if name in desired_fic or not name.startswith(prefix):
                continue
            self._observe()
            current = self._msi.federated_identity_credentials.get(c.resource_group, c.identity_name, name)
            if (current.id, current.issuer, current.subject, current.audiences) != (
                row.id,
                row.issuer,
                row.subject,
                row.audiences,
            ):
                raise AzureOwnedIdentityError("A federation changed before removal.")
            self._observe()
            self._budget.allowed.add(("DELETE", row.id.casefold()))
            self._msi.federated_identity_credentials.delete(c.resource_group, c.identity_name, name)
            self._observe()
        for name, row in roles.items():
            if name in desired_roles or getattr(row, "description", None) != _description(c):
                continue
            self._observe()
            current = self._authz.role_assignments.get(row.scope, name)
            if (
                not self._role_matches(
                    current, AzureIdentityGrant(row.role_definition_id.rsplit("/", 1)[-1], row.scope)
                )
                or current.id != row.id
            ):
                raise AzureOwnedIdentityError("An assignment changed before removal.")
            self._observe()
            self._budget.allowed.add(("DELETE", row.id.casefold()))
            self._authz.role_assignments.delete(row.scope, name)
            self._observe()
        # Reread the complete final union. A successful PUT/DELETE alone is not
        # evidence that ARM's current view has converged.
        final_fic = self._inventory(
            self._msi.federated_identity_credentials.list(c.resource_group, c.identity_name), 20
        )
        final_roles = self._inventory(
            self._authz.role_assignments.list_for_subscription(filter=f"principalId eq '{c.principal_id}'"), 128
        )
        if len({r.name for r in final_fic}) != len(final_fic) or len({r.name for r in final_roles}) != len(final_roles):
            raise AzureOwnedIdentityError("The final native identity inventory contains duplicates.")
        if any(r.id != c.resource_id + "/federatedIdentityCredentials/" + r.name for r in final_fic):
            raise AzureOwnedIdentityError("The final federation identity changed.")
        if {r.name for r in final_fic if r.name.startswith(prefix)} != set(desired_fic):
            raise AzureOwnedIdentityError("Owned federation convergence is not observed.")
        for row in final_fic:
            if (
                row.name in desired_fic
                and (row.issuer, row.subject, tuple(row.audiences or [])) != desired_fic[row.name]
            ):
                raise AzureOwnedIdentityError("Desired federation convergence is not observed.")
        if {r.name for r in final_roles if getattr(r, "description", None) == _description(c)} != set(desired_roles):
            raise AzureOwnedIdentityError("Owned grant convergence is not observed.")
        if any(not self._role_matches(r, desired_roles[r.name]) for r in final_roles if r.name in desired_roles):
            raise AzureOwnedIdentityError("Desired grant convergence is not observed.")
        self._observe()
        return AzureIdentityReconcileResult(
            identity,
            (("azure.workload.identity/client-id", c.client_id), ("azure.workload.identity/tenant-id", c.tenant_id)),
            tuple(sorted(desired_fic)),
            tuple(sorted(desired_roles)),
        )

    def _role_matches(self, row: Any, grant: AzureIdentityGrant) -> bool:
        c = self.context
        return bool(
            row.name == assignment_name(c, grant)
            and row.id == grant.scope + "/providers/Microsoft.Authorization/roleAssignments/" + row.name
            and row.principal_id == c.principal_id
            and row.principal_type == "ServicePrincipal"
            and row.scope == grant.scope
            and row.role_definition_id
            == (
                f"/subscriptions/{c.subscription_id}"
                f"/providers/Microsoft.Authorization/roleDefinitions/{grant.role_definition_id}"
            )
            and row.description == _description(c)
        )

    @staticmethod
    def _absent_or_current(method: Callable[..., Any], *args: str) -> Any:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return method(*args)
        except ResourceNotFoundError:
            return None
