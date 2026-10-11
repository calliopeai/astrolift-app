"""Current exact app/domain target admission and metadata-only route reviews."""

from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import contextmanager
from dataclasses import asdict
from uuid import UUID

from django.db.models import Q
from django.utils.crypto import salted_hmac

from astrolift_clusters.domain_diagnostics import scoped_hostname
from astrolift_clusters.models import ManagedDomain
from astrolift_lifecycle import domain_route_authority as authority
from astrolift_lifecycle.models import AppEnvironment, CustomDomain, Deployment
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.scopes import live_app_owners
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    ScopeKind,
    require_platform_operator,
)
from core.tenancy import get_current_tenant


class DomainAppRouteError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def private_digest(value):
    return salted_hmac(
        "astrolift.domain-app-route-source.v1",
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        algorithm="sha256",
    ).hexdigest()


def _guid(value):
    try:
        result = UUID(str(value))
        if result.int == 0:
            raise ValueError
        return result
    except (ValueError, TypeError, AttributeError):
        raise DomainAppRouteError("INVALID_ROUTE_TARGET") from None


def selected_domain(domain_id, version):
    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None:
        raise DomainAppRouteError("CURRENT_ORGANIZATION_REQUIRED")
    row = ManagedDomain.objects.filter(
        Q(organization_id=tenant.organization_id) | Q(organization__isnull=True), guid=_guid(domain_id)
    ).first()
    if row is None:
        raise DomainAppRouteError("MANAGED_DOMAIN_NOT_FOUND")
    if type(version) is not int or row.version != version:
        raise DomainAppRouteError("MANAGED_DOMAIN_CHANGED")
    if row.organization_id is None:
        require_platform_operator(Permission.PROVIDER_PLUGIN_CONFIGURE)
    if row.verification_state == ManagedDomain.VerificationState.PENDING:
        raise DomainAppRouteError("MANAGED_DOMAIN_CONTROL_UNVERIFIED")
    return row


def permissions_for(environment, *, read=False):
    app = environment.registered_app
    org = get_current_tenant().organization_id
    return (
        (Permission.APP_READ if read else Permission.APP_UPDATE, PermissionScope(ScopeKind.APP, app.pk)),
        (
            Permission.PROVIDER_PLUGIN_READ if read else Permission.PROVIDER_PLUGIN_CONFIGURE,
            PermissionScope(ScopeKind.ORG, org),
        ),
        *(() if read else ((Permission.CLUSTER_MANAGE, PermissionScope(ScopeKind.ORG, org)),)),
    )


