"""Exec opens that name no environment (#2209), on real PostgreSQL.

Real bearer authentication, role bindings, relay and ``K8sExecBackend``;
only the kubelet exec stream is recorded, so no pod is contacted. An open
without ``environmentId`` or a reviewed target reaches an app's only
environment. For an app with several it is refused before any exec or
audit row: taking the first one would be a guess at the runtime the
caller meant. An open that names its environment with a reviewed target
still runs there.

Each connection sends its frames and then disconnects, so a relay that
wrongly keeps a refused connection open fails here instead of hanging.
"""

import asyncio
import json

import pytest
from asgiref.sync import async_to_sync

from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member, Policy
from astrolift_lifecycle.models import AppEnvironment
from astrolift_operations.models import Event
from astrolift_registry.models import RegisteredApp, Workload
from core.cluster_exec import (
    K8sExecBackend,
    _resolve_target,
    reset_driver_exec_backend_for_tests,
    set_driver_exec_backend_for_tests,
)
from core.exec_targets import review_exec_target
from core.permissions import Permission
from core.schema.exec_ws import exec_ws_application, get_exec_backend, set_exec_backend
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_user

pytestmark = pytest.mark.django_db(transaction=True)

POD = "web-7c9f-abc"
OPEN = {"type": "open", "container": "main", "command": ["sh"], "tty": False}


class _Stream:
    """A kubelet exec stream that stays idle until the relay closes it."""

    def __init__(self):
        self.closed = asyncio.Event()

    async def read_stdout(self):
        await self.closed.wait()
        raise StopAsyncIteration

    read_stderr = read_stdout

    async def wait_exit(self):
        await self.closed.wait()
        return 0

    async def write_stdin(self, data):
        return None

    async def resize(self, rows, cols):
        return None

    async def close(self):
        self.closed.set()


class _Kubelet:
    def __init__(self):
        self.opens = []

    def interactive_exec(self, *, auth, namespace, pod_name, container, command, tty=True):
        self.opens.append(
            {"cluster": auth.slug, "namespace": namespace, "pod": pod_name, "container": container}
        )
        return _Stream()


@pytest.fixture
def world():
    world = ScopeWorld("legacy-exec-2209")
    world.user = make_user("legacy-exec-2209")
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    bind_role(
        world.user,
        permissions=[Permission.APP_EXEC_POD],
        kind="APP",
        scope_id=world.medops_app.pk,
        slug="legacy-exec-2209",
    )
    world.cluster_a = make_cluster(world, "legacy-exec-2209-a")
    world.cluster_b = make_cluster(world, "legacy-exec-2209-b")
    world.primary = AppEnvironment.objects.create(
        registered_app=world.medops_app,
        tenant_cluster=world.cluster_a,
        name="production",
        k8s_namespace="legacy-exec-production-2209",
    )
    issued = mint_token()
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="Legacy exec",
        token_hash=issued.token_hash,
        scopes=["read:apps", "write:apps"],
    )
    world.bearer = issued.plaintext
    return world


@pytest.fixture
def kubelet():
    previous = get_exec_backend()
    set_exec_backend(K8sExecBackend())
    recorder = _Kubelet()
    set_driver_exec_backend_for_tests(recorder)
    yield recorder
    reset_driver_exec_backend_for_tests()
    set_exec_backend(previous)


def second_environment(world):
    return AppEnvironment.objects.create(
        registered_app=world.medops_app,
        tenant_cluster=world.cluster_b,
        name="staging",
        k8s_namespace="legacy-exec-staging-2209",
    )


def relay(world, *frames, environment=None):
    """One connection that sends ``frames`` and then disconnects; what the relay sent."""
    incoming = [{"type": "websocket.receive", "text": json.dumps(frame)} for frame in frames]
    sent = []

    async def receive():
        return incoming.pop(0) if incoming else {"type": "websocket.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": f"/app/exec/{world.medops_app.slug}/{POD}",
        "query_string": f"environmentId={environment.guid}".encode() if environment else b"",
        "headers": [
            (b"authorization", f"Bearer {world.bearer}".encode()),
            (b"x-astrolift-organization", str(world.org.guid).encode()),
        ],
    }
    async_to_sync(exec_ws_application)(scope, receive, send)
    return sent


class _Pods:
    """The provider driver a reviewed open reads its pod through: a pod named
    ``POD`` in each environment's namespace, then the recorded exec stream."""

    def __init__(self, world, kubelet, workload):
        self.kubelet = kubelet
        self.labels = {
            environment.k8s_namespace: {
                "astrolift.dev/app": world.medops_app.slug,
                "astrolift.dev/environment": environment.name,
                "astrolift.dev/workload": workload.slug,
            }
            for environment in AppEnvironment.objects.filter(registered_app=world.medops_app)
        }

    def get_manifest(self, cluster_slug, namespace, kind, name):
        return {
            "metadata": {
                "name": name,
                "namespace": namespace,
                "uid": f"uid-{namespace}",
                "labels": self.labels[namespace],
            },
            "spec": {"containers": [{"name": "main"}]},
        }

    def interactive_exec(self, **kwargs):
        return self.kubelet.interactive_exec(**kwargs)


def frames_of(sent):
    return [json.loads(message["text"]) for message in sent if message["type"] == "websocket.send"]


def opened_audits(world):
    return Event.objects.filter(organization_id=world.org.pk, event_type="app.exec_pod.opened")


def test_an_open_naming_no_environment_is_refused_for_an_app_with_several(world, kubelet):
    second_environment(world)
    sent = relay(world, OPEN, {"type": "stdin", "data": "never forwarded\n"})
    assert sent[0] == {"type": "websocket.accept"}
    refusal = json.loads(sent[1]["text"])
    assert refusal["type"] == "error"
    assert "more than one environment" in refusal["message"]
    assert sent[2] == {"type": "websocket.close", "code": 4409}
    assert frames_of(sent) == [refusal]
    assert kubelet.opens == []
    assert not opened_audits(world).exists()


