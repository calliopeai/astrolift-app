"""Durable observations of real deployment work, independent of pod lifetime."""

import logging
from contextlib import contextmanager


def record(deployment_id: int, phase: str, event: str, message: str = "", detail=None):
    from astrolift_lifecycle.models import DeploymentLog

    return DeploymentLog.objects.create(
        deployment_id=deployment_id,
        status="",
        phase=phase,
        event=event,
        message=message,
        detail=detail,
    )


def record_failure(deployment_id: int, name: str, message: str):
    """A diagnostic write must not replace an already-established failure."""
    try:
        record(deployment_id, name, "failed", message)
    except Exception:
        logging.getLogger(__name__).warning("Could not journal failed deployment activity")


@contextmanager
def phase(deployment_id: int, name: str):
    record(deployment_id, name, "started")
    try:
        yield
    except Exception:
        # Keep the original exception in the workflow's existing failure path.
        record_failure(deployment_id, name, "Activity failed; see deployment failure reason.")
        raise
    else:
        record(deployment_id, name, "completed")


def observed_phase(name: str):
    """Wrap synchronous activity work; every retry remains an explicit observation."""
    from functools import wraps

    def decorate(fn):
        @wraps(fn)
        def wrapped(deployment_id, *args, **kwargs):
            with phase(deployment_id, name):
                result = fn(deployment_id, *args, **kwargs)
                if isinstance(result, dict):
                    record(deployment_id, name, "summary", detail=result)
                return result

        return wrapped

    return decorate
