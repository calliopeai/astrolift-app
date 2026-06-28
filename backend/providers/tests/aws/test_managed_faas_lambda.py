"""Tests for the AWS Lambda FaaS managed-service driver (#987).

The astrolift-local container has no moto, so these exercise the driver
against stateful recording fakes for the Lambda + IAM clients. The CDN
custom-origin regression guard at the end ensures the #1010 static-site
S3+OAC path is untouched by the #987 Function-URL custom-origin extension.
"""

from __future__ import annotations

import json

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from aws.managed.faas_lambda import (
    KIND,
    LambdaConfig,
    LambdaDriver,
)

# ---- recording fakes -------------------------------------------------


class _ResourceNotFoundException(Exception):  # noqa: N818 -- mimics SDK class name
    pass


class _ResourceConflictException(Exception):  # noqa: N818 -- mimics SDK class name
    pass


class _InvalidParameterValueException(Exception):  # noqa: N818 -- mimics SDK class name
    pass


class _LambdaExceptions:
    ResourceNotFoundException = _ResourceNotFoundException
    ResourceConflictException = _ResourceConflictException
    InvalidParameterValueException = _InvalidParameterValueException


class _Waiter:
    def __init__(self, log: list, name: str) -> None:
        self._log = log
        self._name = name

    def wait(self, **kwargs):
        self._log.append((f"waiter:{self._name}", kwargs))


