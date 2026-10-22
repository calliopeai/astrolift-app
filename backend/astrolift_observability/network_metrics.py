"""Pod network I/O for one app environment or one verified workload (#2271).

The kubelet's cAdvisor counts a pod's network once, on the pod sandbox that
holds its network namespace; the app containers share that namespace and
report none. Each series is one sandbox interface, labeled with the pod's
namespace and name, and with the sandbox cgroup path in ``id``. That path
contains the pod UID: ``.../pod<uid>/<sandbox>`` under the cgroupfs driver,
``...-pod<uid with underscores>.slice/...`` under systemd.

An app environment reads its own namespace (identity basis ``NAMESPACE``).
A workload reads only its verified current pods: every series must carry
the namespace, the pod name and that pod's UID, and every pod must report
at every step, or the direction is unavailable. A workload never falls back
to the namespace total, and an empty or failed read is never a zero.
"""

import dataclasses
import datetime as dt
import json
import math
import re
from uuid import UUID

from astrolift_drivers.registry import DriverNotFound
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.visibility import cluster_owned_and_live
from astrolift_observability.prom_queries import pick_step_seconds
from astrolift_observability.schema.types import (
    AppNetworkMetrics,
    AppNetworkSeries,
    GoldenSignalIdentityBasis,
    GoldenSignalMeasurement,
    GoldenSignalScope,
    GoldenSignalSource,
    GoldenSignalTarget,
    GoldenSignalUnavailableReason,
    MetricContainerIdentity,
    NetworkDirection,
    TimeSeriesPoint,
)
from astrolift_observability.workload_resources import ResourceUnavailable, rate_window
from astrolift_operations import prometheus_client
from astrolift_operations.prometheus_client import PrometheusError, sanitize_label_value
from astrolift_registry.models import Workload
from core.cluster_observability import _auth_for_cluster, _driver_for_cluster, namespace_for_environment
from core.schema.enums import ObservabilityPanelReason

_COUNTERS = {
    NetworkDirection.RECEIVE: "container_network_receive_bytes_total",
    NetworkDirection.TRANSMIT: "container_network_transmit_bytes_total",
}
UNIT = "bytes_per_second"

_Reason = GoldenSignalUnavailableReason
_Panel = ObservabilityPanelReason
# Target and endpoint problems are configuration; every reason not listed
# (ownership, membership, partial or ambiguous data, read failures) is ERROR.
_PANEL_REASON = {
    _Reason.NOT_CONFIGURED: _Panel.NOT_CONFIGURED,
    _Reason.ENVIRONMENT_NOT_FOUND: _Panel.NOT_CONFIGURED,
    _Reason.WORKLOAD_NOT_FOUND: _Panel.NOT_CONFIGURED,
    _Reason.TARGET_NOT_OWNED: _Panel.NOT_CONFIGURED,
    _Reason.NOT_SUPPORTED_BY_PROVIDER: _Panel.NOT_SUPPORTED_BY_PROVIDER,
    _Reason.NO_PODS: _Panel.NO_DATA_YET,
    _Reason.NO_DATA_YET: _Panel.NO_DATA_YET,
}


def panel_reason(reason: GoldenSignalUnavailableReason) -> ObservabilityPanelReason:
    return _PANEL_REASON.get(reason, _Panel.ERROR)


def _reason(value) -> GoldenSignalUnavailableReason:
    try:
        return _Reason[value]
    except (KeyError, TypeError):
        return _Reason.PROVIDER_ERROR


def _window_seconds(window: str) -> int:
    return int(window[:-1]) * {"m": 60, "h": 3600}[window[-1]]


def cgroup_pattern(uid: str) -> str:
    """The ``id`` of a cgroup inside the pod's own cgroup, under either driver."""
    return ".*pod(" + uid + "|" + uid.replace("-", "_") + r"\.slice)/.+"


