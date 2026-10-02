"""Join usage and limits for a captured set of physical containers."""

import json
import math
import re
from dataclasses import dataclass

from astrolift_observability.prom_queries import pick_rate_window
from astrolift_operations import prometheus_client
from astrolift_operations.prometheus_client import PrometheusError, sanitize_label_value


class ResourceUnavailable(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ResourceMeasurement:
    samples: tuple
    usage: tuple
    limits: tuple
    promql: str
    start: int


def _selector(member, namespace, *, resource=None):
    labels = {"namespace": namespace, "pod": member.pod_name, "container": member.container_name}
    if resource:
        labels.update(uid=member.pod_uid, resource=resource)
    parts = [f"{key}={json.dumps(sanitize_label_value(value))}" for key, value in sorted(labels.items())]
    if not resource:
        pattern = ".*[/_-]" + member.container_id + r"(\.scope)?"
        parts.append("id=~" + json.dumps(pattern))
    return "{" + ",".join(parts) + "}"


def resource_queries(members, namespace, resource, range_seconds):
    window = pick_rate_window(range_seconds)
    usages, limits = [], []
    for member in members:
        selector = _selector(member, namespace)
        if resource == "cpu":
            usages.append(f"rate(container_cpu_usage_seconds_total{selector}[{window}])")
        else:
            usages.append(f"container_memory_working_set_bytes{selector}")
        limits.append(f"kube_pod_container_resource_limits{_selector(member, namespace, resource=resource)}")
    return " or ".join(usages), " or ".join(limits)


def _series(rows, members, namespace, *, limits, resource):
    expected = {(member.pod_name, member.container_name): member for member in members}
    found = {}
    for row in rows:
        labels = row.metric_labels
        key = (labels.get("pod"), labels.get("container"))
        member = expected.get(key)
        if member is None or labels.get("namespace") != namespace:
            raise ResourceUnavailable("INVALID_DATA")
        if key in found:
            raise ResourceUnavailable("AMBIGUOUS_SERIES")
        if limits:
            if (
                labels.get("uid") != member.pod_uid
                or labels.get("resource") != resource
                or labels.get("unit") != ("core" if resource == "cpu" else "byte")
            ):
                raise ResourceUnavailable("INVALID_DATA")
        elif not re.fullmatch(
            ".*[/_-]" + re.escape(member.container_id) + r"(\.scope)?", labels.get("id", "")
        ):
            raise ResourceUnavailable("INVALID_DATA")
        points = {}
        for ts, value in row.values:
            if not math.isfinite(value) or value < 0 or ts in points:
                raise ResourceUnavailable("INVALID_DATA")
            points[ts] = value
        found[key] = points
    if not found:
        raise ResourceUnavailable("MISSING_LIMITS" if limits else "MISSING_USAGE")
    if set(found) != set(expected):
        raise ResourceUnavailable("PARTIAL_DATA")
    return found


def measure_resource(
    *, endpoint, namespace, members, resource, range_seconds, start_unix, end_unix, step_seconds
):
    window = pick_rate_window(range_seconds)
    window_seconds = int(window[:-1]) * {"m": 60, "h": 3600}[window[-1]] if resource == "cpu" else 0
    start = max(start_unix, math.ceil(max(member.started_at for member in members)) + window_seconds)
    if start >= end_unix:
        raise ResourceUnavailable("NO_DATA_YET")
    usage_query, limits_query = resource_queries(members, namespace, resource, range_seconds)
    kwargs = {
        "endpoint": endpoint,
        "start_unix": start,
        "end_unix": end_unix,
        "step_seconds": step_seconds,
        "strict": True,
        "max_series": len(members),
        "max_samples": 400,
        "max_response_bytes": 2_000_000,
        "request_method": "POST",
        "max_request_bytes": 131_072,
    }
    try:
        usage = _series(
            prometheus_client.query_range(query=usage_query, **kwargs),
            members,
            namespace,
            limits=False,
            resource=resource,
        )
        limits = _series(
            prometheus_client.query_range(query=limits_query, **kwargs),
            members,
            namespace,
            limits=True,
            resource=resource,
        )
    except PrometheusError as exc:
        raise ResourceUnavailable("QUERY_ERROR") from exc
    times = {ts for points in usage.values() for ts in points} | {
        ts for points in limits.values() for ts in points
    }
    if not times:
        raise ResourceUnavailable("NO_DATA_YET")
    samples, usages, bounds = [], [], []
    for ts in sorted(times):
        if any(ts not in points for points in [*usage.values(), *limits.values()]):
            raise ResourceUnavailable("PARTIAL_DATA")
        if any(points[ts] <= 0 for points in limits.values()):
            raise ResourceUnavailable("MISSING_LIMITS")
        current, bound = (
            sum(points[ts] for points in usage.values()),
            sum(points[ts] for points in limits.values()),
        )
        if not math.isfinite(current) or not math.isfinite(bound):
            raise ResourceUnavailable("INVALID_DATA")
        ratio = current / bound
        if not math.isfinite(ratio):
            raise ResourceUnavailable("INVALID_DATA")
        samples.append((ts, ratio))
        usages.append((ts, current))
        bounds.append((ts, bound))
    return ResourceMeasurement(
        tuple(samples),
        tuple(usages),
        tuple(bounds),
        f"sum({usage_query}) / (sum({limits_query}) > 0)",
        start,
    )
