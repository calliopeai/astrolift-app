"""CloudWatch ALB HTTP metrics for EKS-hosted apps.

Apps with dedicated ALB ingress receive request metrics from CloudWatch. ALBs emit
RequestCount, TargetResponseTime, and HTTPCode_Target_5XX_Count to
CloudWatch automatically — no app instrumentation required.

This module queries those metrics and returns (unix_timestamp, value)
pairs in the same shape that the Prometheus-backed golden signals use,
so the EKS driver can fall back to CloudWatch when Prometheus has no
http_requests_total / http_request_duration_seconds_bucket data.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

_ALB_NAMESPACE = "AWS/ApplicationELB"


def alb_arn_for_app_namespace(
    *,
    k8s_client: Any,
    elbv2_client: Any,
    namespace: str,
) -> str | None:
    """Find the sole namespace-owned ALB, or None when none is provisioned.

    Discovery failures and shared/untagged load balancers raise rather than
    presenting another app's traffic as this namespace's measurements.
    """
    try:
        ingresses = k8s_client.list_namespaced_ingress(namespace)
        hostnames = set()
        for ingress in ingresses.items:
            lb_list = (ingress.status and ingress.status.load_balancer and ingress.status.load_balancer.ingress) or []
            hostnames.update(lb.hostname.lower() for lb in lb_list if lb.hostname)
        if not hostnames:
            return None
        found: dict[str, set[str]] = {hostname: set() for hostname in hostnames}
        paginator = elbv2_client.get_paginator("describe_load_balancers")
        for page in paginator.paginate():
            for lb in page.get("LoadBalancers", []):
                hostname = lb.get("DNSName", "").lower()
                if hostname in found:
                    arn = lb.get("LoadBalancerArn")
                    if not isinstance(arn, str) or not arn:
                        raise ValueError("ALB discovery returned an invalid load balancer")
                    found[hostname].add(arn)
        if any(len(arns) != 1 for arns in found.values()):
            raise ValueError("ALB ingress load balancer could not be uniquely resolved")
        arns = {arn for matches in found.values() for arn in matches}
        if len(arns) != 1:
            raise ValueError("ALB namespace has multiple load balancers; environment totals are unavailable")
        arn = next(iter(arns))
        # Percentiles for different ALBs cannot be combined into one environment percentile.
        tags = elbv2_client.describe_tags(ResourceArns=[arn])
        rows = tags.get("TagDescriptions", [])
        if len(rows) != 1 or rows[0].get("ResourceArn") != arn:
            raise ValueError("ALB discovery returned invalid ownership evidence")
        stack = next(
            (tag.get("Value", "") for tag in rows[0].get("Tags", []) if tag.get("Key") == "ingress.k8s.aws/stack"), ""
        )
        if not isinstance(stack, str) or not stack.startswith(namespace + "/"):
            raise ValueError("ALB is shared or lacks the namespace ownership tag")
        return arn
    except Exception as exc:
        log.warning("alb_arn_for_app_namespace ns=%s: %s", namespace, exc)
        raise


def _cw_dimension(alb_arn: str) -> str:
    """CloudWatch LoadBalancer dimension = ARN suffix after 'loadbalancer/'."""
    return alb_arn.split(":loadbalancer/")[-1]


def _extract_points(
    cw_response: dict,
    query_id: str,
    divisor: float = 1.0,
) -> list[tuple[float, float]]:
    if not isinstance(cw_response, dict) or "NextToken" in cw_response or cw_response.get("Messages", []) != []:
        raise ValueError("CloudWatch returned incomplete metric evidence")
    results = cw_response.get("MetricDataResults")
    if not isinstance(results, list) or not results:
        raise ValueError("CloudWatch returned invalid metric evidence")
    seen: set[str] = set()
    selected = None
    for result in results:
        if not isinstance(result, dict) or result.get("StatusCode") != "Complete" or result.get("Messages", []) != []:
            raise ValueError("CloudWatch returned incomplete metric evidence")
        result_id = result.get("Id")
        if not isinstance(result_id, str) or not result_id or result_id in seen:
            raise ValueError("CloudWatch returned invalid metric evidence")
        seen.add(result_id)
        timestamps, values = result.get("Timestamps", []), result.get("Values", [])
        if not isinstance(timestamps, list) or not isinstance(values, list) or len(timestamps) != len(values):
            raise ValueError("CloudWatch returned invalid metric evidence")
        pairs = []
        seen_timestamps: set[float] = set()
        for ts, val in zip(timestamps, values, strict=True):
            if (
                not isinstance(ts, datetime)
                or ts.tzinfo is None
                or not isinstance(val, (int, float))
                or isinstance(val, bool)
                or not math.isfinite(val)
                or val < 0
            ):
                raise ValueError("CloudWatch returned invalid metric evidence")
            unix = ts.timestamp()
            if not math.isfinite(unix) or unix < 0 or unix in seen_timestamps:
                raise ValueError("CloudWatch returned invalid metric evidence")
            seen_timestamps.add(unix)
            pairs.append((unix, val / divisor))
        if result_id == query_id:
            selected = sorted(pairs)
    if selected is None:
        raise ValueError("CloudWatch returned invalid metric evidence")
    return selected


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
            MetricDataQueries=[
                {
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
                }
            ],
            StartTime=datetime.fromtimestamp(start_unix, tz=UTC),
            EndTime=datetime.fromtimestamp(end_unix, tz=UTC),
        )
        return _extract_points(resp, "rps", divisor=period)
    except Exception as exc:
        log.warning("cloudwatch.request_rate: %s", exc)
        raise


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
            StartTime=datetime.fromtimestamp(start_unix, tz=UTC),
            EndTime=datetime.fromtimestamp(end_unix, tz=UTC),
        )
        err_map = {t: v for t, v in _extract_points(resp, "err5")}
        total_map = {t: v for t, v in _extract_points(resp, "total")}
        if any(value > 0 and total_map.get(ts, 0) <= 0 for ts, value in err_map.items()):
            raise ValueError("CloudWatch returned inconsistent error-rate evidence")
        out: list[tuple[float, float]] = []
        for ts, tot in sorted(total_map.items()):
            rate = (err_map.get(ts, 0.0) / tot) if tot > 0 else 0.0
            out.append((ts, rate))
        return out
    except Exception as exc:
        log.warning("cloudwatch.error_rate: %s", exc)
        raise


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

    *stat* accepts CloudWatch statistics, including 'p50', 'p90', 'p95', 'p99'.
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
        # GetMetricData uses Stat for percentiles too; ExtendedStatistic belongs
        # to GetMetricStatistics and fails botocore validation here.
        metric_stat["Stat"] = stat

        resp = cw.get_metric_data(
            MetricDataQueries=[{"Id": "lat", "MetricStat": metric_stat, "ReturnData": True}],
            StartTime=datetime.fromtimestamp(start_unix, tz=UTC),
            EndTime=datetime.fromtimestamp(end_unix, tz=UTC),
        )
        return _extract_points(resp, "lat")
    except Exception as exc:
        log.warning("cloudwatch.latency stat=%s: %s", stat, exc)
        raise
