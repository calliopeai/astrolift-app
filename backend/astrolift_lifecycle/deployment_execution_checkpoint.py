"""Per-activity run checks for private SDK callbacks, never persisted or global."""

from contextlib import contextmanager
from contextvars import copy_context
from threading import Event, get_ident, local

from django.db import connections
from temporalio import activity

from astrolift_lifecycle.deployment_execution_receipt import (
    DeploymentExecutionError,
    admitted_deployment_execution,
)


class _ScopedDeploymentExecutionCheckpoint:
    def __init__(self, input):
        self.input = input
        self.context = copy_context()
        self.owner_thread = get_ident()
        self.closed = Event()
        # Nesting describes only connection ownership, never cached admission.
        self.thread_scope = local()

    def _live(self):
        if self.closed.is_set() or activity.is_cancelled():
            raise DeploymentExecutionError()

    def _admitted(self):
        self._live()
        with admitted_deployment_execution(self.input):
            pass
        self._live()

    def _run(self, checks):
        if self.closed.is_set():
            raise DeploymentExecutionError()
        if get_ident() == self.owner_thread or getattr(self.thread_scope, "owned", False):
            return self.context.copy().run(checks)
        connection = connections["default"]
        # A foreign callback thread's existing connection/transaction is not ours.
        if connection.connection is not None or connection.in_atomic_block:
            raise DeploymentExecutionError()
        self.thread_scope.owned = True
        try:
            return self.context.copy().run(checks)
        finally:
            self.thread_scope.owned = False
            connection.close()

    def __call__(self):
        return self._run(self._admitted)

    def compose(self, checks):
        """Keep all DB-only checks within the same owned callback lifetime."""
        if not callable(checks):
            raise DeploymentExecutionError()

        def complete():
            self._admitted()
            if checks() is not None:
                raise DeploymentExecutionError()
            self._admitted()

        def current():
            return self._run(complete)

        return current


def compose_execution_checkpoint(checkpoint, checks):
    """Recognize only our trusted scope; retain standalone private callbacks."""
    if type(checkpoint) is _ScopedDeploymentExecutionCheckpoint:
        return checkpoint.compose(checks)
    return checks


@contextmanager
def deployment_execution_checkpoint(input):
    """Capture inside an actual activity, then re-admit on every callback.

    Full metadata-thread callbacks own and close their reopened DB connections;
    nested final source checks keep those transactions open. Owner-thread
    transactions stay untouched. Cancellation and scope closure refuse further
    admission even when a copied SDK context remains available.
    """
    activity.info()
    if activity.is_cancelled():
        raise DeploymentExecutionError()
    with admitted_deployment_execution(input):
        pass
    current = _ScopedDeploymentExecutionCheckpoint(input)
    try:
        yield current
    finally:
        current.closed.set()
