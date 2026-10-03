"""Real PostgreSQL admission and provider setup; only Kubernetes I/O is replaced."""

import asyncio
import io
import threading

import pytest
from k8s_native.cluster import K8sNativeClusterDriver
from kubernetes import client
from urllib3.response import HTTPResponse

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_drivers.registry import PluginManifest, plugins
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.schema.subscriptions import LifecycleSubscription
from core.cluster_observability import stream_app_logs, stream_app_logs_multi
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, as_tenant, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def live_target(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))
    world = ScopeWorld("live-logs2254")
    actor = make_user("live-logs2254")
    plugin = ProviderPlugin.objects.filter(slug="k8s_native").first()
    if plugin is None:
        ProviderPlugin.objects.bulk_create(
            [ProviderPlugin(name="Kubernetes", slug="k8s_native", plugin_version="0.1.0")]
        )
        plugin = ProviderPlugin.objects.get(slug="k8s_native")
    if not any(p.plugin_id == "k8s_native" for p in plugins.list()):
        plugins.register(
            PluginManifest("k8s_native", "Kubernetes", "0.1.0", {"cluster": K8sNativeClusterDriver})
        )
    assert plugins.get("k8s_native", "cluster") is K8sNativeClusterDriver
    cluster = TenantCluster.objects.create(
        organization=world.org,
        name="Logs",
        slug="live-logs2254",
        provider_plugin=plugin,
        endpoint="https://kubernetes.invalid",
        auth_method="service_account_token",
        auth_config={"token": "test-only-token"},
    )
    app = world.platform_app
    app.default_tenant_cluster = cluster
    app.save(update_fields=["default_tenant_cluster"])
    AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="production")
    bind_role(
        actor, permissions=[Permission.APP_READ_LOGS], kind="APP", scope_id=app.pk, slug="live-logs2254"
    )
    return world, actor, TenantCluster.objects.get(pk=cluster.pk)


@pytest.fixture
def kube_transport(monkeypatch):
    calls = {"discoveries": [], "opens": [], "responses": [], "replicas": ["web-0"]}

    def list_pods(self, **kwargs):
        calls["discoveries"].append((threading.get_ident(), kwargs))
        return client.V1PodList(
            items=[
                client.V1Pod(
                    metadata=client.V1ObjectMeta(name=name, labels={"astrolift.dev/workload": "web"}),
                    spec=client.V1PodSpec(containers=[client.V1Container(name="web")]),
                    status=client.V1PodStatus(phase="Running"),
                )
                for name in calls["replicas"]
            ]
        )

    def read_log(self, **kwargs):
        calls["opens"].append(kwargs)
        response = HTTPResponse(
            body=io.BytesIO(b"2026-10-02T01:00:00Z first line\n2026-10-02T01:00:01Z second line\n"),
            preload_content=False,
        )
        response.released = False
        response.release_conn = lambda: setattr(response, "released", True)
        calls["responses"].append(response)
        return response

    monkeypatch.setattr(client.CoreV1Api, "list_namespaced_pod", list_pods)
    monkeypatch.setattr(client.CoreV1Api, "read_namespaced_pod_log", read_log)
    return calls


def test_single_stream_loads_unprefetched_provider_in_sync_thread(live_target, kube_transport):
    _, _, cluster = live_target
    assert "provider_plugin" not in cluster._state.fields_cache

    async def read():
        return [
            line.message
            async for line in stream_app_logs(
                cluster=cluster,
                namespace="selected-app",
                pod_name="web-0",
                container="web",
                follow=False,
            )
        ]

    assert asyncio.run(read()) == ["first line", "second line"]
    assert kube_transport["responses"][0].released


@pytest.mark.parametrize("plural", [False, True])
def test_subscription_uses_real_app_permission_and_provider(live_target, kube_transport, plural):
    world, actor, _ = live_target

    async def read():
        subscriptions = LifecycleSubscription()
        kwargs = {"info": make_info(actor), "app_slug": world.platform_app.slug, "follow": False}
        if plural:
            stream = subscriptions.astrolift_on_app_logs(**kwargs, environment_name="production")
        else:
            stream = subscriptions.astrolift_on_app_log(**kwargs, pod_name="web-0")
        return [line.message async for line in stream]

    with as_tenant(world, actor):
        assert asyncio.run(read()) == ["first line", "second line"]
    assert all(response.released for response in kube_transport["responses"])


@pytest.mark.parametrize("plural", [False, True])
def test_subscription_denies_sibling_before_kubernetes_io(live_target, kube_transport, plural):
    world, actor, _ = live_target

    async def read():
        subscriptions = LifecycleSubscription()
        kwargs = {"info": make_info(actor), "app_slug": world.medops_app.slug, "follow": False}
        stream = (
            subscriptions.astrolift_on_app_logs(**kwargs)
            if plural
            else subscriptions.astrolift_on_app_log(**kwargs, pod_name="web-0")
        )
        return [line async for line in stream]

    with as_tenant(world, actor):
        assert asyncio.run(read()) == []
    assert kube_transport["discoveries"] == kube_transport["opens"] == []


def test_follow_reads_real_urllib3_lines_and_closes_response(live_target, kube_transport):
    _, _, cluster = live_target

    async def read():
        stream = stream_app_logs(cluster=cluster, namespace="selected-app", pod_name="web-0", container="web")
        try:
            first = await asyncio.wait_for(anext(stream), timeout=2)
            second = await asyncio.wait_for(anext(stream), timeout=2)
            return [first.message, second.message]
        finally:
            await stream.aclose()

    assert asyncio.run(read()) == ["first line", "second line"]
    assert kube_transport["responses"][0].released


def test_replica_refresh_discovers_off_loop_and_closes_all_streams(live_target, kube_transport):
    world, _, cluster = live_target

    async def read():
        loop_thread = threading.get_ident()
        stream = stream_app_logs_multi(
            cluster=cluster,
            namespace="selected-app",
            app_slug=world.platform_app.slug,
            refresh_interval_seconds=0.01,
        )
        try:
            assert (await asyncio.wait_for(anext(stream), timeout=2)).pod_name == "web-0"
            kube_transport["replicas"].append("web-1")
            async with asyncio.timeout(2):
                while (await anext(stream)).pod_name != "web-1":
                    pass
        finally:
            await stream.aclose()
        assert all(thread != loop_thread for thread, _ in kube_transport["discoveries"])

    asyncio.run(read())
    assert len(kube_transport["discoveries"]) >= 2
    assert {row["name"] for row in kube_transport["opens"]} == {"web-0", "web-1"}
    assert all(response.released for response in kube_transport["responses"])
