"""Original operation execution with copied Activity context and bounded drain."""

import asyncio
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor

from temporalio import activity
from temporalio.exceptions import ApplicationError


@activity.defn(name="astrolift.domain_app_route.reconcile")
async def reconcile_domain_app_route(operation_id: str) -> dict[str, str]:
    from astrolift_lifecycle.domain_app_route_execution import reconcile

    activity.heartbeat()
    loop = asyncio.get_running_loop()
    closed = threading.Event()

    def heartbeat():
        if not closed.is_set():
            activity.heartbeat()

    def current():
        if closed.is_set() or activity.is_cancelled():
            raise ApplicationError("Domain route execution is no longer admitted.", non_retryable=True)
        loop.call_soon_threadsafe(heartbeat)

    def execute():
        from django.db import connections
        from django.db.utils import OperationalError

        # This executor is private to this invocation. Close only its own
        # default connection; never sweep other aliases or request threads.
        try:
            return reconcile(operation_id, checkpoint=current)
        except OperationalError as exc:
            if getattr(exc.__cause__, "pgcode", None) == "55P03":
                # A committed parent writer may overlap this read. Preserve
                # NOWAIT and every existing effect reservation; the workflow
                # may reread once the writer releases, never repeat a send.
                return {
                    "operation_id": operation_id,
                    "status": "pending",
                    "reason": "CURRENT_ROUTE_LOCK_BUSY",
                }
            raise
        except BaseException:
            from astrolift_lifecycle.domain_app_route_store import failed

            try:
                failed(operation_id)
            except Exception:
                pass
            raise
        finally:
            connections["default"].close()

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="domain-route")
    context = contextvars.copy_context()
    pending = loop.run_in_executor(executor, context.run, execute)
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        closed.set()
        # A received original native ACK may still be retained as evidence.
        # Cancellation cannot stop an already sent RPC; all later effects
        # meet the closed checkpoint. Drain for a finite interval only.
        try:
            await asyncio.wait_for(asyncio.shield(pending), timeout=6)
        except (Exception, asyncio.CancelledError):
            pass
        raise
    except Exception:
        raise ApplicationError(
            "Current domain route configuration is unavailable.", non_retryable=True
        ) from None
    finally:
        closed.set()
        executor.shutdown(wait=False)
        if not pending.done():

            def consume(completed):
                if not completed.cancelled():
                    completed.exception()

            pending.add_done_callback(consume)
