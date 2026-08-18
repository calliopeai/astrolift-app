"""Tests for the Azure Key Vault key driver (#1454).

``encryption_key`` is the kind whose whole point is that the key outlives the
binding, so the cases that matter are the refusals: adopting somebody else's
key, changing a key's immutable shape in place, and destroying material that
deletion protection still covers.
"""

from __future__ import annotations

import base64
import copy
from types import SimpleNamespace
from typing import Any

import pytest
from astrolift_manifest.env_injection import envelope_keys_for

from _sdk.availability import MATRIX
from _sdk.azure_ownership import OWNERSHIP_ERROR_CODE
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, SnapshotHandle, UpdateSpec
from azure.managed.encryption_key_vault import (
    _ACCESS_MODE_ROLES,
    AzureKeyVaultKeyConfig,
    AzureKeyVaultKeyDriver,
    AzureKeyVaultKeyError,
)
from azure.plugin import PLUGIN
from azure.role_catalog import AZURE_BUILTIN_ROLE_IDS, RoleGrant, resolve_grant_action, validate_arm_scope

SUBSCRIPTION = "00000000-1111-2222-3333-444444444444"
RESOURCE_GROUP = "rg-platform"
VAULT_URL = "https://platform-prod.vault.azure.net"
VAULT_NAME = "platform-prod"
MANAGED_BY_TAG = "astrolift-managed-by"
OWNER_TAG = "astrolift-managed-service-id"
DEFAULT_OPS = ["decrypt", "encrypt", "sign", "unwrapKey", "verify", "wrapKey"]


class _NotFound(Exception):
    status_code = 404


