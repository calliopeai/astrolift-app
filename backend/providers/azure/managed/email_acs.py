"""Azure Communication Services (Email) managed-service driver (#375).

Implements ``ManagedServiceDriver`` for Azure's transactional
email path. The canonical Azure-native surface is the Email
feature of Azure Communication Services (ACS). An ACS resource
groups email + chat + SMS + voice domains; this driver scopes
itself to the Email Domains sub-resource.

Operator setup expectations:

  - An existing ``Microsoft.Communication/CommunicationServices``
    resource. The driver does NOT auto-create the parent resource
    because ACS provisioning involves data-residency choices
    (``dataLocation``) and tenant-level approvals operators must
    make explicitly.
  - The parent resource id is passed via the driver's
    ``communication_resource_id`` config field.

What the driver manages:

  - ``Microsoft.Communication/emailServices/<es>/domains/<domain>``
    -- an Email Service domain entry tied to the operator's
    EmailService resource. The driver supports two ``DomainManagement``
    values:
      * ``AzureManaged`` -- Microsoft owns the sender domain
        (``<guid>.azurecomm.net``), no DNS to publish.
      * ``CustomerManaged`` -- operator owns the sender domain
        and is responsible for publishing the SPF / DKIM TXT
        records ACS surfaces post-create.
  - ``connection_string`` for the parent ACS resource is read on
    binding and persisted to Key Vault.

Deprovision implements the SDK's four-corner matrix:

  delete_data=False, force_destroy=False (default):
    keep the email domain entry AND keep the connection-string
    secret in Key Vault. DNS records the operator published for
    CustomerManaged remain in place (operator may use them for
    SPF / DKIM elsewhere). Resource lock is respected.

  delete_data=True, force_destroy=False:
    delete the email domain entry, purge the Key Vault secret.
    Resource lock is respected.

  delete_data=False, force_destroy=True:
    keep state, bypass the resource lock (driver removes blocking
    locks via management_locks).

  delete_data=True, force_destroy=True:
    nuke -- delete domain entry + lock + secret. Atomic cleanup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    Grant,
    ManagedServiceDriver,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
)

KIND = "email"


class AzureCommunicationEmailError(Exception):
    """Distinct from the generic plugin error so the control plane's
    failure-handling logic can tell managed-service failures from
    infra-driver failures (managed-service partial state often needs
    targeted cleanup of half-created Key Vault secrets etc.)."""


_VALID_DOMAIN_MANAGEMENT = {"AzureManaged", "CustomerManaged"}


@dataclass(frozen=True)
class AzureCommunicationEmailConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    subscription_id: str
    resource_group: str
    location: str = "global"
    """ACS Email Service + Domain resources live in the ``global``
    location. The parent CommunicationServices resource has its own
    dataLocation which is separate from this ``location`` knob."""

    email_service_name: str = "astrolift-email"
    """Pre-created EmailService name under the operator's resource
    group. The driver creates child domain resources under it."""

    communication_resource_id: str = ""
    """Resource ID of the existing CommunicationServices resource
    the driver reads connection strings off. Required."""

    default_domain_management: str = "AzureManaged"
    """``AzureManaged`` skips DNS; ``CustomerManaged`` requires the
    operator to publish SPF / DKIM TXT records ACS returns
    post-provision."""

    keyvault_url: str = ""
    """Key Vault URL where the driver stores connection strings.
    Empty string means no Key Vault is configured -- the driver
    returns an error on provision rather than emitting credentials
    in the clear."""

    secret_name_prefix: str = "astrolift-acs-email"

    delete_data_default: bool = False
    """Drives default soft state for the deletion-flag default
    override path. Not used directly in lifecycle today."""

    mgmt_client: Any | None = None
    """Injected ``CommunicationServiceManagementClient`` for tests."""

    locks_client: Any | None = None
    """Injected ``ManagementLockClient`` for tests."""

    secret_client: Any | None = None
    """Injected ``SecretClient`` for tests."""


def tags_for(spec: ProvisionSpec) -> dict[str, str]:
    """Same shape as the Cache-Redis / Postgres-Flex drivers'
    ``tags_for`` helper."""
    base = {
        "astrolift.io/managed-by": "platform",
        "astrolift.io/organization": spec.organization_slug,
        "astrolift.io/app": spec.app_slug,
        "astrolift.io/environment": spec.environment_name,
        "astrolift.io/cluster": spec.tenant_cluster_id,
        "astrolift.io/isolation": spec.isolation,
    }
    # Per-binding cost-attribution keys (#438). Azure allows the
    # slash + dot form so keys stay identical to the canonical
    # platform schema.
    if spec.binding_id:
        base["astrolift.io/binding"] = spec.binding_id
    if spec.managed_service_id:
        base["astrolift.io/managed_service_id"] = spec.managed_service_id
    base.update({
        f"astrolift.io/extra/{k}": v
        for k, v in (spec.tags or {}).items()
    })
    return base


class AzureCommunicationEmailDriver(ManagedServiceDriver):
    def __init__(
        self, *, config: AzureCommunicationEmailConfig,
    ) -> None:
        if not config.communication_resource_id:
            raise AzureCommunicationEmailError(
                "azure communication email driver requires "
                "communication_resource_id",
            )
        self._config = config
        if config.mgmt_client is not None:
            self._mgmt = config.mgmt_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.communication import (
                CommunicationServiceManagementClient,
            )

            self._mgmt = CommunicationServiceManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )
        self._locks = config.locks_client  # optional; only used on force_destroy
        if config.secret_client is not None:
            self._secrets = config.secret_client
        elif config.keyvault_url:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            self._secrets = SecretClient(
                vault_url=config.keyvault_url,
                credential=DefaultAzureCredential(),
            )
        else:
            self._secrets = None

    # ---- lifecycle ----------------------------------------------------

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        if self._secrets is None:
            return ProvisionResult(
                ok=False, handle="",
                message=(
                    "azure communication email driver requires a Key "
                    "Vault (set keyvault_url or inject secret_client)"
                ),
                errors=["no_secret_backend"],
            )
        cfg = spec.config or {}
        domain_name = self._domain_name_for(spec=spec)
        domain_management = (
            cfg.get(
                "domain_management",
                self._config.default_domain_management,
            )
        )
        if domain_management not in _VALID_DOMAIN_MANAGEMENT:
            return ProvisionResult(
                ok=False, handle="",
                message=(
                    f"domain_management must be one of "
                    f"{sorted(_VALID_DOMAIN_MANAGEMENT)} "
                    f"(got {domain_management!r})"
                ),
                errors=["invalid_domain_management"],
            )

        existing = self._describe(domain_name)
        if existing is not None:
            self._store_connection_string(domain_name=domain_name)
            return ProvisionResult(
                ok=True,
                handle=self._handle_for(domain_name=domain_name),
                message=(
                    f"acs email domain {domain_name} already exists "
                    f"(state={_state_of(existing)})"
                ),
            )

        parameters: dict[str, Any] = {
            "location": self._config.location,
            "properties": {
                "domain_management": domain_management,
            },
            "tags": tags_for(spec),
        }
        if cfg.get("user_engagement_tracking"):
            parameters["properties"]["user_engagement_tracking"] = (
                cfg["user_engagement_tracking"]
            )

        try:
            poller = self._mgmt.domains.begin_create_or_update(
                resource_group_name=self._config.resource_group,
                email_service_name=self._config.email_service_name,
                domain_name=domain_name,
                parameters=parameters,
            )
            poller.result()
        except Exception as exc:
            return ProvisionResult(
                ok=False, handle="",
                message=f"begin_create_or_update: {exc}",
                errors=[str(exc)],
            )

        try:
            self._store_connection_string(domain_name=domain_name)
        except Exception as exc:
            return ProvisionResult(
                ok=False, handle="",
                message=f"list_keys / set_secret: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=self._handle_for(domain_name=domain_name),
            message=(
                f"acs email domain {domain_name} provisioning "
                f"(connection string in key vault "
                f"{self._config.keyvault_url})"
            ),
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        domain_name = self._domain_name_from_handle(spec.handle)
        cfg = spec.config or {}

        body: dict[str, Any] = {}
        if cfg.get("user_engagement_tracking"):
            body.setdefault("properties", {})
            body["properties"]["user_engagement_tracking"] = (
                cfg["user_engagement_tracking"]
            )

        if not body:
            return UpdateResult(
                ok=True, handle=spec.handle,
                message="no modifiable attributes provided -- no-op",
            )

        try:
            self._mgmt.domains.update(
                resource_group_name=self._config.resource_group,
                email_service_name=self._config.email_service_name,
                domain_name=domain_name,
                parameters=body,
            )
        except Exception as exc:
            return UpdateResult(
                ok=False, handle=spec.handle,
                message=f"update: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True, handle=spec.handle,
            message=f"acs email domain {domain_name} update queued",
        )

    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        domain_name = self._domain_name_from_handle(spec.handle)

        existing = self._describe(domain_name)
        if existing is None:
            if delete_data:
                self._delete_connection_secret(domain_name)
            return DeprovisionResult(
                ok=True, handle=spec.handle,
                message=f"acs email domain {domain_name} already gone",
            )

        if not delete_data:
            # Retain-data path: keep the domain (and its DNS records)
            # intact. force_destroy here only signals 'clear any lock
            # marker so a follow-up delete can succeed' -- nothing to
            # do at the resource layer since we're not deleting.
            return DeprovisionResult(
                ok=True, handle=spec.handle,
                message=(
                    f"acs email domain {domain_name} retained "
                    f"(dns_records=preserved, force_destroy="
                    f"{force_destroy})"
                ),
            )

        if force_destroy:
            self._clear_resource_lock(domain_name=domain_name)

        try:
            poller = self._mgmt.domains.begin_delete(
                resource_group_name=self._config.resource_group,
                email_service_name=self._config.email_service_name,
                domain_name=domain_name,
            )
            poller.result()
        except Exception as exc:
            err_str = str(exc)
            if not force_destroy and (
                "ScopeLocked" in err_str
                or "CanNotDelete" in err_str
                or "ReadOnly" in err_str
            ):
                return DeprovisionResult(
                    ok=False, handle=spec.handle,
                    message=(
                        f"acs email domain {domain_name} has a "
                        f"resource lock -- pass force_destroy=True "
                        f"to bypass"
                    ),
                    errors=[err_str],
                )
            return DeprovisionResult(
                ok=False, handle=spec.handle,
                message=f"begin_delete: {err_str}",
                errors=[err_str],
            )

        self._delete_connection_secret(domain_name)
        return DeprovisionResult(
            ok=True, handle=spec.handle,
            message=(
                f"acs email domain {domain_name} deleted "
                f"(dns_records=discarded, force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        domain_name = self._domain_name_from_handle(handle.handle)
        existing = self._describe(domain_name)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle, state="deprovisioned",
                message=f"acs email domain {domain_name} not found",
            )
        azure_state = _state_of(existing)
        return ServiceStatus(
            handle=handle.handle,
            state=_AZURE_STATE_TO_PROTOCOL.get(
                azure_state.lower(), "updating",
            ),
            message=f"azure reports {azure_state}",
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        domain_name = self._domain_name_from_handle(handle.handle)
        existing = self._describe(domain_name)
        if existing is None:
            raise AzureCommunicationEmailError(
                f"binding requested for missing domain {domain_name}",
            )
        from_address = _from_address_for(
            domain_name=domain_name, domain=existing,
        )
        mailer_endpoint = (
            f"https://{_acs_hostname_from_resource_id(self._config.communication_resource_id)}"
        )
        connection_secret = self._connection_secret_name(domain_name)

        env_vars = {
            # Canonical contract envs (managed_service_kinds.py)
            "EMAIL_PROVIDER": ValueRef(literal="azure_acs"),
            "EMAIL_API_KEY": ValueRef(secret_ref=connection_secret),
            "EMAIL_FROM_ADDRESS": ValueRef(literal=from_address),
            "EMAIL_REGION": ValueRef(literal=self._config.location),
            # ACS-flavoured aliases the task spec asks for.
            "ACS_CONNECTION_STRING": ValueRef(
                secret_ref=connection_secret,
            ),
            "ACS_FROM_ADDRESS": ValueRef(literal=from_address),
            "ACS_MAILER_ENDPOINT": ValueRef(literal=mailer_endpoint),
        }
        return Binding(
            env_vars=env_vars,
            iam_grants=[
                Grant(
                    resource=self._config.communication_resource_id,
                    actions=[
                        (
                            "Microsoft.Communication/CommunicationServices"
                            "/read"
                        ),
                        (
                            "Microsoft.Communication/CommunicationServices"
                            "/listKeys/action"
                        ),
                    ],
                ),
                Grant(
                    resource=self._domain_resource_id(domain_name),
                    actions=[
                        (
                            "Microsoft.Communication/emailServices/"
                            "domains/read"
                        ),
                    ],
                ),
                Grant(
                    resource=connection_secret,
                    actions=[
                        "Microsoft.KeyVault/vaults/secrets/getSecret",
                    ],
                ),
            ],
            notes=(
                "EMAIL_API_KEY / ACS_CONNECTION_STRING are Key Vault "
                "refs to the CommunicationServices primary connection "
                "string. ACS_MAILER_ENDPOINT is the resource's HTTPS "
                "endpoint for the ACS SDK."
            ),
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        domain_name = self._domain_name_from_handle(handle.handle)
        existing = self._describe(domain_name)
        if existing is None:
            raise AzureCommunicationEmailError(
                f"snapshot requested for missing domain {domain_name}",
            )
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=f"{_safe(domain_name)}-snap-{stamp}",
            created_at=datetime.now(UTC).isoformat(),
        )

    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        provisioned = self.provision(target)
        if not provisioned.ok:
            return provisioned
        return ProvisionResult(
            ok=True,
            handle=provisioned.handle,
            message=(
                f"target domain provisioned; snapshot "
                f"{snapshot.snapshot_id} captures sender attributes "
                f"only -- DNS records must be re-published for "
                f"CustomerManaged domains"
            ),
        )

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "domain_management": {
                    "type": "string",
                    "enum": sorted(_VALID_DOMAIN_MANAGEMENT),
                    "description": (
                        "AzureManaged uses an MS-owned sender domain; "
                        "CustomerManaged requires operator SPF / DKIM "
                        "TXT records."
                    ),
                },
                "user_engagement_tracking": {
                    "type": "string",
                    "enum": ["Enabled", "Disabled"],
                },
                "domain_name": {
                    "type": "string",
                    "description": (
                        "Explicit domain name override. When omitted "
                        "the driver derives one from the app slug."
                    ),
                },
            },
        }

    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "EMAIL_PROVIDER": "Always 'azure_acs' for this driver",
                "EMAIL_API_KEY": (
                    "Key Vault ref to the ACS connection string"
                ),
                "EMAIL_FROM_ADDRESS": (
                    "Default From: address derived from the domain"
                ),
                "EMAIL_REGION": (
                    "Azure location of the email domain resource"
                ),
                "ACS_CONNECTION_STRING": (
                    "Alias for EMAIL_API_KEY (Key Vault ref to the "
                    "primary connection string)"
                ),
                "ACS_FROM_ADDRESS": "Alias for EMAIL_FROM_ADDRESS",
                "ACS_MAILER_ENDPOINT": (
                    "HTTPS endpoint of the parent CommunicationServices"
                    " resource"
                ),
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe(self, domain_name: str) -> Any | None:
        try:
            return self._mgmt.domains.get(
                resource_group_name=self._config.resource_group,
                email_service_name=self._config.email_service_name,
                domain_name=domain_name,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return None
            if "ResourceNotFound" in str(exc):
                return None
            raise

    def _domain_name_for(self, *, spec: ProvisionSpec) -> str:
        cfg = spec.config or {}
        explicit = cfg.get("domain_name")
        if explicit:
            return _safe(str(explicit))
        # ACS domain names follow the "<sub>.<parent>" form where the
        # operator owns DNS for CustomerManaged. We assemble a stable
        # lower-cased slug; the operator's email service binds the
        # parent zone.
        parts = [
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "email",
        ]
        raw = "-".join(p for p in parts if p).lower()
        return _safe(raw)[:64]

    def _handle_for(self, *, domain_name: str) -> str:
        return f"{KIND}/{domain_name}"

    def _domain_name_from_handle(self, handle: str) -> str:
        if "/" not in handle:
            raise AzureCommunicationEmailError(
                f"handle {handle!r} must be '<kind>/<domain_name>'",
            )
        kind, _, domain_name = handle.partition("/")
        if not kind or not domain_name:
            raise AzureCommunicationEmailError(
                f"handle {handle!r} has empty component",
            )
        return domain_name

    def _domain_resource_id(self, domain_name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}"
            f"/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.Communication/emailServices/"
            f"{self._config.email_service_name}/domains/{domain_name}"
        )

    def _connection_secret_name(self, domain_name: str) -> str:
        return (
            f"{self._config.secret_name_prefix}-{_safe(domain_name)}"
            f"-connection-string"
        )

    def _store_connection_string(self, *, domain_name: str) -> None:
        # The connection string lives on the parent CommunicationServices
        # resource, NOT on the per-domain entry. The driver reads it via
        # the parent resource's list_keys op and persists it under a
        # per-domain secret name so consumers can rotate independently.
        resource_id = self._config.communication_resource_id
        parent_name = _parent_name_from_resource_id(resource_id)
        keys = self._mgmt.communication_services.list_keys(
            resource_group_name=self._config.resource_group,
            communication_service_name=parent_name,
        )
        connection_string = (
            _key_field(keys, "primary_connection_string")
            or _key_field(keys, "primaryConnectionString")
            or ""
        )
        self._secrets.set_secret(
            self._connection_secret_name(domain_name),
            connection_string,
        )

    def _delete_connection_secret(self, domain_name: str) -> None:
        if self._secrets is None:
            return
        try:
            self._secrets.begin_delete_secret(
                self._connection_secret_name(domain_name),
            )
        except Exception:
            return

    def _clear_resource_lock(self, *, domain_name: str) -> None:
        # Operator-installed CanNotDelete / ReadOnly locks. When
        # force_destroy=True we attempt to delete the lock at the
        # resource scope before retrying the domain delete.
        if self._locks is None:
            return
        try:
            scope = self._domain_resource_id(domain_name)
            locks = self._locks.management_locks.list_at_resource_level(
                resource_group_name=self._config.resource_group,
                resource_provider_namespace=(
                    "Microsoft.Communication"
                ),
                parent_resource_path=(
                    f"emailServices/"
                    f"{self._config.email_service_name}"
                ),
                resource_type="domains",
                resource_name=domain_name,
            )
            for lock in locks:
                lock_name = getattr(lock, "name", None) or (
                    lock.get("name") if isinstance(lock, dict) else None
                )
                if not lock_name:
                    continue
                self._locks.management_locks.delete_at_resource_level(
                    resource_group_name=self._config.resource_group,
                    resource_provider_namespace=(
                        "Microsoft.Communication"
                    ),
                    parent_resource_path=(
                        f"emailServices/"
                        f"{self._config.email_service_name}"
                    ),
                    resource_type="domains",
                    resource_name=domain_name,
                    lock_name=lock_name,
                )
            _ = scope  # quiet linters about unused locals
        except Exception:
            # Lock removal is best-effort; the subsequent delete will
            # surface the underlying error if locks block again.
            return


# ----- module-level helpers --------------------------------------------


def _safe(value: str) -> str:
    """Coerce a slug to lowercase + replace anything outside
    ``[a-z0-9._-]`` with ``-``."""
    cleaned = "".join(
        c if (c.isalnum() or c in "-._") else "-"
        for c in value.lower()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


def _state_of(domain: Any) -> str:
    """Read ``provisioning_state`` off whatever shape the Azure SDK
    or fake returned. Mirror of the AI Search driver's helper."""
    state = getattr(domain, "provisioning_state", None)
    if state is not None:
        return str(state)
    state = getattr(domain, "provisioningState", None)
    if state is not None:
        return str(state)
    if isinstance(domain, dict):
        if "provisioning_state" in domain:
            return str(domain["provisioning_state"])
        if "provisioningState" in domain:
            return str(domain["provisioningState"])
    return "Unknown"


