"""Read-only resolver for the App > Logs historical query (#482).

Separate file from ``queries.py`` (which owns the Prometheus-derived
golden-signals surface) because the log query has its own dispatch
path — ``core.cluster_log_query`` resolves the per-cluster log
aggregator driver, completely independent of the Prometheus + URL probe
infrastructure.

The resolver is permission-gated on ``APP_READ_LOGS`` + ``@tenant_scoped``,
mirrors the empty-state behavior of the other observability resolvers
(degrades to empty page rather than raising), and clamps caller-supplied
bounds so a malicious operator can't force a multi-day full-fleet pull.
"""

from __future__ import annotations

import datetime as dt
import logging
import re

import strawberry
from strawberry.types import Info

from astrolift_lifecycle.models import AppEnvironment
from astrolift_observability.schema.types import AppLogLine, AppLogPage
from astrolift_registry.models import RegisteredApp
from core import cluster_log_query
from core.cluster_observability import namespace_for_app
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission

logger = logging.getLogger(__name__)

# Sane upper bound on a single page — operators rarely page past 1000
# lines before refining the query, and Loki's default per-page cap is
# 1000 too. The cursor lets the FE keep paging if the operator really
# wants the whole window.
_MIN_LIMIT = 1
_MAX_LIMIT = 5000
_DEFAULT_LIMIT = 500

# Total query window ceiling — guards against the operator asking for
# "the last year" by accident. The aggregator's actual retention is the
# tighter bound in practice but we still enforce the platform-level
# ceiling so a missing retention config can't lead to a multi-day scan.
_MAX_WINDOW_SECONDS = 31 * 86400

# Heuristic level classifier — same patterns as the FE LogViewer
# helper. Server-side classification lets the historical-mode level
# chip filter work without re-running the regex on every render.
_LEVEL_PATTERNS: dict[str, re.Pattern[str]] = {
    "error": re.compile(r"\b(error|err|fatal|crit|critical|exception|panic)\b", re.IGNORECASE),
    "warn": re.compile(r"\b(warn|warning)\b", re.IGNORECASE),
    "info": re.compile(r"\b(info|notice)\b", re.IGNORECASE),
    "debug": re.compile(r"\b(debug|trace)\b", re.IGNORECASE),
}


def _classify_level(message: str, structured_level: str | None) -> str:
    """Pick the heuristic level the FE chips filter against.

    Structured-log labels (``level=error`` parsed by the aggregator)
    take precedence so an explicit operator-set level can't be
    overridden by an unrelated keyword in the message body.
    """
    if structured_level:
        lowered = structured_level.strip().lower()
        # Map common synonyms to the canonical set.
        if lowered in {"error", "err", "fatal", "crit", "critical", "exception", "panic"}:
            return "error"
        if lowered in {"warn", "warning"}:
            return "warn"
        if lowered in {"info", "notice", "informational"}:
            return "info"
        if lowered in {"debug", "trace"}:
            return "debug"
        # An unknown structured value still tells us the operator
        # cared about levels — surface it verbatim so the FE can
        # show it under "other".
        if lowered:
            return lowered
    for level, pattern in _LEVEL_PATTERNS.items():
        if pattern.search(message):
            return level
    return "other"


def _empty_page(historical_available: bool) -> AppLogPage:
    return AppLogPage(
        items=[],
        next_cursor="",
        reached_retention=False,
        historical_available=historical_available,
        total_count=0,
    )


def _resolve_cluster(*, app: RegisteredApp, environment_name: str | None):
    """Pick the cluster row to query against, matching the per-pod
    log subscription's resolution order so the historical surface
    sees the same cluster as the live tail."""
    if environment_name:
        env = (
            AppEnvironment.objects.select_related("tenant_cluster")
            .filter(
                registered_app=app,
                name=environment_name,
                deleted_at__isnull=True,
            )
            .first()
        )
        if env and env.tenant_cluster_id:
            return env.tenant_cluster
    # Fall back to any wired environment, then to the app's default.
    env = (
        AppEnvironment.objects.select_related("tenant_cluster")
        .filter(
            registered_app=app,
            deleted_at__isnull=True,
            tenant_cluster__isnull=False,
        )
        .order_by("created_at")
        .first()
    )
    if env and env.tenant_cluster_id:
        return env.tenant_cluster
    return getattr(app, "default_tenant_cluster", None)


