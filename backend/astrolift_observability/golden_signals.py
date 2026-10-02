"""Per-signal measurement scope and failures for golden signals."""

import dataclasses
import datetime as dt
import math

from astrolift_drivers.registry import DriverNotFound
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.visibility import cluster_owned_and_live
from astrolift_observability import prom_client, prom_queries
from astrolift_observability.schema.types import (
    AppGoldenSignal,
    AppGoldenSignalsResult,
    GoldenSignalIdentityBasis,
    GoldenSignalKind,
    GoldenSignalMeasurement,
    GoldenSignalScope,
    GoldenSignalSource,
    GoldenSignalTarget,
    GoldenSignalUnavailableReason,
    MetricContainerIdentity,
    TimeSeriesPoint,
)
from astrolift_observability.workload_resources import ResourceUnavailable, measure_resource
from astrolift_operations.prometheus_client import PrometheusError
from astrolift_registry.models import Workload
from core.cluster_observability import _auth_for_cluster, _driver_for_cluster, namespace_for_environment
from core.schema.enums import ObservabilityPanelReason
from providers._sdk.edge_metrics import edge_metrics_for_ingress_class

_SIGNAL_PLAN = (
    (GoldenSignalKind.TRAFFIC, "rps", prom_queries.build_request_rate_query, {}),
    (GoldenSignalKind.ERRORS, "ratio", prom_queries.build_error_rate_query, {}),
    (GoldenSignalKind.LATENCY_P50, "seconds", prom_queries.build_latency_quantile_query, {"quantile": 0.50}),
    (GoldenSignalKind.LATENCY_P90, "seconds", prom_queries.build_latency_quantile_query, {"quantile": 0.90}),
    (GoldenSignalKind.LATENCY_P95, "seconds", prom_queries.build_latency_quantile_query, {"quantile": 0.95}),
    (GoldenSignalKind.LATENCY_P99, "seconds", prom_queries.build_latency_quantile_query, {"quantile": 0.99}),
    (GoldenSignalKind.SATURATION_CPU, "ratio", prom_queries.build_cpu_saturation_query, {}),
    (GoldenSignalKind.SATURATION_MEMORY, "ratio", prom_queries.build_memory_saturation_query, {}),
)
_RESOURCES = {GoldenSignalKind.SATURATION_CPU: "cpu", GoldenSignalKind.SATURATION_MEMORY: "memory"}


def _points(pairs):
    return [TimeSeriesPoint(ts=dt.datetime.fromtimestamp(ts, dt.UTC), value=value) for ts, value in pairs]


def _reason(value):
    try:
        return GoldenSignalUnavailableReason[value]
    except KeyError:
        return GoldenSignalUnavailableReason.PROVIDER_ERROR


def _unavailable(signal, reason):
    configured = reason in {
        GoldenSignalUnavailableReason.NOT_CONFIGURED,
        GoldenSignalUnavailableReason.NOT_INSTRUMENTED,
        GoldenSignalUnavailableReason.ENVIRONMENT_NOT_FOUND,
        GoldenSignalUnavailableReason.WORKLOAD_NOT_FOUND,
    }
    provider = reason == GoldenSignalUnavailableReason.NOT_SUPPORTED_BY_PROVIDER
    empty = reason in {
        GoldenSignalUnavailableReason.NO_DATA_YET,
        GoldenSignalUnavailableReason.NO_PODS,
        GoldenSignalUnavailableReason.MISSING_USAGE,
        GoldenSignalUnavailableReason.MISSING_LIMITS,
    }
    panel = (
        ObservabilityPanelReason.NOT_CONFIGURED
        if configured
        else ObservabilityPanelReason.NOT_SUPPORTED_BY_PROVIDER
        if provider
        else ObservabilityPanelReason.NO_DATA_YET
        if empty
        else ObservabilityPanelReason.ERROR
    )
    return dataclasses.replace(
        signal,
        samples=[],
        reason=panel,
        measurement=dataclasses.replace(signal.measurement, available=False, unavailable_reason=reason),
    )


def _envelope(signals):
    reason = (
        ObservabilityPanelReason.OK
        if any(signal.samples for signal in signals)
        else (
            ObservabilityPanelReason.ERROR
            if any(signal.reason == ObservabilityPanelReason.ERROR for signal in signals)
            else ObservabilityPanelReason.NO_DATA_YET
        )
    )
    return AppGoldenSignalsResult(reason=reason, signals=signals)