def _selector(namespace: str, pod: str, uid: str) -> str:
    labels = {"namespace": namespace, "pod": pod}
    parts = [f"{key}={json.dumps(sanitize_label_value(value))}" for key, value in sorted(labels.items())]
    parts.append("id=~" + json.dumps(cgroup_pattern(uid)))
    return "{" + ",".join(parts) + "}"


def _queries(counter: str, namespace: str, pods: dict[str, str] | None, window: str, seconds: int):
    """The rate and window-total expressions for one direction."""
    if pods is None:
        match = "{namespace=" + json.dumps(sanitize_label_value(namespace)) + "}"
        return (
            f"sum(rate({counter}{match}[{window}]))",
            f"sum(increase({counter}{match}[{seconds}s]))",
        )
    selectors = [_selector(namespace, pod, uid) for pod, uid in sorted(pods.items())]
    return (
        "sum by (namespace, pod, id) ("
        + " or ".join(f"rate({counter}{selector}[{window}])" for selector in selectors)
        + ")",
        "sum by (namespace, pod, id) ("
        + " or ".join(f"increase({counter}{selector}[{seconds}s])" for selector in selectors)
        + ")",
    )


def _read(endpoint: str, query: str, *, start: int, end: int, step: int, max_series: int):
    try:
        return prometheus_client.query_range(
            endpoint=endpoint,
            query=query,
            start_unix=start,
            end_unix=end,
            step_seconds=step,
            strict=True,
            max_series=max_series,
            max_samples=400,
            max_response_bytes=2_000_000,
            request_method="POST",
            max_request_bytes=131_072,
        )
    except PrometheusError as exc:
        raise ResourceUnavailable("QUERY_ERROR") from exc


def _points(row) -> dict[float, float]:
    points: dict[float, float] = {}
    for ts, value in row.values:
        if not math.isfinite(value) or value < 0 or ts in points:
            raise ResourceUnavailable("INVALID_DATA")
        points[ts] = value
    return points


def _by_pod(rows, namespace: str, pods: dict[str, str]) -> dict[str, dict[float, float]]:
    """Each verified pod's points; a series of any other pod or incarnation refuses the read."""
    found: dict[str, dict[float, float]] = {}
    for row in rows:
        labels = row.metric_labels
        pod = labels.get("pod", "")
        uid = pods.get(pod)
        if (
            uid is None
            or labels.get("namespace") != namespace
            or not re.fullmatch(cgroup_pattern(uid), labels.get("id", ""))
        ):
            raise ResourceUnavailable("INVALID_DATA")
        if pod in found:
            raise ResourceUnavailable("AMBIGUOUS_SERIES")
        found[pod] = _points(row)
    if found and set(found) != set(pods):
        raise ResourceUnavailable("PARTIAL_DATA")
    return found


def _sum_complete(series: list[dict[float, float]]) -> list[tuple[float, float]]:
    times = sorted({ts for points in series for ts in points})
    out = []
    for ts in times:
        if any(ts not in points for points in series):
            raise ResourceUnavailable("PARTIAL_DATA")
        total = math.fsum(points[ts] for points in series)
        if not math.isfinite(total):
            raise ResourceUnavailable("INVALID_DATA")
        out.append((ts, total))
    return out


def _window_total(series: list[dict[float, float]]) -> float:
    """The bytes moved: every series' latest evaluation, which they must share.

    The latest, not ``end`` itself: a backend may align the evaluation steps.
    """
    lasts = {max(points, default=None) for points in series}
    if not series or None in lasts or len(lasts) != 1:
        raise ResourceUnavailable("PARTIAL_DATA")
    last = lasts.pop()
    total = math.fsum(points[last] for points in series)
    if not math.isfinite(total):
        raise ResourceUnavailable("INVALID_DATA")
    return total


