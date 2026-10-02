"""AWS Lambda FaaS managed-service driver (#987 provider-managed FaaS).

Spec ref: spec 23-provider-plugin-aws + _sdk/managed_service.py.

A ``faas`` workload (kind generic, variant ``lambda``) is a provider-run
function: the cloud executes the code, there is no pod and the manifest
render emits zero K8s resources. v1 packages the function as a container
image pulled from the per-app ECR repo the kaniko BuildDriver (#978)
already builds and pushes; the image digest flows in via
``ProvisionSpec.config['image_uri']``. Zip packaging is parsed but its
build pipeline is a follow-up.

A *public* faas workload (``faas_public``) gets a Lambda Function URL with
``AuthType=AWS_IAM`` (NOT public/``NONE`` — common org guardrails block
public Function URLs, #1035). The shipped public invoke path is an **API
Gateway HTTP API** (#987/#1035 pivot): a separate ``api_gateway``
managed-service row (see ``api_gateway_http.py``) proxies to the function
ARN via an ``AWS_PROXY`` integration (``lambda:InvokeFunction``); the
Function URL itself is not on that path.

The original design fronted the AWS_IAM Function URL through a ``cdn``
managed-service row whose CloudFront distribution signed requests with a
Lambda Origin Access Control (sigv4) — the secure proxy model, mirroring how
a static_site implies an object_store + cdn pair reached via an S3 OAC. That
path is a confirmed dead-end on the target account (a textbook OAC config
still returns 403) and is **parked** pending #1039. Its plumbing is retained
here but no longer called: ``allow_cloudfront_invoke`` (with
``_CLOUDFRONT_INVOKE_STATEMENT_ID``) grants ``lambda:InvokeFunctionUrl`` to
the CloudFront service principal scoped to one distribution's SourceArn, kept
for a potential revival.

The execution role is minted here with a **service trust**
(``lambda.amazonaws.com``), not the OIDC web-identity trust IRSA uses for
pods. It is ``astrolift-*`` name-scoped (root path) so it matches the
control-plane CreateRole prefix grant, and is reaped both here on
deprovision and by the per-app identity-role teardown fan-out.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from _sdk._telemetry import driver_op
from _sdk.cloud_credentials import CredentialedConfig
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
from _sdk.physical_naming import managed_service_identity, physical_name
from aws._errors import map_client_error
from aws._naming import iam_role_name
from aws.managed._base import (
    ManagedServiceError,
    handle_for,
    live_ownership_refusal,
    parse_handle,
    tags_for,
)
from aws.session import aws_client

log = logging.getLogger("aws.managed.faas_lambda")

KIND = "faas"

# The inline policy name is deterministic so deprovision can delete it by
# name (no ListRolePolicies grant required).
_INLINE_POLICY_NAME = "astrolift-faas-policy"
# Statement id for the legacy public ("*") Function-URL invoke grant. No
# longer added (#1035); kept only so a re-provision can REMOVE it from a
# function created under the pre-#1035 public-NONE shape.
_PUBLIC_URL_STATEMENT_ID = "AstroliftFunctionUrlPublic"
# Statement id for the CloudFront-scoped Function-URL invoke grant (#1035):
# only the fronting CloudFront distribution may invoke the AWS_IAM URL.
_CLOUDFRONT_INVOKE_STATEMENT_ID = "AstroliftFunctionUrlCloudFront"

# CreateFunction can race IAM trust propagation: a freshly-minted role is
# not yet assumable by Lambda, surfacing as InvalidParameterValueException
# ("The role defined for the function cannot be assumed by Lambda"). Bounded
# retry so a first-create succeeds without operator intervention (mirrors the
# kaniko first-build IRSA race, #978).
_ROLE_PROPAGATION_RETRIES = 6
# grants became inline policy statements on the execution role the function's
# code runs as, with whatever actions and resources a config named: any
# identity at all in the shared account (#2087).
_GRANTS_REFUSAL = (
    "faas config grants are no longer supported: the execution role carries basic execution only, "
    "and a function reaches a managed service through that service's binding"
)
_ROLE_PROPAGATION_SLEEP = 5


@dataclass(frozen=True)
class LambdaConfig(CredentialedConfig):
    """Driver-instance config bound from the cluster's plugin config."""

    region: str = "us-east-1"
    role_path_prefix: str = "/"
    """IAM path for the execution role. Root (``/``) so the role ARN is
    ``role/astrolift-<...>`` — matching the ``astrolift-*`` name prefix the
    control-plane task role is scoped to grant ``iam:CreateRole`` on (same
    rationale as IRSAConfig.role_path)."""
    default_architecture: str = "arm64"
    log_retention_days: int = 14
    """Retention for the function's CloudWatch log group. Reserved for the
    follow-up that pre-creates the log group; Lambda auto-creates it today."""
    account_id: str = ""


