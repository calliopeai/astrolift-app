"""Static-site topology activities (#1010).

A ``static_site`` workload serves built assets from an S3 origin bucket
behind a CloudFront distribution -- no container, no pod. The operator
declares only ``kind = "static_site"``; the platform auto-provisions the
two implied managed services and drives the asset pipeline + DNS:

* ``ensure_static_site_services`` -- idempotently ensure the (object_store
  bucket, cdn distribution) ManagedService rows exist and are ACTIVE, in
  that order (the CDN's origin is the bucket). Runs BEFORE
  ``ensure_workload_identity`` so the bucket/cdn ``iam_grants`` fold into
  the app runtime role.
* ``sync_static_assets`` -- the asset pipeline, both modes. PLATFORM_BUILD
  (``static_build_command`` set) mints a per-app static-build IRSA role and
  dispatches an in-cluster build/sync Job; CI_PUSHED (command empty) is a
  no-op for the in-cluster path (CI uploaded the bundle via the REST
  endpoint) plus a best-effort cache invalidation.
* ``ensure_static_dns`` -- write the ``CNAME host -> CloudFront domain``
  record for each public static workload (a static site has no Ingress, so
  external-dns never sees it; the platform writes the record explicitly).
* ``delete_static_dns_records`` -- teardown counterpart: remove those
  CNAMEs (idempotent, #998).

The build_mode is DERIVED, not stored: a static workload with a non-empty
``static_build_command`` is platform-build; empty is CI-pushed.

All bodies are sync-wrapped via ``sync_to_async`` so Django ORM + boto3
access stays off the activity event loop.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.static_site")

# The platform namespace the static-build Job runs in (same as kaniko).
_BUILD_NAMESPACE = "astrolift-system"


@dataclasses.dataclass(slots=True, frozen=True)
class SyncStaticAssetsInput:
    """Payload for ``sync_static_assets``.

    ``deployment_id`` resolves the app + cluster + manifest; ``commit_sha``
    is the source ref the platform-build Job clones + checks out (empty ->
    the app's default branch)."""

    deployment_id: int
    commit_sha: str


# ---- shared helpers ------------------------------------------------------


def _normalized_manifest(app) -> Any:
    """Parse + normalize the app's stored manifest (same path the deploy
    render uses, so the static workloads + their ``static_*`` fields match
    exactly what got deployed)."""
    from astrolift_manifest.normalize import NormalizationDefaults, normalize
    from astrolift_manifest.parser import parse_raw

    if not (app.manifest_raw or "").strip():
        return None
    return normalize(parse_raw(app.manifest_raw), defaults=NormalizationDefaults())


def _static_workloads(manifest) -> list[Any]:
    if manifest is None:
        return []
    return [w for w in manifest.workloads if getattr(w, "kind", "") == "static_site"]


def _region_account(cluster) -> tuple[str, str]:
    pc = cluster.provider_config or {}
    ac = cluster.auth_config or {}
    region = str(pc.get("region", ac.get("region", cluster.region or "")))
    account_id = str(pc.get("account_id", ""))
    return region, account_id


def _resolve_account_id(cluster, account_id: str) -> str:
    """Return ``account_id`` if set, else discover it via STS.

    ``provider_config.account_id`` is the install-bundle value but can be
    absent (autodiscover convention -- the platform infers what wasn't
    pinned, like ``_ensure_cluster_oidc_issuer`` self-heals the OIDC issuer).
    A missing account would otherwise produce a malformed CloudFront ARN
    (``arn:aws:cloudfront:::distribution/...``), the build pod's invalidation
    would AccessDenied, and ``set -euo pipefail`` would fail the whole Job."""
    if account_id:
        return account_id
    region, _ = _region_account(cluster)
    try:
        import boto3

        return str(boto3.client("sts", region_name=region or None).get_caller_identity()["Account"])
    except Exception as exc:  # noqa: BLE001
        log.warning("static build: STS account-id discovery failed (%s)", exc)
        return ""


def _service_names(workload_name: str, env) -> tuple[str, str]:
    """The (object_store, cdn) ManagedService row names for a static workload
    in one environment.

    Env-scoped: the manifest lives on RegisteredApp, so every AppEnvironment
    yields the identical ``workload_name``. Without the env component a second
    env's deploy would find the first env's row (the unique-active constraint
    is (app, kind, name)), reprovision against it, and the build Job's
    ``s3 sync --delete`` would clobber the other env's live assets. Distinct
    names give each env its own bucket + distribution."""
    env_label = getattr(env, "name", "") or "default"
    return f"{workload_name}-{env_label}-assets", f"{workload_name}-{env_label}-cdn"


def _ensure_service_row(*, app, env, kind: str, name: str, variant: str, config: dict[str, Any]):
    """Idempotent, soft-delete-aware ensure of a ManagedService row.

    The unique-active constraint is (registered_app, kind, name); the ``name``
    is env-scoped (see :func:`_service_names`), so a found row always belongs
    to THIS env. A prior teardown may have left it soft-deleted, so we
    un-delete + realign rather than hit the constraint creating a duplicate.
    ``config`` is set only when creating / un-deleting so a re-run doesn't
    clobber a config the provision finalize may have enriched. The row's
    ``app_environment`` is never reassigned -- the env-scoped name guarantees
    one row per (app, env), and bouncing it across envs would repoint a live
    bucket/distribution at the wrong environment."""
    from astrolift_services.models import ManagedService

    row = (
        ManagedService.all_objects.filter(registered_app=app, kind=kind, name=name)
        .order_by("-created_at")
        .first()
    )
    if row is not None:
        fields: list[str] = []
        if row.deleted_at is not None:
            row.deleted_at = None
            row.status = ManagedService.Status.PENDING
            row.config = dict(config)
            fields += ["deleted_at", "status", "config"]
        if row.variant != variant:
            row.variant = variant
            fields.append("variant")
        if fields:
            fields += ["updated_at", "version"]
            row.save(update_fields=fields)
        return row
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=kind,
        name=name,
        variant=variant,
        config=dict(config),
        status=ManagedService.Status.PENDING,
    )