def test_an_open_naming_its_environment_still_runs_there_for_an_app_with_several(world, kubelet, monkeypatch):
    staging = second_environment(world)
    workload = Workload.objects.create(
        registered_app=world.medops_app, slug="web", name="Web", kind="deployment"
    )
    pods = _Pods(world, kubelet, workload)
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: pods)
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
        review = review_exec_target(
            app_slug=world.medops_app.slug,
            workload_slug=workload.slug,
            environment_guid=str(staging.guid),
            pod_name=POD,
            container="main",
        )
    target = {
        key.split("_")[0] + "".join(piece.title() for piece in key.split("_")[1:]): value
        for key, value in review.facts.items()
    }
    [ready] = frames_of(relay(world, {**OPEN, "target": target}, environment=staging))
    assert ready["type"] == "ready" and ready["target"] == target
    assert kubelet.opens == [
        {"cluster": world.cluster_b.slug, "namespace": staging.k8s_namespace, "pod": POD, "container": "main"}
    ]
    assert [row.payload["environment_id"] for row in opened_audits(world)] == [str(staging.guid)]


@pytest.mark.parametrize("second", [None, "soft_deleted"])
def test_an_open_naming_no_environment_runs_in_the_apps_only_environment(world, kubelet, second):
    if second:
        second_environment(world).soft_delete()
    [ready] = frames_of(relay(world, OPEN))
    assert ready["type"] == "ready" and ready["target"] is None
    assert ready["resumable"] is False and ready["disconnect"] == "END" and ready["inputReplay"] is False
    assert kubelet.opens == [
        {
            "cluster": world.cluster_a.slug,
            "namespace": world.primary.k8s_namespace,
            "pod": POD,
            "container": "main",
        }
    ]
    assert [row.payload["session_id"] for row in opened_audits(world)] == [ready["sessionId"]]


def test_another_organizations_environments_never_count_against_this_app(world, kubelet):
    foreign = ScopeWorld("legacy-exec-2209-foreign")
    RegisteredApp.objects.filter(pk=foreign.medops_app.pk).update(slug=world.medops_app.slug)
    for name in ("production", "staging"):
        AppEnvironment.objects.create(
            registered_app=foreign.medops_app,
            tenant_cluster=make_cluster(foreign, f"legacy-exec-2209-foreign-{name}"),
            name=name,
            k8s_namespace=f"legacy-exec-foreign-{name}-2209",
        )
    assert [frame["type"] for frame in frames_of(relay(world, OPEN))] == ["ready"]
    assert [(row["cluster"], row["namespace"]) for row in kubelet.opens] == [
        (world.cluster_a.slug, world.primary.k8s_namespace)
    ]


def retire_a_namesake(world):
    """A deleted app that answered to this slug, with a live environment
    older than the live app's only one.

    Deleting an app leaves its environment rows live, and a new app can
    claim the slug again, so a lookup by slug alone still reaches them.
    """
    world.primary.soft_delete()
    AppEnvironment.objects.create(
        registered_app=world.platform_app,
        tenant_cluster=world.cluster_b,
        name="staging",
        k8s_namespace="legacy-exec-retired-2209",
    )
    world.platform_app.soft_delete()
    RegisteredApp.all_objects.filter(pk=world.platform_app.pk).update(slug=world.medops_app.slug)
    world.primary = AppEnvironment.objects.create(
        registered_app=world.medops_app,
        tenant_cluster=world.cluster_a,
        name="production",
        k8s_namespace="legacy-exec-production-2209",
    )


def test_a_retired_namesakes_environments_never_count_against_this_app(world, kubelet):
    retire_a_namesake(world)
    assert [frame["type"] for frame in frames_of(relay(world, OPEN))] == ["ready"]
    assert [(row["cluster"], row["namespace"]) for row in kubelet.opens] == [
        (world.cluster_a.slug, world.primary.k8s_namespace)
    ]


@pytest.mark.parametrize("namesake", [False, True])
def test_an_environment_policy_reads_the_environment_the_open_would_run_in(world, kubelet, namesake):
    if namesake:
        retire_a_namesake(world)
    Policy.objects.create(
        organization=world.org,
        name="No production shells",
        slug="legacy-exec-2209-production",
        effect="DENY",
        scope_level="ORG",
        action_pattern=Permission.APP_EXEC_POD.value,
        resource_pattern={"env": ["production"]},
    )
    assert relay(world, OPEN) == [{"type": "websocket.close", "code": 4403}]
    assert kubelet.opens == []
    assert not opened_audits(world).exists()


def test_an_app_without_an_environment_keeps_the_no_runtime_answer(world, kubelet):
    world.primary.soft_delete()
    sent = relay(world, OPEN)
    stderr, exit_frame = frames_of(sent)
    assert stderr["type"] == "stderr" and "no runtime cluster" in stderr["data"]
    assert exit_frame == {"type": "exit", "code": 2}
    assert sent[-1] == {"type": "websocket.close", "code": 1000}
    assert kubelet.opens == []


def test_the_backend_resolves_only_an_apps_sole_environment(world):
    second = second_environment(world)
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
        several = async_to_sync(_resolve_target)(app_slug=world.medops_app.slug, workload_slug=POD)
        second.soft_delete()
        sole = async_to_sync(_resolve_target)(app_slug=world.medops_app.slug, workload_slug=POD)
    assert several is None
    assert sole["cluster"].pk == world.cluster_a.pk
    assert sole["namespace"] == world.primary.k8s_namespace