def target(domain, environment_id, environment_version, workload_id, *, reference):
    tenant = get_current_tenant()
    env = (
        AppEnvironment.objects.select_related("registered_app", "tenant_cluster__provider_plugin")
        .filter(
            guid=_guid(environment_id),
            registered_app__organization_id=tenant.organization_id,
            registered_app__deleted_at__isnull=True,
            tenant_cluster__deleted_at__isnull=True,
        )
        .first()
    )
    if env is None or not live_app_owners(RegisteredApp.objects.filter(pk=env.registered_app_id)).exists():
        raise DomainAppRouteError("APP_TARGET_NOT_FOUND")
    if type(environment_version) is not int or env.version != environment_version:
        raise DomainAppRouteError("APP_ENVIRONMENT_CHANGED")
    workload = Workload.objects.filter(
        guid=_guid(workload_id),
        registered_app_id=env.registered_app_id,
        is_public=True,
        kind__in=("deployment", "faas"),
    ).first()
    if workload is None:
        raise DomainAppRouteError("PUBLIC_WORKLOAD_NOT_FOUND")
    cluster = env.tenant_cluster
    if cluster.lifecycle != "managed" or cluster.delivery_mode != "direct_api":
        raise DomainAppRouteError("APP_CLUSTER_DIRECT_MANAGEMENT_REQUIRED")
    if (
        cluster.organization_id not in (None, tenant.organization_id)
        or not cluster.provider_plugin_id
        or cluster.provider_plugin.deleted_at is not None
        or not cluster.provider_plugin.is_enabled
    ):
        raise DomainAppRouteError("APP_CLUSTER_UNAVAILABLE")
    with authority.current(reference, environment=env, permissions=permissions_for(env)):
        if cluster.organization_id is None or domain.organization_id is None:
            require_platform_operator(Permission.CLUSTER_MANAGE)
    if workload.kind != "faas":
        raise DomainAppRouteError("DEPLOYMENT_DOMAIN_ROUTE_NOT_SUPPORTED")
    if env.registered_app.provisioning_status in ("tearing_down", "deregistered"):
        raise DomainAppRouteError("APP_TARGET_RETIRING")
    if cluster.provider_plugin.slug != "aws" or (
        workload.kind == "deployment" and cluster.ingress_class != "envoy"
    ):
        raise DomainAppRouteError("CUSTOM_DOMAIN_EDGE_UNSUPPORTED")
    if env.previewed_environment_id or env.ingress_paused or env.deploys_paused:
        raise DomainAppRouteError("APP_ENVIRONMENT_ROUTING_UNAVAILABLE")
    deployment = (
        Deployment.objects.filter(
            app_environment_id=env.pk,
            registered_app_id=env.registered_app_id,
            status=Deployment.Status.RUNNING,
        )
        .order_by("-created_at", "-pk")
        .first()
    )
    if deployment is None:
        raise DomainAppRouteError("RUNNING_APP_DEPLOYMENT_REQUIRED")
    from astrolift_manifest.normalize import NormalizationDefaults, normalize
    from astrolift_manifest.parser import parse_raw
    from core.app_deploy import namespace_for_environment

    try:
        manifest = normalize(parse_raw(env.registered_app.manifest_raw), defaults=NormalizationDefaults())
        actual = next(w for w in manifest.workloads if w.name == workload.slug)
        if actual.kind != workload.kind or not actual.is_public:
            raise ValueError
        if actual.kind == "deployment":
            container = next((c for c in actual.containers if c.is_primary), actual.containers[0])
            if type(container.port) is not int or not 1 <= container.port <= 65535:
                raise ValueError
            port = container.port
        elif actual.kind == "faas" and actual.faas_public:
            port = 0
        else:
            raise ValueError
    except Exception:
        raise DomainAppRouteError("PUBLIC_WORKLOAD_SOURCE_INVALID") from None
    from core.ingress_reconcile import _edge_config

    edge = _edge_config(env.registered_app) or {}
    from core.edge_access import app_access, is_restricted

    if workload.kind == "faas" and is_restricted(app_access(env.registered_app)):
        raise DomainAppRouteError("FAAS_CUSTOM_DOMAIN_AUTH_UNSUPPORTED")
    if edge.get("identity_headers"):
        raise DomainAppRouteError("CUSTOM_DOMAIN_HEADERS_UNSUPPORTED")
    return env, workload, deployment, namespace_for_environment(env), actual.name, port


def faas_services(env, workload):
    from astrolift_services.models import ManagedService

    names = (f"{workload.slug}-{env.name or 'default'}-fn", f"{workload.slug}-{env.name or 'default'}-api")
    rows = []
    for kind, variant, name in zip(("faas", "api_gateway"), ("lambda", "http_api"), names, strict=True):
        matches = list(
            ManagedService.objects.filter(
                registered_app_id=env.registered_app_id,
                app_environment_id=env.pk,
                kind=kind,
                variant=variant,
                name=name,
            )[:2]
        )
        if len(matches) != 1:
            raise DomainAppRouteError("ORIGINAL_FAAS_SERVICE_REQUIRED")
        row = matches[0]
        if row.status != "active" or not row.backend_ref or row.applied_config != row.config:
            raise DomainAppRouteError("CURRENT_FAAS_SERVICE_NOT_OBSERVED")
        rows.append(row)
    return tuple(rows)


def service_snapshot(services):
    if services is None:
        return None
    return [
        {
            "id": str(r.guid),
            "version": r.version,
            "handle": r.backend_ref,
            "config_sha": private_digest(r.config),
            "placement_sha": private_digest(r.provider_placement_identity),
            "status": r.status,
        }
        for r in services
    ]


