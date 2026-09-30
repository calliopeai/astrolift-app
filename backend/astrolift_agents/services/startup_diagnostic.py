"""Persist scoped pod startup observations before ephemeral workloads disappear."""

import logging

from django.utils import timezone

log = logging.getLogger(__name__)


def observe_startup(row, *, cluster, namespace, task_id=""):
    from core.cluster_observability import list_app_pods

    try:
        pods = list_app_pods(cluster=cluster, namespace=namespace, app_slug=str(row.guid), task_id=task_id)
    except Exception:  # noqa: BLE001 — a failed probe must not replace the last observation
        log.warning("agent startup observation failed for %s", row.guid, exc_info=True)
        return
    record_startup(row, pods)
    return pods


def record_startup(row, pods):
    pod = next((p for p in pods if p.ready), None) or next(iter(pods), None)
    phase, reason, message, name = "Pending", "AwaitingPod", "Waiting for the workload pod", ""
    if pod is None and row.startup_diagnostic:
        return
    if pod is not None:
        name = pod.name
        phase = pod.phase if pod.phase in {"Pending", "Running", "Succeeded", "Failed"} else "Unknown"
        reason = getattr(pod, "scheduling_reason", "") or ""
        message = getattr(pod, "scheduling_message", "") or ""
        if pod.ready:
            phase, reason, message = "Running", "", ""
        elif not reason and phase in {"Succeeded", "Failed"}:
            terminated = next((c for c in pod.container_statuses if c.state == "terminated"), None)
            reason = getattr(terminated, "terminated_reason", "") or phase
            message = reason
        elif not reason:
            if phase == "Running":
                phase = "Starting"
            waiting = next((c for c in pod.container_statuses if c.state == "waiting"), None)
            reason = getattr(waiting, "waiting_reason", "") or ""
            # Container waiting messages may contain configuration values; expose the reason only.
            message = reason or (
                "Waiting for pod readiness" if phase == "Starting" else "Waiting for pod startup"
            )
    snapshot = {"phase": phase[:32], "reason": reason[:128], "message": message[:2000], "podName": name[:255]}
    snapshot["observedAt"] = timezone.now().isoformat()
    row.startup_diagnostic = snapshot
    row.save(update_fields=["startup_diagnostic", "updated_at", "version"])


def startup_failure_suffix(row):
    snapshot = row.startup_diagnostic or {}
    message = snapshot.get("message", "")
    return f"; startup: {message}" if message else ""
