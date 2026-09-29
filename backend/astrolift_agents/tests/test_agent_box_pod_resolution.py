"""Finding an agent box's pod, and its cluster (#129).

Reaching a box takes two lookups the relay itself does not do. The CLI
turns a box slug into a pod name before it dials, and the exec backend
turns the slug into a cluster + namespace. Both resolved a
``RegisteredApp`` and nothing else, so a box stayed invisible to pod
resolution and unroutable to a cluster even once the handshake let it
through.

The pod half is its own field, ``agentBoxPods``, gated on
``agent_box.attach`` rather than the app surface's ``app.read_logs``.
That distinction is the point of half this file: ``app_deployer`` is the
one role that may start a box without holding the app-pod grant, so a
box resolver behind the app gate leaves exactly the role the ticket is
about able to start something it can never reach.

The cluster driver is the in-memory test backend; the database is real,
which is what makes the cross-tenant assertions worth anything.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from _sdk.cluster import PodInfo
from django.contrib.auth import get_user_model

from astrolift_agents.models import AgentBox
from astrolift_identity import device_flow
from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token, verify_token
from astrolift_identity.models import Member, Organization
from astrolift_lifecycle.schema.queries import LifecycleQuery
from core.cluster_observability import (
    reset_pod_backend_for_tests,
    set_pod_backend_for_tests,
)
from core.decorators import TenantRequired
from core.permissions import Permission, PermissionDenied
from core.schema.exec_ws import _check_box_attach_permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import bind_role

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


@pytest.fixture
def info():
    """Minimal ``info``-shaped object; the gate reads the tenant
    contextvar, not the request, so nothing else is needed."""
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None)))


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


def _as(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _list(info, slug: str):
    return LifecycleQuery().agent_box_pods(info, slug=slug)


# ---- the gate --------------------------------------------------------


def test_attach_grant_alone_resolves_a_box_pod(pod_backend, agent_cluster, permission_resolver, info) -> None:
    """The whole reason this field exists. ``app_deployer`` holds
    ``agent.dispatch`` and ``agent_box.attach`` but not
    ``app.read_logs``; behind the app gate it could start a box and
    never resolve a pod for it, which is the ticket's complaint one
    layer down."""
    permission_resolver.grant(Permission.AGENT_BOX_ATTACH)
    permission_resolver.deny(Permission.APP_READ_LOGS)
    org = _org("acme")
    box = _box(org)

    with _as(org):
        pods = _list(info, box.slug)

    assert [p.name for p in pods] == ["agent-box-abc123-x9k2p"]


def test_read_logs_alone_does_not_open_the_box_surface(
    pod_backend, agent_cluster, permission_resolver, info
) -> None:
    """The converse: the app-pod grant is not the box grant, so
    widening one never silently widens the other."""
    permission_resolver.grant(Permission.APP_READ_LOGS)
    org = _org("acme")
    box = _box(org)

    with _as(org), pytest.raises(PermissionDenied):
        _list(info, box.slug)
    assert pod_backend.calls == []


def test_no_grants_denies_before_any_cluster_work(
    pod_backend, agent_cluster, permission_resolver, info
) -> None:
    org = _org("acme")
    box = _box(org)

    with _as(org), pytest.raises(PermissionDenied):
        _list(info, box.slug)
    assert pod_backend.calls == []


@pytest.mark.parametrize("client_kind,refresh", [("cli", False), ("cli", True), ("ide", False)])
@pytest.mark.parametrize("access", ["allowed", "no_grant", "foreign_org"])
def test_device_credentials_preserve_box_rbac_and_tenant_boundaries(
    pod_backend, agent_cluster, info, client_kind, refresh, access
):
    """IDE Connect uses CLI login; generic IDE enrollment remains read-only.

    Exercise real device issuance/refresh, token validation and role bindings.
    Only the cluster transport is replaced by the recording pod backend.
    """
    org = _org("box-token-2188")
    user = get_user_model().objects.create_user(username="box-token-2188")
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.pk,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    if access != "no_grant":
        bind_role(
            user,
            permissions=[Permission.AGENT_BOX_ATTACH],
            kind="ORG",
            scope_id=org.pk,
            slug="box-attach-2188",
        )
    box_org = _org("foreign-box-2188") if access == "foreign_org" else org
    box = _box(box_org)
    session, session_id = device_flow.create_session(client_kind=client_kind)
    assert device_flow.approve_session(session, user=user, organization=org) is None
    issued = device_flow.poll_complete(session_id)
    assert issued.status == "issued"
    assert issued.credentials is not None
    if refresh:
        issued = device_flow.refresh_credentials(issued.credentials.refresh_token)
        assert issued.status == "issued"
        assert issued.credentials is not None
    token = verify_token(issued.credentials.access_token)
    assert token is not None
    bound = set_current_api_token(token)
    try:
        with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
            if client_kind != "cli" or access == "no_grant":
                with pytest.raises(PermissionDenied):
                    _list(info, box.slug)
                assert pod_backend.calls == []
            elif access == "foreign_org":
                assert _list(info, box.slug) == []
                assert pod_backend.calls == []
            else:
                assert [p.name for p in _list(info, box.slug)] == ["agent-box-abc123-x9k2p"]

            # The relay's permission gate uses the same token ceiling and
            # real account grants. Its target check rejects foreign boxes.
            assert _check_box_attach_permission.func(tenant_org_id=box_org.pk, actor_user_id=user.pk) == (
                client_kind == "cli" and access == "allowed"
            )
    finally:
        reset_current_api_token(bound)


def test_denies_without_a_bound_tenant(pod_backend, agent_cluster, permission_resolver, info) -> None:
    permission_resolver.grant(Permission.AGENT_BOX_ATTACH)
    _box(_org("acme"))

    with pytest.raises(TenantRequired):
        _list(info, "box-claude-dev-u7")


def test_app_pod_surface_no_longer_answers_a_box_slug(
    pod_backend, agent_cluster, permission_resolver, info
) -> None:
    """``astroliftAppPods`` is the app gate's surface and stays that
    way. A box slug reaching it finds no ``RegisteredApp`` and returns
    empty, so the box's pods are only ever behind the box grant."""
    permission_resolver.grant(Permission.APP_READ_LOGS)
    org = _org("acme")
    box = _box(org)

    with _as(org):
        assert LifecycleQuery().astrolift_app_pods(info, app_slug=box.slug) == []
    assert pod_backend.calls == []


