"""
``manage.py register_tenant_cluster``

Operator-side cluster registration that mirrors the
``registerTenantCluster`` GraphQL mutation but runs from the backend
container itself. Two motivating reasons:

  1. The UI form expects an operator to paste in endpoint / CA / OIDC
     ARN by hand. On AWS those values are already addressable through
     ``eks:DescribeCluster`` + STS, which the backend task role has —
     ``--auto-discover-aws`` pulls them so the operator only has to
     decide the slug + display name.

  2. Bootstrapping a fresh install is a chicken-and-egg: the operator
     needs Cognito SSO to land on the UI, but until at least one
     cluster is registered the dashboard buries every action behind
     'no cluster' errors. A management command runs at deploy time
     (optionally wired into ON_STARTUP via env vars) and seeds the
     first cluster before the first human ever logs in.

Idempotent: re-running with the same slug updates the row rather than
creating a duplicate. Soft-delete cleared on upsert so an operator who
accidentally unregistered the cluster recovers it on next deploy.

Inputs (CLI flag overrides env var):

  --slug              ASTROLIFT_CLUSTER_SLUG
  --name              ASTROLIFT_CLUSTER_NAME             (default = slug)
  --plugin-slug       ASTROLIFT_CLUSTER_PLUGIN_SLUG      (default 'aws')
  --auth-method       ASTROLIFT_CLUSTER_AUTH_METHOD      (default 'exec_plugin')
  --region            ASTROLIFT_CLUSTER_REGION / AWS_REGION
  --endpoint          ASTROLIFT_CLUSTER_ENDPOINT         (explicit override)
  --ca-cert           ASTROLIFT_CLUSTER_CA_CERT          (explicit override; base64 or PEM)
  --ingress-class     ASTROLIFT_CLUSTER_INGRESS_CLASS    (default 'alb')
  --org-slug          ASTROLIFT_CLUSTER_ORG_SLUG         ('' / 'shared' => unscoped)
  --auto-discover-aws ASTROLIFT_CLUSTER_AUTO_DISCOVER_AWS ('1' / 'true' to enable)
  --aws-cluster-name  ASTROLIFT_CLUSTER_AWS_NAME / EKS_CLUSTER_NAME

When --auto-discover-aws is set (and plugin_slug == 'aws'), the command
calls eks:DescribeCluster + sts:GetCallerIdentity itself, builds the
OIDC provider ARN deterministically (no iam:List* needed), and fills
endpoint / ca_cert / auth_config / provider_config in.

Skips if no slug is provided (silent no-op), so startup scripts can
include the call unconditionally.
"""

from __future__ import annotations

import os
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization

_VALID_AUTH = {"kubeconfig", "exec_plugin", "service_account_token"}


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _discover_aws(cluster_name: str, region: str) -> dict[str, Any]:
    """Hit EKS + STS with whatever creds the process has and return the
    fields the TenantCluster row needs to be useful at deploy planning
    time. boto3 ships with the ``[aws]`` extras of the vendored
    astrolift-providers install — ``import boto3`` is safe here.
    """
    import boto3  # noqa: PLC0415 — lazy import keeps the command importable when boto3 is missing

    sts = boto3.client("sts", region_name=region)
    eks = boto3.client("eks", region_name=region)

    account_id = sts.get_caller_identity()["Account"]
    resp = eks.describe_cluster(name=cluster_name)["cluster"]

    endpoint = resp["endpoint"]
    ca_b64 = resp["certificateAuthority"]["data"]
    issuer = resp["identity"]["oidc"]["issuer"]
    # ARN format is stable: arn:aws:iam::<account>:oidc-provider/<issuer-host-and-path>
    issuer_host_path = issuer.replace("https://", "", 1)
    oidc_provider_arn = f"arn:aws:iam::{account_id}:oidc-provider/{issuer_host_path}"

    return {
        "endpoint": endpoint,
        "ca_cert": ca_b64,
        "account_id": account_id,
        "oidc_issuer_url": issuer,
        "oidc_provider_arn": oidc_provider_arn,
    }