def golden_signals_for_app(*, app, environment_name, workload_slug, seconds, now, cloudwatch_fallback):
    qs = AppEnvironment.objects.filter(registered_app_id=app.pk, deleted_at__isnull=True).select_related(
        "tenant_cluster__provider_plugin"
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
    end, start = int(now.timestamp()), int(now.timestamp()) - seconds
    step = prom_queries.pick_step_seconds(seconds)
    scope = GoldenSignalScope.WORKLOAD if workload_slug else GoldenSignalScope.APP_ENVIRONMENT
    signals = [
        AppGoldenSignal(
            name=kind,
            range_seconds=seconds,
            unit=unit,
            samples=[],
            promql="",
            measurement=GoldenSignalMeasurement(
                effective_scope=scope,
                source=GoldenSignalSource.NONE,
                identity_basis=GoldenSignalIdentityBasis.UNRESOLVED,
                available=False,
                target=target,
            ),
        )
        for kind, unit, _, _ in _SIGNAL_PLAN
    ]
    missing = (
        GoldenSignalUnavailableReason.ENVIRONMENT_NOT_FOUND
        if environment is None
        else GoldenSignalUnavailableReason.TARGET_NOT_OWNED
        if not cluster_owned_and_live(cluster, app.organization_id)
        else GoldenSignalUnavailableReason.WORKLOAD_NOT_FOUND
        if workload_slug and workload is None
        else None
    )
    if missing:
        return AppGoldenSignalsResult(
            reason=ObservabilityPanelReason.NOT_CONFIGURED,
            signals=[_unavailable(signal, missing) for signal in signals],
        )
    cfg, caps = cluster.provider_config or {}, cluster.capabilities or {}
    endpoint = str(cfg.get("prometheus_endpoint") or caps.get("prometheus_endpoint") or "").strip()
    if not endpoint:
        return AppGoldenSignalsResult(
            reason=ObservabilityPanelReason.NOT_CONFIGURED,
            signals=[
                _unavailable(signal, GoldenSignalUnavailableReason.NOT_CONFIGURED) for signal in signals
            ],
        )
    edge = edge_metrics_for_ingress_class(cluster.ingress_class)
    declared = (app.manifest_normalized or {}).get("workloads") or []
    instrumented = any(
        isinstance(row, dict)
        and row.get("metrics_enabled")
        and (not workload or row.get("name") == workload.slug)
        for row in declared
    )
    members, member_error = None, None
    observed = None
    if workload:
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
            if not members:
                raise ResourceUnavailable("NO_PODS")
            observed = dt.datetime.now(dt.UTC)
        except NotImplementedError:
            member_error = GoldenSignalUnavailableReason.NOT_SUPPORTED_BY_PROVIDER
        except Exception as exc:
            member_error = (
                GoldenSignalUnavailableReason.NOT_SUPPORTED_BY_PROVIDER
                if isinstance(exc.__cause__, DriverNotFound)
                else _reason(getattr(exc, "reason", "PROVIDER_ERROR"))
            )
    for index, (kind, _, builder, extra) in enumerate(_SIGNAL_PLAN):
        signal = signals[index]
        resource = _RESOURCES.get(kind)
        source = (
            GoldenSignalSource.CADVISOR_KUBE_STATE_METRICS
            if resource
            else GoldenSignalSource.EDGE_PROMETHEUS
            if edge and not workload
            else GoldenSignalSource.APP_INSTRUMENTATION
        )
        basis = (
            GoldenSignalIdentityBasis.VERIFIED_RUNTIME
            if resource and members
            else GoldenSignalIdentityBasis.UNRESOLVED
            if resource and workload
            else GoldenSignalIdentityBasis.NAMESPACE
            if resource or (edge and not workload)
            else GoldenSignalIdentityBasis.SOURCE_LABELS
        )
        signal.measurement = dataclasses.replace(
            signal.measurement,
            source=source,
            identity_basis=basis,
            membership_observed_at=observed if resource else None,
        )
        if resource and workload:
            if member_error:
                signals[index] = _unavailable(signal, member_error)
                continue
            signal.measurement.containers = [
                MetricContainerIdentity(
                    pod_name=member.pod_name,
                    pod_uid=member.pod_uid,
                    container_name=member.container_name,
                    container_id=member.container_id,
                )
                for member in members
            ]
            signal.measurement.usage_unit = "cores" if resource == "cpu" else "bytes"
            try:
                measured = measure_resource(
                    endpoint=endpoint,
                    namespace=namespace,
                    members=members,
                    resource=resource,
                    range_seconds=seconds,
                    start_unix=start,
                    end_unix=end,
                    step_seconds=step,
                )
            except ResourceUnavailable as exc:
                signals[index] = _unavailable(signal, _reason(exc.reason))
                continue
            signal.samples = _points(measured.samples)
            signal.promql = measured.promql
            signal.measurement.usage_samples, signal.measurement.limit_samples = (
                _points(measured.usage),
                _points(measured.limits),
            )
            signal.measurement.measurement_start = dt.datetime.fromtimestamp(measured.start, dt.UTC)
        else:
            if workload and member_error:
                signals[index] = _unavailable(signal, member_error)
                continue
            plan = builder(
                app_slug=app.slug,
                environment_name=environment.name,
                workload_slug=workload_slug,
                range_seconds=seconds,
                namespace=namespace,
                **({"edge": edge} if not resource else {}),
                **(
                    {
                        "identity_labels": {
                            "astrolift_app_id": str(app.guid),
                            "astrolift_environment_id": str(environment.guid),
                            "astrolift_workload_id": str(workload.guid),
                        }
                    }
                    if workload and not resource
                    else {}
                ),
                **extra,
            )
            signal.promql = plan.promql
            try:
                series = prom_client.query_range_series(
                    endpoint=endpoint,
                    promql=plan.promql,
                    start_unix=start,
                    end_unix=end,
                    step_seconds=step,
                    strict=True,
                )
                if len(series) > 1:
                    raise ResourceUnavailable("AMBIGUOUS_SERIES")
                pairs = series[0][1] if series else []
                if any(not math.isfinite(value) or value < 0 for _, value in pairs):
                    raise ResourceUnavailable("INVALID_DATA")
                signal.samples = _points(pairs)
            except (PrometheusError, ResourceUnavailable) as exc:
                signals[index] = _unavailable(signal, _reason(getattr(exc, "reason", "QUERY_ERROR")))
                continue
        if signal.samples:
            signal.measurement.available = True
        else:
            reason = (
                GoldenSignalUnavailableReason.NOT_INSTRUMENTED
                if not resource and not instrumented and not (edge and not workload)
                else GoldenSignalUnavailableReason.NO_DATA_YET
            )
            signal = _unavailable(signal, reason)
        signals[index] = signal
    if not workload and all(not signal.samples for signal in signals if signal.name not in _RESOURCES):
        signals, configured = cloudwatch_fallback(
            out=signals,
            app=app,
            environment_name=environment.name,
            app_namespace=namespace,
            start_unix=start,
            end_unix=end,
            step=step,
            seconds=seconds,
        )
        for signal in signals:
            if signal.name in _RESOURCES:
                continue
            if configured:
                signal.measurement.source = GoldenSignalSource.CLOUDWATCH_ALB
                signal.measurement.identity_basis = GoldenSignalIdentityBasis.NAMESPACE
                if signal.name == GoldenSignalKind.LATENCY_P90:
                    signal.reason = ObservabilityPanelReason.NOT_SUPPORTED_BY_PROVIDER
                    signal.measurement.unavailable_reason = (
                        GoldenSignalUnavailableReason.NOT_SUPPORTED_BY_PROVIDER
                    )
                    continue
            if signal.samples and signal.promql.startswith("# CloudWatch"):
                signal.reason = ObservabilityPanelReason.OK
                signal.measurement = dataclasses.replace(
                    signal.measurement,
                    available=True,
                    source=GoldenSignalSource.CLOUDWATCH_ALB,
                    identity_basis=GoldenSignalIdentityBasis.NAMESPACE,
                    unavailable_reason=None,
                )
            elif (
                configured
                and signal.measurement.unavailable_reason != GoldenSignalUnavailableReason.PROVIDER_ERROR
            ):
                signal.reason = ObservabilityPanelReason.NO_DATA_YET
                signal.measurement.unavailable_reason = GoldenSignalUnavailableReason.NO_DATA_YET
    return _envelope(signals)
