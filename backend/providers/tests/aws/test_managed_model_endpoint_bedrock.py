"""Tests for AmazonBedrockDriver (#376).

Covers the full ManagedServiceDriver protocol surface against an
in-process fake bedrock control-plane + boto3-style logs client
(moto doesn't model Bedrock yet). The fake records every call so
the tests can assert on request shape as well as observable
result.

Tests for provision (idempotent, log group created, tags applied,
provisioned throughput attached), update (model swap, throughput
swap), four-corner deprovision matrix, status state-mapping,
binding env-var shape, snapshot + restore.
"""
# ruff: noqa: N803
# Boto3-shaped fake clients keep PascalCase kwargs (logGroupName,
# provisionedModelId, etc.) because the driver-under-test calls
# them by those exact keyword names -- matching the real boto3
# surface. Renaming would break the test, not the driver.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from botocore.exceptions import ClientError

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from aws.managed._base import ManagedServiceError, parse_handle
from aws.managed.model_endpoint_bedrock import (
    KIND,
    AmazonBedrockConfig,
    AmazonBedrockDriver,
)

# ---- fakes ------------------------------------------------------


class _BotoResourceAlreadyExistsError(Exception):
    pass


# The bedrock driver matches boto3's ``ResourceAlreadyExistsException``
# by class name (``type(exc).__name__``), so we set the runtime name
# explicitly here. The Python-side identifier stays N818-compliant.
_BotoResourceAlreadyExistsError.__name__ = "ResourceAlreadyExistsException"


@dataclass
class FakeBedrockClient:
    """Records Bedrock control-plane calls + holds provisioned-
    throughput state.

    The driver only calls four bedrock methods on this client:
    create_provisioned_model_throughput,
    delete_provisioned_model_throughput,
    get_provisioned_model_throughput, and (indirectly via the
    driver) nothing else -- the runtime ``invoke_model`` is the
    workload's path, not the driver's.
    """

    provisioned: dict[str, dict[str, Any]] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)
    tags: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    def create_provisioned_model_throughput(
        self,
        *,
        modelUnits: int,
        provisionedModelName: str,
        modelId: str,
        commitmentDuration: str,
        tags: list[dict[str, str]],
        clientRequestToken: str,
    ) -> dict[str, Any]:
        self.create_calls.append(
            {
                "modelUnits": modelUnits,
                "provisionedModelName": provisionedModelName,
                "modelId": modelId,
                "commitmentDuration": commitmentDuration,
                "tags": tags,
                "clientRequestToken": clientRequestToken,
            },
        )
        arn = "arn:aws:bedrock:us-east-1:123456789012:provisioned-model/abcdefgh1234"
        self.provisioned[arn] = {
            "status": "Creating",
            "provisionedModelArn": arn,
            "provisionedModelName": provisionedModelName,
            "modelArn": f"arn:aws:bedrock:us-east-1::foundation-model/{modelId}",
            "desiredModelArn": f"arn:aws:bedrock:us-east-1::foundation-model/{modelId}",
            "desiredModelUnits": modelUnits,
            "commitmentExpirationTime": datetime.now(UTC) + timedelta(days=180),
            "commitmentDuration": commitmentDuration,
            "modelUnits": modelUnits,
        }
        self.tags[arn] = tags
        return {"provisionedModelArn": arn}

    def delete_provisioned_model_throughput(
        self,
        *,
        provisionedModelId: str,
    ) -> None:
        self.delete_calls.append(provisionedModelId)
        self.provisioned.pop(provisionedModelId, None)

    def get_provisioned_model_throughput(
        self,
        *,
        provisionedModelId: str,
    ) -> dict[str, Any]:
        for arn, row in self.provisioned.items():
            if provisionedModelId in (arn, row["provisionedModelName"]):
                return row
        raise ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "missing"}}, "GetProvisionedModelThroughput"
        )

    def list_tags_for_resource(self, *, resourceARN: str) -> dict[str, Any]:
        return {"tags": self.tags[resourceARN]}


