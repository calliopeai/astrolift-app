"""
publish_task_queue_metrics: Temporal backlog per task queue for autoscaling (#2435).

Usage:
    python manage.py publish_task_queue_metrics --sink cloudwatch [--interval 30] [--once]
    python manage.py publish_task_queue_metrics --sink prometheus [--port 9464] [--interval 15]

``cloudwatch`` puts ``Astrolift/Temporal`` metrics for ECS scaling policies;
``prometheus`` serves them on ``:<port>/metrics`` for KEDA. Run one copy per
install (a sidecar next to a worker, or its own small task); every copy
publishes the same gauge, so a duplicate is harmless. Names and scaling
policy examples: docs/operators/scale-out-readiness.md.
"""

from __future__ import annotations

import asyncio
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Publish Temporal task queue backlog and backlog age as autoscaling metrics."

    def add_arguments(self, parser):
        parser.add_argument("--sink", choices=["cloudwatch", "prometheus"], required=True)
        parser.add_argument("--interval", type=float, default=30.0, help="Seconds between samples.")
        parser.add_argument("--port", type=int, default=9464, help="Prometheus listen port.")
        parser.add_argument("--once", action="store_true", help="Publish one sample and exit (cloudwatch).")

    def handle(self, *args, **options):
        asyncio.run(self._run(options))

    async def _run(self, options):
        from temporalio.client import Client

        from astrolift_workflows import task_queue_metrics as m
        from astrolift_workflows.worker import polled_task_queues

        namespace = getattr(settings, "TEMPORAL_NAMESPACE", "default")
        client = await Client.connect(
            getattr(settings, "TEMPORAL_ADDRESS", "localhost:7233"), namespace=namespace
        )
        if options["sink"] == "cloudwatch":
            import boto3

            cloudwatch = boto3.client("cloudwatch")

            def publish(samples):
                m.put_cloudwatch(cloudwatch, samples, namespace)

        else:
            from prometheus_client import CollectorRegistry, start_http_server

            registry = CollectorRegistry()
            gauges = m.prometheus_gauges(registry)
            start_http_server(options["port"], registry=registry)

            def publish(samples):
                m.set_prometheus(gauges, samples, namespace)

        queues = polled_task_queues()
        while True:
            try:
                samples = await m.collect(client, queues)
                await asyncio.to_thread(publish, samples)
                log.debug("task queue backlog %s", {s.task_queue: s.backlog for s in samples})
            except Exception:  # noqa: BLE001 (a failed sample must not stop the next one)
                if options["once"]:
                    raise
                log.exception("task queue metrics sample failed")
            if options["once"]:
                return
            await asyncio.sleep(options["interval"])
