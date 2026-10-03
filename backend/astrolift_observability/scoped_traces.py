"""Trusted resource attribution is required before searching or fetching traces."""

import datetime as dt
import re
from dataclasses import replace

from astrolift_observability import trace_client
from astrolift_observability.schema.types import (
    AppTrace,
    AppTracePage,
    TelemetryScope,
    TraceSpan,
    TraceSpansResult,
)
from astrolift_observability.scope import (
    environment_namespace,
    placement_fingerprint,
    placement_is_current,
    resolve_environment,
)
from core.schema.enums import ObservabilityPanelReason as Reason

ATTRIBUTION = "collector-resource-v1"
MAX_LOOKBACK_SECONDS = 86400
MAX_TRACE_LIMIT = 20


def _window(since, until):
    now = int(dt.datetime.now(dt.UTC).timestamp())

    def seconds(value):
        if value == "now":
            return now
        relative = re.fullmatch(r"now-([0-9]{1,6})([smh])", value)
        if relative:
            return now - int(relative[1]) * {"s": 1, "m": 60, "h": 3600}[relative[2]]
        if re.fullmatch(r"[0-9]{1,12}", value):
            return int(value)
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Trace times must include a time zone")
        return int(stamp.timestamp())

    try:
        start, end = seconds(since), seconds(until)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Invalid trace query window") from exc
    now = int(dt.datetime.now(dt.UTC).timestamp())
    if not 0 <= start <= end <= now + 60 or end - start > MAX_LOOKBACK_SECONDS:
        raise ValueError("Trace query window must be ordered and no longer than 24 hours")
    return start, end


def _scope(app, env):
    return TelemetryScope(
        organization_id=str(app.organization.guid),
        app_id=str(app.guid),
        environment_id=str(env.guid),
        environment_name=env.name,
        cluster_id=str(env.tenant_cluster.guid),
        namespace=environment_namespace(env),
    )


def _attributes(scope):
    return {
        "astrolift.organization.id": scope.organization_id,
        "astrolift.app.id": scope.app_id,
        "astrolift.environment.id": scope.environment_id,
        "astrolift.cluster.id": scope.cluster_id,
        "k8s.namespace.name": scope.namespace,
    }


def _target(app, environment_name, environment_id=None):
    env = resolve_environment(app, environment_name, environment_id)
    if env is None:
        return None, None, None
    scope = _scope(app, env)
    config = env.tenant_cluster.provider_config or {}
    trace_config = config.get("trace_config") or {}
    if (
        config.get("trace_driver") != "tempo"
        or not isinstance(trace_config, dict)
        or trace_config.get("attribution") != ATTRIBUTION
    ):
        return env, scope, None
    driver = trace_client.driver_for_environment(env)
    return env, scope, driver


def _owned(spans, scope, trace_id):
    attrs = _attributes(scope)
    owned = [
        span
        for span in spans
        if span.trace_id == trace_id
        and re.fullmatch(r"[0-9a-f]{16}", span.span_id)
        and all(span.resource_attributes.get(key) == value for key, value in attrs.items())
    ]
    ids = {span.span_id for span in owned}
    return [
        replace(span, parent_span_id=span.parent_span_id if span.parent_span_id in ids else None)
        for span in owned
    ]


def _span_type(span):
    return TraceSpan(
        trace_id=span.trace_id,
        span_id=span.span_id,
        parent_span_id=span.parent_span_id,
        operation=span.operation,
        service=span.service,
        start_time=span.start_time,
        duration_ms=span.duration_ms,
        status_code=span.status_code,
        attributes=dict(span.attributes),
        resource_attributes=dict(span.resource_attributes),
    )


