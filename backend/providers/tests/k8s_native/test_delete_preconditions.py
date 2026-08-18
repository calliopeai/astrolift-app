"""Conditional deletes close the read/delete race (#1389).

Managed-service drivers validate ownership with a GET and delete later. In
between, a reconciler can recreate the resource under the same name, and a
name-only delete then removes the replacement: the object that was inspected
and the object that is deleted are different objects.

The apiserver will refuse that if the delete carries the UID it was told to
expect. These tests pin that the UID is actually sent, that a refusal is
reported as a conflict rather than an error, and above all that nothing retries
it without the precondition.
"""

from __future__ import annotations

import pytest

from _sdk.cluster import DeleteResult
from _sdk.k8s_dynamic_client import PreconditionFailedError


class _Conflict(Exception):
    """Stands in for kubernetes.dynamic.exceptions.ConflictError."""


class _NotFound(Exception):
    """Stands in for kubernetes.dynamic.exceptions.NotFoundError."""


class FakeResource:
    """Records what a delete was asked to do, and can refuse it."""

    namespaced = True

    def __init__(self, *, refuse_uid: str | None = None, missing: bool = False) -> None:
        self.calls: list[dict] = []
        self._refuse_uid = refuse_uid
        self._missing = missing

    def delete(self, **kwargs):
        self.calls.append(kwargs)
        if self._missing:
            raise _NotFound("gone")
        preconditions = (kwargs.get("body") or {}).get("preconditions") or {}
        if self._refuse_uid and preconditions.get("uid") != self._refuse_uid:
            # The apiserver refuses when the live object's UID differs from
            # the one the caller asserted.
            raise _Conflict("uid mismatch")
        return None


# ---- the DeleteResult contract ----------------------------------------------


def test_a_conflict_is_not_ok_even_though_errors_is_empty():
    """A caller that only checks `ok` must still fail closed."""
    result = DeleteResult(deleted=[], not_found=[], errors=[], conflicts=["Secret/s: changed"])

    assert not result.ok


def test_conflicts_appear_in_the_summary():
    result = DeleteResult(deleted=[], not_found=[], errors=[], conflicts=["Secret/s: changed"])

    assert result.summary() == ["Secret/s: changed"]


def test_a_clean_delete_is_still_ok():
    assert DeleteResult(deleted=["Secret/s"], not_found=[], errors=[]).ok


def test_conflicts_defaults_to_empty_for_existing_callers():
    """Every existing construction site omits it."""
    assert DeleteResult(deleted=[], not_found=[], errors=[]).conflicts == []


# ---- the dynamic client ------------------------------------------------------


def _client(monkeypatch, resource):
    """A KubernetesDynamicClient with its apiserver plumbing stubbed out."""
    from _sdk import k8s_dynamic_client as mod

    client = object.__new__(mod.KubernetesDynamicClient)
    monkeypatch.setattr(client, "_refresh_token", lambda: None, raising=False)
    monkeypatch.setattr(client, "_resource_for", lambda *a, **k: resource, raising=False)

    # The real code imports these from the kubernetes package at call time.
    fake_exceptions = type("exc", (), {"NotFoundError": _NotFound, "ConflictError": _Conflict})
    import sys

    monkeypatch.setitem(sys.modules, "kubernetes.dynamic.exceptions", fake_exceptions)
    return client


def test_the_uid_is_sent_as_a_precondition(monkeypatch):
    resource = FakeResource()
    client = _client(monkeypatch, resource)

    client.delete(kind="Secret", namespace="ns", name="s", uid="uid-1")

    assert resource.calls[0]["body"]["preconditions"] == {"uid": "uid-1"}


def test_resource_version_narrows_it_further(monkeypatch):
    resource = FakeResource()
    client = _client(monkeypatch, resource)

    client.delete(kind="Secret", namespace="ns", name="s", uid="uid-1", resource_version="42")

    assert resource.calls[0]["body"]["preconditions"] == {
        "uid": "uid-1",
        "resourceVersion": "42",
    }


def test_no_preconditions_means_no_body_change(monkeypatch):
    """Callers that never did an ownership read keep the old behaviour."""
    resource = FakeResource()
    client = _client(monkeypatch, resource)

    client.delete(kind="Secret", namespace="ns", name="s")

    assert "body" not in resource.calls[0]


