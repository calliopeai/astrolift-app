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
  --region            ASTROLIFT_CLUSTER_REGION (AWS_REGION only when plugin_slug == 'aws';
                      Azure / GCP take theirs from the described cluster)
  --endpoint          ASTROLIFT_CLUSTER_ENDPOINT         (explicit override)
  --ca-cert           ASTROLIFT_CLUSTER_CA_CERT          (explicit override; base64 or PEM)
  --ingress-class     ASTROLIFT_CLUSTER_INGRESS_CLASS    (default: 'alb' on aws, 'nginx' elsewhere)
  --org-slug          ASTROLIFT_CLUSTER_ORG_SLUG         ('' / 'shared' => unscoped)

Per-cloud auto-discovery (#1474). Each cloud has its own enable flag and
its own cluster-identity inputs; the flag only fires when it matches
``--plugin-slug``, and a mismatch is an error rather than a silently
under-configured row:

  --auto-discover-aws     ASTROLIFT_CLUSTER_AUTO_DISCOVER_AWS
  --aws-cluster-name      ASTROLIFT_CLUSTER_AWS_NAME / EKS_CLUSTER_NAME

  --auto-discover-azure   ASTROLIFT_CLUSTER_AUTO_DISCOVER_AZURE
  --azure-cluster-name    ASTROLIFT_CLUSTER_AZURE_NAME / AKS_CLUSTER_NAME
  --azure-resource-group  ASTROLIFT_CLUSTER_AZURE_RESOURCE_GROUP / AZURE_RESOURCE_GROUP
  --azure-subscription-id ASTROLIFT_CLUSTER_AZURE_SUBSCRIPTION_ID / AZURE_SUBSCRIPTION_ID

  --auto-discover-gcp     ASTROLIFT_CLUSTER_AUTO_DISCOVER_GCP
  --gcp-cluster-name      ASTROLIFT_CLUSTER_GCP_NAME / GKE_CLUSTER_NAME
  --gcp-project-id        ASTROLIFT_CLUSTER_GCP_PROJECT / GOOGLE_CLOUD_PROJECT
  --gcp-location          ASTROLIFT_CLUSTER_GCP_LOCATION (default: --region)

On AWS the command calls eks:DescribeCluster + sts:GetCallerIdentity,
builds the OIDC provider ARN deterministically (no iam:List* needed),
and fills endpoint / ca_cert / auth_config / provider_config in.

On Azure it describes the AKS managed cluster (resolving the resource
group from the subscription when the operator did not supply one) and
lifts the cluster CA out of the admin kubeconfig — AKS is the one cloud
whose managed-cluster object does not carry the CA.

On GCP one container.get_cluster call yields both endpoint and CA.

No new auth path in any of the three: each cloud's driver already
resolves ``exec_plugin`` rows itself (EKS via the AWS exec plugin, AKS
via list_cluster_admin_credentials #311, GKE via a Workload Identity
bearer #310), reading exactly the ``auth_config`` keys written here.

Skips if no slug is provided (silent no-op), so startup scripts can
include the call unconditionally.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from astrolift_clusters.ingress_modes import IngressMode
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization
from astrolift_observability.prom_client import is_cluster_internal_endpoint

logger = logging.getLogger(__name__)

_VALID_AUTH = {"kubeconfig", "exec_plugin", "service_account_token"}


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _json_env(name: str) -> dict[str, Any] | None:
    """A JSON object from the environment, or None.

    Malformed JSON is a startup-time misconfiguration, and this command
    runs on every container start -- refusing here would take the control
    plane down over a stray comma. Warn and fall back to what the row
    already carries, which is what the caller does with None.
    """

    raw = _env(name)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("%s is not valid JSON; ignoring it", name)
        return None
    if not isinstance(parsed, dict):
        logger.warning("%s must be a JSON object, got %s; ignoring it", name, type(parsed).__name__)
        return None
    return parsed


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


def _azure_containerservice_client(subscription_id: str) -> Any:
    """Production AKS management client. Swapped out in tests."""
    from azure.identity import DefaultAzureCredential  # noqa: PLC0415 — optional cloud SDK
    from azure.mgmt.containerservice import ContainerServiceClient  # noqa: PLC0415

    return ContainerServiceClient(
        credential=DefaultAzureCredential(),
        subscription_id=subscription_id,
    )


def _resource_group_from_id(resource_id: str) -> str:
    parts = (resource_id or "").split("/")
    for index, part in enumerate(parts):
        if part.lower() == "resourcegroups" and index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _resolve_azure_resource_group(client: Any, cluster_name: str) -> str:
    """Find the resource group holding ``cluster_name`` by listing the
    subscription's managed clusters.

    An installer knows the AKS cluster name it just created; making it
    also thread the resource group through is the kind of out-of-band
    coupling the platform is supposed to absorb. Ambiguity is fatal
    rather than resolved by picking one: AKS names are unique per
    resource group, not per subscription.
    """
    groups = {
        _resource_group_from_id(getattr(managed, "id", "") or "")
        for managed in client.managed_clusters.list()
        if getattr(managed, "name", "") == cluster_name
    }
    groups.discard("")
    if not groups:
        raise CommandError(
            f"no AKS cluster named {cluster_name!r} in the subscription; "
            "pass --azure-resource-group if the identity cannot list managed clusters",
        )
    if len(groups) > 1:
        raise CommandError(
            f"AKS cluster name {cluster_name!r} exists in resource groups {sorted(groups)}; "
            "pass --azure-resource-group to disambiguate",
        )
    return groups.pop()


def _discover_azure(
    *,
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
    client: Any | None = None,
) -> dict[str, Any]:
    """Describe an AKS managed cluster into the fields a TenantCluster
    row needs to be addressable.

    The CA comes out of the admin kubeconfig because the managed-cluster
    API model does not carry it. That fetch is allowed to fail: a
    cluster with admin credentials disabled still registers, just
    without a pinned CA (the AKS apiserver cert chains to a public root,
    which is what the driver's k8s client falls back to).
    """
    from azure.cluster_aks import (  # noqa: PLC0415 — optional cloud SDK
        certificate_authority_from_kubeconfig,
        kubeconfig_from_admin_credentials,
    )

    client = client or _azure_containerservice_client(subscription_id)
    if not resource_group:
        resource_group = _resolve_azure_resource_group(client, cluster_name)

    managed = client.managed_clusters.get(
        resource_group_name=resource_group,
        resource_name=cluster_name,
    )
    fqdn = str(getattr(managed, "fqdn", "") or getattr(managed, "private_fqdn", "") or "")
    if not fqdn:
        raise CommandError(
            f"AKS {resource_group}/{cluster_name} reports no fqdn; the cluster has no reachable apiserver",
        )

    oidc_profile = getattr(managed, "oidc_issuer_profile", None)
    identity = getattr(managed, "identity", None)

    ca_cert = ""
    ca_error = ""
    try:
        credentials = client.managed_clusters.list_cluster_admin_credentials(
            resource_group_name=resource_group,
            resource_name=cluster_name,
        )
        ca_cert = certificate_authority_from_kubeconfig(
            kubeconfig_from_admin_credentials(
                credentials,
                resource_group=resource_group,
                cluster_name=cluster_name,
            )
        )
    except Exception as exc:  # noqa: BLE001 — CA is best-effort, see docstring
        ca_error = str(exc)

    return {
        "endpoint": fqdn if fqdn.startswith("http") else f"https://{fqdn}",
        "ca_cert": ca_cert,
        "ca_error": ca_error,
        "resource_group": resource_group,
        "location": str(getattr(managed, "location", "") or ""),
        "tenant_id": str(getattr(identity, "tenant_id", "") or ""),
        "oidc_issuer_url": str(getattr(oidc_profile, "issuer_url", "") or ""),
    }


def _gcp_container_client() -> Any:
    """Production GKE management client. Swapped out in tests."""
    from google.cloud import container_v1  # noqa: PLC0415 — optional cloud SDK

    return container_v1.ClusterManagerClient()


def _discover_gcp(
    *,
    project_id: str,
    location: str,
    cluster_name: str,
    client: Any | None = None,
) -> dict[str, Any]:
    """Describe a GKE cluster. One call carries both endpoint and CA."""
    client = client or _gcp_container_client()
    resp = client.get_cluster(
        name=f"projects/{project_id}/locations/{location}/clusters/{cluster_name}",
    )
    endpoint = str(getattr(resp, "endpoint", "") or "")
    if not endpoint:
        raise CommandError(
            f"GKE {project_id}/{location}/{cluster_name} reports no endpoint; "
            "a private cluster needs --endpoint passed explicitly",
        )
    master_auth = getattr(resp, "master_auth", None)
    return {
        "endpoint": endpoint if endpoint.startswith("http") else f"https://{endpoint}",
        "ca_cert": str(getattr(master_auth, "cluster_ca_certificate", "") or ""),
    }


# Only AWS wants a non-default ingress class out of the box: the ALB
# controller is what the AWS install bootstraps. Every other plugin gets
# the model's own default, which is also what registerTenantCluster
# writes — an 'alb' row on AKS/GKE renders ALB-only annotations.
_DEFAULT_INGRESS_CLASS: dict[str, str] = {"aws": "alb"}


class Command(BaseCommand):
    help = "Upsert a TenantCluster row, optionally auto-discovering EKS / AKS / GKE values."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--slug", default=None)
        parser.add_argument("--name", default=None)
        parser.add_argument("--plugin-slug", default=None)
        parser.add_argument("--auth-method", default=None)
        parser.add_argument("--region", default=None)
        parser.add_argument("--endpoint", default=None)
        parser.add_argument("--ca-cert", default=None)
        parser.add_argument("--ingress-class", default=None)
        parser.add_argument(
            "--prometheus-endpoint",
            default=None,
            help=(
                "Base URL of the cluster's Prometheus, as the control plane must reach it "
                "(default: ASTROLIFT_CLUSTER_PROMETHEUS_ENDPOINT). Written to "
                "provider_config['prometheus_endpoint'], which every metrics panel reads. "
                "The control plane queries it over HTTP from outside the cluster, so a "
                "Service ClusterIP or *.svc name will not resolve -- front it with an "
                "internal load balancer. Absent leaves whatever the row has, including the "
                "pod IP the capability probe discovers."
            ),
        )
        parser.add_argument(
            "--ingress-mode",
            default=None,
            choices=[m.value for m in IngressMode],
            help=(
                "shared_ingress: one load balancer fronts every app in the org, via the "
                "ALB group annotation. per_app_ingress: one load balancer per app. "
                "Only honoured on ALB clusters. Left alone when not passed, so "
                "re-registering never re-groups load balancers already serving traffic."
            ),
        )
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
        parser.add_argument(
            "--iam-permissions-boundary-arn",
            default=None,
            help=(
                "IAM permissions boundary every role this cluster mints must carry "
                "(default: ASTROLIFT_CLUSTER_IAM_PERMISSIONS_BOUNDARY_ARN). Required in "
                "pull-mode installs whose installer boundary denies CreateRole without it."
            ),
        )
        parser.add_argument(
            "--auto-discover-azure",
            action="store_true",
            default=None,
            help="Describe the AKS managed cluster to fill Azure values automatically",
        )
        parser.add_argument(
            "--azure-cluster-name",
            default=None,
            help="AKS cluster name to describe (default: --slug or AKS_CLUSTER_NAME)",
        )
        parser.add_argument(
            "--azure-resource-group",
            default=None,
            help="Resource group holding the AKS cluster (default: resolved from the subscription)",
        )
        parser.add_argument(
            "--azure-subscription-id",
            default=None,
            help="Azure subscription holding the AKS cluster",
        )
        parser.add_argument(
            "--auto-discover-gcp",
            action="store_true",
            default=None,
            help="Call container.get_cluster to fill GKE values automatically",
        )
        parser.add_argument(
            "--gcp-cluster-name",
            default=None,
            help="GKE cluster name to describe (default: --slug or GKE_CLUSTER_NAME)",
        )
        parser.add_argument(
            "--gcp-project-id",
            default=None,
            help="GCP project holding the GKE cluster",
        )
        parser.add_argument(
            "--gcp-location",
            default=None,
            help="GKE cluster region or zone (default: --region)",
        )

    def handle(self, *args, **opts) -> None:
        slug = opts["slug"] or _env("ASTROLIFT_CLUSTER_SLUG")
        if not slug:
            self.stdout.write("No --slug / ASTROLIFT_CLUSTER_SLUG; nothing to register.")
            return

        name = opts["name"] or _env("ASTROLIFT_CLUSTER_NAME") or slug
        plugin_slug = opts["plugin_slug"] or _env("ASTROLIFT_CLUSTER_PLUGIN_SLUG") or "aws"
        auth_method = opts["auth_method"] or _env("ASTROLIFT_CLUSTER_AUTH_METHOD") or "exec_plugin"
        region = opts["region"] or _env("ASTROLIFT_CLUSTER_REGION") or ""
        if not region and plugin_slug == "aws":
            # AWS_REGION is set on every control-plane task, including the
            # ones registering an AKS or GKE cluster. Falling back to it
            # regardless of plugin stamps 'us-east-1' on an Azure row.
            region = _env("AWS_REGION") or ""
        endpoint = opts["endpoint"] or _env("ASTROLIFT_CLUSTER_ENDPOINT") or ""
        ca_cert = opts["ca_cert"] or _env("ASTROLIFT_CLUSTER_CA_CERT") or ""
        # No default at resolve time. This command runs on every container
        # start (``startup.py``), so resolving a fallback here and putting it
        # in ``defaults`` overwrote the operator's ingress class on every
        # deploy: a cluster moved onto the central auth host silently went
        # back to ``alb`` the next time the control plane restarted, taking
        # the gate off every app on it, with no audit event because a
        # management command emits none (#1729).
        #
        # Same shape as the ``oidc_auth_config`` fix in #1616 and the
        # ``ingress_mode`` fix in #1537, in this same function: absent means
        # "leave whatever the row has". The plugin default still applies, but
        # only when the row is being created.
        explicit_ingress_class = opts["ingress_class"] or _env("ASTROLIFT_CLUSTER_INGRESS_CLASS") or None
        org_slug = opts["org_slug"] or _env("ASTROLIFT_CLUSTER_ORG_SLUG") or ""
        # No default: absent means "leave whatever the row has". Unlike
        # ingress_class, an unset ingress_mode must not resolve to a value
        # here -- see the write site below (#1537).
        ingress_mode = opts.get("ingress_mode") or _env("ASTROLIFT_CLUSTER_INGRESS_MODE") or None
        if ingress_mode is not None and ingress_mode not in {m.value for m in IngressMode}:
            raise CommandError(
                f"--ingress-mode must be one of {sorted(m.value for m in IngressMode)}, "
                f"got {ingress_mode!r}",
            )

        _alb_auth_pool_arn = _env("ASTROLIFT_CLUSTER_ALB_AUTH_USER_POOL_ARN")
        _alb_auth_client_id = _env("ASTROLIFT_CLUSTER_ALB_AUTH_CLIENT_ID")
        _alb_auth_domain = _env("ASTROLIFT_CLUSTER_ALB_AUTH_DOMAIN")
        alb_auth_config = None
        if _alb_auth_pool_arn and _alb_auth_client_id and _alb_auth_domain:
            alb_auth_config = {
                "user_pool_arn": _alb_auth_pool_arn,
                "user_pool_client_id": _alb_auth_client_id,
                "user_pool_domain": _alb_auth_domain,
            }

        _oidc_discovery_url = _env("ASTROLIFT_CLUSTER_OIDC_DISCOVERY_URL")
        _oidc_client_id = _env("ASTROLIFT_CLUSTER_OIDC_CLIENT_ID")
        _oidc_cookie_secret = _env("ASTROLIFT_CLUSTER_OIDC_COOKIE_SECRET")
        # Either secret completes the config: oauth2-proxy (the nginx edge)
        # needs its cookie secret, the Envoy edge needs only the client
        # secret and keeps its own session keys (#2130). Requiring the cookie
        # secret left an Envoy install with no declarative way to register.
        _oidc_client_secret_env = _env("ASTROLIFT_CLUSTER_OIDC_CLIENT_SECRET")
        oidc_auth_config = None
        if _oidc_discovery_url and _oidc_client_id and (_oidc_cookie_secret or _oidc_client_secret_env):
            # auth_proxy_host is the hostname of the in-cluster oauth2-proxy
            # (e.g. auth.cluster.example.com). If not set explicitly, derive
            # it from the discovery URL by replacing the "dex" subdomain with
            # "auth" (the platform's conventional layout: Dex at
            # dex.<domain>/dex, oauth2-proxy at auth.<domain>).
            _oidc_auth_proxy_host = _env("ASTROLIFT_CLUSTER_OIDC_AUTH_PROXY_HOST")
            if not _oidc_auth_proxy_host:
                # Strip scheme and path from discovery_url to get the host.
                # e.g. "https://dex.cluster.example.com/dex" -> "dex.cluster.example.com"
                # Then replace leading "dex." with "auth.".
                _stripped = _oidc_discovery_url.split("//", 1)[-1].split("/")[0]
                if _stripped.startswith("dex."):
                    _oidc_auth_proxy_host = "auth." + _stripped[4:]
                else:
                    _oidc_auth_proxy_host = _stripped
            oidc_auth_config = {
                "discovery_url": _oidc_discovery_url,
                "client_id": _oidc_client_id,
                "upstream_connector": _env("ASTROLIFT_CLUSTER_OIDC_UPSTREAM_CONNECTOR") or "google",
                "auth_proxy_host": _oidc_auth_proxy_host,
            }
            # This dict REPLACES oidc_auth_config wholesale, so a key the
            # environment does not carry is not merely left unset -- it is
            # deleted on the next container start. Two keys are owned by the
            # operator rather than by the environment, and both have to be
            # carried forward or declaring the OIDC vars silently destroys
            # them:
            #
            # ``gateway_secret`` (#1726) -- lose it and every gated app starts
            # refusing traffic it can no longer prove came through the gate.
            #
            # ``proxy_extra_args`` (#1716) -- the operator's provider flags,
            # e.g. ``insecure-oidc-allow-unverified-email`` for a federation
            # whose upstream cannot assert the claim. Lose it and every
            # federated login 500s at the callback again.
            #
            # Environment wins where it declares one, so an install can move
            # either to the declarative path without re-issuing a secret or
            # re-deriving flags that are already deployed and working.
            _existing_oidc: dict = {}
            _existing_row = TenantCluster.all_objects.filter(slug=slug).first()
            if _existing_row is not None:
                _existing_oidc = _existing_row.oidc_auth_config or {}

            # ``cookie_secret`` -- the oauth2-proxy session key. Optional
            # since #2130, so an Envoy install's environment omits it; carry
            # an operator-set one forward rather than dropping it.
            _cookie_secret = _oidc_cookie_secret or _existing_oidc.get("cookie_secret", "")
            if _cookie_secret:
                oidc_auth_config["cookie_secret"] = _cookie_secret

            _oidc_gateway_secret = _env("ASTROLIFT_CLUSTER_OIDC_GATEWAY_SECRET") or _existing_oidc.get(
                "gateway_secret", ""
            )
            if _oidc_gateway_secret:
                oidc_auth_config["gateway_secret"] = _oidc_gateway_secret

            # ``client_secret`` (#2055) -- the install writes the auth host's
            # credentials Secret from it, so losing it on a restart leaves the
            # next install unable to.
            _oidc_client_secret = _oidc_client_secret_env or _existing_oidc.get("client_secret", "")
            if _oidc_client_secret:
                oidc_auth_config["client_secret"] = _oidc_client_secret

            _proxy_args = _json_env("ASTROLIFT_CLUSTER_OIDC_PROXY_EXTRA_ARGS") or _existing_oidc.get(
                "proxy_extra_args"
            )
            if _proxy_args:
                oidc_auth_config["proxy_extra_args"] = _proxy_args

            # Startup must not erase the logout URL set by an operator.
            _logout_url = _env("ASTROLIFT_CLUSTER_OIDC_LOGOUT_URL") or _existing_oidc.get("logout_url")
            if _logout_url:
                from providers.k8s_native.logout import configured_logout_url

                oidc_auth_config["logout_url"] = _logout_url
                try:
                    configured_logout_url(oidc_auth_config)
                except ValueError as exc:
                    raise CommandError(str(exc)) from exc

        auto_discover = opts["auto_discover_aws"]
        if auto_discover is None:
            auto_discover = _env_bool("ASTROLIFT_CLUSTER_AUTO_DISCOVER_AWS")
        aws_cluster_name = (
            opts["aws_cluster_name"] or _env("ASTROLIFT_CLUSTER_AWS_NAME") or _env("EKS_CLUSTER_NAME") or slug
        )

        auto_discover_azure = opts["auto_discover_azure"]
        if auto_discover_azure is None:
            auto_discover_azure = _env_bool("ASTROLIFT_CLUSTER_AUTO_DISCOVER_AZURE")
        azure_cluster_name = (
            opts["azure_cluster_name"]
            or _env("ASTROLIFT_CLUSTER_AZURE_NAME")
            or _env("AKS_CLUSTER_NAME")
            or slug
        )
        azure_resource_group = (
            opts["azure_resource_group"]
            or _env("ASTROLIFT_CLUSTER_AZURE_RESOURCE_GROUP")
            or _env("AZURE_RESOURCE_GROUP")
            or ""
        )
        azure_subscription_id = (
            opts["azure_subscription_id"]
            or _env("ASTROLIFT_CLUSTER_AZURE_SUBSCRIPTION_ID")
            or _env("AZURE_SUBSCRIPTION_ID")
            or ""
        )

        auto_discover_gcp = opts["auto_discover_gcp"]
        if auto_discover_gcp is None:
            auto_discover_gcp = _env_bool("ASTROLIFT_CLUSTER_AUTO_DISCOVER_GCP")
        gcp_cluster_name = (
            opts["gcp_cluster_name"] or _env("ASTROLIFT_CLUSTER_GCP_NAME") or _env("GKE_CLUSTER_NAME") or slug
        )
        gcp_project_id = (
            opts["gcp_project_id"]
            or _env("ASTROLIFT_CLUSTER_GCP_PROJECT")
            or _env("GOOGLE_CLOUD_PROJECT")
            or ""
        )
        gcp_location = opts["gcp_location"] or _env("ASTROLIFT_CLUSTER_GCP_LOCATION") or region

        # A discovery flag aimed at the wrong plugin used to be a silent
        # skip, which is how an operator ends up with a row that saved
        # cleanly and cannot be addressed.
        for flag, owner, requested in (
            ("--auto-discover-aws", "aws", auto_discover),
            ("--auto-discover-azure", "azure", auto_discover_azure),
            ("--auto-discover-gcp", "gcp", auto_discover_gcp),
        ):
            if requested and plugin_slug != owner:
                raise CommandError(f"{flag} requires --plugin-slug {owner}, got {plugin_slug!r}")

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

        if auto_discover_azure and plugin_slug == "azure":
            if not azure_subscription_id:
                raise CommandError(
                    "--azure-subscription-id / AZURE_SUBSCRIPTION_ID required with --auto-discover-azure",
                )
            self.stdout.write(
                f"Auto-discovering Azure values for AKS cluster {azure_cluster_name!r}…",
            )
            try:
                discovered = _discover_azure(
                    subscription_id=azure_subscription_id,
                    resource_group=azure_resource_group,
                    cluster_name=azure_cluster_name,
                )
            except CommandError:
                raise
            except Exception as exc:
                raise CommandError(f"Azure auto-discovery failed: {exc}") from exc

            azure_resource_group = discovered["resource_group"]
            endpoint = endpoint or discovered["endpoint"]
            ca_cert = ca_cert or discovered["ca_cert"]
            region = region or discovered["location"]
            if not ca_cert and discovered["ca_error"]:
                # exec_plugin auth resolves through the same admin
                # credentials, so a failure here usually means the row
                # will not be able to reach the apiserver either.
                self.stdout.write(
                    self.style.WARNING(
                        f"AKS admin kubeconfig unavailable ({discovered['ca_error']}); "
                        "registering without a pinned CA",
                    )
                )
            auth_config = {
                "subscription_id": azure_subscription_id,
                "resource_group": azure_resource_group,
                "cluster_name": azure_cluster_name,
            }
            provider_config = {
                "subscription_id": azure_subscription_id,
                "resource_group": azure_resource_group,
                "cluster_name": azure_cluster_name,
                "location": region,
            }
            if discovered["tenant_id"]:
                provider_config["tenant_id"] = discovered["tenant_id"]
            if discovered["oidc_issuer_url"]:
                provider_config["cluster_oidc_issuer"] = discovered["oidc_issuer_url"]

        if auto_discover_gcp and plugin_slug == "gcp":
            if not gcp_project_id:
                raise CommandError(
                    "--gcp-project-id / GOOGLE_CLOUD_PROJECT required with --auto-discover-gcp",
                )
            if not gcp_location:
                raise CommandError("--gcp-location / --region required with --auto-discover-gcp")
            self.stdout.write(
                f"Auto-discovering GCP values for GKE cluster {gcp_cluster_name!r} in {gcp_location}…",
            )
            try:
                discovered = _discover_gcp(
                    project_id=gcp_project_id,
                    location=gcp_location,
                    cluster_name=gcp_cluster_name,
                )
            except CommandError:
                raise
            except Exception as exc:
                raise CommandError(f"GCP auto-discovery failed: {exc}") from exc

            endpoint = endpoint or discovered["endpoint"]
            ca_cert = ca_cert or discovered["ca_cert"]
            region = region or gcp_location
            auth_config = {
                "project_id": gcp_project_id,
                "location": gcp_location,
                "cluster_name": gcp_cluster_name,
            }
            provider_config = {
                "project_id": gcp_project_id,
                "location": gcp_location,
                "cluster_name": gcp_cluster_name,
            }

        # Resolve org binding. Empty / 'shared' / 'platform' => unscoped row.
        org: Organization | None = None
        if org_slug and org_slug.lower() not in {"shared", "platform", "none"}:
            org = Organization.objects.filter(slug=org_slug).first()
            if org is None:
                raise CommandError(f"organization {org_slug!r} not found")

        # The boundary the cloud requires every role this cluster mints to
        # carry (#1681). #1679 added ``IRSAConfig.permissions_boundary_arn``,
        # read from ``provider_config["iam_permissions_boundary_arn"]`` --
        # and nothing ever wrote that key, so the fix was correct, tested
        # and inert on every install. In pull mode the installer runs under
        # a boundary whose DenyRoleCreationWithoutThisBoundary statement
        # refuses any CreateRole that does not attach the same boundary, so
        # both roles Astrolift mints per app (IRSA + kaniko build) are denied
        # until the cluster knows which one to attach.
        _boundary = opts.get("iam_permissions_boundary_arn") or _env(
            "ASTROLIFT_CLUSTER_IAM_PERMISSIONS_BOUNDARY_ARN"
        )
        if _boundary:
            provider_config["iam_permissions_boundary_arn"] = _boundary

        # The endpoint every metrics panel reads (#1711). Until now the only
        # write paths were the registration UI and the capability probe's
        # pod-IP discovery, which re-resolves to nothing when the Prometheus
        # pod is rescheduled -- an install with a stable internal load
        # balancer in front of Prometheus had no supported way to say so.
        _prometheus_endpoint = (
            opts.get("prometheus_endpoint") or _env("ASTROLIFT_CLUSTER_PROMETHEUS_ENDPOINT") or ""
        ).strip()
        if _prometheus_endpoint:
            provider_config["prometheus_endpoint"] = _prometheus_endpoint
            if is_cluster_internal_endpoint(_prometheus_endpoint):
                # Not an error: a control plane deployed into the same
                # cluster reaches this fine. Said out loud because for the
                # deployment shape this command usually runs in it does not,
                # and the failure it produces reads as "Prometheus is down".
                self.stdout.write(
                    self.style.WARNING(
                        f"prometheus_endpoint {_prometheus_endpoint!r} is a cluster-internal "
                        "address. Unless the control plane runs inside this cluster it cannot "
                        "reach it; front Prometheus with an internal load balancer."
                    )
                )

        # Merge over what the row already carries rather than replacing it.
        # ``provider_config`` is assigned wholesale, and it is ``{}`` on any
        # run that does not auto-discover -- so a re-register with discovery
        # off deleted every discovered value (account id, OIDC provider arn,
        # ECR registry) and took managed services and workload identity with
        # them. Same rule #1616 settled for oidc_auth_config: clearing live
        # config should take a deliberate act, not a partially-configured
        # re-run that happens on every container start.
        _existing_row_pc = TenantCluster.all_objects.filter(slug=slug).first()
        if _existing_row_pc is not None:
            provider_config = {**(_existing_row_pc.provider_config or {}), **provider_config}

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
            "is_active": True,
            "deleted_at": None,
            "deleted_by": None,
        }
        # Only write oidc_auth_config when this run actually built one.
        # It used to sit in ``defaults`` unconditionally, so a re-run whose
        # environment had lost any one of the three OIDC vars reset the field
        # to null and every later deploy rendered unauthenticated Ingresses
        # (#1616). Clearing a live gate should take a deliberate act, not a
        # partially-configured re-run.
        if oidc_auth_config is not None:
            defaults["oidc_auth_config"] = oidc_auth_config
        # Same rule for the Cognito gate (#1773): the control plane re-runs
        # this command on every container start, and a task definition without
        # the three ASTROLIFT_CLUSTER_ALB_AUTH_* vars was nulling an
        # operator-set config, so every app deployed afterwards rendered an
        # Ingress without the authenticate action.
        if alb_auth_config is not None:
            defaults["alb_auth_config"] = alb_auth_config
        # Only when passed. Defaulting it here would re-group load balancers
        # that are already serving traffic the next time anyone re-registers
        # a cluster, which is the operator decision the model docstring
        # deliberately refuses to make for them (#1537).
        if ingress_mode is not None:
            defaults["ingress_mode"] = ingress_mode
        if explicit_ingress_class is not None:
            defaults["ingress_class"] = explicit_ingress_class

        # ``create_defaults`` applies only on insert, so a brand-new row still
        # gets the plugin's ingress class while an existing row keeps the one
        # the operator set.
        create_defaults = {
            **defaults,
            "ingress_class": (explicit_ingress_class or _DEFAULT_INGRESS_CLASS.get(plugin_slug, "nginx")),
        }
        obj, created = TenantCluster.all_objects.update_or_create(
            slug=slug, defaults=defaults, create_defaults=create_defaults
        )

        action = "created" if created else "updated"
        scope = f"org={org.slug}" if org else "shared"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} tenant cluster {slug!r} ({scope}, plugin={plugin_slug}, "
                f"endpoint={endpoint or 'n/a'})"
            )
        )

        # A cluster declared onto the Envoy edge gets it installed with no
        # operator step (#2130). Only for class ``envoy`` with a complete
        # config, and additive, so any other install is untouched.
        from astrolift_clusters.edge_install import ensure_edge_installed

        if ensure_edge_installed(obj):
            self.stdout.write(f"started the Envoy edge install on {slug!r}")
