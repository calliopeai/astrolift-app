"""Tests for AWS OpenSearch vector managed-service driver (#372).

Covers the full ManagedServiceDriver protocol surface for the
``vector_index`` kind under AWS:
provision (idempotent, with-tags, master-password-stored,
deletion-protection tag), update, deprovision four-corner matrix,
status state-mapping, binding env-var shape, snapshot + restore.

All cloud calls go through moto; no real AWS access is required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import boto3
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from aws.managed._base import ManagedServiceError, parse_handle
from aws.managed.vector_opensearch import (
    KIND,
    OpenSearchVectorConfig,
    OpenSearchVectorDriver,
    _generate_master_password,
    _index_name_from_domain,
)


@pytest.fixture
def aws_mock() -> Generator:
    from moto import mock_aws

    with mock_aws():
        os_client = boto3.client("opensearch", region_name="us-east-1")
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        yield {"opensearch": os_client, "sm": sm}


@pytest.fixture
def os_client(aws_mock) -> Any:
    return aws_mock["opensearch"]


@pytest.fixture
def sm_client(aws_mock) -> Any:
    return aws_mock["sm"]


@pytest.fixture
def driver(aws_mock) -> OpenSearchVectorDriver:
    return OpenSearchVectorDriver(
        config=OpenSearchVectorConfig(
            region="us-east-1",
            snapshot_bucket="astrolift-vec-snaps",
            snapshot_role_arn="arn:aws:iam::123456789012:role/snap",
        ),
        opensearch_client=aws_mock["opensearch"],
        secrets_client=aws_mock["sm"],
    )


def _spec(**overrides) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="aws-prod",
        service_handle_hint="v",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_domain(
    driver: OpenSearchVectorDriver,
    os_client,
) -> None:
    result = driver.provision(_spec())
    assert result.ok is True
    kind, domain_name = parse_handle(result.handle)
    assert kind == KIND
    desc = os_client.describe_domain(DomainName=domain_name)
    assert desc["DomainStatus"]["DomainName"] == domain_name
    assert desc["DomainStatus"]["EngineVersion"].startswith("OpenSearch_")


def test_provision_idempotent(driver: OpenSearchVectorDriver) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message


def test_provision_stores_master_password_in_secrets_manager(
    driver: OpenSearchVectorDriver,
    sm_client,
) -> None:
    result = driver.provision(_spec())
    _, domain_name = parse_handle(result.handle)
    secret_name = f"astrolift/opensearch/{domain_name}/master"
    resp = sm_client.get_secret_value(SecretId=secret_name)
    assert len(resp["SecretString"]) >= 16


def test_provision_skips_master_password_when_fgac_off(
    driver: OpenSearchVectorDriver,
    sm_client,
) -> None:
    result = driver.provision(
        _spec(config={"fine_grained_access_control": False}),
    )
    assert result.ok
    _, domain_name = parse_handle(result.handle)
    secret_name = f"astrolift/opensearch/{domain_name}/master"
    with pytest.raises(sm_client.exceptions.ResourceNotFoundException):
        sm_client.get_secret_value(SecretId=secret_name)


def test_provision_honours_size_to_instance_type(
    driver: OpenSearchVectorDriver,
    os_client,
) -> None:
    result = driver.provision(_spec(size="large"))
    _, domain_name = parse_handle(result.handle)
    desc = os_client.describe_domain(DomainName=domain_name)
    assert desc["DomainStatus"]["ClusterConfig"]["InstanceType"] == "m6g.xlarge.search"


def test_provision_honours_spec_config_override(
    driver: OpenSearchVectorDriver,
    os_client,
) -> None:
    result = driver.provision(
        _spec(
            config={
                "instance_type": "r6g.2xlarge.search",
                "instance_count": 2,
                "volume_size_gb": 100,
            },
        ),
    )
    assert result.ok
    _, domain_name = parse_handle(result.handle)
    desc = os_client.describe_domain(DomainName=domain_name)
    cluster = desc["DomainStatus"]["ClusterConfig"]
    assert cluster["InstanceType"] == "r6g.2xlarge.search"
    assert cluster["InstanceCount"] == 2
    assert cluster["ZoneAwarenessEnabled"] is True


def test_provision_tags_domain_with_astrolift_namespace(
    driver: OpenSearchVectorDriver,
    os_client,
) -> None:
    result = driver.provision(_spec())
    _, domain_name = parse_handle(result.handle)
    desc = os_client.describe_domain(DomainName=domain_name)
    arn = desc["DomainStatus"]["ARN"]
    tag_resp = os_client.list_tags(ARN=arn)
    tag_map = {t["Key"]: t["Value"] for t in tag_resp["TagList"]}
    assert tag_map.get("astrolift.io/managed-by") == "platform"
    assert tag_map.get("astrolift.io/app") == "api"
    assert tag_map.get("astrolift.io/environment") == "prod"


def test_provision_marks_deletion_protection_tag_by_default(
    driver: OpenSearchVectorDriver,
    os_client,
) -> None:
    result = driver.provision(_spec())
    _, domain_name = parse_handle(result.handle)
    desc = os_client.describe_domain(DomainName=domain_name)
    tag_resp = os_client.list_tags(ARN=desc["DomainStatus"]["ARN"])
    tag_map = {t["Key"]: t["Value"] for t in tag_resp["TagList"]}
    assert tag_map.get("astrolift.io/deletion-protection") == "true"


def test_provision_skips_deletion_protection_tag_when_off(
    driver: OpenSearchVectorDriver,
    os_client,
) -> None:
    result = driver.provision(_spec(config={"deletion_protection": False}))
    _, domain_name = parse_handle(result.handle)
    desc = os_client.describe_domain(DomainName=domain_name)
    tag_resp = os_client.list_tags(ARN=desc["DomainStatus"]["ARN"])
    tag_map = {t["Key"]: t["Value"] for t in tag_resp["TagList"]}
    assert "astrolift.io/deletion-protection" not in tag_map


# ---- update -----------------------------------------------------


def test_update_resize(
    driver: OpenSearchVectorDriver,
    os_client,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="medium"),
    )
    assert result.ok


def test_update_instance_count(
    driver: OpenSearchVectorDriver,
    os_client,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"instance_count": 3},
        ),
    )
    assert result.ok


def test_update_noop_when_nothing_to_change(
    driver: OpenSearchVectorDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_refuses_when_protection_tagged(
    driver: OpenSearchVectorDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert not result.ok
    assert "deletion-protection" in result.message


def test_deprovision_delete_data_only_skips_snapshot(
    driver: OpenSearchVectorDriver,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "snapshot=skipped" in result.message


def test_deprovision_default_with_protection_off_marks_snapshot(
    driver: OpenSearchVectorDriver,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok
    assert "snapshot=taken" in result.message


def test_deprovision_force_destroy_bypasses_protection_keeps_snapshot(
    driver: OpenSearchVectorDriver,
) -> None:
    provisioned = driver.provision(_spec())  # deletion-protection tag on
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=False,
        force_destroy=True,
    )
    assert result.ok
    assert "snapshot=taken" in result.message
    assert "force_destroy=True" in result.message


def test_deprovision_atomic_both_flags(
    driver: OpenSearchVectorDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "snapshot=skipped" in result.message
    assert "force_destroy=True" in result.message


def test_deprovision_idempotent_when_already_gone(
    driver: OpenSearchVectorDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="vector_index/does-not-exist"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_with_data_delete_drops_master_password_secret(
    driver: OpenSearchVectorDriver,
    sm_client,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    _, domain_name = parse_handle(provisioned.handle)
    secret_name = f"astrolift/opensearch/{domain_name}/master"
    assert sm_client.get_secret_value(SecretId=secret_name)["SecretString"]

    driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    with pytest.raises(sm_client.exceptions.ResourceNotFoundException):
        sm_client.get_secret_value(SecretId=secret_name)


def test_deprovision_keeps_secret_on_data_retained_path(
    driver: OpenSearchVectorDriver,
    sm_client,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    _, domain_name = parse_handle(provisioned.handle)
    secret_name = f"astrolift/opensearch/{domain_name}/master"
    driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    # Secret still recoverable (delete_data=False)
    assert sm_client.get_secret_value(SecretId=secret_name)["SecretString"]


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: OpenSearchVectorDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="vector_index/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_created_to_available(
    driver: OpenSearchVectorDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    # moto returns processing=False / created=True immediately
    assert state.state in {"available", "provisioning"}


# ---- binding ----------------------------------------------------


def test_binding_returns_connection_envelope(
    driver: OpenSearchVectorDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    env = binding.env_vars
    for key in (
        "OPENSEARCH_ENDPOINT",
        "OPENSEARCH_INDEX_NAME",
        "OPENSEARCH_MASTER_USER",
        "OPENSEARCH_MASTER_PASSWORD",
        "AWS_REGION",
    ):
        assert key in env
    assert env["OPENSEARCH_MASTER_PASSWORD"].secret_ref is not None
    assert env["OPENSEARCH_MASTER_PASSWORD"].literal is None
    assert env["OPENSEARCH_MASTER_USER"].literal == "astrolift"
    assert env["OPENSEARCH_ENDPOINT"].literal.startswith("https://")


def test_binding_index_name_derived_from_domain(
    driver: OpenSearchVectorDriver,
) -> None:
    provisioned = driver.provision(_spec())
    _, domain_name = parse_handle(provisioned.handle)
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    assert binding.env_vars["OPENSEARCH_INDEX_NAME"].literal == _index_name_from_domain(domain_name=domain_name)


def test_binding_iam_grants_cover_secret_and_domain(
    driver: OpenSearchVectorDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    actions = {a for g in binding.iam_grants for a in g.actions}
    assert "secretsmanager:GetSecretValue" in actions
    assert "es:ESHttpPost" in actions


def test_binding_for_missing_raises(
    driver: OpenSearchVectorDriver,
) -> None:
    with pytest.raises(ManagedServiceError):
        driver.binding(ServiceHandle(handle="vector_index/missing"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_creates_handle(
    driver: OpenSearchVectorDriver,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    _, domain_name = parse_handle(provisioned.handle)
    assert snap.snapshot_id.startswith(domain_name)


def test_snapshot_surfaces_driver_error(
    driver: OpenSearchVectorDriver,
) -> None:
    with pytest.raises(ManagedServiceError):
        driver.snapshot(ServiceHandle(handle="vector_index/missing"))


def test_restore_provisions_target_domain(
    driver: OpenSearchVectorDriver,
    os_client,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    restore_spec = _spec(service_handle_hint="rest")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    _, target_name = parse_handle(result.handle)
    desc = os_client.describe_domain(DomainName=target_name)
    assert desc["DomainStatus"]["DomainName"] == target_name


def test_restore_surfaces_provision_failure() -> None:
    """If the target provision fails, restore must surface the
    upstream error without claiming success."""

    class _NotFoundError(Exception):
        pass

    _NotFoundError.__name__ = "ResourceNotFoundException"

    class FailingOS:
        def describe_domain(self, **_kwargs):
            raise _NotFoundError("missing")

        def create_domain(self, **_kwargs):
            raise RuntimeError("quota exceeded")

    class FailingSM:
        def create_secret(self, **kwargs):
            return {"ARN": "arn:aws:sm:::secret/dummy"}

        def delete_secret(self, **_kwargs):
            return None

    d = OpenSearchVectorDriver(
        config=OpenSearchVectorConfig(region="us-east-1"),
        opensearch_client=FailingOS(),
        secrets_client=FailingSM(),
    )
    snap = SnapshotHandle(
        handle="vector_index/any",
        snapshot_id="snap-1",
        created_at="2026-05-15T00:00:00+00:00",
    )
    result = d.restore(snap, _spec(service_handle_hint="rt"))
    assert not result.ok
    assert "create_domain" in result.message


# ---- snapshot bucket disabled path ----------------------------


def test_deprovision_without_snapshot_bucket_skips_snapshot_step(
    aws_mock,
) -> None:
    """When snapshot_bucket is empty (operator opted out), the
    snapshot step is skipped silently regardless of delete_data."""
    drv = OpenSearchVectorDriver(
        config=OpenSearchVectorConfig(
            region="us-east-1",
            snapshot_bucket="",
        ),
        opensearch_client=aws_mock["opensearch"],
        secrets_client=aws_mock["sm"],
    )
    provisioned = drv.provision(_spec(config={"deletion_protection": False}))
    result = drv.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok
    assert "snapshot=skipped" in result.message


# ---- module helpers ---------------------------------------------


def test_index_name_from_domain_lowercases_and_suffixes() -> None:
    assert (
        _index_name_from_domain(
            domain_name="astrolift-vec-acme-api-prod-v",
        )
        == "astrolift-vec-acme-api-prod-v-idx"
    )


def test_generated_password_satisfies_complexity() -> None:
    import string as _string

    for _ in range(10):
        pw = _generate_master_password(length=24)
        assert any(c in _string.ascii_uppercase for c in pw)
        assert any(c in _string.ascii_lowercase for c in pw)
        assert any(c in _string.digits for c in pw)
        assert any(c in "-_." for c in pw)
        assert len(pw) == 24


def test_domain_name_canonicalization(
    driver: OpenSearchVectorDriver,
) -> None:
    name = driver._domain_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My_API",
            environment_name="Prod",
            service_handle_hint="v",
        ),
    )
    assert name == name.lower()
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")
    assert 3 <= len(name) <= 28
    assert name[0].isalpha()


# ---- default client construction path --------------------------


def test_default_client_construction_path() -> None:
    """When opensearch_client / secrets_client aren't injected the
    driver must still build successfully — boto3 inits lazily."""
    from moto import mock_aws

    with mock_aws():
        d = OpenSearchVectorDriver(
            config=OpenSearchVectorConfig(region="us-east-1"),
        )
        assert d._os is not None  # type: ignore[attr-defined]
        assert d._sm is not None  # type: ignore[attr-defined]


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: OpenSearchVectorDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "engine_version",
        "instance_type",
        "instance_count",
        "zone_awareness",
        "volume_size_gb",
        "deletion_protection",
        "fine_grained_access_control",
        "kms_key_arn",
        "vpc_subnet_ids",
        "vpc_security_group_ids",
        "access_policies",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: OpenSearchVectorDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "OPENSEARCH_ENDPOINT",
        "OPENSEARCH_INDEX_NAME",
        "OPENSEARCH_MASTER_USER",
        "OPENSEARCH_MASTER_PASSWORD",
        "AWS_REGION",
    ):
        assert key in schema.env_vars


def test_provision_does_not_adopt_another_services_resource(driver) -> None:
    """Names are slug-joined, so another service can map to this one's name (#1961)."""
    first = driver.provision(_spec(managed_service_id="svc-a"))
    second = driver.provision(_spec(managed_service_id="svc-b"))

    assert first.ok, first.message
    assert not second.ok and second.handle == ""
    assert "refusing to adopt" in second.message