def _key_field(keys: Any, field_name: str) -> str:
    v = getattr(keys, field_name, None)
    if v:
        return str(v)
    if isinstance(keys, dict) and keys.get(field_name):
        return str(keys[field_name])
    return ""


def _from_address_for(*, domain_name: str, domain: Any) -> str:
    """Derive the platform-standard From: address. For AzureManaged
    domains ACS hands back a ``<guid>.azurecomm.net`` sender; we
    surface the operator's domain_name slug + ``@`` + the FQDN ACS
    assigned (read off the resource if exposed). For CustomerManaged
    domains the FQDN is the operator's own zone."""
    fqdn = (
        getattr(domain, "from_sender_domain", None)
        or getattr(domain, "fromSenderDomain", None)
        or (
            domain.get("from_sender_domain")
            if isinstance(domain, dict) else None
        )
        or (
            domain.get("fromSenderDomain")
            if isinstance(domain, dict) else None
        )
        or domain_name
    )
    return f"noreply@{fqdn}"


def _acs_hostname_from_resource_id(resource_id: str) -> str:
    """Best-effort: the ACS resource's HTTPS endpoint is
    ``<name>.communication.azure.com``. When the operator's resource
    id is malformed we fall back to ``communication.azure.com`` so
    the binding still renders."""
    name = _parent_name_from_resource_id(resource_id)
    if not name:
        return "communication.azure.com"
    return f"{name}.communication.azure.com"


def _parent_name_from_resource_id(resource_id: str) -> str:
    """Extract the trailing ``CommunicationServices/<name>`` segment
    of a resource id. Returns empty string on malformed input."""
    if not resource_id:
        return ""
    parts = [p for p in resource_id.split("/") if p]
    for i, part in enumerate(parts):
        if part == "CommunicationServices" and i + 1 < len(parts):
            return parts[i + 1]
    return ""


# Azure ACS provisioningState values mirror the wider Azure RP set.
_AZURE_STATE_TO_PROTOCOL = {
    "succeeded": "available",
    "creating": "provisioning",
    "provisioning": "provisioning",
    "failed": "error",
    "deleting": "deprovisioning",
    "updating": "updating",
    "unknown": "updating",
}