def _provision_row(row) -> None:
    """Drive one ManagedService row to ACTIVE via the shared lifecycle path
    (resolve driver -> provision -> persist handle -> flip ACTIVE -> sync
    bindings). Idempotent: drivers probe for an existing resource. Raises on
    a provision failure so the activity's RetryPolicy applies."""
    from astrolift_workflows.activities.managed_service_lifecycle import (
        _finalize_provision_sync,
        _provision_sync,
    )

    result = _provision_sync(row.pk)
    if not result.get("ok"):
        raise RuntimeError(
            result.get("message") or f"provision of {row.kind}/{row.name} returned ok=False",
        )
    _finalize_provision_sync(row.pk, result.get("handle", ""))


def _cdn_driver(cluster, cdn_row):
    """Resolve the CDN driver from the ROW's own variant, not a hardcoded
    ``cloudfront`` -- mirrors the managed-service lifecycle so a future GCP
    (``cloud_cdn``) / Azure (``front_door``) cdn row resolves its own driver
    instead of silently picking CloudFront in this shared (cloud-agnostic)
    activity layer."""
    from astrolift_drivers.registry import plugins
    from core.cluster_observability import managed_config_for

    plugin_slug = cluster.provider_plugin.slug
    variant = getattr(cdn_row, "variant", "") or "cloudfront"
    driver_cls = plugins.get(plugin_slug, f"managed:cdn:{variant}")
    cfg = managed_config_for(plugin_slug, cluster, kind="cdn", variant=variant)
    return driver_cls(config=cfg)


def _bucket_name(row) -> str:
    from aws.managed._base import parse_handle

    if not row or not row.backend_ref:
        return ""
    return parse_handle(row.backend_ref)[1]


def _distribution_id(row) -> str:
    from aws.managed._base import parse_handle

    if not row or not row.backend_ref:
        return ""
    return parse_handle(row.backend_ref)[1]


# ---- ensure_static_site_services -----------------------------------------


