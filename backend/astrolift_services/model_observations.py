"""Identity-bound vLLM and Kubernetes observations; no namespace aggregates."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from enum import Enum

import strawberry
from django.utils import timezone

from astrolift_graphql import GUID
from astrolift_operations import prometheus_client as prom

MAX_SAMPLES = 120
MAX_BODY_BYTES = 1_048_576
_DNS = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_METRICS = {
    "prompt_tokens_per_second": ("tokens/s", "vllm"),
    "generation_tokens_per_second": ("tokens/s", "vllm"),
    "successful_requests_per_second": ("requests/s", "vllm"),
    "requests_running": ("count", "vllm"),
    "requests_waiting": ("count", "vllm"),
    "ttft_p95": ("seconds", "vllm"),
    "latency_p95": ("seconds", "vllm"),
    "queue_time_p95": ("seconds", "vllm"),
    "kv_cache_usage": ("fraction", "vllm"),
    "running_replicas": ("count", "kube_state_metrics"),
    "ready_replicas": ("count", "kube_state_metrics"),
    "cpu_usage": ("cores", "cadvisor"),
    "memory_usage": ("bytes", "cadvisor"),
    "cpu_requests": ("cores", "kube_state_metrics"),
    "memory_requests": ("bytes", "kube_state_metrics"),
    "gpu_utilization": ("fraction", "unsupported_device_pod_mapping"),
    "vram_usage": ("bytes", "unsupported_device_pod_mapping"),
}


@strawberry.enum
class ModelObservationState(Enum):
    AVAILABLE = "available"
    NO_DATA = "no_data"
    STALE = "stale"
    UNCONFIGURED = "unconfigured"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


@strawberry.type
class ModelObservationSample:
    timestamp: datetime
    value: float


@strawberry.type
class ModelMetricObservation:
    key: str
    unit: str
    source: str
    state: ModelObservationState
    observed_at: datetime | None
    value: float | None
    samples: list[ModelObservationSample]
    aggregation_window_seconds: int = 0


@strawberry.type
class ModelDeploymentMetrics:
    service_id: GUID
    cluster_id: GUID
    start: datetime
    end: datetime
    retrieved_at: datetime
    step_seconds: int
    metrics: list[ModelMetricObservation]
    scope: str = "deployment_aggregate_not_app_attributed"
    sample_limit: int = MAX_SAMPLES


def observation_window(start: datetime, end: datetime):
    if timezone.is_naive(start) or timezone.is_naive(end):
        raise ValueError("start and end must include a timezone")
    try:
        start_unix, end_unix = int(start.timestamp()), int(end.timestamp())
    except (OverflowError, ValueError) as exc:
        raise ValueError("invalid observation window") from exc
    if not 60 <= end_unix - start_unix <= 86400 or end > timezone.now():
        raise ValueError("observation window must be 1 minute to 24 hours and must not end in the future")
    return start_unix, end_unix, max(30, math.ceil((end_unix - start_unix) / (MAX_SAMPLES - 1)))


def _target(service):
    from _sdk.k8s_naming import app_namespace, dns_label
    from k8s_native.managed._handle import unpack

    cluster = service.effective_cluster
    parsed = unpack(service.backend_ref)
    if (
        parsed.kind != "model_endpoint"
        or parsed.cluster_id != str(cluster.guid)
        or not _DNS.fullmatch(parsed.namespace)
        or not _DNS.fullmatch(parsed.name)
    ):
        raise ValueError("recorded model target identity is unavailable")
    if service.organization_id:
        from _sdk.k8s_naming import cluster_model_namespace, cluster_model_resource_name

        namespace = cluster_model_namespace(
            organization_id=str(service.organization.guid),
            cluster_id=str(cluster.guid),
            managed_service_id=str(service.guid),
        )
        if parsed.name != cluster_model_resource_name(str(service.guid)):
            raise ValueError("recorded model resource name does not match its owner")
    else:
        owner = service.registered_app or service.project
        namespace = app_namespace(organization_slug=owner.organization.slug, app_slug=owner.slug)
        if parsed.name != dns_label(
            owner.slug, service.effective_environment_name, service.name or service.kind
        ):
            raise ValueError("recorded model target identity does not match its owner")
    if parsed.namespace != namespace:
        raise ValueError("recorded model namespace does not match its owner")
    return namespace, parsed.name


def observation_query(service_guid: str, namespace: str, name: str, *, keys=None) -> str:
    guid = prom.sanitize_label_value(service_guid)
    ns = prom.sanitize_label_value(namespace)
    resource_name = prom.sanitize_label_value(name)
    selector = f'{{managed_service="{guid}",namespace="{ns}",service="{resource_name}"}}'
    group = "managed_service,namespace,service"
    queries = {
        "prompt_tokens_per_second": f"sum by({group})(rate(vllm:prompt_tokens_total{selector}[5m]))",
        "generation_tokens_per_second": f"sum by({group})(rate(vllm:generation_tokens_total{selector}[5m]))",
        "successful_requests_per_second": f"sum by({group})(rate(vllm:request_success_total{selector}[5m]))",
        "requests_running": f"sum by({group})(vllm:num_requests_running{selector})",
        "requests_waiting": f"sum by({group})(vllm:num_requests_waiting{selector})",
        "kv_cache_usage": f"max by({group})(vllm:kv_cache_usage_perc{selector} or vllm:gpu_cache_usage_perc{selector})",
    }
    for key, metric in (
        ("ttft_p95", "time_to_first_token_seconds"),
        ("latency_p95", "e2e_request_latency_seconds"),
        ("queue_time_p95", "request_queue_time_seconds"),
    ):
        queries[key] = (
            f"histogram_quantile(0.95,sum by(le,{group})(rate(vllm:{metric}_bucket{selector}[5m]))) >= 0"
        )
    mapping = f'max by(namespace,pod)(kube_pod_labels{{namespace="{ns}",label_astrolift_io_managed_service_id="{guid}",label_app_kubernetes_io_instance="{resource_name}"}})'
    join = f" * on(namespace,pod) group_left() ({mapping})"
    pod_selector = f'{{namespace="{ns}",container="vllm"}}'
    pod_queries = {
        "running_replicas": f'max by(namespace,pod)(kube_pod_status_phase{{namespace="{ns}",phase="Running"}})',
        "ready_replicas": f'max by(namespace,pod)(kube_pod_status_ready{{namespace="{ns}",condition="true"}})',
        "cpu_usage": f"max by(namespace,pod)(rate(container_cpu_usage_seconds_total{pod_selector}[5m]))",
        "memory_usage": f"max by(namespace,pod)(container_memory_working_set_bytes{pod_selector})",
        "cpu_requests": f'max by(namespace,pod)(kube_pod_container_resource_requests{{namespace="{ns}",container="vllm",resource="cpu"}})',
        "memory_requests": f'max by(namespace,pod)(kube_pod_container_resource_requests{{namespace="{ns}",container="vllm",resource="memory"}})',
    }
    for key, query in pod_queries.items():
        aggregate = f"sum by(namespace)(({query}){join})"
        aggregate = f'label_replace({aggregate},"managed_service","{guid}","namespace",".*")'
        queries[key] = f'label_replace({aggregate},"service","{resource_name}","namespace",".*")'
    return " or ".join(
        f'label_replace(({query}),"astrolift_measurement","{key}","managed_service",".*")'
        for key, query in queries.items()
        if keys is None or key in keys
    )


def empty_observations(keys=None):
    return {
        key: ModelMetricObservation(
            key=key,
            unit=unit,
            source=source,
            state=ModelObservationState.UNSUPPORTED
            if source.startswith("unsupported_")
            else ModelObservationState.UNCONFIGURED,
            observed_at=None,
            value=None,
            samples=[],
            aggregation_window_seconds=300 if key.endswith(("_per_second", "_p95")) else 0,
        )
        for key, (unit, source) in _METRICS.items()
        if keys is None or key in keys
    }


def fail_observations(observations, state=ModelObservationState.UNAVAILABLE):
    for item in observations.values():
        if item.state != ModelObservationState.UNSUPPORTED:
            item.state = state
            item.samples = []
            item.observed_at = None
            item.value = None


def parse_observation_rows(rows, observations, targets, end_unix, step):
    seen = set()
    for row in rows:
        labels = row.metric_labels
        guid, namespace, name = labels.get("managed_service"), labels.get("namespace"), labels.get("service")
        key = labels.get("astrolift_measurement")
        identity = (guid, key)
        if (
            guid not in targets
            or targets[guid] != (namespace, name)
            or identity in seen
            or key not in observations[guid]
            or observations[guid][key].state == ModelObservationState.UNSUPPORTED
        ):
            raise prom.PrometheusQueryError("observation series does not match the persisted target")
        seen.add(identity)
        item = observations[guid][key]
        if key == "kv_cache_usage" and any(value > 1 for _, value in row.values):
            raise prom.PrometheusQueryError("cache fraction is invalid")
        if key in {"running_replicas", "ready_replicas"} and any(
            value != int(value) for _, value in row.values
        ):
            raise prom.PrometheusQueryError("pod count is invalid")
        item.samples = [
            ModelObservationSample(timestamp=datetime.fromtimestamp(ts, UTC), value=value)
            for ts, value in row.values
        ]
        if item.samples:
            item.observed_at = item.samples[-1].timestamp
            item.value = item.samples[-1].value
            item.state = (
                ModelObservationState.STALE
                if row.values[-1][0] < end_unix - max(120, 2 * step)
                else ModelObservationState.AVAILABLE
            )
    for values in observations.values():
        for item in values.values():
            if item.state == ModelObservationState.UNCONFIGURED:
                item.state = ModelObservationState.NO_DATA


def prometheus_endpoint(cluster):
    config = cluster.provider_config if isinstance(cluster.provider_config, dict) else {}
    capabilities = cluster.capabilities if isinstance(cluster.capabilities, dict) else {}
    endpoint = config.get("prometheus_endpoint") or capabilities.get("prometheus_endpoint")
    return (
        endpoint.strip() if isinstance(endpoint, str) else "",
        "sigv4" if config.get("prometheus_auth") == "sigv4" else None,
    )


def deployment_metrics(service, start, end, *, transport_available=True) -> ModelDeploymentMetrics:
    start_unix, end_unix, step = observation_window(start, end)
    cluster = service.effective_cluster
    observations = empty_observations()
    result = ModelDeploymentMetrics(
        service_id=GUID(str(service.guid)),
        cluster_id=GUID(str(cluster.guid)),
        start=start,
        end=end,
        retrieved_at=timezone.now(),
        step_seconds=step,
        metrics=list(observations.values()),
    )
    if service.kind != "model_endpoint" or service.variant != "vllm":
        fail_observations(observations, ModelObservationState.UNSUPPORTED)
        return result
    if not transport_available:
        fail_observations(observations)
        return result
    try:
        namespace, name = _target(service)
    except (ValueError, TypeError, AttributeError):
        fail_observations(observations)
        return result
    endpoint, auth = prometheus_endpoint(cluster)
    if not endpoint:
        return result
    # Two fixed groups keep GET requests bounded below common proxy limits.
    groups = [
        {key for key, item in observations.items() if item.source == "vllm"},
        {key for key, item in observations.items() if item.source in {"cadvisor", "kube_state_metrics"}},
    ]
    for keys in groups:
        selected = {key: observations[key] for key in keys}
        try:
            rows = prom.query_range(
                endpoint=endpoint,
                query=observation_query(str(service.guid), namespace, name, keys=keys),
                start_unix=start_unix,
                end_unix=end_unix,
                step_seconds=step,
                timeout=5,
                auth=auth,
                strict=True,
                max_series=len(keys),
                max_samples=MAX_SAMPLES,
                max_response_bytes=MAX_BODY_BYTES,
            )
            parse_observation_rows(
                rows, {str(service.guid): selected}, {str(service.guid): (namespace, name)}, end_unix, step
            )
        except (prom.PrometheusError, ValueError, TypeError, AttributeError, OverflowError):
            fail_observations(selected)
    return result
