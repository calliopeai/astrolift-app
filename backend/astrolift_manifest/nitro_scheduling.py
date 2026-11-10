"""Scheduling shared by explicitly selected Nitro workloads and agent tasks."""


def apply_nitro_scheduling(pod_spec: dict) -> None:
    """Permit only selected Nitro runtimes onto the dedicated sandbox pool."""
    if pod_spec.get("runtimeClassName") not in {"isolated-sandbox", "confidential-sandbox"}:
        return
    toleration = {
        "key": "astrolift.dev/nitro-only",
        "operator": "Equal",
        "value": "true",
        "effect": "NoSchedule",
    }
    tolerations = pod_spec.setdefault("tolerations", [])
    if toleration not in tolerations:
        tolerations.append(toleration)
