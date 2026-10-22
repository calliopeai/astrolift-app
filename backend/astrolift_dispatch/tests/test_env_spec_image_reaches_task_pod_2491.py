"""An environment spec's pinned image reaches the agent task pod (#2491).

``docs/operators/agent-toolchain-images.md`` tells builders to bake their
toolchains into an image and pin it on an environment spec, rather than
install them on every task. These tests pin that path against a real
database: the spec is written through the spec mutations, the task is
dispatched through ``runAstroliftAgent``, and the dispatch activity's spawn
step renders the Job with the real ``K8sJobSpawner``. Only the cluster
driver is a stand-in; it records the manifests the platform would apply.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask
from astrolift_agents.schema.mutations import (
    AgentsMutation,
    CreateAgentEnvironmentSpecInput,
    RunAstroliftAgentInput,
    UpdateAgentEnvironmentSpecInput,
)
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Team
from astrolift_registry.models import Container, RegisteredApp, Workload
from astrolift_workflows.activities import agent_stage
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db

# Built from a pattern so no run of digits reads as a cloud account id.
DIGEST = "sha256:" + ("ab12cd34ef" * 7)[:64]
AGENT_IMAGE = "docker.io/library/busybox:1.36"


@pytest.fixture
def info():
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


@pytest.fixture
def org():
    return Organization.objects.create(name="Toolchain Org", slug="toolchain-org")


@pytest.fixture
def agent(org):
    team = Team.objects.create(organization=org, name="Factory", slug="factory")
    app = RegisteredApp.objects.create(
        organization=org, team=team, name="Factory", slug="factory", provisioning_status="ready"
    )
    workload = Workload.objects.create(
        registered_app=app, name="Builder", slug="builder", kind=Workload.Kind.AGENT
    )
    # The agent's own image, which a pinned spec image must win over.
    Container.objects.create(workload=workload, name="agent", is_primary=True, image_ref=AGENT_IMAGE)
    return workload


class _Applied:
    ok = True

    def summary(self):  # pragma: no cover - read only when an apply fails
        return "applied"


class _RecordingCluster:
    """The org's cluster: no Job exists yet, and every apply is recorded."""

    def __init__(self):
        self.applied: list[dict] = []

    def get_manifest(self, cluster_slug, namespace, kind, name):
        return None

    def ensure_namespace(self, cluster_slug, name, labels, annotations):
        return None

    def apply_manifests(self, cluster_slug, namespace, manifests):
        self.applied.extend(manifests)
        return _Applied()


@pytest.fixture
def cluster(org, monkeypatch):
    import core.cluster_management as cluster_management

    # Seeded directly; the plugin row is scaffolding for the cluster row.
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Agents K8s",
                slug="agents-k8s-2491",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    TenantCluster.objects.create(
        organization=org,
        name="agents",
        slug="agents",
        provider_plugin=ProviderPlugin.objects.get(slug="agents-k8s-2491"),
        provider_config={},
        endpoint="https://agents.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    recorder = _RecordingCluster()
    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda _cluster: recorder)
    monkeypatch.setattr(
        cluster_management, "_context_for_cluster", lambda _cluster: SimpleNamespace(slug="agents")
    )
    return recorder


@pytest.fixture
def enqueued(monkeypatch):
    """Record the dispatch workflow start; no Temporal server is needed."""
    starts: list[str] = []

    def _start(name, args, *, workflow_id, task_queue=None):
        from astrolift_workflows.client import WorkflowHandle

        starts.append(workflow_id)
        return WorkflowHandle(workflow_id=workflow_id, run_id=f"run-{len(starts)}", enqueued=True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _start)
    return starts


def _create_spec(info, org, **fields) -> AgentEnvironmentSpec:
    with _tenant_ctx(TenantContext(organization_id=org.id)):
        result = AgentsMutation().create_agent_environment_spec(
            info,
            input=CreateAgentEnvironmentSpecInput(
                name="Rust toolchain", slug="rust-toolchain", agent_type="claude", **fields
            ),
            org_id=str(org.guid),
        )
    assert result.ok is True, result.errors
    return AgentEnvironmentSpec.objects.get(organization=org, slug="rust-toolchain")


def _dispatch(info, org, agent, spec) -> AgentTask:
    with _tenant_ctx(TenantContext(organization_id=org.id)):
        result = AgentsMutation().run_astrolift_agent(
            info,
            input=RunAstroliftAgentInput(agent_slug=agent.slug, environment_spec_id=GUID(str(spec.guid))),
        )
    assert result.ok is True, result.errors
    return AgentTask.objects.get(guid=str(result.data.id))