def trace_page(
    app,
    *,
    environment_name,
    since,
    until,
    service=None,
    status=None,
    limit=10,
    operation=None,
    min_duration_ms=None,
    environment_id=None,
):
    start, end = _window(since, until)
    if service is not None and (len(service) > 256 or any(ord(c) < 32 for c in service)):
        raise ValueError("Invalid trace service filter")
    if status not in {None, "OK", "ERROR"}:
        raise ValueError("Invalid trace status filter")
    if operation is not None and (len(operation) > 256 or any(ord(c) < 32 for c in operation)):
        raise ValueError("Invalid trace operation filter")
    if min_duration_ms is not None and not 0 <= min_duration_ms <= MAX_LOOKBACK_SECONDS * 1000:
        raise ValueError("Invalid trace duration filter")
    limit = min(max(limit, 1), MAX_TRACE_LIMIT)
    env, scope, driver = _target(app, environment_name, environment_id)
    if driver is None:
        return AppTracePage(reason=Reason.NOT_CONFIGURED, items=[], scope=scope, limit=limit)
    fingerprint = placement_fingerprint(app, env)
    try:
        candidates = driver.search_scoped(
            resource_attributes=_attributes(scope),
            since=start,
            until=end,
            service=service,
            status=status,
            operation=operation,
            min_duration_ms=min_duration_ms,
            limit=limit + 1,
        )
        items = []
        for trace_id in candidates[:limit]:
            spans = _owned(driver.get_trace(trace_id), scope, trace_id)
            if not spans:
                continue
            root = min(spans, key=lambda span: int(span.start_time))
            ends = [int(span.start_time) + int(span.duration_ms * 1_000_000) for span in spans]
            items.append(
                AppTrace(
                    trace_id=trace_id,
                    root_service=root.service,
                    root_operation=root.operation,
                    span_count=len(spans),
                    duration_ms=max(0, (max(ends) - int(root.start_time)) / 1_000_000),
                    status_code="ERROR"
                    if any(span.status_code == "ERROR" for span in spans)
                    else "OK"
                    if any(span.status_code == "OK" for span in spans)
                    else "UNSET",
                )
            )
        if not placement_is_current(app, env, fingerprint):
            return AppTracePage(reason=Reason.NOT_CONFIGURED, items=[], scope=None, limit=limit)
        reason = Reason.OK if items else Reason.ERROR if candidates else Reason.NO_DATA_YET
        return AppTracePage(
            reason=reason, items=items, scope=scope, limit=limit, truncated=len(candidates) > limit
        )
    except Exception:
        if not placement_is_current(app, env, fingerprint):
            return AppTracePage(reason=Reason.NOT_CONFIGURED, items=[], scope=None, limit=limit)
        return AppTracePage(reason=Reason.ERROR, items=[], scope=scope, limit=limit)


def trace_spans(app, *, environment_name, trace_id, since, until, environment_id=None):
    start, end = _window(since, until)
    if not re.fullmatch(r"[0-9a-f]{32}", trace_id):
        raise ValueError("Invalid trace identity")
    env, scope, driver = _target(app, environment_name, environment_id)
    if driver is None:
        return TraceSpansResult(reason=Reason.NOT_CONFIGURED, items=[], scope=scope)
    fingerprint = placement_fingerprint(app, env)
    try:
        # A supplied ID alone never authorizes a whole-cluster trace fetch.
        candidates = driver.search_scoped(
            resource_attributes=_attributes(scope), since=start, until=end, trace_id=trace_id, limit=1
        )
        if trace_id not in candidates:
            if not placement_is_current(app, env, fingerprint):
                return TraceSpansResult(reason=Reason.NOT_CONFIGURED, items=[], scope=None)
            return TraceSpansResult(reason=Reason.NO_DATA_YET, items=[], scope=scope)
        spans = _owned(driver.get_trace(trace_id), scope, trace_id)
        if not placement_is_current(app, env, fingerprint):
            return TraceSpansResult(reason=Reason.NOT_CONFIGURED, items=[], scope=None)
        return TraceSpansResult(
            reason=Reason.OK if spans else Reason.ERROR,
            items=[_span_type(span) for span in spans],
            scope=scope,
        )
    except Exception:
        if not placement_is_current(app, env, fingerprint):
            return TraceSpansResult(reason=Reason.NOT_CONFIGURED, items=[], scope=None)
        return TraceSpansResult(reason=Reason.ERROR, items=[], scope=scope)
