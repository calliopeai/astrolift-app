"""Provider-managed FaaS topology activities (#987).

A ``faas`` workload runs as a provider function (AWS Lambda, v1) -- no
container, no pod; the manifest render emits zero K8s resources. The
operator declares only ``kind = "faas"``; the platform auto-provisions the
implied managed services and (for a public function) the public HTTPS
surface + DNS:

* ``ensure_faas_services`` -- idempotently ensure the implied managed-
  service rows exist and are ACTIVE: the ``faas`` (lambda) row FIRST, then
  -- when ``faas_public`` -- a ``cdn`` row whose origin is the AWS_IAM
  Lambda Function URL (origin-dependency order, exactly like static_site's
  bucket -> cdn), and finally a POST-cdn step that grants the Lambda's
  Function-URL invoke permission scoped to ONLY that distribution's
  SourceArn (#1035 -- the distribution ARN doesn't exist until the cdn
  provisions). Runs BEFORE ``ensure_workload_identity`` so any bound
  managed-service grants fold into the execution role.

The public custom-domain TLS cert and the ``CNAME host -> CloudFront``
record are NOT owned here: the generalized ``ensure_cloudfront_cert`` /
``ensure_static_dns`` / ``delete_static_dns_records`` activities (in
``static_site.py``) cover both static_site and public faas workloads --
a Function URL has no Ingress, so external-dns never owns the record
(identical to static_site). This avoids duplicating the cert/DNS logic.

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
from urllib.parse import urlparse

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
    """The (faas/lambda, cdn) ManagedService row names for a faas workload in
    one environment.

    Env-scoped for the same reason static_site is: the manifest lives on
    RegisteredApp, so every AppEnvironment yields the identical
    ``workload_name``; without the env component a second env's deploy would
    find the first env's row (the unique-active constraint is (app, kind,
    name)) and reprovision against it.

    The ``cdn`` name uses the IDENTICAL ``{workload}-{env}-cdn`` convention
    static_site uses, so the generalized ``ensure_static_dns`` /
    ``delete_static_dns_records`` resolve the same cdn row for a faas workload
    via static_site's ``_service_names`` -- the cdn row name depends only on
    the workload name + env, not the topology kind."""
    env_label = getattr(env, "name", "") or "default"
    return f"{workload_name}-{env_label}-fn", f"{workload_name}-{env_label}-cdn"


def _faas_driver(cluster, faas_row):
    """Resolve the faas driver from the ROW's own variant (cloud-agnostic),
    mirroring static_site's ``_cdn_driver`` -- a future GCP/Azure faas row
    resolves its own driver instead of silently picking Lambda here."""
    from astrolift_drivers.registry import plugins
    from core.cluster_observability import managed_config_for

    plugin_slug = cluster.provider_plugin.slug
    variant = getattr(faas_row, "variant", "") or "lambda"
    driver_cls = plugins.get(plugin_slug, f"managed:faas:{variant}")
    cfg = managed_config_for(plugin_slug, cluster, kind="faas", variant=variant)
    return driver_cls(config=cfg)


def _resource_id(row) -> str:
    """The backend resource id (function name / distribution id) parsed from a
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
    LambdaDriver's ProvisionSpec.config expects."""
    package_type = (w.faas_package_type or "image").strip().lower()
    cfg: dict[str, Any] = {
        "size": "small",
        "package_type": package_type,
        "memory_mb": int(w.faas_memory_mb),
        "timeout_seconds": int(w.faas_timeout_seconds),
        "architecture": w.faas_architecture or "arm64",
        "public": bool(w.faas_public),
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


def _function_url_host(faas_row, cluster) -> str:
    """The Function URL host (no scheme/path) for a provisioned faas row, to
    use as the cdn distribution's custom origin.

    Prefer the materialized ``FUNCTION_URL`` binding (written on provision
    finalize); fall back to a live ``binding()`` call. Mirrors static_site's
    ``_cdn_domain_for``. CloudFront's origin DomainName must be the bare host,
    so the full ``https://<id>.lambda-url.<region>.on.aws/`` is reduced to its
    netloc."""
    from astrolift_services.models import ManagedServiceBinding

    url = ""
    binding_row = ManagedServiceBinding.objects.filter(
        managed_service=faas_row, env_key="FUNCTION_URL", deleted_at__isnull=True
    ).first()
    if binding_row is not None and binding_row.env_value_ref:
        url = binding_row.env_value_ref
    else:
        try:
            from _sdk.managed_service import ServiceHandle

            binding = _faas_driver(cluster, faas_row).binding(ServiceHandle(handle=faas_row.backend_ref))
            ref = binding.env_vars.get("FUNCTION_URL")
            url = getattr(ref, "literal", "") or ""
        except Exception as exc:  # noqa: BLE001
            log.info("ensure_faas_services: could not resolve FUNCTION_URL (%s)", exc)
            return ""
    if not url:
        return ""
    return urlparse(url).netloc or url


# ---- ensure_faas_services ------------------------------------------------


def _ensure_faas_services_sync(deployment_id: int) -> dict[str, Any]:
    from astrolift_lifecycle.models import Deployment

    deployment = Deployment.objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster__provider_plugin",
        "app_environment__managed_domain",
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
    managed_domain = getattr(env, "managed_domain", None)
    org_slug = app.organization.slug if app.organization_id else "none"

    # Image-mode code reference: the digest build_image recorded on the
    # Deployment row + the app's ECR repo URI. build_image runs before this
    # activity in the deploy flow, so a real digest is present for image apps.
    repo_uri = (app.registry_repo_uri or "").strip()
    digest = (deployment.image_digest or "").strip()
    image_tag = (deployment.image_tag or "").strip()

    # Per-workload public hostnames (cdn aliases). Only meaningful when the env
    # has a managed domain + the workload is public (compute_hostnames keys on
    # is_public).
    aliases_by_workload: dict[str, list[str]] = {}
    if managed_domain is not None:
        from astrolift_manifest.hostname import HostnameInputs, compute_hostnames

        for wh in compute_hostnames(
            manifest,
            HostnameInputs(app_slug=app.slug, org_slug=org_slug, base_zone=managed_domain.zone),
        ):
            aliases_by_workload.setdefault(wh.workload_slug, []).append(wh.hostname)

    acm_cert_arn = ""
    if managed_domain is not None and managed_domain.dns_config:
        acm_cert_arn = str(managed_domain.dns_config.get("cloudfront_certificate_arn", ""))

    ensured: list[dict[str, str]] = []
    for w in faas:
        fn_name, cdn_name = _service_names(w.name, env)

        # 1. Lambda first -- the cdn's origin. Provision fully to ACTIVE so we
        #    can read its Function URL for the distribution config.
        fn_config = _lambda_config(w, repo_uri=repo_uri, digest=digest, image_tag=image_tag)
        fn_row = _ensure_service_row(
            app=app, env=env, kind="faas", name=fn_name, variant="lambda", config=fn_config
        )
        # Realign config on an existing row (image digest / memory / public may
        # have changed) before provisioning.
        if fn_row.config != fn_config:
            fn_row.config = fn_config
            fn_row.save(update_fields=["config", "updated_at", "version"])
        _provision_row(fn_row)
        fn_row.refresh_from_db()

        entry: dict[str, str] = {"workload": w.name, "function": _resource_id(fn_row)}

        # 2. cdn (public only) -- origin is the Function URL just provisioned.
        if w.faas_public:
            origin = _function_url_host(fn_row, cluster)
            aliases = aliases_by_workload.get(w.name, []) if w.is_public else []
            if aliases and not acm_cert_arn:
                # Custom-domain serving needs a us-east-1 ACM cert; without it
                # CloudFront serves on its default *.cloudfront.net domain and
                # the CNAME -> distribution will 403/SSL-mismatch on the custom
                # host. ensure_cloudfront_cert mints it; surface the gap so a
                # broken-looking custom URL is diagnosable, not silent.
                log.warning(
                    "faas %s: public aliases %s but no us-east-1 ACM cert "
                    "(managed_domain.dns_config.cloudfront_certificate_arn unset); "
                    "the distribution will serve on its default cloudfront.net "
                    "domain until a cert is provisioned",
                    w.name,
                    aliases,
                )
            cdn_config = {
                "size": "small",
                "custom_origin_domain": origin,
                "origin_region": region,
                "aliases": aliases,
                "acm_cert_arn": acm_cert_arn,
            }
            cdn_row = _ensure_service_row(
                app=app, env=env, kind="cdn", name=cdn_name, variant="cloudfront", config=cdn_config
            )
            if cdn_row.config != cdn_config:
                cdn_row.config = cdn_config
                cdn_row.save(update_fields=["config", "updated_at", "version"])
            _provision_row(cdn_row)
            cdn_row.refresh_from_db()
            distribution_id = _resource_id(cdn_row)
            entry["distribution_id"] = distribution_id
            entry["origin"] = origin

            # 3. POST-cdn: scope the Lambda's Function-URL invoke permission to
            #    ONLY this distribution (#1035). The Function URL is AWS_IAM and
            #    CloudFront (Lambda OAC, sigv4) is the sole allowed caller; the
            #    distribution ARN doesn't exist until the cdn provisions, so this
            #    can't be done in the lambda provision. A missing/unscoped grant
            #    is the exact 403 #1035 fixes, so fail loudly if the account id
            #    (needed for the ARN) can't be resolved.
            if distribution_id:
                account_id = _resolve_account_id(cluster, account)
                if not account_id:
                    raise RuntimeError(
                        f"faas {w.name}: cannot resolve AWS account id for the "
                        "CloudFront invoke grant (provider_config.account_id "
                        "empty and STS discovery failed)",
                    )
                distribution_arn = f"arn:aws:cloudfront::{account_id}:distribution/{distribution_id}"
                _faas_driver(cluster, fn_row).allow_cloudfront_invoke(
                    _resource_id(fn_row), distribution_arn
                )

        ensured.append(entry)

    return {"stub": False, "ensured": ensured}


@activity.defn(name="astrolift.deploy.ensure_faas_services")
async def ensure_faas_services(deployment_id: int) -> dict:
    """Ensure the (faas/lambda [, cdn]) managed services for every faas
    workload in the deployment exist + are ACTIVE (lambda first, then -- when
    public -- the cdn fronting its Function URL).

    No-op fast-return when the manifest has no faas workload, so non-faas and
    pre-existing apps pay nothing."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_ensure_faas_services_sync)(deployment_id)
    activity.heartbeat()
    return result
