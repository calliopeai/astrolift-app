"""Temporal task queue backlog as an autoscaling signal (#2435).

For every queue the worker polls, ``DescribeTaskQueue`` with backlog stats
gives the approximate backlog and the age of its oldest task (how long work
has waited to start). Workflow and activity backlogs are combined: the count
is their sum, the age the older of the two. ``publish_task_queue_metrics``
sends them to CloudWatch (ECS scaling policies) or serves them for Prometheus
(KEDA on EKS). Names are documented in docs/operators/scale-out-readiness.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

CLOUDWATCH_NAMESPACE = "Astrolift/Temporal"
BACKLOG_METRIC = "ApproximateBacklogCount"
AGE_METRIC = "ApproximateBacklogAgeSeconds"
PROM_BACKLOG = "astrolift_temporal_task_queue_backlog"
PROM_AGE = "astrolift_temporal_task_queue_backlog_age_seconds"


@dataclass(frozen=True)
class QueueBacklog:
    task_queue: str
    backlog: int
    age_seconds: float


async def collect(client, queues) -> list[QueueBacklog]:
    """One sample per queue; a queue the server returns no stats for is skipped."""
    from temporalio.api.enums.v1 import TaskQueueType
    from temporalio.api.taskqueue.v1 import TaskQueue
    from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

    samples = []
    for name in queues:
        stats = []
        for kind in (TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY):
            response = await client.workflow_service.describe_task_queue(
                DescribeTaskQueueRequest(
                    namespace=client.namespace,
                    task_queue=TaskQueue(name=name),
                    task_queue_type=kind,
                    report_stats=True,
                )
            )
            if response.HasField("stats"):
                stats.append(response.stats)
        if not stats:
            log.warning(
                "temporal returned no backlog stats for task queue %s (server too old to report them?)", name
            )
            continue
        samples.append(
            QueueBacklog(
                task_queue=name,
                backlog=sum(s.approximate_backlog_count for s in stats),
                age_seconds=max(s.approximate_backlog_age.ToTimedelta().total_seconds() for s in stats),
            )
        )
    return samples


def put_cloudwatch(
    cloudwatch, samples, temporal_namespace: str, namespace: str = CLOUDWATCH_NAMESPACE
) -> None:
    data = []
    for s in samples:
        dimensions = [
            {"Name": "TaskQueue", "Value": s.task_queue},
            {"Name": "TemporalNamespace", "Value": temporal_namespace},
        ]
        data.append(
            {"MetricName": BACKLOG_METRIC, "Dimensions": dimensions, "Value": s.backlog, "Unit": "Count"}
        )
        data.append(
            {"MetricName": AGE_METRIC, "Dimensions": dimensions, "Value": s.age_seconds, "Unit": "Seconds"}
        )
    if data:
        cloudwatch.put_metric_data(Namespace=namespace, MetricData=data)


def prometheus_gauges(registry):
    from prometheus_client import Gauge

    labels = ("task_queue", "temporal_namespace")
    return (
        Gauge(
            PROM_BACKLOG,
            "Approximate Temporal task queue backlog (workflow + activity tasks)",
            labels,
            registry=registry,
        ),
        Gauge(
            PROM_AGE, "Age of the oldest task in the Temporal task queue backlog", labels, registry=registry
        ),
    )


def set_prometheus(gauges, samples, temporal_namespace: str) -> None:
    backlog, age = gauges
    for s in samples:
        backlog.labels(s.task_queue, temporal_namespace).set(s.backlog)
        age.labels(s.task_queue, temporal_namespace).set(s.age_seconds)