def _modulus(bits: int) -> str:
    return base64.urlsafe_b64encode(b"\xab" * (bits // 8)).decode().rstrip("=")


class FakeKeyVault:
    """In-memory stand-in for the Key Vault data-plane surface the driver uses."""

    def __init__(self) -> None:
        self.keys: dict[str, dict[str, Any]] = {}
        self.deleted: dict[str, dict[str, Any]] = {}
        self.policies: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[tuple[str, dict[str, Any]]] = []

    def get_key(self, name: str) -> dict[str, Any]:
        if name not in self.keys:
            raise _NotFound(name)
        return copy.deepcopy(self.keys[name])

    def create_key(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("create", name))
        self.bodies.append(("create", copy.deepcopy(body)))
        material: dict[str, Any] = {
            "kty": body["kty"],
            "kid": f"{VAULT_URL}/keys/{name}/0123456789abcdef0123456789abcdef",
            "key_ops": list(body.get("key_ops") or DEFAULT_OPS),
        }
        if "key_size" in body:
            material["n"] = _modulus(int(body["key_size"]))
            material["e"] = "AQAB"
        if "crv" in body:
            material["crv"] = body["crv"]
        self.keys[name] = {
            "key": material,
            "attributes": dict(body.get("attributes") or {}),
            "tags": dict(body.get("tags") or {}),
        }
        return copy.deepcopy(self.keys[name])

    def update_key(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("update", name))
        self.bodies.append(("update", copy.deepcopy(body)))
        if name not in self.keys:
            raise _NotFound(name)
        key = self.keys[name]
        key["attributes"].update(body.get("attributes") or {})
        if "tags" in body:
            key["tags"] = dict(body["tags"])
        if "key_ops" in body:
            key["key"]["key_ops"] = list(body["key_ops"])
        return copy.deepcopy(key)

    def delete_key(self, name: str) -> dict[str, Any]:
        self.calls.append(("delete", name))
        if name not in self.keys:
            raise _NotFound(name)
        self.deleted[name] = self.keys.pop(name)
        return copy.deepcopy(self.deleted[name])

    def purge_deleted_key(self, name: str) -> None:
        self.calls.append(("purge", name))
        if name not in self.deleted:
            raise _NotFound(name)
        self.deleted.pop(name)

    def get_rotation_policy(self, name: str) -> dict[str, Any]:
        if name not in self.policies:
            raise _NotFound(name)
        return copy.deepcopy(self.policies[name])

    def set_rotation_policy(self, name: str, policy: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("rotation", name))
        self.bodies.append(("rotation", copy.deepcopy(policy)))
        stored = copy.deepcopy(policy)
        # The service echoes fields it owns; the driver must not treat them as drift.
        stored["id"] = f"{VAULT_URL}/keys/{name}/rotationpolicy"
        stored.setdefault("attributes", {})["created"] = 1_700_000_000
        self.policies[name] = stored
        return copy.deepcopy(stored)


def _spec(**overrides: Any) -> ProvisionSpec:
    values: dict[str, Any] = {
        "organization_id": "org-1",
        "organization_slug": "steadymd",
        "app_id": "app-1",
        "app_slug": "triage",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": "aks-prod",
        "service_handle_hint": "records",
        "size": "custom",
        "config": {},
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _driver(vault: FakeKeyVault | None = None, **policy: Any) -> tuple[AzureKeyVaultKeyDriver, FakeKeyVault]:
    client = vault or FakeKeyVault()
    values: dict[str, Any] = {
        "subscription_id": SUBSCRIPTION,
        "resource_group": RESOURCE_GROUP,
        "vault_url": VAULT_URL,
    }
    values.update(policy)
    return AzureKeyVaultKeyDriver(config=AzureKeyVaultKeyConfig(**values), client=client), client


def _provisioned(**overrides: Any) -> tuple[AzureKeyVaultKeyDriver, FakeKeyVault, str]:
    driver, vault = _driver()
    result = driver.provision(_spec(**overrides))
    assert result.ok, result.message
    return driver, vault, result.handle


def _key_name(handle: str) -> str:
    return handle.split("/keys/", 1)[1]


def test_provision_creates_the_key_with_an_ownership_envelope_and_a_rotation_policy() -> None:
    driver, vault, handle = _provisioned()

    name = _key_name(handle)
    assert handle.startswith(f"encryption_key/{VAULT_NAME}/keys/astrolift-steadymd-triage-prod-records-")
    create = next(body for kind, body in vault.bodies if kind == "create")
    assert create["kty"] == "RSA"
    assert create["key_size"] == 2048
    assert create["attributes"] == {"enabled": True}
    assert create["tags"][MANAGED_BY_TAG] == "platform"
    assert create["tags"][OWNER_TAG] == "service-1"
    policy = vault.policies[name]
    assert {row["action"]["type"] for row in policy["lifetimeActions"]} == {"Rotate", "Notify"}
    assert policy["attributes"]["expiryTime"] == "P90D"
    assert driver.status(ServiceHandle(handle)).state == "available"


def test_provision_replays_without_recreating_the_key_or_rewriting_the_policy() -> None:
    """Temporal retries provision. A rewrite on every replay would churn the
    rotation policy and reset the tags the ownership gate reads."""
    driver, vault, handle = _provisioned()
    vault.calls.clear()

    again = driver.provision(_spec())

    assert again.ok and again.handle == handle
    assert vault.calls == [], f"replay issued provider writes: {vault.calls}"


def test_rotation_off_leaves_the_service_default_policy_alone() -> None:
    """Key Vault answers a key that never had a policy with a Notify-only
    default of its own. Reading that as drift rewrites the policy on every
    single reconcile, forever, because the service keeps putting it back."""
    driver, vault = _driver()
    first = driver.provision(_spec(config={"rotation_enabled": False}))
    name = _key_name(first.handle)
    assert ("rotation", name) not in vault.calls

    vault.policies[name] = {
        "id": f"{VAULT_URL}/keys/{name}/rotationpolicy",
        "lifetimeActions": [{"trigger": {"timeBeforeExpiry": "P30D"}, "action": {"type": "Notify"}}],
        "attributes": {"created": 1_700_000_000},
    }
    vault.calls.clear()

    driver.provision(_spec(config={"rotation_enabled": False}))

    assert vault.calls == []


def test_turning_rotation_on_writes_a_rotate_action_and_then_settles() -> None:
    driver, vault = _driver()
    result = driver.provision(_spec(config={"rotation_enabled": False}))
    name = _key_name(result.handle)

    driver.update(UpdateSpec(result.handle, config={"rotation_period": "P30D"}, managed_service_id="service-1"))
    vault.calls.clear()
    driver.update(UpdateSpec(result.handle, config={"rotation_period": "P30D"}, managed_service_id="service-1"))

    policy = vault.policies[name]
    assert policy["attributes"]["expiryTime"] == "P30D"
    assert {row["action"]["type"] for row in policy["lifetimeActions"]} == {"Rotate", "Notify"}
    assert ("rotation", name) not in vault.calls, "a settled policy was rewritten"


def test_provision_refuses_to_adopt_a_key_another_managed_service_owns() -> None:
    driver, vault, handle = _provisioned()
    vault.keys[_key_name(handle)]["tags"][OWNER_TAG] = "some-other-service"
    vault.calls.clear()

    collision = driver.provision(_spec())

    assert not collision.ok
    assert collision.errors == [OWNERSHIP_ERROR_CODE]
    assert "some-other-service" in collision.message
    assert vault.calls == [], "refused the adoption but wrote to the key anyway"


def test_provision_refuses_an_immutable_shape_change_instead_of_reusing_the_old_key() -> None:
    """A key's type, size, and curve are fixed at creation. Silently reusing the
    old key would hand the app a key that does not match its manifest."""
    driver, vault, handle = _provisioned()
    vault.calls.clear()

    resized = driver.provision(_spec(config={"key_type": "RSA", "key_size": 4096}))
    retyped = driver.provision(_spec(config={"key_type": "EC", "curve": "P-256"}))

    assert not resized.ok and "key size is immutable" in resized.message
    assert not retyped.ok and "key type is immutable" in retyped.message
    assert vault.keys[_key_name(handle)]["key"]["kty"] == "RSA"
    assert vault.calls == []


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"native": {}}, "unsupported fields"),
        ({"key_type": "oct"}, "key_type must be one of"),
        ({"key_type": "RSA", "curve": "P-256"}, "curve is only valid for EC"),
        ({"key_type": "EC", "key_size": 2048}, "key_size is only valid for RSA"),
        ({"key_size": 1024}, "key_size must be one of"),
        ({"key_operations": ["encrypt", "encrypt"]}, "key_operations must be"),
        ({"key_operations": ["derive"]}, "key_operations must be"),
        ({"rotation_period": "P14D"}, "at least 28 days"),
        ({"rotation_period": "90 days"}, "ISO 8601 duration"),
        ({"access_mode": "root"}, "access_mode must be one of"),
        (
            {"expires_on": "2030-01-01T00:00:00Z", "not_before": "2031-01-01T00:00:00Z"},
            "expires_on must be later",
        ),
        ({"expires_on": "next tuesday"}, "RFC 3339"),
    ],
)
def test_config_policy_fails_closed_before_any_provider_call(config: dict[str, Any], message: str) -> None:
    driver, vault = _driver()

    result = driver.provision(_spec(config=config))

    assert not result.ok
    assert result.errors == ["invalid_azure_key_vault_key_config"]
    assert message in result.message
    assert vault.calls == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"subscription_id": ""}, "install subscription and resource group"),
        ({"resource_group": ""}, "install subscription and resource group"),
        ({"vault_url": "http://platform-prod.vault.azure.net"}, "HTTPS origin"),
        ({"vault_url": "https://platform-prod.vault.azure.net/keys"}, "HTTPS origin"),
        ({"vault_url": "https://xy.vault.azure.net"}, "3-24 character vault"),
        ({"key_name_prefix": "astro lift"}, "key_name_prefix"),
    ],
)
def test_an_invalid_install_refuses_rather_than_deriving_a_name_anyway(
    overrides: dict[str, Any],
    message: str,
) -> None:
    driver, vault = _driver(**overrides)

    result = driver.provision(_spec())

    assert not result.ok
    assert message in result.message
    assert vault.calls == []


