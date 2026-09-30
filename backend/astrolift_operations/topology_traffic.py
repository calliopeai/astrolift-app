"""Measured intra-app Istio HTTP/gRPC edges, with authorization before transport."""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime
from enum import Enum

import strawberry
from django.db.models import Q
from django.utils import timezone

from astrolift_identity.operation_context import OperationContext, environment_context
from astrolift_lifecycle.models import AppEnvironment
from astrolift_operations import prometheus_client as prom
from astrolift_operations.scopes import apps
from astrolift_registry.models import Workload
from astrolift_services.scopes import assert_provider_cluster
from core.app_deploy import namespace_for_environment
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind

MAX_ENVIRONMENTS = 8
MAX_EDGES = 256
MAX_SAMPLES = 120
MAX_SERIES = 2048
MAX_BODY_BYTES = 2 * 1024 * 1024


@strawberry.enum
class TopologyTrafficStatus(Enum):
    AVAILABLE = "AVAILABLE"
    NO_DATA = "NO_DATA"
    UNCONFIGURED = "UNCONFIGURED"
    UNAVAILABLE = "UNAVAILABLE"
    PARTIAL = "PARTIAL"


@strawberry.type
class TopologyTrafficSample:
    timestamp: datetime
    request_rate: float
    error_rate: float


@strawberry.type
class TopologyTrafficEdge:
    source_workload_id: strawberry.ID
    source_workload_name: str
    destination_workload_id: strawberry.ID
    destination_workload_name: str
    request_rate: float
    error_rate: float
    error_ratio: float
    samples: list[TopologyTrafficSample]


@strawberry.type
class TopologyEnvironmentTraffic:
    environment_id: strawberry.ID
    environment_name: str
    namespace: str
    status: TopologyTrafficStatus
    reason: str | None
    edges: list[TopologyTrafficEdge]
    truncated: bool = False


@strawberry.type
class TopologyTraffic:
    app_slug: str
    start: datetime
    end: datetime
    step_seconds: int
    status: TopologyTrafficStatus
    environments: list[TopologyEnvironmentTraffic]
    edge_limit: int = MAX_EDGES
    sample_limit: int = MAX_SAMPLES
    environment_limit: int = MAX_ENVIRONMENTS
    source: str = "istio"
    truncated: bool = False


def topology_operations(args):
    app = apps().filter(slug=args.get("app_slug")).first()
    if app is None:
        return (OperationContext(),)
    rows = AppEnvironment.objects.filter(registered_app=app).select_related("tenant_cluster")
    if args.get("environment_name") is not None:
        rows = rows.filter(name=args["environment_name"])
    return tuple(
        environment_context(env)
        if env.tenant_cluster_id
        else OperationContext(environment=env.name, approvals=0)
        for env in rows.order_by("pk")[: MAX_ENVIRONMENTS + 1]
    ) or (OperationContext(),)


def query_topology_traffic(app_slug, start, end, environment_name):
    if timezone.is_naive(start) or timezone.is_naive(end):
        raise ValueError("start and end must include a timezone")
    start_unix, end_unix = int(start.timestamp()), int(end.timestamp())
    if not 60 <= end_unix - start_unix <= 86400 or end > timezone.now():
        raise ValueError("traffic window must be 1 minute to 24 hours and must not end in the future")
    step = max(30, math.ceil((end_unix - start_unix) / (MAX_SAMPLES - 1)))
    app = apps().filter(slug=app_slug).first()
    if app is None:
        return None
    rows = (
        AppEnvironment.objects.filter(registered_app=app)
        .select_related("tenant_cluster", "registered_app__organization")
        .order_by("pk")
    )
    if environment_name is not None:
        rows = rows.filter(name=environment_name)
    environments = list(rows[: MAX_ENVIRONMENTS + 1])
    if len(environments) > MAX_ENVIRONMENTS:
        raise ValueError(f"select one environment for apps with more than {MAX_ENVIRONMENTS} environments")
    if environment_name is not None and not environments:
        raise ValueError("environment not found")
    # Validate every provider target before the first HTTP or cache read.
    for env in environments:
        if env.tenant_cluster_id:
            assert_provider_cluster(env.tenant_cluster, permission=Permission.APP_READ_METRICS)
            if not env.tenant_cluster.is_active:
                raise PermissionDenied(
                    Permission.APP_READ_METRICS,
                    PermissionScope(ScopeKind.APP, app.pk),
                    "metrics target is inactive",
                )
    workloads = list(
        Workload.objects.filter(
            Q(kind__in=["deployment", "statefulset", "workflow"]) | Q(kind="agent", run_family="service"),
            registered_app=app,
        )
        .order_by("pk")
        .values_list("name", "guid")[:65]
    )
    if len(workloads) > 64:
        raise ValueError("traffic supports at most 64 standing workloads per app")
    name_counts = Counter(name for name, _ in workloads)
    workload_map = {name: str(guid) for name, guid in workloads if name_counts[name] == 1}
    results = [_environment_traffic(env, workload_map, start_unix, end_unix, step) for env in environments]
    remaining = MAX_EDGES
    for result in results:
        if len(result.edges) > remaining:
            result.edges = result.edges[:remaining]
            result.truncated = True
        remaining -= len(result.edges)
    truncated = any(result.truncated for result in results)
    states = {result.status for result in results}
    status = (
        next(iter(states))
        if len(states) == 1
        else (TopologyTrafficStatus.PARTIAL if states else TopologyTrafficStatus.UNCONFIGURED)
    )
    if truncated:
        status = TopologyTrafficStatus.PARTIAL
    return TopologyTraffic(
        app_slug=app_slug,
        start=start,
        end=end,
        step_seconds=step,
        status=status,
        environments=results,
        truncated=truncated,
    )


