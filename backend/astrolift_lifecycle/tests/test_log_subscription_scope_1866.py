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