def test_a_handle_from_another_vault_is_refused() -> None:
    """Handles are stored on the row and outlive an install's settings. Acting on
    one that names a different vault would operate on the wrong key silently."""
    driver, vault, handle = _provisioned()
    foreign = handle.replace(f"/{VAULT_NAME}/", "/other-vault/")

    result = driver.deprovision(
        DeprovisionSpec(foreign, managed_service_id="service-1"),
        delete_data=True,
        force_destroy=True,
    )

    assert not result.ok
    assert result.errors == ["invalid_handle"]
    assert not result.retryable
    assert vault.keys


def test_deprovision_without_delete_data_disables_the_key_and_keeps_the_material() -> None:
    driver, vault, handle = _provisioned()

    result = driver.deprovision(
        DeprovisionSpec(handle, config={"deletion_protection": False}, managed_service_id="service-1"),
    )

    assert result.ok
    assert "material is retained" in result.message
    assert vault.keys[_key_name(handle)]["attributes"]["enabled"] is False
    assert not vault.deleted


def test_deletion_protection_blocks_teardown_until_force_destroy() -> None:
    driver, vault, handle = _provisioned()

    protected = driver.deprovision(DeprovisionSpec(handle, managed_service_id="service-1"), delete_data=True)

    assert not protected.ok
    assert protected.errors == ["deletion_protection_enabled"]
    assert not protected.retryable
    assert vault.keys and not vault.deleted

    forced = driver.deprovision(
        DeprovisionSpec(handle, managed_service_id="service-1"),
        delete_data=True,
        force_destroy=True,
    )

    assert forced.ok
    assert _key_name(handle) in vault.deleted