def _ensure_static_site_services_sync(deployment_id: int) -> dict[str, Any]:
    from astrolift_lifecycle.models import Deployment

    deployment = Deployment.objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster__provider_plugin",
        "app_environment__managed_domain",
    ).get(pk=deployment_id)
    app = deployment.registered_app
    env = deployment.app_environment
    manifest = _normalized_manifest(app)
    statics = _static_workloads(manifest)
    if not statics:
        return {"stub": True, "ensured": []}

    cluster = env.tenant_cluster if env else None
    if cluster is None:
        return {"stub": True, "reason": "no tenant_cluster bound", "ensured": []}

    region, _account = _region_account(cluster)
    managed_domain = getattr(env, "managed_domain", None)
    org_slug = app.organization.slug if app.organization_id else "none"

    # Per-workload public hostnames (CDN aliases). Only meaningful when the
    # env has a managed domain + the workload is public.
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
    for w in statics:
        assets_name, cdn_name = _service_names(w.name, env)
        # 1. Bucket first -- the CDN origin. Provision fully to ACTIVE so we
        #    can read its name for the distribution config.
        bucket_row = _ensure_service_row(
            app=app,
            env=env,
            kind="object_store",
            name=assets_name,
            variant="s3",
            config={"size": "small"},
        )
        _provision_row(bucket_row)
        bucket_row.refresh_from_db()
        bucket = _bucket_name(bucket_row)

        # 2. CDN -- origin is the bucket just provisioned. Save the config
        #    BEFORE provision so it flows into the driver's ProvisionSpec.
        aliases = aliases_by_workload.get(w.name, []) if w.is_public else []
        if aliases and not acm_cert_arn:
            # Custom-domain serving needs an ACM cert in us-east-1; without it
            # CloudFront is created on its default *.cloudfront.net domain and
            # the CNAME -> distribution will 403/SSL-mismatch on the custom
            # host. Cert minting is a bounded follow-up -- surface the gap so a
            # broken-looking custom URL is diagnosable, not silent.
            log.warning(
                "static_site %s: public aliases %s but no us-east-1 ACM cert "
                "(managed_domain.dns_config.cloudfront_certificate_arn unset); "
                "the distribution will serve on its default cloudfront.net "
                "domain and the custom host will not resolve over TLS until a "
                "cert is provisioned",
                w.name,
                aliases,
            )
        cdn_config = {
            "size": "small",
            "origin_bucket": bucket,
            "origin_region": region,
            "aliases": aliases,
            "acm_cert_arn": acm_cert_arn,
            "spa": bool(w.static_spa),
            "index": w.static_index or "index.html",
        }
        cdn_row = _ensure_service_row(
            app=app,
            env=env,
            kind="cdn",
            name=cdn_name,
            variant="cloudfront",
            config=cdn_config,
        )
        # Realign config on an existing row (origin bucket / aliases may have
        # changed) before provisioning.
        if cdn_row.config != cdn_config:
            cdn_row.config = cdn_config
            cdn_row.save(update_fields=["config", "updated_at", "version"])
        _provision_row(cdn_row)
        cdn_row.refresh_from_db()

        ensured.append(
            {
                "workload": w.name,
                "bucket": bucket,
                "distribution_id": _distribution_id(cdn_row),
            }
        )

    return {"stub": False, "ensured": ensured}


