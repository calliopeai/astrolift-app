"""Container-level detail for a failed rollout or health check (#2607).

A workload's conditions name the Deployment or StatefulSet, not the container
that failed. This module picks the containers of that workload that are not
ready from the pod list and renders one short clause for the deploy reason.
Pure functions over duck-typed pod objects (``PodInfo`` shape), so the
formatting is testable without Temporal, a cluster or a database.
"""

from __future__ import annotations

from typing import Any

_MAX_CONTAINERS = 3


def container_problem(container: Any) -> str:
    """Why one container is not ready, in a few words.

    Names the check where the status says it: a container that is running
    but not ready is failing its readiness probe. A probe failure that
    restarted the container shows up as the last restart reason.
    """
    state = getattr(container, "state", "") or ""
    if state == "waiting":
        return getattr(container, "waiting_reason", "") or "waiting"
    if state == "terminated":
        reason = getattr(container, "terminated_reason", "") or "terminated"
        code = getattr(container, "terminated_exit_code", None)
        return f"{reason} (exit {code})" if code is not None else reason
    restarts = list(getattr(container, "last_restart_reasons", None) or [])
    if restarts:
        return f"running, not ready; last restart {restarts[0]}"
    return "running, not ready (readiness probe not passing)"


def container_detail(pods: list[Any], workload: str) -> str:
    """``; container a: CrashLoopBackOff; container b: ...`` for a workload.

    Empty when no pod of ``workload`` has a container that is not ready, so
    a caller can append the result unconditionally. Pods with no workload
    label are included; a pod owned by another workload is not.
    """
    clauses: list[str] = []
    for pod in pods:
        owner = getattr(pod, "workload", "") or ""
        if owner and owner != workload:
            continue
        for container in getattr(pod, "container_statuses", None) or []:
            if getattr(container, "ready", False):
                continue
            clauses.append(f"container {container.name}: {container_problem(container)}")
    if not clauses:
        return ""
    shown = clauses[:_MAX_CONTAINERS]
    extra = len(clauses) - len(shown)
    if extra:
        shown.append(f"{extra} more not ready")
    return "; " + "; ".join(shown)