def _environment_traffic(env, workload_map, start, end, step):
    namespace = namespace_for_environment(env)
    result = TopologyEnvironmentTraffic(
        environment_id=strawberry.ID(str(env.guid)),
        environment_name=env.name,
        namespace=namespace,
        status=TopologyTrafficStatus.UNCONFIGURED,
        reason=None,
        edges=[],
    )
    cluster = env.tenant_cluster
    cfg = cluster.provider_config if cluster else {}
    cfg = cfg or {}
    endpoint = str(cfg.get("prometheus_endpoint") or "").strip()
    kind = str(cfg.get("observability_kind") or "prometheus").lower()
    if not endpoint or kind not in {"prometheus", "otel"}:
        result.reason = "Istio-compatible Prometheus endpoint is not configured"
        return result
    names = dict(workload_map)
    others = list(
        AppEnvironment.all_objects.filter(tenant_cluster=cluster)
        .exclude(pk=env.pk)
        .select_related("registered_app__organization")
    )
    same_namespace = [other for other in others if namespace_for_environment(other) == namespace]
    if any(other.registered_app_id == env.registered_app_id for other in same_namespace):
        result.reason = "environments share a namespace and Istio cannot distinguish their traffic"
        return result
    sibling_names = set(
        Workload.all_objects.filter(
            registered_app_id__in={other.registered_app_id for other in same_namespace}
        ).values_list("name", flat=True)
    )
    names = {name: guid for name, guid in names.items() if name not in sibling_names}
    if not names:
        result.status = TopologyTrafficStatus.NO_DATA
        result.reason = "no unambiguous standing workload names"
        return result
    try:
        namespace = prom.sanitize_label_value(namespace)
        for name in names:
            prom.sanitize_label_value(name)
        # Kubernetes workload names contain only DNS label characters. Quote the
        # selectors independently of the PromQL so stored data cannot inject it.
        import re

        matcher = json.dumps("|".join(re.escape(name) for name in sorted(names)))
        ns = json.dumps(namespace)
        query = (
            "sum by (source_workload_namespace, source_workload, destination_workload_namespace, destination_workload, response_code, grpc_response_status) "
            f'(rate(istio_requests_total{{reporter="destination",source_workload_namespace={ns},destination_workload_namespace={ns},source_workload=~{matcher},destination_workload=~{matcher}}}[5m]))'
        )
        rows = prom.query_range(
            endpoint=endpoint,
            query=query,
            start_unix=start,
            end_unix=end,
            step_seconds=step,
            timeout=5.0,
            auth=cfg.get("prometheus_auth"),
            strict=True,
            max_series=MAX_SERIES,
            max_samples=MAX_SAMPLES,
            max_response_bytes=MAX_BODY_BYTES,
        )
        result.edges, result.truncated = _edges(rows, names, namespace)
    except (prom.PrometheusError, ValueError, TypeError, OverflowError):
        result.status = TopologyTrafficStatus.UNAVAILABLE
        result.reason = "traffic backend failed or returned invalid or oversized data"
        return result
    result.status = TopologyTrafficStatus.AVAILABLE if result.edges else TopologyTrafficStatus.NO_DATA
    return result


def _edges(
    rows: tuple[prom.RangeQueryResult, ...], names: dict[str, str], namespace: str
) -> tuple[list[TopologyTrafficEdge], bool]:
    points: dict[tuple[str, str], dict[float, tuple[float, float]]] = {}
    for row in rows:
        labels = row.metric_labels
        if (
            labels.get("source_workload_namespace") != namespace
            or labels.get("destination_workload_namespace") != namespace
        ):
            continue
        source, destination = labels.get("source_workload"), labels.get("destination_workload")
        if source is None or destination is None or source not in names or destination not in names:
            continue
        code, grpc = labels.get("response_code", ""), labels.get("grpc_response_status", "")
        if grpc not in ("", "-"):
            if not grpc.isdecimal() or not 0 <= int(grpc) <= 16:
                raise prom.PrometheusQueryError("unknown gRPC response status")
            failed = int(grpc) != 0
        else:
            if not code.isdecimal() or not 100 <= int(code) <= 599:
                raise prom.PrometheusQueryError("unknown HTTP response status")
            failed = int(code) >= 400
        edge_points = points.setdefault((source, destination), {})
        for timestamp, value in row.values:
            total, errors = edge_points.get(timestamp, (0.0, 0.0))
            total += value
            errors += value if failed else 0.0
            if not math.isfinite(total) or not math.isfinite(errors):
                raise prom.PrometheusQueryError("nonfinite aggregated rate")
            edge_points[timestamp] = total, errors
            if len(edge_points) > MAX_SAMPLES:
                raise prom.PrometheusQueryError("edge exceeds the sample limit")
    out = []
    for (source, destination), points_by_time in sorted(points.items()):
        if not points_by_time:
            continue
        samples = [
            TopologyTrafficSample(
                timestamp=datetime.fromtimestamp(ts, tz=timezone.get_default_timezone()),
                request_rate=total,
                error_rate=errors,
            )
            for ts, (total, errors) in sorted(points_by_time.items())
        ]
        requests, errors = sum(p.request_rate for p in samples), sum(p.error_rate for p in samples)
        if not math.isfinite(requests) or not math.isfinite(errors):
            raise prom.PrometheusQueryError("nonfinite aggregate")
        out.append(
            TopologyTrafficEdge(
                source_workload_id=strawberry.ID(names[source]),
                source_workload_name=source,
                destination_workload_id=strawberry.ID(names[destination]),
                destination_workload_name=destination,
                request_rate=requests / len(samples),
                error_rate=errors / len(samples),
                error_ratio=errors / requests if requests else 0.0,
                samples=samples,
            )
        )
    return out[:MAX_EDGES], len(out) > MAX_EDGES
