"""Current-authority, exact-zone observations without lifecycle/cloud writes."""

import json
import re
import time
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.core.cache import DEFAULT_CACHE_ALIAS, caches
from django.core.cache.backends.locmem import LocMemCache
from django.core.cache.backends.redis import RedisCache
from django.db.models import Q
from django.utils import timezone
from graphql import GraphQLError

from astrolift_clusters.dns_layout import NsDelegationCheck, evaluate_ns_delegation
from astrolift_clusters.models import ManagedDomain, TenantCluster
from astrolift_clusters.models.managed_domain import resolve_managed_domain
from astrolift_clusters.schema.domain_diagnostics import (
    ManagedDomainActions,
    ManagedDomainDiagnosticCheck,
    ManagedDomainDiagnosticRecord,
    ManagedDomainDiagnostics,
    ManagedDomainProbe,
    ManagedDomainProbeTool,
    ManagedDomainProviderZone,
    ManagedDomainRoute,
)
from astrolift_clusters.schema.domain_diagnostics import (
    ManagedDomainCheckState as State,
)
from astrolift_clusters.scopes import cluster_catalog_org_scope, cluster_org_scope, domain_org_scope
from astrolift_graphql import GUID
from astrolift_identity import abac
from astrolift_identity.api_tokens import get_current_api_token, with_active_org_member
from astrolift_identity.models import ApiToken, Member, Organization
from astrolift_identity.permission_resolver import resolve_effective_permissions_for_apps
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import live_app_owners
from astrolift_registry.visibility import visible_registry_apps
from core.current_credential import current_dispatch_credential
from core.current_session import fresh_authenticated_session
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    ScopeKind,
    _check_permission_decision,
    check_permission,
    require_platform_operator,
)
from core.tenancy import get_current_tenant

from . import domain_diagnostic_network as network

P = Permission.PROVIDER_PLUGIN_READ
MAX_RECORDS = 100
MAX_PAGES = 3


def _charge(domain):
    tenant = get_current_tenant()
    minute = int(time.time() // 60)
    buckets = (
        (f"actor:{tenant.actor_user_id}:org:{tenant.organization_id}:domain:{domain.guid}", 10),
        (f"actor:{tenant.actor_user_id}:org:{tenant.organization_id}", 30),
        (f"org:{tenant.organization_id}:domain:{domain.guid}", 60),
    )
    try:
        backend = caches[DEFAULT_CACHE_ALIAS]
        if not isinstance(backend, (RedisCache, LocMemCache)):
            raise ValueError
        for key, maximum in buckets:
            key = f"managed-domain-diagnostics:v1:{minute}:{key}"
            backend.add(key, 0, 120)
            if backend.incr(key) > maximum:
                raise GraphQLError(
                    "Domain diagnostic rate limit reached.", extensions={"code": "RATE_LIMITED"}
                )
    except GraphQLError:
        raise
    except Exception:
        raise GraphQLError(
            "Domain diagnostic admission is unavailable.", extensions={"code": "UNAVAILABLE"}
        ) from None


def hostname(value):
    if not isinstance(value, str) or value != value.strip() or len(value) > 253:
        raise ValueError("INVALID_DOMAIN_HOSTNAME")
    name = value.lower().rstrip(".")
    if not name or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in name.split(".")
    ):
        raise ValueError("INVALID_DOMAIN_HOSTNAME")
    return name


def scoped_hostname(value, zone):
    value, zone = hostname(value), hostname(zone)
    if value != zone and not value.endswith("." + zone):
        raise ValueError("HOSTNAME_OUTSIDE_MANAGED_DOMAIN")
    return value


