"""Provider-managed FaaS topology activities (#987).

A ``faas`` workload runs as a provider function (AWS Lambda, v1) -- no
container, no pod; the manifest render emits zero K8s resources. The
operator declares only ``kind = "faas"``; the platform auto-provisions the
implied managed services and (for a public function) the public HTTPS
invoke surface:

* ``ensure_faas_services`` -- idempotently ensure the implied managed-
  service rows exist and are ACTIVE: the ``faas`` (lambda) row FIRST, then
  -- when ``faas_public`` -- an ``api_gateway`` (HTTP API) row that proxies
  to the function (origin-dependency order: the function ARN must exist
  before the API's AWS_PROXY integration is wired). Runs BEFORE
  ``ensure_workload_identity`` so any bound managed-service grants fold into
  the execution role.

The public invoke surface is an API Gateway HTTP API, NOT
CloudFront-OAC -> Lambda-Function-URL (#1035 pivot): that sigv4 model is a
confirmed dead-end on AWS (a full textbook OAC config still 403s, and the
account also blocks public Function URLs). An HTTP API's ``execute-api``
endpoint is PUBLIC by default and invokes the function directly by ARN
(``lambda:InvokeFunction``), so the Lambda needs no Function URL at all --
the api_gateway row carries the whole invoke path. Custom domains
(apigatewayv2 domain name + REGIONAL ACM cert + Route53) are a fast-follow;
v1 serves over the ``execute-api`` URL, so neither
``ensure_cloudfront_cert`` nor ``ensure_static_dns`` touch a faas workload.

v1 packages the function as a container image from the per-app ECR repo
the kaniko BuildDriver (#978) already builds and pushes; the digest the
``build_image`` activity recorded on the Deployment row flows into the
lambda row's provision config (so ``build_image`` must run first). Zip
packaging is parsed but its build pipeline is a follow-up.

All bodies are sync-wrapped via ``sync_to_async`` so Django ORM + boto3
access stays off the activity event loop.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

# Reuse the generic managed-service-row machinery from the static_site
# activities (these helpers are topology-agnostic -- they ensure/provision a
# ManagedService row and read a handle's resource id). Importing them here
# (rather than copying) keeps one implementation of the idempotent,
# soft-delete-aware ensure + the shared provision/finalize path, and lets the
# tests monkeypatch ``faas._provision_row`` exactly as the static-site tests
# monkeypatch ``static_site._provision_row``.
from astrolift_workflows.activities.static_site import (
    _ensure_service_row,
    _normalized_manifest,
    _provision_row,
    _region_account,
    _resolve_account_id,
)

log = logging.getLogger("astrolift_workflows.activities.faas")


# ---- shared helpers ------------------------------------------------------


def _faas_workloads(manifest) -> list[Any]:
    if manifest is None:
        return []
    return [w for w in manifest.workloads if getattr(w, "kind", "") == "faas"]


def _service_names(workload_name: str, env) -> tuple[str, str]:
    """The (faas/lambda, api_gateway) ManagedService row names for a faas
    workload in one environment.

    Env-scoped for the same reason static_site is: the manifest lives on
    RegisteredApp, so every AppEnvironment yields the identical
    ``workload_name``; without the env component a second env's deploy would
    find the first env's row (the unique-active constraint is (app, kind,
    name)) and reprovision against it.

    The public-invoke surface is now an API Gateway HTTP API (#1035 pivot --
    CloudFront-OAC -> Function-URL sigv4 is a dead-end on AWS), so the second
    row is the ``api_gateway`` row, not a ``cdn`` row."""
    env_label = getattr(env, "name", "") or "default"
    return f"{workload_name}-{env_label}-fn", f"{workload_name}-{env_label}-api"


def _resource_id(row) -> str:
    """The backend resource id (function name / api id) parsed from a
    provisioned row's handle (``<kind>/<resource_id>``)."""
    from aws.managed._base import parse_handle

    if not row or not row.backend_ref:
        return ""
    return parse_handle(row.backend_ref)[1]


def _image_uri(repo_uri: str, digest: str, image_tag: str) -> str:
    """The container image reference for a PackageType=Image Lambda.

    Prefer the immutable ``<repo>@<digest>`` the build recorded (so a tag
    re-push can't silently swap the function's code); fall back to
    ``<repo>:<tag>`` when the digest wasn't read back. Empty when no repo --
    the driver's provision validation then rejects it with a clear message
    rather than creating a broken function."""
    repo_uri = (repo_uri or "").strip()
    if not repo_uri:
        return ""
    if digest:
        return f"{repo_uri}@{digest}"
    if image_tag:
        return f"{repo_uri}:{image_tag}"
    return ""


