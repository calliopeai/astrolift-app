"""Real durable attachment state, with only the external cluster driver faked."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_agents.services import agent_host_terminal as terminal
from astrolift_agents.services.agent_host import AgentHost, HostError
from astrolift_agents.services.agent_host_projection import ROOT
from astrolift_agents.tests import test_agent_box as box_tests
from astrolift_operations.models import ZentinelleConnection
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import bind_role, make_user

pytestmark = pytest.mark.django_db(transaction=True)
org = box_tests.org
cluster = box_tests.cluster


@pytest.fixture
def attachment(org, cluster, monkeypatch):
    from astrolift_clusters.models import ProviderPlugin, TenantCluster

    plugin, _ = ProviderPlugin.objects.get_or_create(slug="host-k8s", defaults={"name": "Host K8s"})
    TenantCluster.objects.create(
        organization=org,
        provider_plugin=plugin,
        name="Host cluster",
        slug="host-cluster",
        lifecycle="managed",
        auth_method="kubeconfig",
    )
    monkeypatch.setattr(terminal, "_driver_for_cluster", lambda cluster_row: cluster.driver)
    monkeypatch.setattr(
        terminal, "_context_for_cluster", lambda cluster_row: SimpleNamespace(slug="host-cluster")
    )
    user = make_user("terminal")
    bind_role(
        user,
        permissions=[Permission.AGENT_BOX_ATTACH],
        kind="ORG",
        scope_id=org.pk,
        slug="terminal-controller",
    )
    box = box_tests._box(org, status="running", namespace="agents", external_id="job-box", pod_name="pod-box")
    connection = ZentinelleConnection.objects.create(
        organization=org, base_url="https://governance.example", tenant_ids=["tenant-a"]
    )
    box.model_gateway_connection = connection
    box.model_gateway_agent_id = "agent-box-1"
    box.model_gateway_expires_at = timezone.now() + timedelta(minutes=5)
    box.save()
    from astrolift_identity.models import OrganizationModule

    OrganizationModule.objects.create(organization=org, key="agent_live_attach", enabled=True)
    from astrolift_dispatch.agent_network_fence import render_agent_fence

    manifests = {
        ("Pod", "pod-box"): {
            "metadata": {"labels": {"astrolift.dev/workload-kind": "agent-box"}},
            "spec": {"runtimeClassName": "gvisor"},
            "status": {"phase": "Running"},
        },
        ("RuntimeClass", "gvisor"): {"handler": "runsc"},
        ("NetworkPolicy", "astrolift-agent-fence"): render_agent_fence(
            namespace="agents", zentinelle_gateway=True
        ),
        ("Deployment", "zentinelle-gateway"): {"status": {"availableReplicas": 2}},
    }
    monkeypatch.setattr(
        cluster.driver, "get_manifest", lambda ctx, ns, kind, name: manifests.get((kind, name)), raising=False
    )
    ctx = TenantContext(organization_id=org.pk, actor_user_id=user.pk)
    h = AgentHost(user, ctx)
    with tenant_context(ctx):
        h.command(
            "initialize",
            {
                "channel": ROOT,
                "clientId": "client-one",
                "protocolVersions": ["1.0.0"],
                "initialSubscriptions": [ROOT],
            },
        )
    return SimpleNamespace(host=h, box=box, tenant=ctx, manifests=manifests)


@pytest.mark.parametrize(
    "removed,reason",
    [("RuntimeClass", "gVisor"), ("NetworkPolicy", "network fence"), ("Deployment", "ready model gateway")],
)
def test_missing_live_controls_refuse_attach_and_name_the_fallback(attachment, removed, reason):
    a = attachment
    a.manifests.pop(next(key for key in a.manifests if key[0] == removed))
    with tenant_context(a.tenant):
        uri = terminal.channel(a.box, a.host.user, a.host.client_id)
        with pytest.raises(HostError) as failure:
            a.host.command("subscribe", {"channel": uri})
    assert failure.value.data["ahp_available"] is False
    assert failure.value.data["fallback"] == "agent_task.watch"
    assert reason in failure.value.data["reason"]


def test_snapshots_output_replay_resize_and_detach_are_durable(attachment):
    a = attachment
    with tenant_context(a.tenant):
        uri = terminal.channel(a.box, a.host.user, a.host.client_id)
        a.host.command("subscribe", {"channel": uri})
        owner = uuid4()
        terminal.acquire(a.host, uri, owner)
        output = terminal.emit(a.host, uri, owner, {"type": "terminal/data", "data": "Hello\r\n"})
        assert a.host.poll() == [output]
        assert terminal.snapshot(a.host, uri)["state"]["content"] == [
            {"type": "unclassified", "value": "Hello\r\n"}
        ]
        params = {
            "channel": uri,
            "clientSeq": 1,
            "action": {"type": "terminal/resized", "rows": 40, "cols": 120},
        }
        row, pending, fresh = terminal.reserve_input(a.host, params)
        assert fresh and pending["rejectionReason"]
        accepted = terminal.finish_input(a.host, row, True)
        assert "rejectionReason" not in accepted
        assert terminal.reserve_input(a.host, params) == (row, accepted, False)
        assert terminal.snapshot(a.host, uri)["state"]["cols"] == 120
        terminal.release(a.host, owner)
        replacement = uuid4()
        terminal.acquire(a.host, uri, replacement)
        terminal.release(a.host, replacement)
    a.box.refresh_from_db()
    assert a.box.status == "running"


def test_concurrent_clients_get_separate_views_and_cannot_claim_each_other(attachment):
    a = attachment
    with tenant_context(a.tenant):
        one = terminal.channel(a.box, a.host.user, a.host.client_id)
        a.host.command("subscribe", {"channel": one})
        owner = uuid4()
        terminal.acquire(a.host, one, owner)
        with pytest.raises(HostError, match="already has"):
            terminal.acquire(a.host, one, uuid4())
        other = AgentHost(a.host.user, a.tenant)
        other.command(
            "initialize", {"channel": ROOT, "clientId": "client-two", "protocolVersions": ["1.0.0"]}
        )
        two = terminal.channel(a.box, other.user, other.client_id)
        assert one != two
        with pytest.raises(HostError, match="permission denied"):
            other.command("subscribe", {"channel": one})
        other.command("subscribe", {"channel": two})
        terminal.acquire(other, two, uuid4())
        terminal.emit(a.host, one, owner, {"type": "terminal/data", "data": "One view"})
        assert not other.poll()


@pytest.mark.parametrize("tamper", ["unrestricted-egress", "pod-label", "selector"])
def test_a_named_policy_cannot_stand_in_for_an_effective_fence(attachment, tamper):
    a = attachment
    if tamper == "unrestricted-egress":
        a.manifests[("NetworkPolicy", "astrolift-agent-fence")]["spec"]["egress"] = [{}]
    elif tamper == "pod-label":
        a.manifests[("Pod", "pod-box")]["metadata"]["labels"] = {}
    else:
        a.manifests[("NetworkPolicy", "astrolift-agent-fence")]["spec"]["podSelector"]["matchLabels"] = {
            "other": "label"
        }
    with tenant_context(a.tenant):
        with pytest.raises(HostError) as failure:
            terminal.snapshot(a.host, terminal.channel(a.box, a.host.user, a.host.client_id))
    assert "agent network fence" in failure.value.data["reason"]
