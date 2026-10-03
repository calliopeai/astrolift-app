from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.stub import Stubber

from aws.timeseries_cloudwatch import alb_arn_for_app_namespace, latency


@pytest.mark.parametrize("stack", ["app-env/web", "shared-group", None])
def test_alb_discovery_requires_namespace_ownership(stack):
    arn = "arn:aws:elasticloadbalancing:us-west-2:123456789012:loadbalancer/app/test/123"
    elbv2 = boto3.client("elbv2", region_name="us-west-2", aws_access_key_id="test", aws_secret_access_key="test")
    k8s = MagicMock(spec=["list_namespaced_ingress"])
    k8s.list_namespaced_ingress.return_value = SimpleNamespace(
        items=[
            SimpleNamespace(
                status=SimpleNamespace(
                    load_balancer=SimpleNamespace(ingress=[SimpleNamespace(hostname="app.example.net")])
                )
            )
        ]
    )
    with Stubber(elbv2) as stub:
        stub.add_response(
            "describe_load_balancers", {"LoadBalancers": [{"LoadBalancerArn": arn, "DNSName": "app.example.net"}]}, {}
        )
        stub.add_response(
            "describe_tags",
            {
                "TagDescriptions": [
                    {
                        "ResourceArn": arn,
                        "Tags": [{"Key": "ingress.k8s.aws/stack", "Value": stack}]
                        if stack
                        else [{"Key": "Name", "Value": "untagged-owner"}],
                    }
                ]
            },
            {"ResourceArns": [arn]},
        )
        if stack == "app-env/web":
            assert alb_arn_for_app_namespace(k8s_client=k8s, elbv2_client=elbv2, namespace="app-env") == arn
        else:
            with pytest.raises(ValueError, match="shared or lacks"):
                alb_arn_for_app_namespace(k8s_client=k8s, elbv2_client=elbv2, namespace="app-env")
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("stat", ["p50", "p90", "p95", "p99"])
def test_percentiles_use_get_metric_data_stat(stat):
    cw = boto3.client("cloudwatch", region_name="us-west-2", aws_access_key_id="test", aws_secret_access_key="test")
    start, end = datetime.fromtimestamp(60, UTC), datetime.fromtimestamp(120, UTC)
    with Stubber(cw) as stub:
        stub.add_response(
            "get_metric_data",
            {
                "MetricDataResults": [
                    {"Id": "lat", "Timestamps": [start], "Values": [0.25], "StatusCode": "Complete"},
                ]
            },
            {
                "MetricDataQueries": [
                    {
                        "Id": "lat",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "AWS/ApplicationELB",
                                "MetricName": "TargetResponseTime",
                                "Dimensions": [{"Name": "LoadBalancer", "Value": "app/test/123"}],
                            },
                            "Period": 60,
                            "Stat": stat,
                        },
                        "ReturnData": True,
                    }
                ],
                "StartTime": start,
                "EndTime": end,
            },
        )
        assert latency(
            cw=cw,
            alb_arn="arn:aws:elasticloadbalancing:us-west-2:123456789012:loadbalancer/app/test/123",
            start_unix=60,
            end_unix=120,
            period=60,
            stat=stat,
        ) == [(60.0, 0.25)]
        stub.assert_no_pending_responses()


def test_cloudwatch_failure_is_an_error_instead_of_an_empty_window():
    cw = boto3.client("cloudwatch", region_name="us-west-2", aws_access_key_id="test", aws_secret_access_key="test")
    with Stubber(cw) as stub:
        stub.add_client_error("get_metric_data", service_error_code="AccessDenied", http_status_code=403)
        with pytest.raises(cw.exceptions.ClientError):
            latency(
                cw=cw,
                alb_arn="arn:aws:elasticloadbalancing:us-west-2:123456789012:loadbalancer/app/test/123",
                start_unix=60,
                end_unix=120,
                period=60,
            )


_ALB_ARN = "arn:aws:elasticloadbalancing:us-west-2:123456789012:loadbalancer/app/test/123"
_SAMPLE_TIME = datetime.fromtimestamp(60, UTC)


def _metric_result(query_id, values=(), timestamps=None, **extra):
    return {
        "Id": query_id,
        "StatusCode": "Complete",
        "Timestamps": [_SAMPLE_TIME] * len(values) if timestamps is None else timestamps,
        "Values": list(values),
        **extra,
    }


def _cloudwatch():
    return boto3.client("cloudwatch", region_name="us-west-2", aws_access_key_id="test", aws_secret_access_key="test")


def _read_error_rate(cw):
    from aws.timeseries_cloudwatch import error_rate

    return error_rate(cw=cw, alb_arn=_ALB_ARN, start_unix=60, end_unix=120, period=60)


