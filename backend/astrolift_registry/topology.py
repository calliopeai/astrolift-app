"""An app's topology kind, for the Apps list's Kind column and filter (#2149).

A port of ``classifyTopology`` in ``frontend/lib/topology.ts``; the two must
answer the same, so a change to one is a change to both. The kind only picks
how an app is drawn and filtered, never what it may do.

The list classifies from workloads alone, as the browser list did (it never
passed managed services), so ``managed_services`` is optional here too.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

__all__ = ["TOPOLOGY_KINDS", "classify_topology"]

TOPOLOGY_KINDS = (
    "service",
    "service-data",
    "service-worker",
    "microservices",
    "service-agent",
    "agent",
    "functions",
    "scheduled",
    "task",
    "workflow",
    "mixed",
)

_WORKER_NAME = re.compile(r"(worker|consumer|processor|queue|celery|sidekiq|jobs?)\b", re.IGNORECASE)
_DATA_NAME = re.compile(r"(postgres|mysql|maria|redis|mongo|valkey|db|database)", re.IGNORECASE)
_QUEUE_SERVICES = frozenset({"queue", "sqs", "rabbitmq", "kafka"})


def classify_topology(workloads: Sequence[tuple[str, str]], managed_services: Iterable[str] = ()) -> str:
    """``workloads`` as ``(kind, name)`` pairs; returns one of :data:`TOPOLOGY_KINDS`."""
    if not workloads:
        return "service"
    services = list(managed_services)

    agents = [w for w in workloads if w[0] == "agent"]
    # A self-hosted database runs as a statefulset; it is data, not a service.
    self_hosted_data = [w for w in workloads if w[0] == "statefulset" and _DATA_NAME.search(w[1])]
    long_running = [
        w
        for w in workloads
        if w[0] == "deployment" or (w[0] == "statefulset" and not _DATA_NAME.search(w[1]))
    ]
    has_data = bool(self_hosted_data) or any(s not in _QUEUE_SERVICES and s != "email" for s in services)
    has_queue = any(s in _QUEUE_SERVICES for s in services)

    if agents:
        if long_running:
            return "service-agent"
        return "agent" if len(agents) == len(workloads) else "mixed"

    def only(kind: str) -> bool:
        return all(w[0] == kind or w in self_hosted_data for w in workloads)

    if only("function"):
        return "functions"
    if only("workflow"):
        return "workflow"
    if only("cronjob"):
        return "scheduled"
    if all(w[0] in ("job", "task") for w in workloads):
        return "task"

    if len(long_running) != len(workloads) - len(self_hosted_data):
        return "mixed"

    if len(long_running) == 1:
        return "service-data" if has_data else "service"
    workers = sum(1 for w in long_running if _WORKER_NAME.search(w[1]))
    if len(long_running) - workers == 1:
        return "service-worker"
    # Two services sharing a queue and neither named for it: still web + worker.
    if workers == 0 and has_queue and len(long_running) == 2:
        return "service-worker"
    return "microservices"