class LambdaDriver(ManagedServiceDriver):
    KIND = KIND

    def __init__(
        self,
        *,
        config: LambdaConfig,
        client: Any | None = None,
        iam_client: Any | None = None,
    ) -> None:
        self._config = config
        if client is not None:
            self._lambda = client
        else:
            self._lambda = aws_client("lambda", region=config.region, credential=config.credential)
        if iam_client is not None:
            self._iam = iam_client
        else:
            self._iam = aws_client("iam", region=config.region, credential=config.credential)

    # ---- lifecycle ------------------------------------------------

    @driver_op(
        cloud="aws",
        driver="faas_lambda",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        invalid = self._validate_packaging(cfg)
        if invalid is not None:
            return ProvisionResult(ok=False, handle="", message=invalid, errors=[invalid])
        if cfg.get("grants"):
            return ProvisionResult(ok=False, handle="", message=_GRANTS_REFUSAL, errors=[_GRANTS_REFUSAL])

        function_name = ""
        try:
            managed_service_identity(spec.managed_service_id)
            function_name = self._function_name(spec)
            existing = self._owned_function(function_name, spec.managed_service_id)
            if spec.recorded_handle and existing is None:
                raise ManagedServiceError("recorded Lambda function is missing; refusing replacement")
            role_name = self._role_name_for(function_name)
            role = self._owned_role(role_name, spec.managed_service_id)
            url = self._function_url(function_name) if existing else None
            if existing and role is None:
                raise ManagedServiceError("recorded Lambda execution role is missing")
            role_arn = self._ensure_exec_role(role_name=role_name, function_name=function_name, spec=spec)
            if existing:
                self._apply_code_then_config(function_name, cfg, role_arn=role_arn)
            else:
                try:
                    response = self._create_function(self._build_create_args(spec, function_name, role_arn))
                    self._validate_function_metadata(function_name, response)
                except self._lambda.exceptions.ResourceConflictException:
                    # A conflict is not an ownership proof. Re-read both parents
                    # before changing a function created by a concurrent caller.
                    if self._owned_function(function_name, spec.managed_service_id) is None:
                        raise ManagedServiceError("raced Lambda function is missing") from None
                    if self._owned_role(role_name, spec.managed_service_id) is None:
                        raise ManagedServiceError("raced Lambda execution role is missing") from None
                    self._function_url(function_name)
                    self._apply_code_then_config(function_name, cfg, role_arn=role_arn)
            self._wait_active(function_name)
            if self._owned_function(function_name, spec.managed_service_id) is None:
                raise ManagedServiceError("Lambda function disappeared after readiness wait")
            if bool(cfg.get("public", False)):
                self._ensure_function_url(function_name, existing=url)
        except Exception as exc:
            return ProvisionResult(
                ok=False, handle=spec.recorded_handle or "", message=f"provision Lambda: {exc}", errors=[str(exc)]
            )

        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=function_name),
            message=f"function {function_name} provisioned",
        )

    @driver_op(cloud="aws", driver="faas_lambda")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        cfg = spec.config or {}
        if cfg.get("grants"):
            return UpdateResult(ok=False, handle=spec.handle, message=_GRANTS_REFUSAL, errors=[_GRANTS_REFUSAL])
        invalid = self._validate_packaging(cfg)
        if invalid:
            return UpdateResult(False, spec.handle, invalid, [invalid])
        try:
            function_name = self._target(spec.handle)
            if self._owned_function(function_name, spec.managed_service_id) is None:
                raise ManagedServiceError("recorded Lambda function is missing")
            role_name = self._role_name_for(function_name)
            role = self._owned_role(role_name, spec.managed_service_id)
            if role is None:
                raise ManagedServiceError("recorded Lambda execution role is missing")
            self._function_url(function_name)
            self._apply_code_then_config(function_name, cfg, role_arn=role["Arn"])
            self._put_exec_policy(role_name, function_name)
        except (ManagedServiceError, ValueError) as exc:
            return UpdateResult(False, spec.handle, str(exc), ["ownership_refused"], retryable=False)
        except Exception as exc:
            return UpdateResult(ok=False, handle=spec.handle, message=f"update function: {exc}", errors=[str(exc)])
        return UpdateResult(ok=True, handle=spec.handle, message=f"function {function_name} updated")

    @driver_op(cloud="aws", driver="faas_lambda", audit=True, sensitive_kind="managed_service_deprovision")
    def deprovision(
        self, spec: DeprovisionSpec, *, delete_data: bool = False, force_destroy: bool = False
    ) -> DeprovisionResult:
        del delete_data, force_destroy
        try:
            function_name = self._target(spec.handle)
            # Preflight the whole tree before its first deletion. Force never
            # bypasses live ownership, including a role left by partial create.
            function = self._owned_function(function_name, spec.managed_service_id)
            role_name = self._role_name_for(function_name)
            role = self._owned_role(role_name, spec.managed_service_id)
            url = self._function_url(function_name)
            if url is not None:
                self._lambda.delete_function_url_config(FunctionName=function_name)
            if function is not None:
                self._lambda.delete_function(FunctionName=function_name)
            if role is not None:
                self._delete_exec_role(role_name)
        except (ManagedServiceError, ValueError) as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["ownership_refused"], retryable=False)
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete Lambda: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, f"function {function_name} deleted")

    # ---- read-only ops --------------------------------------------

    @driver_op(cloud="aws", driver="faas_lambda")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            function_name = self._target(handle.handle)
            resp = self._lambda.get_function_configuration(FunctionName=function_name)
            self._validate_function_metadata(function_name, resp)
        except self._lambda.exceptions.ResourceNotFoundException:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"function {function_name} does not exist",
            )
        except Exception as exc:
            return ServiceStatus(handle=handle.handle, state="error", message=str(exc))
        state = resp.get("State", "")
        last = resp.get("LastUpdateStatus", "")
        if state == "Failed" or last == "Failed":
            return ServiceStatus(
                handle=handle.handle,
                state="error",
                message=f"function {function_name} in {state}/{last}",
            )
        if state == "Active" and last in ("Successful", ""):
            return ServiceStatus(
                handle=handle.handle,
                state="available",
                message=f"function {function_name} active",
            )
        return ServiceStatus(
            handle=handle.handle,
            state="provisioning",
            message=f"function {function_name} state {state}/{last}",
        )

    @driver_op(cloud="aws", driver="faas_lambda")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        function_name = self._target(handle.handle)
        resp = self._lambda.get_function_configuration(FunctionName=function_name)
        self._validate_function_metadata(function_name, resp)
        function_arn = resp["FunctionArn"]
        url = self._function_url(function_name)
        function_url = str(url["FunctionUrl"]) if url else ""
        return Binding(
            env_vars={
                "FUNCTION_NAME": ValueRef(literal=function_name),
                "FUNCTION_URL": ValueRef(literal=function_url),
                # The rest of the faas envelope (#1402). FUNCTION_ARN is the
                # portable resource-locator slot every other faas driver
                # fills; on AWS it is literally an ARN.
                "FUNCTION_ARN": ValueRef(literal=function_arn),
                "FUNCTION_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=[
                Grant(resource=function_arn, actions=["lambda:InvokeFunction"]),
            ],
            notes="Lambda invoke scoped to this function.",
        )

    @driver_op(cloud="aws", driver="faas_lambda")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError(
            "Lambda functions have no snapshot semantic -- the deployable "
            "artifact is the container image / zip in its registry/bucket",
        )

    @driver_op(cloud="aws", driver="faas_lambda")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        return ProvisionResult(
            ok=False,
            handle="",
            message="Lambda has no snapshot, hence no restore",
            errors=["not_implemented"],
        )

    @driver_op(cloud="aws", driver="faas_lambda", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "package_type": {
                    "type": "string",
                    "enum": ["image", "zip"],
                    "description": "image: pull from the per-app ECR repo; zip: code object in S3.",
                },
                "image_uri": {
                    "type": "string",
                    "description": "ECR image reference (<repo>@<digest>) for package_type=image.",
                },
                "s3_bucket": {"type": "string", "description": "Code zip bucket (package_type=zip)."},
                "s3_key": {"type": "string", "description": "Code zip key (package_type=zip)."},
                "runtime": {"type": "string", "description": "Lambda runtime (package_type=zip)."},
                "handler": {"type": "string", "description": "Entrypoint handler (package_type=zip)."},
                "memory_mb": {"type": "integer", "description": "Function memory in MB."},
                "timeout_seconds": {"type": "integer", "description": "Function timeout in seconds."},
                "architecture": {"type": "string", "enum": ["arm64", "x86_64"]},
                "environment": {"type": "object", "description": "Environment variables."},
                "public": {
                    "type": "boolean",
                    "description": (
                        "Create an AWS_IAM Function URL fronted by a cdn row "
                        "(CloudFront Lambda OAC); the function is reachable only "
                        "through the distribution, never publicly."
                    ),
                },
            },
        }

    @driver_op(cloud="aws", driver="faas_lambda", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "FUNCTION_NAME": "Lambda function name",
                "FUNCTION_ARN": "Portable resource locator; the Lambda function ARN",
                "FUNCTION_REGION": "AWS region",
                "FUNCTION_URL": "Public Function URL (empty when not public)",
            }
        )

    # ---- internals ------------------------------------------------

    def _identity_config(self) -> None:
        if (
            re.fullmatch(r"[0-9]{12}", self._config.account_id) is None
            or re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", self._config.region) is None
        ):
            raise ManagedServiceError("Lambda requires a region and 12-digit account_id")
        if (
            len(self._config.role_path_prefix) > 512
            or re.fullmatch(r"/(?:[!-~]+/)?", self._config.role_path_prefix) is None
        ):
            raise ManagedServiceError("invalid Lambda execution role path")

    def _partition(self) -> str:
        region = self._config.region
        if region.startswith("cn-"):
            return "aws-cn"
        if region.startswith("us-gov-"):
            return "aws-us-gov"
        if region.startswith("us-iso-"):
            return "aws-iso"
        if region.startswith("us-isob-"):
            return "aws-iso-b"
        return "aws"

    def _target(self, handle: str) -> str:
        self._identity_config()
        kind, name = parse_handle(handle)
        if kind != KIND or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) is None:
            raise ManagedServiceError("invalid Lambda managed-service handle")
        return name

    def _function_name(self, spec: ProvisionSpec) -> str:
        if spec.recorded_handle:
            return self._target(spec.recorded_handle)
        name = physical_name(spec.managed_service_id, prefix="astrolift", max_length=64)
        return self._target(handle_for(kind=KIND, resource_id=name))

    def _function_arn(self, name: str) -> str:
        return f"arn:{self._partition()}:lambda:{self._config.region}:{self._config.account_id}:function:{name}"

    def _role_arn(self, name: str) -> str:
        return f"arn:{self._partition()}:iam::{self._config.account_id}:role{self._config.role_path_prefix}{name}"

    def _validate_function_metadata(self, name: str, metadata: dict[str, Any]) -> None:
        if (
            metadata.get("FunctionName") != name
            or metadata.get("FunctionArn") != self._function_arn(name)
            or metadata.get("Role") != self._role_arn(self._role_name_for(name))
        ):
            raise ManagedServiceError("Lambda function identity or execution-role parent mismatch")

    def _assert_owner(self, tags: Any, service_id: str, resource: str) -> None:
        managed_service_identity(service_id)
        refusal = live_ownership_refusal(tags, managed_service_id=service_id, resource=resource)
        if refusal:
            raise ManagedServiceError(refusal)

    def _owned_function(self, name: str, service_id: str) -> dict[str, Any] | None:
        managed_service_identity(service_id)
        try:
            response = self._lambda.get_function(FunctionName=name)
        except self._lambda.exceptions.ResourceNotFoundException:
            return None
        metadata = response.get("Configuration") or {}
        self._validate_function_metadata(name, metadata)
        self._assert_owner(response.get("Tags"), service_id, "Lambda function")
        return metadata

    def _owned_role(self, name: str, service_id: str) -> dict[str, Any] | None:
        managed_service_identity(service_id)
        try:
            role = self._iam.get_role(RoleName=name)["Role"]
        except self._iam.exceptions.NoSuchEntityException:
            return None
        if (
            role.get("RoleName") != name
            or role.get("Path") != self._config.role_path_prefix
            or role.get("Arn") != self._role_arn(name)
        ):
            raise ManagedServiceError("Lambda execution-role identity mismatch")
        self._assert_owner(role.get("Tags"), service_id, "Lambda execution role")
        return role

    def _function_url(self, name: str) -> dict[str, Any] | None:
        try:
            response = self._lambda.get_function_url_config(FunctionName=name)
        except self._lambda.exceptions.ResourceNotFoundException:
            return None
        self._validate_url(name, response)
        return response

    def _validate_url(self, name: str, response: dict[str, Any]) -> None:
        url = urlsplit(str(response.get("FunctionUrl") or ""))
        if (
            response.get("FunctionArn") != self._function_arn(name)
            or response.get("AuthType") not in {"AWS_IAM", "NONE"}
            or url.scheme != "https"
            or re.fullmatch(
                r"[a-z0-9-]+\.lambda-url\." + re.escape(self._config.region) + r"\.on\.aws", url.hostname or ""
            )
            is None
            or url.username is not None
            or url.password is not None
            or url.port is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ManagedServiceError("Lambda Function URL parent identity mismatch")

    def _role_name_for(self, function_name: str) -> str:
        # Reconstructible from the function name alone (which is all the
        # handle carries) so deprovision can reap the role without storing it.
        return iam_role_name(function_name, "fn", max_len=64)

    def _validate_packaging(self, cfg: dict[str, Any]) -> str | None:
        package_type = str(cfg.get("package_type", "image")).lower()
        if package_type == "image":
            if not str(cfg.get("image_uri", "")).strip():
                return "faas provision (image mode) requires config.image_uri"
            return None
        if package_type == "zip":
            missing = [k for k in ("s3_bucket", "s3_key", "runtime", "handler") if not str(cfg.get(k, "")).strip()]
            if missing:
                return f"faas provision (zip mode) requires config.{', config.'.join(missing)}"
            return None
        return f"faas provision: unknown package_type {package_type!r} (expected image|zip)"

    def _service_trust_policy(self) -> dict[str, Any]:
        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                },
            ],
        }

    def _exec_inline_policy(self, function_name: str) -> dict[str, Any]:
        statements: list[dict[str, Any]] = [
            {
                "Sid": "AstroliftLambdaBasicExecution",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": (
                    f"arn:{self._partition()}:logs:{self._config.region}:{self._config.account_id}:"
                    f"log-group:/aws/lambda/{function_name}:*"
                ),
            },
        ]
        return {"Version": "2012-10-17", "Statement": statements}

    def _ensure_exec_role(self, *, role_name: str, function_name: str, spec: ProvisionSpec) -> str:
        trust = self._service_trust_policy()
        role = self._owned_role(role_name, spec.managed_service_id)
        if role is None:
            with contextlib.suppress(self._iam.exceptions.EntityAlreadyExistsException):
                self._iam.create_role(
                    Path=self._config.role_path_prefix,
                    RoleName=role_name,
                    AssumeRolePolicyDocument=json.dumps(trust),
                    Description=f"Astrolift Lambda execution role for {function_name}",
                    Tags=tags_for(spec),
                )
            role = self._owned_role(role_name, spec.managed_service_id)
            if role is None:
                raise ManagedServiceError("created Lambda execution role is missing")
        self._iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(trust))
        self._put_exec_policy(role_name, function_name)
        return str(role["Arn"])

    def _put_exec_policy(self, role_name: str, function_name: str) -> None:
        try:
            self._iam.put_role_policy(
                RoleName=role_name,
                PolicyName=_INLINE_POLICY_NAME,
                PolicyDocument=json.dumps(self._exec_inline_policy(function_name)),
            )
        except Exception as exc:
            raise map_client_error(exc) from exc

    def _delete_exec_role(self, role_name: str) -> None:
        # Inline policy name is deterministic, so delete by name (no
        # ListRolePolicies). Both ops NotFound-tolerant for idempotent reruns.
        try:
            self._iam.delete_role_policy(RoleName=role_name, PolicyName=_INLINE_POLICY_NAME)
        except self._iam.exceptions.NoSuchEntityException:
            pass
        except Exception as exc:
            raise map_client_error(exc) from exc
        try:
            self._iam.delete_role(RoleName=role_name)
        except self._iam.exceptions.NoSuchEntityException:
            pass
        except Exception as exc:
            raise map_client_error(exc) from exc

    def _build_create_args(self, spec: ProvisionSpec, function_name: str, role_arn: str) -> dict[str, Any]:
        cfg = spec.config or {}
        package_type = str(cfg.get("package_type", "image")).lower()
        args: dict[str, Any] = {
            "FunctionName": function_name,
            "Role": role_arn,
            "MemorySize": int(cfg.get("memory_mb", 512)),
            "Timeout": int(cfg.get("timeout_seconds", 30)),
            "Architectures": [str(cfg.get("architecture") or self._config.default_architecture)],
            "Environment": {"Variables": dict(cfg.get("environment") or {})},
            "Tags": {t["Key"]: t["Value"] for t in tags_for(spec)},
        }
        if package_type == "image":
            args["PackageType"] = "Image"
            args["Code"] = {"ImageUri": str(cfg["image_uri"]).strip()}
        else:
            args["PackageType"] = "Zip"
            args["Code"] = {"S3Bucket": str(cfg["s3_bucket"]), "S3Key": str(cfg["s3_key"])}
            args["Runtime"] = str(cfg["runtime"])
            args["Handler"] = str(cfg["handler"])
        return args

    def _create_function(self, create_args: dict[str, Any]) -> dict[str, Any]:
        # Bounded retry past the IAM trust-propagation race; ResourceConflict
        # propagates to the caller's reconcile path.
        for attempt in range(_ROLE_PROPAGATION_RETRIES):
            try:
                return dict(self._lambda.create_function(**create_args))
            except self._lambda.exceptions.InvalidParameterValueException:
                if attempt == _ROLE_PROPAGATION_RETRIES - 1:
                    raise
                time.sleep(_ROLE_PROPAGATION_SLEEP)
        raise ManagedServiceError("Lambda create retry limit exceeded")

    def _apply_code_then_config(
        self,
        function_name: str,
        cfg: dict[str, Any],
        *,
        role_arn: str | None,
    ) -> None:
        # Lambda serializes updates: the code update must settle before the
        # config update or the latter throws ResourceConflictException.
        package_type = str(cfg.get("package_type", "image")).lower()
        code_args: dict[str, Any] = {"FunctionName": function_name}
        if package_type == "image":
            code_args["ImageUri"] = str(cfg["image_uri"]).strip()
        else:
            code_args["S3Bucket"] = str(cfg["s3_bucket"])
            code_args["S3Key"] = str(cfg["s3_key"])
        self._lambda.update_function_code(**code_args)
        self._wait_updated(function_name)

        conf_args: dict[str, Any] = {
            "FunctionName": function_name,
            "MemorySize": int(cfg.get("memory_mb", 512)),
            "Timeout": int(cfg.get("timeout_seconds", 30)),
            "Environment": {"Variables": dict(cfg.get("environment") or {})},
        }
        if role_arn:
            conf_args["Role"] = role_arn
        if package_type == "zip":
            conf_args["Runtime"] = str(cfg["runtime"])
            conf_args["Handler"] = str(cfg["handler"])
        self._lambda.update_function_configuration(**conf_args)
        self._wait_updated(function_name)

    def _ensure_function_url(self, function_name: str, *, existing: dict[str, Any] | None = None) -> str:
        # Existing children were preflighted before any parent effects.
        if existing is not None:
            response = self._lambda.update_function_url_config(FunctionName=function_name, AuthType="AWS_IAM")
        else:
            try:
                response = self._lambda.create_function_url_config(FunctionName=function_name, AuthType="AWS_IAM")
            except self._lambda.exceptions.ResourceConflictException:
                self._function_url(function_name)
                response = self._lambda.update_function_url_config(FunctionName=function_name, AuthType="AWS_IAM")
        self._validate_url(function_name, response)
        with contextlib.suppress(self._lambda.exceptions.ResourceNotFoundException):
            self._lambda.remove_permission(FunctionName=function_name, StatementId=_PUBLIC_URL_STATEMENT_ID)
        return str(response["FunctionUrl"])

    @driver_op(cloud="aws", driver="faas_lambda", audit=True)
    def allow_cloudfront_invoke(
        self, function_name: str, distribution_arn: str, *, managed_service_id: str = ""
    ) -> None:
        """Grant ONLY the given CloudFront distribution permission to invoke the
        function's (AWS_IAM) Function URL (#1035).

        Post-cdn step: the distribution ARN doesn't exist until the cdn
        provisions, so ``ensure_faas_services`` calls this after the cdn row is
        ACTIVE. Idempotency requires the existing statement to name exactly
        this function and distribution; a conflicting statement is refused."""
        if not distribution_arn:
            return
        self._target(handle_for(kind=KIND, resource_id=function_name))
        if self._owned_function(function_name, managed_service_id) is None:
            raise ManagedServiceError("recorded Lambda function is missing")
        expected = f"arn:{self._partition()}:cloudfront::{self._config.account_id}:distribution/"
        if (
            not distribution_arn.startswith(expected)
            or re.fullmatch(r"[A-Z0-9]+", distribution_arn[len(expected) :]) is None
        ):
            raise ManagedServiceError("CloudFront distribution identity mismatch")
        if self._cloudfront_permission(function_name, distribution_arn):
            return
        try:
            response = self._lambda.add_permission(
                FunctionName=function_name,
                StatementId=_CLOUDFRONT_INVOKE_STATEMENT_ID,
                Action="lambda:InvokeFunctionUrl",
                Principal="cloudfront.amazonaws.com",
                SourceArn=distribution_arn,
                FunctionUrlAuthType="AWS_IAM",
            )
        except self._lambda.exceptions.ResourceConflictException:
            if not self._cloudfront_permission(function_name, distribution_arn):
                raise ManagedServiceError("conflicting CloudFront invoke permission is unavailable") from None
            return
        self._validate_cloudfront_statement(function_name, distribution_arn, json.loads(response["Statement"]))

    def _validate_cloudfront_statement(self, name: str, distribution_arn: str, statement: dict[str, Any]) -> None:
        if (
            not isinstance(statement, dict)
            or statement.get("Sid") != _CLOUDFRONT_INVOKE_STATEMENT_ID
            or statement.get("Resource") != self._function_arn(name)
            or statement.get("Action") != "lambda:InvokeFunctionUrl"
            or statement.get("Effect") != "Allow"
            or statement.get("Principal") != {"Service": "cloudfront.amazonaws.com"}
            or (statement.get("Condition") or {}).get("ArnLike", {}).get("AWS:SourceArn") != distribution_arn
        ):
            raise ManagedServiceError("CloudFront invoke permission parent or distribution mismatch")

    def _cloudfront_permission(self, name: str, distribution_arn: str) -> bool:
        try:
            response = self._lambda.get_policy(FunctionName=name)
        except self._lambda.exceptions.ResourceNotFoundException:
            return False
        statements = json.loads(response["Policy"]).get("Statement")
        if not isinstance(statements, list) or any(not isinstance(row, dict) for row in statements):
            raise ManagedServiceError("Lambda permission metadata is invalid")
        selected = [row for row in statements if row.get("Sid") == _CLOUDFRONT_INVOKE_STATEMENT_ID]
        if len(selected) > 1:
            raise ManagedServiceError("Lambda permission identity is ambiguous")
        if selected:
            self._validate_cloudfront_statement(name, distribution_arn, selected[0])
        return bool(selected)

    def _wait_active(self, function_name: str) -> None:
        self._lambda.get_waiter("function_active_v2").wait(FunctionName=function_name)

    def _wait_updated(self, function_name: str) -> None:
        self._lambda.get_waiter("function_updated_v2").wait(FunctionName=function_name)
