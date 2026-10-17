"""Astrolift Temporal worker process.

Run with:

    python -m astrolift_workflows

Boots Django, connects to Temporal using ``settings.TEMPORAL_ADDRESS`` /
``settings.TEMPORAL_NAMESPACE``, and starts one Worker per task queue defined
in ``astrolift_workflows.worker`` (deploy / provision / observability). All
workflows + activities are registered on each worker so any workflow can be
scheduled to any queue, with queue choice owned by the caller.

Termination is graceful on SIGTERM/SIGINT: every Worker stops polling at
once, in-flight activities get ``ASTROLIFT_WORKER_DRAIN_SECONDS`` (default 25)
to finish, then the ones still running are cancelled and Temporal retries
them on another worker; an activity that checkpoints in its heartbeat
details resumes from the last one. Keep the container stop timeout above the
drain window (docs/operators/scale-out-readiness.md).
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


def drain_timeout():
    """How long a stopping worker lets in-flight activities finish (#2435).

    Temporal's default is zero: shutdown cancels every running activity at
    once, so each scale-in threw away work that was about to complete. 25s
    fits inside ECS's default 30s stop timeout.
    """
    from datetime import timedelta

    return timedelta(seconds=float(os.environ.get("ASTROLIFT_WORKER_DRAIN_SECONDS", "25")))


def release_db_connections():
    """Activity interceptor: a synchronous activity releases its DB connection.

    Sync activities run on executor threads that live as long as the worker,
    and Django only closes connections at request boundaries, so each thread
    held a connection forever (and kept a dead one after a failover). Closing
    around every activity applies ``CONN_MAX_AGE`` and bounds a worker's
    connections to the activities actually running (#2435).
    """
    import functools
    import inspect

    from django.db import close_old_connections
    from temporalio.worker import ActivityInboundInterceptor, Interceptor

    class Inbound(ActivityInboundInterceptor):
        async def execute_activity(self, input):
            fn = input.fn
            if not inspect.iscoroutinefunction(fn):

                @functools.wraps(fn)
                def run(*args):
                    close_old_connections()
                    try:
                        return fn(*args)
                    finally:
                        close_old_connections()

                input.fn = run
            return await super().execute_activity(input)

    class ReleaseDbConnections(Interceptor):
        def intercept_activity(self, next):
            return Inbound(next)

    return ReleaseDbConnections()


def build_workers(client, queues):
    """One Worker per queue, every workflow and activity on each.

    A synchronous activity needs a thread executor or Worker refuses to start
    (``validate_deployment_identity_origin`` is one, since #2278), so all of
    them share one. Tests build workers through here, so a missing executor
    fails a test instead of crash-looping the worker on deploy.
    """
    from concurrent.futures import ThreadPoolExecutor

    from temporalio.worker import Worker

    from astrolift_workflows.worker import ACTIVITIES, WORKFLOWS

    executor = ThreadPoolExecutor(max_workers=int(os.environ.get("ASTROLIFT_SYNC_ACTIVITY_THREADS", "16")))
    interceptor = release_db_connections()
    return [
        Worker(
            client,
            task_queue=q,
            workflows=list(WORKFLOWS),
            activities=list(ACTIVITIES),
            activity_executor=executor,
            graceful_shutdown_timeout=drain_timeout(),
            interceptors=[interceptor],
        )
        for q in queues
    ]


async def drain(workers) -> None:
    """Stop every worker together so the drain window is shared, not summed."""
    await asyncio.gather(*(w.shutdown() for w in workers))


async def _run() -> None:
    from django.conf import settings
    from temporalio.client import Client

    from astrolift_workflows.worker import ACTIVITIES, WORKFLOWS, polled_task_queues

    log = logging.getLogger("astrolift.worker")
    address = getattr(settings, "TEMPORAL_ADDRESS", "localhost:7233")
    namespace = getattr(settings, "TEMPORAL_NAMESPACE", "default")

    log.info("connecting to temporal address=%s namespace=%s", address, namespace)
    client = await Client.connect(address, namespace=namespace)

    # Register the platform's scheduled workflows (spec 37). Non-fatal: a
    # registration failure must NOT crash the worker — log and keep serving.
    from astrolift_workflows.schedule_boot import register_schedules

    try:
        await register_schedules(client)
    except Exception:  # noqa: BLE001 — registrar must never take down the worker
        log.exception("schedule registrar failed; worker continuing without schedule sync")

    # ``client.py`` defaults workflow dispatches to settings.TEMPORAL_TASK_QUEUE
    # which is ``astrolift-main`` (see astrolift_workflows.client._task_queue).
    # The spec'd per-concern queues (deploy/provision/observability) are the
    # routing destinations once resolvers start passing an explicit
    # ``task_queue=`` argument. Until every call site is migrated we have to
    # poll BOTH the default queue AND the spec'd ones so workflows started
    # without explicit routing actually get picked up.
    queues = polled_task_queues()
    workers = build_workers(client, queues)

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
        await drain(workers)
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