def _lambda_config(w, *, repo_uri: str, digest: str, image_tag: str) -> dict[str, Any]:
    """The provision config for a faas workload's lambda row -- the shape the
    LambdaDriver's ProvisionSpec.config expects.

    ``public`` is always False: the public invoke surface is the API Gateway
    HTTP API (the AWS_PROXY integration invokes the function by ARN), so the
    Lambda needs no Function URL even for a ``faas_public`` workload (#1035
    pivot). The LambdaDriver's Function-URL code is retained for a possible
    future direct mode but is not requested on this path."""
    package_type = (w.faas_package_type or "image").strip().lower()
    cfg: dict[str, Any] = {
        "size": "small",
        "package_type": package_type,
        "memory_mb": int(w.faas_memory_mb),
        "timeout_seconds": int(w.faas_timeout_seconds),
        "architecture": w.faas_architecture or "arm64",
        "public": False,
        "environment": {},
        # Cross-service grants folded into the execution role would be gathered
        # the way ensure_workload_identity does for pods; a v1 public faas binds
        # no other managed service, so basic-execution only.
        "grants": [],
    }
    if package_type == "image":
        cfg["image_uri"] = _image_uri(repo_uri, digest, image_tag)
    else:
        # Zip packaging pipeline is a follow-up (#987): carry the runtime +
        # handler so the row is shaped, but s3_bucket/s3_key come from the
        # not-yet-built zip build step -- provision validation surfaces the gap
        # rather than silently mis-deploying.
        cfg["runtime"] = w.faas_runtime
        cfg["handler"] = w.faas_handler
    return cfg


# ---- ensure_faas_services ------------------------------------------------


def _ensure_faas_services_sync(deployment_id: int) -> dict[str, Any]:
    from astrolift_lifecycle.models import Deployment

    deployment = Deployment.objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster__provider_plugin",
    ).get(pk=deployment_id)
    app = deployment.registered_app
    env = deployment.app_environment
    manifest = _normalized_manifest(app)
    faas = _faas_workloads(manifest)
    if not faas:
        return {"stub": True, "ensured": []}

    cluster = env.tenant_cluster if env else None
    if cluster is None:
        return {"stub": True, "reason": "no tenant_cluster bound", "ensured": []}

    region, account = _region_account(cluster)

    # Image-mode code reference: the digest build_image recorded on the
    # Deployment row + the app's ECR repo URI. build_image runs before this
    # activity in the deploy flow, so a real digest is present for image apps.
    repo_uri = (app.registry_repo_uri or "").strip()
    digest = (deployment.image_digest or "").strip()
    image_tag = (deployment.image_tag or "").strip()

    ensured: list[dict[str, str]] = []
    for w in faas:
        fn_name, api_name = _service_names(w.name, env)

        # 1. Lambda first -- the api_gateway integration's target. Provision
        #    fully to ACTIVE so its function ARN exists before the API wires it.
        fn_config = _lambda_config(w, repo_uri=repo_uri, digest=digest, image_tag=image_tag)
        fn_row = _ensure_service_row(
            app=app, env=env, kind="faas", name=fn_name, variant="lambda", config=fn_config
        )
        # Realign config on an existing row (image digest / memory may have
        # changed) before provisioning.
        if fn_row.config != fn_config:
            fn_row.config = fn_config
            fn_row.save(update_fields=["config", "updated_at", "version"])
        _provision_row(fn_row)
        fn_row.refresh_from_db()

        entry: dict[str, str] = {"workload": w.name, "function": _resource_id(fn_row)}

        # 2. api_gateway (public only) -- an HTTP API whose $default route
        #    AWS_PROXY-proxies to the function ARN. The driver writes the
        #    lambda:InvokeFunction grant (principal apigateway.amazonaws.com,
        #    SourceArn scoped to this API) itself during provision, so there is
        #    no post-step. The function ARN must exist first (hence the ordering
        #    above), so fail loudly if the account id can't be resolved.
        if w.faas_public:
            function_name = _resource_id(fn_row)
            account_id = _resolve_account_id(cluster, account)
            if not account_id:
                raise RuntimeError(
                    f"faas {w.name}: cannot resolve AWS account id for the "
                    "Lambda function ARN (provider_config.account_id empty and "
                    "STS discovery failed)",
                )
            lambda_arn = f"arn:aws:lambda:{region}:{account_id}:function:{function_name}"
            api_config = {"size": "small", "lambda_function_arn": lambda_arn}
            api_row = _ensure_service_row(
                app=app, env=env, kind="api_gateway", name=api_name, variant="http_api", config=api_config
            )
            if api_row.config != api_config:
                api_row.config = api_config
                api_row.save(update_fields=["config", "updated_at", "version"])
            _provision_row(api_row)
            api_row.refresh_from_db()
            entry["api_id"] = _resource_id(api_row)

        ensured.append(entry)

    return {"stub": False, "ensured": ensured}


@activity.defn(name="astrolift.deploy.ensure_faas_services")
async def ensure_faas_services(deployment_id: int) -> dict:
    """Ensure the (faas/lambda [, api_gateway]) managed services for every faas
    workload in the deployment exist + are ACTIVE (lambda first, then -- when
    public -- the HTTP API that proxies to it).

    No-op fast-return when the manifest has no faas workload, so non-faas and
    pre-existing apps pay nothing."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_ensure_faas_services_sync)(deployment_id)
    activity.heartbeat()
    return result
