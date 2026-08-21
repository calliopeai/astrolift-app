"""Tests for the ``--command`` arg on ``seed_flow_test_agent``.

The seeded primary Container's ``command`` decides what the spawned
flow-test Job runs. The default exits 0 cleanly (so the Job completes
without an API key or Brief); a custom ``--command`` overrides it; ``[]``
falls back to the image entrypoint; invalid JSON is a usage error.
"""

from __future__ import annotations

import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from astrolift_agents.management.commands.seed_flow_test_agent import _DEFAULT_COMMAND
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization
from astrolift_registry.models import Container

pytestmark = pytest.mark.django_db

_IMAGE = "123456789012.dkr.ecr.us-west-2.amazonaws.com/astrolift/agent-claude:latest"


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture
def org_and_cluster():
    org = Organization.objects.create(name="Seed Org", slug="seed-cmd-org")
    # Seeded directly; the plugin row is scaffolding for this test.
    plugin = ProviderPlugin(
        name="K8s",
        slug="k8s-seed",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    plugin = ProviderPlugin.objects.get(slug="k8s-seed")
    cluster = TenantCluster.objects.create(
        organization=org,
        name="seed-cluster",
        slug="seed-cluster",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    return org, cluster


def _seed(*extra):
    call_command(
        "seed_flow_test_agent",
        "--org-slug",
        "seed-cmd-org",
        "--cluster-slug",
        "seed-cluster",
        "--image",
        _IMAGE,
        *extra,
    )


def test_default_command_exits_zero_cleanly(org_and_cluster):
    """No --command -> the default shell command that prints a marker and
    exits 0, so the flow-test Job completes without an API key/Brief."""
    _seed()
    container = Container.objects.get(workload__slug="flow-test-agent", name="agent")
    assert container.command == _DEFAULT_COMMAND
    # The default must be a clean exit-0 shell invocation on /bin/sh.
    assert container.command[0] == "/bin/sh"
    assert container.command[1] == "-c"
    assert "exit 0" in container.command[2]


def test_custom_command_overrides_default(org_and_cluster):
    custom = ["python", "-m", "agent", "--once"]
    _seed("--command", json.dumps(custom))
    container = Container.objects.get(workload__slug="flow-test-agent", name="agent")
    assert container.command == custom


def test_empty_command_falls_back_to_image_entrypoint(org_and_cluster):
    """'[]' stores an empty list — the spawner then omits the Job
    container's command key and the image ENTRYPOINT runs."""
    _seed("--command", "[]")
    container = Container.objects.get(workload__slug="flow-test-agent", name="agent")
    assert container.command == []


def test_invalid_json_command_is_usage_error(org_and_cluster):
    with pytest.raises(CommandError, match="not valid JSON"):
        _seed("--command", "not-json")


def test_non_list_command_is_usage_error(org_and_cluster):
    with pytest.raises(CommandError, match="must be a JSON list of strings"):
        _seed("--command", json.dumps({"cmd": "x"}))


def test_non_string_elements_command_is_usage_error(org_and_cluster):
    with pytest.raises(CommandError, match="must be a JSON list of strings"):
        _seed("--command", json.dumps(["/bin/sh", 7]))


def test_idempotent_on_repeat_updates_command_in_place(org_and_cluster):
    """Re-running with a different --command updates the single existing
    Container row rather than piling up duplicates."""
    _seed()  # default
    _seed("--command", json.dumps(["echo", "second"]))
    rows = Container.objects.filter(workload__slug="flow-test-agent", name="agent")
    assert rows.count() == 1
    assert rows.first().command == ["echo", "second"]
