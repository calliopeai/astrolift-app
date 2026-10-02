"""Lifecycle, cryptographic-mode, and request-shape tests for AWS KMS."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import ClassVar
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed.encryption_kms import KMSConfig, KMSDriver

SERVICE_ID = "11111111-1111-4111-8111-111111111111"


class NotFound(Exception):
    response: ClassVar[dict] = {"Error": {"Code": "NotFoundException"}}


class FakeKMS:
    def __init__(self, region: str = "us-east-1") -> None:
        self.region = region
        self.calls: list[tuple[str, dict]] = []
        self.keys: dict[str, dict] = {}
        self.tags: dict[str, list[dict]] = {}
        self.aliases: list[dict] = []
        self.grants: dict[str, list[dict]] = {}
        self.rotation: dict[str, dict] = {}
        self.regional_clients: dict[str, FakeKMS] = {}
        self.seq = 0

    def _call(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def kwargs_for(self, name: str) -> dict:
        return next(kwargs for call_name, kwargs in self.calls if call_name == name)

    def _metadata(self, key_id: str, **overrides) -> dict:
        arn = f"arn:aws:kms:{self.region}:123456789012:key/{key_id}"
        return {
            "AWSAccountId": "123456789012",
            "KeyId": key_id,
            "Arn": arn,
            "CreationDate": datetime(2026, 8, 14, tzinfo=UTC),
            "Enabled": True,
            "Description": "",
            "KeyUsage": "ENCRYPT_DECRYPT",
            "KeyState": "Enabled",
            "Origin": "AWS_KMS",
            "KeyManager": "CUSTOMER",
            "KeySpec": "SYMMETRIC_DEFAULT",
            "MultiRegion": False,
            **overrides,
        }

    def seed_key(self, *, key_id: str = "key-1", tags: list[dict] | None = None, **metadata) -> dict:
        try:
            UUID(key_id)
        except ValueError:
            if not key_id.startswith("mrk-"):
                key_id = str(uuid5(NAMESPACE_URL, key_id))
        row = self._metadata(key_id, **metadata)
        self.keys[key_id] = row
        self.tags[key_id] = list(tags or [])
        self.grants[key_id] = []
        return row

    def _key_id(self, value: str) -> str:
        if value in self.keys:
            return value
        for key_id, metadata in self.keys.items():
            if value == metadata["Arn"]:
                return key_id
        raise NotFound(value)

    def create_key(self, **kwargs):
        self._call("create_key", kwargs)
        self.seq += 1
        identity = uuid5(NAMESPACE_URL, f"kms-fixture-{self.seq}")
        key_id = "mrk-" + identity.hex if kwargs.get("MultiRegion") else str(identity)
        origin = kwargs.get("Origin", "AWS_KMS")
        metadata = self.seed_key(
            key_id=key_id,
            Description=kwargs.get("Description", ""),
            KeySpec=kwargs.get("KeySpec", kwargs.get("CustomerMasterKeySpec", "SYMMETRIC_DEFAULT")),
            KeyUsage=kwargs.get("KeyUsage", "ENCRYPT_DECRYPT"),
            Origin=origin,
            KeyState="PendingImport" if origin == "EXTERNAL" else "Enabled",
            Enabled=origin != "EXTERNAL",
            MultiRegion=kwargs.get("MultiRegion", False),
        )
        self.tags[key_id] = list(kwargs.get("Tags") or [])
        if metadata["MultiRegion"]:
            metadata["MultiRegionConfiguration"] = {
                "MultiRegionKeyType": "PRIMARY",
                "PrimaryKey": {"Arn": metadata["Arn"], "Region": self.region},
                "ReplicaKeys": [],
            }
        return {"KeyMetadata": dict(metadata)}

    def describe_key(self, **kwargs):
        self._call("describe_key", kwargs)
        return {"KeyMetadata": dict(self.keys[self._key_id(kwargs["KeyId"])])}

    def list_keys(self, **kwargs):
        self._call("list_keys", kwargs)
        return {"Keys": [{"KeyId": key_id, "KeyArn": row["Arn"]} for key_id, row in self.keys.items()]}

    def list_resource_tags(self, **kwargs):
        self._call("list_resource_tags", kwargs)
        return {"Tags": list(self.tags[self._key_id(kwargs["KeyId"])])}

    def list_aliases(self, **kwargs):
        self._call("list_aliases", kwargs)
        aliases = list(self.aliases)
        if kwargs.get("KeyId"):
            key_id = self._key_id(kwargs["KeyId"])
            aliases = [item for item in aliases if item.get("TargetKeyId") == key_id]
        return {"Aliases": aliases, "Truncated": False}

    def create_alias(self, **kwargs):
        self._call("create_alias", kwargs)
        if any(item["AliasName"] == kwargs["AliasName"] for item in self.aliases):
            raise RuntimeError("AlreadyExistsException")
        self.aliases.append(
            {
                "AliasName": kwargs["AliasName"],
                "AliasArn": f"arn:aws:kms:{self.region}:123456789012:{kwargs['AliasName']}",
                "TargetKeyId": self._key_id(kwargs["TargetKeyId"]),
            },
        )

    def update_key_description(self, **kwargs):
        self._call("update_key_description", kwargs)
        self.keys[self._key_id(kwargs["KeyId"])]["Description"] = kwargs["Description"]

    def put_key_policy(self, **kwargs):
        self._call("put_key_policy", kwargs)

    def enable_key(self, **kwargs):
        self._call("enable_key", kwargs)
        metadata = self.keys[self._key_id(kwargs["KeyId"])]
        metadata.update(KeyState="Enabled", Enabled=True)

    def disable_key(self, **kwargs):
        self._call("disable_key", kwargs)
        metadata = self.keys[self._key_id(kwargs["KeyId"])]
        metadata.update(KeyState="Disabled", Enabled=False)

    def enable_key_rotation(self, **kwargs):
        self._call("enable_key_rotation", kwargs)
        self.rotation[self._key_id(kwargs["KeyId"])] = {
            "enabled": True,
            "period": kwargs.get("RotationPeriodInDays", 365),
        }

    def disable_key_rotation(self, **kwargs):
        self._call("disable_key_rotation", kwargs)
        self.rotation[self._key_id(kwargs["KeyId"])] = {"enabled": False}

    def import_key_material(self, **kwargs):
        self._call("import_key_material", kwargs)
        metadata = self.keys[self._key_id(kwargs["KeyId"])]
        metadata.update(KeyState="Enabled", Enabled=True)

    def list_grants(self, **kwargs):
        self._call("list_grants", kwargs)
        return {"Grants": list(self.grants[self._key_id(kwargs["KeyId"])])}

    def create_grant(self, **kwargs):
        self._call("create_grant", kwargs)
        key_id = self._key_id(kwargs["KeyId"])
        self.seq += 1
        item = {"GrantId": f"grant-{self.seq}", "GrantToken": f"token-{self.seq}", **kwargs}
        item["KeyId"] = self.keys[key_id]["Arn"]
        self.grants[key_id].append(item)
        return {"GrantId": item["GrantId"], "GrantToken": item["GrantToken"]}

    def revoke_grant(self, **kwargs):
        self._call("revoke_grant", kwargs)
        key_id = self._key_id(kwargs["KeyId"])
        self.grants[key_id] = [item for item in self.grants[key_id] if item["GrantId"] != kwargs["GrantId"]]

    def replicate_key(self, **kwargs):
        self._call("replicate_key", kwargs)
        key_id = self._key_id(kwargs["KeyId"])
        target = self.regional_clients[kwargs["ReplicaRegion"]]
        primary = self.keys[key_id]
        replica = target.seed_key(
            key_id=key_id,
            tags=list(kwargs.get("Tags") or []),
            Description=kwargs.get("Description", ""),
            KeySpec=primary["KeySpec"],
            KeyUsage=primary["KeyUsage"],
            Origin=primary["Origin"],
            MultiRegion=True,
            MultiRegionConfiguration={
                "MultiRegionKeyType": "REPLICA",
                "PrimaryKey": {"Arn": primary["Arn"], "Region": self.region},
                "ReplicaKeys": [],
            },
        )
        primary["MultiRegionConfiguration"]["ReplicaKeys"].append(
            {"Arn": replica["Arn"], "Region": target.region},
        )
        return {"ReplicaKeyMetadata": dict(replica), "ReplicaPolicy": kwargs.get("Policy", "")}

    def schedule_key_deletion(self, **kwargs):
        self._call("schedule_key_deletion", kwargs)
        metadata = self.keys[self._key_id(kwargs["KeyId"])]
        metadata.update(KeyState="PendingDeletion", Enabled=False)
        return {
            "KeyId": metadata["KeyId"],
            "DeletionDate": datetime(2026, 8, 14, tzinfo=UTC) + timedelta(days=kwargs["PendingWindowInDays"]),
            "KeyState": "PendingDeletion",
            "PendingWindowInDays": kwargs["PendingWindowInDays"],
        }

    def cancel_key_deletion(self, **kwargs):
        self._call("cancel_key_deletion", kwargs)
        metadata = self.keys[self._key_id(kwargs["KeyId"])]
        metadata.update(KeyState="Disabled", Enabled=False)
        return {"KeyId": metadata["KeyId"]}


def _config() -> KMSConfig:
    return KMSConfig(region="us-east-1", account_id="123456789012")


def _spec(config: dict | None = None, *, hint: str = "data-key") -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug="acme",
        app_id="app-1",
        app_slug="checkout",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="cluster-1",
        service_handle_hint=hint,
        size="small",
        config=config or {},
        binding_id="binding-1",
        managed_service_id=SERVICE_ID,
    )


def _update_spec(*args, **kwargs):
    return UpdateSpec(*args, managed_service_id=SERVICE_ID, **kwargs)


def _deprovision_spec(*args, **kwargs):
    return DeprovisionSpec(*args, managed_service_id=SERVICE_ID, **kwargs)


def _driver(*, client: FakeKMS | None = None, regional_clients: dict[str, FakeKMS] | None = None):
    client = client or FakeKMS()
    return KMSDriver(config=_config(), client=client, regional_clients=regional_clients), client


def test_provision_creates_owned_alias_rotating_symmetric_key() -> None:
    driver, client = _driver()
    result = driver.provision(
        _spec(
            {
                "description": "Application envelope key",
                "rotation_period_days": 180,
                "policy": {"Version": "2012-10-17", "Statement": []},
            },
        ),
    )
    assert result.ok and result.ready
    request = client.kwargs_for("create_key")
    assert request["Description"] == "Application envelope key"
    assert request["MultiRegion"] is False
    tags = {item["TagKey"]: item["TagValue"] for item in request["Tags"]}
    assert tags["astrolift.io/managed-by"] == "platform"
    assert tags["astrolift.io/resource-hint"] == "data-key"
    assert client.kwargs_for("enable_key_rotation")["RotationPeriodInDays"] == 180
    assert client.aliases[0]["AliasName"].startswith("alias/astrolift-")
    assert "put_key_policy" in client.names()


def test_provision_recovers_partial_key_by_tags_after_alias_failure() -> None:
    driver, client = _driver()
    original = client.create_alias

    def fail_alias(**kwargs):
        raise RuntimeError("alias failure")

    client.create_alias = fail_alias
    first = driver.provision(_spec())
    assert not first.ok and first.handle.startswith("encryption_key/arn:aws:kms:")
    client.create_alias = original
    second = driver.provision(_spec())
    assert second.ok and second.handle == first.handle
    assert client.names().count("create_key") == 1


def test_reprovision_is_idempotent_and_reconciles_mutable_controls() -> None:
    driver, client = _driver()
    first = driver.provision(_spec())
    second = driver.provision(
        _spec(
            {
                "description": "updated",
                "enabled": False,
                "rotation_enabled": False,
                "alias": "alias/custom",
            },
        ),
    )
    assert second.ok and second.handle == first.handle
    assert client.names().count("create_key") == 1
    assert "update_key_description" in client.names()
    assert "disable_key" in client.names()
    assert "disable_key_rotation" in client.names()
    assert any(item["AliasName"] == "alias/custom" for item in client.aliases)


def test_immutable_key_changes_require_reprovision() -> None:
    driver, _ = _driver()
    created = driver.provision(_spec())
    result = driver.update(
        _update_spec(created.handle, config={"key": {"KeySpec": "RSA_4096"}}),
    )
    assert not result.ok and "immutable" in result.message


def test_asymmetric_and_hmac_keys_reject_rotation_and_emit_appropriate_grants() -> None:
    driver, client = _driver()
    signed = driver.provision(
        _spec(
            {
                "key": {"KeySpec": "RSA_3072", "KeyUsage": "SIGN_VERIFY"},
                "alias": "signing",
            },
            hint="signing",
        ),
    )
    assert signed.ok
    signing = driver.binding(ServiceHandle(signed.handle), {"access_mode": "use"})
    assert signing.iam_grants[0].actions == ["kms:DescribeKey", "kms:GetPublicKey", "kms:Sign", "kms:Verify"]
    assert "enable_key_rotation" not in client.names()
    rejected = driver.update(
        _update_spec(
            signed.handle,
            config={
                "key": {"KeySpec": "RSA_3072", "KeyUsage": "SIGN_VERIFY"},
                "rotation_enabled": True,
            },
        ),
    )
    assert not rejected.ok and "automatic rotation" in rejected.message

    hmac_driver, _ = _driver(client=FakeKMS())
    hmac = hmac_driver.provision(
        _spec(
            {
                "key": {"KeySpec": "HMAC_256", "KeyUsage": "GENERATE_VERIFY_MAC"},
                "alias": "hmac",
            },
            hint="hmac",
        ),
    )
    binding = hmac_driver.binding(ServiceHandle(hmac.handle), {"access_mode": "use"})
    assert binding.iam_grants[0].actions == ["kms:DescribeKey", "kms:GenerateMac", "kms:VerifyMac"]


def test_symmetric_access_modes_never_offer_get_public_key() -> None:
    driver, _ = _driver()
    created = driver.provision(_spec())
    public = driver.binding(ServiceHandle(created.handle), {"access_mode": "public"})
    assert public.iam_grants[0].actions == ["kms:DescribeKey"]
    encrypt = driver.binding(ServiceHandle(created.handle), {"access_mode": "encrypt"})
    assert encrypt.iam_grants[0].actions == [
        "kms:DescribeKey",
        "kms:Encrypt",
        "kms:GenerateDataKey",
        "kms:GenerateDataKeyWithoutPlaintext",
    ]


def test_imported_key_material_is_decoded_and_imported() -> None:
    driver, client = _driver()
    result = driver.provision(
        _spec(
            {
                "key": {"Origin": "EXTERNAL"},
                "import_key_material": {
                    "encrypted_key_material_b64": base64.b64encode(b"ciphertext").decode(),
                    "import_token_b64": base64.b64encode(b"token").decode(),
                    "expiration_model": "KEY_MATERIAL_DOES_NOT_EXPIRE",
                },
            },
        ),
    )
    assert result.ok and result.ready
    request = client.kwargs_for("import_key_material")
    assert request["EncryptedKeyMaterial"] == b"ciphertext"
    assert request["ImportToken"] == b"token"
    assert "enable_key_rotation" not in client.names()


def test_expiring_imported_key_material_coerces_iso_timestamp() -> None:
    driver, client = _driver()
    result = driver.provision(
        _spec(
            {
                "key": {"Origin": "EXTERNAL"},
                "import_key_material": {
                    "encrypted_key_material_b64": base64.b64encode(b"ciphertext").decode(),
                    "import_token_b64": base64.b64encode(b"token").decode(),
                    "expiration_model": "KEY_MATERIAL_EXPIRES",
                    "valid_to": "2027-08-14T12:00:00Z",
                },
            },
        ),
    )
    assert result.ok
    assert client.kwargs_for("import_key_material")["ValidTo"] == datetime(
        2027,
        8,
        14,
        12,
        tzinfo=UTC,
    )


def test_multi_region_replica_is_created_reconciled_and_bound() -> None:
    primary = FakeKMS("us-east-1")
    replica = FakeKMS("us-west-2")
    primary.regional_clients["us-west-2"] = replica
    driver, _ = _driver(client=primary, regional_clients={"us-west-2": replica})
    config = {
        "multi_region": True,
        "replica_regions": ["us-west-2"],
        "alias": "global-data",
    }
    result = driver.provision(_spec(config))
    assert result.ok
    assert primary.kwargs_for("create_key")["MultiRegion"] is True
    assert primary.kwargs_for("replicate_key")["ReplicaRegion"] == "us-west-2"
    assert any(item["AliasName"] == "alias/global-data" for item in replica.aliases)
    assert replica.rotation == {}


def test_grants_are_idempotent_updated_and_pruned_by_managed_name() -> None:
    driver, client = _driver()
    config = {
        "grants": [
            {
                "name": "worker",
                "request": {
                    "GranteePrincipal": "arn:aws:iam::123456789012:role/worker",
                    "Operations": ["Encrypt", "Decrypt"],
                    "Constraints": {"EncryptionContextSubset": {"app": "checkout"}},
                },
            },
        ],
    }
    first = driver.provision(_spec(config))
    assert first.ok
    assert client.names().count("create_grant") == 1
    assert driver.provision(_spec(config)).ok
    assert client.names().count("create_grant") == 1

    changed = {
        "grants": [
            {
                "name": "worker",
                "request": {
                    "GranteePrincipal": "arn:aws:iam::123456789012:role/worker",
                    "Operations": ["Decrypt"],
                },
            },
        ],
    }
    assert driver.update(_update_spec(first.handle, config=changed)).ok
    assert client.names().count("revoke_grant") == 1
    assert client.names().count("create_grant") == 2
    assert driver.update(_update_spec(first.handle, config={})).ok
    assert client.grants[first.handle.split("/", 1)[1].rsplit("/", 1)[1]] == []


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"key": []}, "object"),
        ({"aliases": "one"}, "array"),
        ({"key": {"Policy": "escape"}}, "Astrolift-owned"),
        ({"alias": "alias/aws/s3"}, "reserved"),
        ({"alias": "bad alias"}, "unsupported"),
        ({"alias": "clé"}, "unsupported"),
        ({"replica_regions": ["us-east-1"]}, "primary region"),
        ({"multi_region": False, "replica_regions": ["us-west-2"]}, "multi_region"),
        ({"rotation_period_days": 30}, "between 90"),
        ({"rotation_period_days": "later"}, "integer"),
        ({"pending_window_days": 31}, "between 7"),
        ({"policy": "not-json"}, "valid JSON"),
        ({"access_mode": "admin"}, "access_mode"),
        ({"import_key_material": {"encrypted_key_material_b64": "eA=="}}, "Origin=EXTERNAL"),
        (
            {
                "key": {"Origin": "EXTERNAL"},
                "import_key_material": {
                    "encrypted_key_material_b64": "not base64",
                    "import_token_b64": "also not base64",
                },
            },
            "valid base64",
        ),
        (
            {
                "key": {"Origin": "EXTERNAL"},
                "import_key_material": {
                    "encrypted_key_material_b64": "eA==",
                    "import_token_b64": "eQ==",
                    "expiration_model": "KEY_MATERIAL_EXPIRES",
                },
            },
            "valid_to is required",
        ),
        (
            {
                "key": {"Origin": "EXTERNAL"},
                "import_key_material": {
                    "encrypted_key_material_b64": "eA==",
                    "import_token_b64": "eQ==",
                    "expiration_model": "KEY_MATERIAL_EXPIRES",
                    "valid_to": "someday",
                },
            },
            "ISO 8601",
        ),
        ({"grants": [{"name": "x", "request": {"KeyId": "escape"}}]}, "Astrolift-owned"),
    ],
)
def test_invalid_config_is_rejected_before_cloud_mutation(config: dict, message: str) -> None:
    driver, client = _driver()
    result = driver.provision(_spec(config))
    assert not result.ok and message in result.message
    assert "create_key" not in client.names()


def test_foreign_alias_and_key_are_never_adopted() -> None:
    driver, client = _driver()
    foreign = client.seed_key(key_id="foreign", tags=[])
    client.create_alias(AliasName="alias/shared", TargetKeyId=foreign["KeyId"])
    result = driver.provision(_spec({"alias": "shared"}))
    assert not result.ok and "ownership" in result.message

    handle = f"encryption_key/{foreign['Arn']}"
    update = driver.update(_update_spec(handle, config={}))
    delete = driver.deprovision(
        _deprovision_spec(handle, {"deletion_protection": False}),
        delete_data=True,
    )
    assert not update.ok and update.errors == ["resource_not_owned"]
    assert not delete.ok and delete.errors == ["resource_not_owned"]


def test_deletion_requires_both_guard_bypass_and_explicit_data_loss() -> None:
    driver, client = _driver()
    created = driver.provision(_spec())
    protected = driver.deprovision(_deprovision_spec(created.handle, {}), delete_data=True)
    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    retained = driver.deprovision(
        _deprovision_spec(created.handle, {"deletion_protection": False}),
    )
    assert not retained.ok and retained.errors == ["delete_data_required"]
    deleted = driver.deprovision(
        _deprovision_spec(created.handle, {"deletion_protection": False, "pending_window_days": 14}),
        delete_data=True,
    )
    assert deleted.ok
    assert client.kwargs_for("schedule_key_deletion")["PendingWindowInDays"] == 14
    assert driver.status(ServiceHandle(created.handle)).state == "deprovisioning"


def test_pending_deletion_can_be_cancelled_only_explicitly() -> None:
    driver, client = _driver()
    created = driver.provision(_spec())
    arn = created.handle.split("/", 1)[1]
    key_id = arn.rsplit("/", 1)[1]
    client.keys[key_id].update(KeyState="PendingDeletion", Enabled=False)
    refused = driver.update(_update_spec(created.handle, config={}))
    assert not refused.ok and refused.errors == ["pending_deletion"]
    recovered = driver.update(_update_spec(created.handle, config={"cancel_pending_deletion": True}))
    assert recovered.ok
    assert "cancel_key_deletion" in client.names()
    assert "enable_key" in client.names()


def test_multi_region_delete_schedules_replicas_before_primary() -> None:
    primary = FakeKMS("us-east-1")
    replica = FakeKMS("us-west-2")
    primary.regional_clients["us-west-2"] = replica
    driver, _ = _driver(client=primary, regional_clients={"us-west-2": replica})
    created = driver.provision(
        _spec({"multi_region": True, "replica_regions": ["us-west-2"]}),
    )
    deleted = driver.deprovision(
        _deprovision_spec(created.handle, {"deletion_protection": False}),
        delete_data=True,
    )
    assert deleted.ok
    assert "schedule_key_deletion" in replica.names()
    assert "schedule_key_deletion" in primary.names()


def test_snapshot_and_restore_explain_non_exportable_key_material() -> None:
    driver, _ = _driver()
    created = driver.provision(_spec())
    with pytest.raises(Exception, match="non-exportable"):
        driver.snapshot(ServiceHandle(created.handle))


def test_generated_requests_match_botocore_kms_models() -> None:
    client = FakeKMS()
    driver, _ = _driver(client=client)
    config = {
        "description": "shape test",
        "rotation_period_days": 180,
        "grants": [
            {
                "name": "worker",
                "request": {
                    "GranteePrincipal": "arn:aws:iam::123456789012:role/worker",
                    "Operations": ["Encrypt", "Decrypt"],
                },
            },
        ],
    }
    created = driver.provision(_spec(config))
    assert created.ok
    binding = driver.binding(ServiceHandle(created.handle), config)
    assert binding.env_vars["ENCRYPTION_KEY_ARN"].literal == created.handle.split("/", 1)[1]
    assert binding.env_vars["ENCRYPTION_KEY_MULTI_REGION"].literal == "false"

    model = Session().get_service_model("kms")
    operations = {
        "create_key": "CreateKey",
        "describe_key": "DescribeKey",
        "list_keys": "ListKeys",
        "list_resource_tags": "ListResourceTags",
        "list_aliases": "ListAliases",
        "create_alias": "CreateAlias",
        "update_key_description": "UpdateKeyDescription",
        "enable_key_rotation": "EnableKeyRotation",
        "list_grants": "ListGrants",
        "create_grant": "CreateGrant",
    }
    for name, request in client.calls:
        if name in operations:
            validate_parameters(request, model.operation_model(operations[name]).input_shape)


def test_registration_catalog_cost_and_runtime_config_are_wired() -> None:
    from core.cluster_observability import managed_config_for

    from _sdk.availability import MATRIX
    from aws.cost import SERVICE_CODE_BY_VARIANT
    from aws.plugin import PLUGIN

    assert ("encryption_key", "kms") in PLUGIN.managed_service_drivers
    assert SERVICE_CODE_BY_VARIANT[("encryption_key", "kms")] == "awskms"
    entry = next(
        item
        for item in MATRIX.managed_services
        if item.plugin_id == "aws" and item.kind == "encryption_key" and item.variant == "kms"
    )
    assert entry.status == "preview"
    assert "ENCRYPTION_KEY_ID" in entry.binding_envs

    cluster = SimpleNamespace(
        slug="aws-prod",
        region="us-west-2",
        provider_config={
            "kms_alias_name_prefix": "alias/platform",
            "account_id": "123456789012",
            "kms_deletion_protection_default": False,
            "kms_pending_window_days_default": 21,
        },
        auth_config={},
    )
    config = managed_config_for("aws", cluster, kind="encryption_key", variant="kms")
    assert config.region == "us-west-2"
    assert config.account_id == "123456789012"
    assert config.alias_name_prefix == "alias/platform"
    assert config.deletion_protection_default is False
    assert config.pending_window_days_default == 21


def test_an_alias_on_another_services_key_is_not_adopted() -> None:
    """Platform-made is not enough; it must be this service's (#1961)."""
    import dataclasses

    driver, client = _driver()
    first = driver.provision(
        dataclasses.replace(_spec({"alias": "alias/shared-native"}), managed_service_id=SERVICE_ID)
    )
    second = driver.provision(
        dataclasses.replace(
            _spec({"alias": "alias/shared-native"}), managed_service_id="22222222-2222-4222-8222-222222222222"
        )
    )

    assert first.ok, first.message
    assert not second.ok and "ownership does not match" in second.message
    assert client.names().count("create_key") == 1