def scoped_record_name(value, zone):
    if not isinstance(value, str) or value != value.strip() or len(value) > 253:
        raise ValueError("INVALID_DOMAIN_HOSTNAME")
    name, zone = value.lower().rstrip("."), hostname(zone)
    if any(
        not re.fullmatch(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?", label) for label in name.split(".")
    ) or (name != zone and not name.endswith("." + zone)):
        raise ValueError("HOSTNAME_OUTSIDE_MANAGED_DOMAIN")
    return name


@contextmanager
def _auth(info):
    tenant = get_current_tenant()
    request = info.context.request
    if (
        tenant is None
        or tenant.actor_user_id is None
        or tenant.organization_id is None
        or request.user.pk != tenant.actor_user_id
    ):
        raise PermissionDenied(P, None, "An authenticated organization actor is required.")
    with current_dispatch_credential(P):
        user = get_user_model().objects.filter(pk=tenant.actor_user_id, is_active=True).first()
        if (
            user is None
            or not Organization.objects.filter(pk=tenant.organization_id, deleted_at__isnull=True).exists()
        ):
            raise PermissionDenied(P, None, "Current organization actor is unavailable.")
        token = get_current_api_token()
        session = {}
        if token is not None:
            if not with_active_org_member(
                ApiToken.objects.filter(pk=token.pk, deleted_at__isnull=True),
                user="user",
                organization="organization",
            ).exists():
                raise PermissionDenied(P, None, "Current credential membership is unavailable.")
        else:
            session = fresh_authenticated_session(request, actor_user_id=user.pk, permission=P)
            if (
                not user.is_superuser
                and not Member.objects.filter(
                    user=user, scope_kind="ORG", scope_id=tenant.organization_id, is_active=True
                ).exists()
            ):
                raise PermissionDenied(P, None, "Current organization membership is unavailable.")
        with abac.request_attributes(
            abac.attributes_from_request(
                SimpleNamespace(META=getattr(request, "META", {}), session=session), user.pk
            )
        ):
            yield user


def _operator(user):
    try:
        require_platform_operator(user)
        return True
    except Exception:
        return False


def _allowed(permission, scope):
    try:
        check_permission(permission, scope=scope)
        return True
    except PermissionDenied:
        return False


def provision_clusters(domains):
    """Resolve legacy internal references once, never expose untrusted IDs."""
    from uuid import UUID

    domains = tuple(domains)
    references = {}
    for domain in domains:
        value = (domain.dns_config or {}).get("provision_cluster_id")
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int) or isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,18}", value):
            references[domain.pk] = ("pk", int(value))
        else:
            try:
                references[domain.pk] = ("guid", UUID(str(value)))
            except (ValueError, TypeError):
                continue
    if not references:
        return {}
    rows = TenantCluster.objects.filter(
        Q(pk__in=[value for kind, value in references.values() if kind == "pk"])
        | Q(guid__in=[value for kind, value in references.values() if kind == "guid"]),
        deleted_at__isnull=True,
        is_active=True,
    )
    by_pk = {row.pk: row for row in rows}
    by_guid = {row.guid: row for row in by_pk.values()}
    result = {}
    for domain in domains:
        ref = references.get(domain.pk)
        if ref is None:
            continue
        cluster = (by_pk if ref[0] == "pk" else by_guid).get(ref[1])
        if cluster is not None and cluster.organization_id in (domain.organization_id, None):
            result[domain.pk] = cluster
    return result


def provision_cluster(domain):
    return provision_clusters((domain,)).get(domain.pk)


def actions(info, domain=None):
    with _auth(info) as user:
        operator = _operator(user)
        configure = Permission.PROVIDER_PLUGIN_CONFIGURE
        result = ManagedDomainActions(
            can_create=_allowed(configure, cluster_catalog_org_scope(configure)({}))
        )
        if domain is not None:
            result.can_delete = (domain.organization_id is not None or operator) and _allowed(
                configure, domain_org_scope(configure, "domain_id")({"domain_id": str(domain.guid)})
            )
            cluster = provision_cluster(domain)
            manage = Permission.CLUSTER_MANAGE
            result.can_revalidate = bool(
                domain.dns_driver != "cloudflare_read_only"
                and domain.verification_state != ManagedDomain.VerificationState.PENDING
                and cluster
                and (domain.organization_id is not None or operator)
                and (cluster.organization_id is not None or operator)
                and _allowed(manage, cluster_org_scope(manage)({"cluster_id": str(cluster.guid)}))
            )
        return result


