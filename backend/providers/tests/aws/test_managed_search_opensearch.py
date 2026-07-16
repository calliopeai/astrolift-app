"""Tests for AWS OpenSearch Service managed-service driver (#373).

Covers the full ManagedServiceDriver protocol surface:
provision (idempotent, with-tags, secret-stored, deletion-protection
marker), update, deprovision four-corner matrix
(delete_data x force_destroy), status state-mapping, binding
env-var shape, snapshot + restore.

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
    UpdateSpec,
)
from aws.managed._base import ManagedServiceError, parse_handle
from aws.managed.search_opensearch import (
    KIND,
    OpenSearchSearchConfig,
    OpenSearchSearchDriver,
    _generate_master_password,
    _index_name_for,
)


@pytest.fixture
def aws_mock() -> Generator:
    """Single moto context covering OpenSearch + Secrets Manager."""
    from moto import mock_aws

    with mock_aws():
        os_client = boto3.client("opensearch", region_name="us-east-1")
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        yield {"os": os_client, "sm": sm}


@pytest.fixture
def os_client(aws_mock) -> Any:
    return aws_mock["os"]


@pytest.fixture
def sm_client(aws_mock) -> Any:
    return aws_mock["sm"]


@pytest.fixture
def driver(aws_mock) -> OpenSearchSearchDriver:
    return OpenSearchSearchDriver(
        config=OpenSearchSearchConfig(
            region="us-east-1",
            snapshot_repo_s3_bucket="astrolift-snaps",
            snapshot_repo_role_arn="arn:aws:iam::123:role/snap",
        ),
        opensearch_client=aws_mock["os"],
        secrets_client=aws_mock["sm"],
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="aws-prod",
        service_handle_hint="search",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_domain(
    driver: OpenSearchSearchDriver,
    os_client,
) -> None:
    result = driver.provision(_spec())
    assert result.ok is True
    kind, domain_name = parse_handle(result.handle)
    assert kind == KIND
    resp = os_client.describe_domain(DomainName=domain_name)
    status = resp["DomainStatus"]
    assert status["EngineVersion"].startswith("OpenSearch_")
    assert status["NodeToNodeEncryptionOptions"]["Enabled"] is True
    assert status["EncryptionAtRestOptions"]["Enabled"] is True
    assert status["DomainEndpointOptions"]["EnforceHTTPS"] is True


def test_provision_default_instance_type_is_t3_small(
    driver: OpenSearchSearchDriver,
    os_client,
) -> None:
    result = driver.provision(_spec())
    _, domain_name = parse_handle(result.handle)
    resp = os_client.describe_domain(DomainName=domain_name)
    cluster = resp["DomainStatus"]["ClusterConfig"]
    assert cluster["InstanceType"] == "t3.small.search"
    assert cluster["InstanceCount"] == 1


def test_provision_idempotent(driver: OpenSearchSearchDriver) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message


def test_provision_stores_master_password_in_secrets_manager(
    driver: OpenSearchSearchDriver,
    sm_client,
) -> None:
    result = driver.provision(_spec())
    _, domain_name = parse_handle(result.handle)
    secret_name = f"astrolift/opensearch/{domain_name}/master"
    resp = sm_client.get_secret_value(SecretId=secret_name)
    assert len(resp["SecretString"]) >= 16


def test_provision_records_deletion_protection_marker(
    driver: OpenSearchSearchDriver,
    sm_client,
) -> None:
    result = driver.provision(_spec())
    _, domain_name = parse_handle(result.handle)
    marker = f"astrolift/opensearch/{domain_name}/protection"
    resp = sm_client.get_secret_value(SecretId=marker)
    assert resp["SecretString"] == "1"


def test_provision_marker_off_when_disabled(
    driver: OpenSearchSearchDriver,
    sm_client,
) -> None:
    result = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    _, domain_name = parse_handle(result.handle)
    marker = f"astrolift/opensearch/{domain_name}/protection"
    resp = sm_client.get_secret_value(SecretId=marker)
    assert resp["SecretString"] == "0"


def test_provision_honours_size_to_instance_type(
    driver: OpenSearchSearchDriver,
    os_client,
) -> None:
    result = driver.provision(_spec(size="large"))
    _, domain_name = parse_handle(result.handle)
    resp = os_client.describe_domain(DomainName=domain_name)
    assert resp["DomainStatus"]["ClusterConfig"]["InstanceType"] == "m6g.large.search"
    assert resp["DomainStatus"]["EBSOptions"]["VolumeSize"] == 50


def test_provision_honours_spec_config_override(
    driver: OpenSearchSearchDriver,
    os_client,
) -> None:
    result = driver.provision(
        _spec(
            config={
                "instance_type": "r6g.xlarge.search",
                "instance_count": 3,
                "volume_size_gb": 200,
                "deletion_protection": False,
            },
        ),
    )
    _, domain_name = parse_handle(result.handle)
    resp = os_client.describe_domain(DomainName=domain_name)
    cluster = resp["DomainStatus"]["ClusterConfig"]
    assert cluster["InstanceType"] == "r6g.xlarge.search"
    assert cluster["InstanceCount"] == 3
    assert resp["DomainStatus"]["EBSOptions"]["VolumeSize"] == 200


def test_provision_tags_domain(
    driver: OpenSearchSearchDriver,
    os_client,
) -> None:
    result = driver.provision(_spec())
    _, domain_name = parse_handle(result.handle)
    resp = os_client.describe_domain(DomainName=domain_name)
    arn = resp["DomainStatus"]["ARN"]
    tag_resp = os_client.list_tags(ARN=arn)
    tag_map = {t["Key"]: t["Value"] for t in tag_resp["TagList"]}
    assert tag_map.get("astrolift.io/managed-by") == "platform"
    assert tag_map.get("astrolift.io/app") == "api"
    assert tag_map.get("astrolift.io/environment") == "prod"


# ---- update -----------------------------------------------------


def test_update_resize(
    driver: OpenSearchSearchDriver,
    os_client,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="medium"),
    )
    assert result.ok


def test_update_noop_when_nothing_to_change(
    driver: OpenSearchSearchDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message


def test_update_deletion_protection_toggle(
    driver: OpenSearchSearchDriver,
    sm_client,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"deletion_protection": False},
        ),
    )
    assert result.ok
    _, domain_name = parse_handle(provisioned.handle)
    marker = f"astrolift/opensearch/{domain_name}/protection"
    resp = sm_client.get_secret_value(SecretId=marker)
    assert resp["SecretString"] == "0"


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_refuses_with_protection_on(
    driver: OpenSearchSearchDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert not result.ok
    assert "DeletionProtection" in result.message


def test_deprovision_delete_data_only_skips_snapshot(
    driver: OpenSearchSearchDriver,
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


def test_deprovision_default_with_protection_off_takes_snapshot(
    driver: OpenSearchSearchDriver,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert result.ok
    assert "snapshot=taken" in result.message


def test_deprovision_force_destroy_bypasses_protection_keeps_snapshot(
    driver: OpenSearchSearchDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=False,
        force_destroy=True,
    )
    assert result.ok
    assert "snapshot=taken" in result.message
    assert "force_destroy=True" in result.message


def test_deprovision_atomic_both_flags(
    driver: OpenSearchSearchDriver,
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
    driver: OpenSearchSearchDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="search/does-not-exist"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_with_data_delete_drops_master_password_secret(
    driver: OpenSearchSearchDriver,
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
    driver: OpenSearchSearchDriver,
    sm_client,
) -> None:
    provisioned = driver.provision(
        _spec(config={"deletion_protection": False}),
    )
    _, domain_name = parse_handle(provisioned.handle)
    secret_name = f"astrolift/opensearch/{domain_name}/master"

    driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    # Secret still resolvable on the retained-data path
    assert sm_client.get_secret_value(SecretId=secret_name)["SecretString"]


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: OpenSearchSearchDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="search/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_available_when_endpoint_ready(
    driver: OpenSearchSearchDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    # moto returns Processing=False + Endpoint immediately
    assert state.state == "available"


# ---- binding ----------------------------------------------------


def test_binding_returns_connection_envelope(
    driver: OpenSearchSearchDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    env = binding.env_vars
    for key in (
        "SEARCH_URL",
        "SEARCH_API_KEY",
        "SEARCH_INDEX_PREFIX",
        "OPENSEARCH_ENDPOINT",
        "OPENSEARCH_INDEX_NAME",
        "OPENSEARCH_MASTER_USER",
        "OPENSEARCH_MASTER_PASSWORD",
    ):
        assert key in env
    # API key + master password are secret refs, not literals
    assert env["SEARCH_API_KEY"].secret_ref is not None
    assert env["SEARCH_API_KEY"].literal is None
    assert env["OPENSEARCH_MASTER_PASSWORD"].secret_ref is not None
    # Endpoint + user are literals
    assert env["SEARCH_URL"].literal.startswith("https://")
    assert env["OPENSEARCH_MASTER_USER"].literal == "astrolift"


def test_binding_iam_grants_cover_secret_and_domain(
    driver: OpenSearchSearchDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    grants = binding.iam_grants
    actions = {a for g in grants for a in g.actions}
    assert "secretsmanager:GetSecretValue" in actions
    assert "es:ESHttpGet" in actions
    assert "es:ESHttpPost" in actions


def test_binding_for_missing_raises(
    driver: OpenSearchSearchDriver,
) -> None:
    with pytest.raises(ManagedServiceError):
        driver.binding(ServiceHandle(handle="search/missing"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_returns_deterministic_id(
    driver: OpenSearchSearchDriver,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    _, domain_name = parse_handle(provisioned.handle)
    assert snap.snapshot_id.startswith(domain_name)


def test_snapshot_for_missing_raises(
    driver: OpenSearchSearchDriver,
) -> None:
    with pytest.raises(ManagedServiceError):
        driver.snapshot(ServiceHandle(handle="search/missing"))


def test_restore_provisions_target_domain(
    driver: OpenSearchSearchDriver,
    os_client,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))

    restore_spec = _spec(service_handle_hint="restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    _, target_name = parse_handle(result.handle)
    resp = os_client.describe_domain(DomainName=target_name)
    assert resp["DomainStatus"]["DomainName"] == target_name


# ---- naming + helpers -------------------------------------------


def test_domain_name_canonicalization(
    driver: OpenSearchSearchDriver,
) -> None:
    name = driver._domain_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="search",
        ),
    )
    # OpenSearch domain name rules
    assert 3 <= len(name) <= 28
    assert name[0].isalpha()
    for c in name:
        assert c.islower() or c.isdigit() or c == "-"
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")


def test_index_name_for_swaps_hyphens_to_underscores() -> None:
    assert _index_name_for(domain_name="acme-prod-search") == ("acme_prod_search")


def test_generated_master_password_complexity() -> None:
    pw = _generate_master_password(length=32)
    assert len(pw) == 32
    # AWS complexity rules satisfied: at least one of each class
    assert any(c.isupper() for c in pw)
    assert any(c.islower() for c in pw)
    assert any(c.isdigit() for c in pw)
    assert any(c in "-_." for c in pw)


def test_default_client_construction_path() -> None:
    """When opensearch_client / secrets_client aren't injected the
    driver must still build successfully — boto3 inits lazily, so
    this only needs to confirm no exception during __init__."""
    from moto import mock_aws

    with mock_aws():
        d = OpenSearchSearchDriver(
            config=OpenSearchSearchConfig(region="us-east-1"),
        )
        assert d._os is not None  # type: ignore[attr-defined]
        assert d._sm is not None  # type: ignore[attr-defined]


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: OpenSearchSearchDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "engine_version",
        "instance_type",
        "instance_count",
        "volume_size_gb",
        "deletion_protection",
        "kms_key_id",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: OpenSearchSearchDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "SEARCH_URL",
        "SEARCH_API_KEY",
        "SEARCH_INDEX_PREFIX",
        "OPENSEARCH_ENDPOINT",
        "OPENSEARCH_INDEX_NAME",
        "OPENSEARCH_MASTER_USER",
        "OPENSEARCH_MASTER_PASSWORD",
    ):
        assert key in schema.env_vars


# ---- snapshot bucket-less fallback ------------------------------


def test_deprovision_skips_snapshot_when_no_bucket_configured(
    sm_client,
    os_client,
) -> None:
    """When ``snapshot_repo_s3_bucket`` is empty the driver must
    fall back to skipping the final snapshot rather than crashing
    (matches the ElastiCache best-effort posture)."""
    d = OpenSearchSearchDriver(
        config=OpenSearchSearchConfig(region="us-east-1"),
        opensearch_client=os_client,
        secrets_client=sm_client,
    )
    provisioned = d.provision(
        _spec(config={"deletion_protection": False}),
    )
    result = d.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok
    assert "snapshot=skipped" in result.message


# ---- error path: surface SDK errors -----------------------------


def test_provision_surfaces_create_domain_error(
    sm_client,
    os_client,
) -> None:
    """A real create_domain failure (e.g. duplicate name probed via
    a separate domain) must surface a clean error rather than
    pass through."""
    d = OpenSearchSearchDriver(
        config=OpenSearchSearchConfig(region="us-east-1"),
        opensearch_client=os_client,
        secrets_client=sm_client,
    )
    provisioned = d.provision(_spec())
    assert provisioned.ok
    # Second provision via the same spec exercises the
    # already-exists short-circuit; force a real error by patching
    # create_domain to raise.
    other_spec = _spec(service_handle_hint="bogus")

    def boom(**_kwargs):
        raise RuntimeError("simulated AWS failure")

    os_client.create_domain = boom  # type: ignore[assignment]
    result = d.provision(other_spec)
    assert not result.ok
    assert "create_domain" in result.message