def _measure(endpoint, rate_query, total_query, *, namespace, pods, start, end, step):
    """``(samples, total_bytes)`` of one direction, or ResourceUnavailable."""
    max_series = len(pods) if pods is not None else 1
    rates = _read(endpoint, rate_query, start=start, end=end, step=step, max_series=max_series)
    totals = _read(endpoint, total_query, start=end - step, end=end, step=step, max_series=max_series)
    if pods is None:
        rate_series = [_points(row) for row in rates]
        total_series = [_points(row) for row in totals]
    else:
        rate_series = list(_by_pod(rates, namespace, pods).values())
        total_series = list(_by_pod(totals, namespace, pods).values())
    samples = _sum_complete(rate_series)
    if not samples:
        raise ResourceUnavailable("NO_DATA_YET")
    return samples, _window_total(total_series)


def _members(*, cluster, namespace, app, environment, workload):
    """The workload's verified current containers (#2263), or ResourceUnavailable."""
    try:
        driver = _driver_for_cluster(cluster)
        read = getattr(driver, "metric_containers", None)
        if read is None:
            raise ResourceUnavailable("NOT_SUPPORTED_BY_PROVIDER")
        members = read(
            auth=_auth_for_cluster(cluster),
            namespace=namespace,
            app_slug=app.slug,
            app_id=str(app.guid),
            environment_id=str(environment.guid),
            workload_slug=workload.slug,
            workload_id=str(workload.guid),
            workload_kind=workload.kind,
        )
    except ResourceUnavailable:
        raise
    except NotImplementedError as exc:
        raise ResourceUnavailable("NOT_SUPPORTED_BY_PROVIDER") from exc
    except Exception as exc:
        if isinstance(exc.__cause__, DriverNotFound):
            raise ResourceUnavailable("NOT_SUPPORTED_BY_PROVIDER") from exc
        raise ResourceUnavailable(_reason(getattr(exc, "reason", None)).name) from exc
    if not members:
        raise ResourceUnavailable("NO_PODS")
    return tuple(members)


def _pods(members) -> dict[str, str]:
    """Pod name to canonical pod UID for the verified members."""
    pods: dict[str, str] = {}
    for member in members:
        try:
            uid = str(UUID(str(member.pod_uid)))
        except ValueError as exc:
            raise ResourceUnavailable("OWNERSHIP_UNVERIFIED") from exc
        if pods.setdefault(member.pod_name, uid) != uid:
            raise ResourceUnavailable("OWNERSHIP_UNVERIFIED")
    return pods


def _unavailable(series: AppNetworkSeries, reason: GoldenSignalUnavailableReason) -> AppNetworkSeries:
    return dataclasses.replace(
        series,
        samples=[],
        total_bytes=None,
        reason=panel_reason(reason),
        measurement=dataclasses.replace(series.measurement, available=False, unavailable_reason=reason),
    )


def _envelope_reason(series: list[AppNetworkSeries]) -> ObservabilityPanelReason:
    if any(row.samples for row in series):
        return _Panel.OK
    reasons = {row.reason for row in series}
    if _Panel.ERROR in reasons:
        return _Panel.ERROR
    return reasons.pop() if len(reasons) == 1 else _Panel.NO_DATA_YET


