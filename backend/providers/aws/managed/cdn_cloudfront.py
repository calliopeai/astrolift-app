"""CloudFront CDN managed-service driver (#1010 static-site topology).

Spec ref: spec 23-provider-plugin-aws + _sdk/managed_service.py.

A static-site workload implies an object_store bucket (the origin) and
this CDN distribution in front of it. The bucket stays private; the
distribution reaches it through an Origin Access Control (OAC) and a
bucket policy scoped to the distribution's ARN.

A public faas workload (#1035) instead points the distribution at a Lambda
Function URL (a custom HTTPS origin) through a *Lambda* OAC: CloudFront
sigv4-signs its requests to the AWS_IAM Function URL, and the function
trusts only this distribution. Same OAC primitive as S3, ``OriginType``
``lambda`` instead of ``s3``; the invoke grant is written Lambda-side
(``LambdaDriver.allow_cloudfront_invoke``), not as a bucket policy here.

CloudFront is a global service but boto3 routes its control-plane calls
through us-east-1, and CloudFront aliases require their ACM certificate
in us-east-1 (regional ALB certs do NOT work) -- both pinned below.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
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

log = logging.getLogger("aws.managed.cdn_cloudfront")

KIND = "cdn"

# Disabling a distribution flips Status to InProgress until the change
# propagates to every edge; a distribution can only be deleted once it
# reports Deployed. Bounded poll so a stuck propagation surfaces.
_DISABLE_MAX_POLLS = 60
_DISABLE_POLL_SECONDS = 10


@dataclass(frozen=True)
class CloudFrontConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    region: str = "us-east-1"  # CloudFront + its ACM certs are us-east-1
    comment_prefix: str = "astrolift"  # distribution Comment/CallerReference prefix
    price_class: str = "PriceClass_100"
    default_ttl: int = 3600


class CloudFrontDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: CloudFrontConfig,
        client: Any | None = None,
        s3_client: Any | None = None,
    ) -> None:
        self._config = config
        self._injected_s3 = s3_client
        if client is not None:
            self._cf = client
        else:
            import boto3

            # CloudFront is global but boto3 wants us-east-1.
            self._cf = boto3.client("cloudfront", region_name="us-east-1")

    # ---- lifecycle ------------------------------------------------

    @driver_op(
        cloud="aws",
        driver="cdn_cloudfront",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        origin_bucket = str(cfg.get("origin_bucket", "")).strip()
        # A faas (Lambda Function URL) origin is a custom HTTPS origin -- no
        # S3 bucket, no OAC. Exactly one origin kind is provided (#987).
        custom_origin_domain = str(cfg.get("custom_origin_domain", "")).strip()
        if not origin_bucket and not custom_origin_domain:
            return ProvisionResult(
                ok=False,
                handle="",
                message=(
                    "cdn provision requires config.origin_bucket (S3 origin) "
                    "or config.custom_origin_domain (custom HTTPS origin)"
                ),
                errors=["origin_required"],
            )
        origin_region = str(cfg.get("origin_region") or self._config.region)
        aliases = [str(a) for a in (cfg.get("aliases") or []) if a]
        acm_cert_arn = str(cfg.get("acm_cert_arn", "")).strip()
        spa = bool(cfg.get("spa", False))
        index = str(cfg.get("index") or "index.html")
        comment = self._comment_for(spec)

        oac_id = ""
        if origin_bucket:
            try:
                oac_id = self._ensure_oac(origin_bucket)
            except Exception as exc:
                return ProvisionResult(
                    ok=False,
                    handle="",
                    message=f"origin-access-control: {exc}",
                    errors=[str(exc)],
                )
        elif custom_origin_domain:
            # A Lambda Function URL origin gets a Lambda OAC so CloudFront
            # sigv4-signs its requests; the URL is AWS_IAM, not public (#1035).
            try:
                oac_id = self._ensure_lambda_oac(custom_origin_domain)
            except Exception as exc:
                return ProvisionResult(
                    ok=False,
                    handle="",
                    message=f"origin-access-control (lambda): {exc}",
                    errors=[str(exc)],
                )

        dist_config = self._build_distribution_config(
            spec=spec,
            origin_bucket=origin_bucket,
            custom_origin_domain=custom_origin_domain,
            origin_region=origin_region,
            oac_id=oac_id,
            aliases=aliases,
            acm_cert_arn=acm_cert_arn,
            spa=spa,
            index=index,
            comment=comment,
        )

        try:
            response = self._cf.create_distribution_with_tags(
                DistributionConfigWithTags={
                    "DistributionConfig": dist_config,
                    "Tags": {
                        "Items": [{"Key": t["Key"], "Value": t["Value"]} for t in tags_for(spec)],
                    },
                },
            )
            distribution = response["Distribution"]
            distribution_id = distribution["Id"]
            distribution_arn = distribution.get("ARN", "")
        except (
            self._cf.exceptions.DistributionAlreadyExists,
            self._cf.exceptions.CNAMEAlreadyExists,
        ):
            # Idempotent: a prior attempt created the distribution.
            # Probe by Comment and reconcile aliases/cert/SPA in place.
            try:
                distribution_id, distribution_arn = self._reconcile_existing(
                    comment=comment,
                    aliases=aliases,
                    acm_cert_arn=acm_cert_arn,
                    spa=spa,
                    index=index,
                )
            except Exception as exc:
                return ProvisionResult(
                    ok=False,
                    handle="",
                    message=f"reconcile existing distribution: {exc}",
                    errors=[str(exc)],
                )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_distribution: {exc}",
                errors=[str(exc)],
            )

        # OAC read path: let this distribution (and only this one) read
        # the private origin bucket. Separate s3 client; benign races
        # (concurrent provision) are swallowed. Custom (non-S3) origins
        # have no bucket policy to write.
        if origin_bucket:
            self._grant_bucket_read(
                origin_bucket=origin_bucket,
                origin_region=origin_region,
                distribution_arn=distribution_arn,
            )

        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=distribution_id),
            message=f"distribution {distribution_id} provisioned",
        )

    @driver_op(cloud="aws", driver="cdn_cloudfront")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        # CloudFront config changes flow through provision's reconcile
        # path; there is no size/scale knob to apply here.
        _, distribution_id = parse_handle(spec.handle)
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"distribution {distribution_id} has no updatable "
                "attributes via update (config changes reconcile on "
                "the next provision)"
            ),
        )

    @driver_op(
        cloud="aws",
        driver="cdn_cloudfront",
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
        # A CDN holds no persistent state, so delete_data is moot -- both
        # values take the same path. A distribution must be Enabled=False
        # and Deployed before delete; force_destroy only affects whether
        # we tolerate a non-converging disable wait.
        del delete_data
        _, distribution_id = parse_handle(spec.handle)

        try:
            current = self._cf.get_distribution(Id=distribution_id)
        except self._cf.exceptions.NoSuchDistribution:
            # Idempotent (#998): the distribution is gone. A prior attempt may
            # have died after delete_distribution but before the OAC cleanup,
            # leaving the deterministically-named OAC + bucket policy orphaned.
            # The live config that carried the origin refs is gone, so recover
            # them from spec.config and reap the leftovers on this retry.
            origin_bucket = str((spec.config or {}).get("origin_bucket", ""))
            custom_origin_domain = str((spec.config or {}).get("custom_origin_domain", ""))
            if origin_bucket:
                self._cleanup_origin(
                    origin_bucket=origin_bucket,
                    oac_id=self._oac_id_by_name(origin_bucket),
                )
            elif custom_origin_domain:
                # A faas (Lambda Function URL) origin has no bucket policy, but
                # its deterministically-named Lambda OAC may be orphaned (#1035).
                self._cleanup_origin(
                    origin_bucket="",
                    oac_id=self._lambda_oac_id_by_name(custom_origin_domain),
                )
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"distribution {distribution_id} already gone",
            )
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"get_distribution: {exc}",
                errors=[str(exc)],
            )

        etag = current.get("ETag", "")
        dist = current["Distribution"]
        dist_config = dist["DistributionConfig"]
        origin_bucket, oac_id = self._origin_refs(dist_config)

        if dist_config.get("Enabled", False):
            dist_config["Enabled"] = False
            try:
                upd = self._cf.update_distribution(
                    Id=distribution_id,
                    DistributionConfig=dist_config,
                    IfMatch=etag,
                )
                etag = upd.get("ETag", etag)
            except self._cf.exceptions.NoSuchDistribution:
                return DeprovisionResult(
                    ok=True,
                    handle=spec.handle,
                    message=f"distribution {distribution_id} already gone",
                )
            except Exception as exc:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"disable distribution: {exc}",
                    errors=[str(exc)],
                )

        etag, deployed = self._wait_until_deployed(distribution_id, etag)
        if not deployed and not force_destroy:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(f"distribution {distribution_id} not yet Deployed after disable; retry or pass force_destroy"),
                errors=["not_deployed"],
            )

        try:
            self._cf.delete_distribution(Id=distribution_id, IfMatch=etag)
        except self._cf.exceptions.NoSuchDistribution:
            pass
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_distribution: {exc}",
                errors=[str(exc)],
            )

        self._cleanup_origin(origin_bucket=origin_bucket, oac_id=oac_id)
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=f"distribution {distribution_id} deleted",
        )

    # ---- cache invalidation ---------------------------------------

    @driver_op(cloud="aws", driver="cdn_cloudfront", audit=True)
    def invalidate(
        self,
        distribution_id: str,
        paths: list[str] | None = None,
    ) -> dict[str, str]:
        wildcard = paths or ["/*"]
        caller_ref = f"{self._config.comment_prefix}-inv-{datetime.now(tz=UTC).strftime('%Y%m%d%H%M%S%f')}"
        response = self._cf.create_invalidation(
            DistributionId=distribution_id,
            InvalidationBatch={
                "Paths": {"Quantity": len(wildcard), "Items": wildcard},
                "CallerReference": caller_ref,
            },
        )
        return {"invalidation_id": response["Invalidation"]["Id"]}

    # ---- read-only ops --------------------------------------------

    @driver_op(cloud="aws", driver="cdn_cloudfront")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, distribution_id = parse_handle(handle.handle)
        try:
            response = self._cf.get_distribution(Id=distribution_id)
        except self._cf.exceptions.NoSuchDistribution:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"distribution {distribution_id} does not exist",
            )
        except Exception as exc:
            return ServiceStatus(
                handle=handle.handle,
                state="error",
                message=str(exc),
            )
        state = response["Distribution"].get("Status", "")
        if state == "Deployed":
            return ServiceStatus(
                handle=handle.handle,
                state="available",
                message=f"distribution {distribution_id} deployed",
            )
        return ServiceStatus(
            handle=handle.handle,
            state="provisioning",
            message=f"distribution {distribution_id} status {state}",
        )

    @driver_op(cloud="aws", driver="cdn_cloudfront")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        _, distribution_id = parse_handle(handle.handle)
        response = self._cf.get_distribution(Id=distribution_id)
        dist = response["Distribution"]
        domain_name = dist.get("DomainName", "")
        # The distribution ARN is exactly the grant resource CloudFront
        # scopes cloudfront:CreateInvalidation to.
        distribution_arn = dist.get("ARN", "")
        return Binding(
            env_vars={
                "CDN_DISTRIBUTION_ID": ValueRef(literal=distribution_id),
                "CDN_DOMAIN_NAME": ValueRef(literal=domain_name),
                # Filled by runtime IRSA; literal placeholder so the
                # canonical cdn envelope always materializes.
                "CDN_INVALIDATION_ROLE": ValueRef(literal=""),
            },
            iam_grants=[
                Grant(
                    resource=distribution_arn,
                    actions=["cloudfront:CreateInvalidation"],
                ),
            ],
            notes="CDN invalidation scoped to this distribution.",
        )

    @driver_op(cloud="aws", driver="cdn_cloudfront")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError(
            "CloudFront distributions have no snapshot semantic -- the "
            "served assets live in the origin bucket (snapshot there)",
        )

    @driver_op(cloud="aws", driver="cdn_cloudfront")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        return ProvisionResult(
            ok=False,
            handle="",
            message="CloudFront has no snapshot, hence no restore",
            errors=["not_implemented"],
        )

    @driver_op(cloud="aws", driver="cdn_cloudfront", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "spa": {
                    "type": "boolean",
                    "description": ("Single-page-app mode: serve the index on 403/404 so client-side routing works."),
                },
                "index": {
                    "type": "string",
                    "description": "Default root object (e.g. index.html).",
                },
                "aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Custom hostnames (CNAMEs) for the distribution.",
                },
                "acm_cert_arn": {
                    "type": "string",
                    "description": (
                        "ACM certificate ARN in us-east-1 for the aliases. "
                        "Without it the distribution serves on its default "
                        "cloudfront.net domain."
                    ),
                },
            },
        }

    @driver_op(cloud="aws", driver="cdn_cloudfront", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "CDN_DISTRIBUTION_ID": "CloudFront distribution ID",
                "CDN_DOMAIN_NAME": "Distribution domain (dXXXX.cloudfront.net)",
                "CDN_INVALIDATION_ROLE": "IRSA role ARN for cache invalidation",
            }
        )

    # ---- internals ------------------------------------------------

    def _comment_for(self, spec: ProvisionSpec) -> str:
        # ASCII only (#1026); also the idempotency probe key.
        ident = spec.managed_service_id or spec.app_slug
        return f"{self._config.comment_prefix} {ident}"

    def _ensure_oac(self, origin_bucket: str) -> str:
        return self._ensure_oac_named(
            f"{self._config.comment_prefix}-{origin_bucket}-oac"[:64],
            origin_type="s3",
            description="astrolift static-site origin access control",
        )

    def _ensure_lambda_oac(self, custom_origin_domain: str) -> str:
        return self._ensure_oac_named(
            self._lambda_oac_name(custom_origin_domain),
            origin_type="lambda",
            description="astrolift faas (Lambda Function URL) origin access control",
        )

    def _lambda_oac_name(self, custom_origin_domain: str) -> str:
        # Deterministic so the id is recoverable by name on the idempotent
        # teardown path (mirrors the S3 OAC naming).
        return f"{self._config.comment_prefix}-{custom_origin_domain}-oac"[:64]

    def _ensure_oac_named(self, name: str, *, origin_type: str, description: str) -> str:
        # Shared by the S3 (#1010) and Lambda (#1035) OAC paths — identical
        # sigv4/always signing, differing only by OriginAccessControlOriginType.
        try:
            response = self._cf.create_origin_access_control(
                OriginAccessControlConfig={
                    "Name": name,
                    "Description": description,
                    "SigningProtocol": "sigv4",
                    "SigningBehavior": "always",
                    "OriginAccessControlOriginType": origin_type,
                },
            )
            return response["OriginAccessControl"]["Id"]
        except self._cf.exceptions.OriginAccessControlAlreadyExists:
            return self._find_oac_id(name)

    def _find_oac_id(self, name: str) -> str:
        # CloudFront pages these lists (default MaxItems 100) -- walk every
        # page via Marker/NextMarker so the reconcile probe never misses an
        # existing OAC on a later page (which would wedge the provision).
        marker = ""
        while True:
            kwargs = {"Marker": marker} if marker else {}
            oac_list = self._cf.list_origin_access_controls(**kwargs).get("OriginAccessControlList", {})
            for item in oac_list.get("Items", []) or []:
                if item.get("Name") == name:
                    return item["Id"]
            marker = oac_list.get("NextMarker", "")
            if not marker:
                break
        raise ManagedServiceError(
            f"origin access control {name!r} reported existing but not found",
        )

    def _s3_origin(
        self,
        origin_bucket: str,
        origin_region: str,
        oac_id: str,
        index: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Static-site origin: a private S3 bucket read via OAC. Returns
        (origin item, default cache behavior, default-root-object)."""
        origin_id = "s3-origin"
        origin_item = {
            "Id": origin_id,
            "DomainName": f"{origin_bucket}.s3.{origin_region}.amazonaws.com",
            "OriginAccessControlId": oac_id,
            # OAC supersedes the legacy OAI; it must be empty.
            "S3OriginConfig": {"OriginAccessIdentity": ""},
        }
        behavior = {
            "TargetOriginId": origin_id,
            "ViewerProtocolPolicy": "redirect-to-https",
            "Compress": True,
            "AllowedMethods": {
                "Quantity": 2,
                "Items": ["GET", "HEAD"],
                "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
            },
            "ForwardedValues": {
                "QueryString": False,
                "Cookies": {"Forward": "none"},
            },
            "MinTTL": 0,
            "DefaultTTL": self._config.default_ttl,
            "MaxTTL": 86400,
        }
        return origin_item, behavior, index

    def _custom_origin(
        self,
        custom_origin_domain: str,
        oac_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Custom HTTPS origin: a Lambda Function URL host (https-only) fronted
        through a Lambda OAC so CloudFront sigv4-signs requests to the AWS_IAM
        Function URL (#1035) for a public faas workload. Returns (origin item,
        default cache behavior, default-root-object)."""
        origin_id = "custom-origin"
        origin_item = {
            "Id": origin_id,
            "DomainName": custom_origin_domain,
            # Lambda OAC: CloudFront signs (sigv4) requests to the AWS_IAM
            # Function URL; the function trusts only this distribution (#1035).
            "OriginAccessControlId": oac_id,
            "CustomOriginConfig": {
                "HTTPPort": 80,
                "HTTPSPort": 443,
                "OriginProtocolPolicy": "https-only",
                "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
            },
        }
        behavior = {
            "TargetOriginId": origin_id,
            "ViewerProtocolPolicy": "redirect-to-https",
            "Compress": True,
            "AllowedMethods": {
                "Quantity": 7,
                "Items": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
                "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
            },
            # Forward query strings to the function. Do NOT forward the viewer
            # Host header -- a Lambda Function URL validates Host, so it must
            # see the origin's own host. Caching off by default (dynamic).
            "ForwardedValues": {
                "QueryString": True,
                "Cookies": {"Forward": "none"},
            },
            "MinTTL": 0,
            "DefaultTTL": 0,
            "MaxTTL": 0,
        }
        # A function URL serves every path; there is no default root object.
        return origin_item, behavior, ""

    def _build_distribution_config(
        self,
        *,
        spec: ProvisionSpec,
        origin_bucket: str,
        custom_origin_domain: str,
        origin_region: str,
        oac_id: str,
        aliases: list[str],
        acm_cert_arn: str,
        spa: bool,
        index: str,
        comment: str,
    ) -> dict[str, Any]:
        if custom_origin_domain:
            origin_item, default_behavior, default_root = self._custom_origin(custom_origin_domain, oac_id)
            origin_ref = custom_origin_domain
        else:
            origin_item, default_behavior, default_root = self._s3_origin(origin_bucket, origin_region, oac_id, index)
            origin_ref = origin_bucket
        caller_ref = f"{self._config.comment_prefix}-{spec.managed_service_id or spec.app_slug}-{origin_ref}"
        config: dict[str, Any] = {
            "CallerReference": caller_ref,
            "Comment": comment,
            "Enabled": True,
            "DefaultRootObject": default_root,
            "PriceClass": self._config.price_class,
            "Origins": {"Quantity": 1, "Items": [origin_item]},
            "DefaultCacheBehavior": default_behavior,
        }

        if acm_cert_arn and aliases:
            config["Aliases"] = {"Quantity": len(aliases), "Items": aliases}
            config["ViewerCertificate"] = {
                "ACMCertificateArn": acm_cert_arn,
                "SSLSupportMethod": "sni-only",
                "MinimumProtocolVersion": "TLSv1.2_2021",
            }
        else:
            config["Aliases"] = {"Quantity": 0}
            config["ViewerCertificate"] = {"CloudFrontDefaultCertificate": True}

        if spa:
            page = f"/{index}"
            config["CustomErrorResponses"] = {
                "Quantity": 2,
                "Items": [
                    {
                        "ErrorCode": 403,
                        "ResponsePagePath": page,
                        "ResponseCode": "200",
                        "ErrorCachingMinTTL": 10,
                    },
                    {
                        "ErrorCode": 404,
                        "ResponsePagePath": page,
                        "ResponseCode": "200",
                        "ErrorCachingMinTTL": 10,
                    },
                ],
            }
        return config

    def _reconcile_existing(
        self,
        *,
        comment: str,
        aliases: list[str],
        acm_cert_arn: str,
        spa: bool,
        index: str,
    ) -> tuple[str, str]:
        distribution_id = self._find_distribution_by_comment(comment)
        current = self._cf.get_distribution(Id=distribution_id)
        etag = current.get("ETag", "")
        dist = current["Distribution"]
        distribution_arn = dist.get("ARN", "")
        config = dist["DistributionConfig"]

        config["DefaultRootObject"] = index
        if acm_cert_arn and aliases:
            config["Aliases"] = {"Quantity": len(aliases), "Items": aliases}
            config["ViewerCertificate"] = {
                "ACMCertificateArn": acm_cert_arn,
                "SSLSupportMethod": "sni-only",
                "MinimumProtocolVersion": "TLSv1.2_2021",
            }
        if spa:
            page = f"/{index}"
            config["CustomErrorResponses"] = {
                "Quantity": 2,
                "Items": [
                    {
                        "ErrorCode": 403,
                        "ResponsePagePath": page,
                        "ResponseCode": "200",
                        "ErrorCachingMinTTL": 10,
                    },
                    {
                        "ErrorCode": 404,
                        "ResponsePagePath": page,
                        "ResponseCode": "200",
                        "ErrorCachingMinTTL": 10,
                    },
                ],
            }
        self._cf.update_distribution(
            Id=distribution_id,
            DistributionConfig=config,
            IfMatch=etag,
        )
        return distribution_id, distribution_arn

    def _find_distribution_by_comment(self, comment: str) -> str:
        # Paginate (default MaxItems 100, default quota 200) so the
        # AlreadyExists reconcile path finds a distribution on any page
        # instead of wedging on a deterministic CallerReference.
        marker = ""
        while True:
            kwargs = {"Marker": marker} if marker else {}
            dist_list = self._cf.list_distributions(**kwargs).get("DistributionList", {})
            for item in dist_list.get("Items", []) or []:
                if item.get("Comment") == comment:
                    return item["Id"]
            marker = dist_list.get("NextMarker", "")
            if not marker:
                break
        raise ManagedServiceError(
            f"distribution with comment {comment!r} reported existing but not found",
        )

    def _origin_refs(self, dist_config: dict[str, Any]) -> tuple[str, str]:
        items = dist_config.get("Origins", {}).get("Items", []) or []
        if not items:
            return "", ""
        origin = items[0]
        domain = origin.get("DomainName", "")
        oac_id = origin.get("OriginAccessControlId", "")
        bucket = domain.split(".s3.")[0] if ".s3." in domain else ""
        return bucket, oac_id

    def _grant_bucket_read(
        self,
        *,
        origin_bucket: str,
        origin_region: str,
        distribution_arn: str,
    ) -> None:
        if not distribution_arn:
            return
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AllowCloudFrontServicePrincipalReadOnly",
                    "Effect": "Allow",
                    "Principal": {"Service": "cloudfront.amazonaws.com"},
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{origin_bucket}/*",
                    "Condition": {
                        "StringEquals": {"AWS:SourceArn": distribution_arn},
                    },
                },
            ],
        }
        # Non-fatal: a concurrent provision may have written an equivalent
        # policy. But this OAC read grant is load-bearing -- without it the
        # distribution gets 403 from the private origin and serves 403 for
        # every object. A genuine failure (missing s3:PutBucketPolicy, a BPA
        # rejection) must not be silently dropped, so log it: a Deployed-but-403
        # site is otherwise undiagnosable (provision still reports ok).
        try:
            self._s3(origin_region).put_bucket_policy(
                Bucket=origin_bucket,
                Policy=json.dumps(policy),
            )
        except Exception as exc:  # diagnosable, not fatal
            log.warning(
                "cdn provision: put_bucket_policy on origin bucket %s failed "
                "(%s); the distribution may serve 403 until the OAC read grant "
                "is applied",
                origin_bucket,
                exc,
            )

    def _oac_id_by_name(self, origin_bucket: str) -> str:
        # The OAC name is deterministic, so the id is recoverable by name on
        # the idempotent teardown path. Absent -> "" (already reaped).
        name = f"{self._config.comment_prefix}-{origin_bucket}-oac"[:64]
        with contextlib.suppress(Exception):
            return self._find_oac_id(name)
        return ""

    def _lambda_oac_id_by_name(self, custom_origin_domain: str) -> str:
        # Lambda OAC counterpart of _oac_id_by_name for the faas custom-origin
        # idempotent teardown path. Absent -> "" (already reaped).
        with contextlib.suppress(Exception):
            return self._find_oac_id(self._lambda_oac_name(custom_origin_domain))
        return ""

    def _cleanup_origin(self, *, origin_bucket: str, oac_id: str) -> None:
        if oac_id:
            with contextlib.suppress(Exception):
                got = self._cf.get_origin_access_control(Id=oac_id)
                self._cf.delete_origin_access_control(
                    Id=oac_id,
                    IfMatch=got.get("ETag", ""),
                )
        if origin_bucket:
            # The origin bucket is single-purpose for this static site;
            # the only policy on it is the OAC read grant we wrote.
            with contextlib.suppress(Exception):
                self._s3().delete_bucket_policy(Bucket=origin_bucket)

    def _wait_until_deployed(
        self,
        distribution_id: str,
        etag: str,
    ) -> tuple[str, bool]:
        for _ in range(_DISABLE_MAX_POLLS):
            current = self._cf.get_distribution(Id=distribution_id)
            etag = current.get("ETag", etag)
            if current["Distribution"].get("Status") == "Deployed":
                return etag, True
            time.sleep(_DISABLE_POLL_SECONDS)
        return etag, False

    def _s3(self, region: str | None = None) -> Any:
        if self._injected_s3 is not None:
            return self._injected_s3
        import boto3

        return boto3.client("s3", region_name=region or self._config.region)
