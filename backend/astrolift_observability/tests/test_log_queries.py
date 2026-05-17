"""Resolver tests for the historical app-log query + multi-pod fan-out
subscription (#482).

Covers ten cases per the issue acceptance:

1. multi-pod subscription fans out lines from N pods
2. multi-pod subscription tolerates per-pod backend errors
3. multi-pod cancellation tears down all child streams
4. multi-pod denies without ``APP_READ_LOGS``
5. multi-pod completes silently when the app has no cluster
6. historical query enforces ``since <= until`` and clamps limit
7. historical query passes level + search filters through
8. historical query pagination via cursor (re-issue with next cursor)
9. historical query reports ``historicalAvailable=False`` when no driver
10. historical query denies without ``APP_READ_LOGS``

The aggregator driver is faked through
``core.cluster_log_query.set_log_query_driver_for_tests`` so we can
drive the resolver without standing up a real Loki backend. Per-pod
streams are faked through the existing
``core.cluster_observability.set_log_backend_for_tests`` /
``set_pod_backend_for_tests`` hooks.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from datetime import UTC
from types import SimpleNamespace

import pytest
from _sdk.cluster import PodInfo, PodLogLine
from _sdk.log_stream import LogLine, LogPage

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.schema.subscriptions import LifecycleSubscription
from astrolift_observability.schema.log_queries import LogHistoryQuery
from astrolift_registry.models import RegisteredApp
from core.cluster_log_query import (
    reset_log_query_driver_for_tests,
    set_log_query_driver_for_tests,
)
from core.cluster_observability import (
    reset_log_backend_for_tests,
    reset_pod_backend_for_tests,
    set_log_backend_for_tests,
    set_pod_backend_for_tests,
)
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


def _info(user=None):
    return SimpleNamespace(
        context=SimpleNamespace(user=user, request=SimpleNamespace(user=user))
    )


def _scaffold(
    *,
    log_driver: str | None = None,
    log_config: dict | None = None,
):
    """Mint a self-contained org + cluster + app + env. Caller picks
    whether the cluster carries a wired log-aggregator driver."""
    org = Organization.objects.create(name="Acme Logs", slug="acme-logs")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-logs")
    project = Project.objects.create(
        organization=org, team=team, name="Demo", slug="demo-logs"
    )
    plugin = ProviderPlugin(
        name="K8s",
        slug="k8s-logs",
        version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    plugin = ProviderPlugin.objects.get(slug="k8s-logs")

    cfg: dict = {}
    if log_driver:
        cfg["log_driver"] = log_driver
        cfg["log_config"] = log_config or {}
    cluster = TenantCluster.objects.create(
        organization=org,
        name="prod",
        slug="prod-logs",
        provider_plugin=plugin,
        provider_config=cfg,
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello Logs",
        slug="hello-logs",
        k8s_namespace="acme-hello",
        provisioning_status="ready",
        default_tenant_cluster=cluster,
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://hello-logs.example.com",
    )
    return org, app, cluster


def _tenant(org, actor=None):
    return tenant_context(
        TenantContext(
            organization_id=org.id,
            actor_user_id=getattr(actor, "id", None),
        )
    )


def _fake_pod(name: str, workload: str = "web") -> PodInfo:
    return PodInfo(
        name=name,
        workload=workload,
        status="Running",
        phase="Running",
        ready=True,
        restarts=0,
        age=dt.datetime.now(UTC),
        node="node-a",
        container_statuses=[],
    )


# ---------------------------------------------------------------------------
# Multi-pod subscription — five cases
# ---------------------------------------------------------------------------


class _FixedPodBackend:
    def __init__(self, pods):
        self._pods = list(pods)

    def list_pods(self, *, auth, namespace, app_slug):
        return list(self._pods)


class _ScriptedLogBackend:
    """Returns a different scripted line list per pod. Lets the fan-out
    test assert which lines came from which replica."""

    def __init__(self, lines_by_pod: dict[str, list[PodLogLine]]):
        self._by_pod = lines_by_pod

    async def stream(self, **kw):
        pod_name = kw["pod_name"]
        for line in self._by_pod.get(pod_name, []):
            await asyncio.sleep(0)
            yield line


class _ExplodingLogBackend:
    """Raises for one named pod, scripts another. Lets the isolation
    test assert that a broken pod doesn't kill its siblings."""

    def __init__(
        self,
        *,
        good_pod: str,
        good_lines: list[PodLogLine],
        bad_pod: str,
    ):
        self._good_pod = good_pod
        self._good_lines = good_lines
        self._bad_pod = bad_pod

    async def stream(self, **kw):
        pod_name = kw["pod_name"]
        if pod_name == self._bad_pod:
            raise RuntimeError(f"backend offline for {pod_name}")
        if pod_name == self._good_pod:
            for line in self._good_lines:
                await asyncio.sleep(0)
                yield line


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_multi_pod_subscription_fans_out_across_replicas(permission_resolver):
    """Three pods, three lines each. The merged stream surfaces all
    nine lines (order between pods is arbitrary but every line lands
    with its source pod_name tag)."""
    from django.contrib.auth import get_user_model

    actor = await asyncio.to_thread(
        get_user_model().objects.create,
        username="multi-actor",
        email="multi@test",
    )
    org, app, _cluster = await asyncio.to_thread(_scaffold)
    permission_resolver.grant(Permission.APP_READ_LOGS)

    pods = [_fake_pod(f"hello-app-web-{i}") for i in range(3)]
    set_pod_backend_for_tests(_FixedPodBackend(pods))
    lines_by_pod = {
        pod.name: [
            PodLogLine(
                pod_name=pod.name,
                container="web",
                timestamp=dt.datetime.now(UTC),
                message=f"{pod.name} line {i}",
                stream="stdout",
            )
            for i in range(3)
        ]
        for pod in pods
    }
    set_log_backend_for_tests(_ScriptedLogBackend(lines_by_pod))
    try:
        with _tenant(org, actor):
            sub = LifecycleSubscription()
            gen = sub.astrolift_on_app_logs(
                info=_info(actor),
                app_slug=app.slug,
                follow=False,
                tail_lines=10,
            )
            collected = []
            async with asyncio.timeout(2.0):
                async for line in gen:
                    collected.append(line)
    finally:
        reset_log_backend_for_tests()
        reset_pod_backend_for_tests()

    assert len(collected) == 9
    pod_names = sorted({line.pod_name for line in collected})
    assert pod_names == sorted(pod.name for pod in pods)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_multi_pod_subscription_isolates_broken_pods(permission_resolver):
    """One pod's backend raises on open; the merged stream still
    surfaces every line from the healthy replica."""
    from django.contrib.auth import get_user_model

    actor = await asyncio.to_thread(
        get_user_model().objects.create,
        username="iso-actor",
        email="iso@test",
    )
    org, app, _cluster = await asyncio.to_thread(_scaffold)
    permission_resolver.grant(Permission.APP_READ_LOGS)

    pods = [_fake_pod("good-pod"), _fake_pod("bad-pod")]
    set_pod_backend_for_tests(_FixedPodBackend(pods))
    good_lines = [
        PodLogLine(
            pod_name="good-pod",
            container="web",
            timestamp=dt.datetime.now(UTC),
            message=f"line {i}",
            stream="stdout",
        )
        for i in range(3)
    ]
    set_log_backend_for_tests(
        _ExplodingLogBackend(
            good_pod="good-pod",
            good_lines=good_lines,
            bad_pod="bad-pod",
        )
    )
    try:
        with _tenant(org, actor):
            sub = LifecycleSubscription()
            gen = sub.astrolift_on_app_logs(
                info=_info(actor),
                app_slug=app.slug,
                follow=False,
            )
            collected = []
            async with asyncio.timeout(2.0):
                async for line in gen:
                    collected.append(line)
    finally:
        reset_log_backend_for_tests()
        reset_pod_backend_for_tests()

    # All three lines from the healthy pod survive the bad pod's
    # failure to open its stream.
    assert [line.message for line in collected] == ["line 0", "line 1", "line 2"]
    assert all(line.pod_name == "good-pod" for line in collected)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_multi_pod_subscription_cancels_child_streams(permission_resolver):
    """Closing the merged generator mid-stream cancels every per-pod
    task — the backend records the cancellation for both pods."""
    from django.contrib.auth import get_user_model

    actor = await asyncio.to_thread(
        get_user_model().objects.create,
        username="cancel-actor",
        email="cancel@test",
    )
    org, app, _cluster = await asyncio.to_thread(_scaffold)
    permission_resolver.grant(Permission.APP_READ_LOGS)

    pods = [_fake_pod("pod-a"), _fake_pod("pod-b")]
    set_pod_backend_for_tests(_FixedPodBackend(pods))

    cancelled: set[str] = set()

    class _BlockingBackend:
        async def stream(self, **kw):
            pod_name = kw["pod_name"]
            try:
                while True:
                    await asyncio.sleep(0.01)
                    yield PodLogLine(
                        pod_name=pod_name,
                        container="web",
                        timestamp=dt.datetime.now(UTC),
                        message=f"{pod_name} tick",
                        stream="stdout",
                    )
            except (asyncio.CancelledError, GeneratorExit):
                cancelled.add(pod_name)
                raise

    set_log_backend_for_tests(_BlockingBackend())
    try:
        with _tenant(org, actor):
            sub = LifecycleSubscription()
            gen = sub.astrolift_on_app_logs(
                info=_info(actor),
                app_slug=app.slug,
                follow=True,
            )
            iterator = gen.__aiter__()
            # Drain a couple of frames so both pod tasks have actually
            # started writing into the shared queue.
            await asyncio.wait_for(iterator.__anext__(), timeout=1.0)
            await asyncio.wait_for(iterator.__anext__(), timeout=1.0)
            await gen.aclose()
            # Give the cancellation a tick to propagate through the
            # finally blocks of each child task.
            await asyncio.sleep(0.05)
    finally:
        reset_log_backend_for_tests()
        reset_pod_backend_for_tests()

    # Both per-pod tasks observed cancellation — sockets released.
    assert cancelled == {"pod-a", "pod-b"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_multi_pod_subscription_denies_without_permission(permission_resolver):
    """Missing ``APP_READ_LOGS`` ends the subscription before opening
    any pod stream — the generator yields nothing."""
    from django.contrib.auth import get_user_model

    actor = await asyncio.to_thread(
        get_user_model().objects.create,
        username="deny-actor",
        email="deny@test",
    )
    org, app, _cluster = await asyncio.to_thread(_scaffold)
    # Note: we intentionally do NOT grant APP_READ_LOGS.

    pods = [_fake_pod("p1")]
    set_pod_backend_for_tests(_FixedPodBackend(pods))
    set_log_backend_for_tests(_ScriptedLogBackend({}))
    try:
        with _tenant(org, actor):
            sub = LifecycleSubscription()
            gen = sub.astrolift_on_app_logs(
                info=_info(actor),
                app_slug=app.slug,
                follow=False,
            )
            collected = []
            async for line in gen:
                collected.append(line)
    finally:
        reset_log_backend_for_tests()
        reset_pod_backend_for_tests()

    assert collected == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_multi_pod_subscription_completes_without_cluster(permission_resolver):
    """An app with no wired cluster ends the subscription silently —
    the FE renders the empty log-pane state rather than an error."""
    from django.contrib.auth import get_user_model

    actor = await asyncio.to_thread(
        get_user_model().objects.create,
        username="nocluster-actor",
        email="nocluster@test",
    )
    # Scaffold the org but bypass the cluster wiring on the app.
    org = await asyncio.to_thread(
        Organization.objects.create,
        name="Acme Empty",
        slug="acme-empty",
    )
    team = await asyncio.to_thread(
        Team.objects.create,
        organization=org,
        name="Eng",
        slug="eng-empty",
    )
    project = await asyncio.to_thread(
        Project.objects.create,
        organization=org,
        team=team,
        name="Demo",
        slug="demo-empty",
    )
    app = await asyncio.to_thread(
        RegisteredApp.objects.create,
        organization=org,
        team=team,
        project=project,
        name="No Cluster",
        slug="no-cluster",
        provisioning_status="ready",
    )

    permission_resolver.grant(Permission.APP_READ_LOGS)
    set_pod_backend_for_tests(_FixedPodBackend([]))
    set_log_backend_for_tests(_ScriptedLogBackend({}))
    try:
        with _tenant(org, actor):
            sub = LifecycleSubscription()
            gen = sub.astrolift_on_app_logs(
                info=_info(actor),
                app_slug=app.slug,
                follow=False,
            )
            collected = []
            async for line in gen:
                collected.append(line)
    finally:
        reset_log_backend_for_tests()
        reset_pod_backend_for_tests()

    assert collected == []


# ---------------------------------------------------------------------------
# Historical query — five cases
# ---------------------------------------------------------------------------


class _FakeLogQueryDriver:
    """In-memory aggregator stand-in. Captures the resolver-supplied
    args + returns a scripted page so the tests can assert dispatch
    shape end-to-end."""

    def __init__(self, page: LogPage):
        self.calls: list[dict] = []
        self.page = page

    def query_logs(
        self,
        query,
        since,
        until,
        *,
        limit,
        cursor,
        level,
        search,
    ):
        self.calls.append(
            {
                "query": query,
                "since": since,
                "until": until,
                "limit": limit,
                "cursor": cursor,
                "level": level,
                "search": search,
            }
        )
        return self.page


def _line(message: str, *, ts_ns: int = 0, pod: str = "p1") -> LogLine:
    return LogLine(
        timestamp=str(ts_ns or 1_700_000_000_000_000_000),
        namespace="acme-hello",
        pod=pod,
        container="web",
        message=message,
        level=None,
        labels=None,
    )


@pytest.mark.django_db
def test_historical_query_normalizes_window_and_clamps_limit(permission_resolver):
    """``since > until`` swaps to a valid range; ``limit`` over the
    ceiling clamps; ``limit`` under the floor clamps. The driver sees
    the resolved values, not the caller's."""
    org, app, _cluster = _scaffold(
        log_driver="loki",
        log_config={"endpoint": "http://loki:3100"},
    )
    permission_resolver.grant(Permission.APP_READ_LOGS)

    driver = _FakeLogQueryDriver(LogPage(items=[]))
    set_log_query_driver_for_tests(driver)
    try:
        with _tenant(org):
            # Reverse order + outsized limit + insane window.
            since = dt.datetime(2026, 1, 1, tzinfo=UTC)
            until = dt.datetime(2025, 1, 1, tzinfo=UTC)
            LogHistoryQuery().astrolift_app_logs(
                _info(),
                app_slug=app.slug,
                since=since,
                until=until,
                limit=999_999,
            )
    finally:
        reset_log_query_driver_for_tests()

    assert len(driver.calls) == 1
    call = driver.calls[0]
    # since/until were swapped + clamped to the 31d ceiling.
    parsed_since = dt.datetime.fromisoformat(call["since"])
    parsed_until = dt.datetime.fromisoformat(call["until"])
    assert parsed_since < parsed_until
    span = (parsed_until - parsed_since).total_seconds()
    assert span <= 31 * 86400 + 1
    # Limit clamped to the platform ceiling.
    assert call["limit"] == 5000


@pytest.mark.django_db
def test_historical_query_passes_level_and_search_through(permission_resolver):
    """The resolver hands the operator-supplied level/search down to
    the driver verbatim AND filters the page contents client-side as a
    uniform fallback for backends that can't push the filter down."""
    org, app, _cluster = _scaffold(
        log_driver="loki",
        log_config={"endpoint": "http://loki:3100"},
    )
    permission_resolver.grant(Permission.APP_READ_LOGS)

    # Page contains lines that match + don't match the level/search
    # so we can assert the client-side filter as well.
    page = LogPage(
        items=[
            _line("ERROR database down", pod="web-1"),
            _line("info background tick", pod="web-1"),
            _line("WARN slow query", pod="web-1"),
        ],
        next_cursor="",
    )
    driver = _FakeLogQueryDriver(page)
    set_log_query_driver_for_tests(driver)
    try:
        with _tenant(org):
            since = dt.datetime.now(UTC) - dt.timedelta(minutes=30)
            until = dt.datetime.now(UTC)
            result = LogHistoryQuery().astrolift_app_logs(
                _info(),
                app_slug=app.slug,
                since=since,
                until=until,
                level="error",
                search="database",
            )
    finally:
        reset_log_query_driver_for_tests()

    assert driver.calls[0]["level"] == "error"
    assert driver.calls[0]["search"] == "database"
    # Only the ERROR line that also mentions "database" survives both
    # filters.
    assert [item.message for item in result.items] == ["ERROR database down"]
    assert result.items[0].level == "error"


@pytest.mark.django_db
def test_historical_query_pagination_via_cursor(permission_resolver):
    """Calling with the previous page's ``nextCursor`` returns the
    next slice — the resolver hands the cursor to the driver unchanged
    and surfaces the new cursor back to the caller."""
    org, app, _cluster = _scaffold(
        log_driver="loki",
        log_config={"endpoint": "http://loki:3100"},
    )
    permission_resolver.grant(Permission.APP_READ_LOGS)

    page = LogPage(
        items=[_line("line one"), _line("line two")],
        next_cursor="1700000000000000123",
    )
    driver = _FakeLogQueryDriver(page)
    set_log_query_driver_for_tests(driver)
    try:
        with _tenant(org):
            since = dt.datetime.now(UTC) - dt.timedelta(minutes=15)
            until = dt.datetime.now(UTC)
            first = LogHistoryQuery().astrolift_app_logs(
                _info(),
                app_slug=app.slug,
                since=since,
                until=until,
            )
            assert first.next_cursor == "1700000000000000123"

            # Page two — caller passes the cursor back; driver sees it.
            LogHistoryQuery().astrolift_app_logs(
                _info(),
                app_slug=app.slug,
                since=since,
                until=until,
                cursor=first.next_cursor,
            )
    finally:
        reset_log_query_driver_for_tests()

    assert driver.calls[0]["cursor"] == ""
    assert driver.calls[1]["cursor"] == "1700000000000000123"


@pytest.mark.django_db
def test_historical_query_reports_unavailable_without_driver(permission_resolver):
    """Cluster has no log driver wired — page returns
    ``historical_available=False`` + empty items so the FE renders the
    "live tail only" badge."""
    org, app, _cluster = _scaffold(log_driver=None)
    permission_resolver.grant(Permission.APP_READ_LOGS)

    # No driver override — the resolver inspects provider_config and
    # finds no log_driver.
    with _tenant(org):
        since = dt.datetime.now(UTC) - dt.timedelta(minutes=30)
        until = dt.datetime.now(UTC)
        result = LogHistoryQuery().astrolift_app_logs(
            _info(),
            app_slug=app.slug,
            since=since,
            until=until,
        )
    assert result.items == []
    assert result.historical_available is False


@pytest.mark.django_db
def test_historical_query_denies_without_read_logs(permission_resolver):
    """Resolver-entry permission check fires before the driver is
    looked up — denial raises ``PermissionDenied`` (queries surface
    permission failures via raise, not envelope, per the existing
    obs-query contract)."""
    org, app, _cluster = _scaffold(
        log_driver="loki",
        log_config={"endpoint": "http://loki:3100"},
    )
    # NOTE: APP_READ_LOGS not granted.

    with _tenant(org), pytest.raises(PermissionDenied):
        LogHistoryQuery().astrolift_app_logs(
            _info(),
            app_slug=app.slug,
            since=dt.datetime.now(UTC) - dt.timedelta(minutes=1),
            until=dt.datetime.now(UTC),
        )
