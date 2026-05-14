"""
Observability resolvers — astrolift_app_pods + onAppLog subscription.

The pod resolver hits a stubbed pod backend (no real cluster); the
subscription test installs a fake log backend and asserts the
async generator yields what the backend pushes + tears down on
cancellation.

Both gates we care about:
  - APP_READ_LOGS permission denial returns empty / never yields.
  - Tenant scoping — the @tenant_scoped decorator raises when no
    tenant is bound; we exercise that via the contextvar.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_lifecycle.schema.subscriptions import LifecycleSubscription
from core.decorators import TenantRequired
from core.k8s.logs import LogLine, set_log_backend
from core.k8s.pods import (
    ContainerStatusInfo,
    PodInfo,
    install_pod_backend_function,
    reset_pod_backend,
)
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# astrolift_app_pods
# ---------------------------------------------------------------------------


def _grant_read_logs(resolver):
    resolver.grant(Permission.APP_READ_LOGS)
    resolver.grant(Permission.APP_READ)


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _install_pods(pods):
    install_pod_backend_function(
        lambda *, cluster, namespace, app_slug: list(pods),
    )


def _fake_pod(**overrides):
    base = {
        "name": "hello-app-web-abc-1",
        "workload": "web",
        "status": "Running",
        "phase": "Running",
        "ready": True,
        "restarts": 0,
        "age": datetime.now(UTC),
        "node": "ip-10-0-0-12.ec2.internal",
        "container_statuses": [
            ContainerStatusInfo(
                name="web",
                ready=True,
                restart_count=0,
                image="ghcr.io/acme/hello:v1",
                state="running",
            ),
        ],
    }
    base.update(overrides)
    return PodInfo(**base)


def test_app_pods_returns_stub_pods(org, app, env, actor, fake_info, permission_resolver):
    """Happy path — backend stub returns two pods, resolver returns
    them with the full container-status payload intact."""
    _grant_read_logs(permission_resolver)
    # Bind the env's cluster as the app's default so the resolver
    # has a cluster to point at.
    app.default_tenant_cluster = env.tenant_cluster
    app.save(update_fields=["default_tenant_cluster"])
    pods = [
        _fake_pod(name="hello-app-web-1"),
        _fake_pod(
            name="hello-app-web-2",
            status="CrashLoopBackOff",
            ready=False,
            restarts=4,
            container_statuses=[
                ContainerStatusInfo(
                    name="web",
                    ready=False,
                    restart_count=4,
                    image="ghcr.io/acme/hello:v1",
                    state="waiting",
                    waiting_reason="CrashLoopBackOff",
                )
            ],
        ),
    ]
    _install_pods(pods)
    try:
        q = LifecycleQuery()
        with _tenant_for(org, actor):
            result = q.astrolift_app_pods(fake_info, app_slug=app.slug)
    finally:
        reset_pod_backend()

    assert [p.name for p in result] == ["hello-app-web-1", "hello-app-web-2"]
    assert result[1].status == "CrashLoopBackOff"
    assert result[1].ready is False
    assert result[1].restarts == 4
    assert result[1].container_statuses[0].waiting_reason == "CrashLoopBackOff"


def test_app_pods_empty_when_no_cluster_wired(org, app, actor, fake_info, permission_resolver):
    """App with no default cluster + no env-named cluster returns
    an empty list — the UI renders 'no pods yet' for this case."""
    _grant_read_logs(permission_resolver)
    _install_pods([_fake_pod()])
    try:
        q = LifecycleQuery()
        with _tenant_for(org, actor):
            result = q.astrolift_app_pods(fake_info, app_slug=app.slug)
    finally:
        reset_pod_backend()
    assert result == []


def test_app_pods_uses_environment_cluster_when_given(
    org, app, env, env_requires_approval, actor, fake_info, permission_resolver
):
    """When the caller names an environment, its cluster is what
    the resolver hands to the pod backend."""
    _grant_read_logs(permission_resolver)
    saw = {}

    def _backend(*, cluster, namespace, app_slug):
        saw["cluster_slug"] = cluster.slug
        saw["namespace"] = namespace
        saw["app_slug"] = app_slug
        return [_fake_pod()]

    install_pod_backend_function(_backend)
    try:
        q = LifecycleQuery()
        with _tenant_for(org, actor):
            q.astrolift_app_pods(
                fake_info,
                app_slug=app.slug,
                environment_name=env_requires_approval.name,
            )
    finally:
        reset_pod_backend()

    assert saw["app_slug"] == app.slug
    assert saw["cluster_slug"] == env_requires_approval.tenant_cluster.slug


def test_app_pods_swallows_cluster_errors(org, app, env, actor, fake_info, permission_resolver):
    """A k8s API error (cluster offline, timeout, …) yields an
    empty list — never a GraphQL error. The platform-event log is
    the diagnostic surface for cluster outages."""
    _grant_read_logs(permission_resolver)
    app.default_tenant_cluster = env.tenant_cluster
    app.save(update_fields=["default_tenant_cluster"])

    def _exploding(*, cluster, namespace, app_slug):
        raise RuntimeError("connection refused")

    install_pod_backend_function(_exploding)
    try:
        q = LifecycleQuery()
        with _tenant_for(org, actor):
            result = q.astrolift_app_pods(fake_info, app_slug=app.slug)
    finally:
        reset_pod_backend()
    assert result == []


def test_app_pods_denies_without_read_logs(org, app, env, actor, fake_info, permission_resolver):
    """Resolver-entry permission check fires before any cluster
    work — denial raises PermissionDenied, not an empty list."""
    # Note: we don't grant APP_READ_LOGS.
    q = LifecycleQuery()
    with _tenant_for(org, actor), pytest.raises(PermissionDenied):
        q.astrolift_app_pods(fake_info, app_slug=app.slug)


def test_app_pods_denies_without_tenant(app, env, fake_info, permission_resolver):
    """Calling outside a bound tenant fails the @tenant_scoped
    guard, even when the permission resolver grants the verb."""
    _grant_read_logs(permission_resolver)
    q = LifecycleQuery()
    # No tenant_context — guard rejects.
    with pytest.raises(TenantRequired):
        q.astrolift_app_pods(fake_info, app_slug=app.slug)


def test_app_pods_skips_pods_when_wrong_tenant(org, app, env, actor, fake_info, permission_resolver):
    """Tenant scoping: an app in a different org isn't visible. The
    resolver returns [] because the slug filter doesn't see the row.

    We exercise this by leaving the actor's tenant bound to the
    real org but querying a slug that doesn't exist there.
    """
    _grant_read_logs(permission_resolver)
    _install_pods([_fake_pod()])
    try:
        q = LifecycleQuery()
        with _tenant_for(org, actor):
            result = q.astrolift_app_pods(fake_info, app_slug="this-app-does-not-exist")
    finally:
        reset_pod_backend()
    assert result == []


# ---------------------------------------------------------------------------
# astrolift_on_app_log subscription
# ---------------------------------------------------------------------------


class _FakeLogBackend:
    """Pushes a scripted list of LogLines, then closes — exactly what
    we want to drive the async generator deterministically."""

    def __init__(self, lines: list[LogLine]):
        self._lines = lines
        self.cancelled = False

    async def stream(self, **kw):
        try:
            for line in self._lines:
                # Yield to the loop so the consumer can drain.
                await asyncio.sleep(0)
                yield line
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def _collect_first_n(gen, n: int, timeout: float = 1.0):
    out = []
    async with asyncio.timeout(timeout):
        async for line in gen:
            out.append(line)
            if len(out) >= n:
                break
    return out


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_on_app_log_yields_backend_lines(org, app, env, actor, permission_resolver):
    """Subscription yields each line the backend produces, mapping
    the LogLine dataclass into the GraphQL type.

    Uses transaction=True so the ORM rows are visible from the
    subscription resolver's ``sync_to_async`` thread — the default
    django_db wraps each test in a savepoint that's only visible from
    the test thread, which trips up cross-thread ORM access."""
    from types import SimpleNamespace

    _grant_read_logs(permission_resolver)
    app.default_tenant_cluster = env.tenant_cluster
    app.save(update_fields=["default_tenant_cluster"])

    lines = [
        LogLine(
            pod_name="hello-app-web-1",
            container="web",
            timestamp=datetime.now(UTC),
            message=f"hello {i}",
            stream="stdout",
        )
        for i in range(3)
    ]
    backend = _FakeLogBackend(lines)
    set_log_backend(backend)
    try:
        with _tenant_for(org, actor):
            sub = LifecycleSubscription()
            info = SimpleNamespace(context=SimpleNamespace(user=actor, request=None))
            gen = sub.astrolift_on_app_log(
                info=info,
                app_slug=app.slug,
                pod_name="hello-app-web-1",
                container=None,
                follow=False,
                tail_lines=10,
            )
            result = await _collect_first_n(gen, 3)
    finally:
        from core.k8s.logs import reset_log_backend

        reset_log_backend()

    assert [r.message for r in result] == ["hello 0", "hello 1", "hello 2"]
    assert all(r.pod_name == "hello-app-web-1" for r in result)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_on_app_log_tears_down_on_cancel(org, app, env, actor, permission_resolver):
    """Closing the async generator early (subscriber disconnect)
    propagates CancelledError to the backend so resources are
    released."""
    from types import SimpleNamespace

    _grant_read_logs(permission_resolver)
    app.default_tenant_cluster = env.tenant_cluster
    app.save(update_fields=["default_tenant_cluster"])

    class _BlockingBackend:
        def __init__(self):
            self.cancelled = False

        async def stream(self, **kw):
            try:
                # Stay alive long enough for the consumer to cancel.
                while True:
                    await asyncio.sleep(0.01)
                    yield LogLine(
                        pod_name=kw["pod_name"],
                        container="",
                        timestamp=datetime.now(UTC),
                        message="tick",
                        stream="stdout",
                    )
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    backend = _BlockingBackend()
    set_log_backend(backend)
    try:
        with _tenant_for(org, actor):
            sub = LifecycleSubscription()
            info = SimpleNamespace(context=SimpleNamespace(user=actor, request=None))
            gen = sub.astrolift_on_app_log(
                info=info,
                app_slug=app.slug,
                pod_name="hello-app-web-1",
                container=None,
                follow=True,
                tail_lines=0,
            )

            # Drain one event so the generator is mid-flight, then
            # close it to trigger tear-down.
            iterator = gen.__aiter__()
            first = await asyncio.wait_for(iterator.__anext__(), timeout=1.0)
            assert first.message == "tick"
            await gen.aclose()
    finally:
        from core.k8s.logs import reset_log_backend

        reset_log_backend()

    assert backend.cancelled is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_on_app_log_denies_without_permission(org, app, env, actor, permission_resolver):
    """Subscription resolver checks APP_READ_LOGS before opening
    a stream. Denial completes the generator with no yields."""
    from types import SimpleNamespace

    # No grants — APP_READ_LOGS missing.
    set_log_backend(_FakeLogBackend([]))
    try:
        with _tenant_for(org, actor):
            sub = LifecycleSubscription()
            info = SimpleNamespace(context=SimpleNamespace(user=actor, request=None))
            gen = sub.astrolift_on_app_log(
                info=info,
                app_slug=app.slug,
                pod_name="hello-app-web-1",
                container=None,
                follow=False,
                tail_lines=0,
            )
            collected = []
            async for line in gen:
                collected.append(line)
    finally:
        from core.k8s.logs import reset_log_backend

        reset_log_backend()

    assert collected == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_on_app_log_completes_without_cluster(org, app, actor, permission_resolver):
    """If the app has no cluster wired, the subscription completes
    silently rather than erroring — the UI renders the empty
    log-pane state."""
    from types import SimpleNamespace

    _grant_read_logs(permission_resolver)
    set_log_backend(_FakeLogBackend([]))
    try:
        with _tenant_for(org, actor):
            sub = LifecycleSubscription()
            info = SimpleNamespace(context=SimpleNamespace(user=actor, request=None))
            gen = sub.astrolift_on_app_log(
                info=info,
                app_slug=app.slug,
                pod_name="hello-app-web-1",
                container=None,
                follow=False,
                tail_lines=0,
            )
            collected = []
            async for line in gen:
                collected.append(line)
    finally:
        from core.k8s.logs import reset_log_backend

        reset_log_backend()

    assert collected == []