def network_metrics_for_app(*, app, environment_name, workload_slug, seconds, now) -> AppNetworkMetrics:
    """``app`` is the caller's own live app, or None: no series at all."""
    end = int(now.timestamp())
    start = end - seconds
    step = pick_step_seconds(seconds)
    window = rate_window(seconds)
    window_seconds = _window_seconds(window)

    def result(rows):
        return AppNetworkMetrics(
            reason=_envelope_reason(rows) if rows else _Panel.NOT_CONFIGURED,
            range_seconds=seconds,
            step_seconds=step,
            rate_window_seconds=window_seconds,
            sourced_at=now,
            series=rows,
        )

    if app is None:
        return result([])
    qs = AppEnvironment.objects.filter(registered_app_id=app.pk, deleted_at__isnull=True).select_related(
        "tenant_cluster"
    )
    if environment_name:
        qs = qs.filter(name=environment_name)
    environment = qs.order_by("name").first()
    cluster = environment.tenant_cluster if environment else None
    workload = (
        Workload.objects.filter(registered_app_id=app.pk, deleted_at__isnull=True, slug=workload_slug).first()
        if workload_slug
        else None
    )
    if environment:
        environment.registered_app = app
    namespace = namespace_for_environment(environment) if environment else None
    target = GoldenSignalTarget(
        organization_id=str(app.organization.guid),
        app_id=str(app.guid),
        app_slug=app.slug,
        environment_id=str(environment.guid) if environment else None,
        environment_name=environment.name if environment else None,
        cluster_id=str(cluster.guid) if cluster else None,
        namespace=namespace,
        workload_id=str(workload.guid) if workload else None,
        workload_slug=workload.slug if workload else workload_slug,
    )
    series = [
        AppNetworkSeries(
            direction=direction,
            unit=UNIT,
            samples=[],
            total_bytes=None,
            promql="",
            measurement=GoldenSignalMeasurement(
                effective_scope=GoldenSignalScope.WORKLOAD
                if workload_slug
                else GoldenSignalScope.APP_ENVIRONMENT,
                source=GoldenSignalSource.CADVISOR,
                identity_basis=GoldenSignalIdentityBasis.UNRESOLVED
                if workload_slug
                else GoldenSignalIdentityBasis.NAMESPACE,
                available=False,
                target=target,
            ),
            reason=_Panel.NO_DATA_YET,
        )
        for direction in _COUNTERS
    ]
    pods = None
    measured_from = start
    try:
        if environment is None:
            raise ResourceUnavailable("ENVIRONMENT_NOT_FOUND")
        if not cluster_owned_and_live(cluster, app.organization_id):
            raise ResourceUnavailable("TARGET_NOT_OWNED")
        if workload_slug and workload is None:
            raise ResourceUnavailable("WORKLOAD_NOT_FOUND")
        config, capabilities = cluster.provider_config or {}, cluster.capabilities or {}
        endpoint = str(
            config.get("prometheus_endpoint") or capabilities.get("prometheus_endpoint") or ""
        ).strip()
        if not endpoint:
            raise ResourceUnavailable("NOT_CONFIGURED")
        if workload is not None:
            members = _members(
                cluster=cluster, namespace=namespace, app=app, environment=environment, workload=workload
            )
            observed = dt.datetime.now(dt.UTC)
            pods = _pods(members)
            # Every current pod exists, with a full rate window, from here on.
            measured_from = max(
                start, math.ceil(max(member.started_at for member in members)) + window_seconds
            )
            for row in series:
                row.measurement.identity_basis = GoldenSignalIdentityBasis.VERIFIED_RUNTIME
                row.measurement.membership_observed_at = observed
                row.measurement.measurement_start = dt.datetime.fromtimestamp(measured_from, dt.UTC)
                row.measurement.containers = [
                    MetricContainerIdentity(
                        pod_name=member.pod_name,
                        pod_uid=member.pod_uid,
                        container_name=member.container_name,
                        container_id=member.container_id,
                    )
                    for member in members
                ]
            if measured_from >= end:
                raise ResourceUnavailable("NO_DATA_YET")
    except ResourceUnavailable as exc:
        return result([_unavailable(row, _reason(exc.reason)) for row in series])

    assert namespace is not None
    measured: list[AppNetworkSeries] = []
    for row in series:
        try:
            rate_query, total_query = _queries(
                _COUNTERS[row.direction], namespace, pods, window, end - measured_from
            )
        except PrometheusError:
            measured.append(_unavailable(row, _Reason.QUERY_ERROR))
            continue
        row.promql = rate_query
        try:
            samples, total = _measure(
                endpoint,
                rate_query,
                total_query,
                namespace=namespace,
                pods=pods,
                start=measured_from,
                end=end,
                step=step,
            )
        except ResourceUnavailable as exc:
            measured.append(_unavailable(row, _reason(exc.reason)))
            continue
        row.samples = [
            TimeSeriesPoint(ts=dt.datetime.fromtimestamp(ts, dt.UTC), value=value) for ts, value in samples
        ]
        row.total_bytes = total
        row.reason = _Panel.OK
        row.measurement.available = True
        measured.append(row)
    return result(measured)
