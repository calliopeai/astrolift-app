"""Tests for AzureCommunicationEmailDriver (#375).

Mirrors the AzureAISearchFullTextDriver test surface: provision
(idempotent, tagged, connection string persisted to Key Vault),
update, deprovision four-corner matrix (delete_data x
force_destroy with resource-lock bypass), status state-mapping,
binding env-var shape, snapshot + restore.

Uses in-process fakes for the CommunicationServiceManagementClient,
ManagementLockClient + Key Vault SecretClient -- no Azure account
required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk import UnsupportedOperationError
from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from azure.managed.email_acs import (
    KIND,
    AzureCommunicationEmailConfig,
    AzureCommunicationEmailDriver,
    AzureCommunicationEmailError,
    _acs_hostname_from_resource_id,
    _from_address_for,
    _parent_name_from_resource_id,
    _safe,
)

# ---- fakes ------------------------------------------------------


class _NotFound(Exception):
    pass


_NotFound.__name__ = "ResourceNotFoundError"


@dataclass
class FakePoller:
    value: Any = None

    def result(self) -> Any:
        return self.value


@dataclass
class FakeDomain:
    name: str
    provisioning_state: str = "Succeeded"
    domain_management: str = "AzureManaged"
    from_sender_domain: str = ""
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeConnectionKeys:
    primary_connection_string: str = "endpoint=https://acme.communication.azure.com/;accesskey=zzzzzzzzzzzzzz"
    secondary_connection_string: str = "endpoint=https://acme.communication.azure.com/;accesskey=yyyyyyyyyyyyyy"


@dataclass
class FakeDomainsOperations:
    domains: dict[str, FakeDomain] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def get(
        self,
        *,
        resource_group_name: str,
        email_service_name: str,
        domain_name: str,
    ) -> FakeDomain:
        if domain_name not in self.domains:
            raise _NotFound(domain_name)
        return self.domains[domain_name]

    def begin_create_or_update(
        self,
        *,
        resource_group_name: str,
        email_service_name: str,
        domain_name: str,
        parameters: dict[str, Any],
    ) -> FakePoller:
        self.create_calls.append(
            {"name": domain_name, "parameters": parameters},
        )
        props = parameters.get("properties") or {}
        d = FakeDomain(
            name=domain_name,
            provisioning_state="Succeeded",
            domain_management=props.get(
                "domain_management",
                "AzureManaged",
            ),
            from_sender_domain=domain_name,
            properties=dict(props),
        )
        self.domains[domain_name] = d
        return FakePoller(value=d)

    def begin_update(
        self,
        *,
        resource_group_name: str,
        email_service_name: str,
        domain_name: str,
        parameters: dict[str, Any],
    ) -> FakePoller:
        self.update_calls.append(
            {"name": domain_name, "parameters": parameters},
        )
        d = self.domains.get(domain_name)
        if d is None:
            raise _NotFound(domain_name)
        props = parameters.get("properties") or {}
        d.properties.update(props)
        return FakePoller(value=d)

    def begin_delete(
        self,
        *,
        resource_group_name: str,
        email_service_name: str,
        domain_name: str,
    ) -> FakePoller:
        self.delete_calls.append(domain_name)
        if domain_name not in self.domains:
            raise _NotFound(domain_name)
        del self.domains[domain_name]
        return FakePoller(value=None)


@dataclass
class FakeCommunicationServicesOperations:
    primary_connection_string: str = "endpoint=https://acme.communication.azure.com/;accesskey=primarykey"
    list_keys_calls: list[str] = field(default_factory=list)

    def list_keys(
        self,
        *,
        resource_group_name: str,
        communication_service_name: str,
    ) -> FakeConnectionKeys:
        self.list_keys_calls.append(communication_service_name)
        return FakeConnectionKeys(
            primary_connection_string=self.primary_connection_string,
        )


@dataclass
class FakeMgmtClient:
    domains_obj: FakeDomainsOperations = field(
        default_factory=FakeDomainsOperations,
    )
    comm_obj: FakeCommunicationServicesOperations = field(
        default_factory=FakeCommunicationServicesOperations,
    )

    @property
    def domains(self) -> FakeDomainsOperations:
        return self.domains_obj

    @property
    def communication_services(
        self,
    ) -> FakeCommunicationServicesOperations:
        return self.comm_obj


@dataclass
class FakeSecretClient:
    secrets: dict[str, str] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)

    def set_secret(self, name: str, value: str) -> None:
        self.secrets[name] = value

    def begin_delete_secret(self, name: str) -> Any:
        if name not in self.secrets:
            raise _NotFound(name)
        del self.secrets[name]
        self.deleted.append(name)
        return FakePoller(value=None)


@dataclass
class FakeLocksOperations:
    locks: dict[str, list[Any]] = field(default_factory=dict)
    list_calls: list[str] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def list_at_resource_level(
        self,
        *,
        resource_group_name: str,
        resource_provider_namespace: str,
        parent_resource_path: str,
        resource_type: str,
        resource_name: str,
    ) -> list[Any]:
        self.list_calls.append(resource_name)
        return self.locks.get(resource_name, [])

    def delete_at_resource_level(
        self,
        *,
        resource_group_name: str,
        resource_provider_namespace: str,
        parent_resource_path: str,
        resource_type: str,
        resource_name: str,
        lock_name: str,
    ) -> None:
        self.delete_calls.append(f"{resource_name}/{lock_name}")
        self.locks.pop(resource_name, None)


@dataclass
class FakeLocksClient:
    management_locks: FakeLocksOperations = field(
        default_factory=FakeLocksOperations,
    )


# ---- fixtures ---------------------------------------------------


_COMM_RESOURCE_ID = (
    "/subscriptions/sub-1/resourceGroups/rg-test/providers/Microsoft.Communication/CommunicationServices/acme"
)


@pytest.fixture
def mgmt() -> FakeMgmtClient:
    return FakeMgmtClient()


@pytest.fixture
def secrets_client() -> FakeSecretClient:
    return FakeSecretClient()


@pytest.fixture
def locks() -> FakeLocksClient:
    return FakeLocksClient()


@pytest.fixture
def driver(
    mgmt: FakeMgmtClient,
    secrets_client: FakeSecretClient,
    locks: FakeLocksClient,
) -> AzureCommunicationEmailDriver:
    return AzureCommunicationEmailDriver(
        config=AzureCommunicationEmailConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            email_service_name="astrolift-email",
            communication_resource_id=_COMM_RESOURCE_ID,
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=secrets_client,
            locks_client=locks,
        ),
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="azure-prod",
        service_handle_hint="email",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- constructor --------------------------------------------------


def test_constructor_requires_communication_resource_id(
    secrets_client: FakeSecretClient,
) -> None:
    with pytest.raises(AzureCommunicationEmailError):
        AzureCommunicationEmailDriver(
            config=AzureCommunicationEmailConfig(
                subscription_id="sub-1",
                resource_group="rg-test",
                keyvault_url="https://kv.vault.azure.net",
                mgmt_client=FakeMgmtClient(),
                secret_client=secrets_client,
            ),
        )


# ---- provision --------------------------------------------------


def test_provision_creates_domain(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    assert result.handle.startswith(f"{KIND}/")
    assert len(mgmt.domains_obj.domains) == 1


def test_provision_defaults_to_azure_managed(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec())
    create = mgmt.domains_obj.create_calls[0]
    assert create["parameters"]["properties"]["domain_management"] == "AzureManaged"


def test_provision_honours_customer_managed(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(
        _spec(config={"domain_management": "CustomerManaged"}),
    )
    create = mgmt.domains_obj.create_calls[0]
    assert create["parameters"]["properties"]["domain_management"] == "CustomerManaged"


def test_provision_rejects_unknown_domain_management(
    driver: AzureCommunicationEmailDriver,
) -> None:
    result = driver.provision(
        _spec(config={"domain_management": "GibberishManaged"}),
    )
    assert not result.ok
    assert "domain_management" in result.message


def test_provision_idempotent(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message
    assert len(mgmt.domains_obj.create_calls) == 1


def test_provision_persists_connection_string_in_key_vault(
    driver: AzureCommunicationEmailDriver,
    secrets_client: FakeSecretClient,
) -> None:
    result = driver.provision(_spec())
    domain_name = result.handle.split("/", 1)[1]
    secret_name = f"astrolift-acs-email-{_safe(domain_name)}-connection-string"
    assert secret_name in secrets_client.secrets
    assert secrets_client.secrets[secret_name].startswith("endpoint=")


def test_provision_tags_domain_with_astrolift_namespace(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec())
    tags = mgmt.domains_obj.create_calls[0]["parameters"]["tags"]
    assert tags["astrolift.io/managed-by"] == "platform"
    assert tags["astrolift.io/app"] == "api"
    assert tags["astrolift.io/environment"] == "prod"


def test_provision_explicit_domain_name(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
) -> None:
    result = driver.provision(
        _spec(config={"domain_name": "ACME-Hosted"}),
    )
    assert result.ok
    assert result.handle.endswith("/acme-hosted")
    assert "acme-hosted" in mgmt.domains_obj.domains


def test_provision_without_keyvault_returns_error() -> None:
    d = AzureCommunicationEmailDriver(
        config=AzureCommunicationEmailConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            communication_resource_id=_COMM_RESOURCE_ID,
            mgmt_client=FakeMgmtClient(),
        ),
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "Key Vault" in result.message


def test_provision_surfaces_create_failure(
    secrets_client: FakeSecretClient,
) -> None:
    mgmt = FakeMgmtClient()

    def boom(**_kwargs):
        raise RuntimeError("simulated azure failure")

    mgmt.domains_obj.begin_create_or_update = boom  # type: ignore[assignment]
    d = AzureCommunicationEmailDriver(
        config=AzureCommunicationEmailConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            communication_resource_id=_COMM_RESOURCE_ID,
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=secrets_client,
        ),
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "begin_create_or_update" in result.message


def test_provision_surfaces_connection_string_fetch_failure(
    secrets_client: FakeSecretClient,
) -> None:
    mgmt = FakeMgmtClient()

    def boom(**_kwargs):
        raise RuntimeError("list_keys boom")

    mgmt.comm_obj.list_keys = boom  # type: ignore[assignment]
    d = AzureCommunicationEmailDriver(
        config=AzureCommunicationEmailConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            communication_resource_id=_COMM_RESOURCE_ID,
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=secrets_client,
        ),
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "list_keys" in result.message


# ---- update -----------------------------------------------------


def test_update_user_engagement_tracking(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"user_engagement_tracking": "Enabled"},
        ),
    )
    assert result.ok
    last = mgmt.domains_obj.update_calls[-1]
    assert last["parameters"]["properties"]["user_engagement_tracking"] == "Enabled"


def test_update_noop_when_nothing_to_change(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    before = len(mgmt.domains_obj.update_calls)
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message
    assert len(mgmt.domains_obj.update_calls) == before


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_retains_domain_and_secret(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    domain_name = provisioned.handle.split("/", 1)[1]
    secret_name = f"astrolift-acs-email-{_safe(domain_name)}-connection-string"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert result.ok
    assert "dns_records=preserved" in result.message
    # Domain + secret retained on retained-data path
    assert domain_name in mgmt.domains_obj.domains
    assert secret_name in secrets_client.secrets
    assert domain_name not in mgmt.domains_obj.delete_calls


def test_deprovision_delete_data_drops_domain_and_secret(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    domain_name = provisioned.handle.split("/", 1)[1]
    secret_name = f"astrolift-acs-email-{_safe(domain_name)}-connection-string"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "dns_records=discarded" in result.message
    assert domain_name not in mgmt.domains_obj.domains
    assert secret_name not in secrets_client.secrets


def test_deprovision_force_destroy_only_retains_domain(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    domain_name = provisioned.handle.split("/", 1)[1]
    secret_name = f"astrolift-acs-email-{_safe(domain_name)}-connection-string"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=False,
        force_destroy=True,
    )
    assert result.ok
    assert "dns_records=preserved" in result.message
    assert domain_name in mgmt.domains_obj.domains
    assert secret_name in secrets_client.secrets


def test_deprovision_atomic_both_flags(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    domain_name = provisioned.handle.split("/", 1)[1]
    secret_name = f"astrolift-acs-email-{_safe(domain_name)}-connection-string"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "dns_records=discarded" in result.message
    assert domain_name not in mgmt.domains_obj.domains
    assert secret_name not in secrets_client.secrets


def test_deprovision_idempotent_when_already_gone(
    driver: AzureCommunicationEmailDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="email/never-existed"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_respects_resource_lock_without_force(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())

    def boom(**_kwargs):
        raise RuntimeError("ScopeLocked: CanNotDelete")

    mgmt.domains_obj.begin_delete = boom  # type: ignore[assignment]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert not result.ok
    assert "resource lock" in result.message


def test_deprovision_force_destroy_attempts_lock_removal(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
    locks: FakeLocksClient,
) -> None:
    provisioned = driver.provision(_spec())
    domain_name = provisioned.handle.split("/", 1)[1]

    @dataclass
    class FakeLock:
        name: str

    locks.management_locks.locks[domain_name] = [
        FakeLock(name="prod-lock"),
    ]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert domain_name in locks.management_locks.list_calls
    assert f"{domain_name}/prod-lock" in (locks.management_locks.delete_calls)


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: AzureCommunicationEmailDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="email/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_succeeded_to_available(
    driver: AzureCommunicationEmailDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


def test_status_maps_creating_to_provisioning(
    driver: AzureCommunicationEmailDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    domain_name = provisioned.handle.split("/", 1)[1]
    mgmt.domains_obj.domains[domain_name].provisioning_state = "Creating"
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "provisioning"


# ---- binding ----------------------------------------------------


def test_binding_returns_connection_envelope(
    driver: AzureCommunicationEmailDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    env = binding.env_vars
    for key in (
        "EMAIL_PROVIDER",
        "EMAIL_API_KEY",
        "EMAIL_FROM_ADDRESS",
        "EMAIL_REGION",
        "ACS_CONNECTION_STRING",
        "ACS_FROM_ADDRESS",
        "ACS_MAILER_ENDPOINT",
    ):
        assert key in env
    assert env["EMAIL_PROVIDER"].literal == "azure_acs"
    assert env["EMAIL_API_KEY"].secret_ref is not None
    assert env["EMAIL_API_KEY"].secret_ref.startswith("azure-kv://kv.vault.azure.net/secrets/")
    assert env["EMAIL_API_KEY"].literal is None
    assert env["ACS_CONNECTION_STRING"].secret_ref is not None
    assert env["ACS_MAILER_ENDPOINT"].literal == ("https://acme.communication.azure.com")
    assert env["EMAIL_FROM_ADDRESS"].literal.startswith("noreply@")


def test_binding_iam_grants_cover_service_and_secret(
    driver: AzureCommunicationEmailDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    actions = {a for g in binding.iam_grants for a in g.actions}
    assert any("Microsoft.Communication" in a for a in actions)
    assert any("KeyVault" in a for a in actions)
    assert any("listKeys" in a for a in actions)


def test_binding_for_missing_raises(
    driver: AzureCommunicationEmailDriver,
) -> None:
    with pytest.raises(AzureCommunicationEmailError):
        driver.binding(ServiceHandle(handle="email/missing"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_is_explicitly_unsupported(
    driver: AzureCommunicationEmailDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError, match="no snapshot API"):
        driver.snapshot(ServiceHandle(handle="email/anything"))


def test_restore_is_explicitly_unsupported(
    driver: AzureCommunicationEmailDriver,
) -> None:
    from _sdk.managed_service import SnapshotHandle

    snapshot = SnapshotHandle("email/source", "not-a-backup", "2026-08-14T00:00:00Z")
    with pytest.raises(UnsupportedOperationError, match="cannot be restored"):
        driver.restore(snapshot, _spec(service_handle_hint="restored"))


# ---- naming + helpers -------------------------------------------


def test_domain_name_canonicalization(
    driver: AzureCommunicationEmailDriver,
) -> None:
    name = driver._domain_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="email",
        ),
    )
    assert len(name) <= 64
    for c in name:
        assert c.islower() or c.isdigit() or c in "-._"
    assert "--" not in name


def test_handle_round_trip(
    driver: AzureCommunicationEmailDriver,
) -> None:
    handle = driver._handle_for(domain_name="my-mail")  # type: ignore[attr-defined]
    assert handle.startswith(f"{KIND}/")
    assert driver._domain_name_from_handle(handle) == "my-mail"  # type: ignore[attr-defined]


def test_handle_rejects_malformed(
    driver: AzureCommunicationEmailDriver,
) -> None:
    with pytest.raises(AzureCommunicationEmailError):
        driver._domain_name_from_handle("no-slash")  # type: ignore[attr-defined]
    with pytest.raises(AzureCommunicationEmailError):
        driver._domain_name_from_handle("email/")  # type: ignore[attr-defined]


def test_parent_name_from_resource_id() -> None:
    assert _parent_name_from_resource_id(_COMM_RESOURCE_ID) == "acme"
    assert _parent_name_from_resource_id("") == ""
    assert _parent_name_from_resource_id("/bogus/path") == ""


def test_acs_hostname_falls_back_when_id_malformed() -> None:
    assert _acs_hostname_from_resource_id("") == ("communication.azure.com")
    assert _acs_hostname_from_resource_id(_COMM_RESOURCE_ID) == ("acme.communication.azure.com")


def test_from_address_for_uses_sender_domain_attr() -> None:
    class _D:
        from_sender_domain = "subdomain.example.com"

    addr = _from_address_for(domain_name="x", domain=_D())
    assert addr == "noreply@subdomain.example.com"


def test_from_address_for_dict_fallback() -> None:
    addr = _from_address_for(
        domain_name="x",
        domain={"fromSenderDomain": "alt.example.com"},
    )
    assert addr == "noreply@alt.example.com"


def test_safe_strips_unsafe_chars() -> None:
    assert _safe("Hello World!!") == "hello-world"


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(
    driver: AzureCommunicationEmailDriver,
) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "domain_management",
        "user_engagement_tracking",
        "domain_name",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: AzureCommunicationEmailDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "EMAIL_PROVIDER",
        "EMAIL_API_KEY",
        "EMAIL_FROM_ADDRESS",
        "EMAIL_REGION",
        "ACS_CONNECTION_STRING",
        "ACS_FROM_ADDRESS",
        "ACS_MAILER_ENDPOINT",
    ):
        assert key in schema.env_vars
