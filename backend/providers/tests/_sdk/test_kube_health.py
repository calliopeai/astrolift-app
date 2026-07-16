"""Unit tests for the shared kube-health helpers (#68 slice 1).

Pure functions over a kubernetes-style client API surface — every
driver (k8s_native / EKS / GKE / AKS) routes through these so the
helpers are the right place to exercise the aggregation + sort
behavior.
"""

from __future__ import annotations

from dataclasses import dataclass

from _sdk._kube_health import (
    PLATFORM_NAMESPACE,
    default_namespaces,
    events_from_client,
    pod_phase_summary_from_client,
)

# ---- fakes -----------------------------------------------------------


@dataclass
class _PodStatus:
    phase: str | None


@dataclass
class _Pod:
    status: _PodStatus | None


@dataclass
class _PodList:
    items: list


@dataclass
class _EventMeta:
    name: str


@dataclass
class _Involved:
    kind: str
    name: str


@dataclass
class _Event:
    metadata: _EventMeta
    reason: str
    message: str
    type: str
    count: int
    first_timestamp: str
    last_timestamp: str
    involved_object: _Involved


@dataclass
class _EventList:
    items: list


class FakeClient:
    def __init__(self, pods=None, events=None, fail_namespaces=None):
        self._pods = pods or {}
        self._events = events or {}
        self._fail = set(fail_namespaces or [])

    def list_namespaced_pod(self, *, namespace):
        if namespace in self._fail:
            raise RuntimeError(f"namespace {namespace} not found")
        return _PodList(items=self._pods.get(namespace, []))

    def list_namespaced_event(self, *, namespace):
        if namespace in self._fail:
            raise RuntimeError(f"namespace {namespace} not found")
        return _EventList(items=self._events.get(namespace, []))


# ---- default_namespaces ----------------------------------------------


def test_default_namespaces_returns_platform_when_none():
    assert default_namespaces(None) == [PLATFORM_NAMESPACE]
    assert default_namespaces([]) == [PLATFORM_NAMESPACE]


def test_default_namespaces_passthrough():
    assert default_namespaces(["a", "b"]) == ["a", "b"]


# ---- pod_phase_summary_from_client -----------------------------------


def test_pod_phase_summary_aggregates_by_namespace_and_phase():
    client = FakeClient(
        pods={
            "ns-a": [
                _Pod(status=_PodStatus(phase="Running")),
                _Pod(status=_PodStatus(phase="Running")),
                _Pod(status=_PodStatus(phase="Pending")),
            ],
            "ns-b": [
                _Pod(status=_PodStatus(phase="Failed")),
            ],
        }
    )
    out = pod_phase_summary_from_client(
        client,
        namespaces=["ns-a", "ns-b"],
    )
    counts = {(r.namespace, r.phase): r.count for r in out}
    assert counts[("ns-a", "Running")] == 2
    assert counts[("ns-a", "Pending")] == 1
    assert counts[("ns-b", "Failed")] == 1


def test_pod_phase_summary_handles_missing_status():
    client = FakeClient(
        pods={
            "ns": [
                _Pod(status=None),
                _Pod(status=_PodStatus(phase=None)),
            ],
        }
    )
    out = pod_phase_summary_from_client(client, namespaces=["ns"])
    assert len(out) == 1
    assert out[0].phase == "Unknown"
    assert out[0].count == 2


def test_pod_phase_summary_skips_unreachable_namespace():
    """A 404 / RBAC failure on one namespace must not strand the
    whole rollup; the reachable namespaces still surface."""
    client = FakeClient(
        pods={"ns-ok": [_Pod(status=_PodStatus(phase="Running"))]},
        fail_namespaces={"ns-bad"},
    )
    out = pod_phase_summary_from_client(
        client,
        namespaces=["ns-bad", "ns-ok"],
    )
    assert len(out) == 1
    assert out[0].namespace == "ns-ok"


def test_pod_phase_summary_empty_namespaces_returns_empty():
    out = pod_phase_summary_from_client(FakeClient(), namespaces=[])
    assert out == []


def test_pod_phase_summary_no_pods_in_namespace():
    client = FakeClient(pods={"ns": []})
    assert pod_phase_summary_from_client(client, namespaces=["ns"]) == []


# ---- events_from_client ----------------------------------------------


def _ev(name, reason, message, t="Warning", count=1, last="2026-05-15T10:00:00"):
    return _Event(
        metadata=_EventMeta(name=name),
        reason=reason,
        message=message,
        type=t,
        count=count,
        first_timestamp="2026-05-15T09:00:00",
        last_timestamp=last,
        involved_object=_Involved(kind="Pod", name=f"{name}-pod"),
    )


def test_events_filters_to_warning_by_default():
    client = FakeClient(
        events={
            "ns": [
                _ev("e1", "OOMKilled", "out of memory", t="Warning"),
                _ev("e2", "Pulled", "image pulled", t="Normal"),
            ],
        }
    )
    out = events_from_client(client, namespaces=["ns"])
    assert len(out) == 1
    assert out[0].reason == "OOMKilled"


def test_events_event_type_none_returns_all():
    client = FakeClient(
        events={
            "ns": [
                _ev("e1", "X", "x", t="Warning"),
                _ev("e2", "Y", "y", t="Normal"),
            ],
        }
    )
    out = events_from_client(client, namespaces=["ns"], event_type=None)
    assert len(out) == 2


def test_events_sorts_by_last_seen_desc():
    client = FakeClient(
        events={
            "ns": [
                _ev("old", "X", "x", last="2026-05-15T08:00:00"),
                _ev("new", "Y", "y", last="2026-05-15T12:00:00"),
                _ev("mid", "Z", "z", last="2026-05-15T10:00:00"),
            ],
        }
    )
    out = events_from_client(client, namespaces=["ns"])
    assert [e.name for e in out] == ["new", "mid", "old"]


def test_events_respects_limit():
    client = FakeClient(
        events={
            "ns": [_ev(f"e{i}", "X", "x", last=f"2026-05-15T10:0{i}:00") for i in range(5)],
        }
    )
    out = events_from_client(client, namespaces=["ns"], limit=2)
    assert len(out) == 2


def test_events_captures_involved_object():
    client = FakeClient(events={"ns": [_ev("e", "OOM", "msg")]})
    out = events_from_client(client, namespaces=["ns"])
    assert out[0].involved_object == "Pod/e-pod"


def test_events_skips_unreachable_namespace():
    client = FakeClient(
        events={"ns-ok": [_ev("e", "X", "x")]},
        fail_namespaces={"ns-bad"},
    )
    out = events_from_client(
        client,
        namespaces=["ns-bad", "ns-ok"],
    )
    assert len(out) == 1
    assert out[0].namespace == "ns-ok"


def test_events_handles_missing_metadata():
    """Older k8s versions or partial events may omit metadata.name."""

    class WeirdEvent:
        metadata = None
        reason = "X"
        message = "x"
        type = "Warning"
        count = 1
        first_timestamp = "a"
        last_timestamp = "b"
        involved_object = _Involved(kind="Pod", name="p")

    client = FakeClient(events={"ns": [WeirdEvent()]})
    out = events_from_client(client, namespaces=["ns"])
    assert len(out) == 1
    assert out[0].name == ""
