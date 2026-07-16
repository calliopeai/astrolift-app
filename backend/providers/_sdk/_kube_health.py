"""Shared kube-API helpers for cluster health surfacing (#68 slice 1).

The Pod / Event listing endpoints are cloud-neutral once the driver
has a kubernetes client in hand — only the auth bootstrap differs
(IRSA exec_plugin, Workload Identity, Federated Identity, plain
kubeconfig). Drivers call these helpers with their resolved client
and the platform-managed namespace set; the helpers do the rest.
"""

from __future__ import annotations

from typing import Any

from _sdk.cluster import ClusterEvent, PodPhaseSummary, WorkloadHealth


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
        except Exception:
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
        except Exception:
            continue
        for raw in getattr(resp, "items", []) or []:
            ev_type = getattr(raw, "type", "") or ""
            if event_type and ev_type != event_type:
                continue
            involved = getattr(raw, "involved_object", None)
            io_str = f"{getattr(involved, 'kind', '')}/{getattr(involved, 'name', '')}" if involved is not None else ""
            events.append(
                ClusterEvent(
                    namespace=ns,
                    name=getattr(
                        getattr(raw, "metadata", None),
                        "name",
                        "",
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


def workload_health_from_client(
    k8s_client: Any,
    *,
    namespaces: list[str],
    now_iso: str | None = None,
) -> list[WorkloadHealth]:
    """Per-Deployment health rollup across ``namespaces`` (#362).

    The driver client is expected to expose
    ``list_namespaced_deployment(namespace=...)`` and
    ``list_namespaced_pod(namespace=..., label_selector=...)`` in the
    shape the official ``kubernetes`` Python client returns
    (``items`` list of objects with ``.metadata``, ``.spec``,
    ``.status``). Per-namespace failures (RBAC, missing namespace)
    are swallowed and the namespace is skipped — partial visibility
    is the operator's expected baseline during multi-tenant rollout.

    ``restart_count_24h`` walks every owned pod's
    ``container_statuses[*].last_state.terminated.finished_at`` and
    sums restarts whose last termination falls in the trailing 24h.
    Clients that surface ``restart_count`` but not termination
    timestamps contribute their full ``restart_count`` (best-effort);
    the UI surfaces this as a single integer either way.

    ``last_image_deployed_at`` reads the Deployment's Progressing
    condition with reason ``NewReplicaSetAvailable`` — the canonical
    "rollout completed" signal. Empty string when the condition is
    absent.
    """
    import datetime as _dt

    if now_iso is None:
        now_dt = _dt.datetime.now(_dt.UTC)
    else:
        try:
            now_dt = _dt.datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        except ValueError:
            now_dt = _dt.datetime.now(_dt.UTC)
    window_start = now_dt - _dt.timedelta(hours=24)

    out: list[WorkloadHealth] = []
    for ns in namespaces:
        try:
            deployments = k8s_client.list_namespaced_deployment(namespace=ns)
        except Exception:
            continue
        for dep in getattr(deployments, "items", []) or []:
            meta = getattr(dep, "metadata", None)
            name = getattr(meta, "name", "") or ""
            if not name:
                continue
            spec = getattr(dep, "spec", None)
            status = getattr(dep, "status", None)
            desired = int(getattr(spec, "replicas", 0) or 0) if spec else 0
            ready = int(getattr(status, "ready_replicas", 0) or 0) if status else 0

            selector_str = _selector_to_label_selector(
                getattr(spec, "selector", None) if spec else None,
            )
            restart_count_24h = _restart_count_for_deployment(
                k8s_client,
                namespace=ns,
                label_selector=selector_str,
                window_start=window_start,
            )
            last_deployed = _last_image_deployed_at(status)

            out.append(
                WorkloadHealth(
                    namespace=ns,
                    name=name,
                    desired_replicas=desired,
                    ready_replicas=ready,
                    restart_count_24h=restart_count_24h,
                    last_image_deployed_at=last_deployed,
                ),
            )
    out.sort(key=lambda w: (w.namespace, w.name))
    return out


def _selector_to_label_selector(selector: Any) -> str:
    """Render a Deployment's ``spec.selector.match_labels`` map into the
    ``key=value,key=value`` form the kube API's ``labelSelector`` arg
    expects. Returns the empty string when the selector is missing —
    the caller falls back to listing all pods in the namespace, which
    is acceptable for the platform's narrow operator-facing
    namespaces."""
    if selector is None:
        return ""
    match_labels = getattr(selector, "match_labels", None) or {}
    if not match_labels:
        return ""
    return ",".join(f"{k}={v}" for k, v in sorted(match_labels.items()))


def _restart_count_for_deployment(
    k8s_client: Any,
    *,
    namespace: str,
    label_selector: str,
    window_start: Any,
) -> int:
    """Sum container restart counts for pods matched by ``label_selector``
    in ``namespace``, scoped to restarts whose last termination fell
    inside the trailing 24h window. Clients without termination
    timestamps contribute their full restart_count (best-effort)."""
    import datetime as _dt

    try:
        pods = k8s_client.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector,
        )
    except TypeError:
        # Older client signatures may not accept label_selector kwarg.
        try:
            pods = k8s_client.list_namespaced_pod(namespace=namespace)
        except Exception:
            return 0
    except Exception:
        return 0

    total = 0
    for pod in getattr(pods, "items", []) or []:
        status = getattr(pod, "status", None)
        if status is None:
            continue
        container_statuses = getattr(status, "container_statuses", None) or []
        for cs in container_statuses:
            restart_count = int(getattr(cs, "restart_count", 0) or 0)
            if restart_count == 0:
                continue
            last_state = getattr(cs, "last_state", None)
            terminated = getattr(last_state, "terminated", None) if last_state is not None else None
            finished_at = getattr(terminated, "finished_at", None) if terminated is not None else None
            if finished_at is None:
                # No termination timestamp — best-effort: contribute
                # the running counter rather than dropping the signal.
                total += restart_count
                continue
            try:
                if isinstance(finished_at, _dt.datetime):
                    finished_dt = finished_at
                else:
                    finished_dt = _dt.datetime.fromisoformat(
                        str(finished_at).replace("Z", "+00:00"),
                    )
            except ValueError:
                total += restart_count
                continue
            if finished_dt.tzinfo is None:
                finished_dt = finished_dt.replace(tzinfo=_dt.UTC)
            if finished_dt >= window_start:
                total += restart_count
    return total


def _last_image_deployed_at(status: Any) -> str:
    """Pull the ``NewReplicaSetAvailable`` Progressing condition's
    last-transition timestamp off a Deployment ``status`` block. Empty
    string when missing — covers freshly-created Deployments and
    older API versions where the condition isn't populated."""
    if status is None:
        return ""
    conditions = getattr(status, "conditions", None) or []
    for cond in conditions:
        cond_type = getattr(cond, "type", "") or ""
        reason = getattr(cond, "reason", "") or ""
        if cond_type == "Progressing" and reason == "NewReplicaSetAvailable":
            ts = getattr(cond, "last_transition_time", None)
            if ts is None:
                return ""
            return str(ts)
    return ""


PLATFORM_NAMESPACE = "astrolift-system"
"""The platform's own namespace. When the caller passes
``namespaces=None`` to a driver health method, this is the default
single-element set the driver scans."""


def default_namespaces(namespaces: list[str] | None) -> list[str]:
    """Resolve the standard fallback for health-method ``namespaces``."""
    return list(namespaces) if namespaces else [PLATFORM_NAMESPACE]
