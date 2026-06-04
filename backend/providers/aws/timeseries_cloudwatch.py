"""CloudWatch ALB HTTP metrics for EKS-hosted apps.

Every Astrolift-managed app on AWS gets an ALB ingress. ALBs emit
RequestCount, TargetResponseTime, and HTTPCode_Target_5XX_Count to
CloudWatch automatically — no app instrumentation required.

This module queries those metrics and returns (unix_timestamp, value)
pairs in the same shape that the Prometheus-backed golden signals use,
so the EKS driver can fall back to CloudWatch when Prometheus has no
http_requests_total / http_request_duration_seconds_bucket data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_ALB_NAMESPACE = "AWS/ApplicationELB"


def alb_arn_for_app_namespace(
    *,
    k8s_client: Any,
    elbv2_client: Any,
    namespace: str,
) -> str | None:
    """Return the ALB ARN serving the first Ingress in *namespace*, or None."""
    try:
        ingresses = k8s_client.list_namespaced_ingress(namespace)
        for ingress in ingresses.items:
            lb_list = (
                ingress.status
                and ingress.status.load_balancer
                and ingress.status.load_balancer.ingress
            ) or []
            hostname = next((lb.hostname for lb in lb_list if lb.hostname), None)
            if not hostname:
                continue
            paginator = elbv2_client.get_paginator("describe_load_balancers")
            for page in paginator.paginate():
                for lb in page.get("LoadBalancers", []):
                    if lb.get("DNSName", "").lower() == hostname.lower():
                        return lb["LoadBalancerArn"]
    except Exception as exc:
        log.warning("alb_arn_for_app_namespace ns=%s: %s", namespace, exc)
    return None


def _cw_dimension(alb_arn: str) -> str:
    """CloudWatch LoadBalancer dimension = ARN suffix after 'loadbalancer/'."""
    return alb_arn.split(":loadbalancer/")[-1]


def _extract_points(
    cw_response: dict,
    query_id: str,
    divisor: float = 1.0,
) -> list[tuple[float, float]]:
    for result in cw_response.get("MetricDataResults", []):
        if result.get("Id") != query_id:
            continue
        pairs = sorted(
            (ts.timestamp(), val / divisor)
            for ts, val in zip(result.get("Timestamps", []), result.get("Values", []))
        )
        return pairs
    return []


def request_rate(
    *,
    cw: Any,
    alb_arn: str,
    start_unix: int,
    end_unix: int,
    period: int,
) -> list[tuple[float, float]]:
    """RequestCount / period → requests-per-second time series."""
    dim = _cw_dimension(alb_arn)
    try:
        resp = cw.get_metric_data(
            MetricDataQueries=[{
                "Id": "rps",
                "MetricStat": {
                    "Metric": {
                        "Namespace": _ALB_NAMESPACE,
                        "MetricName": "RequestCount",
                        "Dimensions": [{"Name": "LoadBalancer", "Value": dim}],
                    },
                    "Period": period,
                    "Stat": "Sum",
                },
                "ReturnData": True,
            }],
            StartTime=datetime.fromtimestamp(start_unix, tz=timezone.utc),
            EndTime=datetime.fromtimestamp(end_unix, tz=timezone.utc),
        )
        return _extract_points(resp, "rps", divisor=period)
    except Exception as exc:
        log.warning("cloudwatch.request_rate: %s", exc)
        return []


def error_rate(
    *,
    cw: Any,
    alb_arn: str,
    start_unix: int,
    end_unix: int,
    period: int,
) -> list[tuple[float, float]]:
    """HTTPCode_Target_5XX_Count / RequestCount → [0, 1] error-rate series."""
    dim = _cw_dimension(alb_arn)
    try:
        resp = cw.get_metric_data(
            MetricDataQueries=[
                {
                    "Id": "err5",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": _ALB_NAMESPACE,
                            "MetricName": "HTTPCode_Target_5XX_Count",
                            "Dimensions": [{"Name": "LoadBalancer", "Value": dim}],
                        },
                        "Period": period,
                        "Stat": "Sum",
                    },
                    "ReturnData": True,
                },
                {
                    "Id": "total",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": _ALB_NAMESPACE,
                            "MetricName": "RequestCount",
                            "Dimensions": [{"Name": "LoadBalancer", "Value": dim}],
                        },
                        "Period": period,
                        "Stat": "Sum",
                    },
                    "ReturnData": True,
                },
            ],
            StartTime=datetime.fromtimestamp(start_unix, tz=timezone.utc),
            EndTime=datetime.fromtimestamp(end_unix, tz=timezone.utc),
        )
        err_map = {t: v for t, v in _extract_points(resp, "err5")}
        total_map = {t: v for t, v in _extract_points(resp, "total")}
        out: list[tuple[float, float]] = []
        for ts, tot in sorted(total_map.items()):
            rate = (err_map.get(ts, 0.0) / tot) if tot > 0 else 0.0
            out.append((ts, rate))
        return out
    except Exception as exc:
        log.warning("cloudwatch.error_rate: %s", exc)
        return []


def latency(
    *,
    cw: Any,
    alb_arn: str,
    start_unix: int,
    end_unix: int,
    period: int,
    stat: str = "p50",
) -> list[tuple[float, float]]:
    """TargetResponseTime → seconds latency series.

    *stat* is a CloudWatch extended-statistic string: 'p50', 'p95', 'p99'.
    """
    dim = _cw_dimension(alb_arn)
    try:
        metric_stat: dict = {
            "Metric": {
                "Namespace": _ALB_NAMESPACE,
                "MetricName": "TargetResponseTime",
                "Dimensions": [{"Name": "LoadBalancer", "Value": dim}],
            },
            "Period": period,
        }
        # CloudWatch uses ExtendedStatistic for percentiles, Stat for averages.
        if stat.startswith("p"):
            metric_stat["ExtendedStatistic"] = stat
        else:
            metric_stat["Stat"] = stat

        resp = cw.get_metric_data(
            MetricDataQueries=[{"Id": "lat", "MetricStat": metric_stat, "ReturnData": True}],
            StartTime=datetime.fromtimestamp(start_unix, tz=timezone.utc),
            EndTime=datetime.fromtimestamp(end_unix, tz=timezone.utc),
        )
        return _extract_points(resp, "lat")
    except Exception as exc:
        log.warning("cloudwatch.latency stat=%s: %s", stat, exc)
        return []
