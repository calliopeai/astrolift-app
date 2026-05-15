"""Shared kube-API helpers for cluster health surfacing (#68 slice 1).

The Pod / Event listing endpoints are cloud-neutral once the driver
has a kubernetes client in hand — only the auth bootstrap differs
(IRSA exec_plugin, Workload Identity, Federated Identity, plain
kubeconfig). Drivers call these helpers with their resolved client
and the platform-managed namespace set; the helpers do the rest.
"""

from __future__ import annotations

from typing import Any

from _sdk.cluster import ClusterEvent, PodPhaseSummary


def pod_phase_summary_from_client(
    k8s_client: Any,
    *,
    namespaces: list[str],
) -> list[PodPhaseSummary]:
    """Aggregate pod phases across the requested namespaces.

    Per-namespace failure (e.g. namespace missing) is silently
    skipped — the operator sees the namespaces that ARE reachable
    rather than the whole card erroring out on one stale binding.
    """
    out: list[PodPhaseSummary] = []
    for ns in namespaces:
        try:
            pods = k8s_client.list_namespaced_pod(namespace=ns)
        except Exception:  # noqa: BLE001
            continue
        counts: dict[str, int] = {}
        for item in getattr(pods, "items", []) or []:
            phase = "Unknown"
            status = getattr(item, "status", None)
            if status is not None:
                phase = getattr(status, "phase", None) or "Unknown"
            counts[phase] = counts.get(phase, 0) + 1
        for phase, count in sorted(counts.items()):
            out.append(
                PodPhaseSummary(namespace=ns, phase=phase, count=count),
            )
    return out


def events_from_client(
    k8s_client: Any,
    *,
    namespaces: list[str],
    event_type: str | None = "Warning",
    limit: int = 50,
) -> list[ClusterEvent]:
    """List recent K8s Events across the requested namespaces.

    Default ``event_type=Warning`` covers the standard triage flow;
    ``None`` returns all types. Results are sorted by last_seen
    descending, truncated to ``limit``.
    """
    events: list[ClusterEvent] = []
    for ns in namespaces:
        try:
            resp = k8s_client.list_namespaced_event(namespace=ns)
        except Exception:  # noqa: BLE001
            continue
        for raw in getattr(resp, "items", []) or []:
            ev_type = getattr(raw, "type", "") or ""
            if event_type and ev_type != event_type:
                continue
            involved = getattr(raw, "involved_object", None)
            io_str = (
                f"{getattr(involved, 'kind', '')}/"
                f"{getattr(involved, 'name', '')}"
                if involved is not None
                else ""
            )
            events.append(
                ClusterEvent(
                    namespace=ns,
                    name=getattr(
                        getattr(raw, "metadata", None), "name", "",
                    )
                    or "",
                    reason=getattr(raw, "reason", "") or "",
                    message=getattr(raw, "message", "") or "",
                    type=ev_type,
                    count=int(getattr(raw, "count", 0) or 0),
                    first_seen=str(
                        getattr(raw, "first_timestamp", "") or "",
                    ),
                    last_seen=str(
                        getattr(raw, "last_timestamp", "") or "",
                    ),
                    involved_object=io_str,
                ),
            )
    events.sort(key=lambda e: e.last_seen, reverse=True)
    return events[:limit]


PLATFORM_NAMESPACE = "astrolift-system"
"""The platform's own namespace. When the caller passes
``namespaces=None`` to a driver health method, this is the default
single-element set the driver scans."""


def default_namespaces(namespaces: list[str] | None) -> list[str]:
    """Resolve the standard fallback for health-method ``namespaces``."""
    return list(namespaces) if namespaces else [PLATFORM_NAMESPACE]
