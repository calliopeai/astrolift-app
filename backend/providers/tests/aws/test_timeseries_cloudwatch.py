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