class FakeLambda:
    def __init__(self, *, conflict_on_create: bool = False) -> None:
        self.calls: list[tuple] = []
        self.exceptions = _LambdaExceptions()
        self._functions: dict[str, dict] = {}
        self._urls: dict[str, str] = {}
        self._url_auth: dict[str, str] = {}
        self._permissions: set[tuple[str, str]] = set()
        self._conflict_on_create = conflict_on_create

    def _record(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def kwargs_for(self, name: str) -> dict:
        for n, kw in self.calls:
            if n == name:
                return kw
        raise AssertionError(f"{name} was not called")

    def seed_function(self, function_name: str, *, url: str | None = None, url_auth: str = "AWS_IAM") -> None:
        self._functions[function_name] = {
            "FunctionArn": f"arn:aws:lambda:us-east-1:123456789012:function:{function_name}",
            "State": "Active",
            "LastUpdateStatus": "Successful",
        }
        if url:
            self._urls[function_name] = url
            self._url_auth[function_name] = url_auth

    def seed_permission(self, function_name: str, statement_id: str) -> None:
        self._permissions.add((function_name, statement_id))

    # ---- lambda API surface ----
    def get_function(self, **kwargs):
        self._record("get_function", kwargs)
        name = kwargs["FunctionName"]
        if name not in self._functions:
            raise _ResourceNotFoundException(name)
        return {"Configuration": self._functions[name]}

    def get_function_configuration(self, **kwargs):
        self._record("get_function_configuration", kwargs)
        name = kwargs["FunctionName"]
        if name not in self._functions:
            raise _ResourceNotFoundException(name)
        return self._functions[name]

    def create_function(self, **kwargs):
        self._record("create_function", kwargs)
        if self._conflict_on_create:
            raise _ResourceConflictException(kwargs["FunctionName"])
        self.seed_function(kwargs["FunctionName"])

    def update_function_code(self, **kwargs):
        self._record("update_function_code", kwargs)
        self.seed_function(kwargs["FunctionName"])

    def update_function_configuration(self, **kwargs):
        self._record("update_function_configuration", kwargs)

    def create_function_url_config(self, **kwargs):
        self._record("create_function_url_config", kwargs)
        name = kwargs["FunctionName"]
        if name in self._urls:
            # A url already exists -> conflict (drives the self-heal path).
            raise _ResourceConflictException(name)
        url = f"https://{name}.lambda-url.us-east-1.on.aws/"
        self._urls[name] = url
        self._url_auth[name] = kwargs.get("AuthType", "")
        return {"FunctionUrl": url}

    def update_function_url_config(self, **kwargs):
        self._record("update_function_url_config", kwargs)
        name = kwargs["FunctionName"]
        if "AuthType" in kwargs:
            self._url_auth[name] = kwargs["AuthType"]
        return {"FunctionUrl": self._urls.get(name, "")}

    def get_function_url_config(self, **kwargs):
        self._record("get_function_url_config", kwargs)
        name = kwargs["FunctionName"]
        if name not in self._urls:
            raise _ResourceNotFoundException(name)
        return {"FunctionUrl": self._urls[name], "AuthType": self._url_auth.get(name, "")}

    def delete_function_url_config(self, **kwargs):
        self._record("delete_function_url_config", kwargs)
        name = kwargs["FunctionName"]
        if name not in self._urls:
            raise _ResourceNotFoundException(name)
        del self._urls[name]

    def add_permission(self, **kwargs):
        self._record("add_permission", kwargs)
        key = (kwargs["FunctionName"], kwargs["StatementId"])
        if key in self._permissions:
            raise _ResourceConflictException(str(key))
        self._permissions.add(key)

    def remove_permission(self, **kwargs):
        self._record("remove_permission", kwargs)
        key = (kwargs["FunctionName"], kwargs["StatementId"])
        if key not in self._permissions:
            raise _ResourceNotFoundException(str(key))
        self._permissions.discard(key)

    def delete_function(self, **kwargs):
        self._record("delete_function", kwargs)
        name = kwargs["FunctionName"]
        if name not in self._functions:
            raise _ResourceNotFoundException(name)
        del self._functions[name]

    def get_waiter(self, name: str):
        return _Waiter(self.calls, name)


class _EntityAlreadyExistsException(Exception):  # noqa: N818 -- mimics SDK class name
    pass


class _NoSuchEntityException(Exception):  # noqa: N818 -- mimics SDK class name
    pass


class _IAMExceptions:
    EntityAlreadyExistsException = _EntityAlreadyExistsException
    NoSuchEntityException = _NoSuchEntityException


class FakeIAM:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.exceptions = _IAMExceptions()
        self._roles: dict[str, dict] = {}
        self._inline: dict[tuple[str, str], str] = {}

    def _record(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def kwargs_for(self, name: str) -> dict:
        for n, kw in self.calls:
            if n == name:
                return kw
        raise AssertionError(f"{name} was not called")

    def seed_role(self, role_name: str) -> None:
        self._roles[role_name] = {
            "Arn": f"arn:aws:iam::123456789012:role/{role_name}",
        }

    def create_role(self, **kwargs):
        self._record("create_role", kwargs)
        name = kwargs["RoleName"]
        if name in self._roles:
            raise _EntityAlreadyExistsException(name)
        arn = f"arn:aws:iam::123456789012:role/{name}"
        self._roles[name] = {"Arn": arn}
        return {"Role": {"Arn": arn}}

    def get_role(self, **kwargs):
        self._record("get_role", kwargs)
        name = kwargs["RoleName"]
        if name not in self._roles:
            raise _NoSuchEntityException(name)
        return {"Role": self._roles[name]}

    def update_assume_role_policy(self, **kwargs):
        self._record("update_assume_role_policy", kwargs)

    def put_role_policy(self, **kwargs):
        self._record("put_role_policy", kwargs)
        self._inline[(kwargs["RoleName"], kwargs["PolicyName"])] = kwargs["PolicyDocument"]

    def delete_role_policy(self, **kwargs):
        self._record("delete_role_policy", kwargs)
        key = (kwargs["RoleName"], kwargs["PolicyName"])
        if key not in self._inline:
            raise _NoSuchEntityException(str(key))
        del self._inline[key]

    def delete_role(self, **kwargs):
        self._record("delete_role", kwargs)
        name = kwargs["RoleName"]
        if name not in self._roles:
            raise _NoSuchEntityException(name)
        del self._roles[name]


# ---- helpers ---------------------------------------------------------


def _driver(*, conflict_on_create: bool = False, lam: FakeLambda | None = None, iam: FakeIAM | None = None):
    lam = lam or FakeLambda(conflict_on_create=conflict_on_create)
    iam = iam or FakeIAM()
    drv = LambdaDriver(config=LambdaConfig(region="us-east-1"), client=lam, iam_client=iam)
    return drv, lam, iam


def _spec(config: dict | None = None) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug="acme",
        app_id="app-1",
        app_slug="api",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="cluster-1",
        service_handle_hint="faas",
        size="small",
        config=config or {"image_uri": "123.dkr.ecr.us-east-1.amazonaws.com/acme-api@sha256:abc"},
    )


# Function name is deterministic from org/app/env + the service_handle_hint
# ("faas" here, per _spec); the role appends "-fn".
_FN = "astrolift-acme-api-prod-faas"
_ROLE = "astrolift-acme-api-prod-faas-fn"


# ---- provision: idempotency ------------------------------------------


def test_provision_image_mode_probes_then_creates():
    drv, lam, _ = _driver()
    result = drv.provision(_spec())
    assert result.ok is True
    assert result.handle == f"{KIND}/{_FN}"
    # Probe (NotFound) then create -- not an in-place update.
    assert "get_function" in lam.names()
    assert "create_function" in lam.names()
    assert "update_function_code" not in lam.names()
    create = lam.kwargs_for("create_function")
    assert create["PackageType"] == "Image"
    assert create["Code"]["ImageUri"].endswith("@sha256:abc")
    assert create["FunctionName"] == _FN
    # waited for the function to go active.
    assert ("waiter:function_active_v2", {"FunctionName": _FN}) in lam.calls


def test_provision_existing_reconciles_via_update():
    lam = FakeLambda()
    lam.seed_function(_FN)
    drv, lam, _ = _driver(lam=lam)
    result = drv.provision(_spec({"image_uri": "repo@sha256:new"}))
    assert result.ok is True
    # Existing -> reconcile in place; never re-create.
    assert "create_function" not in lam.names()
    assert "update_function_code" in lam.names()
    assert "update_function_configuration" in lam.names()
    assert lam.kwargs_for("update_function_code")["ImageUri"] == "repo@sha256:new"


def test_provision_create_conflict_falls_back_to_update():
    drv, lam, _ = _driver(conflict_on_create=True)
    result = drv.provision(_spec())
    assert result.ok is True
    # Probe says absent, create raced into a conflict -> reconcile.
    assert "create_function" in lam.names()
    assert "update_function_code" in lam.names()
    assert "update_function_configuration" in lam.names()


def test_provision_image_mode_requires_image_uri():
    drv, lam, iam = _driver()
    result = drv.provision(_spec({"package_type": "image"}))
    assert result.ok is False
    assert "image_uri" in result.message
    # No cloud mutation attempted on a validation failure.
    assert "create_function" not in lam.names()
    assert "create_role" not in iam.names()


def test_provision_zip_mode_sets_runtime_handler():
    drv, lam, _ = _driver()
    result = drv.provision(
        _spec(
            {
                "package_type": "zip",
                "s3_bucket": "astrolift-staging",
                "s3_key": "acme/api.zip",
                "runtime": "python3.12",
                "handler": "app.handler",
            },
        ),
    )
    assert result.ok is True
    create = lam.kwargs_for("create_function")
    assert create["PackageType"] == "Zip"
    assert create["Code"] == {"S3Bucket": "astrolift-staging", "S3Key": "acme/api.zip"}
    assert create["Runtime"] == "python3.12"
    assert create["Handler"] == "app.handler"


def test_two_faas_workloads_get_distinct_function_names():
    # Two faas workloads in the SAME org/app/env must not collide onto one
    # Lambda. The per-workload service_handle_hint scopes the name; dropping it
    # from _function_name would make these equal (silent overwrite).
    import dataclasses

    drv, _, _ = _driver()
    a = drv._function_name(_spec())  # hint defaults to "faas"
    b = drv._function_name(dataclasses.replace(_spec(), service_handle_hint="worker-prod-fn"))
    assert a != b
    assert a.startswith("astrolift-") and b.startswith("astrolift-")


# ---- update: code-then-config serialization --------------------------


def test_update_serializes_code_before_config_with_wait():
    drv, lam, _ = _driver()
    result = drv.update(UpdateSpec(handle=f"{KIND}/{_FN}", config={"image_uri": "repo@sha256:v2"}))
    assert result.ok is True
    tracked = ("update_function_code", "update_function_configuration")
    order = [n for n in lam.names() if n in tracked or n.startswith("waiter:")]
    # code update -> wait for it to settle -> config update -> wait.
    assert order == [
        "update_function_code",
        "waiter:function_updated_v2",
        "update_function_configuration",
        "waiter:function_updated_v2",
    ]


# ---- public Function URL ---------------------------------------------


def test_public_creates_aws_iam_function_url_not_public():
    # #1035: a public faas Function URL is AWS_IAM (NOT NONE), and provision
    # must NOT add a public ("*") invoke permission -- the function is reachable
    # only through the fronting CloudFront distribution (granted post-cdn).
    # Falsifiable: reverting to AuthType=NONE + Principal "*" fails both asserts.
    drv, lam, _ = _driver()
    result = drv.provision(_spec({"image_uri": "repo@sha256:abc", "public": True}))
    assert result.ok is True
    assert lam.kwargs_for("create_function_url_config")["AuthType"] == "AWS_IAM"
    # No public grant added during provision.
    public_grants = [
        kw for n, kw in lam.calls if n == "add_permission" and kw.get("Principal") == "*"
    ]
    assert public_grants == []


def test_public_reasserts_aws_iam_on_preexisting_none_url():
    # Self-heal/migration: a function created under the pre-#1035 public-NONE
    # shape must be flipped to AWS_IAM on re-provision (and its legacy public
    # grant reaped). The create raises conflict (url exists) -> update path.
    lam = FakeLambda()
    lam.seed_function(_FN, url=f"https://{_FN}.lambda-url.us-east-1.on.aws/", url_auth="NONE")
    lam.seed_permission(_FN, "AstroliftFunctionUrlPublic")
    drv, lam, _ = _driver(lam=lam)
    result = drv.provision(_spec({"image_uri": "repo@sha256:abc", "public": True}))
    assert result.ok is True
    assert lam.kwargs_for("update_function_url_config")["AuthType"] == "AWS_IAM"
    assert lam._url_auth[_FN] == "AWS_IAM"
    # Legacy public grant removed; nothing else lingers as public.
    assert ("remove_permission", {"FunctionName": _FN, "StatementId": "AstroliftFunctionUrlPublic"}) in lam.calls
    assert (_FN, "AstroliftFunctionUrlPublic") not in lam._permissions


def test_private_does_not_create_function_url():
    drv, lam, _ = _driver()
    drv.provision(_spec({"image_uri": "repo@sha256:abc"}))
    assert "create_function_url_config" not in lam.names()
    assert "add_permission" not in lam.names()


def test_allow_cloudfront_invoke_scopes_to_distribution():
    # #1035: the only invoke grant on the AWS_IAM URL is the CloudFront service
    # principal scoped to ONE distribution SourceArn. Falsifiable: a Principal
    # "*" / missing SourceArn would fail these asserts.
    dist_arn = "arn:aws:cloudfront::123456789012:distribution/E123"
    drv, lam, _ = _driver()
    drv.allow_cloudfront_invoke(_FN, dist_arn)
    perm = lam.kwargs_for("add_permission")
    assert perm["StatementId"] == "AstroliftFunctionUrlCloudFront"
    assert perm["Action"] == "lambda:InvokeFunctionUrl"
    assert perm["Principal"] == "cloudfront.amazonaws.com"
    assert perm["SourceArn"] == dist_arn
    assert perm["FunctionUrlAuthType"] == "AWS_IAM"


def test_allow_cloudfront_invoke_is_idempotent():
    dist_arn = "arn:aws:cloudfront::123456789012:distribution/E123"
    drv, lam, _ = _driver()
    drv.allow_cloudfront_invoke(_FN, dist_arn)
    # A second grant under the same StatementId conflicts; it must be swallowed,
    # not raised (re-provision must converge).
    drv.allow_cloudfront_invoke(_FN, dist_arn)
    assert (_FN, "AstroliftFunctionUrlCloudFront") in lam._permissions


def test_allow_cloudfront_invoke_noop_without_arn():
    drv, lam, _ = _driver()
    drv.allow_cloudfront_invoke(_FN, "")
    assert "add_permission" not in lam.names()


# ---- execution role: SERVICE trust, not OIDC -------------------------


def test_exec_role_has_lambda_service_trust_not_oidc():
    drv, _, iam = _driver()
    drv.provision(_spec())
    trust = json.loads(iam.kwargs_for("create_role")["AssumeRolePolicyDocument"])
    stmt = trust["Statement"][0]
    assert stmt["Principal"] == {"Service": "lambda.amazonaws.com"}
    assert stmt["Action"] == "sts:AssumeRole"
    # Decisively NOT the IRSA OIDC web-identity trust.
    assert "Federated" not in stmt["Principal"]
    assert stmt["Action"] != "sts:AssumeRoleWithWebIdentity"
    # Role is astrolift-* name-scoped at root path (matches CreateRole grant).
    create = iam.kwargs_for("create_role")
    assert create["RoleName"] == _ROLE
    assert create["RoleName"].startswith("astrolift-")
    assert create["Path"] == "/"


def test_exec_role_folds_bound_grants_into_inline_policy():
    drv, _, iam = _driver()
    drv.provision(
        _spec(
            {
                "image_uri": "repo@sha256:abc",
                "grants": [{"actions": ["s3:GetObject"], "resource": "arn:aws:s3:::bkt/*"}],
            },
        ),
    )
    policy = json.loads(iam.kwargs_for("put_role_policy")["PolicyDocument"])
    actions = [s["Action"] for s in policy["Statement"]]
    # basic-execution logs statement is always present...
    assert ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"] in actions
    # ...plus the bound managed-service grant folded in.
    assert ["s3:GetObject"] in actions


def test_exec_role_self_heals_when_already_exists():
    iam = FakeIAM()
    iam.seed_role(_ROLE)
    drv, _, iam = _driver(iam=iam)
    result = drv.provision(_spec())
    assert result.ok is True
    # Existing role: re-assert trust instead of failing.
    assert "update_assume_role_policy" in iam.names()
    assert "put_role_policy" in iam.names()


# ---- deprovision: NotFound tolerance ---------------------------------


def test_deprovision_deletes_function_url_function_and_role():
    lam = FakeLambda()
    lam.seed_function(_FN, url="https://x.lambda-url.us-east-1.on.aws/")
    iam = FakeIAM()
    iam.seed_role(_ROLE)
    iam._inline[(_ROLE, "astrolift-faas-policy")] = "{}"
    drv, lam, iam = _driver(lam=lam, iam=iam)
    result = drv.deprovision(DeprovisionSpec(handle=f"{KIND}/{_FN}"))
    assert result.ok is True
    assert "delete_function_url_config" in lam.names()
    assert "delete_function" in lam.names()
    assert "delete_role_policy" in iam.names()
    assert "delete_role" in iam.names()


def test_deprovision_idempotent_when_everything_gone():
    # Nothing seeded: every delete hits NotFound and must be swallowed.
    drv, lam, iam = _driver()
    result = drv.deprovision(DeprovisionSpec(handle=f"{KIND}/{_FN}"))
    assert result.ok is True
    assert "delete_function" in lam.names()
    assert "delete_role" in iam.names()


# ---- status + binding ------------------------------------------------


def test_status_available_when_active():
    lam = FakeLambda()
    lam.seed_function(_FN)
    drv, _, _ = _driver(lam=lam)
    st = drv.status(ServiceHandle(handle=f"{KIND}/{_FN}"))
    assert st.state == "available"


def test_status_deprovisioned_when_missing():
    drv, _, _ = _driver()
    st = drv.status(ServiceHandle(handle=f"{KIND}/{_FN}"))
    assert st.state == "deprovisioned"


def test_binding_env_and_invoke_grant_shape():
    lam = FakeLambda()
    lam.seed_function(_FN, url="https://x.lambda-url.us-east-1.on.aws/")
    drv, _, _ = _driver(lam=lam)
    binding = drv.binding(ServiceHandle(handle=f"{KIND}/{_FN}"))
    assert binding.env_vars["FUNCTION_NAME"].literal == _FN
    assert binding.env_vars["FUNCTION_URL"].literal == "https://x.lambda-url.us-east-1.on.aws/"
    assert len(binding.iam_grants) == 1
    grant = binding.iam_grants[0]
    assert grant.actions == ["lambda:InvokeFunction"]
    assert grant.resource.endswith(f"function:{_FN}")


# ---- managed_config_for(kind="faas") ---------------------------------


def test_managed_config_for_faas_returns_lambda_config():
    from types import SimpleNamespace

    from core.cluster_observability import managed_config_for

    cluster = SimpleNamespace(slug="aws-prod", region="us-west-2", provider_config={}, auth_config={})
    cfg = managed_config_for("aws", cluster, kind="faas")
    assert isinstance(cfg, LambdaConfig)
    assert cfg.region == "us-west-2"
    assert cfg.default_architecture == "arm64"

    pinned = managed_config_for(
        "aws",
        SimpleNamespace(
            slug="aws-prod",
            region="us-west-2",
            provider_config={"faas_default_architecture": "x86_64", "faas_log_retention_days": 30},
            auth_config={},
        ),
        kind="faas",
    )
    assert pinned.default_architecture == "x86_64"
    assert pinned.log_retention_days == 30


# ---- #1010 cdn custom-origin extension: static-site regression guard --


def _cf_driver():
    from aws.managed.cdn_cloudfront import CloudFrontConfig, CloudFrontDriver

    # No client needed: we exercise the pure config builder.
    return CloudFrontDriver(config=CloudFrontConfig(), client=object(), s3_client=object())


def test_cdn_custom_origin_renders_lambda_oac_custom_origin():
    # #1035: a custom (Lambda Function URL) origin now carries a Lambda OAC id so
    # CloudFront sigv4-signs the AWS_IAM Function URL -- but stays a custom (not
    # S3) origin. Falsifiable: dropping the OAC attach leaves OriginAccessControlId empty.
    drv = _cf_driver()
    config = drv._build_distribution_config(
        spec=_spec(),
        origin_bucket="",
        custom_origin_domain="abc.lambda-url.us-east-1.on.aws",
        origin_region="us-east-1",
        oac_id="oac-lambda-1",
        aliases=[],
        acm_cert_arn="",
        spa=False,
        index="index.html",
        comment="astrolift faas",
    )
    origin = config["Origins"]["Items"][0]
    assert origin["DomainName"] == "abc.lambda-url.us-east-1.on.aws"
    assert origin["CustomOriginConfig"]["OriginProtocolPolicy"] == "https-only"
    assert origin["OriginAccessControlId"] == "oac-lambda-1"
    # A custom origin must NOT carry the S3 origin config.
    assert "S3OriginConfig" not in origin
    # OAC sigv4 to Lambda needs managed cache/origin-request policies, not
    # legacy ForwardedValues (#1035); no default root object.
    assert config["DefaultCacheBehavior"]["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    assert config["DefaultCacheBehavior"]["OriginRequestPolicyId"] == "b689b0a8-53d0-40ab-baf2-68738e2966ac"
    assert "ForwardedValues" not in config["DefaultCacheBehavior"]
    assert config["DefaultRootObject"] == ""


def test_cdn_s3_origin_still_renders_oac():
    drv = _cf_driver()
    config = drv._build_distribution_config(
        spec=_spec(),
        origin_bucket="my-bucket",
        custom_origin_domain="",
        origin_region="us-east-1",
        oac_id="oac-123",
        aliases=[],
        acm_cert_arn="",
        spa=False,
        index="index.html",
        comment="astrolift static",
    )
    origin = config["Origins"]["Items"][0]
    # Static-site path is unchanged: S3 domain + OAC, no custom-origin config.
    assert origin["DomainName"] == "my-bucket.s3.us-east-1.amazonaws.com"
    assert origin["OriginAccessControlId"] == "oac-123"
    assert origin["S3OriginConfig"] == {"OriginAccessIdentity": ""}
    assert "CustomOriginConfig" not in origin
    assert config["DefaultRootObject"] == "index.html"
    assert config["DefaultCacheBehavior"]["ForwardedValues"]["QueryString"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
