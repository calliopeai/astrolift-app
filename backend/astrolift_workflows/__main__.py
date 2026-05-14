"""Astrolift Temporal worker process.

Run with:

    python -m astrolift_workflows

Boots Django, connects to Temporal using ``settings.TEMPORAL_ADDRESS`` /
``settings.TEMPORAL_NAMESPACE``, and starts one Worker per task queue defined
in ``astrolift_workflows.worker`` (deploy / provision / observability). All
workflows + activities are registered on each worker so any workflow can be
scheduled to any queue, with queue choice owned by the caller.

Termination is graceful on SIGTERM/SIGINT: each Worker stops accepting new
poll responses and waits for in-flight activities to drain before exit.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys


def _bootstrap_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()


async def _run() -> None:
    from django.conf import settings
    from temporalio.client import Client
    from temporalio.worker import Worker

    from astrolift_workflows.worker import (
        ACTIVITIES,
        TASK_QUEUE_DEPLOY,
        TASK_QUEUE_OBSERVABILITY,
        TASK_QUEUE_PROVISION,
        WORKFLOWS,
    )

    log = logging.getLogger("astrolift.worker")
    address = getattr(settings, "TEMPORAL_ADDRESS", "localhost:7233")
    namespace = getattr(settings, "TEMPORAL_NAMESPACE", "default")
    # ``client.py`` defaults workflow dispatches to settings.TEMPORAL_TASK_QUEUE
    # which is ``astrolift-main`` (see astrolift_workflows.client._task_queue).
    # The spec'd per-concern queues (deploy/provision/observability) are the
    # routing destinations once resolvers start passing an explicit
    # ``task_queue=`` argument. Until every call site is migrated we have to
    # poll BOTH the default queue AND the spec'd ones so workflows started
    # without explicit routing actually get picked up.
    default_queue = getattr(settings, "TEMPORAL_TASK_QUEUE", "astrolift-main")

    log.info("connecting to temporal address=%s namespace=%s", address, namespace)
    client = await Client.connect(address, namespace=namespace)

    queues = (
        TASK_QUEUE_DEPLOY,
        TASK_QUEUE_PROVISION,
        TASK_QUEUE_OBSERVABILITY,
        default_queue,
    )
    workers = [
        Worker(
            client,
            task_queue=q,
            workflows=list(WORKFLOWS),
            activities=list(ACTIVITIES),
        )
        for q in queues
    ]

    log.info(
        "starting %d workers queues=%s workflows=%d activities=%d",
        len(workers),
        ",".join(queues),
        len(WORKFLOWS),
        len(ACTIVITIES),
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    run_tasks = [asyncio.create_task(w.run()) for w in workers]
    stop_task = asyncio.create_task(stop.wait())

    done, _pending = await asyncio.wait(run_tasks + [stop_task], return_when=asyncio.FIRST_COMPLETED)

    if stop_task in done:
        log.info("shutdown signal received; draining workers")
        for w in workers:
            await w.shutdown()
        for t in run_tasks:
            t.cancel()
        return

    for t in run_tasks:
        if t.done() and t.exception():
            raise t.exception()  # type: ignore[misc]


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _bootstrap_django()
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