@dataclass
class FakeLogsClient:
    log_groups: dict[str, dict[str, Any]] = field(default_factory=dict)
    create_calls: list[str] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)
    retention_calls: list[tuple[str, int]] = field(default_factory=list)

    def create_log_group(self, *, logGroupName: str) -> None:
        self.create_calls.append(logGroupName)
        if logGroupName in self.log_groups:
            raise _BotoResourceAlreadyExistsError(logGroupName)
        self.log_groups[logGroupName] = {"retention": None}

    def put_retention_policy(
        self,
        *,
        logGroupName: str,
        retentionInDays: int,
    ) -> None:
        self.retention_calls.append((logGroupName, retentionInDays))
        if logGroupName in self.log_groups:
            self.log_groups[logGroupName]["retention"] = retentionInDays

    def delete_log_group(self, *, logGroupName: str) -> None:
        self.delete_calls.append(logGroupName)
        self.log_groups.pop(logGroupName, None)


# ---- fixtures ---------------------------------------------------


@pytest.fixture
def bedrock_client() -> FakeBedrockClient:
    return FakeBedrockClient()


@pytest.fixture
def logs_client() -> FakeLogsClient:
    return FakeLogsClient()


@pytest.fixture
def driver(
    bedrock_client: FakeBedrockClient,
    logs_client: FakeLogsClient,
) -> AmazonBedrockDriver:
    return AmazonBedrockDriver(
        config=AmazonBedrockConfig(region="us-east-1"),
        bedrock_client=bedrock_client,
        logs_client=logs_client,
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="22222222-2222-4222-8222-222222222222",
        managed_service_id="11111111-1111-4111-8111-111111111111",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="aws-prod",
        service_handle_hint="model",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_record_and_log_group(
    driver: AmazonBedrockDriver,
    logs_client: FakeLogsClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    kind, record_id = parse_handle(result.handle)
    assert kind == KIND
    log_group = f"/aws/astrolift/bedrock/{record_id}"
    assert log_group in logs_client.log_groups
    assert logs_client.log_groups[log_group]["retention"] == 30


def test_provision_default_model_id_for_small_size(
    driver: AmazonBedrockDriver,
) -> None:
    result = driver.provision(_spec())
    assert "claude-3-haiku" in result.message


def test_provision_picks_larger_model_for_xlarge_size(
    driver: AmazonBedrockDriver,
) -> None:
    result = driver.provision(_spec(size="xlarge"))
    assert "claude-3-opus" in result.message


def test_provision_honours_spec_model_id_override(
    driver: AmazonBedrockDriver,
) -> None:
    result = driver.provision(
        _spec(config={"model_id": "mistral.mistral-large-2402-v1:0"}),
    )
    assert "mistral.mistral-large" in result.message


def test_provision_idempotent(
    driver: AmazonBedrockDriver,
    logs_client: FakeLogsClient,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message
    # No double create on the log group either (re-creating returns
    # ResourceAlreadyExistsException; driver swallows it).
    log_group = f"/aws/astrolift/bedrock/{parse_handle(a.handle)[1]}"
    assert logs_client.create_calls.count(log_group) >= 1


def test_provision_on_demand_reports_ready(
    driver: AmazonBedrockDriver,
) -> None:
    """On-demand model access creates no cloud resource, so provision is
    ready immediately and the workflow skips its readiness wait (#1038)."""
    result = driver.provision(_spec())
    assert result.ok
    assert result.ready is True


def test_provision_idempotent_on_demand_reports_ready(
    driver: AmazonBedrockDriver,
) -> None:
    """The already-exists fast path must also report ready for on-demand."""
    driver.provision(_spec())
    result = driver.provision(_spec())
    assert "already exists" in result.message
    assert result.ready is True


def test_provision_with_throughput_not_ready(
    driver: AmazonBedrockDriver,
) -> None:
    """A provisioned-throughput commitment is a real resource that
    transitions creating -> InService, so provision is NOT ready and the
    workflow must poll status() before finalize."""
    result = driver.provision(
        _spec(config={"provisioned_throughput": "OneMonth"}),
    )
    assert result.ok
    assert result.ready is False


def test_provision_attaches_provisioned_throughput(
    driver: AmazonBedrockDriver,
    bedrock_client: FakeBedrockClient,
) -> None:
    result = driver.provision(
        _spec(
            config={
                "provisioned_throughput": "OneMonth",
                "model_units": 2,
            },
        ),
    )
    assert result.ok
    assert "native readiness pending" in result.message
    assert len(bedrock_client.create_calls) == 1
    call = bedrock_client.create_calls[0]
    assert call["commitmentDuration"] == "OneMonth"
    assert call["modelUnits"] == 2


def test_provision_records_tags_on_provisioned_throughput(
    driver: AmazonBedrockDriver,
    bedrock_client: FakeBedrockClient,
) -> None:
    driver.provision(
        _spec(config={"provisioned_throughput": "OneMonth"}),
    )
    tags = {row["key"]: row["value"] for row in bedrock_client.create_calls[0]["tags"]}
    assert tags["astrolift.io/managed-by"] == "platform"
    assert tags["astrolift.io/app"] == "api"


def test_provision_surfaces_throughput_failure(
    bedrock_client: FakeBedrockClient,
    logs_client: FakeLogsClient,
) -> None:
    def boom(**_kwargs):
        raise RuntimeError("quota exceeded")

    bedrock_client.create_provisioned_model_throughput = boom  # type: ignore[assignment]
    d = AmazonBedrockDriver(
        config=AmazonBedrockConfig(region="us-east-1"),
        bedrock_client=bedrock_client,
        logs_client=logs_client,
    )
    result = d.provision(
        _spec(config={"provisioned_throughput": "OneMonth"}),
    )
    assert not result.ok
    assert "quota exceeded" not in result.message
    assert result.errors == ["throughput_unverified"]


# ---- update -----------------------------------------------------


def test_update_swaps_model_id(driver: AmazonBedrockDriver) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"model_id": "anthropic.claude-3-opus-20240229-v1:0"},
        ),
    )
    assert result.ok
    assert "claude-3-opus" in result.message


