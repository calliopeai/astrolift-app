"""Tests for #666 — inline ImagePullBackOff / CrashLoopBackOff / OOMKilled
chip on AstroliftAppPod.

The resolver pulls Warning events from the cluster driver alongside the
existing pod list, and joins them by pod name. We install both backends
(pods + events) via the core.cluster_observability test override hooks
so the test exercises the full resolver dispatch without a real cluster.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from _sdk.cluster import ContainerStatusInfo, PodInfo

from astrolift_lifecycle.schema.queries import LifecycleQuery
from core.cluster_observability import (
    reset_events_backend_for_tests,
    reset_pod_backend_for_tests,
    set_events_backend_for_tests,
    set_pod_backend_for_tests,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _grant(resolver):
    resolver.grant(Permission.APP_READ_LOGS)
    resolver.grant(Permission.APP_READ)


def _bind_default_cluster(app, env):
    app.default_tenant_cluster = env.tenant_cluster
    app.save(update_fields=["default_tenant_cluster"])


class _FixedPodBackend:
    def __init__(self, pods):
        self._pods = list(pods)

    def list_pods(self, *, auth, namespace, app_slug):  # noqa: ARG002
        return list(self._pods)


class _FixedEventsBackend:
    def __init__(self, events):
        self._events = list(events)

    def list_events(self, ctx, *, namespaces, event_type, limit):  # noqa: ARG002
        return list(self._events)


def _make_pod(name="hello-app-web-abc-1"):
    return PodInfo(
        name=name,
        workload="web",
        status="CrashLoopBackOff",
        phase="Running",
        ready=False,
        restarts=3,
        age=datetime.now(UTC),
        node="ip-10-0-0-12",
        container_statuses=[
            ContainerStatusInfo(
                name="web",
                ready=False,
                restart_count=3,
                image="ghcr.io/acme/api:abc",
                state="waiting",
                waiting_reason="CrashLoopBackOff",
                terminated_reason="",
            ),
        ],
    )


def _make_event(
    *, pod_name, reason="CrashLoopBackOff", message="back-off restarting", last_seen="2026-05-17T12:00:00Z"
):
    return SimpleNamespace(
        namespace="acme-hello-app",
        name=f"{pod_name}.evt",
        reason=reason,
        message=message,
        type="Warning",
        count=5,
        first_seen=last_seen,
        last_seen=last_seen,
        involved_object=f"Pod/{pod_name}",
    )


@pytest.fixture(autouse=True)
def _reset_backends():
    yield
    reset_pod_backend_for_tests()
    reset_events_backend_for_tests()


@pytest.fixture
def fake_info(actor):
    return SimpleNamespace(
        context=SimpleNamespace(
            user=actor,
            request=SimpleNamespace(user=actor),
        ),
    )


# ---- happy path ---------------------------------------------------


def test_pod_resolver_surfaces_matching_warning_event(org, app, env, actor, fake_info, permission_resolver):
    _grant(permission_resolver)
    _bind_default_cluster(app, env)
    pod = _make_pod()
    event = _make_event(pod_name=pod.name)
    set_pod_backend_for_tests(_FixedPodBackend([pod]))
    set_events_backend_for_tests(_FixedEventsBackend([event]))

    with _tenant_for(org, actor):
        result = LifecycleQuery().astrolift_app_pods(fake_info, app_slug=app.slug)
    assert len(result) == 1
    assert result[0].recent_error_event is not None
    assert result[0].recent_error_event.reason == "CrashLoopBackOff"
    assert result[0].recent_error_event.count == 5


def test_pod_resolver_picks_newest_event_when_multiple(org, app, env, actor, fake_info, permission_resolver):
    """When two Warning events target the same pod, the resolver
    keeps the one with the highest last_seen."""
    _grant(permission_resolver)
    _bind_default_cluster(app, env)
    pod = _make_pod()
    old = _make_event(pod_name=pod.name, reason="ImagePullBackOff", last_seen="2026-05-10T00:00:00Z")
    new = _make_event(pod_name=pod.name, reason="CrashLoopBackOff", last_seen="2026-05-17T12:00:00Z")
    set_pod_backend_for_tests(_FixedPodBackend([pod]))
    set_events_backend_for_tests(_FixedEventsBackend([old, new]))

    with _tenant_for(org, actor):
        result = LifecycleQuery().astrolift_app_pods(fake_info, app_slug=app.slug)
    assert result[0].recent_error_event.reason == "CrashLoopBackOff"


# ---- no match -----------------------------------------------------


def test_pod_resolver_no_event_renders_null(org, app, env, actor, fake_info, permission_resolver):
    _grant(permission_resolver)
    _bind_default_cluster(app, env)
    pod = _make_pod()
    set_pod_backend_for_tests(_FixedPodBackend([pod]))
    set_events_backend_for_tests(_FixedEventsBackend([]))

    with _tenant_for(org, actor):
        result = LifecycleQuery().astrolift_app_pods(fake_info, app_slug=app.slug)
    assert result[0].recent_error_event is None


def test_pod_resolver_event_for_different_pod_ignored(org, app, env, actor, fake_info, permission_resolver):
    """Events targeting a pod NOT in our list should not leak onto
    the rows we did return."""
    _grant(permission_resolver)
    _bind_default_cluster(app, env)
    pod = _make_pod(name="hello-app-web-abc-1")
    event_for_other_pod = _make_event(pod_name="some-other-pod")
    set_pod_backend_for_tests(_FixedPodBackend([pod]))
    set_events_backend_for_tests(_FixedEventsBackend([event_for_other_pod]))

    with _tenant_for(org, actor):
        result = LifecycleQuery().astrolift_app_pods(fake_info, app_slug=app.slug)
    assert result[0].recent_error_event is None


# ---- degradation --------------------------------------------------


def test_pod_resolver_event_backend_failure_swallows(org, app, env, actor, fake_info, permission_resolver):
    """Events-backend raising still returns the pod list — chip just
    renders empty."""
    _grant(permission_resolver)
    _bind_default_cluster(app, env)
    pod = _make_pod()

    class _Boom:
        def list_events(self, ctx, *, namespaces, event_type, limit):  # noqa: ARG002
            raise RuntimeError("apiserver down")

    set_pod_backend_for_tests(_FixedPodBackend([pod]))
    set_events_backend_for_tests(_Boom())

    with _tenant_for(org, actor):
        result = LifecycleQuery().astrolift_app_pods(fake_info, app_slug=app.slug)
    assert len(result) == 1
    assert result[0].recent_error_event is None


def test_pod_resolver_no_events_backend_swallows(org, app, env, actor, fake_info, permission_resolver):
    """When no events backend is installed, the resolver still
    returns pods (no chip).  Mirrors a cluster whose driver doesn't
    implement list_events."""
    _grant(permission_resolver)
    _bind_default_cluster(app, env)
    pod = _make_pod()
    set_pod_backend_for_tests(_FixedPodBackend([pod]))
    # NOT installing events backend on purpose — should degrade.

    with _tenant_for(org, actor):
        result = LifecycleQuery().astrolift_app_pods(fake_info, app_slug=app.slug)
    assert len(result) == 1
    assert result[0].recent_error_event is None


def test_pod_resolver_event_with_bad_involved_object_ignored(
    org, app, env, actor, fake_info, permission_resolver
):
    """Events whose involved_object isn't a Pod/... shape are
    ignored — defensive against unexpected K8s payloads."""
    _grant(permission_resolver)
    _bind_default_cluster(app, env)
    pod = _make_pod()
    bad_event = SimpleNamespace(
        namespace="acme-hello-app",
        name="evt-1",
        reason="ImagePullBackOff",
        message="not a pod object",
        type="Warning",
        count=1,
        first_seen="2026-05-17T00:00:00Z",
        last_seen="2026-05-17T00:00:00Z",
        involved_object="Deployment/web",  # not a Pod
    )
    set_pod_backend_for_tests(_FixedPodBackend([pod]))
    set_events_backend_for_tests(_FixedEventsBackend([bad_event]))

    with _tenant_for(org, actor):
        result = LifecycleQuery().astrolift_app_pods(fake_info, app_slug=app.slug)
    assert result[0].recent_error_event is None