class Command(BaseCommand):
    help = "Upsert a TenantCluster row, optionally auto-discovering EKS values."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--slug", default=None)
        parser.add_argument("--name", default=None)
        parser.add_argument("--plugin-slug", default=None)
        parser.add_argument("--auth-method", default=None)
        parser.add_argument("--region", default=None)
        parser.add_argument("--endpoint", default=None)
        parser.add_argument("--ca-cert", default=None)
        parser.add_argument("--ingress-class", default=None)
        parser.add_argument("--org-slug", default=None)
        parser.add_argument(
            "--auto-discover-aws",
            action="store_true",
            default=None,
            help="Call eks:DescribeCluster + STS to fill AWS values automatically",
        )
        parser.add_argument(
            "--aws-cluster-name",
            default=None,
            help="EKS cluster name to describe (default: --slug or EKS_CLUSTER_NAME)",
        )

    def handle(self, *args, **opts) -> None:
        slug = opts["slug"] or _env("ASTROLIFT_CLUSTER_SLUG")
        if not slug:
            self.stdout.write("No --slug / ASTROLIFT_CLUSTER_SLUG; nothing to register.")
            return

        name = opts["name"] or _env("ASTROLIFT_CLUSTER_NAME") or slug
        plugin_slug = opts["plugin_slug"] or _env("ASTROLIFT_CLUSTER_PLUGIN_SLUG") or "aws"
        auth_method = opts["auth_method"] or _env("ASTROLIFT_CLUSTER_AUTH_METHOD") or "exec_plugin"
        region = opts["region"] or _env("ASTROLIFT_CLUSTER_REGION") or _env("AWS_REGION") or ""
        endpoint = opts["endpoint"] or _env("ASTROLIFT_CLUSTER_ENDPOINT") or ""
        ca_cert = opts["ca_cert"] or _env("ASTROLIFT_CLUSTER_CA_CERT") or ""
        ingress_class = opts["ingress_class"] or _env("ASTROLIFT_CLUSTER_INGRESS_CLASS") or "alb"
        org_slug = opts["org_slug"] or _env("ASTROLIFT_CLUSTER_ORG_SLUG") or ""

        auto_discover = opts["auto_discover_aws"]
        if auto_discover is None:
            auto_discover = _env_bool("ASTROLIFT_CLUSTER_AUTO_DISCOVER_AWS")
        aws_cluster_name = (
            opts["aws_cluster_name"]
            or _env("ASTROLIFT_CLUSTER_AWS_NAME")
            or _env("EKS_CLUSTER_NAME")
            or slug
        )

        if auth_method not in _VALID_AUTH:
            raise CommandError(f"--auth-method must be one of {sorted(_VALID_AUTH)}")

        plugin = ProviderPlugin.objects.filter(slug=plugin_slug).first()
        if plugin is None:
            raise CommandError(
                f"plugin {plugin_slug!r} not registered — run bootstrap_provider_plugins first"
            )

        auth_config: dict[str, Any] = {}
        provider_config: dict[str, Any] = {}

        if auto_discover and plugin_slug == "aws":
            if not region:
                raise CommandError("--region / AWS_REGION required with --auto-discover-aws")
            self.stdout.write(
                f"Auto-discovering AWS values for EKS cluster {aws_cluster_name!r} in {region}…"
            )
            try:
                discovered = _discover_aws(aws_cluster_name, region)
            except Exception as exc:
                raise CommandError(f"AWS auto-discovery failed: {exc}") from exc

            endpoint = endpoint or discovered["endpoint"]
            ca_cert = ca_cert or discovered["ca_cert"]
            auth_config = {
                "cluster_name": aws_cluster_name,
                "region": region,
            }
            provider_config = {
                "account_id": discovered["account_id"],
                "region": region,
                "oidc_provider_arn": discovered["oidc_provider_arn"],
                "cluster_oidc_issuer": discovered["oidc_issuer_url"].replace("https://", "", 1),
                "ecr_registry": f"{discovered['account_id']}.dkr.ecr.{region}.amazonaws.com",
            }

        # Resolve org binding. Empty / 'shared' / 'platform' => unscoped row.
        org: Organization | None = None
        if org_slug and org_slug.lower() not in {"shared", "platform", "none"}:
            org = Organization.objects.filter(slug=org_slug).first()
            if org is None:
                raise CommandError(f"organization {org_slug!r} not found")

        # Upsert. all_objects to bypass soft-delete filtering so an
        # accidentally-deleted row gets restored rather than duplicated.
        defaults = {
            "organization": org,
            "provider_plugin": plugin,
            "name": name,
            "auth_method": auth_method,
            "region": region,
            "endpoint": endpoint,
            "ca_cert": ca_cert,
            "auth_config": auth_config,
            "provider_config": provider_config,
            "ingress_class": ingress_class,
            "is_active": True,
            "deleted_at": None,
            "deleted_by": None,
        }
        obj, created = TenantCluster.all_objects.update_or_create(
            slug=slug, defaults=defaults
        )

        action = "created" if created else "updated"
        scope = f"org={org.slug}" if org else "shared"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} tenant cluster {slug!r} ({scope}, plugin={plugin_slug}, "
                f"endpoint={endpoint or 'n/a'})"
            )
        )
