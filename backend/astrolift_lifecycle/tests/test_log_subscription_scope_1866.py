"""The app log subscriptions check ``app.read_logs`` at the app's own scope (#1866).

They used to check it targetless, which a team-scoped grant with its team
selected (a team token, or the header) satisfies for every app in the org.
Real RoleBindings on a two-team world: the caller's own app streams, a
sibling team's app completes without a line even though its cluster is wired.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from _sdk.cluster import PodLogLine

from astrolift_identity.models import Policy
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.schema.subscriptions import LifecycleSubscription
from core.cluster_observability import reset_log_backend_for_tests, set_log_backend_for_tests
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_user


class _Lines:
    def __init__(self):
        self.opened = []

    async def stream(self, **kw):
        self.opened.append(kw.get("namespace"))
        yield PodLogLine(
            pod_name=kw.get("pod_name") or "pod",
            container="",
            timestamp=datetime.now(UTC),
            message="hello",
            stream="stdout",
        )


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    world = ScopeWorld("logs-1866")
    cluster = make_cluster(world, "logs-1866")
    for app in (world.medops_app, world.platform_app):
        app.default_tenant_cluster = cluster
        app.save(update_fields=["default_tenant_cluster"])
    world.user = make_user("logs-1866")
    bind_role(
        world.user,
        permissions=[Permission.APP_READ_LOGS],
        kind="TEAM",
        scope_id=world.medops.pk,
        slug="logs-1866-reader",
    )
    return world


async def _lines(world, app):
    info = SimpleNamespace(
        context=SimpleNamespace(
            user=world.user,
            request=None,
            # What the WS handshake resolved: the org, with MedOps selected.
            _ws_tenant=TenantContext(
                organization_id=world.org.pk, actor_user_id=world.user.pk, team_id=world.medops.pk
            ),
        )
    )
    gen = LifecycleSubscription().astrolift_on_app_log(
        info=info, app_slug=app.slug, pod_name="pod", container=None, follow=False, tail_lines=1
    )
    return [line.message async for line in gen]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_team_reads_its_own_apps_logs_and_not_a_siblings(world):
    backend = _Lines()
    set_log_backend_for_tests(backend)
    try:
        own = await _lines(world, world.medops_app)
        sibling = await _lines(world, world.platform_app)
    finally:
        reset_log_backend_for_tests()
    assert own == ["hello"]
    assert sibling == []
    assert len(backend.opened) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("plural", [False, True])
async def test_log_admission_uses_persisted_environment_and_region(world, plural):
    from asgiref.sync import sync_to_async

    def setup():
        cluster = world.medops_app.default_tenant_cluster
        cluster.region = "us-east-1"
        cluster.save(update_fields=["region", "updated_at", "version"])
        env = AppEnvironment.objects.create(
            registered_app=world.medops_app,
            tenant_cluster=cluster,
            name="production",
            k8s_namespace=world.medops_app.k8s_namespace,
        )
        policy = Policy.objects.create(
            organization=world.org,
            name="No production logs",
            slug="log-deny",
            scope_level="ORG",
            effect="DENY",
            action_pattern="app.read_logs",
            resource_pattern={"env": ["production"]},
        )
        return env, policy

    env, policy = await sync_to_async(setup)()
    info = SimpleNamespace(
        context=SimpleNamespace(
            user=world.user,
            request=None,
            _ws_tenant=TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk),
        )
    )
    backend = _Lines()
    set_log_backend_for_tests(backend)
    try:
        kwargs = {"environment_name": env.name} if plural else {"pod_name": "pod"}
        route = (
            LifecycleSubscription().astrolift_on_app_logs
            if plural
            else LifecycleSubscription().astrolift_on_app_log
        )
        assert [
            line async for line in route(info, app_slug=world.medops_app.slug, follow=False, **kwargs)
        ] == []
        assert backend.opened == []
        await sync_to_async(
            lambda: Policy.objects.filter(pk=policy.pk).update(resource_pattern={"region": ["us-east-1"]})
        )()
        assert [
            line async for line in route(info, app_slug=world.medops_app.slug, follow=False, **kwargs)
        ] == []
        assert backend.opened == []
    finally:
        reset_log_backend_for_tests()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_log_operation_context_is_bound_until_inner_stream_is_closed(world):
    import asyncio

    from asgiref.sync import sync_to_async

    from astrolift_identity.abac import current_attributes
    from core.cluster_observability import reset_pod_backend_for_tests, set_pod_backend_for_tests

    def setup():
        cluster = world.medops_app.default_tenant_cluster
        cluster.region = "us-west-2"
        cluster.save(update_fields=["region", "updated_at", "version"])
        return AppEnvironment.objects.create(
            registered_app=world.medops_app,
            tenant_cluster=cluster,
            name="staging",
            k8s_namespace="staging-logs",
        )

    env = await sync_to_async(setup)()
    closed = []

    class BoundLines(_Lines):
        async def stream(self, **kwargs):
            try:
                assert current_attributes().environment == "staging"
                assert current_attributes().region == "us-west-2"
                async for line in super().stream(**kwargs):
                    yield line
                await asyncio.Event().wait()
            finally:
                closed.append(True)

    info = SimpleNamespace(
        context=SimpleNamespace(
            user=world.user,
            request=None,
            _ws_tenant=TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk),
        )
    )
    backend = BoundLines()
    set_log_backend_for_tests(backend)
    set_pod_backend_for_tests(SimpleNamespace(list_pods=lambda **kwargs: [SimpleNamespace(name="pod")]))
    stream = LifecycleSubscription().astrolift_on_app_logs(
        info, app_slug=world.medops_app.slug, environment_name=env.name, follow=True
    )
    try:
        assert (await anext(stream)).message == "hello"
    finally:
        await stream.aclose()
        reset_log_backend_for_tests()
        reset_pod_backend_for_tests()
    assert closed == [True]
    assert backend.opened == ["staging-logs"]