@contextmanager
def http_api_port(selected, hostname, *, operation_id, current, certificate_arn="", source_fingerprint=""):
    from _sdk.domain_route_http_api import HttpApiRouteTarget
    from aws.domain_route_api_gateway import HttpApiDomainPort
    from aws.domain_route_clients import clients
    from aws.managed._base import parse_handle

    from core.cluster_credentials import credential_for_cluster

    current()
    env, workload, *_ = selected
    faas, api = faas_services(env, workload)
    cluster = env.tenant_cluster
    account = str(cluster.provider_config.get("account_id") or cluster.cloud_account_id or "")
    region = str(cluster.provider_config.get("region") or cluster.region or "")
    if not re.fullmatch(r"[0-9]{12}", account):
        raise DomainAppRouteError("ORIGINAL_NATIVE_ACCOUNT_REQUIRED")
    try:
        function_kind, function_name = parse_handle(faas.backend_ref)
        api_kind, api_id = parse_handle(api.backend_ref)
    except Exception:
        raise DomainAppRouteError("ORIGINAL_FAAS_HANDLE_INVALID") from None
    if (
        function_kind != "faas"
        or api_kind != "api_gateway"
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", function_name)
        or not re.fullmatch(r"[a-z0-9]{1,32}", api_id)
    ):
        raise DomainAppRouteError("ORIGINAL_FAAS_HANDLE_INVALID")
    function_arn = f"arn:aws:lambda:{region}:{account}:function:{function_name}"
    if api.config.get("lambda_function_arn") != function_arn:
        raise DomainAppRouteError("ORIGINAL_API_FUNCTION_BINDING_CHANGED")
    target = HttpApiRouteTarget(
        str(_guid(operation_id)),
        hostname,
        account,
        region,
        "aws",
        api_id,
        str(api.guid),
        str(faas.guid),
        function_arn,
        certificate_arn,
        source_fingerprint,
    )
    current()
    with clients(
        region=region,
        credential=credential_for_cluster(cluster),
        expected_account_id=account,
        checkpoint=current,
        services=("sts", "apigatewayv2", "lambda"),
    ) as (native, _account):
        yield (
            HttpApiDomainPort(
                api_client=native["apigatewayv2"], lambda_client=native["lambda"], sts_client=native["sts"]
            ),
            target,
        )
    current()


def snapshot(domain, selected, hostname):
    env, workload, deployment, namespace, service, port = selected
    app, cluster = env.registered_app, env.tenant_cluster
    services = faas_services(env, workload) if workload.kind == "faas" else None
    return {
        "schema": 1,
        "target_kind": "FAAS_HTTP_API" if services else "DEPLOYMENT",
        "native_services": service_snapshot(services),
        "domain_id": str(domain.guid),
        "domain_version": domain.version,
        "domain_org_id": domain.organization_id,
        "zone": domain.zone,
        "dns_driver": domain.dns_driver,
        "zone_id": domain.provision_zone_id,
        "connection_id": domain.dns_provider_connection_id,
        "connection_version": domain.dns_provider_connection_version,
        "domain_config_sha": private_digest(domain.dns_config),
        "domain_control_sha": private_digest(
            [
                domain.verification_state,
                domain.provision_nameservers,
                domain.provision_state,
                domain.default_for,
                private_digest(domain.verification_token),
            ]
        ),
        "certificate_id": domain.provision_cert_id,
        "app_id": str(app.guid),
        "app_version": app.version,
        "app_org_id": app.organization_id,
        "app_source_sha": private_digest([app.manifest_raw, app.manifest_normalized]),
        "app_edge_sha": private_digest(app.edge_access),
        "environment_id": str(env.guid),
        "environment_version": env.version,
        "environment_config_sha": private_digest(env.deploy_config),
        "workload_id": str(workload.guid),
        "workload_version": workload.version,
        "deployment_id": str(deployment.guid),
        "deployment_version": deployment.version,
        "cluster_id": str(cluster.guid),
        "cluster_version": cluster.version,
        "provider_id": str(cluster.provider_plugin.guid),
        "provider_version": cluster.provider_plugin.version,
        "cluster_source_sha": private_digest(
            [
                cluster.provider_config,
                cluster.auth_config,
                cluster.oidc_auth_config,
                cluster.endpoint,
                cluster.ca_cert,
                cluster.region,
                cluster.auth_method,
                cluster.lifecycle,
                cluster.delivery_mode,
                cluster.ingress_class,
                cluster.cloud_account_id,
            ]
        ),
        "hostname": scoped_hostname(hostname, domain.zone),
        "namespace": namespace,
        "service": service,
        "port": port,
    }


