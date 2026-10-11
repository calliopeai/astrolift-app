"""Traffic attributed by the running model's authenticated subscription mapping."""

from datetime import UTC, datetime

import strawberry
from django.utils import timezone

from astrolift_graphql import GUID
from astrolift_operations import prometheus_client as prom
from astrolift_services.model_observations import (
    MAX_BODY_BYTES,
    MAX_SAMPLES,
    ModelMetricObservation,
    ModelObservationSample,
    ModelObservationState,
    _target,
    fail_observations,
    observation_window,
    prometheus_endpoint,
)

_METRICS = {
    "requests_per_second": "requests/s",
    "error_requests_per_second": "requests/s",
    "response_bytes_per_second": "bytes/s",
    "latency_p95": "seconds",
    "input_tokens": "tokens",
    "output_tokens": "tokens",
    "cost_usd": "USD",
}
_UNSUPPORTED = {"input_tokens", "output_tokens", "cost_usd"}


def subscription_metric_operation(args):
    from astrolift_identity.operation_context import OperationContext, environment_context
    from astrolift_services.models import ManagedServiceAttachment
    from core.scope_args import read_guid
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    row = (
        ManagedServiceAttachment.objects.filter(
            guid=read_guid(args, "subscription_id"),
            app_environment__deleted_at__isnull=True,
            app_environment__registered_app__organization_id=tenant.organization_id if tenant else None,
        )
        .select_related("app_environment__tenant_cluster", "app_environment__registered_app")
        .first()
    )
    return (environment_context(row.app_environment) if row is not None else OperationContext(approvals=0),)


@strawberry.type
class ModelSubscriptionMetrics:
    service_id: GUID
    cluster_id: GUID
    subscription_id: GUID
    start: datetime
    end: datetime
    retrieved_at: datetime
    step_seconds: int
    metrics: list[ModelMetricObservation]
    scope: str = "authenticated_subscription"


def subscription_observation_query(service_guid, subscription_guid, namespace, name):
    values = {
        "managed_service": service_guid,
        "subscription_id": subscription_guid,
        "namespace": namespace,
        "service": name,
    }
    base = ",".join(values)
    selector = ",".join(f'{key}="{prom.sanitize_label_value(value)}"' for key, value in values.items())
    selector += ',pod!=""'
    identity = base + ",pod"
    # Immutable subscription IDs are never reused. Keep historical usage across
    # key rotations, while requiring the meter's verified startup mapping.
    admitted = (
        f"(max by({identity}) ((astrolift_model_subscription_info{{{selector}}} == 2) / 2) "
        f"and on({identity}) max by({identity}) ((astrolift_model_subscription_auth_revision{{{selector}}} >= 0) < 9007199254740992))"
    )

    def rate(metric, extra=""):
        dimensions = "route,le" if metric.endswith("_bucket") else "route,status_class,outcome"
        # Duplicate scrape jobs must not duplicate traffic or make the mapping
        # join ambiguous. Retain each pod's actual counter dimensions.
        return (
            f"(max by({identity},{dimensions}) (rate({metric}{{{selector}{extra}}}[5m])) "
            f"* on({identity}) group_left() {admitted})"
        )

    requests = f'sum by({base})({rate("astrolift_model_subscription_requests_total")})'
    # Each absent error category may be a genuine zero, but only after the
    # initialized request counters have produced a valid rate window.
    errors = (
        f'(sum by({base})({rate("astrolift_model_subscription_requests_total", ",outcome=~\"error|interrupted|disconnected\"")}) or (0 * {requests}))'
        f' + on({base}) '
        f'(sum by({base})({rate("astrolift_model_subscription_requests_total", ",outcome=\"completed\",status_class=~\"4xx|5xx\"")}) or (0 * {requests}))'
    )
    queries = {
        "requests_per_second": requests,
        "error_requests_per_second": errors,
        "response_bytes_per_second": f'sum by({base})({rate("astrolift_model_subscription_response_bytes_total")})',
        "latency_p95": (
            f'histogram_quantile(0.95,sum by(le,{base})('
            f'{rate("astrolift_model_subscription_request_duration_seconds_bucket")})) >= 0'
        ),
    }
    return " or ".join(
        f'label_replace(({query}),"astrolift_measurement","{key}","subscription_id",".*")'
        for key, query in queries.items()
    )


def subscription_metrics(row, start, end, *, transport_available=True):
    service = row.managed_service
    start_unix, end_unix, step = observation_window(start, end)
    observations = {
        key: ModelMetricObservation(
            key=key,
            unit=unit,
            source="authenticated_model_subscription",
            state=ModelObservationState.UNSUPPORTED
            if key in _UNSUPPORTED
            else ModelObservationState.UNCONFIGURED,
            observed_at=None,
            value=None,
            samples=[],
            aggregation_window_seconds=0 if key in _UNSUPPORTED else 300,
        )
        for key, unit in _METRICS.items()
    }
    result = ModelSubscriptionMetrics(
        service_id=GUID(str(service.guid)),
        cluster_id=GUID(str(service.tenant_cluster.guid)),
        subscription_id=GUID(str(row.guid)),
        start=start,
        end=end,
        retrieved_at=timezone.now(),
        step_seconds=step,
        metrics=list(observations.values()),
    )
    available = {key: item for key, item in observations.items() if key not in _UNSUPPORTED}
    if not transport_available:
        fail_observations(available)
        return result
    endpoint, auth = prometheus_endpoint(service.tenant_cluster)
    if not endpoint:
        return result
    try:
        namespace, name = _target(service)
        rows = prom.query_range(
            endpoint=endpoint,
            query=subscription_observation_query(str(service.guid), str(row.guid), namespace, name),
            start_unix=start_unix,
            end_unix=end_unix,
            step_seconds=step,
            timeout=5,
            auth=auth,
            strict=True,
            max_series=len(available),
            max_samples=MAX_SAMPLES,
            max_response_bytes=MAX_BODY_BYTES,
            request_method="POST",
            max_request_bytes=32_768,
        )
        seen = set()
        for series in rows:
            labels = series.metric_labels
            key = labels.get("astrolift_measurement")
            if (
                key not in available
                or key in seen
                or (
                    labels.get("managed_service"),
                    labels.get("subscription_id"),
                    labels.get("namespace"),
                    labels.get("service"),
                )
                != (str(service.guid), str(row.guid), namespace, name)
            ):
                raise prom.PrometheusQueryError("Subscription series disagrees with its authorized target.")
            seen.add(key)
            item = available[key]
            item.samples = [
                ModelObservationSample(timestamp=datetime.fromtimestamp(ts, UTC), value=value)
                for ts, value in series.values
            ]
            if series.values:
                timestamp, value = series.values[-1]
                item.state = (
                    ModelObservationState.AVAILABLE
                    if end_unix - timestamp <= max(120, 2 * step)
                    else ModelObservationState.STALE
                )
                item.observed_at = item.samples[-1].timestamp
                item.value = value
        for key, item in available.items():
            if key not in seen or not item.samples:
                item.state = ModelObservationState.NO_DATA
    except (prom.PrometheusError, ValueError, TypeError, AttributeError, OverflowError):
        fail_observations(available)
    return result
