"""AWS Bedrock managed-service driver (#376).

Implements ``ManagedServiceDriver`` for the canonical AWS managed
model-endpoint path. Bedrock's invocation surface is a single
regional endpoint (``bedrock-runtime``) keyed by ``model_id`` --
no Bedrock-side resource is created for on-demand foundation
models, so the driver's "provision" is really a record-keeping +
optional provisioned-throughput attach step.

When ``spec.config.provisioned_throughput`` is set the driver
allocates a provisioned-throughput commitment via
``create_provisioned_model_throughput``. Provisioned throughput
carries a commitment window (1 month / 6 months); the deprovision
path refuses to delete during an active commitment unless
``force_destroy=True`` is set.

Deprovision matrix:

  delete_data=False, force_destroy=False (default):
    Preserve CloudWatch invocation logs. Refuse if a provisioned
    throughput commitment is still in-window.

  delete_data=True, force_destroy=False:
    Drop the platform's CloudWatch log group + retained model
    record. Commitment window still respected.

  delete_data=False, force_destroy=True:
    Preserve invocation logs. Bypass commitment-window check and
    delete the provisioned throughput.

  delete_data=True, force_destroy=True:
    Atomic teardown: delete log group, delete provisioned
    throughput regardless of commitment, drop the record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from _sdk._telemetry import driver_op
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
from aws.managed._base import (
    ManagedServiceError,
    handle_for,
    parse_handle,
    tags_for,
)

KIND = "model_endpoint"


# Size -> default foundation model id. Operators override per spec
# via ``spec.config.model_id``. Defaults stay conservative: a
# small Claude Haiku for small/medium, Sonnet for large, Opus
# for xlarge -- matches the rough cost/latency tiering.
_SIZE_TO_MODEL_ID = {
    "small": "anthropic.claude-3-haiku-20240307-v1:0",
    "medium": "anthropic.claude-3-haiku-20240307-v1:0",
    "large": "anthropic.claude-3-sonnet-20240229-v1:0",
    "xlarge": "anthropic.claude-3-opus-20240229-v1:0",
}


@dataclass(frozen=True)
class AmazonBedrockConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    region: str

    invoke_endpoint_override: str = ""
    """Optional override for the bedrock-runtime endpoint. Empty
    string falls back to the regional default
    (``bedrock-runtime.<region>.amazonaws.com``)."""

    default_model_id: str = "anthropic.claude-3-haiku-20240307-v1:0"

    invocation_log_group_prefix: str = "/aws/astrolift/bedrock"
    """CloudWatch Logs group prefix for invocation logs. Logs
    persist across ``delete_data=False`` deprovisions for
    compliance + retroactive audit."""

    invocation_log_retention_days: int = 30

    irsa_role_tag_key: str = "astrolift.io/irsa-role"
    """Tag key on the platform-managed record that maps to the
    IRSA-eligible role workloads assume to call ``bedrock:InvokeModel``."""

    irsa_role_tag_value_prefix: str = "bedrock-invoker"
    """Value-prefix on the IRSA role tag. Driver appends the
    model-record id for traceability."""


class AmazonBedrockDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: AmazonBedrockConfig,
        bedrock_client: Any | None = None,
        logs_client: Any | None = None,
    ) -> None:
        self._config = config
        if bedrock_client is not None:
            self._bedrock = bedrock_client
        else:
            import boto3

            self._bedrock = boto3.client(
                "bedrock", region_name=config.region,
            )
        if logs_client is not None:
            self._logs = logs_client
        else:
            import boto3

            self._logs = boto3.client(
                "logs", region_name=config.region,
            )
        # Bedrock has no first-party "model record" resource for
        # on-demand models. The driver owns this dict as the
        # source of truth for the platform record; in prod the
        # ManagedServiceBinding row upstream of the driver is the
        # durable equivalent.
        self._records: dict[str, dict[str, Any]] = {}

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="aws",
        driver="model_endpoint_bedrock",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        record_id = self._record_id_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe(record_id)
        if existing is not None:
            return ProvisionResult(
                ok=True,
                handle=handle_for(kind=KIND, resource_id=record_id),
                ready=not (existing.get("ProvisionedThroughputArn") or ""),
                message=(
                    f"bedrock model endpoint {record_id} already exists "
                    f"(model_id={existing.get('ModelId')})"
                ),
            )

        model_id = (
            cfg.get("model_id")
            or _SIZE_TO_MODEL_ID.get(spec.size)
            or self._config.default_model_id
        )

        # CloudWatch log group for invocation logs. Idempotent: a
        # pre-existing group is left alone.
        log_group = self._log_group_for(record_id=record_id)
        self._ensure_log_group(log_group)

        provisioned_throughput_arn = ""
        commitment_duration = cfg.get("provisioned_throughput")
        if commitment_duration:
            try:
                resp = self._bedrock.create_provisioned_model_throughput(
                    modelUnits=int(cfg.get("model_units", 1)),
                    provisionedModelName=record_id,
                    modelId=model_id,
                    commitmentDuration=commitment_duration,
                    tags=_pt_tags(spec),
                )
                provisioned_throughput_arn = (
                    resp.get("provisionedModelArn") or ""
                )
            except Exception as exc:
                return ProvisionResult(
                    ok=False,
                    handle="",
                    message=(
                        f"create_provisioned_model_throughput: {exc}"
                    ),
                    errors=[str(exc)],
                )

        self._store_record(
            record_id=record_id,
            model_id=model_id,
            log_group=log_group,
            provisioned_throughput_arn=provisioned_throughput_arn,
            commitment_duration=str(commitment_duration or ""),
            spec=spec,
        )

        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=record_id),
            # On-demand foundation-model access creates no cloud resource
            # -> ready immediately. A provisioned-throughput commitment is
            # a real resource that transitions creating -> InService, so
            # leave ready=False and let the workflow poll status().
            ready=not provisioned_throughput_arn,
            message=(
                f"bedrock model endpoint {record_id} provisioned "
                f"(model_id={model_id}, "
                f"provisioned_throughput="
                f"{'attached' if provisioned_throughput_arn else 'none'})"
            ),
        )

    @driver_op(cloud="aws", driver="model_endpoint_bedrock")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, record_id = parse_handle(spec.handle)
        existing = self._describe(record_id)
        if existing is None:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"bedrock model endpoint {record_id} not found",
                errors=["not_found"],
            )
        cfg = spec.config or {}

        new_model_id = None
        if cfg.get("model_id"):
            new_model_id = cfg["model_id"]
        elif spec.size:
            new_model_id = _SIZE_TO_MODEL_ID.get(spec.size)

        if not new_model_id and "provisioned_throughput" not in cfg:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided -- no-op",
            )

        if new_model_id:
            existing["ModelId"] = new_model_id

        if "provisioned_throughput" in cfg:
            existing["CommitmentDuration"] = str(
                cfg["provisioned_throughput"],
            )

        # Persist mutated record back to the in-memory store +
        # CloudWatch tags (the durable side-channel).
        self._records[record_id] = existing

        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"bedrock model endpoint {record_id} updated "
                f"(model_id={existing['ModelId']})"
            ),
        )

    @driver_op(
        cloud="aws",
        driver="model_endpoint_bedrock",
        audit=True,
        sensitive_kind="managed_service_deprovision",
    )
    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        _, record_id = parse_handle(spec.handle)

        existing = self._describe(record_id)
        if existing is None:
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=(
                    f"bedrock model endpoint {record_id} already gone"
                ),
            )

        pt_arn = existing.get("ProvisionedThroughputArn") or ""
        commitment_active = self._commitment_active(existing)
        if pt_arn and commitment_active and not force_destroy:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"bedrock model endpoint {record_id} has an "
                    f"active provisioned-throughput commitment; pass "
                    f"force_destroy=True to bypass"
                ),
                errors=["commitment_active"],
            )

        if pt_arn:
            try:
                self._bedrock.delete_provisioned_model_throughput(
                    provisionedModelId=pt_arn,
                )
            except Exception as exc:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=(
                        f"delete_provisioned_model_throughput: {exc}"
                    ),
                    errors=[str(exc)],
                )

        log_action = "preserved"
        if delete_data:
            self._delete_log_group(
                log_group=existing.get("LogGroup")
                or self._log_group_for(record_id=record_id),
            )
            log_action = "deleted"
            self._records.pop(record_id, None)

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"bedrock model endpoint {record_id} delete queued "
                f"(provisioned_throughput="
                f"{'released' if pt_arn else 'none'}, "
                f"logs={log_action}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="aws", driver="model_endpoint_bedrock")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, record_id = parse_handle(handle.handle)
        existing = self._describe(record_id) or {}
        pt_arn = existing.get("ProvisionedThroughputArn") or ""
        if pt_arn:
            try:
                resp = self._bedrock.get_provisioned_model_throughput(
                    provisionedModelId=pt_arn,
                )
                pt_state = (resp.get("status") or "").lower()
                if pt_state == "creating":
                    return ServiceStatus(
                        handle=handle.handle,
                        state="provisioning",
                        message=(
                            f"provisioned throughput {pt_arn} creating"
                        ),
                    )
                if pt_state == "failed":
                    return ServiceStatus(
                        handle=handle.handle,
                        state="error",
                        message=(
                            f"provisioned throughput {pt_arn} failed"
                        ),
                    )
            except Exception:
                # Best-effort -- fall through to available.
                pass
        # On-demand foundation-model access has no cloud resource that can
        # be "unavailable": a syntactically valid handle is always
        # available. The in-memory record is per-driver-instance and is
        # absent across Temporal activity boundaries (each activity builds
        # a fresh driver), so a missing record must NOT read as
        # "deprovisioned" -- doing so false-failed the provision readiness
        # gate with "backend not ready: state=deprovisioned".
        model_id = existing.get("ModelId") or self._config.default_model_id
        return ServiceStatus(
            handle=handle.handle,
            state="available",
            message=(
                f"bedrock model endpoint {record_id} available "
                f"(model_id={model_id})"
            ),
        )

    @driver_op(cloud="aws", driver="model_endpoint_bedrock")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        _, record_id = parse_handle(handle.handle)
        # Capability-only service: reconstruct the connection envelope from
        # the handle + driver config alone. The in-memory record is absent
        # in the fresh driver instance the finalize activity builds, so we
        # must not require it (that raised and aborted provisioning). When
        # present (same-instance) its ModelId wins; otherwise fall back to
        # operator config then the driver default.
        existing = self._describe(record_id) or {}
        cfg = config or {}
        invoke_endpoint = (
            self._config.invoke_endpoint_override
            or f"https://bedrock-runtime.{self._config.region}.amazonaws.com"
        )
        model_id = (
            existing.get("ModelId")
            or cfg.get("model_id")
            or _SIZE_TO_MODEL_ID.get(str(cfg.get("size", "")))
            or self._config.default_model_id
        )
        log_group = existing.get("LogGroup") or self._log_group_for(
            record_id=record_id,
        )
        irsa_role_tag_value = (
            f"{self._config.irsa_role_tag_value_prefix}-{record_id}"
        )
        return Binding(
            env_vars={
                # Canonical contract envs
                "MODEL_ENDPOINT_URL": ValueRef(literal=invoke_endpoint),
                "MODEL_ENDPOINT_MODEL_ID": ValueRef(literal=model_id),
                "MODEL_ENDPOINT_PROVIDER": ValueRef(literal="bedrock"),
                # AWS-flavoured aliases
                "BEDROCK_REGION": ValueRef(literal=self._config.region),
                "BEDROCK_MODEL_ID": ValueRef(literal=model_id),
                "BEDROCK_INVOKE_ENDPOINT": ValueRef(
                    literal=invoke_endpoint,
                ),
            },
            iam_grants=[
                Grant(
                    resource=(
                        f"arn:aws:bedrock:{self._config.region}::"
                        f"foundation-model/{model_id}"
                    ),
                    actions=[
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                    ],
                ),
            ],
            notes=(
                f"IRSA role lookup uses tag "
                f"{self._config.irsa_role_tag_key}="
                f"{irsa_role_tag_value}. CloudWatch invocation logs "
                f"persist at {log_group} -- retention "
                f"governed by deprovision delete_data flag."
            ),
        )

    @driver_op(cloud="aws", driver="model_endpoint_bedrock")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        # Foundation-model endpoints have no snapshot primitive --
        # state is the provisioned-throughput commitment + the
        # CloudWatch log group, both already durable. We return a
        # deterministic id so the workflow layer's snapshot path
        # gets a handle to track.
        _, record_id = parse_handle(handle.handle)
        existing = self._describe(record_id)
        if existing is None:
            raise ManagedServiceError(
                f"snapshot for missing record {record_id}",
            )
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=f"{record_id}-snap-{stamp}",
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="model_endpoint_bedrock")
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
                f"target record provisioned; snapshot "
                f"{snapshot.snapshot_id} carries no replayable state "
                f"for on-demand bedrock models"
            ),
        )

    @driver_op(cloud="aws", driver="model_endpoint_bedrock", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "model_id": {"type": "string"},
                "provisioned_throughput": {
                    "type": "string",
                    "enum": ["OneMonth", "SixMonths"],
                },
                "model_units": {"type": "integer", "minimum": 1},
            },
        }

    @driver_op(cloud="aws", driver="model_endpoint_bedrock", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "MODEL_ENDPOINT_URL": (
                    "Bedrock runtime HTTPS endpoint"
                ),
                "MODEL_ENDPOINT_MODEL_ID": (
                    "Foundation model id (e.g. anthropic.claude-3-...)"
                ),
                "MODEL_ENDPOINT_PROVIDER": (
                    "Provider literal: 'bedrock'"
                ),
                "BEDROCK_REGION": "AWS region hosting the endpoint",
                "BEDROCK_MODEL_ID": "Alias for MODEL_ENDPOINT_MODEL_ID",
                "BEDROCK_INVOKE_ENDPOINT": (
                    "Alias for MODEL_ENDPOINT_URL"
                ),
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe(self, record_id: str) -> dict[str, Any] | None:
        return self._records.get(record_id)

    def _store_record(
        self,
        *,
        record_id: str,
        model_id: str,
        log_group: str,
        provisioned_throughput_arn: str,
        commitment_duration: str,
        spec: ProvisionSpec,
    ) -> None:
        self._records[record_id] = {
            "RecordId": record_id,
            "ModelId": model_id,
            "LogGroup": log_group,
            "ProvisionedThroughputArn": provisioned_throughput_arn,
            "CommitmentDuration": commitment_duration,
            "CreatedAt": datetime.now(UTC).isoformat(),
            "OrgSlug": spec.organization_slug,
            "AppSlug": spec.app_slug,
            "EnvName": spec.environment_name,
        }

    def _commitment_active(self, record: dict[str, Any]) -> bool:
        # Conservative: any non-empty commitment is treated as
        # active. The control plane that owns the row can blank
        # out the field when the commitment window ends; we don't
        # second-guess time math here.
        return bool(record.get("CommitmentDuration"))

    def _record_id_for(self, *, spec: ProvisionSpec) -> str:
        # Bedrock provisioned-model names: 1-63 chars, alphanumeric +
        # hyphens. We add the same canonicalisation as the
        # OpenSearch driver to keep slugs predictable.
        raw = (
            f"astrolift-{spec.organization_slug}-{spec.app_slug}-"
            f"{spec.environment_name}-"
            f"{spec.service_handle_hint or 'model'}"
        ).lower()
        clean = "".join(
            c if (c.isalnum() or c == "-") else "-" for c in raw
        )
        while "--" in clean:
            clean = clean.replace("--", "-")
        clean = clean.strip("-")
        if not clean or not clean[0].isalpha():
            clean = f"a{clean}"
        return clean[:63]

    def _log_group_for(self, *, record_id: str) -> str:
        return f"{self._config.invocation_log_group_prefix}/{record_id}"

    def _ensure_log_group(self, log_group: str) -> None:
        try:
            self._logs.create_log_group(logGroupName=log_group)
        except Exception as exc:
            if "ResourceAlreadyExists" in type(exc).__name__:
                return
            if "ResourceAlreadyExists" in str(exc):
                return
            # Soft state: failure to create the log group doesn't
            # block provision. Operators inspecting CloudWatch will
            # see the absence; bedrock's own per-region invocation
            # logging config remains the platform's logging fallback.
            return
        try:
            self._logs.put_retention_policy(
                logGroupName=log_group,
                retentionInDays=int(
                    self._config.invocation_log_retention_days,
                ),
            )
        except Exception:
            return

    def _delete_log_group(self, *, log_group: str) -> None:
        try:
            self._logs.delete_log_group(logGroupName=log_group)
        except Exception:
            return


# ----- module-level helpers --------------------------------------------


def _pt_tags(spec: ProvisionSpec) -> dict[str, str]:
    """Tag dict for Bedrock provisioned-throughput requests. The
    SDK expects a flat ``{key: value}`` map here rather than the
    ``[{"Key": ..., "Value": ...}]`` list shape used elsewhere."""
    flat = {t["Key"]: t["Value"] for t in tags_for(spec)}
    return flat