def review(info, *, domain_id, domain_version, environment_id, environment_version, workload_id, hostname):
    reference = authority.capture(info.context.request)
    domain = selected_domain(domain_id, domain_version)
    selected = target(domain, environment_id, environment_version, workload_id, reference=reference)
    accepted = snapshot(domain, selected, hostname)
    database_snapshot = dict(accepted)
    env = selected[0]
    deadline = time.monotonic() + 60

    def current():
        if time.monotonic() >= deadline:
            raise DomainAppRouteError("ROUTE_REVIEW_DEADLINE")
        with authority.current(reference, environment=env, permissions=permissions_for(env)):
            fresh_domain = selected_domain(domain_id, domain_version)
            fresh_selected = target(
                fresh_domain, environment_id, environment_version, workload_id, reference=reference
            )
            if snapshot(fresh_domain, fresh_selected, hostname) != database_snapshot:
                raise DomainAppRouteError("CURRENT_ROUTE_TARGET_CHANGED")

    current()
    from core.app_deploy import driver_for_capability

    if accepted["target_kind"] == "FAAS_HTTP_API":
        # This UUID identifies a read only; it is excluded from the native
        # source fingerprint and never authorizes a cloud effect.
        from uuid import uuid4

        with http_api_port(
            selected,
            accepted["hostname"],
            operation_id=uuid4(),
            certificate_arn=domain.provision_cert_id,
            current=current,
        ) as (port, native_target):
            observed_api = port.observe_target(native_target, current=current)
            port.inspect_allocation(native_target, current=current)
            native_metadata = asdict(observed_api)
            native_metadata["target"].pop("operation_id")
            accepted["native_api"] = native_metadata
        current()
        certificate_status = "MISSING"
        dns = driver_for_capability(env.tenant_cluster, "dns")
        if domain.provision_cert_id:
            cert = dns.observe_reviewed_domain_certificate(
                zone=domain.zone,
                hostname=accepted["hostname"],
                arn=domain.provision_cert_id,
                expected_account_id=native_target.account_id,
                checkpoint=current,
            )
            certificate_status = cert.status
        accepted["edge"] = {
            "account_id": native_target.account_id,
            "region": native_target.region,
            "dns_name": None,
            "canonical_zone_id": None,
            "certificate_status": certificate_status,
            "allocation_state": "PENDING_NATIVE_DOMAIN",
        }
    else:
        raise DomainAppRouteError("DEPLOYMENT_DOMAIN_ROUTE_NOT_SUPPORTED")
    current()
    if domain.dns_driver == "cloudflare_read_only" or domain.dns_provider_connection_id:
        accepted["writer"] = "MANUAL"
    elif domain.dns_driver not in ("aws", "route53") or not domain.provision_zone_id:
        accepted["writer"] = "UNSUPPORTED"
    else:
        from _sdk.dns import ReviewedDnsHostname

        dns = driver_for_capability(env.tenant_cluster, "dns")
        current()
        accepted["dns_present"] = dns.observe_reviewed_hostname(
            ReviewedDnsHostname(
                domain.provision_zone_id, domain.zone, accepted["hostname"], accepted["edge"]["account_id"]
            ),
            checkpoint=current,
        )
        accepted["dns_matches"] = False
        accepted["writer"] = "ROUTE53_ALIAS"
    # The native edge observation is additional evidence, not part of the original database reread.
    current()
    from astrolift_registry.models import HostnameClaim

    collision = (
        CustomDomain.objects.filter(hostname=accepted["hostname"]).exists()
        or HostnameClaim.objects.filter(managed_domain=domain, hostname=accepted["hostname"]).exists()
    )
    accepted["can_attach"] = (
        accepted["writer"] == "ROUTE53_ALIAS"
        and not accepted.get("dns_present", False)
        and not collision
        and accepted["edge"]["certificate_status"] == "ISSUED"
    )
    accepted["can_prepare"] = (
        accepted["writer"] == "ROUTE53_ALIAS" and not domain.provision_cert_id and not collision
    )
    return accepted, digest(accepted), reference