def read_domain(info, domain_id, expected_version=None):
    with _auth(info) as user:
        tenant = get_current_tenant()
        domain = (
            ManagedDomain.objects.select_related("organization")
            .filter(
                Q(organization_id=tenant.organization_id) | Q(organization__isnull=True),
                guid=domain_id,
                deleted_at__isnull=True,
            )
            .first()
        )
        if domain is None:
            return None
        check_permission(P, scope=domain_org_scope(P, "domain_id")({"domain_id": str(domain.guid)}))
        if domain.organization_id is None and not _operator(user):
            raise PermissionDenied(P, None, "Shared domain diagnostics require the platform operator.")
        if expected_version is not None and (
            type(expected_version) is not int or expected_version != domain.version
        ):
            raise ValueError("MANAGED_DOMAIN_VERSION_CHANGED")
        hostname(domain.zone)
        return domain


def _snapshot(domain):
    return json.dumps(
        (
            domain.version,
            domain.updated_at.isoformat(),
            domain.organization_id,
            domain.zone,
            domain.dns_driver,
            domain.dns_config,
            domain.provision_zone_id,
            domain.provision_nameservers,
            domain.provision_state,
            domain.verification_state,
            domain.dns_provider_connection_id,
            domain.dns_provider_zone_id,
            domain.dns_provider_connection_version,
        ),
        sort_keys=True,
        allow_nan=False,
    )


def _current(info, domain):
    saved, started = _snapshot(domain), time.monotonic()

    def current():
        if time.monotonic() - started >= 30:
            raise ValueError("DOMAIN_DIAGNOSTIC_DEADLINE_EXCEEDED")
        fresh = read_domain(info, domain.guid, domain.version)
        if fresh is None or _snapshot(fresh) != saved:
            raise ValueError("MANAGED_DOMAIN_SOURCE_CHANGED")

    return current


def _route53(current):
    import boto3
    from botocore.config import Config
    from botocore.session import Session

    current()
    session = Session()
    config = Config(
        connect_timeout=3,
        read_timeout=3,
        retries={"total_max_attempts": 1},
        ignore_configured_endpoint_urls=True,
    )
    session.set_default_client_config(config)
    session.register("before-send.*.*", lambda **_kwargs: current())
    current()
    client = boto3.Session(botocore_session=session).client("route53", region_name="us-east-1", config=config)
    return client