def _normalize_window(
    since: dt.datetime,
    until: dt.datetime,
) -> tuple[dt.datetime, dt.datetime]:
    """Enforce ``since <= until`` and clamp the window to
    :data:`_MAX_WINDOW_SECONDS`. The clamp moves ``since`` forward so
    operators always see the most-recent slice of an over-wide
    request."""
    if until < since:
        since, until = until, since
    span = (until - since).total_seconds()
    if span > _MAX_WINDOW_SECONDS:
        since = until - dt.timedelta(seconds=_MAX_WINDOW_SECONDS)
    return since, until


def _clamp_limit(limit: int) -> int:
    if limit < _MIN_LIMIT:
        return _MIN_LIMIT
    if limit > _MAX_LIMIT:
        return _MAX_LIMIT
    return limit


def _ns_to_iso(ts: str) -> str:
    """Coerce a Loki-style nanosecond-int timestamp into ISO-8601.

    Other backends return ISO already — those pass through untouched.
    The FE relies on ISO so the Date() coercion in the LogViewer
    succeeds; falling back to the original string preserves backend
    fidelity when we can't parse it.
    """
    if not ts:
        return ""
    if ts.isdigit():
        try:
            seconds, ns = divmod(int(ts), 1_000_000_000)
            base = dt.datetime.fromtimestamp(seconds, tz=dt.UTC)
            # Tack the nanosecond remainder onto the microsecond field
            # so the ISO string still encodes sub-second precision.
            micros = ns // 1000
            return base.replace(microsecond=micros).isoformat()
        except (ValueError, OSError):
            return ts
    return ts


@strawberry.type
class LogHistoryQuery:
    @strawberry.field
    @require_permission(Permission.APP_READ_LOGS)
    @tenant_scoped()
    def astrolift_app_logs(
        self,
        info: Info,
        app_slug: str,
        since: dt.datetime,
        until: dt.datetime,
        environment_name: str | None = None,
        workload_slug: str | None = None,
        level: str | None = None,
        search: str | None = None,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> AppLogPage:
        """Paginated historical log lines for an app/env within
        ``[since, until]``.

        Backend dispatch:
          1. Look up the app for the current tenant.
          2. Pick the cluster (env-named, falling back to the app
             default — same as the live-tail subscription).
          3. Resolve the cluster's log-aggregator driver via
             ``core.cluster_log_query``. When no driver is configured
             the page returns ``historicalAvailable=False`` and the FE
             renders the "live tail only on this cluster" badge.
          4. Run a paginated query with the operator-supplied filters.

        Failure modes degrade to empty pages rather than raising — the
        operator already lives with the same contract on the live-tail
        path (a broken backend yields an empty stream, not a GraphQL
        error).
        """
        app = (
            RegisteredApp.objects.filter(
                slug=app_slug,
                deleted_at__isnull=True,
            )
            .only("id", "slug", "k8s_namespace", "organization", "default_tenant_cluster")
            .select_related("organization", "default_tenant_cluster")
            .first()
        )
        if app is None:
            return _empty_page(historical_available=False)

        cluster = _resolve_cluster(app=app, environment_name=environment_name)
        if cluster is None or not getattr(cluster, "is_active", True):
            return _empty_page(historical_available=False)

        since, until = _normalize_window(since, until)
        bounded_limit = _clamp_limit(limit)

        namespace = namespace_for_app(app)
        try:
            page = cluster_log_query.query_app_logs(
                cluster=cluster,
                namespace=namespace,
                app_slug=app.slug,
                workload_slug=workload_slug,
                since_iso=since.isoformat(),
                until_iso=until.isoformat(),
                limit=bounded_limit,
                cursor=cursor or "",
                level=level,
                search=search,
            )
        except Exception:
            # Aggregator transport errors land here. Match the live
            # tail's "swallow + render empty" contract; the operator
            # already sees the cluster's health on the events panel.
            logger.exception(
                "astrolift_app_logs: aggregator query raised for app %s",
                app.slug,
            )
            return _empty_page(historical_available=True)

        if page is None:
            return _empty_page(historical_available=False)

        items: list[AppLogLine] = []
        for raw in page.items:
            classified = _classify_level(raw.message, raw.level)
            # Optional client-side level / search filter for backends
            # that didn't push the filter down themselves. We re-run
            # the filter so the contract is uniform regardless of
            # which aggregator is wired.
            if level and classified != level.strip().lower():
                continue
            if search and search.lower() not in raw.message.lower():
                continue
            items.append(
                AppLogLine(
                    pod_name=raw.pod,
                    container=raw.container,
                    timestamp=_ns_to_iso(raw.timestamp),
                    message=raw.message,
                    level=classified,
                    stream="stdout",
                )
            )

        return AppLogPage(
            items=items,
            next_cursor=page.next_cursor,
            reached_retention=page.reached_retention,
            historical_available=True,
            total_count=len(items),
        )
