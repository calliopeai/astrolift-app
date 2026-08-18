"""Finding an agent box's pod, and its cluster (#129).

Reaching a box takes two lookups the relay itself does not do. The CLI
turns ``--app <slug>`` into a pod name through the same pod surface the
app path uses, and the exec backend turns the slug into a cluster +
namespace. Both resolved a ``RegisteredApp`` and nothing else, so a box
stayed invisible to pod resolution and unroutable to a cluster even once
the handshake let it through.

The cluster driver is the in-memory test backend; the database is real,
which is what makes the cross-tenant assertions worth anything.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from _sdk.cluster import PodInfo

from astrolift_agents.models import AgentBox
from astrolift_identity.models import Organization
from astrolift_lifecycle.schema.queries import _list_pods_for_app
from core.cluster_observability import (
    reset_pod_backend_for_tests,
    set_pod_backend_for_tests,
)

pytestmark = pytest.mark.django_db


class _RecordingPodBackend:
    """Answers every query with one pod and records the selector it was
    asked for — the selector is the assertion in most of these."""

    def __init__(self, pods=None):
        self.calls: list[dict] = []
        self._pods = list(pods if pods is not None else [_pod()])

    def list_pods(self, *, auth, namespace, app_slug):  # noqa: ARG002
        self.calls.append({"namespace": namespace, "app_slug": app_slug})
        return list(self._pods)


def _pod(name="agent-box-abc123-x9k2p"):
    return PodInfo(
        name=name,
        workload="agent-box",
        status="Running",
        phase="Running",
        ready=True,
        restarts=0,
        age=datetime.now(UTC),
        node="ip-10-0-0-12",
        container_statuses=[],
    )


def _fake_cluster():
    return SimpleNamespace(
        slug="agents-cluster",
        auth_method="kubeconfig",
        auth_config={},
        endpoint="https://k8s.example.net",
        ca_cert="",
        default_namespace_prefix="",
        is_active=True,
    )


@pytest.fixture
def pod_backend():
    backend = _RecordingPodBackend()
    set_pod_backend_for_tests(backend)
    yield backend
    reset_pod_backend_for_tests()


@pytest.fixture
def agent_cluster(monkeypatch):
    """Point the box's cluster resolution at an in-memory cluster."""
    import astrolift_agents.services.agent_cluster as agent_cluster_mod

    cluster = _fake_cluster()
    monkeypatch.setattr(agent_cluster_mod, "resolve_agent_cluster", lambda _org: cluster)
    return cluster


def _org(slug: str):
    return Organization.objects.create(name=slug.title(), slug=slug)


def _box(org, *, slug="box-claude-dev-u7", namespace="agents-acme"):
    return AgentBox.objects.create(
        organization=org,
        name="Claude box",
        slug=slug,
        status=AgentBox.Status.RUNNING,
        namespace=namespace,
    )


# ---- pod resolution --------------------------------------------------


def test_box_slug_resolves_to_its_pod(pod_backend, agent_cluster) -> None:
    org = _org("acme")
    box = _box(org)

    pods = _list_pods_for_app(box.slug, org_id=org.id)

    assert [p.name for p in pods] == ["agent-box-abc123-x9k2p"]


def test_box_pods_are_selected_by_guid_in_the_frozen_namespace(pod_backend, agent_cluster) -> None:
    """The box render labels the pod ``astrolift.dev/app=<guid>`` so the
    platform's existing pod surfaces find it with no box-specific
    selector — so the guid, not the slug, is what reaches the driver.
    The namespace comes off the row rather than being recomputed,
    because a recomputed guess can miss the pod."""
    org = _org("acme")
    box = _box(org, namespace="agents-acme-frozen")

    _list_pods_for_app(box.slug, org_id=org.id)

    assert pod_backend.calls == [{"namespace": "agents-acme-frozen", "app_slug": str(box.guid)}]


def test_another_orgs_box_lists_nothing(pod_backend, agent_cluster) -> None:
    """The mandatory cross-tenant case: a box slug that exists, in an org
    the caller is not in, must not reach the driver at all."""
    caller = _org("caller-org")
    other = _org("other-org")
    box = _box(other)

    assert _list_pods_for_app(box.slug, org_id=caller.id) == []
    assert pod_backend.calls == []
    # Control: the same slug under its own org does resolve, so the empty
    # list above is the tenant boundary and not a broken fixture.
    assert _list_pods_for_app(box.slug, org_id=other.id) != []


def test_retired_box_lists_nothing(pod_backend, agent_cluster) -> None:
    org = _org("acme")
    box = _box(org)
    box.soft_delete()

    assert _list_pods_for_app(box.slug, org_id=org.id) == []
    assert pod_backend.calls == []


def test_unknown_slug_still_lists_nothing(pod_backend, agent_cluster) -> None:
    """No app and no box: the app path's existing empty-list contract is
    unchanged, so the UI keeps rendering 'no pods yet'."""
    org = _org("acme")

    assert _list_pods_for_app("neither-an-app-nor-a-box", org_id=org.id) == []


def test_box_with_no_agent_cluster_lists_nothing(pod_backend, monkeypatch) -> None:
    """An org with nothing to dispatch onto degrades to an empty list
    rather than 500ing the page, matching the unwired-app path."""
    import astrolift_agents.services.agent_cluster as agent_cluster_mod
    from astrolift_agents.services.agent_cluster import NoAgentClusterError

    def _raise(_org):
        raise NoAgentClusterError("no managed cluster")

    monkeypatch.setattr(agent_cluster_mod, "resolve_agent_cluster", _raise)

    org = _org("acme")
    box = _box(org)

    assert _list_pods_for_app(box.slug, org_id=org.id) == []


# ---- exec-backend cluster resolution ---------------------------------


def test_exec_backend_resolves_a_box_to_its_cluster_and_namespace(agent_cluster) -> None:
    from core.cluster_exec import _resolve_box_target

    org = _org("acme")
    box = _box(org, namespace="agents-acme-frozen")

    resolved = _resolve_box_target(box_slug=box.slug, org_id=org.id)

    assert resolved is not None
    assert resolved["cluster"] is agent_cluster
    assert resolved["namespace"] == "agents-acme-frozen"


def test_exec_backend_refuses_another_orgs_box(agent_cluster) -> None:
    """Without this the relay's tenancy check would be the only thing
    standing between one org and a shell in another org's agent."""
    from core.cluster_exec import _resolve_box_target

    caller = _org("caller-org")
    other = _org("other-org")
    box = _box(other)

    assert _resolve_box_target(box_slug=box.slug, org_id=caller.id) is None
    assert _resolve_box_target(box_slug=box.slug, org_id=other.id) is not None