def test_update_resize_picks_size_model(
    driver: AmazonBedrockDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="large"),
    )
    assert result.ok
    assert "claude-3-sonnet" in result.message


def test_update_noop_when_nothing_to_change(
    driver: AmazonBedrockDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message


def test_update_refuses_on_missing(
    driver: AmazonBedrockDriver,
) -> None:
    result = driver.update(
        UpdateSpec(handle=f"{KIND}/ghost", config={"model_id": "x"}),
    )
    assert not result.ok
    assert result.errors == ["not_found"]


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_no_throughput_preserves_logs(
    driver: AmazonBedrockDriver,
    logs_client: FakeLogsClient,
) -> None:
    provisioned = driver.provision(_spec())
    _, record_id = parse_handle(provisioned.handle)
    log_group = f"/aws/astrolift/bedrock/{record_id}"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, managed_service_id=_spec().managed_service_id),
    )
    assert result.ok
    assert "logs=preserved" in result.message
    # Log group still present on the retained-data path
    assert log_group in logs_client.log_groups


def test_deprovision_delete_data_drops_logs(
    driver: AmazonBedrockDriver,
    logs_client: FakeLogsClient,
) -> None:
    provisioned = driver.provision(_spec())
    _, record_id = parse_handle(provisioned.handle)
    log_group = f"/aws/astrolift/bedrock/{record_id}"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, managed_service_id=_spec().managed_service_id),
        delete_data=True,
    )
    assert result.ok
    assert "logs=deleted" in result.message
    assert log_group not in logs_client.log_groups


def test_deprovision_default_refuses_with_active_commitment(
    driver: AmazonBedrockDriver,
) -> None:
    provisioned = driver.provision(
        _spec(config={"provisioned_throughput": "SixMonths"}),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, managed_service_id=_spec().managed_service_id),
    )
    assert not result.ok
    assert "commitment" in result.message


def test_deprovision_force_destroy_cannot_bypass_aws_commitment(
    driver: AmazonBedrockDriver,
    bedrock_client: FakeBedrockClient,
) -> None:
    provisioned = driver.provision(
        _spec(config={"provisioned_throughput": "SixMonths"}),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, managed_service_id=_spec().managed_service_id),
        force_destroy=True,
    )
    assert not result.ok
    assert result.errors == ["commitment_active"]
    assert not bedrock_client.delete_calls


def test_deprovision_atomic_both_flags(
    driver: AmazonBedrockDriver,
    bedrock_client: FakeBedrockClient,
    logs_client: FakeLogsClient,
) -> None:
    provisioned = driver.provision(
        _spec(config={"provisioned_throughput": "OneMonth"}),
    )
    native = next(iter(bedrock_client.provisioned.values()))
    native["commitmentExpirationTime"] = datetime.now(UTC) - timedelta(seconds=1)
    log_group = f"/aws/astrolift/bedrock/{native['provisionedModelName']}"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, managed_service_id=_spec().managed_service_id),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "logs=deleted" in result.message
    assert log_group not in logs_client.log_groups
    assert len(bedrock_client.delete_calls) == 1


