"""Bedrock capability bindings and durable, owned provisioned throughput.

On-demand access creates no Bedrock resource. Paid throughput uses a versioned
native ARN/intent handle and live ownership checks across activity instances.
AWS commitment terms apply even when force_destroy is requested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.cloud_credentials import CredentialedConfig
from _sdk.managed_service import (
    UPDATE_NOT_SUPPORTED_IN_PLACE,
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
    unsupported_update,
)
from aws.managed._base import (
    ManagedServiceError,
    handle_for,
    parse_handle,
)
from aws.managed._bedrock_throughput import PaidHandle, Throughput, digest, model_arn
from aws.session import aws_client

KIND = "model_endpoint"

# A system-defined cross-region inference profile id: a geography prefix
# before the model id, e.g. ``us.anthropic.claude-sonnet-4-6`` (#2137). An
# application profile or a system profile may also be named by its ARN.
_PROFILE_ID = re.compile(r"^(?:us|eu|apac|us-gov|ca|jp|au|global)\.[a-z0-9-]+\.")
_PROFILE_ARN = re.compile(r"^arn:aws[a-z-]*:bedrock:[a-z0-9-]+:\d{12}:(?:application-)?inference-profile/")

_INVOKE_ACTIONS = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]


def is_inference_profile(model_id: str) -> bool:
    """Whether ``model_id`` names an inference profile, not a foundation model."""
    return bool(_PROFILE_ID.match(model_id) or _PROFILE_ARN.match(model_id))


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
class AmazonBedrockConfig(CredentialedConfig):
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
            self._bedrock = aws_client(
                "bedrock",
                region=config.region,
                credential=config.credential,
            )
        if logs_client is not None:
            self._logs = logs_client
        else:
            self._logs = aws_client(
                "logs",
                region=config.region,
                credential=config.credential,
            )
        # Only a same-instance optimization for resource-free on-demand bindings.
        self._records: dict[str, dict[str, Any]] = {}
        self._throughput = Throughput(self._bedrock, config)

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

        if cfg.get("provisioned_throughput") or (spec.recorded_handle and PaidHandle.parse(spec.recorded_handle)):
            return self._provision_throughput(spec)
        if spec.recorded_handle:
            try:
                _, native = self._throughput.observe(
                    spec.recorded_handle, service=spec.managed_service_id, organization=spec.organization_id
                )
                if native:
                    return ProvisionResult(
                        ok=False,
                        handle="",
                        message="Recorded paid throughput cannot become an on-demand binding implicitly",
                        errors=["replacement_required"],
                    )
            except ManagedServiceError:
                return ProvisionResult(
                    ok=False,
                    handle="",
                    message="Recorded Bedrock identity could not be verified",
                    errors=["throughput_unverified"],
                )
        existing = self._describe(record_id)
        if existing is not None:
            return ProvisionResult(
                ok=True,
                handle=handle_for(kind=KIND, resource_id=record_id),
                ready=not (existing.get("ProvisionedThroughputArn") or ""),
                message=(f"bedrock model endpoint {record_id} already exists (model_id={existing.get('ModelId')})"),
            )

        model_id = cfg.get("model_id") or _SIZE_TO_MODEL_ID.get(spec.size) or self._config.default_model_id

        # CloudWatch log group for invocation logs. Idempotent: a
        # pre-existing group is left alone.
        log_group = self._log_group_for(record_id=record_id)
        self._ensure_log_group(log_group)

        provisioned_throughput_arn = ""
        commitment_duration = ""

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
        cfg = spec.config or {}
        saved = PaidHandle.parse(spec.handle)
        _, record_id = parse_handle(spec.handle)
        existing = self._describe(record_id)
        try:
            if saved or cfg.get("provisioned_throughput") or not existing:
                paid, row = self._throughput.observe(
                    spec.handle, service=spec.managed_service_id, organization=spec.organization_id
                )
                if paid or row:
                    if row is None:
                        return UpdateResult(
                            ok=False, handle=spec.handle, message="Bedrock throughput is missing", errors=["not_found"]
                        )
                    if paid is None:
                        raise ManagedServiceError("Bedrock throughput durable identity is missing")
                    requested = cfg.get("model_id") or (_SIZE_TO_MODEL_ID.get(spec.size) if spec.size else None)
                    if (
                        (requested and digest(model_arn(requested, self._config)) != paid.model_hash)
                        or (
                            "model_units" in cfg
                            and (type(cfg["model_units"]) is not int or cfg["model_units"] != paid.units)
                        )
                        or ("provisioned_throughput" in cfg and cfg["provisioned_throughput"] != paid.term)
                    ):
                        return UpdateResult(
                            ok=False,
                            handle=spec.handle,
                            retryable=False,
                            errors=[UPDATE_NOT_SUPPORTED_IN_PLACE],
                            message=(
                                "No native update was performed. Paid model, units or commitment changes "
                                "need a separately reviewed replacement service with a new identity; "
                                "automatic reprovision of this identity is not supported"
                            ),
                        )
                    if row.get("status") != "InService":
                        return UpdateResult(
                            ok=False,
                            handle=spec.handle,
                            message="Bedrock throughput is not InService",
                            errors=["not_ready"],
                        )
                    return UpdateResult(
                        ok=True,
                        handle=paid.encode(),
                        message="Owned provisioned throughput matches the unchanged configuration",
                    )
        except ManagedServiceError:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message="Bedrock throughput identity or readiness could not be verified",
                errors=["throughput_unverified"],
            )
        if existing is None:
            if not spec.managed_service_id:
                return UpdateResult(
                    ok=False, handle=spec.handle, message="Bedrock capability identity is missing", errors=["not_found"]
                )
            existing = {"ModelId": self._config.default_model_id}

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

        if cfg.get("provisioned_throughput"):
            return unsupported_update(spec.handle, reason="Attaching paid throughput requires explicit provisioning")

        # The persisted desired config supplies fresh on-demand bindings.
        self._records[record_id] = existing

        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=(f"bedrock model endpoint {record_id} updated (model_id={existing['ModelId']})"),
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
        try:
            paid = PaidHandle.parse(spec.handle)
            _, record_id = parse_handle(spec.handle)
            existing = self._describe(record_id)
            if paid:
                if delete_data and not paid.log_prefix_hash:
                    raise ManagedServiceError("Bedrock paid log identity is not recorded; explicit recovery required")
                record_id = paid.record_name
            if paid or not existing or (spec.config or {}).get("provisioned_throughput"):
                paid, row = self._throughput.observe(
                    spec.handle, service=spec.managed_service_id, organization=spec.organization_id
                )
                if row:
                    if paid is None:
                        raise ManagedServiceError("Bedrock throughput durable identity is missing")
                    if self._throughput.commitment_active(row):
                        return DeprovisionResult(
                            ok=False,
                            handle=spec.handle,
                            message="AWS commitment is active or its expiry is unknown; force_destroy cannot bypass it",
                            errors=["commitment_active"],
                        )
                    self._bedrock.delete_provisioned_model_throughput(provisionedModelId=paid.arn)
                    record_id = row["provisionedModelName"]
            if delete_data:
                if not self._delete_log_group(log_group=self._log_group_for(record_id=record_id)):
                    return DeprovisionResult(
                        ok=False,
                        handle=spec.handle,
                        message=(
                            "Native throughput removal accepted or already gone; invocation log deletion unconfirmed"
                        ),
                        errors=["log_cleanup_unconfirmed"],
                    )
                self._records.pop(record_id, None)
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message="Bedrock removal accepted or already gone; logs=" + ("deleted" if delete_data else "preserved"),
            )
        except Exception:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message="Bedrock removal could not be verified or accepted",
                errors=["throughput_unverified"],
            )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="aws", driver="model_endpoint_bedrock")
    def status(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> ServiceStatus:
        try:
            saved = PaidHandle.parse(handle.handle)
            _, record_id = parse_handle(handle.handle)
            existing = self._describe(record_id)
            if existing and not saved:
                return ServiceStatus(
                    handle=handle.handle, state="available", message="On-demand Bedrock capability binding"
                )
            paid, row = self._throughput.observe(
                handle.handle, service=handle.managed_service_id, organization=handle.organization_id
            )
            if row is None:
                return ServiceStatus(
                    handle=handle.handle,
                    state="deprovisioned" if paid else "available",
                    message="Native throughput missing" if paid else "On-demand Bedrock capability binding",
                )
            if paid is None:
                raise ManagedServiceError("Bedrock throughput durable identity is missing")
            if config is not None:
                self._assert_paid_config(paid, config)
            state = {
                "Creating": "provisioning",
                "Updating": "updating",
                "InService": "available",
                "Failed": "error",
            }.get(str(row.get("status")), "error")
            return ServiceStatus(
                handle=handle.handle, state=state, message="Owned Bedrock throughput native state observed"
            )
        except Exception:
            return ServiceStatus(
                handle=handle.handle,
                state="error",
                message="Bedrock throughput identity or readiness could not be verified",
            )

    @driver_op(cloud="aws", driver="model_endpoint_bedrock")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        paid = PaidHandle.parse(handle.handle)
        _, record_id = parse_handle(handle.handle)
        existing = self._describe(record_id) or {}
        cfg = config or {}
        paid_grants = None
        if paid or cfg.get("provisioned_throughput"):
            paid, row = self._throughput.observe(
                handle.handle, service=handle.managed_service_id, organization=handle.organization_id
            )
            if not paid or not row or row.get("status") != "InService":
                raise ManagedServiceError("Bedrock provisioned throughput is not verified InService")
            if config is not None:
                self._assert_paid_config(paid, cfg)
            existing = {"ModelId": paid.arn}
            record_id = row["provisionedModelName"]
            paid_grants = [Grant(resource=paid.arn, actions=list(_INVOKE_ACTIONS))]
        invoke_endpoint = (
            self._config.invoke_endpoint_override or f"https://bedrock-runtime.{self._config.region}.amazonaws.com"
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
        irsa_role_tag_value = f"{self._config.irsa_role_tag_value_prefix}-{record_id}"
        return Binding(
            env_vars={
                # Canonical contract envs
                "MODEL_ENDPOINT_URL": ValueRef(literal=invoke_endpoint),
                "MODEL_DEPLOYMENT_NAME": ValueRef(literal=model_id),
                "MODEL_REGION": ValueRef(literal=self._config.region),
                "MODEL_API_STYLE": ValueRef(literal="bedrock"),
                "MODEL_AUTH_MODE": ValueRef(literal="cloud_identity"),
                "MODEL_ENDPOINT_MODEL_ID": ValueRef(literal=model_id),
                "MODEL_ENDPOINT_PROVIDER": ValueRef(literal="bedrock"),
                # AWS-flavoured aliases
                "BEDROCK_REGION": ValueRef(literal=self._config.region),
                "BEDROCK_MODEL_ID": ValueRef(literal=model_id),
                "BEDROCK_INVOKE_ENDPOINT": ValueRef(
                    literal=invoke_endpoint,
                ),
            },
            iam_grants=paid_grants if paid_grants is not None else self._invoke_grants(model_id),
            notes=(
                f"IRSA role lookup uses tag "
                f"{self._config.irsa_role_tag_key}="
                f"{irsa_role_tag_value}. CloudWatch invocation logs "
                f"persist at {log_group} -- retention "
                f"governed by deprovision delete_data flag."
            ),
        )

    def _invoke_grants(self, model_id: str) -> list[Grant]:
        """The exact resources invoking ``model_id`` needs.

        A foundation model is one ARN. An inference profile is the profile
        plus every foundation model it routes to, in each region it routes to
        (#2137): Bedrock authorizes the call against all of them, so a grant
        on ``foundation-model/us.anthropic...`` (which is not a resource)
        authorizes nothing. Resolved live and fails closed: a profile that
        cannot be read is an error, never a grant that silently does nothing.
        """
        if not is_inference_profile(model_id):
            return [
                Grant(
                    resource=f"arn:aws:bedrock:{self._config.region}::foundation-model/{model_id}",
                    actions=list(_INVOKE_ACTIONS),
                )
            ]
        try:
            profile = self._bedrock.get_inference_profile(inferenceProfileIdentifier=model_id)
        except Exception as exc:
            raise ManagedServiceError(
                f"Bedrock inference profile {model_id!r} could not be resolved in "
                f"{self._config.region}: {exc}. The binding needs the profile's destination "
                "models to grant, so it is refused rather than granted on a resource that "
                "does not exist."
            ) from exc
        profile_arn = str(profile.get("inferenceProfileArn") or "")
        model_arns = sorted({str(m.get("modelArn") or "") for m in profile.get("models") or []} - {""})
        if not profile_arn or not model_arns:
            raise ManagedServiceError(
                f"Bedrock inference profile {model_id!r} returned no ARN or no destination models"
            )
        return [Grant(resource=arn, actions=list(_INVOKE_ACTIONS)) for arn in [profile_arn, *model_arns]]

    @driver_op(cloud="aws", driver="model_endpoint_bedrock")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        # Foundation-model endpoints have no snapshot primitive --
        # state is the provisioned-throughput commitment + the
        # CloudWatch log group, both already durable. We return a
        # deterministic id so the workflow layer's snapshot path
        # gets a handle to track.
        if PaidHandle.parse(handle.handle):
            raise ManagedServiceError("Bedrock provisioned throughput has no snapshot primitive")
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
        if PaidHandle.parse(snapshot.handle) or (target.config or {}).get("provisioned_throughput"):
            return ProvisionResult(
                ok=False,
                handle="",
                message="Bedrock throughput snapshots cannot restore paid resources",
                errors=["snapshot_not_supported"],
            )
        provisioned = self.provision(target)
        if not provisioned.ok:
            return provisioned
        return ProvisionResult(
            ok=True,
            handle=provisioned.handle,
            ready=provisioned.ready,
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
                "MODEL_ENDPOINT_URL": ("Bedrock runtime HTTPS endpoint"),
                "MODEL_DEPLOYMENT_NAME": "Foundation model/profile ID or provisioned throughput ARN used as modelId",
                "MODEL_REGION": "AWS region of the Bedrock runtime",
                "MODEL_API_STYLE": "Client protocol: 'bedrock'",
                "MODEL_AUTH_MODE": "Credential kind: 'cloud_identity' (IRSA)",
                "MODEL_ENDPOINT_MODEL_ID": "Foundation model/profile ID or provisioned throughput ARN",
                "MODEL_ENDPOINT_PROVIDER": ("Provider literal: 'bedrock'"),
                "BEDROCK_REGION": "AWS region hosting the endpoint",
                "BEDROCK_MODEL_ID": "Alias for MODEL_ENDPOINT_MODEL_ID",
                "BEDROCK_INVOKE_ENDPOINT": ("Alias for MODEL_ENDPOINT_URL"),
            },
        )

    def editable_fields(self) -> list[str]:
        # Native paid operations enforce the narrower per-handle restriction.
        return ["model_id"]

    # ---- internals ----------------------------------------------------

    def _assert_paid_config(self, paid: PaidHandle, cfg: dict[str, Any]) -> None:
        requested = (
            cfg.get("model_id") or _SIZE_TO_MODEL_ID.get(str(cfg.get("size", "small"))) or self._config.default_model_id
        )
        units = cfg.get("model_units", 1)
        if (
            digest(model_arn(requested, self._config)) != paid.model_hash
            or type(units) is not int
            or units != paid.units
            or cfg.get("provisioned_throughput") != paid.term
        ):
            raise ManagedServiceError("Bedrock current configuration differs from recorded throughput")

    def _provision_throughput(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            cfg = spec.config or {}
            model = cfg.get("model_id") or _SIZE_TO_MODEL_ID.get(spec.size) or self._config.default_model_id
            expected, tags = self._throughput.expected(spec, model)
            name = "astrolift-" + expected.service.replace("-", "")
            # Validate storage and region bounds before any provider effects.
            partition = (
                "aws-us-gov"
                if self._config.region.startswith("us-gov-")
                else "aws-cn"
                if self._config.region.startswith("cn-")
                else "aws"
            )
            placeholder = f"arn:{partition}:bedrock:{self._config.region}:000000000000:provisioned-model/000000000000"
            replace(expected, arn=placeholder).encode()
            if not re.fullmatch(r"[./_#A-Za-z0-9-]{1,512}", self._log_group_for(record_id=name)):
                raise ManagedServiceError("Bedrock invocation log-group identity is invalid")
            if not re.fullmatch(r"[a-z0-9-]{1,20}", self._config.region):
                raise ManagedServiceError("Bedrock region is invalid")
            candidates = (
                [spec.recorded_handle]
                if spec.recorded_handle
                else [
                    handle_for(kind=KIND, resource_id=name),
                    handle_for(kind=KIND, resource_id=self._record_id_for(spec=spec)),
                ]
            )
            for candidate in dict.fromkeys(candidates):
                if PaidHandle.parse(candidate):
                    saved, row = self._throughput.observe(
                        candidate, service=expected.service, organization=expected.organization
                    )
                else:
                    row = self._throughput.get(parse_handle(candidate)[1])
                    saved = None
                    if row:
                        original_row = row
                        try:
                            saved, row = self._throughput.observe(
                                candidate, service=expected.service, organization=expected.organization
                            )
                        except ManagedServiceError:
                            saved = self._throughput.recover_legacy(spec, expected, original_row)
                            row = original_row
                if row:
                    if saved is None:
                        raise ManagedServiceError("Bedrock throughput durable identity is missing")
                    if replace(saved, arn="", legacy_tags_hash="", legacy_name="") != expected:
                        raise ManagedServiceError("Bedrock recorded throughput does not match the requested intent")
                    if row.get("status") not in ("Creating", "Updating", "InService"):
                        raise ManagedServiceError("Bedrock throughput is failed or unknown")
                    return ProvisionResult(
                        ok=True,
                        handle=saved.encode(),
                        ready=row.get("status") == "InService",
                        message="Owned Bedrock throughput recovered",
                    )
                if spec.recorded_handle:
                    raise ManagedServiceError("Recorded Bedrock throughput is missing; explicit recovery required")
            response = self._bedrock.create_provisioned_model_throughput(
                modelUnits=expected.units,
                provisionedModelName=name,
                modelId=model,
                commitmentDuration=expected.term,
                tags=tags,
                clientRequestToken=digest(["astrolift-bedrock-pt1", expected.service]),
            )
            arn = response.get("provisionedModelArn")
            self._throughput.validate_arn(arn)
            saved = replace(expected, arn=arn)
            row = self._throughput.get(arn)
            if row is None:
                raise ManagedServiceError("Created Bedrock throughput is not yet observable; recover before retry")
            self._throughput.verify(row, saved, service=expected.service, organization=expected.organization)
            if row.get("status") not in ("Creating", "Updating", "InService"):
                raise ManagedServiceError("Created Bedrock throughput is failed or unknown")
            self._ensure_log_group(self._log_group_for(record_id=name))
            return ProvisionResult(
                ok=True,
                handle=saved.encode(),
                ready=False,
                message="Bedrock throughput creation accepted; native readiness pending",
            )
        except Exception:
            return ProvisionResult(
                ok=False,
                handle="",
                message=(
                    "Bedrock throughput identity or creation could not be confirmed; "
                    "retry recovery before any replacement"
                ),
                errors=["throughput_unverified"],
            )

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

    def _record_id_for(self, *, spec: ProvisionSpec) -> str:
        # Bedrock provisioned-model names: 1-63 chars, alphanumeric +
        # hyphens. We add the same canonicalisation as the
        # OpenSearch driver to keep slugs predictable.
        raw = (
            f"astrolift-{spec.organization_slug}-{spec.app_slug}-"
            f"{spec.environment_name}-"
            f"{spec.service_handle_hint or 'model'}"
        ).lower()
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
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

    def _delete_log_group(self, *, log_group: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._logs.delete_log_group(logGroupName=log_group)
        except ClientError as exc:
            return bool(exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException")
        except Exception:
            return False
        return True
