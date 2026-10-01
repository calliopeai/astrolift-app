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
from django.db.models import Case, Exists, F, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce, Lower
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import (
    GUID,
    FilterField,
    PageType,
    SortKey,
    filter_q,
    filter_values,
    keyset_page,
    numbered_page,
    resolve_list_sort,
    search_q,
)
from astrolift_identity.schema.types import ProjectType, project_to_type
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.dependency_context import (
    AppDependencyContext,
    dependency_operations,
    read_dependency_context,
)
from astrolift_registry.models import AppTeamAccess, Container, RegisteredApp, Workload
from astrolift_registry.schema.types import (
    STALE_DEPLOY_WINDOW_DAYS,
    AppDoctorReportType,
    AppFreshness,
    AppHealthPulseType,
    AppListState,
    AppsListFilterInput,
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
    build_app_doctor_report,
    build_app_freshness,
    build_autowire_status,
    build_ci_workflow_sync_status,
    build_config_drift,
    build_settings_last_modified,
    container_to_type,
    workload_to_type,
)
from astrolift_registry.schema.workload_list import (
    WORKLOADS_DEFAULT_SORT,
    WORKLOADS_FILTERS,
    WORKLOADS_SORT_KEYS,
    WorkloadsListFilterInput,
    cron_last_runs,
)
from astrolift_registry.scopes import (
    app_scope_by_guid,
    app_scope_by_slug,
    registry_organization_scope,
)
from astrolift_registry.topology import classify_topology
from astrolift_registry.visibility import visible_registry_apps
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
#: What the one search box matches (spec 44 §5.1), plus a prefix of the id.
_APPS_SEARCH_FIELDS = ("name", "slug", "description", "source_repo", "source_url")


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
            qs = qs.filter(search_q(needle, *_APPS_SEARCH_FIELDS, prefix=("guid",)))
    if team_slug:
        qs = qs.filter(team__slug=team_slug)
    if project_slug:
        qs = qs.filter(project__slug=project_slug)
    if source_kind and source_kind is not AstroliftAppSourceKindFilter.ALL:
        qs = qs.filter(source_kind=source_kind.value)
    # Apps and Agents are separate entity-modules (specs 34-36). An app that
    # exists only to host agents belongs in the Agents list — exclude those
    # agent-only hosts here so they don't bleed into the Apps list. A mixed
    # registration with an application workload remains an app. Both
    # app-list resolvers + the page totalCount route through this helper, so
    # the separation holds uniformly.
    from astrolift_registry.models import Workload

    non_agent_host_app_ids = (
        Workload.objects.filter(
            deleted_at__isnull=True,
        )
        .exclude(kind=Workload.Kind.AGENT)
        .values("registered_app_id")
    )
    agent_only_host_app_ids = (
        Workload.objects.filter(
            kind=Workload.Kind.AGENT,
            deleted_at__isnull=True,
        )
        .exclude(registered_app_id__in=non_agent_host_app_ids)
        .values("registered_app_id")
    )
    qs = qs.exclude(pk__in=agent_only_host_app_ids)
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
    info: Info,
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
        return RegisteredAppPageType(
            items=_app_page_items(
                items, info=info, freshness_by_app=freshness_by_app, include_freshness=include_freshness
            ),
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
    return RegisteredAppPageType(
        items=_app_page_items(
            items, info=info, freshness_by_app=freshness_by_app, include_freshness=include_freshness
        ),
        next_cursor=next_cursor,
        total_count=total_count,
    )


def _app_page_items(
    apps: list[RegisteredApp],
    *,
    info: Info,
    freshness_by_app: dict[int, AppFreshness],
    include_freshness: bool,
) -> list[RegisteredAppType]:
    """Map one page of apps to rows, with every per-row input fetched in bulk."""
    preview_counts = _active_preview_counts(apps)
    managed_hostnames = _managed_hostnames_for_apps(apps)
    viewer_perms = _viewer_permissions_for_apps(apps)
    topology = _topology_for_apps([a.pk for a in apps])
    clusters = _cluster_slugs_for_apps(apps)
    return [
        app_to_type(
            a,
            info=info,
            freshness=freshness_by_app.get(a.pk) if include_freshness else None,
            active_preview_count=preview_counts.get(a.pk, 0),
            managed_hostname=managed_hostnames.get(a.pk, ""),
            viewer_permissions=viewer_perms.get(a.pk),
            topology_kind=topology.get(a.pk),
            cluster_slugs=clusters.get(a.pk, []),
        )
        for a in apps
    ]


# ---------------------------------------------------------------------------
# The list contract on the Apps list (spec 44 §5.1, #2149)
# ---------------------------------------------------------------------------
#
# The reference application of ``astrolift_graphql``'s list helpers (see the
# README there). Everything the Apps screen used to work out in the browser,
# status, failing, kind, cluster, the deploy pulse and the column sorts, is
# either a column or an annotation here, so a numbered page's OFFSET and its
# totalCount are exact. Kind is the one exception: it is classified from the
# workloads in Python, once per request, and folded back in as ``pk__in``.

#: Header statuses in the order the Status column sorts them.
_APP_STATE_ORDER = [state.value for state in AppListState]

_APPS_DEFAULT_SORT = "-created"

_APPS_SORT_KEYS: dict[str, SortKey] = {
    "name": SortKey(Lower("name")),
    "created": SortKey("created_at"),
    # Never deployed sorts below the oldest deploy, as it did in the browser.
    "deployed": SortKey("_deployed_at", nulls_low=True),
    "status": SortKey("_state_rank"),
}

#: The legacy ``sortBy`` enum, spelled as a sort spec for the numbered path.
_LEGACY_APPS_SORT_SPEC = {
    AppsListSortKey.CREATED_DESC: "-created",
    AppsListSortKey.DEPLOYED_DESC: "-deployed",
    AppsListSortKey.NAME_ASC: "name",
}

#: The legacy single-value ``status`` argument, as a ``deploy`` filter value.
_LEGACY_STATUS_TO_PULSE = {
    AstroliftAppListStatusFilter.OK: "ok",
    AstroliftAppListStatusFilter.DEGRADED: "degraded",
    AstroliftAppListStatusFilter.STALE: "stale",
    AstroliftAppListStatusFilter.NEVER_DEPLOYED: "never",
}


def _iexact_any(path: str, values: list[str]) -> Q:
    query = Q()
    for value in values:
        query |= Q(**{f"{path}__iexact": value})
    return query


def _apps_on_clusters(slugs: list[str]) -> Q:
    envs = AppEnvironment.objects.filter(_iexact_any("tenant_cluster__slug", slugs), deleted_at__isnull=True)
    return Q(pk__in=envs.values("registered_app_id"))


_APPS_FILTERS: dict[str, FilterField] = {
    "failing": FilterField("_failing"),
    "status": FilterField("_list_state"),
    "deploy": FilterField("_deploy_pulse"),
    "cluster": FilterField(q=_apps_on_clusters),
    "project": FilterField(q=lambda v: _iexact_any("project__slug", v) | _iexact_any("project__name", v)),
    "team": FilterField("team__slug"),
    # ``archived`` is a tri-state the resolver applies, ``kind`` is Python.
}


def _annotate_apps_list(qs):
    """Annotate the columns the Apps list filters and sorts on.

    Three correlated subqueries over the app's deployments (latest status,
    latest time, latest successful time) feed the rest:

    * ``_deployed_at``: the last successful deploy, else the latest one,
      which is what the Deployed column shows.
    * ``_deploy_pulse``: the health pulse as ``build_app_freshness`` derives
      it, in SQL, so ``deploy`` filters without the post-DB scan.
    * ``_failing``: provisioning failed or the latest deploy did.
    * ``_list_state`` / ``_state_rank``: the header status and its sort rank.
      ``live`` is ``ready`` with a managed zone, resolved the way
      ``_managed_hostnames_for_apps`` resolves it.
    """
    deploys = Deployment.objects.filter(registered_app=OuterRef("pk"), deleted_at__isnull=True).order_by(
        "-created_at"
    )
    stale_before = timezone.now() - dt.timedelta(days=STALE_DEPLOY_WINDOW_DAYS)
    org_zone = "organization__default_managed_domain"
    from astrolift_clusters.models import ManagedDomain

    qs = qs.annotate(
        _latest_deploy_status=Subquery(deploys.values("status")[:1]),
        _latest_deploy_at=Subquery(deploys.values("created_at")[:1]),
        _last_success_at=Subquery(
            deploys.filter(status=Deployment.Status.RUNNING.value).values("created_at")[:1]
        ),
    ).annotate(
        _deployed_at=Coalesce("_last_success_at", "_latest_deploy_at"),
        _has_zone=Case(
            When(
                Q(**{f"{org_zone}__isnull": False, f"{org_zone}__deleted_at__isnull": True})
                & ~Q(**{f"{org_zone}__verification_state": ManagedDomain.VerificationState.PENDING}),
                then=Value(True),
            ),
            When(Exists(_platform_zone_sq()), then=Value(True)),
            default=Value(False),
            output_field=models.BooleanField(),
        ),
    )
    qs = qs.annotate(
        _deploy_pulse=Case(
            When(_latest_deploy_at__isnull=True, then=Value("never")),
            When(_latest_deploy_status=Deployment.Status.FAILED.value, then=Value("degraded")),
            When(_deployed_at__lte=stale_before, then=Value("stale")),
            default=Value("ok"),
            output_field=models.CharField(),
        ),
        _failing=Case(
            When(
                Q(provisioning_status=RegisteredApp.ProvisioningStatus.FAILED.value)
                | Q(_latest_deploy_status=Deployment.Status.FAILED.value),
                then=Value(True),
            ),
            default=Value(False),
            output_field=models.BooleanField(),
        ),
        _list_state=Case(
            When(archived_at__isnull=False, then=Value(AppListState.ARCHIVED.value)),
            When(
                provisioning_status=RegisteredApp.ProvisioningStatus.READY.value,
                _has_zone=True,
                then=Value(AppListState.LIVE.value),
            ),
            default=F("provisioning_status"),
            output_field=models.CharField(),
        ),
    )
    return qs.annotate(
        _state_rank=Case(
            *(When(_list_state=state, then=Value(rank)) for rank, state in enumerate(_APP_STATE_ORDER)),
            default=Value(len(_APP_STATE_ORDER)),
            output_field=models.IntegerField(),
        )
    )


def _apply_apps_contract_filter(qs, values: dict):
    """Apply the ``filter`` input's declared fields to an annotated queryset.

    ``archived`` is left to the resolver (it composes with the legacy
    ``includeArchived``); ``kind`` is classified here from the workloads of
    the apps still in the set.
    """
    qs = qs.filter(filter_q(values, _APPS_FILTERS))
    kinds = values.get("kind")
    if kinds:
        topology = _topology_for_apps(qs.values("pk"))
        qs = qs.filter(pk__in=[pk for pk, kind in topology.items() if kind in kinds])
    return qs


def _topology_for_apps(app_ids) -> dict[int, str]:
    """Each app's topology kind, from its live workloads; apps without any are absent.

    ``app_ids`` is a list or a ``values("pk")`` queryset, so the kind filter
    classifies the whole filtered set in one query and a page classifies
    only its own rows.
    """
    by_app: dict[int, list[tuple[str, str]]] = {}
    rows = Workload.objects.filter(registered_app_id__in=app_ids, deleted_at__isnull=True).values_list(
        "registered_app_id", "kind", "name"
    )
    for app_id, kind, name in rows:
        by_app.setdefault(app_id, []).append((kind, name))
    return {app_id: classify_topology(workloads) for app_id, workloads in by_app.items()}


def _cluster_slugs_for_apps(apps: Iterable[RegisteredApp]) -> dict[int, list[str]]:
    """Distinct cluster slugs per app, in environment-name order, in one query."""
    rows = (
        AppEnvironment.objects.filter(
            registered_app_id__in=[a.pk for a in apps],
            deleted_at__isnull=True,
            tenant_cluster__isnull=False,
        )
        .order_by("registered_app_id", "name")
        .values_list("registered_app_id", "tenant_cluster__slug")
    )
    out: dict[int, list[str]] = {}
    for app_id, slug in rows:
        seen = out.setdefault(app_id, [])
        if slug not in seen:
            seen.append(slug)
    return out


def _apps_list_page(
    qs,
    *,
    info: Info,
    include_archived: bool,
    filter: AppsListFilterInput | None,
    cursor: str | None,
    limit: int,
    include_freshness: bool,
    status: AstroliftAppListStatusFilter,
    sort_by: AppsListSortKey,
    sort: str | None,
    page: int | None,
    page_size: int | None,
) -> RegisteredAppPageType:
    """One Apps page from a scoped, legacy-filtered queryset (both resolvers).

    ``qs`` arrives scoped to the viewer and narrowed by the legacy
    arguments (search, teamSlug, projectSlug, sourceKind). This applies
    the ``filter`` input and pages in one of two modes:

    * numbered, when ``page``, ``pageSize`` or ``sort`` is given: every
      filter and sort in SQL, an exact ``totalCount``, ``page`` and
      ``pageSize`` echoed, ``nextCursor`` null. The legacy ``sortBy`` and
      ``status`` still apply, spelled as a sort spec and a ``deploy`` filter.
    * cursor, otherwise: the pre-#2149 walk, unchanged, with the ``filter``
      input applied first.
    """
    values = filter_values(filter)
    archived = values.pop("archived", None)
    if archived is True:
        qs = qs.filter(archived_at__isnull=False)
    elif archived is False or not include_archived:
        qs = qs.filter(archived_at__isnull=True)

    if page is None and page_size is None and sort is None:
        if values:
            qs = _apply_apps_contract_filter(_annotate_apps_list(qs), values)
        return _build_apps_page(
            qs,
            info=info,
            cursor=cursor,
            limit=limit,
            include_freshness=include_freshness,
            status=status,
            sort_by=sort_by,
        )

    if _status_filter_active(status) and "deploy" not in values:
        values["deploy"] = [_LEGACY_STATUS_TO_PULSE[status]]
    order_by = resolve_list_sort(
        sort or _LEGACY_APPS_SORT_SPEC.get(sort_by), _APPS_SORT_KEYS, default=_APPS_DEFAULT_SORT
    )
    qs = _apply_apps_contract_filter(_annotate_apps_list(_annotate_managed_domain(qs)), values)
    result = numbered_page(
        qs.prefetch_related("approver_users"), order_by=order_by, page=page, page_size=page_size
    )
    freshness_by_app = _freshness_for_apps(result.rows) if include_freshness else {}
    return RegisteredAppPageType(
        items=_app_page_items(
            result.rows, info=info, freshness_by_app=freshness_by_app, include_freshness=include_freshness
        ),
        next_cursor=None,
        total_count=result.total_count,
        page=result.page,
        page_size=result.page_size,
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

    Excludes a row still awaiting its TXT proof-of-control challenge
    (#1931), the same as ``resolve_managed_domain`` -- a hostname must not
    render for a zone nothing has proven the platform may use.
    """
    return qs.select_related("organization__default_managed_domain").annotate(
        _platform_zone=Subquery(_platform_zone_sq())
    )


def _platform_zone_sq():
    """The platform-level zone for tenant apps (one global row), as a subquery."""
    from astrolift_clusters.models import ManagedDomain

    return (
        ManagedDomain.objects.filter(
            organization__isnull=True,
            default_for__in=[ManagedDomain.DefaultFor.TENANT_APPS, ManagedDomain.DefaultFor.BOTH],
            deleted_at__isnull=True,
        )
        .exclude(verification_state=ManagedDomain.VerificationState.PENDING)
        .order_by("pk")
        .values("zone")[:1]
    )


def _managed_hostnames_for_apps(apps: Iterable[RegisteredApp]) -> dict[int, str]:
    """Map each app to its platform-managed hostname (#1043).

    Reads the managed-domain inputs that :func:`_annotate_managed_domain`
    pre-loaded onto the rows — the ``organization.default_managed_domain``
    FK (select_related cache) and the ``_platform_zone`` annotation —
    so it issues no queries of its own. Resolution mirrors
    ``resolve_managed_domain``: a non-deleted, non-pending org-default wins
    (#1931 -- a row still awaiting its TXT challenge is not proven, so it
    does not render a hostname), else the platform-level zone.
    ``app_to_type`` reads this map via the ``managed_hostname=`` kwarg.
    """
    from astrolift_clusters.models import ManagedDomain

    app_list = list(apps)
    if not app_list:
        return {}

    out: dict[int, str] = {}
    for app in app_list:
        org = app.organization if app.organization_id else None
        domain = getattr(org, "default_managed_domain", None) if org is not None else None
        if domain is not None and getattr(domain, "deleted_at", None) is not None:
            domain = None
        if domain is not None and domain.verification_state == ManagedDomain.VerificationState.PENDING:
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
    from core.app_deploy import namespace_for_environment
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    env = _scaling_environment_for_workload(workload, environment_name)
    if env is None or env.tenant_cluster_id is None:
        raise RuntimeError("no env / cluster")
    cluster = env.tenant_cluster
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    namespace = namespace_for_environment(env)
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


def _app_team_accesses_qs(*, app_slug: str, search: str | None = None):
    """Filtered, unordered team-access rows for one app in the caller's org.

    Shared by the list field and its paginated sibling so the two
    can never disagree about which grants exist. Ordering is
    deliberately not applied here — ``keyset_page`` imposes it from
    the seek key.

    The pre-#1235 resolver fetched the app first (org-scoped, live
    only) and returned ``[]`` when it was missing; joining through
    ``registered_app`` selects exactly that row set in one query and
    keeps the org constraint on the query itself. ``AppTeamAccess``
    has no organization FK of its own, so this join IS the tenant
    boundary — ``@tenant_scoped`` only asserts a tenant exists.
    Deny-by-default: no tenant context matches no rows (#1042/#1183).
    """
    org_id = _caller_org_id()
    if org_id is None:
        return AppTeamAccess.objects.none()
    qs = AppTeamAccess.objects.select_related("registered_app", "team").filter(
        registered_app__slug=app_slug,
        registered_app__organization_id=org_id,
        registered_app__deleted_at__isnull=True,
        deleted_at__isnull=True,
    )
    if search:
        qs = qs.filter(search_q(search, "team__slug", "team__name"))
    return qs


def _workloads_qs(
    *,
    app_slug: str | None,
    search: str | None = None,
    kinds: list[str] | None = None,
):
    """Filtered, unordered workload rows for the caller's org.

    Shared by the list field and its paginated sibling so the two
    can never disagree about what a workload row is. Ordering is
    deliberately not applied here — ``keyset_page`` imposes it from
    the seek key.

    ``Workload`` has no organization FK of its own; it reaches the
    tenant through ``registered_app``, and the default manager is
    not tenant-aware — this filter IS the tenant boundary. Deny-by-
    default: no tenant context matches no rows (#1042 / #1183).
    """
    org_id = _caller_org_id()
    if org_id is None:
        return Workload.objects.none()
    apps = visible_registry_apps(RegisteredApp.objects.all(), Permission.APP_READ)
    qs = Workload.objects.select_related("registered_app").filter(registered_app__in=apps)
    if app_slug:
        qs = qs.filter(registered_app__slug=app_slug)
    if search:
        qs = qs.filter(search_q(search, "name", "slug", "kind", "registered_app__slug"))
    if kinds:
        # A filter, not a seek key: ``kind`` is an eight-value TextChoices and
        # never null, so it raises none of the NOT NULL / uniqueness concerns a
        # sort column would (#1235).
        qs = qs.filter(kind__in=kinds)
    return qs


@strawberry.type
class RegistryQuery:
    @strawberry.field(
        description="Read-only APP_READ projection for an exact live app/environment. Expected cluster/provider GUIDs refuse reassignment. Persisted observations do not prove live health, TLS binding or renewal; mutation authority is unchanged."
    )
    @require_permission(
        Permission.APP_READ,
        scope=app_scope_by_guid("app_id", permission=Permission.APP_READ),
        operation=dependency_operations,
    )
    @tenant_scoped()
    def astrolift_app_dependency_context(
        self,
        info: Info,
        app_id: GUID,
        environment_id: GUID,
        expected_cluster_id: GUID | None = None,
        expected_provider_id: GUID | None = None,
    ) -> AppDependencyContext | None:
        """APP_READ snapshot for an exact live app/environment pair; no cluster-control authority.

        Expected immutable identities refuse replaced or reassigned targets. Heartbeat
        and app-wide domain/certificate metadata are persisted observations, not live
        health, environment binding, renewal or TLS verification.
        """
        return read_dependency_context(app_id, environment_id, expected_cluster_id, expected_provider_id)

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
    @require_permission(Permission.APP_READ, any_scope=True)
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
        qs = visible_registry_apps(
            RegisteredApp.objects.select_related("organization", "team", "project").filter(
                organization_id=org_id,
                deleted_at__isnull=True,
                archived_at__isnull=True,
            ),
            Permission.APP_READ,
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
                    info=info,
                    active_preview_count=preview_counts.get(a.pk, 0),
                    managed_hostname=managed_hostnames.get(a.pk, ""),
                    viewer_permissions=viewer_perms.get(a.pk),
                )
                for a in apps
            ]
        return [
            app_to_type(
                a,
                info=info,
                freshness=freshness_by_app.get(a.pk),
                active_preview_count=preview_counts.get(a.pk, 0),
                managed_hostname=managed_hostnames.get(a.pk, ""),
                viewer_permissions=viewer_perms.get(a.pk),
            )
            for a in apps
        ]

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
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
        filter: AppsListFilterInput | None = None,
        sort: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
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

        The list contract (spec 44 §5.1, #2149): ``filter`` takes the
        declared filters (archived-only, failing, status, deploy, kind,
        cluster, project, team), ``sort`` a multi-key spec over ``name``,
        ``created``, ``deployed`` and ``status`` (``-deployed,name``), and
        ``page`` / ``pageSize`` a numbered page. Any of ``sort``, ``page``
        or ``pageSize`` selects numbered paging; see ``_apps_list_page``.
        """
        status = _coerce_apps_status(status)
        org_id = _caller_org_id()
        qs = RegisteredApp.objects.select_related("organization", "team", "project").filter(
            deleted_at__isnull=True,
        )
        # Deny-by-default: no tenant context → no rows (#1042).
        qs = (
            visible_registry_apps(qs.filter(organization_id=org_id), Permission.APP_READ)
            if org_id is not None
            else qs.none()
        )
        qs = _apply_apps_list_filters(
            qs,
            search=search,
            team_slug=team_slug,
            project_slug=project_slug,
            source_kind=source_kind,
        )
        return _apps_list_page(
            qs,
            info=info,
            include_archived=include_archived,
            filter=filter,
            cursor=cursor,
            limit=limit,
            include_freshness=include_freshness,
            status=status,
            sort_by=sort_by,
            sort=sort,
            page=page,
            page_size=page_size,
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
                    info=info,
                    active_preview_count=preview_counts.get(a.pk, 0),
                    managed_hostname=managed_hostnames.get(a.pk, ""),
                    viewer_permissions=viewer_perms.get(a.pk),
                )
                for a in scoped_apps
            ]
        return [
            app_to_type(
                a,
                info=info,
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
        filter: AppsListFilterInput | None = None,
        sort: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> RegisteredAppPageType:
        """Cursor-paginated viewer-scoped apps list (#481, #729).

        Same shape as :func:`astrolift_apps_page` but the queryset is
        first narrowed to apps the viewer's RoleBindings reach. EXEMPT
        from the tenancy guardrail for the same reason
        :func:`astrolift_my_apps` is — the viewer's bindings ARE the
        gate.

        ``include_archived`` (default False, #743) opts the page into
        showing archived apps the viewer can reach. Same opt-in shape
        as :func:`astrolift_apps_page`, and so are ``filter``, ``sort``,
        ``page`` and ``pageSize`` (#2149).
        """
        status = _coerce_apps_status(status)
        scope_filter = _viewer_scope_filter()
        if scope_filter is None:
            return RegisteredAppPageType(items=[], next_cursor=None, total_count=0)

        tenant = get_current_tenant()
        base_qs = RegisteredApp.objects.select_related("organization", "team", "project").filter(
            deleted_at__isnull=True
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
        return _apps_list_page(
            qs,
            info=info,
            include_archived=include_archived,
            filter=filter,
            cursor=cursor,
            limit=limit,
            include_freshness=include_freshness,
            status=status,
            sort_by=sort_by,
            sort=sort,
            page=page,
            page_size=page_size,
        )

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=app_scope_by_slug("slug", permission=Permission.APP_READ))
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

        Freshness (``latestDeployment`` / ``lastDeployedAt`` /
        ``healthPulse``) is not opt-in here. The opt-in on the list
        resolvers exists to keep a 200-row page from paying for a
        rollup nobody asked for; a detail request is one row, and
        leaving it null made the field structurally always null on this
        path (#1691) — an app with a healthy pod and several successful
        deploys read as though nothing had ever shipped.
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
        # Managed CI-workflow versioned-sync status (#1209) — detail-only,
        # DB-only (reads columns already on ``app``, no host round-trip).
        ci_workflow_sync = build_ci_workflow_sync_status(app)
        return app_to_type(
            app,
            info=info,
            freshness=_freshness_for_apps([app]).get(app.pk),
            drift=drift,
            autowire=autowire,
            ci_workflow_sync_status=ci_workflow_sync,
            settings_last_modified=settings_last_modified,
            include_retention_policies=True,
        )

    @strawberry.field(
        deprecation_reason=(
            "Returns every grant in one unbounded response. Use astroliftAppTeamAccessesPage."
        )
    )
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_app_team_accesses(self, info: Info, app_slug: str) -> list[AppTeamAccessType]:
        """List every team that holds active access to ``app_slug``.

        Includes the home-team row (``is_home=true``) plus any
        additional teams granted via ``grantTeamAccessToApp``.
        Backfilled deployments will show exactly one row (the home
        team at ``OWNER``) until the operator grants more teams.
        """
        rows = _app_team_accesses_qs(app_slug=app_slug).order_by("team__slug")
        # Every row in the set belongs to the one app the filter names,
        # so the app's home team comes off the select_related row rather
        # than a second fetch.
        return [app_team_access_to_type(r, home_team_id=r.registered_app.team_id) for r in rows]

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_app_team_accesses_page(
        self,
        info: Info,
        app_slug: str,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[AppTeamAccessType]:
        """Cursor-paginated team-access grants for one app (#1235).

        Replaces ``astroliftAppTeamAccesses``, which had no cap at all:
        an app shared across a large org's teams returned every grant in
        one response.

        Seek key is ``(team__slug, guid)`` ASCENDING — the alphabetical
        order the Teams card already renders. Kept (rather than moved to
        the ``-created_at`` default) because this table is read as a
        roster, where "who has access" is looked up by name, not as a
        feed; and because ``(registered_app, team)`` is unique among
        live rows, so ``team__slug`` is already unique within this
        filtered set and the walk cannot churn. ``guid`` rides along as
        the tiebreak anyway so the walk stays correct if that ever stops
        holding. Both columns are NOT NULL — ``team`` is a non-nullable
        FK and ``Team.slug`` a non-nullable slug field.
        """
        page = keyset_page(
            _app_team_accesses_qs(app_slug=app_slug, search=search),
            cursor=after,
            limit=limit,
            sort_field="team__slug",
            tiebreak_field="guid",
            descending=False,
        )
        return page.map(lambda r: app_team_access_to_type(r, home_team_id=r.registered_app.team_id))

    @strawberry.field(
        deprecation_reason=("Caps at 200 rows with no way to reach the 201st. Use astroliftWorkloadsPage.")
    )
    @require_permission(Permission.APP_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_workloads(self, info: Info, app_slug: str | None = None) -> list[WorkloadType]:
        # No ``order_by``: this field never had one, and the workload
        # cards on /apps/<slug>/workloads, /functions, /tasks and the
        # /jobs schedule list all render in whatever order it returned.
        # Imposing one now would visibly reshuffle every one of them, so
        # the deprecated field keeps its exact behaviour; the page field
        # below defines its own (newest-first).
        from astrolift_registry.viewer_actions import workload_viewer_permissions

        rows = list(_workloads_qs(app_slug=app_slug)[:200])
        permissions = workload_viewer_permissions(rows)
        return [workload_to_type(w, viewer_permission=permissions[w.pk]) for w in rows]

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_workloads_page(
        self,
        info: Info,
        app_slug: str | None = None,
        search: str | None = None,
        kinds: list[str] | None = None,
        limit: int = 50,
        after: str | None = None,
        filter: WorkloadsListFilterInput | None = None,
        sort: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> PageType[WorkloadType]:
        """Cursor-paginated workload list (#1235).

        Replaces ``astroliftWorkloads``, whose 200-row cap left an
        install's 201st workload unreachable. Four surfaces read that
        one field — the app workloads tab, /functions, /tasks and the
        /jobs schedule list — so the cap silently truncated all of them
        at once.

        Seek key is ``(-created_at, -guid)``. ``search`` matches the
        workload name, slug and kind plus the owning app's slug, so the
        cross-app surfaces can narrow to one app by name without a
        second round-trip.

        ``kinds`` narrows server-side, which the single-kind fleet surfaces
        need (#1242). /functions, /tasks and the /jobs schedule list are each
        one kind, and filtering on the client left two artefacts: ``totalCount``
        had to be nulled out, because the server counted every workload in the
        org rather than every function, and a page whose 25 rows happened to
        contain none of the wanted kind rendered the empty state with Next
        still enabled. The rows on screen were always right; the page
        boundaries were not.

        ``search`` is not a substitute. It ORs ``icontains`` across name, slug,
        kind and the owning app's slug, so ``search="function"`` also matches a
        service named ``function-gateway``.

        The list contract (#2155): ``filter`` takes kind, isPublic, app and
        owner (``"me"`` is the viewer), and ANDs with ``kinds``. Any of
        ``sort``, ``page`` or ``pageSize`` selects numbered paging: ``sort``
        a multi-key spec over name, slug, kind, app, public and created
        (default ``name``), an exact filtered ``totalCount``, ``page`` and
        ``pageSize`` echoed, ``nextCursor`` null. Otherwise the cursor walk
        runs unchanged, with ``filter`` applied first. Cron jobs carry their
        latest run either way.
        """
        tenant = get_current_tenant()
        qs = _workloads_qs(app_slug=app_slug, search=search, kinds=kinds).filter(
            filter_q(filter, WORKLOADS_FILTERS, me=tenant.actor_user_id if tenant else None)
        )
        if page is not None or page_size is not None or sort is not None:
            order_by = resolve_list_sort(sort, WORKLOADS_SORT_KEYS, default=WORKLOADS_DEFAULT_SORT)
            result = numbered_page(qs, order_by=order_by, page=page, page_size=page_size)
        else:
            result = keyset_page(qs, cursor=after, limit=limit)
        last_runs = cron_last_runs(result.rows)
        from astrolift_registry.viewer_actions import workload_viewer_permissions

        permissions = workload_viewer_permissions(result.rows)
        return result.map(
            lambda w: workload_to_type(w, last_run=last_runs.get(w.pk), viewer_permission=permissions[w.pk])
        )

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
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
        return workload_to_type(w, last_run=cron_last_runs([w]).get(w.pk)) if w else None

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
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
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
    @require_permission(Permission.APP_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_containers(self, info: Info, workload_slug: str | None = None) -> list[ContainerType]:
        from astrolift_services.secret_visibility import can_reveal_app_secrets

        org_id = _caller_org_id()
        if org_id is None:
            return []
        qs = Container.objects.select_related("workload__registered_app").filter(
            workload__registered_app__in=visible_registry_apps(
                RegisteredApp.objects.all(), Permission.APP_READ
            ),
            workload__deleted_at__isnull=True,
        )
        if workload_slug:
            qs = qs.filter(workload__slug=workload_slug)
        containers = list(qs[:500])
        # One RBAC query for the page, however many apps it spans (#1948).
        apps = {c.workload.registered_app_id: c.workload.registered_app for c in containers}
        viewer_perms = _viewer_permissions_for_apps(apps.values())
        revealed = {
            pk: can_reveal_app_secrets(info, app=app, known_permissions=viewer_perms.get(pk))
            for pk, app in apps.items()
        }
        return [container_to_type(c, env_revealed=revealed[c.workload.registered_app_id]) for c in containers]

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
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
        from core.app_deploy import namespace_for_app, namespace_for_environment

        namespace = namespace_for_environment(env) if env else namespace_for_app(app)
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

        from astrolift_services.secret_visibility import redacted_render_input

        resources = render_manifests(
            redacted_render_input(info, app=app, manifest=normalized),
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
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
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
        from core.app_deploy import namespace_for_app, namespace_for_environment

        namespace = namespace_for_environment(env) if env else namespace_for_app(app)
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

        from astrolift_services.secret_visibility import redacted_render_input

        normalized = redacted_render_input(info, app=app, manifest=normalized)
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
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_app_doctor(self, info: Info, app_slug: str) -> AppDoctorReportType:
        """Verify every dependency this app needs is actually usable (#1550).

        The service behind this has existed and been tested since #1550 was
        filed; nothing exposed it, so the checks were written, green, and
        unreachable -- an operator asking "is this app fully wired?" had no
        way to run them. This is that exposure.

        Org-scoped before any probing: slugs are unique only within an org,
        and the checks read a push-role trust policy and resolve hostnames,
        so a caller must not be able to aim them at another tenant's app.
        Fails closed (an empty report, not a crash) when there is no tenant.

        Read-mostly rather than read-only, deliberately: the manifest check
        runs the same idempotent resync the Settings button does, which heals
        drift in place and never clobbers a staged draft. Everything else
        only reads.
        """
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            RegisteredApp.objects.filter(slug=app_slug, organization_id=org_id, deleted_at__isnull=True)
            .select_related("organization")
            .first()
            if org_id is not None
            else None
        )
        if app is None:
            # An empty report rather than an error: the field is non-null and
            # this is a diagnostic surface, so "nothing to report" is the
            # honest answer for an app this caller cannot see.
            return AppDoctorReportType(healthy=False, checks=[])
        return build_app_doctor_report(app)

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=registry_organization_scope(Permission.APP_READ))
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