@activity.defn(name="astrolift.deploy.ensure_static_site_services")
async def ensure_static_site_services(deployment_id: int) -> dict:
    """Ensure the (object_store, cdn) managed services for every static_site
    workload in the deployment exist + are ACTIVE (bucket then cdn).

    No-op fast-return when the manifest has no static workload, so non-static
    and pre-existing apps pay nothing."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_ensure_static_site_services_sync)(deployment_id)
    activity.heartbeat()
    return result


# ---- sync_static_assets --------------------------------------------------


# S3 origin + CloudFront invalidation IAM scope for the in-cluster static
# build/sync pod. Descriptions/Sids stay ASCII (#1026); resources are scoped
# to the one bucket + the one distribution.
def _static_build_permissions(*, bucket_arn: str, distribution_arn: str) -> list[dict[str, Any]]:
    return [
        {"Effect": "Allow", "Action": ["s3:ListBucket", "s3:GetBucketLocation"], "Resource": bucket_arn},
        {
            "Effect": "Allow",
            "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
            "Resource": f"{bucket_arn}/*",
        },
        {"Effect": "Allow", "Action": ["cloudfront:CreateInvalidation"], "Resource": distribution_arn},
        {"Effect": "Allow", "Action": ["sts:GetCallerIdentity"], "Resource": "*"},
    ]


def _sync_static_assets_sync(inp: SyncStaticAssetsInput) -> dict[str, Any]:
    from astrolift_lifecycle.models import Deployment

    deployment = Deployment.objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster__provider_plugin",
    ).get(pk=inp.deployment_id)
    app = deployment.registered_app
    env = deployment.app_environment
    statics = _static_workloads(_normalized_manifest(app))
    if not statics:
        return {"stub": True}

    from core.app_deploy import AppDeployError, cluster_for_deployment

    try:
        cluster = cluster_for_deployment(deployment)
    except AppDeployError as exc:
        log.info("sync_static_assets: no cluster for deployment %s (%s) -- stub", inp.deployment_id, exc)
        return {"stub": True}

    plugin_slug = getattr(getattr(cluster, "provider_plugin", None), "slug", "")
    if plugin_slug != "aws":
        # Only the AWS static-build/sync identity path is wired today.
        return {"stub": True}

    region, account_id = _region_account(cluster)
    results: list[dict[str, Any]] = []
    for w in statics:
        outcome = _sync_one_workload(
            deployment=deployment,
            app=app,
            env=env,
            cluster=cluster,
            workload=w,
            region=region,
            account_id=account_id,
            commit_sha=inp.commit_sha,
        )
        results.append(outcome)
    return {"stub": False, "workloads": results}


def _resolve_static_rows(app, workload_name: str, env):
    from astrolift_services.models import ManagedService

    assets_name, cdn_name = _service_names(workload_name, env)
    assets = ManagedService.objects.filter(registered_app=app, kind="object_store", name=assets_name).first()
    cdn = ManagedService.objects.filter(registered_app=app, kind="cdn", name=cdn_name).first()
    return assets, cdn


def _sync_one_workload(
    *, deployment, app, env, cluster, workload, region: str, account_id: str, commit_sha: str
) -> dict[str, Any]:
    from astrolift_services.models import ManagedService

    assets, cdn = _resolve_static_rows(app, workload.name, env)
    active = ManagedService.Status.ACTIVE
    if assets is None or cdn is None or assets.status != active or cdn.status != active:
        # ensure_static_site_services runs first in the deploy flow; if the
        # rows aren't ACTIVE yet there is nothing to sync against.
        return {"workload": workload.name, "stub": True, "reason": "static services not active"}

    bucket = _bucket_name(assets)
    distribution_id = _distribution_id(cdn)

    # The pinned mode-select rule: build command set => platform-build;
    # empty => CI pushed (the bundle was synced via the REST endpoint).
    if not (workload.static_build_command or "").strip():
        invalidation = ""
        try:
            invalidation = (
                _cdn_driver(cluster, cdn).invalidate(distribution_id, ["/*"]).get("invalidation_id", "")
            )
        except Exception as exc:  # noqa: BLE001 -- invalidation is best-effort
            log.info("sync_static_assets: ci-pushed invalidate skipped for %s (%s)", workload.name, exc)
        return {
            "workload": workload.name,
            "mode": "ci_pushed",
            "synced": False,
            "invalidation_id": invalidation,
        }

    return _platform_build_workload(
        deployment=deployment,
        app=app,
        cluster=cluster,
        workload=workload,
        bucket=bucket,
        distribution_id=distribution_id,
        region=region,
        account_id=account_id,
        commit_sha=commit_sha,
    )


def _platform_build_workload(
    *, deployment, app, cluster, workload, bucket, distribution_id, region, account_id, commit_sha
) -> dict[str, Any]:
    from astrolift_workflows.activities.build_image import (
        _ensure_cluster_oidc_issuer,
        _resolve_source_url,
    )
    from core.app_deploy import driver_for_capability, static_build_role_name
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    account_id = _resolve_account_id(cluster, account_id)
    if not account_id:
        # Explicit terminal failure beats a malformed ARN -> a cryptic
        # in-Job AccessDenied on cloudfront:CreateInvalidation.
        raise RuntimeError(
            f"static asset build for {workload.name}: cannot resolve AWS account id "
            "(provider_config.account_id empty and STS discovery failed)",
        )
    bucket_arn = f"arn:aws:s3:::{bucket}"
    distribution_arn = f"arn:aws:cloudfront::{account_id}:distribution/{distribution_id}"

    # Mint (or self-heal) a build-scoped IRSA role and bind it to the
    # static-build SA in the platform namespace -- exactly as build_image
    # does for the kaniko push role, but scoped to S3 write + CloudFront
    # invalidation on this app's resources.
    _ensure_cluster_oidc_issuer(cluster)
    identity_driver = driver_for_capability(cluster, "identity")
    sa_name = static_build_role_name(app)
    identity_driver.create_identity_role(
        sa_name, _static_build_permissions(bucket_arn=bucket_arn, distribution_arn=distribution_arn)
    )
    annotation = identity_driver.bind_service_account(cluster.slug, _BUILD_NAMESPACE, sa_name, sa_name)
    role_arn = annotation.get("eks.amazonaws.com/role-arn", "")

    cluster_driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    source_uri = _resolve_source_url(app, commit_sha)

    from providers.k8s_native.build_static import StaticAssetBuildDriver

    driver = StaticAssetBuildDriver(
        cluster_driver=cluster_driver,
        cluster_slug=ctx.slug,
        service_account=sa_name,
        service_account_role_arn=role_arn,
        namespace=_BUILD_NAMESPACE,
        build_id=f"{deployment.pk}-{workload.name}-{commit_sha or 'head'}",
    )
    result = driver.build(
        source_uri=source_uri,
        build_command=workload.static_build_command,
        output_dir=workload.static_output_dir,
        bucket=bucket,
        distribution_id=distribution_id,
        region=region,
    )
    if not getattr(result, "success", False):
        errors = "; ".join(getattr(result, "errors", []) or []) or "unknown static build failure"
        raise RuntimeError(f"static asset build failed for {workload.name}: {errors}")
    return {"workload": workload.name, "mode": "platform_build", "synced": True}


@activity.defn(name="astrolift.deploy.sync_static_assets")
async def sync_static_assets(inp: SyncStaticAssetsInput) -> dict:
    """Sync each static_site workload's built assets to its origin bucket.

    PLATFORM_BUILD (build command set) runs an in-cluster build/sync Job;
    CI_PUSHED (command empty) is a no-op for the in-cluster path (CI already
    synced via the REST endpoint) plus a best-effort cache invalidation.
    No-op fast-return when the manifest has no static workload."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_sync_static_assets_sync)(inp)
    activity.heartbeat()
    return result