def support(info, domain_id, domain_version):
    from astrolift_lifecycle.schema.domain_app_routes import ManagedDomainAppRouteSupport

    reference = authority.capture(info.context.request)
    domain = selected_domain(domain_id, domain_version)
    with authority.current(
        reference,
        permissions=(
            (
                Permission.PROVIDER_PLUGIN_READ,
                PermissionScope(ScopeKind.ORG, get_current_tenant().organization_id),
            ),
        ),
    ):
        writer = (
            "MANUAL"
            if domain.dns_driver == "cloudflare_read_only" or domain.dns_provider_connection_id
            else (
                "ROUTE53_ALIAS"
                if domain.dns_driver in ("route53", "aws") and domain.provision_zone_id
                else "UNSUPPORTED"
            )
        )
    return ManagedDomainAppRouteSupport(
        domain_id=domain.guid,
        version=domain.version,
        allowed=writer != "UNSUPPORTED",
        writer=writer,
        reason="SELECT_EXACT_APP_TARGET"
        if writer == "ROUTE53_ALIAS"
        else (
            "READ_ONLY_PROVIDER_MANUAL_DNS_REQUIRED" if writer == "MANUAL" else "TRUSTED_NATIVE_ZONE_REQUIRED"
        ),
    )


def targets_page(info, domain_id, domain_version, first, after):
    from django.db.models import Prefetch

    from astrolift_graphql import keyset_page
    from astrolift_identity import abac
    from astrolift_identity.permission_resolver import resolve_effective_permissions_for_apps
    from astrolift_lifecycle.schema.domain_app_routes import (
        ManagedDomainAppRouteTarget,
        ManagedDomainAppRouteTargetsPage,
    )
    from core.permissions import _check_permission_decision

    reference = authority.capture(info.context.request)
    selected_domain(domain_id, domain_version)
    tenant = get_current_tenant()
    if type(first) is not int or not 1 <= first <= 50:
        raise DomainAppRouteError("INVALID_TARGET_PAGE_SIZE")
    with authority.current(
        reference,
        permissions=(
            (Permission.PROVIDER_PLUGIN_READ, PermissionScope(ScopeKind.ORG, tenant.organization_id)),
        ),
    ):
        apps = live_app_owners(
            RegisteredApp.objects.filter(organization_id=tenant.organization_id).exclude(
                provisioning_status__in=("tearing_down", "deregistered")
            )
        )
        rows = (
            AppEnvironment.objects.filter(registered_app__in=apps, tenant_cluster__deleted_at__isnull=True)
            .filter(
                Q(tenant_cluster__organization_id=tenant.organization_id)
                | Q(tenant_cluster__organization__isnull=True)
            )
            .select_related("registered_app", "tenant_cluster__provider_plugin")
            .prefetch_related(
                Prefetch(
                    "registered_app__workloads",
                    queryset=Workload.objects.filter(is_public=True, kind="faas"),
                    to_attr="route_workloads",
                )
            )
        )
        page = keyset_page(rows, cursor=after, limit=first)
        contexts = {}
        for env in page.rows:
            key = (env.name, env.tenant_cluster.region)
            contexts.setdefault(key, {})[env.registered_app_id] = env.registered_app
        decisions = {}
        for (name, region), candidates in contexts.items():
            with abac.operation_attributes(environment=name, region=region, approvals=0):
                decisions[(name, region)] = resolve_effective_permissions_for_apps(
                    tenant, candidates.values()
                )
        items = []
        for env in page.rows:
            try:
                scope = PermissionScope(ScopeKind.APP, env.registered_app_id)
                from astrolift_registry.scopes import _credential_scope

                _credential_scope(scope, Permission.APP_UPDATE)
                _check_permission_decision(
                    Permission.APP_UPDATE,
                    scope,
                    lambda env=env: (
                        Permission.APP_UPDATE.value
                        in decisions[(env.name, env.tenant_cluster.region)].get(env.registered_app_id, set()),
                        "App target unavailable.",
                    ),
                )
            except PermissionDenied:
                continue
            workloads = env.registered_app.route_workloads
            if len(workloads) > 20:
                raise DomainAppRouteError("PUBLIC_WORKLOAD_INVENTORY_LIMIT")
            for workload in workloads:
                if len(items) >= 200:
                    raise DomainAppRouteError("PUBLIC_WORKLOAD_INVENTORY_LIMIT")
                items.append(
                    ManagedDomainAppRouteTarget(
                        kind="FAAS_HTTP_API" if workload.kind == "faas" else "DEPLOYMENT",
                        app_id=env.registered_app.guid,
                        app_name=env.registered_app.name,
                        app_version=env.registered_app.version,
                        environment_id=env.guid,
                        environment_name=env.name,
                        environment_version=env.version,
                        workload_id=workload.guid,
                        workload_name=workload.name,
                        workload_version=workload.version,
                        cluster_id=env.tenant_cluster.guid,
                        cluster_name=env.tenant_cluster.name,
                        cluster_version=env.tenant_cluster.version,
                    )
                )
        selected_domain(domain_id, domain_version)
    return ManagedDomainAppRouteTargetsPage(
        items=items, next_cursor=page.next_cursor, has_next_page=page.next_cursor is not None
    )


