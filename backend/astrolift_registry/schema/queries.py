"""Read-only queries for the registry app."""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
from collections.abc import Iterable

import strawberry
from django.conf import settings
from django.db import models
from django.db.models import OuterRef, Q, Subquery
from django.db.models.functions import Lower
from django.utils import timezone
from strawberry.types import Info

from astrolift_identity.schema.types import ProjectType, project_to_type
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import AppTeamAccess, Container, RegisteredApp, Workload
from astrolift_registry.schema.types import (
    AppFreshness,
    AppHealthPulseType,
    AppsListSortKey,
    AppTeamAccessType,
    AstroliftAppHealthPulseStatus,
    AstroliftAppListStatusFilter,
    AstroliftAppSourceKindFilter,
    ContainerType,
    RegisteredAppPageType,
    RegisteredAppType,
    RenderedManifestType,
    WorkloadManifestType,
    WorkloadScalingStatus,
    WorkloadType,
    app_team_access_to_type,
    app_to_type,
    build_app_freshness,
    build_autowire_status,
    build_config_drift,
    build_settings_last_modified,
    container_to_type,
    workload_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

# ---------------------------------------------------------------------------
# Apps-list filters + cursor pagination (#481)
# ---------------------------------------------------------------------------
#
# Cursor format: base64-JSON of ``[sort_key, ...sort-specific fields]``.
# The sort key is embedded so a mid-walk sort change restarts from page
# 1 rather than producing a corrupt page.  See ``_encode_apps_cursor``.
#
# Filter rules in one place so ``astrolift_apps``, ``astrolift_my_apps``,
# and their page variants stay aligned. Status filtering relies on the
# same bulk-freshness rollup the rows already pay for when
# ``include_freshness=True``; resolvers force the rollup on whenever
# status is filtered so the cheap path still produces correct results.


_APPS_LIST_PAGE_DEFAULT_LIMIT = 50
_APPS_LIST_PAGE_MAX_LIMIT = 200


def _encode_apps_cursor(sort_by: AppsListSortKey, *values: object) -> str:
    """Encode a sort-keyed cursor.

    Payload: ``[sort_key_value, ...sort-specific fields]``.  The sort
    key is stored so ``_decode_apps_cursor`` can reject tokens whose
    embedded sort doesn't match the current request — stale cross-sort
    cursors restart from page 1 instead of producing a corrupt page.
    """
    payload = json.dumps([sort_by.value, *values], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode()


def _decode_apps_cursor(token: str, sort_by: AppsListSortKey) -> tuple | None:
    """Decode a cursor or return ``None`` on garbage / sort-key mismatch.

    Swallowing bad tokens lets a stale share-link restart from page 1
    instead of erroring.  Sort-key mismatch (client changed ``sort_by``
    mid-walk) also returns ``None`` so the walk restarts cleanly.
    """
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad)
        data = json.loads(raw)
        if not isinstance(data, list) or not data or data[0] != sort_by.value:
            return None
        return tuple(data[1:])
    except (binascii.Error, ValueError, TypeError, json.JSONDecodeError):
        return None


def _apply_apps_list_filters(
    qs,
    *,
    search: str | None,
    team_slug: str | None,
    project_slug: str | None,
    source_kind: AstroliftAppSourceKindFilter | None,
):
    """Apply the search / team / project / source-kind filters in one place.

    Status filtering happens after the freshness rollup so it lives in
    ``_filter_by_status_bucket`` rather than the queryset chain. Each
    filter here narrows the queryset; an unset filter is a no-op.
    """
    if search:
        needle = search.strip()
        if needle:
            qs = qs.filter(
                Q(name__icontains=needle)
                | Q(slug__icontains=needle)
                | Q(description__icontains=needle)
                | Q(source_repo__icontains=needle)
                | Q(source_url__icontains=needle)
            )
    if team_slug:
        qs = qs.filter(team__slug=team_slug)
    if project_slug:
        qs = qs.filter(project__slug=project_slug)
    if source_kind and source_kind is not AstroliftAppSourceKindFilter.ALL:
        qs = qs.filter(source_kind=source_kind.value)
    # Apps and Agents are separate entity-modules (specs 34-36). An app that
    # exists to host an agent (has a kind=agent Workload) belongs in the Agents
    # list — exclude such agent-host apps here so they don't bleed into the
    # Apps list (a regular app has no agent workloads and is unaffected). Both
    # app-list resolvers + the page totalCount route through this helper, so
    # the separation holds uniformly.
    from astrolift_registry.models import Workload

    agent_host_app_ids = Workload.objects.filter(
        kind=Workload.Kind.AGENT,
        deleted_at__isnull=True,
    ).values("registered_app_id")
    qs = qs.exclude(pk__in=agent_host_app_ids)
    return qs


def _coerce_apps_status(
    status: AstroliftAppListStatusFilter | None,
) -> AstroliftAppListStatusFilter:
    """Normalise an unset / ``ALL`` value to ``ALL`` so the rest of the
    pipeline branches on a single sentinel."""
    if status is None:
        return AstroliftAppListStatusFilter.ALL
    return status


def _status_filter_active(status: AstroliftAppListStatusFilter) -> bool:
    return status is not AstroliftAppListStatusFilter.ALL


_STATUS_FILTER_TO_PULSE: dict[AstroliftAppListStatusFilter, AstroliftAppHealthPulseStatus] = {
    AstroliftAppListStatusFilter.OK: AstroliftAppHealthPulseStatus.OK,
    AstroliftAppListStatusFilter.DEGRADED: AstroliftAppHealthPulseStatus.DEGRADED,
    AstroliftAppListStatusFilter.STALE: AstroliftAppHealthPulseStatus.STALE,
    AstroliftAppListStatusFilter.NEVER_DEPLOYED: AstroliftAppHealthPulseStatus.NEVER,
}


def _filter_apps_by_status(
    apps: list[RegisteredApp],
    freshness_by_app: dict[int, AppFreshness],
    status: AstroliftAppListStatusFilter,
) -> list[RegisteredApp]:
    """Keep only apps whose freshness pulse matches ``status``.

    Called after :func:`_freshness_for_apps` has rolled up the per-app
    pulse. ``ALL`` is a no-op pass-through. Unmapped status enum values
    fall through to a no-op rather than emptying the list — defensive
    against a future filter value the resolver doesn't yet wire.
    """
    target = _STATUS_FILTER_TO_PULSE.get(status)
    if target is None:
        return apps
    return [a for a in apps if (freshness_by_app.get(a.pk) or _empty_freshness()).pulse.status is target]


def _empty_freshness() -> AppFreshness:
    """Sentinel freshness for an app that has no rollup row — treated
    as ``never`` so status filtering matches the same bucket the FE
    renders for the "no deploys yet" empty case."""
    return AppFreshness(
        latest_deployment=None,
        last_deployed_at=None,
        pulse=AppHealthPulseType(
            status=AstroliftAppHealthPulseStatus.NEVER,
            age_seconds=None,
            message="no deploys yet",
        ),
    )


def _clamp_page_limit(limit: int) -> int:
    return max(1, min(int(limit or _APPS_LIST_PAGE_DEFAULT_LIMIT), _APPS_LIST_PAGE_MAX_LIMIT))


def _apply_apps_sort(qs, sort_by: AppsListSortKey):
    """Return ``(ordered_qs, annotated_qs)`` with the sort applied.

    ``DEPLOYED_DESC`` annotates each row with its most-recent successful
    deploy timestamp via a correlated subquery, then sorts on that with
    NULLs last.  ``NAME_ASC`` annotates with ``Lower(name)`` for a
    case-insensitive sort.  ``CREATED_DESC`` is the legacy default and
    needs no annotation.
    """
    if sort_by is AppsListSortKey.DEPLOYED_DESC:
        last_deploy_sq = (
            Deployment.objects.filter(
                registered_app=OuterRef("pk"),
                status=Deployment.Status.RUNNING.value,
                deleted_at__isnull=True,
            )
            .order_by("-created_at")
            .values("created_at")[:1]
        )
        qs = qs.annotate(_last_deployed_at=Subquery(last_deploy_sq))
        return qs.order_by(
            models.F("_last_deployed_at").desc(nulls_last=True),
            "-created_at",
        )
    if sort_by is AppsListSortKey.NAME_ASC:
        qs = qs.annotate(_name_lower=Lower("name"))
        return qs.order_by("_name_lower", "guid")
    # CREATED_DESC (default)
    return qs.order_by("-created_at", "-guid")


def _seek_apps(qs, sort_by: AppsListSortKey, cursor_vals: tuple):
    """Apply the seek-key WHERE clause for the given sort mode.

    ``cursor_vals`` is whatever ``_decode_apps_cursor`` returned (the
    payload after stripping the sort-key prefix).
    """
    if sort_by is AppsListSortKey.DEPLOYED_DESC:
        deployed_str, created_str = cursor_vals
        cursor_created = dt.datetime.fromisoformat(created_str)
        if deployed_str:
            cursor_deployed = dt.datetime.fromisoformat(deployed_str)
            # Rows that come *after* this cursor with NULLS LAST ordering:
            # deployed < cursor, OR same deployed + earlier created, OR NULL deployed
            return qs.filter(
                Q(_last_deployed_at__lt=cursor_deployed)
                | (Q(_last_deployed_at=cursor_deployed) & Q(created_at__lt=cursor_created))
                | Q(_last_deployed_at__isnull=True)
            )
        else:
            # Cursor is in the NULL-deployed tail — only rows with NULL deployed
            # that were created before the cursor created_at
            return qs.filter(Q(_last_deployed_at__isnull=True) & Q(created_at__lt=cursor_created))

    if sort_by is AppsListSortKey.NAME_ASC:
        name_lower, guid_str = cursor_vals
        return qs.filter(Q(_name_lower__gt=name_lower) | (Q(_name_lower=name_lower) & Q(guid__gt=guid_str)))

    # CREATED_DESC
    created_str, guid_str = cursor_vals
    cursor_at = dt.datetime.fromisoformat(created_str)
    return qs.filter(Q(created_at__lt=cursor_at) | (Q(created_at=cursor_at) & Q(guid__lt=guid_str)))


def _cursor_for_row(sort_by: AppsListSortKey, row: RegisteredApp) -> str:
    """Emit the next-cursor token for a given row + sort mode."""
    if sort_by is AppsListSortKey.DEPLOYED_DESC:
        deployed = getattr(row, "_last_deployed_at", None)
        deployed_str = deployed.isoformat() if deployed is not None else ""
        return _encode_apps_cursor(sort_by, deployed_str, row.created_at.isoformat())
    if sort_by is AppsListSortKey.NAME_ASC:
        name_lower = getattr(row, "_name_lower", row.name.lower())
        return _encode_apps_cursor(sort_by, name_lower, str(row.guid))
    # CREATED_DESC
    return _encode_apps_cursor(sort_by, row.created_at.isoformat(), str(row.guid))


def _paginate_apps(
    qs,
    *,
    cursor: str | None,
    limit: int,
    sort_by: AppsListSortKey = AppsListSortKey.CREATED_DESC,
) -> tuple[list[RegisteredApp], str | None, int]:
    """Materialise a (page, next_cursor, total_count) triple from ``qs``.

    Supports three sort modes via ``sort_by`` (#729).  The cursor encodes
    the active sort key so a mid-walk sort change silently restarts from
    page 1 rather than producing a corrupt page.  ``total_count`` is the
    filtered total so the FE can render "N of M" without a second query.
    """
    page_size = _clamp_page_limit(limit)
    ordered = _apply_apps_sort(qs, sort_by)
    total_count = ordered.count()
    if cursor:
        decoded = _decode_apps_cursor(cursor, sort_by)
        if decoded is not None:
            ordered = _seek_apps(ordered, sort_by, decoded)
    # Fetch one extra to detect end-of-stream cheaply.
    rows = list(ordered[: page_size + 1])
    items = rows[:page_size]
    next_cursor = _cursor_for_row(sort_by, items[-1]) if len(rows) > page_size and items else None
    return items, next_cursor, total_count


def _build_apps_page(
    qs,
    *,
    cursor: str | None,
    limit: int,
    include_freshness: bool,
    status: AstroliftAppListStatusFilter,
    sort_by: AppsListSortKey = AppsListSortKey.CREATED_DESC,
) -> RegisteredAppPageType:
    """Materialise a :class:`RegisteredAppPageType` from a filtered queryset.

    Encapsulates the cursor + freshness + status-filter compose so the
    two page resolvers (``astrolift_apps_page`` /
    ``astrolift_my_apps_page``) share the post-DB pipeline. When
    ``status`` is set, the resolver may scan up to ``limit * 4`` rows
    to find ``limit`` matches; callers paginate to walk the rest.

    Total-count is the *filter-aware* count of the queryset BEFORE the
    cursor narrow but AFTER all DB-side filters. For status-filtered
    pages we can only approximate the total without re-rolling
    freshness for the entire queryset, so we report the filtered
    queryset's total minus an estimate — simpler to surface the DB
    total and let the FE understand that the user-facing count may
    differ slightly from the on-screen total when status is applied.
    The "N of M" caption stays useful (it's the unfiltered total
    after every DB-side filter).
    """
    page_size = _clamp_page_limit(limit)
    effective_freshness = include_freshness or _status_filter_active(status)

    # ``app_to_type`` reads ``approver_users.values_list("pk")`` per row —
    # a per-row M2M query that explodes the count on a list query. Prefetch
    # it once for the page so the cost stays bounded.
    qs = qs.prefetch_related("approver_users")
    # Fold the managed-domain inputs into the row fetch so the per-row
    # hostname compute (``_compute_managed_hostname``) doesn't N+1 (#1043).
    qs = _annotate_managed_domain(qs)

    if not _status_filter_active(status):
        items, next_cursor, total_count = _paginate_apps(qs, cursor=cursor, limit=page_size, sort_by=sort_by)
        freshness_by_app = _freshness_for_apps(items) if effective_freshness else {}
        preview_counts = _active_preview_counts(items)
        managed_hostnames = _managed_hostnames_for_apps(items)
        return RegisteredAppPageType(
            items=[
                app_to_type(
                    a,
                    freshness=freshness_by_app.get(a.pk) if include_freshness else None,
                    active_preview_count=preview_counts.get(a.pk, 0),
                    managed_hostname=managed_hostnames.get(a.pk, ""),
                )
                for a in items
            ],
            next_cursor=next_cursor,
            total_count=total_count,
        )

    # Status-filtered path: walk the queryset under the cursor,
    # roll freshness in batches, keep matches until we have a full
    # page (or hit the scan cap).
    ordered = _apply_apps_sort(qs, sort_by)
    total_count = ordered.count()
    if cursor:
        decoded = _decode_apps_cursor(cursor, sort_by)
        if decoded is not None:
            ordered = _seek_apps(ordered, sort_by, decoded)

    scan_cap = max(page_size * 4, page_size + 1)
    scanned = list(ordered[:scan_cap])
    freshness_by_app = _freshness_for_apps(scanned)
    kept = _filter_apps_by_status(scanned, freshness_by_app, status)
    items = kept[:page_size]
    has_more_in_scan = len(kept) > page_size
    has_more_unscanned = len(scanned) == scan_cap and not has_more_in_scan
    if items and (has_more_in_scan or has_more_unscanned):
        anchor = items[-1] if has_more_in_scan else scanned[-1]
        next_cursor: str | None = _cursor_for_row(sort_by, anchor)
    else:
        next_cursor = None
    preview_counts = _active_preview_counts(items)
    managed_hostnames = _managed_hostnames_for_apps(items)
    return RegisteredAppPageType(
        items=[
            app_to_type(
                a,
                freshness=freshness_by_app.get(a.pk) if include_freshness else None,
                active_preview_count=preview_counts.get(a.pk, 0),
                managed_hostname=managed_hostnames.get(a.pk, ""),
            )
            for a in items
        ],
        next_cursor=next_cursor,
        total_count=total_count,
    )


def _active_preview_counts(apps: Iterable[RegisteredApp]) -> dict[int, int]:
    """Bulk-count active preview environments per app (#730).

    One aggregate query per page (vs N FK fetches if the resolver
    inlined the count per row). ``app_to_type`` reads this map via the
    ``active_preview_count=`` kwarg; missing keys default to 0 (an app
    with zero previews simply doesn't appear in the aggregate's group
    output).
    """
    from astrolift_lifecycle.models import PreviewEnvironment

    app_ids = [a.pk for a in apps]
    if not app_ids:
        return {}
    rows = (
        PreviewEnvironment.objects.filter(
            registered_app_id__in=app_ids,
            torn_down_at__isnull=True,
            deleted_at__isnull=True,
        )
        .values("registered_app_id")
        .annotate(count=models.Count("pk"))
    )
    return {row["registered_app_id"]: int(row["count"]) for row in rows}


def _annotate_managed_domain(qs):
    """Fold both managed-domain resolution inputs into the row fetch (#1043).

    ``_compute_managed_hostname`` calls ``resolve_managed_domain`` per
    row: it reads ``organization.default_managed_domain`` (an FK) and,
    when that's unset, falls back to an unconditional platform-level
    (``organization IS NULL``) ``ManagedDomain`` lookup. On a list that
    fallback fires once *per row* — the N+1 this ticket fixes.

    Both inputs depend only on the app's organization, so we load them
    with the page SELECT instead of per row: ``select_related`` pulls the
    org-default FK into the existing join, and an uncorrelated
    ``Subquery`` annotates the platform-level zone (a single global row)
    onto each row. Neither adds a query — the cost stays O(1) regardless
    of row count. ``_managed_hostnames_for_apps`` then reads both off the
    materialised rows with no further DB hits.
    """
    from astrolift_clusters.models import ManagedDomain

    platform_zone_sq = (
        ManagedDomain.objects.filter(
            organization__isnull=True,
            default_for__in=[ManagedDomain.DefaultFor.TENANT_APPS, ManagedDomain.DefaultFor.BOTH],
            deleted_at__isnull=True,
        )
        .order_by("pk")
        .values("zone")[:1]
    )
    return qs.select_related("organization__default_managed_domain").annotate(
        _platform_zone=Subquery(platform_zone_sq)
    )


def _managed_hostnames_for_apps(apps: Iterable[RegisteredApp]) -> dict[int, str]:
    """Map each app to its platform-managed hostname (#1043).

    Reads the managed-domain inputs that :func:`_annotate_managed_domain`
    pre-loaded onto the rows — the ``organization.default_managed_domain``
    FK (select_related cache) and the ``_platform_zone`` annotation —
    so it issues no queries of its own. Resolution mirrors
    ``resolve_managed_domain``: a non-deleted org-default wins, else the
    platform-level zone. ``app_to_type`` reads this map via the
    ``managed_hostname=`` kwarg.
    """
    app_list = list(apps)
    if not app_list:
        return {}

    out: dict[int, str] = {}
    for app in app_list:
        org = app.organization if app.organization_id else None
        domain = getattr(org, "default_managed_domain", None) if org is not None else None
        if domain is not None and getattr(domain, "deleted_at", None) is not None:
            domain = None
        zone = domain.zone if domain is not None else getattr(app, "_platform_zone", None)
        if not zone:
            out[app.pk] = ""
            continue
        subdomain = (app.subdomain or app.slug or "").strip()
        out[app.pk] = f"{subdomain}.{zone}" if subdomain else ""
    return out


def _viewer_permissions_for_apps(apps: Iterable[RegisteredApp]) -> dict[int, set[str]]:
    """Bulk per-app viewer permissions for the list resolvers (#478).

    One RoleBinding query for the whole page via
    :func:`astrolift_identity.permission_resolver.resolve_effective_permissions_for_apps`;
    ``app_to_type`` reads the map via the ``viewer_permissions=`` kwarg.
    Empty when there is no acting user (e.g. system callers).
    """
    from astrolift_identity.permission_resolver import resolve_effective_permissions_for_apps

    tenant = get_current_tenant()
    if tenant is None or tenant.actor_user_id is None:
        return {}
    return resolve_effective_permissions_for_apps(tenant, apps)


def _viewer_scope_filter() -> Q | None:
    """Return the ``Q`` that limits a queryset to apps the viewer's
    RoleBindings reach (#312). ``None`` when no scope applies — caller
    short-circuits to an empty list.

    Mirrors the existing inline logic in ``astrolift_my_apps`` so the
    page variant doesn't fork the rule. Superusers get ``Q()`` (an
    unconstrained pass-through); anonymous callers get ``None``.
    """
    from django.contrib.auth import get_user_model

    from astrolift_identity.models import RoleBinding

    tenant = get_current_tenant()
    if tenant is None or tenant.actor_user_id is None:
        return None

    User = get_user_model()
    viewer = User.objects.filter(pk=tenant.actor_user_id).first()
    if viewer is None:
        return None

    if getattr(viewer, "is_superuser", False) and getattr(viewer, "is_active", True):
        return Q()

    now = timezone.now()
    bindings = list(
        RoleBinding.objects.filter(
            user_id=tenant.actor_user_id,
            deleted_at__isnull=True,
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    )
    if not bindings:
        return None

    org_ids: set[int] = set()
    team_ids: set[int] = set()
    project_ids: set[int] = set()
    app_ids: set[int] = set()
    for b in bindings:
        if b.scope_kind == RoleBinding.ScopeKind.ORG:
            org_ids.add(b.scope_id)
        elif b.scope_kind == RoleBinding.ScopeKind.TEAM:
            team_ids.add(b.scope_id)
        elif b.scope_kind == RoleBinding.ScopeKind.PROJECT:
            project_ids.add(b.scope_id)
        elif b.scope_kind == RoleBinding.ScopeKind.APP:
            app_ids.add(b.scope_id)

    scope_filter = Q()
    if org_ids:
        scope_filter |= Q(organization_id__in=org_ids)
    if team_ids:
        scope_filter |= Q(team_id__in=team_ids)
    if project_ids:
        scope_filter |= Q(project_id__in=project_ids)
    if app_ids:
        scope_filter |= Q(pk__in=app_ids)

    if not scope_filter.children:
        return None
    return scope_filter


def _scaling_environment_for_workload(workload, environment_name: str | None):
    """Pick the env to drive the scaling policy off of (#430).

    Mirrors the same resolution rules ``k8s_ops._primary_environment_for_workload``
    uses — the first active env on the app, or the env named
    explicitly. Returns ``None`` when nothing matches so
    ``resolve_replica_bounds`` falls back to the platform default.
    """
    qs = AppEnvironment.objects.filter(
        registered_app=workload.registered_app,
        deleted_at__isnull=True,
    ).order_by("id")
    if environment_name:
        qs = qs.filter(name=environment_name)
    return qs.first()


def _read_live_replicas(*, workload, environment_name: str | None) -> tuple[int, int]:
    """Best-effort live (current, desired) read from the cluster.

    Calls into ``ClusterDriver.get_workload_status`` on the Deployment
    backing ``workload`` and returns the ``(ready_replicas,
    desired_replicas)`` tuple. Raises on any error — the caller
    swallows so an unreachable cluster degrades to the manifest-side
    replica count, not a 500.
    """
    from core.app_deploy import namespace_for_app
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    env = _scaling_environment_for_workload(workload, environment_name)
    if env is None or env.tenant_cluster_id is None:
        raise RuntimeError("no env / cluster")
    cluster = env.tenant_cluster
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    namespace = namespace_for_app(workload.registered_app)
    get_status = getattr(driver, "get_workload_status", None)
    if not callable(get_status):
        raise RuntimeError("driver lacks get_workload_status")
    status = get_status(ctx.slug, namespace, "Deployment", workload.slug)
    return int(status.ready_replicas or 0), int(status.desired_replicas or 0)


def _filter_resources_for_workload(resources: list[dict], workload_slug: str) -> list[dict]:
    """Filter a rendered manifest down to one workload's resources (#430).

    The renderer tags every workload resource with
    ``metadata.labels['astrolift.dev/workload']``. Anything missing
    that label (cluster-scoped resources, app-wide ConfigMaps) is
    excluded — those don't belong on the per-workload page.
    """
    out: list[dict] = []
    for resource in resources:
        metadata = resource.get("metadata") or {}
        labels = metadata.get("labels") or {}
        if labels.get("astrolift.dev/workload") == workload_slug:
            out.append(resource)
    return out


def _previous_deployment_for(*, app, environment, current_image_tag: str):
    """Find the most recent deployment to render a diff against (#430).

    Strategy:
      - Restrict to the same (app, env) pair.
      - Only ``RUNNING``, ``SUPERSEDED``, or ``ROLLED_BACK`` count — a
        deploy that never reached running can't serve as a baseline.
      - Skip rows whose ``image_tag`` matches the current image tag —
        diff-against-self is noise, not signal.

    Returns the Deployment row or ``None`` when no candidate exists.
    """
    qs = Deployment.objects.filter(
        registered_app=app,
        deleted_at__isnull=True,
        status__in=[
            Deployment.Status.RUNNING.value,
            Deployment.Status.SUPERSEDED.value,
            Deployment.Status.ROLLED_BACK.value,
        ],
    ).order_by("-created_at")
    if environment is not None:
        qs = qs.filter(app_environment=environment)
    if current_image_tag:
        qs = qs.exclude(image_tag=current_image_tag)
    return qs.first()


def _freshness_for_apps(apps: Iterable[RegisteredApp]) -> dict[int, AppFreshness]:
    """Bulk-build the per-app freshness payload for a list of apps (#405).

    Avoids the N+1 the per-row resolver would otherwise spawn: two
    queries total regardless of list size — one for the most-recent
    ``Deployment`` per app, one for the most-recent ``running``
    deploy per app. Returns a dict keyed by ``RegisteredApp.pk`` so
    callers can map back without re-fetching.

    Apps with no deployment rows still appear in the result — the
    builder emits a ``never`` pulse for those.
    """
    app_list = list(apps)
    if not app_list:
        return {}

    app_ids = [a.pk for a in app_list]
    now = timezone.now()

    base = (
        Deployment.objects.filter(
            registered_app_id__in=app_ids,
            deleted_at__isnull=True,
        )
        .select_related("triggered_by_user", "app_environment")
        .order_by("registered_app_id", "-created_at")
    )

    # Two passes — one for the most recent row per app, one for the
    # most recent ``running`` row per app. Each pass scans the
    # ordered queryset and keeps the first hit per ``registered_app_id``.
    latest_by_app: dict[int, Deployment] = {}
    for d in base:
        if d.registered_app_id not in latest_by_app:
            latest_by_app[d.registered_app_id] = d

    success_dt_by_app: dict[int, dt.datetime] = {}
    for d in base.filter(status=Deployment.Status.RUNNING.value):
        if d.registered_app_id not in success_dt_by_app:
            success_dt_by_app[d.registered_app_id] = d.created_at

    freshness_by_app: dict[int, AppFreshness] = {}
    for app in app_list:
        freshness_by_app[app.pk] = build_app_freshness(
            latest_deployment=latest_by_app.get(app.pk),
            last_success_at=success_dt_by_app.get(app.pk),
            now=now,
        )
    return freshness_by_app


@strawberry.type(name="AstroliftDiscoveredAppManifest")
class DiscoveredAppManifestType:
    """One app manifest found by scanning a repo (#979).

    Backs the monorepo / multi-service discovery step of the app onboarding
    wizard: the operator points at a repo, the scan walks
    ``apps/*/astrolift.toml`` + a root ``astrolift.toml``, and each app
    manifest comes back as one of these preview rows WITHOUT anything being
    persisted. The operator then confirms registration via ``registerAppRepo``.

    ``manifest_path`` is the repo-relative path (the value that becomes
    ``RegisteredApp.manifest_path`` and the key registration dedupes on).
    ``build_context`` is the per-service subdir the app builds from
    (``apps/<slug>`` for a monorepo service, ``"."`` for a root manifest).
    ``name`` is the manifest's top-level name; ``workload_count`` is how many
    workloads it declares. ``already_registered`` is True when an app for that
    repo + manifest path already exists.
    """

    manifest_path: str
    name: str
    build_context: str
    workload_count: int
    already_registered: bool


@strawberry.type(name="AstroliftScanAppManifestsResult")
class ScanAppManifestsResultType:
    """Outcome of a repo app-manifest scan (#979).

    ``ok`` is True when the scan ran (``apps`` may still be empty when the repo
    has no app manifests); False when the repo couldn't be fetched (no source
    connection / SCM error), in which case ``error`` carries the operator-facing
    message and ``apps`` is empty.
    """

    ok: bool
    apps: list[DiscoveredAppManifestType]
    error: str | None = None


def _caller_org_id() -> int | None:
    """Current tenant's organization id, or None when there's no tenant
    context. Read resolvers over org-owned rows MUST treat None as
    deny-by-default ("no rows" / not-found), never as "all rows" (#1042)."""
    tenant = get_current_tenant()
    return tenant.organization_id if tenant is not None else None


@strawberry.type
class RegistryQuery:
    @strawberry.field
    def astrolift_platform_api_url(self, info: Info) -> str:
        """Public base URL the platform's REST API answers at.

        Surfaced to the Settings page CI-setup section (#382) so the
        operator can paste it verbatim into ``ASTROLIFT_API_URL`` on
        their CI side. Platform-level value — not tenant-scoped — but
        still gated to authenticated callers so we don't leak the
        install's API origin to anonymous probes.
        """
        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not getattr(viewer, "is_authenticated", False):
            raise PermissionError("astrolift_platform_api_url requires an authenticated viewer")
        return (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_apps(
        self,
        info: Info,
        include_freshness: bool = False,
        search: str | None = None,
        team_slug: str | None = None,
        project_slug: str | None = None,
        status: AstroliftAppListStatusFilter | None = None,
        source_kind: AstroliftAppSourceKindFilter | None = None,
    ) -> list[RegisteredAppType]:
        """Org-scoped list of registered apps.

        ``include_freshness`` (default False, #405) opts the row into
        the deployment-freshness rollup — ``latestDeployment``,
        ``lastDeployedAt``, and ``healthPulse``. Off by default so
        callers that only need the cheap registry fields don't pay
        the two-query freshness join.

        Filter args (#481) narrow the result server-side:

        * ``search`` — case-insensitive contains across
          ``name`` / ``slug`` / ``description`` / ``source_repo`` /
          ``source_url``. Empty / whitespace-only strings are no-ops.
        * ``team_slug`` / ``project_slug`` — exact match on the
          owning team or project slug.
        * ``status`` — health-pulse bucket (``OK`` / ``DEGRADED`` /
          ``STALE`` / ``NEVER_DEPLOYED``). When set, the freshness
          rollup is forced on regardless of ``include_freshness`` so
          the filter has the data it needs.
        * ``source_kind`` — exact match on the source-host kind.

        This resolver keeps the flat-list return shape for back-compat
        with the ``LIST_APPS`` query (#405). New callers should use
        ``astroliftAppsPage`` for cursor pagination + ``totalCount``.

        Archived apps (#743) are hidden from this list. The standard
        list query has no opt-in for surfacing them; ops UIs that need
        to see archived rows should call ``astroliftAppsPage`` with
        ``includeArchived: true``.
        """
        status = _coerce_apps_status(status)
        org_id = _caller_org_id()
        if org_id is None:
            return []
        qs = RegisteredApp.objects.select_related("organization", "team", "project").filter(
            organization_id=org_id,
            deleted_at__isnull=True,
            archived_at__isnull=True,
        )
        qs = _apply_apps_list_filters(
            qs,
            search=search,
            team_slug=team_slug,
            project_slug=project_slug,
            source_kind=source_kind,
        )
        # Status-filtered queries always need the freshness rollup to
        # decide membership; force it on so the contract holds for
        # callers that didn't think to flip the flag.
        effective_freshness = include_freshness or _status_filter_active(status)

        # Soft cap kept at 200 to mirror the legacy shape — the page
        # variant is the right surface when callers need more than that.
        apps = list(_annotate_managed_domain(qs).order_by("-created_at")[:200])
        freshness_by_app = _freshness_for_apps(apps) if effective_freshness else {}
        if _status_filter_active(status):
            apps = _filter_apps_by_status(apps, freshness_by_app, status)
        preview_counts = _active_preview_counts(apps)
        managed_hostnames = _managed_hostnames_for_apps(apps)
        viewer_perms = _viewer_permissions_for_apps(apps)
        if not include_freshness:
            return [
                app_to_type(
                    a,
                    active_preview_count=preview_counts.get(a.pk, 0),
                    managed_hostname=managed_hostnames.get(a.pk, ""),
                    viewer_permissions=viewer_perms.get(a.pk),
                )
                for a in apps
            ]
        return [
            app_to_type(
                a,
                freshness=freshness_by_app.get(a.pk),
                active_preview_count=preview_counts.get(a.pk, 0),
                managed_hostname=managed_hostnames.get(a.pk, ""),
                viewer_permissions=viewer_perms.get(a.pk),
            )
            for a in apps
        ]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_apps_page(
        self,
        info: Info,
        include_freshness: bool = False,
        search: str | None = None,
        team_slug: str | None = None,
        project_slug: str | None = None,
        status: AstroliftAppListStatusFilter | None = None,
        source_kind: AstroliftAppSourceKindFilter | None = None,
        sort_by: AppsListSortKey = AppsListSortKey.CREATED_DESC,
        cursor: str | None = None,
        limit: int = _APPS_LIST_PAGE_DEFAULT_LIMIT,
        include_archived: bool = False,
    ) -> RegisteredAppPageType:
        """Cursor-paginated org-scoped apps list (#481, #729).

        Same filter axes as :func:`astrolift_apps` plus cursor +
        limit. Returns a ``next_cursor`` of null when the caller has
        reached the end of the result. ``total_count`` is the
        filtered total so the FE can render "N of M" without a second
        aggregate query.

        ``sort_by`` controls the ordering and seek-key shape (#729):
        ``CREATED_DESC`` (default), ``DEPLOYED_DESC`` (most-recently
        deployed first, NULLs last), or ``NAME_ASC`` (case-insensitive
        alphabetical).  The cursor encodes the active sort key so a
        mid-walk sort change restarts from page 1 rather than producing
        a corrupt page.

        Status filtering forces the freshness rollup on regardless of
        ``include_freshness`` — without the rollup the resolver has no
        signal to filter on.

        When ``status`` is set the page is built by:
          1. Scan the queryset under the cursor.
          2. Roll up freshness for the scanned rows.
          3. Drop rows whose pulse doesn't match.
          4. Re-issue the cursor against the last *kept* row.

        Because the status filter is post-DB, the cursor may need to
        scan past the requested ``limit`` to find ``limit`` matching
        rows. We cap the post-filter scan at ``limit * 4`` to keep
        the worst-case page latency bounded; callers that keep
        paginating will still receive every match across multiple
        pages.

        ``include_archived`` (default False, #743) opts the page into
        showing archived apps. The standard list view hides them so
        operators don't see scaled-to-zero rows mixed in; the future
        "Archived apps" admin page flips this flag on.
        """
        status = _coerce_apps_status(status)
        org_id = _caller_org_id()
        qs = RegisteredApp.objects.select_related("organization", "team", "project").filter(
            deleted_at__isnull=True,
        )
        # Deny-by-default: no tenant context → no rows (#1042).
        qs = qs.filter(organization_id=org_id) if org_id is not None else qs.none()
        if not include_archived:
            qs = qs.filter(archived_at__isnull=True)
        qs = _apply_apps_list_filters(
            qs,
            search=search,
            team_slug=team_slug,
            project_slug=project_slug,
            source_kind=source_kind,
        )
        return _build_apps_page(
            qs,
            cursor=cursor,
            limit=limit,
            include_freshness=include_freshness,
            status=status,
            sort_by=sort_by,
        )

    @strawberry.field
    @tenant_scoped()
    def astrolift_my_apps(
        self,
        info: Info,
        include_freshness: bool = False,
        search: str | None = None,
        team_slug: str | None = None,
        project_slug: str | None = None,
        status: AstroliftAppListStatusFilter | None = None,
        source_kind: AstroliftAppSourceKindFilter | None = None,
    ) -> list[RegisteredAppType]:
        """Apps the viewer can reach by any RoleBinding on the app or
        an ancestor (project / team / org).

        Self-service surface — no ``@require_permission``: the viewer's
        own bindings are what gate visibility. Returns an empty list
        when there's no authenticated actor. Listed in the tenancy
        guardrail EXEMPT set with this rationale.

        Implementation: collect every active RoleBinding for the
        caller, project the (scope_kind, scope_id) tuples, then
        union-resolve to the set of app ids that are visible. An
        ORG-scoped binding grants visibility to every app in the org;
        TEAM/PROJECT-scoped grants visibility to every app under that
        sub-tree; APP-scoped grants visibility to just that app.
        Superusers see every app in their active tenant (matches the
        permission resolver's superuser short-circuit).

        Filter args (#481) mirror :func:`astrolift_apps` — see that
        docstring for the per-axis behaviour. Filters compose with
        the viewer's scope: a search needle still only walks rows the
        viewer can see.

        Archived apps (#743) are hidden — same rationale as
        :func:`astrolift_apps`. The page variant exposes an
        ``includeArchived`` opt-in for ops; this flat list does not.
        """
        status = _coerce_apps_status(status)
        scope_filter = _viewer_scope_filter()
        if scope_filter is None:
            return []

        tenant = get_current_tenant()
        base_qs = RegisteredApp.objects.select_related("organization", "team", "project").filter(
            deleted_at__isnull=True,
            archived_at__isnull=True,
        )
        if tenant is not None and tenant.organization_id is not None:
            base_qs = base_qs.filter(organization_id=tenant.organization_id)

        qs = base_qs.filter(scope_filter)
        qs = _apply_apps_list_filters(
            qs,
            search=search,
            team_slug=team_slug,
            project_slug=project_slug,
            source_kind=source_kind,
        )
        effective_freshness = include_freshness or _status_filter_active(status)

        # Legacy ordering preserved (``slug``) so existing callers see
        # the same row order they did before #481. The page variant
        # uses the createdAt seek key for cursor stability.
        scoped_apps = list(_annotate_managed_domain(qs).order_by("slug")[:200])
        freshness_by_app = _freshness_for_apps(scoped_apps) if effective_freshness else {}
        if _status_filter_active(status):
            scoped_apps = _filter_apps_by_status(scoped_apps, freshness_by_app, status)
        preview_counts = _active_preview_counts(scoped_apps)
        managed_hostnames = _managed_hostnames_for_apps(scoped_apps)
        viewer_perms = _viewer_permissions_for_apps(scoped_apps)
        if not include_freshness:
            return [
                app_to_type(
                    a,
                    active_preview_count=preview_counts.get(a.pk, 0),
                    managed_hostname=managed_hostnames.get(a.pk, ""),
                    viewer_permissions=viewer_perms.get(a.pk),
                )
                for a in scoped_apps
            ]
        return [
            app_to_type(
                a,
                freshness=freshness_by_app.get(a.pk),
                active_preview_count=preview_counts.get(a.pk, 0),
                managed_hostname=managed_hostnames.get(a.pk, ""),
                viewer_permissions=viewer_perms.get(a.pk),
            )
            for a in scoped_apps
        ]

    @strawberry.field
    @tenant_scoped()
    def astrolift_my_apps_page(
        self,
        info: Info,
        include_freshness: bool = False,
        search: str | None = None,
        team_slug: str | None = None,
        project_slug: str | None = None,
        status: AstroliftAppListStatusFilter | None = None,
        source_kind: AstroliftAppSourceKindFilter | None = None,
        sort_by: AppsListSortKey = AppsListSortKey.CREATED_DESC,
        cursor: str | None = None,
        limit: int = _APPS_LIST_PAGE_DEFAULT_LIMIT,
        include_archived: bool = False,
    ) -> RegisteredAppPageType:
        """Cursor-paginated viewer-scoped apps list (#481, #729).

        Same shape as :func:`astrolift_apps_page` but the queryset is
        first narrowed to apps the viewer's RoleBindings reach. EXEMPT
        from the tenancy guardrail for the same reason
        :func:`astrolift_my_apps` is — the viewer's bindings ARE the
        gate.

        ``include_archived`` (default False, #743) opts the page into
        showing archived apps the viewer can reach. Same opt-in shape
        as :func:`astrolift_apps_page`.
        """
        status = _coerce_apps_status(status)
        scope_filter = _viewer_scope_filter()
        if scope_filter is None:
            return RegisteredAppPageType(items=[], next_cursor=None, total_count=0)

        tenant = get_current_tenant()
        base_qs = RegisteredApp.objects.select_related("organization", "team", "project").filter(
            deleted_at__isnull=True
        )
        if not include_archived:
            base_qs = base_qs.filter(archived_at__isnull=True)
        if tenant is not None and tenant.organization_id is not None:
            base_qs = base_qs.filter(organization_id=tenant.organization_id)

        qs = base_qs.filter(scope_filter)
        qs = _apply_apps_list_filters(
            qs,
            search=search,
            team_slug=team_slug,
            project_slug=project_slug,
            source_kind=source_kind,
        )
        return _build_apps_page(
            qs,
            cursor=cursor,
            limit=limit,
            include_freshness=include_freshness,
            status=status,
            sort_by=sort_by,
        )

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app(
        self,
        info: Info,
        slug: str,
        include_drift: bool = False,
    ) -> RegisteredAppType | None:
        """Single app by slug.

        ``include_drift`` (default False, #407 C) opts the row into
        the config-drift rollup — ``configDrift`` is left null when
        False so the cheap header query stays cheap. The app overview
        passes True; sidebar / breadcrumb queries pass False.
        """
        org_id = _caller_org_id()
        app = (
            RegisteredApp.objects.select_related("organization", "team", "project")
            .filter(slug=slug, organization_id=org_id)
            .first()
        )
        if app is None:
            return None
        drift = build_config_drift(app) if include_drift else None
        # Per-section "Modified N ago" timestamps power the Settings
        # landing card grid (#454). Computed only on the detail path —
        # the list resolvers leave the wrapper None.
        settings_last_modified = build_settings_last_modified(app)
        # Autowire completeness rollup (#1108) — detail-only, DB-cheap.
        autowire = build_autowire_status(app)
        return app_to_type(
            app,
            drift=drift,
            autowire=autowire,
            settings_last_modified=settings_last_modified,
            include_retention_policies=True,
        )

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app_team_accesses(self, info: Info, app_slug: str) -> list[AppTeamAccessType]:
        """List every team that holds active access to ``app_slug``.

        Includes the home-team row (``is_home=true``) plus any
        additional teams granted via ``grantTeamAccessToApp``.
        Backfilled deployments will show exactly one row (the home
        team at ``OWNER``) until the operator grants more teams.
        """

        org_id = _caller_org_id()
        app = (
            RegisteredApp.objects.select_related("team")
            .filter(slug=app_slug, organization_id=org_id, deleted_at__isnull=True)
            .first()
        )
        if app is None:
            return []
        rows = (
            AppTeamAccess.objects.select_related("registered_app", "team")
            .filter(registered_app=app, deleted_at__isnull=True)
            .order_by("team__slug")
        )
        return [app_team_access_to_type(r, home_team_id=app.team_id) for r in rows]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_workloads(self, info: Info, app_slug: str | None = None) -> list[WorkloadType]:
        org_id = _caller_org_id()
        if org_id is None:
            return []
        qs = Workload.objects.select_related("registered_app").filter(registered_app__organization_id=org_id)
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        return [workload_to_type(w) for w in qs[:200]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_workload(self, info: Info, app_slug: str, slug: str) -> WorkloadType | None:
        """Single workload by (app_slug, slug).

        Workloads are scoped under the registered app — the same
        workload slug can recur across orgs without colliding because
        the app+slug compound is unique within tenant.
        """
        org_id = _caller_org_id()
        w = (
            Workload.objects.select_related("registered_app")
            .filter(registered_app__slug=app_slug, registered_app__organization_id=org_id, slug=slug)
            .first()
        )
        return workload_to_type(w) if w else None

    # ----------------------------------------------------------------
    # Live workload scaling status (#430)
    # ----------------------------------------------------------------
    #
    # Combines the manifest-side HPA configuration (Workload row) with
    # the live ``WorkloadStatus`` read from the cluster driver. Powers
    # the scaling card on the workload-detail page — the slider, the
    # HPA gauge, and the "Scaling…" indicator.
    #
    # Live read degrades to manifest-only when the driver doesn't
    # surface ``get_workload_status`` (older provider plugins) or the
    # cluster is unreachable; the resolver never raises — empty-state
    # rendering happens client-side off the same single round-trip.

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_workload_scaling_status(
        self,
        info: Info,
        app_slug: str,
        workload_slug: str,
        environment_name: str | None = None,
    ) -> WorkloadScalingStatus | None:
        """Live + configured scaling status for one workload.

        Returns ``None`` when the workload doesn't exist for the
        current tenant. Returns a populated value with zero live
        replica counts when the cluster is unreachable / unwired so
        the FE can still render the slider (the manual-scale mutation
        will fail loudly if the driver can't reach the cluster, which
        is the right place to surface that error).
        """
        from astrolift_lifecycle.services.k8s_ops import resolve_replica_bounds

        org_id = _caller_org_id()
        workload = (
            Workload.objects.filter(
                registered_app__slug=app_slug,
                registered_app__organization_id=org_id,
                slug=workload_slug,
                deleted_at__isnull=True,
            )
            .select_related("registered_app")
            .first()
        )
        if workload is None:
            return None

        # Manifest-side HPA fields — always present.
        hpa_min = workload.hpa_min_replicas
        hpa_max = workload.hpa_max_replicas
        hpa_target = workload.hpa_target_cpu_pct
        # The renderer only emits an HPA when both min + max are set
        # (and positive); mirror that here so the FE doesn't render a
        # gauge for an HPA the cluster doesn't actually have.
        hpa_enabled = bool(hpa_min and hpa_max and hpa_min > 0 and hpa_max > 0)

        # Live read — best effort. We honour the same cluster
        # resolution rules as the pod-list query so an environment
        # override picks the same target.
        live_current = workload.replicas
        live_desired = workload.replicas
        try:
            live_current, live_desired = _read_live_replicas(
                workload=workload,
                environment_name=environment_name,
            )
        except Exception:  # noqa: BLE001 — cluster-side failures are non-fatal
            pass

        # Replica bounds — share the same policy the scale mutation
        # uses so the slider can't propose an out-of-range value.
        env = _scaling_environment_for_workload(workload, environment_name)
        lower, upper = resolve_replica_bounds(env)
        # When HPA is enabled, the slider caps at the HPA max so manual
        # scales don't immediately get reconciled away.
        if hpa_enabled and hpa_max is not None:
            upper = min(upper, hpa_max)

        return WorkloadScalingStatus(
            hpa_enabled=hpa_enabled,
            hpa_min_replicas=hpa_min,
            hpa_max_replicas=hpa_max,
            hpa_target_cpu_pct=hpa_target,
            current_replicas=live_current,
            desired_replicas=live_desired,
            is_scaling=live_current != live_desired,
            replica_lower_bound=lower,
            replica_upper_bound=upper,
            sourced_at=timezone.now(),
        )

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_containers(self, info: Info, workload_slug: str | None = None) -> list[ContainerType]:
        org_id = _caller_org_id()
        if org_id is None:
            return []
        qs = Container.objects.select_related("workload").filter(
            workload__registered_app__organization_id=org_id
        )
        if workload_slug:
            qs = qs.filter(workload__slug=workload_slug)
        return [container_to_type(c) for c in qs[:500]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_rendered_manifest(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
        image_tag: str | None = None,
    ) -> RenderedManifestType | None:
        """Return the rendered Kubernetes resources for an app+env.

        The renderer is called with the same inputs the deploy
        activity uses, so what users see here is exactly what would
        land in the cluster. ``image_tag`` defaults to ``"preview"``
        when omitted so the output is meaningful even before any
        deploy has been issued.

        Manifest parse / normalize errors surface in the ``error``
        field rather than as a GraphQL exception — that lets the UI
        render a friendly editor diagnostic without losing the
        environment + namespace context.
        """
        from astrolift_manifest.normalize import (
            NormalizationDefaults,
            normalize,
        )
        from astrolift_manifest.parser import ManifestError, parse_raw
        from astrolift_manifest.render import render_manifests

        org_id = _caller_org_id()
        app = (
            RegisteredApp.objects.select_related("organization")
            .filter(slug=app_slug, organization_id=org_id)
            .first()
        )
        if app is None:
            return None

        env = (
            AppEnvironment.objects.filter(
                registered_app=app,
                deleted_at__isnull=True,
                **({"name": environment_name} if environment_name else {}),
            )
            .order_by("created_at")
            .first()
        )
        env_name = env.name if env else (environment_name or "preview")
        namespace = app.k8s_namespace or f"{app.organization.slug}-{app.slug}"
        image = image_tag or "preview"

        # Apps registered before the wizard's manifest step shipped, or
        # whose `astrolift.toml` failed to fetch from the source repo,
        # have empty `manifest_raw`. Surface a friendlier error than
        # the parser's "required string 'name' is missing or empty" so
        # the UI can point the operator at the manifest editor.
        if not (app.manifest_raw or "").strip():
            return RenderedManifestType(
                app_slug=app.slug,
                environment_name=env_name,
                image_tag=image,
                namespace=namespace,
                resources=[],
                error=(
                    "No manifest saved for this app yet. Open the Manifest "
                    "tab and paste your astrolift.toml, or re-run the app "
                    "registration wizard to fetch from the source repo."
                ),
                error_path=None,
                error_line=None,
                error_column=None,
            )

        try:
            normalized = normalize(
                parse_raw(app.manifest_raw),
                defaults=NormalizationDefaults(),
            )
        except ManifestError as exc:
            from astrolift_manifest.parser import locate_in_source

            error_path = getattr(exc, "path", None) or None
            line, column = exc.line, exc.column
            # Semantic errors (kind=spaceship, healthcheck typo) don't
            # carry source positions — best-effort locate by leaf key.
            if line is None and error_path:
                line, column = locate_in_source(app.manifest_raw, error_path)
            return RenderedManifestType(
                app_slug=app.slug,
                environment_name=env_name,
                image_tag=image,
                namespace=namespace,
                resources=[],
                error=str(exc),
                error_path=error_path,
                error_line=line,
                error_column=column,
            )
        except Exception as exc:  # defensive: never blow up the resolver
            return RenderedManifestType(
                app_slug=app.slug,
                environment_name=env_name,
                image_tag=image,
                namespace=namespace,
                resources=[],
                error=f"unexpected error: {exc}",
                error_path=None,
                error_line=None,
                error_column=None,
            )

        resources = render_manifests(
            normalized,
            namespace=namespace,
            image_tag=image,
            image_repository=app.registry_repo_uri or app.slug,
            environment_name=env_name,
        )
        return RenderedManifestType(
            app_slug=app.slug,
            environment_name=env_name,
            image_tag=image,
            namespace=namespace,
            resources=resources,
            error=None,
            error_path=None,
            error_line=None,
            error_column=None,
        )

    # ----------------------------------------------------------------
    # Per-workload manifest preview + diff (#430)
    # ----------------------------------------------------------------
    #
    # Renders the platform's full manifest for the app, then filters
    # resources down to ones labelled ``astrolift.dev/workload=<slug>``
    # — the per-workload subset of what the deploy activity would
    # apply.
    #
    # For the diff side: render the same manifest twice — once at the
    # current image tag, once at the prior deployment's tag — and
    # surface both side-by-side. v1 limitation: the platform doesn't
    # store the raw ``astrolift.toml`` per deployment, so a structural
    # change to the manifest between deploys won't show up in the
    # diff — only the image-tag delta will. Full historical TOML diff
    # is a follow-up backend ticket.

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_workload_manifest(
        self,
        info: Info,
        app_slug: str,
        workload_slug: str,
        environment_name: str | None = None,
        image_tag: str | None = None,
    ) -> WorkloadManifestType | None:
        """Return the rendered Kubernetes resources for one workload.

        Filters the app-wide manifest to resources whose
        ``metadata.labels['astrolift.dev/workload']`` matches
        ``workload_slug``. Populates ``previous_image_tag`` +
        ``resources_previous`` from the most-recent prior deployment
        for the same (app, environment) so the FE can render an
        image-tag diff without a second round-trip.

        Returns ``None`` when the app doesn't exist for the current
        tenant. Returns a populated result with an empty
        ``resources`` list when the workload isn't in the manifest
        yet (the FE renders the "no resources" empty state).
        """
        from astrolift_manifest.normalize import (
            NormalizationDefaults,
            normalize,
        )
        from astrolift_manifest.parser import ManifestError, parse_raw
        from astrolift_manifest.render import render_manifests

        org_id = _caller_org_id()
        app = (
            RegisteredApp.objects.select_related("organization")
            .filter(slug=app_slug, organization_id=org_id)
            .first()
        )
        if app is None:
            return None

        env = (
            AppEnvironment.objects.filter(
                registered_app=app,
                deleted_at__isnull=True,
                **({"name": environment_name} if environment_name else {}),
            )
            .order_by("created_at")
            .first()
        )
        env_name = env.name if env else (environment_name or "preview")
        namespace = app.k8s_namespace or f"{app.organization.slug}-{app.slug}"
        image = image_tag or "preview"

        prior = _previous_deployment_for(app=app, environment=env, current_image_tag=image)
        previous_image_tag = prior.image_tag if prior else ""
        previous_deployment_id = str(prior.guid) if prior else ""

        if not (app.manifest_raw or "").strip():
            return WorkloadManifestType(
                app_slug=app.slug,
                workload_slug=workload_slug,
                environment_name=env_name,
                image_tag=image,
                namespace=namespace,
                resources=[],
                previous_image_tag=previous_image_tag,
                previous_deployment_id=previous_deployment_id,
                resources_previous=[],
                error=(
                    "No manifest saved for this app yet. Open the Manifest "
                    "tab and paste your astrolift.toml, or re-run the app "
                    "registration wizard to fetch from the source repo."
                ),
                error_path=None,
                error_line=None,
                error_column=None,
            )

        try:
            normalized = normalize(
                parse_raw(app.manifest_raw),
                defaults=NormalizationDefaults(),
            )
        except ManifestError as exc:
            from astrolift_manifest.parser import locate_in_source

            error_path = getattr(exc, "path", None) or None
            line, column = exc.line, exc.column
            if line is None and error_path:
                line, column = locate_in_source(app.manifest_raw, error_path)
            return WorkloadManifestType(
                app_slug=app.slug,
                workload_slug=workload_slug,
                environment_name=env_name,
                image_tag=image,
                namespace=namespace,
                resources=[],
                previous_image_tag=previous_image_tag,
                previous_deployment_id=previous_deployment_id,
                resources_previous=[],
                error=str(exc),
                error_path=error_path,
                error_line=line,
                error_column=column,
            )
        except Exception as exc:  # noqa: BLE001 — defensive: never blow up the resolver
            return WorkloadManifestType(
                app_slug=app.slug,
                workload_slug=workload_slug,
                environment_name=env_name,
                image_tag=image,
                namespace=namespace,
                resources=[],
                previous_image_tag=previous_image_tag,
                previous_deployment_id=previous_deployment_id,
                resources_previous=[],
                error=f"unexpected error: {exc}",
                error_path=None,
                error_line=None,
                error_column=None,
            )

        rendered = render_manifests(
            normalized,
            namespace=namespace,
            image_tag=image,
            image_repository=app.registry_repo_uri or app.slug,
            environment_name=env_name,
        )
        scoped = _filter_resources_for_workload(rendered, workload_slug)
        scoped_previous: list[dict[str, object]] = []
        if previous_image_tag and previous_image_tag != image:
            rendered_previous = render_manifests(
                normalized,
                namespace=namespace,
                image_tag=previous_image_tag,
                image_repository=app.registry_repo_uri or app.slug,
                environment_name=env_name,
            )
            scoped_previous = _filter_resources_for_workload(rendered_previous, workload_slug)

        return WorkloadManifestType(
            app_slug=app.slug,
            workload_slug=workload_slug,
            environment_name=env_name,
            image_tag=image,
            namespace=namespace,
            resources=scoped,
            previous_image_tag=previous_image_tag,
            previous_deployment_id=previous_deployment_id,
            resources_previous=scoped_previous,
            error=None,
            error_path=None,
            error_line=None,
            error_column=None,
        )

    @strawberry.field
    @tenant_scoped()
    def assignable_astrolift_projects(self, info: Info) -> list[ProjectType]:
        """Projects in the current tenant org the viewer can assign
        apps to (#391).

        Self-service: no ``@require_permission`` gate — the viewer's
        own bindings are the gate, same shape as ``astrolift_my_apps``.
        Returns projects reachable by an active RoleBinding at ORG /
        TEAM / PROJECT scope. APP-scope bindings don't surface
        projects: a user with app-only access on one app shouldn't be
        offered the underlying project as an assignment target for
        OTHER apps.

        Superusers see every active project in the tenant.
        """
        from django.db.models import Q
        from django.utils import timezone

        from astrolift_identity.models import Project, RoleBinding

        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return []

        from django.contrib.auth import get_user_model

        User = get_user_model()
        viewer = (
            User.objects.filter(pk=tenant.actor_user_id).first() if tenant.actor_user_id is not None else None
        )

        base_qs = Project.objects.select_related("organization", "team").filter(
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        )

        if (
            viewer is not None
            and getattr(viewer, "is_superuser", False)
            and getattr(viewer, "is_active", True)
        ):
            return [project_to_type(p) for p in base_qs.order_by("name")[:500]]

        if viewer is None:
            return []

        now = timezone.now()
        bindings = list(
            RoleBinding.objects.filter(
                user_id=viewer.pk,
                deleted_at__isnull=True,
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        )
        if not bindings:
            return []

        org_ids: set[int] = set()
        team_ids: set[int] = set()
        project_ids: set[int] = set()
        for b in bindings:
            if b.scope_kind == RoleBinding.ScopeKind.ORG:
                org_ids.add(b.scope_id)
            elif b.scope_kind == RoleBinding.ScopeKind.TEAM:
                team_ids.add(b.scope_id)
            elif b.scope_kind == RoleBinding.ScopeKind.PROJECT:
                project_ids.add(b.scope_id)

        scope_filter = Q()
        if org_ids:
            scope_filter |= Q(organization_id__in=org_ids)
        if team_ids:
            scope_filter |= Q(team_id__in=team_ids)
        if project_ids:
            scope_filter |= Q(pk__in=project_ids)
        if not scope_filter.children:
            return []

        qs = base_qs.filter(scope_filter).order_by("team__name", "name")[:500]
        return [project_to_type(p) for p in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def scan_app_manifests(
        self,
        info: Info,
        source_repo: str,
        source_kind: str = "github",
        ref: str = "main",
    ) -> ScanAppManifestsResultType:
        """Scan a repo for app manifests and return a preview (#979).

        Backs the monorepo / multi-service discovery step of the app onboarding
        wizard: given a repo handle (``source_repo`` = ``owner/name``), walks
        ``apps/*/astrolift.toml`` + a root ``astrolift.toml`` and returns each
        app manifest as a preview row WITHOUT persisting anything. The operator
        then confirms registration via ``registerAppRepo``.

        Read-only and org-scoped: the active tenant (established by
        ``@tenant_scoped``) is the organization boundary — the repo is fetched
        through that org's own source connection, and ``already_registered`` is
        computed against that org's apps, so a caller can neither scan with
        another tenant's credentials nor register the same repo twice.
        """
        from astrolift_registry.services.manifest_sync import discover_app_manifests

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return ScanAppManifestsResultType(ok=False, apps=[], error="no active organization")

        result = discover_app_manifests(
            organization_id=org_id,
            source_kind=source_kind or "github",
            source_repo=source_repo,
            ref=ref or "main",
        )
        return ScanAppManifestsResultType(
            ok=result.status == "ok",
            apps=[
                DiscoveredAppManifestType(
                    manifest_path=a.manifest_path,
                    name=a.name,
                    build_context=a.build_context,
                    workload_count=a.workload_count,
                    already_registered=a.already_registered,
                )
                for a in result.apps
            ],
            error=result.error,
        )