# ---- ensure_static_dns ---------------------------------------------------


def _cdn_domain_for(cdn_row, cluster) -> str:
    """Read the CloudFront domain (``dXXX.cloudfront.net``) for a cdn row.

    Prefer the materialized ``CDN_DOMAIN_NAME`` binding (written on provision
    finalize); fall back to a live ``binding()`` call if the row is missing."""
    from astrolift_services.models import ManagedServiceBinding

    row = ManagedServiceBinding.objects.filter(
        managed_service=cdn_row, env_key="CDN_DOMAIN_NAME", deleted_at__isnull=True
    ).first()
    if row is not None and row.env_value_ref:
        return row.env_value_ref
    try:
        from _sdk.managed_service import ServiceHandle

        binding = _cdn_driver(cluster, cdn_row).binding(ServiceHandle(handle=cdn_row.backend_ref))
        ref = binding.env_vars.get("CDN_DOMAIN_NAME")
        return getattr(ref, "literal", "") or ""
    except Exception as exc:  # noqa: BLE001
        log.info("ensure_static_dns: could not resolve CDN_DOMAIN_NAME (%s)", exc)
        return ""


def _ensure_static_dns_sync(deployment_id: int) -> dict[str, Any]:
    from astrolift_lifecycle.models import Deployment

    deployment = Deployment.objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster__provider_plugin",
        "app_environment__managed_domain",
    ).get(pk=deployment_id)
    app = deployment.registered_app
    env = deployment.app_environment
    manifest = _normalized_manifest(app)
    statics = [w for w in _static_workloads(manifest) if w.is_public]
    if not statics:
        return {"stub": True, "records": []}

    cluster = env.tenant_cluster if env else None
    managed_domain = getattr(env, "managed_domain", None)
    if cluster is None or managed_domain is None:
        log.info("ensure_static_dns: deployment %s has no cluster/managed_domain -- skipping", deployment_id)
        return {"stub": True, "records": []}

    from astrolift_manifest.hostname import HostnameInputs, compute_hostnames
    from astrolift_services.models import ManagedService
    from core.app_deploy import driver_for_capability

    org_slug = app.organization.slug if app.organization_id else "none"
    host_by_workload: dict[str, str] = {
        wh.workload_slug: wh.hostname
        for wh in compute_hostnames(
            manifest,
            HostnameInputs(app_slug=app.slug, org_slug=org_slug, base_zone=managed_domain.zone),
        )
    }
    dns_driver = driver_for_capability(cluster, "dns")

    records: list[dict[str, str]] = []
    for w in statics:
        host = host_by_workload.get(w.name)
        if not host:
            continue
        _assets_name, cdn_name = _service_names(w.name, env)
        cdn_row = ManagedService.objects.filter(registered_app=app, kind="cdn", name=cdn_name).first()
        if cdn_row is None or not cdn_row.backend_ref:
            log.info("ensure_static_dns: cdn service not ready for %s -- skipping", w.name)
            continue
        cdn_domain = _cdn_domain_for(cdn_row, cluster)
        if not cdn_domain:
            continue
        dns_driver.ensure_record(zone=managed_domain.zone, name=host, type="CNAME", value=cdn_domain, ttl=300)
        records.append({"host": host, "value": cdn_domain})
    return {"stub": False, "records": records}


