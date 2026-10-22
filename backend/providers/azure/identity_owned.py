"""The platform's own UAMIs, through fixed native ARM clients (#2279).

This internal port is deliberately separate from the legacy injected driver.
:class:`AzureIdentityProvisioner` observes or creates the UAMI one
org/app/cluster owner gets, after checking the AKS cluster's live OIDC issuer.
:class:`AzureOwnedIdentityDriver` reconciles and removes an identity the caller
recorded from that observation. Both take a fresh admission/source checkpoint.
Neither certifies token propagation or workload rollout.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from _sdk._telemetry import driver_op
from _sdk.azure_tags import serialize_azure_arm_tags
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
_AKS_NAME = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9_-]{0,61}[a-zA-Z0-9])?\Z")
_LOCATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ]{0,63}\Z")
_MAX_BYTES = 2 * 1024 * 1024
# A stable AKS API that carries oidcIssuerProfile and securityProfile.workloadIdentity.
_AKS_API_VERSION = "2024-09-01"


class AzureOwnedIdentityError(ValueError):
    """A bounded native observation or effect could not be confirmed."""


class AzureIdentityRefused(AzureOwnedIdentityError):
    """The native state is known, and the platform will not act on it.

    Raised with a fixed message the platform composed itself, never SDK or
    response text, so it can reach an operator: a same-name identity someone
    else owns, a recorded identity that was replaced or deleted, a cluster
    whose live issuer is not the configured one.
    """


def _guid(value: object) -> str:
    if not isinstance(value, str):
        raise AzureOwnedIdentityError("A canonical nonzero identity GUID is required.")
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
        _require_placement(self.resource_group, self.identity_name)
        if self.resource_id != _resource_id(self.subscription_id, self.resource_group, self.identity_name):
            raise AzureOwnedIdentityError("The recorded managed identity placement is inconsistent.")
        _require_issuer(self.issuer)

    @property
    def owner_key(self) -> str:
        # The app identity is shared by its environments. Federation still binds
        # each exact namespace/KSA; role reconciliation receives the complete union.
        return f"{self.organization_id}|{self.app_id}|{self.cluster_id}"


def _require_placement(resource_group: str, identity_name: str) -> None:
    if not isinstance(resource_group, str) or not _GROUP.fullmatch(resource_group) or resource_group.endswith("."):
        raise AzureOwnedIdentityError("An exact resource group is required.")
    if not isinstance(identity_name, str) or not _NAME.fullmatch(identity_name):
        raise AzureOwnedIdentityError("An exact managed identity name is required.")


def _require_issuer(value: object) -> None:
    if not isinstance(value, str):
        raise AzureOwnedIdentityError("An exact HTTPS issuer declaration is required.")
    issuer = urlsplit(value)
    if (
        not 1 <= len(value) <= 512
        or issuer.scheme != "https"
        or not issuer.hostname
        or issuer.username
        or issuer.password
        or issuer.port not in (None, 443)
        or issuer.query
        or issuer.fragment
        or "%" in value
        or any(ord(c) < 33 for c in value)
    ):
        raise AzureOwnedIdentityError("An exact HTTPS issuer declaration is required.")


def _resource_id(subscription_id: str, resource_group: str, identity_name: str) -> str:
    return (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{identity_name}"
    )


def owner_tags(*, organization_id: str, app_id: str, cluster_id: str) -> dict[str, str]:
    """The ARM tags that mark a UAMI as this platform's, for one owner."""
    return serialize_azure_arm_tags(
        {
            "astrolift.io/managed-by": "platform",
            "astrolift.io/organization-id": organization_id,
            "astrolift.io/app-id": app_id,
            "astrolift.io/cluster-id": cluster_id,
        }
    )


def _owner_markers_match(tags: Any, *, organization_id: str, app_id: str, cluster_id: str) -> bool:
    """Every recognized spelling of each owner marker carries this owner's value."""
    if not isinstance(tags, dict) or len(tags) > 50:
        return False
    required = {
        "managedby": "platform",
        "organizationid": organization_id,
        "appid": app_id,
        "clusterid": cluster_id,
    }
    for suffix, expected in required.items():
        matches = [
            value
            for key, value in tags.items()
            if isinstance(key, str)
            and re.sub("[^a-z0-9]", "", key.lower()) in {"astrolift" + suffix, "astroliftio" + suffix}
        ]
        if not matches or any(value != expected for value in matches):
            return False
    return True


