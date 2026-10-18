"""The backlog metric job feeds worker autoscaling (#2435).

The names asserted here are the contract the installer's ECS scaling
policies and KEDA triggers are written against.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from google.protobuf.duration_pb2 import Duration
from prometheus_client import CollectorRegistry
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueueStats
from temporalio.api.workflowservice.v1 import DescribeTaskQueueResponse

from astrolift_workflows import task_queue_metrics as m


def _stats(count: int, age_seconds: int) -> DescribeTaskQueueResponse:
    age = Duration()
    age.FromTimedelta(timedelta(seconds=age_seconds))
    return DescribeTaskQueueResponse(
        stats=TaskQueueStats(approximate_backlog_count=count, approximate_backlog_age=age)
    )


class _Service:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    async def describe_task_queue(self, request):
        self.requests.append(request)
        return self.responses[(request.task_queue.name, request.task_queue_type)]


WF, ACT = TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY


def _client(responses):
    return SimpleNamespace(namespace="astro", workflow_service=_Service(responses))


async def test_workflow_and_activity_backlogs_combine_per_queue():
    client = _client(
        {
            ("astrolift.deploy", WF): _stats(3, 5),
            ("astrolift.deploy", ACT): _stats(7, 42),
            ("astrolift-main", WF): _stats(0, 0),
            ("astrolift-main", ACT): _stats(0, 0),
        }
    )

    samples = await m.collect(client, ("astrolift.deploy", "astrolift-main"))

    assert samples == [
        m.QueueBacklog("astrolift.deploy", backlog=10, age_seconds=42.0),
        m.QueueBacklog("astrolift-main", backlog=0, age_seconds=0.0),
    ]
    assert all(r.report_stats and r.namespace == "astro" for r in client.workflow_service.requests)


async def test_a_queue_without_stats_is_skipped_not_published_as_zero():
    # A zero would tell the scaler there is no work and scale workers in.
    client = _client({("q", WF): DescribeTaskQueueResponse(), ("q", ACT): DescribeTaskQueueResponse()})

    assert await m.collect(client, ("q",)) == []


def test_cloudwatch_names_and_dimensions():
    sent = {}
    cloudwatch = SimpleNamespace(put_metric_data=lambda **kw: sent.update(kw))

    m.put_cloudwatch(cloudwatch, [m.QueueBacklog("astrolift.deploy", 10, 42.0)], "astro")

    dimensions = [
        {"Name": "TaskQueue", "Value": "astrolift.deploy"},
        {"Name": "TemporalNamespace", "Value": "astro"},
    ]
    assert sent == {
        "Namespace": "Astrolift/Temporal",
        "MetricData": [
            {"MetricName": "ApproximateBacklogCount", "Dimensions": dimensions, "Value": 10, "Unit": "Count"},
            {
                "MetricName": "ApproximateBacklogAgeSeconds",
                "Dimensions": dimensions,
                "Value": 42.0,
                "Unit": "Seconds",
            },
        ],
    }


def test_nothing_is_put_without_samples():
    cloudwatch = SimpleNamespace(put_metric_data=lambda **kw: pytest.fail("empty put"))

    m.put_cloudwatch(cloudwatch, [], "astro")


def test_prometheus_names_and_labels():
    registry = CollectorRegistry()
    gauges = m.prometheus_gauges(registry)

    m.set_prometheus(gauges, [m.QueueBacklog("astrolift.deploy", 10, 42.0)], "astro")

    labels = {"task_queue": "astrolift.deploy", "temporal_namespace": "astro"}
    assert registry.get_sample_value("astrolift_temporal_task_queue_backlog", labels) == 10
    assert registry.get_sample_value("astrolift_temporal_task_queue_backlog_age_seconds", labels) == 42


def test_the_command_publishes_every_polled_queue_once(monkeypatch, settings):
    import boto3
    from temporalio.client import Client

    settings.TEMPORAL_NAMESPACE = "astro"
    settings.TEMPORAL_TASK_QUEUE = "astrolift-main"
    queues = ("astrolift.deploy", "astrolift.provision", "astrolift.observability", "astrolift-main")
    client = _client({(q, t): _stats(1, 2) for q in queues for t in (WF, ACT)})

    async def connect(address, namespace):
        assert namespace == "astro"
        return client

    puts = []
    monkeypatch.setattr(Client, "connect", connect)
    monkeypatch.setattr(
        boto3, "client", lambda name: SimpleNamespace(put_metric_data=lambda **kw: puts.append(kw))
    )

    call_command("publish_task_queue_metrics", "--sink", "cloudwatch", "--once")

    [put] = puts
    published = {
        d["Dimensions"][0]["Value"] for d in put["MetricData"] if d["MetricName"] == "ApproximateBacklogCount"
    }
    assert published == set(queues)
    assert {d["Value"] for d in put["MetricData"] if d["MetricName"] == "ApproximateBacklogCount"} == {2}