def _spawned_container(cluster, task) -> dict:
    """Run the dispatch activity's spawn step and return the Job's agent container."""
    spawn = agent_stage._spawn_agent_task_sync(task.pk)
    assert spawn["ok"] is True, spawn["error"]
    jobs = [manifest for manifest in cluster.applied if manifest["kind"] == "Job"]
    assert len(jobs) == 1
    assert jobs[0]["metadata"]["labels"]["astrolift.dev/task-id"] == str(task.guid)
    return jobs[0]["spec"]["template"]["spec"]["containers"][0]


@pytest.mark.parametrize(
    ("image_tag", "pull_policy"),
    [
        # A digest on a private registry with a port: the ":" of the port
        # and the ":" inside the digest both survive.
        (f"registry.example.internal:5000/factory/rust-wasm@{DIGEST}", "IfNotPresent"),
        # A version tag: a node that already holds the image starts from it.
        ("ghcr.io/example/agent-toolchains:rust-1.90-wasm32-wrangler-4", "IfNotPresent"),
        # A mutable tag is pulled again for every task.
        ("ghcr.io/example/agent-toolchains:latest", "Always"),
    ],
    ids=["digest-private-registry", "version-tag", "latest"],
)
def test_pinned_spec_image_reaches_the_task_pod(
    permission_resolver, info, org, agent, cluster, enqueued, image_tag, pull_policy
):
    """The spec also names the catalog runtime its image derives from, and
    the agent has an image of its own; the pinned image wins over both."""
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_CREATE)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    spec = _create_spec(info, org, image_tag=image_tag, runtime="claude-code")
    task = _dispatch(info, org, agent, spec)

    container = _spawned_container(cluster, task)

    assert container["image"] == image_tag
    assert container["imagePullPolicy"] == pull_policy


def test_an_image_only_update_reaches_a_task_queued_before_it(
    permission_resolver, info, org, agent, cluster, enqueued
):
    """The spawn reads the spec when it makes the Job, so a toolchain rolled
    by updating the image alone reaches tasks that have not started yet, and
    that update leaves the rest of the spec as it was."""
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_CREATE)
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_UPDATE)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    old = "ghcr.io/example/agent-toolchains:rust-1.89"
    new = "ghcr.io/example/agent-toolchains:rust-1.90"
    spec = _create_spec(
        info, org, image_tag=old, runtime="claude-code", env_vars={"WRANGLER_SEND_METRICS": "false"}
    )
    task = _dispatch(info, org, agent, spec)

    with _tenant_ctx(TenantContext(organization_id=org.id)):
        result = AgentsMutation().update_agent_environment_spec(
            info, slug=spec.slug, input=UpdateAgentEnvironmentSpecInput(image_tag=new)
        )
    assert result.ok is True, result.errors

    assert _spawned_container(cluster, task)["image"] == new
    spec.refresh_from_db()
    assert (spec.runtime, spec.env_vars) == ("claude-code", {"WRANGLER_SEND_METRICS": "false"})


@pytest.mark.parametrize(
    ("image_tag", "pod_image"),
    [
        # A tag: the same tag in the -vnc repository.
        ("ghcr.io/example/agent-toolchains:rust-1.90", "ghcr.io/example/agent-toolchains-vnc:rust-1.90"),
        # A repository already ending in -vnc is used as it is.
        (f"ghcr.io/example/agent-toolchains-vnc@{DIGEST}", f"ghcr.io/example/agent-toolchains-vnc@{DIGEST}"),
        # A digest elsewhere is looked up in the -vnc repository unchanged,
        # so that repository has to hold an image with this very digest.
        (f"ghcr.io/example/agent-toolchains@{DIGEST}", f"ghcr.io/example/agent-toolchains-vnc@{DIGEST}"),
    ],
    ids=["tag", "vnc-repository-digest", "digest"],
)
def test_a_vnc_spec_runs_the_vnc_repository_of_its_pinned_image(
    permission_resolver, info, org, agent, cluster, enqueued, image_tag, pod_image
):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_CREATE)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    spec = _create_spec(info, org, image_tag=image_tag, runtime="claude-code", vnc_enabled=True)
    task = _dispatch(info, org, agent, spec)
    assert task.vnc_enabled is True

    container = _spawned_container(cluster, task)

    assert container["image"] == pod_image
    assert {"containerPort": 5900} in container["ports"]