@dataclass(frozen=True, slots=True)
class AzureIdentityPlacement:
    """Where one owner's UAMI lives, and the AKS cluster that federates it.

    Everything here comes from the caller's own records and configuration;
    the native IDs come only from observing the identity.
    """

    organization_id: str
    app_id: str
    cluster_id: str
    tenant_id: str
    subscription_id: str
    resource_group: str
    identity_name: str
    location: str
    cluster_name: str
    issuer: str

    def __post_init__(self) -> None:
        for value in (self.organization_id, self.app_id, self.cluster_id, self.tenant_id, self.subscription_id):
            _guid(value)
        _require_placement(self.resource_group, self.identity_name)
        if not isinstance(self.location, str) or not _LOCATION.fullmatch(self.location):
            raise AzureOwnedIdentityError("An exact Azure location is required.")
        if not isinstance(self.cluster_name, str) or not _AKS_NAME.fullmatch(self.cluster_name):
            raise AzureOwnedIdentityError("An exact AKS cluster name is required.")
        _require_issuer(self.issuer)

    @property
    def resource_id(self) -> str:
        return _resource_id(self.subscription_id, self.resource_group, self.identity_name)

    @property
    def cluster_resource_id(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.ContainerService/managedClusters/{self.cluster_name}"
        )

    @property
    def tags(self) -> dict[str, str]:
        return owner_tags(organization_id=self.organization_id, app_id=self.app_id, cluster_id=self.cluster_id)

    def context(self, *, environment_id: str, identity: AzureManagedIdentity) -> AzureIdentityContext:
        """The reconcile context for an identity observed at this placement."""
        return AzureIdentityContext(
            organization_id=self.organization_id,
            app_id=self.app_id,
            environment_id=environment_id,
            cluster_id=self.cluster_id,
            tenant_id=self.tenant_id,
            subscription_id=self.subscription_id,
            resource_group=self.resource_group,
            identity_name=self.identity_name,
            resource_id=identity.resource_id,
            client_id=identity.client_id,
            principal_id=identity.principal_id,
            issuer=self.issuer,
        )


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
    removed_federation_names: tuple[str, ...] = ()
    removed_assignment_names: tuple[str, ...] = ()
    foreign_federation_names: tuple[str, ...] = ()
    """Federations on the identity this owner does not own, left in place."""
    foreign_assignment_names: tuple[str, ...] = ()
    """Role assignments on the principal this owner does not own, left in place."""


@dataclass(frozen=True, slots=True)
class AzureIdentityRemovalResult:
    """What a removal observably did, and what it could not claim.

    Removing an assignment or deleting the identity does not revoke access a
    token issued earlier already carries; that ends when the token expires.
    """

    identity_deleted: bool
    identity_absent: bool = False
    """The identity was already gone. Owned assignments on its principal, if
    any survived it, grant nothing and are not inspected."""
    identity_replaced: bool = False
    """Another identity holds the recorded name, so the recorded one is gone
    (``identity_absent`` is set too). The replacement is left untouched."""
    removed_federation_names: tuple[str, ...] = ()
    removed_assignment_names: tuple[str, ...] = ()
    foreign_federation_names: tuple[str, ...] = ()
    foreign_assignment_names: tuple[str, ...] = ()
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


def _admitted_credential(credential: Any) -> TokenCredential:
    """The token credential a native port may use.

    Production configs carry the cluster's stamped ``CloudCredential``; for
    Azure that is only ever the ambient identity, which is the public Entra
    chain fixed below. An explicitly supplied token credential is a trusted
    admitted caller port. Anything else is refused rather than guessed at.
    """
    from _sdk.cloud_credentials import CloudCredential

    if credential is None or (
        isinstance(credential, CloudCredential) and credential.cloud == "azure" and credential.is_ambient
    ):
        from azure.identity import DefaultAzureCredential

        return DefaultAzureCredential(
            authority="https://login.microsoftonline.com",
            additionally_allowed_tenants=[],
            exclude_interactive_browser_credential=True,
        )
    if isinstance(credential, CloudCredential) or not callable(getattr(credential, "get_token", None)):
        raise AzureOwnedIdentityError("Native identity requires the ambient Azure credential or an admitted port.")
    return cast("TokenCredential", credential)


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