def test_deprovision_idempotent_when_already_gone(
    driver: AmazonBedrockDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle=f"{KIND}/never-existed"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_surfaces_pt_delete_failure(
    driver: AmazonBedrockDriver,
    bedrock_client: FakeBedrockClient,
) -> None:
    provisioned = driver.provision(
        _spec(config={"provisioned_throughput": "OneMonth"}),
    )

    def boom(**_kwargs):
        raise RuntimeError("transient API failure")

    next(iter(bedrock_client.provisioned.values()))["commitmentExpirationTime"] = datetime.now(UTC) - timedelta(
        seconds=1
    )
    bedrock_client.delete_provisioned_model_throughput = boom  # type: ignore[assignment]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, managed_service_id=_spec().managed_service_id),
        force_destroy=True,
    )
    assert not result.ok
    assert "transient API failure" not in result.message
    assert result.errors == ["throughput_unverified"]


# ---- status -----------------------------------------------------


def test_status_without_record_is_available(
    driver: AmazonBedrockDriver,
) -> None:
    """A capability-only handle with no in-memory record is available.

    Each Temporal activity builds a fresh driver instance, so the
    readiness-poll activity never shares the provision activity's
    in-memory record. Treating its absence as ``deprovisioned`` (the
    old behavior) false-failed the provision readiness gate with
    "backend not ready: state=deprovisioned" (#1038). On-demand
    foundation-model access has no cloud resource to be unavailable, so
    a valid handle reads as available regardless of local record state.
    """
    state = driver.status(ServiceHandle(handle=f"{KIND}/never-seen"))
    assert state.state == "available"


def test_status_for_on_demand_record_is_available(
    driver: AmazonBedrockDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle, managed_service_id=_spec().managed_service_id))
    assert state.state == "available"


def test_status_for_provisioning_throughput(
    driver: AmazonBedrockDriver,
    bedrock_client: FakeBedrockClient,
) -> None:
    provisioned = driver.provision(
        _spec(config={"provisioned_throughput": "OneMonth"}),
    )
    # Flip the throughput status into 'Creating'
    arn = next(iter(bedrock_client.provisioned))
    bedrock_client.provisioned[arn]["status"] = "Creating"
    state = driver.status(ServiceHandle(handle=provisioned.handle, managed_service_id=_spec().managed_service_id))
    assert state.state == "provisioning"


def test_status_for_failed_throughput(
    driver: AmazonBedrockDriver,
    bedrock_client: FakeBedrockClient,
) -> None:
    provisioned = driver.provision(
        _spec(config={"provisioned_throughput": "OneMonth"}),
    )
    arn = next(iter(bedrock_client.provisioned))
    bedrock_client.provisioned[arn]["status"] = "Failed"
    state = driver.status(ServiceHandle(handle=provisioned.handle, managed_service_id=_spec().managed_service_id))
    assert state.state == "error"


# ---- binding ----------------------------------------------------


def test_binding_returns_connection_envelope(
    driver: AmazonBedrockDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle, managed_service_id=_spec().managed_service_id),
    )
    env = binding.env_vars
    for key in (
        "MODEL_ENDPOINT_URL",
        "MODEL_ENDPOINT_MODEL_ID",
        "MODEL_ENDPOINT_PROVIDER",
        "BEDROCK_REGION",
        "BEDROCK_MODEL_ID",
        "BEDROCK_INVOKE_ENDPOINT",
    ):
        assert key in env
    assert env["MODEL_ENDPOINT_PROVIDER"].literal == "bedrock"
    assert env["BEDROCK_REGION"].literal == "us-east-1"
    assert env["MODEL_ENDPOINT_URL"].literal.startswith("https://")
    assert "bedrock-runtime" in env["MODEL_ENDPOINT_URL"].literal


def test_binding_iam_grants_cover_invoke_model(
    driver: AmazonBedrockDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle, managed_service_id=_spec().managed_service_id),
    )
    actions = {a for g in binding.iam_grants for a in g.actions}
    assert "bedrock:InvokeModel" in actions
    assert "bedrock:InvokeModelWithResponseStream" in actions