def test_delete_data_soft_deletes_and_purges_only_when_the_binding_asks() -> None:
    """Soft delete is the recoverable state Key Vault always leaves behind.
    Purging destroys the material outright, so it needs its own opt-in."""
    driver, vault, handle = _provisioned()
    name = _key_name(handle)

    kept = driver.deprovision(
        DeprovisionSpec(handle, config={"deletion_protection": False}, managed_service_id="service-1"),
        delete_data=True,
    )

    assert kept.ok and "purge_on_delete" in kept.message
    assert name in vault.deleted

    purged_driver, purged_vault, purged_handle = _provisioned(service_handle_hint="ledger")
    purged = purged_driver.deprovision(
        DeprovisionSpec(
            purged_handle,
            config={"deletion_protection": False, "purge_on_delete": True},
            managed_service_id="service-1",
        ),
        delete_data=True,
    )

    assert purged.ok and "purged" in purged.message
    assert not purged_vault.deleted and not purged_vault.keys


def test_teardown_replay_after_the_key_is_gone_stays_successful() -> None:
    driver, _vault, handle = _provisioned()
    spec = DeprovisionSpec(handle, config={"deletion_protection": False}, managed_service_id="service-1")

    first = driver.deprovision(spec, delete_data=True)
    second = driver.deprovision(spec, delete_data=True)

    assert first.ok and second.ok
    assert "already absent" in second.message


def test_update_reconciles_attributes_and_rotation_and_refuses_a_foreign_owner() -> None:
    driver, vault, handle = _provisioned()
    name = _key_name(handle)
    config = {
        "enabled": False,
        "key_operations": ["decrypt", "encrypt"],
        "rotation_enabled": False,
    }

    updated = driver.update(UpdateSpec(handle, config=config, managed_service_id="service-1"))

    assert updated.ok
    assert vault.keys[name]["attributes"]["enabled"] is False
    assert vault.keys[name]["key"]["key_ops"] == ["decrypt", "encrypt"]
    assert vault.policies[name]["lifetimeActions"] == []
    assert vault.keys[name]["tags"][OWNER_TAG] == "service-1", "update must not rewrite the ownership envelope"

    vault.keys[name]["tags"][OWNER_TAG] = "some-other-service"
    refused = driver.update(UpdateSpec(handle, config=config, managed_service_id="service-1"))

    assert not refused.ok
    assert refused.errors == [OWNERSHIP_ERROR_CODE]
    assert not refused.retryable


def test_status_separates_an_absent_key_from_a_disabled_one() -> None:
    driver, vault, handle = _provisioned()
    name = _key_name(handle)

    vault.keys[name]["attributes"]["enabled"] = False
    disabled = driver.status(ServiceHandle(handle))
    vault.keys.pop(name)
    absent = driver.status(ServiceHandle(handle))

    assert disabled.state == "available" and "disabled" in disabled.message
    assert absent.state == "deprovisioned"


def test_status_and_binding_refuse_a_key_astrolift_did_not_create() -> None:
    driver, vault, handle = _provisioned()
    vault.keys[_key_name(handle)]["tags"][MANAGED_BY_TAG] = "terraform"

    assert driver.status(ServiceHandle(handle)).state == "error"
    with pytest.raises(Exception, match="carries no Astrolift astrolift-managed-by=platform"):
        driver.binding(ServiceHandle(handle))


def test_binding_emits_the_canonical_encryption_key_envelope() -> None:
    """Only ``envelope_keys_for('encryption_key')`` reaches a workload (#1003),
    and the portable values have to mean the same thing they do on KMS."""
    driver, _vault, handle = _provisioned()
    name = _key_name(handle)

    binding = driver.binding(ServiceHandle(handle))

    assert set(envelope_keys_for("encryption_key")) <= set(binding.env_vars)
    literals = {key: ref.literal for key, ref in binding.env_vars.items()}
    assert literals["ENCRYPTION_KEY_ID"] == f"{VAULT_URL}/keys/{name}"
    assert literals["ENCRYPTION_KEY_ARN"] == literals["ENCRYPTION_KEY_ID"]
    assert literals["ENCRYPTION_KEY_ALIAS"] == name
    assert literals["ENCRYPTION_KEY_SPEC"] == "RSA_2048"
    assert literals["ENCRYPTION_KEY_USAGE"] == "ENCRYPT_DECRYPT"
    assert literals["ENCRYPTION_KEY_MULTI_REGION"] == "false"
    assert literals["AZURE_KEY_VAULT_KEY_ID"] == f"{VAULT_URL}/keys/{name}/0123456789abcdef0123456789abcdef"
    assert literals["AZURE_KEY_VAULT_KEY_VERSION"] == "0123456789abcdef0123456789abcdef"
    assert all(ref.secret_ref is None for ref in binding.env_vars.values()), "a key binding carries no secret"