class _NativeIdentityPort:
    """Fixed, bounded ARM clients for one admitted tenant and subscription.

    ``checkpoint`` must revalidate current caller admission and the exact owner,
    placement, credential and issuer. It runs before every native request and
    after every response, including continuation reads. It supplies no remote CAS.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        subscription_id: str,
        checkpoint: Callable[[], None],
        credential: Any,
        transport: HttpTransport[Any, Any] | None,
    ) -> None:
        from azure.core.pipeline.transport import RequestsTransport
        from azure.mgmt.authorization import AuthorizationManagementClient
        from azure.mgmt.msi import ManagedServiceIdentityClient

        if not callable(checkpoint):
            raise AzureOwnedIdentityError("Fresh admission and source validation are required.")
        self._tenant_id = tenant_id
        self._subscription_id = subscription_id
        self._checkpoint = checkpoint
        self._lock = Lock()
        self._budget = _Budget()
        self._credential = _TenantCredential(_admitted_credential(credential), tenant_id)
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
            subscription_id,
            api_version="2024-11-30",
            **common,
        )
        self._authz = AuthorizationManagementClient(self._credential, subscription_id, **common)

    def close(self) -> None:
        # The declared SDK close methods have no annotations in MSI7/authz4.
        for client in (self._msi, self._authz):
            close: Callable[[], None] = client.close
            close()

    def _read_json(self, path: str, api_version: str) -> tuple[int, Any]:
        from azure.core.pipeline.transport import HttpRequest

        response = self._msi._client.send_request(
            HttpRequest("GET", f"https://management.azure.com{path}?api-version={api_version}")
        )
        return response.status_code, json.loads(response.body()) if response.status_code == 200 else None

    def _verify_subscription(self) -> None:
        status, subscription = self._read_json(f"/subscriptions/{self._subscription_id}", "2022-12-01")
        if status != 200:
            raise AzureOwnedIdentityError("Current subscription admission could not be verified.")
        if (
            subscription.get("subscriptionId") != self._subscription_id
            or subscription.get("id", "").casefold() != f"/subscriptions/{self._subscription_id}"
            or subscription.get("tenantId") != self._tenant_id
            or subscription.get("state") != "Enabled"
        ):
            raise AzureIdentityRefused(
                f"Subscription {self._subscription_id} is not an enabled subscription of tenant {self._tenant_id}."
            )

    @staticmethod
    def _absent_or_current(method: Callable[..., Any], *args: str) -> Any:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return method(*args)
        except ResourceNotFoundError:
            return None


class AzureIdentityProvisioner(_NativeIdentityPort):
    """Observe the UAMI one owner gets, creating it only where none exists.

    A same-name identity without this owner's markers is refused, never
    adopted or retagged. With ``recorded`` the identity must still carry the
    native IDs the caller recorded: one that was deleted or replaced is
    refused rather than re-created, because pods and grants were bound to
    the old principal. Before anything, the AKS cluster's live OIDC issuer and
    workload identity must match the placement, so nothing is ever federated
    to an issuer the cluster does not sign with.

    The MSI API offers no conditional create. An identity created under the
    same name between the absent read and the PUT would be retagged and
    adopted; that window is the residual race this port does not close.
    """

    def __init__(
        self,
        placement: AzureIdentityPlacement,
        *,
        checkpoint: Callable[[], None],
        credential: Any = None,
        transport: HttpTransport[Any, Any] | None = None,
    ) -> None:
        super().__init__(
            tenant_id=placement.tenant_id,
            subscription_id=placement.subscription_id,
            checkpoint=checkpoint,
            credential=credential,
            transport=transport,
        )
        self.placement = placement

    @driver_op(
        cloud="azure", driver="identity", audit=True, redact_args=("recorded",), redact_errors=True, heartbeat=False
    )
    def ensure(self, *, recorded: AzureManagedIdentity | None = None) -> AzureManagedIdentity:
        if not self._lock.acquire(blocking=False):
            raise AzureOwnedIdentityError("Concurrent use of one native identity port is unsupported.")
        try:
            p = self.placement
            self._budget.bytes_read = self._budget.requests = 0
            self._budget.allowed = {
                ("GET", f"/subscriptions/{p.subscription_id}"),
                ("GET", p.cluster_resource_id.casefold()),
                ("GET", p.resource_id.casefold()),
            }
            return self._ensure(recorded)
        except AzureIdentityRefused:
            raise
        except Exception:
            # SDK errors can contain tokens, custom tags or native response bodies.
            raise AzureOwnedIdentityError(
                "Managed identity ownership was not confirmed; re-observe before retry."
            ) from None
        finally:
            self._lock.release()

    def _ensure(self, recorded: AzureManagedIdentity | None) -> AzureManagedIdentity:
        p = self.placement
        self._verify_subscription()
        self._verify_cluster()
        current = self._absent_or_current(self._msi.user_assigned_identities.get, p.resource_group, p.identity_name)
        if current is None:
            if recorded is not None:
                raise AzureIdentityRefused(
                    f"The recorded managed identity {p.identity_name} no longer exists in resource group "
                    f"{p.resource_group}. The platform does not re-create a recorded identity; remove the "
                    "app's workload identity, then deploy to create a new one."
                )
            self._budget.allowed.add(("PUT", p.resource_id.casefold()))
            self._msi.user_assigned_identities.create_or_update(
                p.resource_group, p.identity_name, {"location": p.location, "tags": p.tags}
            )
            current = self._msi.user_assigned_identities.get(p.resource_group, p.identity_name)
        identity = self._owned(current)
        if recorded is not None and (
            identity.resource_id,
            identity.client_id,
            identity.principal_id,
            identity.tenant_id,
        ) != (recorded.resource_id, recorded.client_id, recorded.principal_id, recorded.tenant_id):
            raise AzureIdentityRefused(
                f"Managed identity {p.identity_name} in resource group {p.resource_group} was replaced: its "
                "client or principal ID is not the recorded one. The platform does not adopt a replacement; "
                "remove the app's workload identity, then deploy to create a new one."
            )
        return identity

    def _verify_cluster(self) -> None:
        p = self.placement
        status, cluster = self._read_json(p.cluster_resource_id, _AKS_API_VERSION)
        if status != 200 or not isinstance(cluster, dict):
            raise AzureIdentityRefused(
                f"AKS cluster {p.cluster_name} in resource group {p.resource_group} could not be read, so its "
                "OIDC issuer and workload identity are unverified."
            )
        properties = cluster.get("properties") or {}
        oidc = properties.get("oidcIssuerProfile") or {}
        workload = (properties.get("securityProfile") or {}).get("workloadIdentity") or {}
        if str(cluster.get("id", "")).casefold() != p.cluster_resource_id.casefold():
            raise AzureOwnedIdentityError("The AKS cluster response named another resource.")
        if oidc.get("enabled") is not True or workload.get("enabled") is not True:
            raise AzureIdentityRefused(
                f"AKS cluster {p.cluster_name} does not have both its OIDC issuer and workload identity enabled."
            )
        live = oidc.get("issuerURL")
        if live != p.issuer:
            try:
                _require_issuer(live)
                reported = f" {live}"
            except AzureOwnedIdentityError:
                reported = " another issuer"
            raise AzureIdentityRefused(
                f"AKS cluster {p.cluster_name} signs with OIDC issuer{reported}, not the configured {p.issuer}."
            )

    def _owned(self, row: Any) -> AzureManagedIdentity:
        p = self.placement
        if (
            getattr(row, "id", None) != p.resource_id
            or getattr(row, "name", None) != p.identity_name
            or getattr(row, "tenant_id", None) != p.tenant_id
        ):
            raise AzureOwnedIdentityError("The managed identity response named another resource.")
        if not _owner_markers_match(
            getattr(row, "tags", None),
            organization_id=p.organization_id,
            app_id=p.app_id,
            cluster_id=p.cluster_id,
        ):
            raise AzureIdentityRefused(
                f"A managed identity named {p.identity_name} already exists in resource group "
                f"{p.resource_group} without this app's ownership markers. The platform does not adopt it."
            )
        return AzureManagedIdentity(
            p.identity_name,
            p.resource_id,
            _guid(getattr(row, "client_id", None)),
            _guid(getattr(row, "principal_id", None)),
            p.tenant_id,
        )


class AzureOwnedIdentityDriver(_NativeIdentityPort):
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
        credential: Any = None,
        transport: HttpTransport[Any, Any] | None = None,
    ) -> None:
        super().__init__(
            tenant_id=context.tenant_id,
            subscription_id=context.subscription_id,
            checkpoint=checkpoint,
            credential=credential,
            transport=transport,
        )
        self.context = context

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
        c = self.context
        self._verify_subscription()
        row = self._msi.user_assigned_identities.get(c.resource_group, c.identity_name)
        self._recorded(row)
        return AzureManagedIdentity(c.identity_name, c.resource_id, c.client_id, c.principal_id, c.tenant_id)

    def _recorded(self, row: Any) -> None:
        c = self.context
        if (
            getattr(row, "id", None) != c.resource_id
            or getattr(row, "name", None) != c.identity_name
            or getattr(row, "client_id", None) != c.client_id
            or getattr(row, "principal_id", None) != c.principal_id
            or getattr(row, "tenant_id", None) != c.tenant_id
        ):
            raise AzureIdentityRefused(
                f"Managed identity {c.identity_name} in resource group {c.resource_group} was replaced or is "
                "incomplete: it no longer carries the recorded native IDs."
            )
        if not _owner_markers_match(
            getattr(row, "tags", None),
            organization_id=c.organization_id,
            app_id=c.app_id,
            cluster_id=c.cluster_id,
        ):
            raise AzureIdentityRefused(
                f"Managed identity {c.identity_name} in resource group {c.resource_group} no longer carries "
                "this app's ownership markers."
            )

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

    @driver_op(
        cloud="azure",
        driver="identity",
        audit=True,
        redact_args=("federations", "grants"),
        redact_errors=True,
        heartbeat=False,
    )
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
        removed_fics: list[str] = []
        removed_roles: list[str] = []
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
            removed_fics.append(name)
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
            removed_roles.append(name)
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
            removed_federation_names=tuple(sorted(removed_fics)),
            removed_assignment_names=tuple(sorted(removed_roles)),
            foreign_federation_names=tuple(sorted(r.name for r in final_fic if not r.name.startswith(prefix))),
            foreign_assignment_names=tuple(
                sorted(r.name for r in final_roles if getattr(r, "description", None) != _description(c))
            ),
        )

    @driver_op(cloud="azure", driver="identity", audit=True, redact_errors=True, heartbeat=False)
    def remove(self) -> AzureIdentityRemovalResult:
        """Take this owner's federations and grants off the identity, then delete it.

        The identity is deleted only while it still carries the recorded IDs
        and this owner's markers, and only once nothing foreign remains on it:
        deleting it would take another party's federation with it and orphan
        their assignments. Then it stays, stripped of everything this owner
        added, and the result names what is left. An identity already gone is
        reported absent, not deleted, and so is one replaced under its name:
        the replacement is not this owner's to strip or delete.
        """
        if not self._lock.acquire(blocking=False):
            raise AzureOwnedIdentityError("Concurrent use of one native identity port is unsupported.")
        try:
            self._reset()
            return self._remove()
        except AzureIdentityRefused:
            raise
        except Exception:
            # SDK errors can contain tokens, custom tags or native response bodies.
            raise AzureOwnedIdentityError(
                "Owned identity removal was not confirmed; re-observe before retry."
            ) from None
        finally:
            self._lock.release()

    def _remove(self) -> AzureIdentityRemovalResult:
        c = self.context
        self._verify_subscription()
        current = self._absent_or_current(self._msi.user_assigned_identities.get, c.resource_group, c.identity_name)
        if current is None:
            return AzureIdentityRemovalResult(identity_deleted=False, identity_absent=True)
        if self._replacement(current):
            return AzureIdentityRemovalResult(identity_deleted=False, identity_absent=True, identity_replaced=True)
        self._recorded(current)
        stripped = self._reconcile((), ())
        result = AzureIdentityRemovalResult(
            identity_deleted=False,
            removed_federation_names=stripped.removed_federation_names,
            removed_assignment_names=stripped.removed_assignment_names,
            foreign_federation_names=stripped.foreign_federation_names,
            foreign_assignment_names=stripped.foreign_assignment_names,
        )
        if stripped.foreign_federation_names or stripped.foreign_assignment_names:
            return result
        self._observe()
        self._budget.allowed.add(("DELETE", c.resource_id.casefold()))
        self._msi.user_assigned_identities.delete(c.resource_group, c.identity_name)
        if self._absent_or_current(self._msi.user_assigned_identities.get, c.resource_group, c.identity_name):
            raise AzureOwnedIdentityError("The managed identity is still present after its deletion.")
        return dataclasses.replace(result, identity_deleted=True)

    def _replacement(self, row: Any) -> bool:
        """``row`` is a complete identity at the recorded name with another principal.

        A principal is fixed for an identity's lifetime, so the recorded one was
        deleted. Anything incomplete or misnamed is not evidence of that.
        """
        c = self.context
        if (getattr(row, "id", None), getattr(row, "name", None), getattr(row, "tenant_id", None)) != (
            c.resource_id,
            c.identity_name,
            c.tenant_id,
        ):
            return False
        try:
            _guid(getattr(row, "client_id", None))
            return _guid(getattr(row, "principal_id", None)) != c.principal_id
        except AzureOwnedIdentityError:
            return False

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