@pytest.mark.parametrize("status", ["InternalError", "Forbidden", "PartialData"])
@pytest.mark.parametrize("failed_id", ["err5", "total"])
def test_cloudwatch_result_failure_never_becomes_zero(status, failed_id, caplog):
    cw = _cloudwatch()
    results = [_metric_result("err5"), _metric_result("total", [60.0])]
    for result in results:
        if result["Id"] == failed_id:
            result["StatusCode"] = status
            result["Messages"] = [{"Code": status, "Value": "PRIVATE_PROVIDER_MARKER"}]
    with Stubber(cw) as stub:
        stub.add_response("get_metric_data", {"MetricDataResults": results})
        with pytest.raises(ValueError, match="incomplete metric evidence") as caught:
            _read_error_rate(cw)
        assert "PRIVATE_PROVIDER_MARKER" not in str(caught.value)
        assert "PRIVATE_PROVIDER_MARKER" not in caplog.text
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "extra",
    [{"NextToken": "next-page"}, {"Messages": [{"Code": "MaxMetricsExceeded", "Value": "PRIVATE_PROVIDER_MARKER"}]}],
)
def test_complete_but_paginated_or_warned_cloudwatch_response_is_refused(extra, caplog):
    cw = _cloudwatch()
    with Stubber(cw) as stub:
        stub.add_response(
            "get_metric_data", {"MetricDataResults": [_metric_result("err5"), _metric_result("total", [60.0])], **extra}
        )
        with pytest.raises(ValueError, match="incomplete metric evidence") as caught:
            _read_error_rate(cw)
        assert "PRIVATE_PROVIDER_MARKER" not in str(caught.value)
        assert "PRIVATE_PROVIDER_MARKER" not in caplog.text
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "results",
    [
        [_metric_result("total", [60.0])],
        [_metric_result("err5"), _metric_result("err5"), _metric_result("total", [60.0])],
        [_metric_result("err5", [1.0], timestamps=[]), _metric_result("total", [60.0])],
        [_metric_result("err5"), _metric_result("total", [60.0, 60.0])],
        [_metric_result("err5", [float("nan")]), _metric_result("total", [60.0])],
        [_metric_result("err5"), _metric_result("total", [float("inf")])],
        [_metric_result("err5", [-1.0]), _metric_result("total", [60.0])],
        [_metric_result("err5", [1.0]), _metric_result("total", [0.0])],
        [_metric_result("err5", [1.0]), _metric_result("total")],
    ],
)
def test_invalid_cloudwatch_evidence_is_never_an_available_zero(results):
    cw = _cloudwatch()
    with Stubber(cw) as stub:
        stub.add_response("get_metric_data", {"MetricDataResults": results})
        with pytest.raises(ValueError, match=r"CloudWatch returned (invalid|inconsistent).*evidence"):
            _read_error_rate(cw)
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("empty", [_metric_result("err5"), {"Id": "err5", "StatusCode": "Complete"}])
def test_complete_empty_error_series_can_establish_measured_zero(empty):
    cw = _cloudwatch()
    with Stubber(cw) as stub:
        stub.add_response("get_metric_data", {"MetricDataResults": [empty, _metric_result("total", [60.0])]})
        assert _read_error_rate(cw) == [(60.0, 0.0)]
        stub.assert_no_pending_responses()


def test_complete_empty_request_window_stays_empty_and_real_error_ratio_is_preserved():
    cw = _cloudwatch()
    with Stubber(cw) as stub:
        stub.add_response("get_metric_data", {"MetricDataResults": [_metric_result("err5"), _metric_result("total")]})
        stub.add_response(
            "get_metric_data", {"MetricDataResults": [_metric_result("err5", [3.0]), _metric_result("total", [60.0])]}
        )
        assert _read_error_rate(cw) == []
        assert _read_error_rate(cw) == [(60.0, 0.05)]
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("read", ["request_rate", "latency"])
def test_all_cloudwatch_signals_refuse_incomplete_samples(read):
    from aws import timeseries_cloudwatch

    cw = _cloudwatch()
    query_id = "rps" if read == "request_rate" else "lat"
    with Stubber(cw) as stub:
        stub.add_response(
            "get_metric_data", {"MetricDataResults": [_metric_result(query_id, [1.0], StatusCode="PartialData")]}
        )
        with pytest.raises(ValueError, match="incomplete metric evidence"):
            getattr(timeseries_cloudwatch, read)(cw=cw, alb_arn=_ALB_ARN, start_unix=60, end_unix=120, period=60)
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"MetricDataResults": {}},
        {"MetricDataResults": [None]},
        {"MetricDataResults": [_metric_result("lat", [1.0], StatusCode=None)]},
        {"MetricDataResults": [_metric_result("lat", [1.0], Messages=None)]},
        {"MetricDataResults": [_metric_result("lat", [True])]},
        {"MetricDataResults": [_metric_result("lat", [1.0], timestamps=[datetime.fromtimestamp(60)])]},
        {"MetricDataResults": [_metric_result("lat", [1.0], timestamps=[datetime.fromtimestamp(-60, UTC)])]},
        {"MetricDataResults": [_metric_result("lat", [1.0], timestamps="PRIVATE_PROVIDER_MARKER")]},
    ],
)
def test_malformed_transport_evidence_has_a_static_refusal(response):
    from aws.timeseries_cloudwatch import _extract_points

    with pytest.raises(ValueError, match=r"CloudWatch returned (invalid|incomplete) metric evidence") as caught:
        _extract_points(response, "lat")
    assert "PRIVATE_PROVIDER_MARKER" not in str(caught.value)