def legacy_custom_domain_allowed(domain):
    from astrolift_lifecycle.models import ManagedDomainAppBinding

    return not ManagedDomainAppBinding.all_objects.filter(custom_domain_id=domain.pk).exists()


def require_legacy_custom_domain(domain):
    if not legacy_custom_domain_allowed(domain):
        raise DomainAppRouteError("PROTECTED_DOMAIN_ROUTE_MANAGE_REQUIRED")


def protected_hostname_pairs(app_id):
    """Retained Connect ownership survives ordinary manifest and teardown passes."""
    from astrolift_lifecycle.models import ManagedDomainAppBinding

    return set(
        ManagedDomainAppBinding.all_objects.filter(registered_app_id=app_id, purpose="connect").values_list(
            "managed_domain_id", "hostname"
        )
    )


def require_unbound_app(app_id):
    if protected_hostname_pairs(app_id):
        raise DomainAppRouteError("PROTECTED_DOMAIN_ROUTE_MANAGE_REQUIRED")


def require_unbound_service(service_id):
    from astrolift_lifecycle.models import ManagedDomainAppBinding

    if ManagedDomainAppBinding.all_objects.filter(
        Q(faas_service_id=service_id) | Q(api_service_id=service_id), purpose="connect"
    ).exists():
        raise DomainAppRouteError("PROTECTED_DOMAIN_ROUTE_MANAGE_REQUIRED")


def require_unbound_zone(*, domain_id=None, zone=None):
    from astrolift_lifecycle.models import ManagedDomainAppBinding

    rows = ManagedDomainAppBinding.all_objects.all()
    if domain_id is not None:
        rows = rows.filter(managed_domain_id=domain_id)
    elif isinstance(zone, str):
        rows = rows.filter(target_snapshot__zone=zone.rstrip(".").lower())
    else:
        raise DomainAppRouteError("INVALID_ROUTE_TARGET")
    if rows.exists():
        raise DomainAppRouteError("PROTECTED_DOMAIN_ROUTE_MANAGE_REQUIRED")