def test_binding_without_record_reconstructs_envelope(
    bedrock_client: FakeBedrockClient,
    logs_client: FakeLogsClient,
) -> None:
    """binding() on a fresh driver instance (no in-memory record) must
    still emit the invoke env + grant.

    This is the finalize path: the activity that materializes binding
    rows builds a brand-new driver, so it never shares the provision
    activity's record. Requiring the record there raised
    ``ManagedServiceError`` and aborted provisioning (#1038). A
    capability-only service reconstructs the envelope from the handle +
    driver config alone.
    """
    provisioner = AmazonBedrockDriver(
        config=AmazonBedrockConfig(region="us-east-1"),
        bedrock_client=bedrock_client,
        logs_client=logs_client,
    )
    provisioned = provisioner.provision(_spec())
    # A separate driver instance with an empty record store, exactly like
    # the finalize activity sees.
    fresh = AmazonBedrockDriver(
        config=AmazonBedrockConfig(region="us-east-1"),
        bedrock_client=FakeBedrockClient(),
        logs_client=FakeLogsClient(),
    )
    binding = fresh.binding(ServiceHandle(handle=provisioned.handle, managed_service_id=_spec().managed_service_id))
    assert binding.env_vars["MODEL_ENDPOINT_PROVIDER"].literal == "bedrock"
    assert binding.env_vars["BEDROCK_REGION"].literal == "us-east-1"
    assert "bedrock-runtime" in binding.env_vars["MODEL_ENDPOINT_URL"].literal
    actions = {a for g in binding.iam_grants for a in g.actions}
    assert "bedrock:InvokeModel" in actions


def test_binding_without_record_honours_config_model_id(
    driver: AmazonBedrockDriver,
) -> None:
    """When the durable record is absent, an operator config model_id is
    honored over the driver default so the workload binds the model the
    operator selected."""
    binding = driver.binding(
        ServiceHandle(handle=f"{KIND}/acme-api-prod-model"),
        config={"model_id": "mistral.mistral-large-2402-v1:0"},
    )
    assert binding.env_vars["BEDROCK_MODEL_ID"].literal == "mistral.mistral-large-2402-v1:0"


def test_binding_notes_mention_irsa_tag(
    driver: AmazonBedrockDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle, managed_service_id=_spec().managed_service_id),
    )
    assert "astrolift.io/irsa-role" in binding.notes


def test_binding_endpoint_honours_override(
    bedrock_client: FakeBedrockClient,
    logs_client: FakeLogsClient,
) -> None:
    d = AmazonBedrockDriver(
        config=AmazonBedrockConfig(
            region="us-east-1",
            invoke_endpoint_override=("https://bedrock-runtime.acme.local"),
        ),
        bedrock_client=bedrock_client,
        logs_client=logs_client,
    )
    provisioned = d.provision(_spec())
    binding = d.binding(ServiceHandle(handle=provisioned.handle, managed_service_id=_spec().managed_service_id))
    assert binding.env_vars["MODEL_ENDPOINT_URL"].literal == "https://bedrock-runtime.acme.local"


# ---- snapshot + restore -----------------------------------------


def test_snapshot_returns_deterministic_id(
    driver: AmazonBedrockDriver,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle, managed_service_id=_spec().managed_service_id))
    _, record_id = parse_handle(provisioned.handle)
    assert snap.snapshot_id.startswith(record_id)


def test_snapshot_for_missing_raises(
    driver: AmazonBedrockDriver,
) -> None:
    with pytest.raises(ManagedServiceError):
        driver.snapshot(ServiceHandle(handle=f"{KIND}/missing"))


def test_restore_provisions_target_record(
    driver: AmazonBedrockDriver,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle, managed_service_id=_spec().managed_service_id))

    restore_spec = _spec(service_handle_hint="restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    _, restored_id = parse_handle(result.handle)
    assert restored_id != parse_handle(provisioned.handle)[1]


# ---- naming + helpers -------------------------------------------


def test_record_name_canonicalization(
    driver: AmazonBedrockDriver,
) -> None:
    name = driver._record_id_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="model",
        ),
    )
    assert 1 <= len(name) <= 63
    assert name[0].isalpha()
    for c in name:
        assert c.islower() or c.isdigit() or c == "-"
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: AmazonBedrockDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in ("model_id", "provisioned_throughput", "model_units"):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: AmazonBedrockDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "MODEL_ENDPOINT_URL",
        "MODEL_ENDPOINT_MODEL_ID",
        "MODEL_ENDPOINT_PROVIDER",
        "BEDROCK_REGION",
        "BEDROCK_MODEL_ID",
        "BEDROCK_INVOKE_ENDPOINT",
    ):
        assert key in schema.env_vars