# ---- pod resolution --------------------------------------------------


def test_box_pods_are_selected_by_guid_in_the_frozen_namespace(
    pod_backend, agent_cluster, permission_resolver, info
) -> None:
    """The box render labels the pod ``astrolift.dev/app=<guid>`` so the
    platform's existing pod surfaces find it with no box-specific
    selector — so the guid, not the slug, is what reaches the driver.
    The namespace comes off the row rather than being recomputed,
    because a recomputed guess can miss the pod."""
    permission_resolver.grant(Permission.AGENT_BOX_ATTACH)
    org = _org("acme")
    box = _box(org, namespace="agents-acme-frozen")

    with _as(org):
        _list(info, box.slug)

    assert pod_backend.calls == [{"namespace": "agents-acme-frozen", "app_slug": str(box.guid)}]


def test_another_orgs_box_lists_nothing(pod_backend, agent_cluster, permission_resolver, info) -> None:
    """The mandatory cross-tenant case: a box slug that exists, in an org
    the caller is not in, must not reach the driver at all. The grant is
    held — it is the org filter doing the work, which is the point,
    since ``@tenant_scoped`` asserts a tenant and does not filter."""
    permission_resolver.grant(Permission.AGENT_BOX_ATTACH)
    caller = _org("caller-org")
    other = _org("other-org")
    box = _box(other)

    with _as(caller):
        assert _list(info, box.slug) == []
    assert pod_backend.calls == []
    # Control: the same slug under its own org does resolve, so the empty
    # list above is the tenant boundary and not a broken fixture.
    with _as(other):
        assert _list(info, box.slug) != []


def test_retired_box_lists_nothing(pod_backend, agent_cluster, permission_resolver, info) -> None:
    permission_resolver.grant(Permission.AGENT_BOX_ATTACH)
    org = _org("acme")
    box = _box(org)
    box.soft_delete()

    with _as(org):
        assert _list(info, box.slug) == []
    assert pod_backend.calls == []


def test_unknown_slug_lists_nothing(pod_backend, agent_cluster, permission_resolver, info) -> None:
    permission_resolver.grant(Permission.AGENT_BOX_ATTACH)
    org = _org("acme")

    with _as(org):
        assert _list(info, "neither-an-app-nor-a-box") == []


def test_box_with_no_agent_cluster_lists_nothing(pod_backend, permission_resolver, info, monkeypatch) -> None:
    """An org with nothing to dispatch onto degrades to an empty list
    rather than 500ing the client, matching the unwired-app path."""
    import astrolift_agents.services.agent_cluster as agent_cluster_mod
    from astrolift_agents.services.agent_cluster import NoAgentClusterError

    def _raise(_org):
        raise NoAgentClusterError("no managed cluster")

    monkeypatch.setattr(agent_cluster_mod, "resolve_agent_cluster", _raise)
    permission_resolver.grant(Permission.AGENT_BOX_ATTACH)
    org = _org("acme")
    box = _box(org)

    with _as(org):
        assert _list(info, box.slug) == []


def test_unreachable_cluster_lists_nothing(agent_cluster, permission_resolver, info) -> None:
    """A driver failure is not an error the client can act on, so it
    reads as 'no pods' — the same contract the app surface has."""

    class _Exploding:
        def list_pods(self, *, auth, namespace, app_slug):  # noqa: ARG002
            raise RuntimeError("connection refused")

    set_pod_backend_for_tests(_Exploding())
    try:
        permission_resolver.grant(Permission.AGENT_BOX_ATTACH)
        org = _org("acme")
        box = _box(org)
        with _as(org):
            assert _list(info, box.slug) == []
    finally:
        reset_pod_backend_for_tests()


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