def _ingress_hostnames(*hostnames):
    k8s = MagicMock(spec=["list_namespaced_ingress"])
    k8s.list_namespaced_ingress.return_value = SimpleNamespace(
        items=[
            SimpleNamespace(
                status=SimpleNamespace(load_balancer=SimpleNamespace(ingress=[SimpleNamespace(hostname=hostname)]))
            )
            for hostname in hostnames
        ]
    )
    return k8s


def test_multiple_namespace_albs_are_refused_instead_of_first_ingress_totals():
    elbv2 = boto3.client("elbv2", region_name="us-west-2", aws_access_key_id="test", aws_secret_access_key="test")
    with Stubber(elbv2) as stub:
        stub.add_response(
            "describe_load_balancers",
            {
                "LoadBalancers": [
                    {"LoadBalancerArn": _ALB_ARN, "DNSName": "first.example.net"},
                ],
                "NextMarker": "second-page",
            },
            {},
        )
        stub.add_response(
            "describe_load_balancers",
            {
                "LoadBalancers": [
                    {"LoadBalancerArn": _ALB_ARN + "2", "DNSName": "second.example.net"},
                ]
            },
            {"Marker": "second-page"},
        )
        with pytest.raises(ValueError, match="multiple load balancers"):
            alb_arn_for_app_namespace(
                k8s_client=_ingress_hostnames("first.example.net", "second.example.net"),
                elbv2_client=elbv2,
                namespace="app-env",
            )
        stub.assert_no_pending_responses()


def test_multiple_ingresses_with_the_same_owned_alb_are_not_ambiguous():
    elbv2 = boto3.client("elbv2", region_name="us-west-2", aws_access_key_id="test", aws_secret_access_key="test")
    with Stubber(elbv2) as stub:
        stub.add_response(
            "describe_load_balancers",
            {"LoadBalancers": [{"LoadBalancerArn": _ALB_ARN, "DNSName": "app.example.net"}]},
            {},
        )
        stub.add_response(
            "describe_tags",
            {
                "TagDescriptions": [
                    {"ResourceArn": _ALB_ARN, "Tags": [{"Key": "ingress.k8s.aws/stack", "Value": "app-env/web"}]}
                ]
            },
            {"ResourceArns": [_ALB_ARN]},
        )
        assert (
            alb_arn_for_app_namespace(
                k8s_client=_ingress_hostnames("APP.EXAMPLE.NET", "app.example.net"),
                elbv2_client=elbv2,
                namespace="app-env",
            )
            == _ALB_ARN
        )
        stub.assert_no_pending_responses()


def test_unresolved_second_ingress_cannot_hide_behind_the_first_alb():
    elbv2 = boto3.client("elbv2", region_name="us-west-2", aws_access_key_id="test", aws_secret_access_key="test")
    with Stubber(elbv2) as stub:
        stub.add_response(
            "describe_load_balancers",
            {"LoadBalancers": [{"LoadBalancerArn": _ALB_ARN, "DNSName": "first.example.net"}]},
            {},
        )
        with pytest.raises(ValueError, match="could not be uniquely resolved"):
            alb_arn_for_app_namespace(
                k8s_client=_ingress_hostnames("first.example.net", "unresolved.example.net"),
                elbv2_client=elbv2,
                namespace="app-env",
            )
        stub.assert_no_pending_responses()


def test_no_provisioned_alb_has_no_discovery_or_metric_reads():
    elbv2 = MagicMock(spec=["get_paginator", "describe_tags"])
    assert alb_arn_for_app_namespace(k8s_client=_ingress_hostnames(), elbv2_client=elbv2, namespace="app-env") is None
    elbv2.get_paginator.assert_not_called()
    elbv2.describe_tags.assert_not_called()