def _zone(info, domain, current):
    now = timezone.now()
    unsupported = ManagedDomainProviderZone(
        state=State.UNSUPPORTED,
        reason="PROVIDER_INVENTORY_UNSUPPORTED",
        checked_at=now,
        zone_id=None,
        zone_name=None,
        private_zone=None,
        nameservers=[],
        records=[],
        truncated=False,
    )
    with _auth(info) as user:
        if not _operator(user):
            return unsupported
    # A protected attachment is authoritative for discovery even when an
    # older Route53 writer remains configured. Invalid or withdrawn binding
    # metadata must never fall back to ambient AWS credentials.
    if (
        domain.dns_driver == "cloudflare_read_only"
        or domain.dns_provider_connection_id is not None
        or domain.dns_provider_zone_id
        or domain.dns_provider_connection_version is not None
    ):
        return _cloudflare_zone(info, domain, current)
    if domain.dns_driver != "route53":
        return unsupported
    candidate = domain.provision_zone_id or (domain.dns_config or {}).get("zone_id", "")
    candidate = candidate.removeprefix("/hostedzone/") if isinstance(candidate, str) else ""
    if not re.fullmatch(r"Z[A-Z0-9]{1,63}", candidate):
        unsupported.reason = "PROVIDER_ZONE_NOT_BOUND"
        return unsupported
    client = None
    try:
        client = _route53(current)
        current()
        result = client.get_hosted_zone(Id=candidate)
        current()
        zone = result["HostedZone"]
        if (
            zone["Id"].removeprefix("/hostedzone/") != candidate
            or hostname(zone["Name"]) != hostname(domain.zone)
            or type(zone["Config"]["PrivateZone"]) is not bool
        ):
            raise ValueError("PROVIDER_ZONE_IDENTITY_MISMATCH")
        nameservers = sorted(
            hostname(value) for value in result.get("DelegationSet", {}).get("NameServers", [])
        )
        records, continuation, seen = [], {}, set()
        truncated = False
        for _ in range(MAX_PAGES):
            current()
            page = client.list_resource_record_sets(
                HostedZoneId=candidate, MaxItems=str(MAX_RECORDS - len(records) + 1), **continuation
            )
            current()
            for raw in page["ResourceRecordSets"]:
                name = raw["Name"].lower().rstrip(".")
                scoped_record_name(name.removeprefix("*."), domain.zone)
                values = [row["Value"] for row in raw.get("ResourceRecords", [])]
                if len(values) > 64 or any(
                    not isinstance(value, str) or len(value) > 4096 for value in values
                ):
                    raise ValueError("PROVIDER_RECORD_BOUND_EXCEEDED")
                alias = raw.get("AliasTarget", {})
                if raw.get("TTL") is not None and (
                    type(raw["TTL"]) is not int or not 0 <= raw["TTL"] <= 2147483647
                ):
                    raise ValueError("PROVIDER_RECORD_INVALID")
                if alias and (
                    not isinstance(alias.get("DNSName"), str)
                    or len(alias["DNSName"]) > 253
                    or not isinstance(alias.get("HostedZoneId"), str)
                    or not re.fullmatch(r"Z[A-Z0-9]{1,63}", alias["HostedZoneId"])
                    or type(alias.get("EvaluateTargetHealth")) is not bool
                ):
                    raise ValueError("PROVIDER_RECORD_INVALID")
                records.append(
                    ManagedDomainDiagnosticRecord(
                        name=name,
                        type=raw["Type"],
                        ttl=raw.get("TTL"),
                        values=values,
                        alias_target=alias.get("DNSName"),
                        alias_zone_id=alias.get("HostedZoneId"),
                        evaluate_target_health=alias.get("EvaluateTargetHealth"),
                    )
                )
            if len(records) > MAX_RECORDS:
                truncated = True
                records = records[:MAX_RECORDS]
                break
            truncated = bool(page["IsTruncated"])
            if not truncated:
                break
            continuation = {
                "StartRecordName": page["NextRecordName"],
                "StartRecordType": page["NextRecordType"],
            }
            if page.get("NextRecordIdentifier"):
                continuation["StartRecordIdentifier"] = page["NextRecordIdentifier"]
            key = json.dumps(continuation, sort_keys=True)
            if key in seen:
                raise ValueError("PROVIDER_CURSOR_INVALID")
            seen.add(key)
            truncated = True
        return ManagedDomainProviderZone(
            state=State.OK,
            reason="PROVIDER_ZONE_OBSERVED",
            checked_at=now,
            zone_id=candidate,
            zone_name=hostname(zone["Name"]),
            private_zone=zone["Config"]["PrivateZone"],
            nameservers=nameservers,
            records=records,
            truncated=truncated,
            binding_source="PLATFORM_WRITTEN" if domain.provision_zone_id else "OPERATOR_CONFIGURATION",
        )
    except Exception as error:
        current()
        code = getattr(error, "response", {}).get("Error", {}).get("Code")
        reason = (
            "PROVIDER_ACCESS_DENIED"
            if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation")
            else "PROVIDER_ZONE_UNAVAILABLE"
        )
        return ManagedDomainProviderZone(
            state=State.ERROR,
            reason=reason,
            checked_at=now,
            zone_id=candidate,
            zone_name=None,
            private_zone=None,
            nameservers=[],
            records=[],
            truncated=False,
        )
    finally:
        if client is not None:
            client.close()


