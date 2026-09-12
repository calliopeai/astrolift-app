"""A stalled StatefulSet rollout says so instead of "rollout timed out" (#1724).

``RollingUpdate`` will not advance past a pod that never becomes Ready, so a
workload that crashes on startup keeps its old pod on the previous revision
indefinitely while the StatefulSet already carries the corrected template. Both
the #1721 and #1722 fixes had to be released by deleting the pod by hand,
because five deploys in a row reported a bare timeout while the fix sat applied
and inert one revision away.
"""

from __future__ import annotations

from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig


class _FakeClient:
    def __init__(self, status):
        self._status = status

    def get(self, *, kind, namespace, name):
        return {"status": self._status}


def _driver(status):
    driver = K8sNativeClusterDriver(config=K8sNativeConfig())
    driver._k8s_cache["c"] = _FakeClient(status)
    return driver


def test_diverged_revisions_are_reported_as_stalled():
    stalled, message = _driver(
        {"currentRevision": "web-old", "updateRevision": "web-new", "readyReplicas": 0}
    )._diagnose_stall("c", "ns", "statefulset", "web")
    assert stalled is True
    assert "web-old" in message and "web-new" in message
    assert "will not resolve on its own" in message


def test_matching_revisions_are_not_a_stall():
    """A slow rollout is not a stuck one; only divergence means the change
    cannot land."""
    stalled, message = _driver(
        {"currentRevision": "web-1", "updateRevision": "web-1", "readyReplicas": 0}
    )._diagnose_stall("c", "ns", "statefulset", "web")
    assert stalled is False
    assert message == ""


def test_non_statefulset_kinds_are_skipped():
    """Deployments replace pods without waiting for the old one to be Ready, so
    they cannot reach this state."""
    stalled, _ = _driver({"currentRevision": "a", "updateRevision": "b"})._diagnose_stall(
        "c", "ns", "deployment", "web"
    )
    assert stalled is False


def test_diagnosis_never_raises():
    """Best-effort: a diagnosis is not worth failing a deploy path over."""

    class _Boom:
        def get(self, **kw):
            raise RuntimeError("cluster unreachable")

    driver = K8sNativeClusterDriver(config=K8sNativeConfig())
    driver._k8s_cache["c"] = _Boom()
    assert driver._diagnose_stall("c", "ns", "statefulset", "web") == (False, "")


def test_missing_revision_fields_are_not_a_stall():
    stalled, _ = _driver({})._diagnose_stall("c", "ns", "statefulset", "web")
    assert stalled is False