@activity.defn(name="astrolift.deploy.ensure_static_dns")
async def ensure_static_dns(deployment_id: int) -> dict:
    """Write a ``CNAME host -> CloudFront domain`` for each public static_site
    workload. A static site has no Ingress, so external-dns never sees it --
    the platform writes the record explicitly (idempotent UPSERT)."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_ensure_static_dns_sync)(deployment_id)
    activity.heartbeat()
    return result


# ---- delete_static_dns_records (teardown) --------------------------------


def _delete_static_dns_records_sync(registered_app_id: int) -> dict[str, Any]:
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.select_related("organization").get(pk=registered_app_id)
    manifest = _normalized_manifest(app)
    statics = [w for w in _static_workloads(manifest) if w.is_public]
    if not statics:
        return {"deleted": []}

    from aws._errors import NotFoundError

    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_manifest.hostname import HostnameInputs, compute_hostnames
    from core.app_deploy import AppDeployError, driver_for_capability

    org_slug = app.organization.slug if app.organization_id else "none"
    deleted: list[str] = []
    envs = AppEnvironment.all_objects.select_related(
        "tenant_cluster__provider_plugin", "managed_domain"
    ).filter(registered_app=app)
    for env in envs:
        cluster = env.tenant_cluster
        managed_domain = getattr(env, "managed_domain", None)
        if cluster is None or managed_domain is None:
            continue
        try:
            dns_driver = driver_for_capability(cluster, "dns")
        except AppDeployError:
            continue
        host_by_workload = {
            wh.workload_slug: wh.hostname
            for wh in compute_hostnames(
                manifest,
                HostnameInputs(app_slug=app.slug, org_slug=org_slug, base_zone=managed_domain.zone),
            )
        }
        for w in statics:
            host = host_by_workload.get(w.name)
            if not host:
                continue
            try:
                dns_driver.delete_record(managed_domain.zone, host, "CNAME")
                deleted.append(host)
            except NotFoundError:
                # Idempotent (#998): the record is already gone.
                pass
    return {"deleted": deleted}


@activity.defn(name="astrolift.deploy.delete_static_dns_records")
async def delete_static_dns_records(registered_app_id: int) -> dict:
    """Remove the platform-written static-site CNAMEs for an app (teardown).

    Best-effort + idempotent: a missing record is swallowed so re-runs and
    partial teardowns complete."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_delete_static_dns_records_sync)(registered_app_id)