def _cloudflare_zone(info, domain, current):
    from astrolift_clusters.cloudflare_dns_transport import DnsConnectionError
    from astrolift_clusters.dns_provider_connections import observe_zone, observe_zone_records
    from astrolift_clusters.models import DnsProviderConnection

    result = ManagedDomainProviderZone(
        state=State.ERROR,
        reason="PROVIDER_ZONE_NOT_BOUND",
        checked_at=timezone.now(),
        zone_id=None,
        zone_name=None,
        private_zone=None,
        nameservers=[],
        records=[],
        truncated=False,
        binding_source="PROTECTED_CLOUDFLARE_CONNECTION",
    )
    current()
    version, zone_id = domain.dns_provider_connection_version, domain.dns_provider_zone_id
    if (
        domain.organization_id is None
        or domain.dns_provider_connection_id is None
        or type(version) is not int
        or version < 1
        or not isinstance(zone_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", zone_id)
    ):
        return result
    connection = DnsProviderConnection.objects.filter(
        pk=domain.dns_provider_connection_id,
        organization_id=domain.organization_id,
        provider="CLOUDFLARE",
        deleted_at__isnull=True,
    ).first()
    if connection is None:
        result.reason = "PROVIDER_CONNECTION_UNAVAILABLE"
        return result
    try:
        request = info.context.request
        zone = observe_zone(request, connection.guid, version, zone_id, domain.zone, checkpoint=current)
        current()
        if (
            zone.connection_id != connection.guid
            or zone.connection_version != version
            or zone.id != zone_id
            or hostname(zone.name) != hostname(domain.zone)
        ):
            raise ValueError("PROVIDER_ZONE_IDENTITY_MISMATCH")
        records = observe_zone_records(
            request, connection.guid, version, zone_id, domain.zone, checkpoint=current
        )
        current()
        final_zone = observe_zone(request, connection.guid, version, zone_id, domain.zone, checkpoint=current)
        current()
        if final_zone != zone:
            raise ValueError("PROVIDER_ZONE_IDENTITY_MISMATCH")
        values = []
        for record in records[:MAX_RECORDS]:
            # Transport validates the complete bounded inventory first; this
            # additional suffix check keeps the common diagnostic boundary.
            name = scoped_record_name(record.name.removeprefix("*."), domain.zone)
            if record.name.startswith("*."):
                name = "*." + name
            values.append(
                ManagedDomainDiagnosticRecord(
                    name=name,
                    type=record.type,
                    ttl=record.ttl,
                    values=[record.content],
                    proxied=record.proxied,
                    priority=record.priority,
                )
            )
        return ManagedDomainProviderZone(
            state=State.OK,
            reason="PROVIDER_ZONE_OBSERVED_READ_ONLY",
            checked_at=timezone.now(),
            zone_id=zone.id,
            zone_name=hostname(zone.name),
            private_zone=False,
            nameservers=list(zone.name_servers),
            records=values,
            truncated=len(records) > MAX_RECORDS,
            binding_source="PROTECTED_CLOUDFLARE_CONNECTION",
        )
    except Exception as error:
        # Re-admit after a failure: changed domain/caller is a refusal, never
        # a successful response containing previous provider observations.
        current()
        reason = error.reason if isinstance(error, DnsConnectionError) else "PROVIDER_ZONE_UNAVAILABLE"
        result.reason = (
            "PROVIDER_ACCESS_DENIED"
            if reason in ("PERMISSION_DENIED", "FORBIDDEN", "UNAUTHENTICATED")
            else "PROVIDER_CONNECTION_UNAVAILABLE"
            if reason in ("VERSION_MISMATCH", "DISCONNECTED", "EXPIRED", "NOT_FOUND", "CONNECTION_CHANGED")
            else "PROVIDER_ZONE_UNAVAILABLE"
        )
        return result


def _routes(info, domain):
    with _auth(info):
        org = get_current_tenant().organization_id
        apps = visible_registry_apps(
            live_app_owners(RegisteredApp.objects.filter(organization_id=org)), Permission.APP_READ
        )
        rows = list(
            AppEnvironment.objects.filter(registered_app__in=apps)
            .filter(
                url__iregex=r"^https?://(?:[a-z0-9-]+\.)*"
                + re.escape(hostname(domain.zone))
                + r"(?::[0-9]+)?(?:/|$)"
            )
            .select_related("registered_app", "tenant_cluster")
            .order_by("guid")[:51]
        )
        permissions = resolve_effective_permissions_for_apps(
            get_current_tenant(), {row.registered_app_id: row.registered_app for row in rows}.values()
        )
        routes = []
        for row in rows:
            parsed = urlsplit(row.url)
            if (
                parsed.scheme not in ("https", "http")
                or parsed.username
                or parsed.password
                or not parsed.hostname
            ):
                continue
            try:
                name = scoped_hostname(parsed.hostname, domain.zone)
                _check_permission_decision(
                    Permission.APP_READ,
                    PermissionScope(kind=ScopeKind.APP, id=row.registered_app_id),
                    lambda app_id=row.registered_app_id: (
                        Permission.APP_READ.value in permissions.get(app_id, set()),
                        "App route is unavailable.",
                    ),
                )
            except (ValueError, PermissionDenied):
                continue
            cluster = row.tenant_cluster
            if cluster and (cluster.deleted_at or cluster.organization_id not in (None, org)):
                cluster = None
            routes.append(
                ManagedDomainRoute(
                    app_id=GUID(str(row.registered_app.guid)),
                    app_name=row.registered_app.name,
                    app_slug=row.registered_app.slug,
                    environment_id=GUID(str(row.guid)),
                    environment_name=row.name,
                    recorded_url=parsed._replace(query="", fragment="").geturl(),
                    hostname=name,
                    cluster_id=GUID(str(cluster.guid)) if cluster else None,
                    cluster_name=cluster.name if cluster else None,
                    cluster_slug=cluster.slug if cluster else None,
                    ingress_class=cluster.ingress_class if cluster else None,
                    observed_state=State.UNKNOWN,
                )
            )
        return routes[:50], len(rows) > 50


def diagnostics(info, domain_id, version, requested, record_type):
    domain = read_domain(info, domain_id, version)
    if domain is None:
        return None
    name = scoped_record_name(requested or domain.zone, domain.zone)
    current = _current(info, domain)
    _charge(domain)
    provider = _zone(info, domain, current)
    expected = provider.nameservers or sorted(hostname(value) for value in domain.provision_nameservers or [])
    checks = []
    for resolver in network.RESOLVERS:
        now = timezone.now()
        try:
            observed, reason = network.dns_lookup(hostname(domain.zone), "NS", resolver, current=current)
            state = State.UNKNOWN if not expected else State.OK
            if provider.private_zone:
                state, reason = State.UNSUPPORTED, "PRIVATE_ZONE_PUBLIC_DELEGATION_NOT_APPLICABLE"
            elif expected:
                passed, _ = evaluate_ns_delegation(
                    check=NsDelegationCheck(domain.zone, frozenset(expected), frozenset(observed))
                )
                state, reason = (
                    (State.OK, "PUBLIC_DELEGATION_MATCH")
                    if passed
                    else (State.MISMATCH, "PUBLIC_DELEGATION_MISMATCH")
                )
            checks.append(
                ManagedDomainDiagnosticCheck(
                    key="delegation",
                    state=state,
                    perspective="public_dns:" + resolver,
                    checked_at=now,
                    reason=reason,
                    expected=expected,
                    observed=observed,
                )
            )
        except Exception:
            current()
            checks.append(
                ManagedDomainDiagnosticCheck(
                    key="delegation",
                    state=State.UNKNOWN,
                    perspective="public_dns:" + resolver,
                    checked_at=now,
                    reason="DNS_LOOKUP_UNAVAILABLE",
                    expected=expected,
                    observed=[],
                )
            )
    checks.append(
        ManagedDomainDiagnosticCheck(
            key="internal_dns",
            state=State.UNSUPPORTED,
            perspective="cluster_internal_dns",
            checked_at=timezone.now(),
            reason="INTERNAL_DNS_PROBE_NOT_CONFIGURED",
            expected=[],
            observed=[],
        )
    )
    lookup = _probe(domain, current, name, ManagedDomainProbeTool.LOOKUP, record_type)
    checks.append(
        ManagedDomainDiagnosticCheck(
            key="lookup",
            state=lookup.state,
            perspective=lookup.perspective,
            checked_at=lookup.checked_at,
            reason=lookup.reason,
            expected=[],
            observed=lookup.values,
        )
    )
    org = Organization.objects.select_related("default_managed_domain").get(
        pk=get_current_tenant().organization_id
    )
    for key, preview in (("effective_tenant_apps_domain", False), ("effective_preview_domain", True)):
        effective = resolve_managed_domain(org, for_preview=preview)
        safe = effective is not None and effective.organization_id in (None, org.pk)
        checks.append(
            ManagedDomainDiagnosticCheck(
                key=key,
                state=State.OK
                if safe and effective.pk == domain.pk
                else State.MISMATCH
                if safe
                else State.UNKNOWN,
                perspective="recorded_platform_configuration",
                checked_at=timezone.now(),
                reason="REGISTERED_DOMAIN_IS_EFFECTIVE_DEFAULT"
                if safe and effective.pk == domain.pk
                else "REGISTERED_DOMAIN_NOT_EFFECTIVE_DEFAULT"
                if safe
                else "EFFECTIVE_DEFAULT_UNAVAILABLE",
                expected=[domain.zone],
                observed=[effective.zone] if safe else [],
            )
        )
    routes, truncated = _routes(info, domain)
    current()
    cluster = provision_cluster(domain)
    return ManagedDomainDiagnostics(
        id=GUID(str(domain.guid)),
        version=domain.version,
        zone=domain.zone,
        verification_state=domain.verification_state,
        provision_state=domain.provision_state,
        checked_at=timezone.now(),
        checks=checks,
        provider_zone=provider,
        routes=routes,
        routes_truncated=truncated,
        actions=actions(info, domain),
        provision_cluster_id=GUID(str(cluster.guid)) if cluster else None,
    )


def probe(info, domain_id, version, requested, tool, record_type):
    domain = read_domain(info, domain_id, version)
    if domain is None:
        return None
    name = (
        scoped_record_name(requested, domain.zone)
        if tool.value in ("LOOKUP", "DIG")
        else scoped_hostname(requested, domain.zone)
    )
    current = _current(info, domain)
    _charge(domain)
    return _probe(domain, current, name, tool, record_type)


def _probe(domain, current, name, tool, record_type):
    result = ManagedDomainProbe(
        state=State.UNKNOWN,
        perspective="public_dns:1.1.1.1",
        checked_at=timezone.now(),
        reason="DNS_LOOKUP_UNAVAILABLE",
        hostname=name,
        tool=tool,
        record_type=record_type,
        values=[],
    )
    try:
        lookup_type = record_type.value if tool.value in ("LOOKUP", "DIG") else "A"
        values, reason = network.dns_lookup(name, lookup_type, network.RESOLVERS[0], current=current)
        result.values, result.reason = values, reason
        if tool.value in ("LOOKUP", "DIG"):
            result.state = State.OK
        elif values:
            # Reject a mixed public/private answer; never choose the public
            # half of a rebinding response and silently discard the rest.
            addresses = sorted(network.public_address(value) for value in values)
            result.public_address = addresses[0]
            result.perspective = "control_plane_public_ip"
            if tool.value == "HTTPS":
                result.http_status, result.latency_ms = network.https_probe(
                    name, addresses[0], current=current
                )
                result.state, result.tls_verified, result.reason = (
                    State.OK,
                    True,
                    "TLS_VERIFIED_HTTP_STATUS_OBSERVED",
                )
                result.values = []
            else:
                state, result.reason, result.values = network.icmp_probe(
                    tool.value, addresses[0], current=current
                )
                result.state = State(state)
    except Exception:
        current()
        result.state, result.reason, result.values = State.ERROR, "DIAGNOSTIC_PROBE_UNAVAILABLE", []
    current()
    return result
