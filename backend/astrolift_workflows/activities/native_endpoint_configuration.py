"""Registered Endpoint-only native deployment steps for DeployAppWorkflow."""

import re
from contextlib import contextmanager
from contextvars import copy_context
from threading import Event, Thread

from _sdk._telemetry import heartbeat_sink
from temporalio import activity
from temporalio.exceptions import ApplicationError

from astrolift_workflows.inputs import DeployAppInput
from astrolift_workflows.native_identity_inputs import NativeConfigurationReceipt

#: Prefix of every refusal raised here; the workflow's refusal activity keeps it.
REFUSED = "NATIVE_CONFIGURATION_REFUSED:"
_FIXED_REASON = re.compile(r"[A-Z][A-Z0-9_]{2,63}")


def _refused(error):
    """Only fixed upper-case codes leave; native text never does."""
    from core.permissions import PermissionDenied

    reason = "CURRENT_AUTHORITY_REFUSED" if isinstance(error, PermissionDenied) else str(error)
    return ApplicationError(
        REFUSED + (reason if _FIXED_REASON.fullmatch(reason) else "CONFIGURATION_UNCONFIRMED"),
        non_retryable=True,
    )


def execute_native_endpoint_configuration(input, *, ports):
    # Django-backed executor loads at call time; the workflow sandbox imports this module.
    from astrolift_workflows.native_endpoint_configuration import execute_native_endpoint_configuration as run

    return run(input, ports=ports)


def _server_ports():
    from astrolift_workflows.native_endpoint_configuration import NativeConfigurationPorts

    return NativeConfigurationPorts()


@contextmanager
def _heartbeat_scope():
    timeout = activity.info().heartbeat_timeout
    if timeout is None or timeout.total_seconds() < 1 or activity.is_cancelled():
        raise ValueError("NATIVE_HEARTBEAT_REQUIRED")
    stopped, failed = Event(), Event()
    context = copy_context()
    activity.heartbeat()

    def pulse():
        while not stopped.wait(min(1.0, timeout.total_seconds() / 4)):
            try:
                if context.copy().run(activity.is_cancelled):
                    return
                context.copy().run(activity.heartbeat)
            except Exception:
                failed.set()
                return

    thread = Thread(target=pulse, name="native-configuration-heartbeat", daemon=True)
    thread.start()
    try:
        # Existing driver loops inherit the same sink, with empty details only.
        with heartbeat_sink(lambda _detail: activity.heartbeat()):
            yield
    finally:
        stopped.set()
        # The supported sync SDK heartbeat waits at most ten seconds for its
        # event-loop enqueue. Drain that in-flight call before closing scope.
        thread.join(timeout=11)
        if thread.is_alive() or failed.is_set():
            raise ValueError("NATIVE_HEARTBEAT_UNCONFIRMED")


@activity.defn(name="astrolift.app.configure_native_endpoint", no_thread_cancel_exception=True)
def configure_native_endpoint_deployment(input: DeployAppInput) -> NativeConfigurationReceipt:
    try:
        with _heartbeat_scope():
            return execute_native_endpoint_configuration(input, ports=_server_ports())
    except Exception as error:
        raise _refused(error) from None


@activity.defn(name="astrolift.app.complete_native_endpoint")
def complete_native_endpoint_deployment(input: DeployAppInput, receipt: NativeConfigurationReceipt) -> None:
    from astrolift_workflows.native_endpoint_configuration import complete_native_endpoint_configuration

    try:
        complete_native_endpoint_configuration(input, receipt)
    except Exception as error:
        raise _refused(error) from None