def test_propagation_policy_and_preconditions_coexist(monkeypatch):
    """Both live in DeleteOptions; adding one must not drop the other."""
    resource = FakeResource()
    client = _client(monkeypatch, resource)

    client.delete(kind="Secret", namespace="ns", name="s", propagation_policy="Foreground", uid="uid-1")

    body = resource.calls[0]["body"]
    assert body["propagationPolicy"] == "Foreground"
    assert body["preconditions"] == {"uid": "uid-1"}


def test_a_replacement_between_read_and_delete_is_refused(monkeypatch):
    """The race itself. Ownership was validated on uid-1; uid-2 holds the name
    now, and deleting it would destroy someone else's object."""
    resource = FakeResource(refuse_uid="uid-2")
    client = _client(monkeypatch, resource)

    with pytest.raises(PreconditionFailedError, match="changed between"):
        client.delete(kind="Secret", namespace="ns", name="s", uid="uid-1")


def test_a_conflict_without_preconditions_is_not_reinterpreted(monkeypatch):
    """A 409 on an unconditional delete means something else entirely, and
    calling it a precondition failure would mislead."""
    resource = FakeResource(refuse_uid="never-matches")
    client = _client(monkeypatch, resource)

    with pytest.raises(_Conflict):
        client.delete(kind="Secret", namespace="ns", name="s")


def test_a_missing_resource_is_still_swallowed(monkeypatch):
    """Idempotent teardown loops depend on this."""
    client = _client(monkeypatch, FakeResource(missing=True))

    assert client.delete(kind="Secret", namespace="ns", name="s", uid="uid-1") is False


# ---- the cluster driver ------------------------------------------------------


class RecordingK8s:
    """Stands in for the dynamic client at the cluster-driver seam."""

    def __init__(self, *, refuse_uid: str | None = None) -> None:
        self.calls: list[dict] = []
        self._refuse_uid = refuse_uid

    def delete(self, **kwargs):
        self.calls.append(kwargs)
        if self._refuse_uid and kwargs.get("uid") != self._refuse_uid:
            raise PreconditionFailedError("uid mismatch")
        return True


def _driver(k8s):
    from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig

    driver = K8sNativeClusterDriver(config=K8sNativeConfig())
    driver._k8s = lambda _cluster: k8s
    return driver


def _manifest(name="s", uid=None, resource_version=None):
    meta = {"name": name}
    if uid:
        meta["uid"] = uid
    if resource_version:
        meta["resourceVersion"] = resource_version
    return {"apiVersion": "v1", "kind": "Secret", "metadata": meta}


def test_the_driver_forwards_uid_from_the_manifest():
    """The ownership read writes uid into the manifest metadata it hands back."""
    k8s = RecordingK8s()

    _driver(k8s).delete_manifests("c1", "ns", [_manifest(uid="uid-1", resource_version="42")])

    assert k8s.calls[0]["uid"] == "uid-1"
    assert k8s.calls[0]["resource_version"] == "42"


def test_a_manifest_without_metadata_stays_a_name_only_delete():
    """Callers that never did an ownership read must be unaffected."""
    k8s = RecordingK8s()

    _driver(k8s).delete_manifests("c1", "ns", [_manifest()])

    assert k8s.calls[0]["uid"] is None
    assert k8s.calls[0]["resource_version"] is None


def test_a_refused_delete_is_a_conflict_not_an_error():
    """The two call for different responses: an error may be retried, a
    conflict must not be."""
    k8s = RecordingK8s(refuse_uid="uid-2")

    result = _driver(k8s).delete_manifests("c1", "ns", [_manifest(uid="uid-1")])

    assert result.conflicts and not result.errors
    assert not result.ok
    assert result.deleted == []


def test_a_refused_delete_is_never_retried_unconditionally():
    """The whole point. Falling back to a name-only delete would remove the
    replacement, which is the object the precondition was protecting."""
    k8s = RecordingK8s(refuse_uid="uid-2")

    _driver(k8s).delete_manifests("c1", "ns", [_manifest(uid="uid-1")])

    assert len(k8s.calls) == 1
    assert all(call["uid"] == "uid-1" for call in k8s.calls)


def test_one_conflict_does_not_stop_the_other_deletes():
    """Teardown should remove everything it legitimately owns and report the
    one it could not, rather than stopping at the first conflict."""
    k8s = RecordingK8s(refuse_uid="uid-ok")

    result = _driver(k8s).delete_manifests(
        "c1",
        "ns",
        [_manifest(name="a", uid="uid-bad"), _manifest(name="b", uid="uid-ok")],
    )

    assert result.deleted == ["Secret/b"]
    assert len(result.conflicts) == 1
