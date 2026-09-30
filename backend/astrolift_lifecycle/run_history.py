"""Durable observations of real deployment work, independent of pod lifetime."""

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


@contextmanager
def phase(deployment_id: int, name: str):
    record(deployment_id, name, "started")
    try:
        yield
    except Exception:
        # Keep the original exception in the workflow's existing failure path.
        record(deployment_id, name, "failed", "Activity failed; see deployment failure reason.")
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
