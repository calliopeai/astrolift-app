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
public Function URLs, #1035). The function is reached only through a
``cdn`` managed-service row whose CloudFront distribution fronts the
Function URL via a Lambda Origin Access Control (sigv4-signed) — the secure
proxy model. The Lambda grants ``lambda:InvokeFunctionUrl`` to the
CloudFront service principal scoped to that one distribution's SourceArn
(a post-cdn step: the distribution ARN doesn't exist until the cdn
provisions). This mirrors how a static_site implies an object_store + cdn
pair reached via an S3 OAC.

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
import time
from dataclasses import dataclass
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
from aws._errors import map_client_error
from aws._naming import iam_role_name
from aws.managed._base import (
    ManagedServiceError,
    handle_for,
    parse_handle,
    tags_for,
)

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
_ROLE_PROPAGATION_SLEEP = 5


@dataclass(frozen=True)
class LambdaConfig:
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
            import boto3

            self._lambda = boto3.client("lambda", region_name=config.region)
        if iam_client is not None:
            self._iam = iam_client
        else:
            import boto3

            self._iam = boto3.client("iam", region_name=config.region)

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

        function_name = self._function_name(spec)
        role_name = self._role_name_for(function_name)

        try:
            role_arn = self._ensure_exec_role(
                role_name=role_name,
                function_name=function_name,
                grants=list(cfg.get("grants") or []),
            )
        except Exception as exc:
            return ProvisionResult(ok=False, handle="", message=f"exec-role: {exc}", errors=[str(exc)])

        try:
            if self._function_exists(function_name):
                # Idempotent: a prior attempt created the function. Reconcile
                # code + config in place rather than re-creating.
                self._apply_code_then_config(function_name, cfg, role_arn=role_arn)
            else:
                try:
                    self._create_function(self._build_create_args(spec, function_name, role_arn))
                except self._lambda.exceptions.ResourceConflictException:
                    # Concurrent provision created it between the probe and the
                    # create — reconcile instead.
                    self._apply_code_then_config(function_name, cfg, role_arn=role_arn)
            self._wait_active(function_name)
        except Exception as exc:
            return ProvisionResult(ok=False, handle="", message=f"create/update function: {exc}", errors=[str(exc)])

        if bool(cfg.get("public", False)):
            try:
                self._ensure_function_url(function_name)
            except Exception as exc:
                return ProvisionResult(
                    ok=False,
                    handle="",
                    message=f"function-url: {exc}",
                    errors=[str(exc)],
                )

        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=function_name),
            message=f"function {function_name} provisioned",
        )

    @driver_op(cloud="aws", driver="faas_lambda")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, function_name = parse_handle(spec.handle)
        try:
            # The new code reference (image digest / zip key) and any
            # env/memory/timeout changes arrive via spec.config. Role is not
            # changed on update (left as-is).
            self._apply_code_then_config(function_name, spec.config or {}, role_arn=None)
        except Exception as exc:
            return UpdateResult(ok=False, handle=spec.handle, message=f"update function: {exc}", errors=[str(exc)])
        return UpdateResult(ok=True, handle=spec.handle, message=f"function {function_name} updated")

    @driver_op(
        cloud="aws",
        driver="faas_lambda",
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
        # A function holds no persistent state, so delete_data / force_destroy
        # take the same path (mirrors the cdn driver).
        del delete_data, force_destroy
        _, function_name = parse_handle(spec.handle)

        # Function URL config dies with the function, but delete it explicitly
        # first so a half-torn-down resource (function gone, url config orphan)
        # also converges. All steps NotFound-tolerant (#998).
        try:
            self._lambda.delete_function_url_config(FunctionName=function_name)
        except self._lambda.exceptions.ResourceNotFoundException:
            pass
        except Exception as exc:
            return DeprovisionResult(
                ok=False, handle=spec.handle, message=f"delete function-url: {exc}", errors=[str(exc)],
            )

        try:
            self._lambda.delete_function(FunctionName=function_name)
        except self._lambda.exceptions.ResourceNotFoundException:
            pass
        except Exception as exc:
            return DeprovisionResult(
                ok=False, handle=spec.handle, message=f"delete_function: {exc}", errors=[str(exc)],
            )

        try:
            self._delete_exec_role(self._role_name_for(function_name))
        except Exception as exc:
            return DeprovisionResult(
                ok=False, handle=spec.handle, message=f"delete exec-role: {exc}", errors=[str(exc)],
            )

        return DeprovisionResult(ok=True, handle=spec.handle, message=f"function {function_name} deleted")

    # ---- read-only ops --------------------------------------------

    @driver_op(cloud="aws", driver="faas_lambda")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, function_name = parse_handle(handle.handle)
        try:
            resp = self._lambda.get_function_configuration(FunctionName=function_name)
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
                handle=handle.handle, state="error", message=f"function {function_name} in {state}/{last}",
            )
        if state == "Active" and last in ("Successful", ""):
            return ServiceStatus(
                handle=handle.handle, state="available", message=f"function {function_name} active",
            )
        return ServiceStatus(
            handle=handle.handle, state="provisioning", message=f"function {function_name} state {state}/{last}",
        )

    @driver_op(cloud="aws", driver="faas_lambda")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        _, function_name = parse_handle(handle.handle)
        resp = self._lambda.get_function_configuration(FunctionName=function_name)
        function_arn = resp.get("FunctionArn", "")
        function_url = ""
        with contextlib.suppress(self._lambda.exceptions.ResourceNotFoundException):
            function_url = self._lambda.get_function_url_config(FunctionName=function_name).get("FunctionUrl", "")
        return Binding(
            env_vars={
                "FUNCTION_NAME": ValueRef(literal=function_name),
                "FUNCTION_URL": ValueRef(literal=function_url),
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
                "grants": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Bound managed-service IAM grants folded into the execution role.",
                },
            },
        }

    @driver_op(cloud="aws", driver="faas_lambda", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "FUNCTION_NAME": "Lambda function name",
                "FUNCTION_URL": "Public Function URL (empty when not public)",
            }
        )

    # ---- internals ------------------------------------------------

    def _function_name(self, spec: ProvisionSpec) -> str:
        # Deterministic, charset/length-safe, astrolift-* prefixed so it
        # matches the control-plane function:astrolift-* grant. The
        # service_handle_hint (the per-workload row name, e.g.
        # ``<workload>-<env>-fn``) is folded in so two faas workloads in the
        # same app/env get distinct functions instead of silently overwriting
        # one another (matches object_store_s3._bucket_name_for, which scopes
        # by the same hint).
        return iam_role_name(
            "astrolift",
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
            max_len=64,
        )

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

    def _exec_inline_policy(self, function_name: str, grants: list[dict[str, Any]]) -> dict[str, Any]:
        statements: list[dict[str, Any]] = [
            {
                "Sid": "AstroliftLambdaBasicExecution",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": f"arn:aws:logs:{self._config.region}:*:log-group:/aws/lambda/{function_name}:*",
            },
        ]
        for grant in grants:
            actions = grant.get("actions") or []
            resource = grant.get("resource")
            if actions and resource:
                statements.append({"Effect": "Allow", "Action": actions, "Resource": resource})
        return {"Version": "2012-10-17", "Statement": statements}

    def _ensure_exec_role(
        self,
        *,
        role_name: str,
        function_name: str,
        grants: list[dict[str, Any]],
    ) -> str:
        trust = self._service_trust_policy()
        try:
            resp = self._iam.create_role(
                Path=self._config.role_path_prefix,
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust),
                Description=f"Astrolift Lambda execution role for {function_name}",
                Tags=[{"Key": "astrolift.io/managed-by", "Value": "platform"}],
            )
            role_arn = resp["Role"]["Arn"]
        except self._iam.exceptions.EntityAlreadyExistsException:
            # Idempotent + self-healing: re-assert the (correct) trust in case
            # a prior create wrote a stale one (mirrors create_identity_role).
            try:
                role_arn = self._iam.get_role(RoleName=role_name)["Role"]["Arn"]
                self._iam.update_assume_role_policy(
                    RoleName=role_name,
                    PolicyDocument=json.dumps(trust),
                )
            except Exception as exc:
                raise map_client_error(exc) from exc
        except Exception as exc:
            raise map_client_error(exc) from exc

        try:
            self._iam.put_role_policy(
                RoleName=role_name,
                PolicyName=_INLINE_POLICY_NAME,
                PolicyDocument=json.dumps(self._exec_inline_policy(function_name, grants)),
            )
        except Exception as exc:
            raise map_client_error(exc) from exc
        return role_arn

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

    def _create_function(self, create_args: dict[str, Any]) -> None:
        # Bounded retry past the IAM trust-propagation race; ResourceConflict
        # propagates to the caller's reconcile path.
        for attempt in range(_ROLE_PROPAGATION_RETRIES):
            try:
                self._lambda.create_function(**create_args)
                return
            except self._lambda.exceptions.InvalidParameterValueException:
                if attempt == _ROLE_PROPAGATION_RETRIES - 1:
                    raise
                time.sleep(_ROLE_PROPAGATION_SLEEP)

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

    def _function_exists(self, function_name: str) -> bool:
        try:
            self._lambda.get_function(FunctionName=function_name)
            return True
        except self._lambda.exceptions.ResourceNotFoundException:
            return False

    def _ensure_function_url(self, function_name: str) -> str:
        # AuthType=AWS_IAM (not NONE): public Function URLs are blocked by common
        # org guardrails, and an IAM-auth URL fronted by CloudFront (Lambda OAC,
        # sigv4) is the secure proxy model (#1035). The function is NOT public;
        # the CloudFront-scoped invoke grant is added post-cdn via
        # allow_cloudfront_invoke once the distribution ARN exists.
        try:
            resp = self._lambda.create_function_url_config(FunctionName=function_name, AuthType="AWS_IAM")
            url = resp.get("FunctionUrl", "")
        except self._lambda.exceptions.ResourceConflictException:
            # Idempotent + self-healing: re-assert AWS_IAM in case a prior
            # provision created the URL as AuthType=NONE (the pre-#1035 public
            # shape) — this migrates the live function off the public URL.
            self._lambda.update_function_url_config(FunctionName=function_name, AuthType="AWS_IAM")
            url = self._lambda.get_function_url_config(FunctionName=function_name).get("FunctionUrl", "")
        # Reap the legacy public ("*") invoke grant if a pre-#1035 provision left
        # one — the function is now reachable only through CloudFront. Absent ==
        # ResourceNotFoundException (nothing to remove).
        with contextlib.suppress(self._lambda.exceptions.ResourceNotFoundException):
            self._lambda.remove_permission(
                FunctionName=function_name,
                StatementId=_PUBLIC_URL_STATEMENT_ID,
            )
        return url

    @driver_op(cloud="aws", driver="faas_lambda", audit=True)
    def allow_cloudfront_invoke(self, function_name: str, distribution_arn: str) -> None:
        """Grant ONLY the given CloudFront distribution permission to invoke the
        function's (AWS_IAM) Function URL (#1035).

        Post-cdn step: the distribution ARN doesn't exist until the cdn
        provisions, so ``ensure_faas_services`` calls this after the cdn row is
        ACTIVE. Idempotent — a re-grant under the same StatementId raises
        ResourceConflictException, which is swallowed (the distribution id is
        stable per app/env, so the SourceArn doesn't drift across redeploys)."""
        if not distribution_arn:
            return
        with contextlib.suppress(self._lambda.exceptions.ResourceConflictException):
            self._lambda.add_permission(
                FunctionName=function_name,
                StatementId=_CLOUDFRONT_INVOKE_STATEMENT_ID,
                Action="lambda:InvokeFunctionUrl",
                Principal="cloudfront.amazonaws.com",
                SourceArn=distribution_arn,
                FunctionUrlAuthType="AWS_IAM",
            )

    def _wait_active(self, function_name: str) -> None:
        self._lambda.get_waiter("function_active_v2").wait(FunctionName=function_name)

    def _wait_updated(self, function_name: str) -> None:
        self._lambda.get_waiter("function_updated_v2").wait(FunctionName=function_name)