def test_binding_reports_an_ec_signing_key_as_sign_verify() -> None:
    driver, _vault = _driver()
    result = driver.provision(
        _spec(config={"key_type": "EC", "curve": "P-384", "key_operations": ["sign", "verify"]}),
    )

    binding = driver.binding(ServiceHandle(result.handle))

    assert binding.env_vars["ENCRYPTION_KEY_SPEC"].literal == "EC_P-384"
    assert binding.env_vars["ENCRYPTION_KEY_USAGE"].literal == "SIGN_VERIFY"


@pytest.mark.parametrize("access_mode", sorted(_ACCESS_MODE_ROLES))
def test_every_access_mode_grants_a_vetted_builtin_role_at_the_key_scope(access_mode: str) -> None:
    """An Azure grant only authorizes anything if it names a built-in role at a
    real ARM scope; anything else reports a configured binding and grants nothing."""
    driver, _vault, handle = _provisioned()

    binding = driver.binding(ServiceHandle(handle), {"access_mode": access_mode})

    grant = binding.iam_grants[0]
    assert grant.resource == (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}"
        f"/providers/Microsoft.KeyVault/vaults/{VAULT_NAME}/keys/{_key_name(handle)}"
    )
    assert validate_arm_scope(grant.resource) == grant.resource
    resolved = resolve_grant_action(grant.actions[0])
    assert resolved == RoleGrant(grant.actions[0], AZURE_BUILTIN_ROLE_IDS[grant.actions[0]])


def test_an_unknown_access_mode_is_refused_rather_than_defaulted() -> None:
    driver, _vault, handle = _provisioned()

    with pytest.raises(AzureKeyVaultKeyError, match="access_mode"):
        driver.binding(ServiceHandle(handle), {"access_mode": "root"})


def test_snapshot_and_restore_are_honestly_unsupported() -> None:
    driver, _vault, handle = _provisioned()

    with pytest.raises(AzureKeyVaultKeyError, match="non-exportable"):
        driver.snapshot(ServiceHandle(handle))
    restored = driver.restore(SnapshotHandle(handle=handle, snapshot_id="none", created_at="now"), _spec())

    assert not restored.ok
    assert restored.errors == ["not_implemented"]


def test_registration_catalog_and_runtime_config_are_wired() -> None:
    from core.cluster_observability import managed_config_for

    driver, _vault = _driver()
    entry = next(
        item
        for item in MATRIX.managed_services
        if item.plugin_id == "azure" and item.kind == "encryption_key" and item.variant == "key_vault_key"
    )

    assert PLUGIN.managed_service_drivers[("encryption_key", "key_vault_key")] is AzureKeyVaultKeyDriver
    assert entry.status == "preview"
    assert set(entry.binding_envs) == set(driver.binding_schema().env_vars)
    assert driver.config_schema()["additionalProperties"] is False

    cluster = SimpleNamespace(
        slug="azure-prod",
        region="eastus2",
        auth_config={},
        provider_config={
            "subscription_id": SUBSCRIPTION,
            "resource_group": RESOURCE_GROUP,
            "vault_url": VAULT_URL,
            "key_vault_key_name_prefix": "workload",
            "key_vault_key_deletion_protection_default": False,
            "key_vault_key_purge_on_delete_default": True,
            "key_vault_key_rotation_period_default": "P180D",
            "key_vault_key_rotation_notify_before_expiry_default": "P14D",
            "key_vault_key_api_version": "7.5",
            "key_vault_key_request_timeout_seconds": 45,
        },
    )

    config = managed_config_for("azure", cluster, kind="encryption_key", variant="key_vault_key")

    assert config == AzureKeyVaultKeyConfig(
        subscription_id=SUBSCRIPTION,
        resource_group=RESOURCE_GROUP,
        vault_url=VAULT_URL,
        key_name_prefix="workload",
        deletion_protection_default=False,
        purge_on_delete_default=True,
        rotation_period_default="P180D",
        rotation_notify_before_expiry_default="P14D",
        api_version="7.5",
        request_timeout_seconds=45.0,
    )


def test_runtime_config_refuses_an_install_with_no_vault() -> None:
    from core.cluster_observability import ClusterObservabilityError, managed_config_for

    cluster = SimpleNamespace(
        slug="azure-prod",
        region="eastus2",
        auth_config={},
        provider_config={"subscription_id": SUBSCRIPTION, "resource_group": RESOURCE_GROUP, "vault_url": ""},
    )

    with pytest.raises(ClusterObservabilityError, match="vault_url"):
        managed_config_for("azure", cluster, kind="encryption_key", variant="key_vault_key")
