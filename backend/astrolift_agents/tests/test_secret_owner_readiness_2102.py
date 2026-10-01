"""Actual PostgreSQL/management-command readiness with compatible runtime behavior."""

import json
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone

from astrolift_agents.models.agent_secret_binding import AgentSecretBindingOverride
from astrolift_agents.services.secret_audit_reporting import metadata_digest
from astrolift_agents.services.secret_owner_cutover import cutover_readiness
from astrolift_agents.services.secret_owner_namespaces import agent_secret_prefix
from astrolift_agents.tests import secret_owner_migration_support as support
from astrolift_dispatch.agent_secrets import resolve_task_secret_manifest, unscoped_secret_refs
from astrolift_identity.models import Organization
from astrolift_services.models import ManagedService

pytestmark = pytest.mark.django_db
world = support.world
no_index = support.no_index


@pytest.fixture
def no_backend(monkeypatch):
    calls = []

    def refuse(*args, **kwargs):
        calls.append(True)
        raise AssertionError("metadata reporting constructed a secret backend")

    monkeypatch.setattr("core.app_deploy.driver_for_capability", refuse)
    monkeypatch.setattr("astrolift_dispatch.agent_secrets.resolve_secrets_backend", refuse)
    return calls


def cli(world, *, require_ready=False):
    out = StringIO()
    call_command(
        "audit_agent_secret_owner_cutover", org=str(world.org.guid), require_ready=require_ready, stdout=out
    )
    return json.loads(out.getvalue())


def test_cli_clean_inventory_is_metadata_only_not_cutover_authority(world, no_backend):
    before = [(str(spec.guid), spec.version, spec.secret_refs) for spec in world.specs]
    report = cli(world, require_ready=True)
    assert report["metadata_ready"] is True
    assert report["runtime_owner_enforcement"] is False
    assert report["secret_store_contacted"] is False
    assert report["owner_counts"] == {"project": 2, "team": 1, "shared": 1}
    assert report["live_specs"] == 4
    assert report["typed_ref_locations"] == 4
    assert report["cluster_guid"] == str(world.cluster.guid)
    assert report["cluster_version"] == world.cluster.version
    assert {row["spec_guid"] for row in report["inventory"]} == {row[0] for row in before}
    assert all(not row["source_revision_verified"] for row in report["inventory"])
    for spec in world.specs:
        spec.refresh_from_db()
    assert [(str(spec.guid), spec.version, spec.secret_refs) for spec in world.specs] == before
    assert no_backend == [] and world.store.calls == []


def test_inventory_includes_shadowed_refs_and_tombstone_identity_without_store(world, no_backend):
    hidden = f"agents/{world.org.guid}/hidden"
    removed_ref = f"agents/{world.org.guid}/removed#password"
    world.own.secret_refs = [{"env_var": "TOKEN", "uri": hidden}]
    world.own.config_repo = "opaque-source-repository"
    world.own.config_branch = "release-branch"
    world.own.config_manifest_path = "agents/private/astrolift.toml"
    world.own.save()
    override = AgentSecretBindingOverride.objects.create(
        environment_spec=world.own, env_var="TOKEN", uri=agent_secret_prefix(world.own) + "override"
    )
    removed = AgentSecretBindingOverride.objects.create(
        environment_spec=world.own, env_var="REMOVED", uri=removed_ref, removed=True
    )
    AgentSecretBindingOverride.objects.create(
        environment_spec=world.own, env_var="DELETED", uri="never-reported", deleted_at=timezone.now()
    )
    out = StringIO()
    with pytest.raises(CommandError, match="owner readiness refused"):
        call_command("audit_agent_secret_owner_cutover", org=world.org.slug, require_ready=True, stdout=out)
    report = json.loads(out.getvalue())
    assert report["typed_ref_locations"] == 6
    assert report["removed_overrides"] == 1
    item = next(row for row in report["inventory"] if row["spec_guid"] == str(world.own.guid))
    assert item["spec_version"] == world.own.version
    assert item["source_configured"] is True
    assert item["source_metadata_sha256"] == metadata_digest(
        {
            "config_repo": world.own.config_repo,
            "config_branch": world.own.config_branch,
            "config_manifest_path": world.own.config_manifest_path,
        }
    )
    by_source = {row["source"]: row for row in item["locations"]}
    assert by_source["manifest"]["ref_sha256"] == metadata_digest(hidden)
    assert by_source["manifest"]["reason"] == "legacy_ref_requires_reviewed_migration"
    assert by_source["override"]["override_guid"] == str(override.guid)
    assert by_source["removed_override"]["override_guid"] == str(removed.guid)
    assert by_source["removed_override"]["override_version"] == removed.version
    assert by_source["removed_override"]["ref_sha256"] == metadata_digest(removed_ref)
    assert by_source["removed_override"]["reason"] == "legacy_ref_requires_reviewed_migration"
    for raw in (hidden, removed_ref, "never-reported", world.own.config_repo, world.own.config_manifest_path):
        assert raw not in out.getvalue()
    assert no_backend == [] and world.store.calls == []


@pytest.mark.parametrize(
    "raw",
    [
        [{"env_var": "TOKEN", "uri": "not-a-safe-reference\nprivate-marker"}],
        [{"env_var": "TOKEN", "uri": "foreign", "value": "must-not-echo"}],
        {"secret": "must-not-echo"},
        ["must-not-echo"],
        [{"env_var": "TOKEN", "uri": "agents/sibling/nope"}, {"env_var": "TOKEN", "uri": "must-not-echo"}],
    ],
)
def test_malformed_or_foreign_metadata_is_opaque_and_never_hidden(world, no_backend, raw):
    world.own.secret_refs = raw
    world.own.save()
    report = cli(world)
    assert not report["metadata_ready"]
    own_findings = [row for row in report["findings"] if row.get("spec_guid") == str(world.own.guid)]
    assert own_findings
    assert all("reason" in row and ("ref_sha256" in row or "metadata_sha256" in row) for row in own_findings)
    output = json.dumps(report)
    assert (
        "must-not-echo" not in output
        and "private-marker" not in output
        and "agents/sibling/nope" not in output
    )
    assert no_backend == [] and world.store.calls == []


def test_invalid_ancestry_and_missing_cluster_still_inventory_every_ref(world, no_backend):
    world.own.project = world.platform_project
    world.own.save()
    world.cluster.lifecycle = "decommissioned"
    world.cluster.save()
    report = cli(world)
    assert report["cluster_guid"] is None and report["cluster_version"] is None
    assert report["live_specs"] == 4 and report["typed_ref_locations"] == 4
    assert report["owner_counts"] == {"project": 1, "team": 1, "shared": 1}
    assert {row["reason"] for row in report["findings"]} >= {
        "managed_cluster_unavailable",
        "stale_or_inconsistent_owner",
    }
    assert not report["metadata_ready"]
    assert no_backend == [] and world.store.calls == []


def test_readiness_is_not_runtime_enforcement_legacy_and_sibling_refs_still_dispatch(world):
    legacy = f"agents/{world.org.guid}/legacy"
    sibling = world.sibling.secret_refs[0]["uri"]
    for uri in (legacy, sibling, agent_secret_prefix(world.own) + "current"):
        world.own.secret_refs = [{"env_var": "TOKEN", "uri": uri}]
        world.own.save()
        world.store.data[uri] = {"value": "static-test-value"}
        assert unscoped_secret_refs(world.own) == {}
        report = cutover_readiness(world.org)
        assert report["metadata_ready"] is (uri.endswith("/current"))
        assert report["runtime_owner_enforcement"] is False
        before = list(world.store.calls)
        result = resolve_task_secret_manifest(
            cluster=world.cluster, spec=world.own, secret_name="test", namespace="test", task_guid="test"
        )
        assert result["stringData"]["TOKEN"] == "static-test-value"
        assert world.store.calls[len(before) :] == [("get", uri)]


def test_org_shared_model_audit_includes_owner_without_echoing_config_ref(world, no_backend):
    bad_ref = "opaque-private-ref\nnever-echo"
    service = ManagedService.objects.create(
        organization=world.org,
        tenant_cluster=world.cluster,
        kind="model_endpoint",
        name="shared-model",
        variant="vllm",
        config={"password_secret_ref": bad_ref},
    )
    another = ManagedService.objects.create(
        organization=Organization.objects.create(name="Other", slug="other-readiness"),
        tenant_cluster=world.cluster,
        kind="model_endpoint",
        name="foreign-model",
        variant="vllm",
        config={"password_secret_ref": bad_ref},
    )
    out = StringIO()
    call_command("audit_agent_secret_namespace", org=str(world.org.guid), stdout=out)
    output = out.getvalue()
    assert str(service.guid) in output and str(another.guid) not in output
    assert metadata_digest(bad_ref) in output and bad_ref not in output and "never-echo" not in output
    assert f"services/{world.org.guid}/{service.guid}/" in output
    assert no_backend == [] and world.store.calls == []


def test_unknown_organization_error_does_not_echo_user_input(no_backend):
    with pytest.raises(CommandError, match="^organization not found$"):
        call_command(
            "audit_agent_secret_owner_cutover", org="opaque-private-ref\nnever-echo", stdout=StringIO()
        )
    assert no_backend == []


@pytest.mark.django_db(transaction=True)
def test_actual_manage_cli_uses_owned_pg_database_and_strict_exit_without_backend(world):
    environment = os.environ.copy()
    environment["POSTGRES_DB"] = connection.settings_dict["NAME"]
    assert connection.vendor == "postgresql"
    assert environment["POSTGRES_DB"].startswith("test_")
    guarded_entry = """
import django, runpy
django.setup()
import core.app_deploy
from astrolift_dispatch import agent_secrets
from astrolift_agents.services import secret_owner_migration
def denied(*args, **kwargs):
    raise AssertionError('readiness constructed a backend')
core.app_deploy.driver_for_capability = denied
agent_secrets.resolve_secrets_backend = denied
secret_owner_migration.resolve_secrets_backend = denied
runpy.run_path('manage.py', run_name='__main__')
"""
    argv = [
        sys.executable,
        "-c",
        guarded_entry,
        "audit_agent_secret_owner_cutover",
        "--org",
        str(world.org.guid),
        "--require-ready",
    ]
    backend = Path(__file__).resolve().parents[2]
    clean = subprocess.run(argv, cwd=backend, env=environment, capture_output=True, text=True, timeout=40)
    assert clean.returncode == 0, "actual readiness CLI failed"

    def report_from_output(output):
        reports = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
        return next(row for row in reports if row.get("schema") == "astrolift.agent-secret-owner-cutover/v1")

    report = report_from_output(clean.stdout)
    assert report["metadata_ready"] is True
    assert report["cluster_guid"] == str(world.cluster.guid)
    world.own.secret_refs = [{"env_var": "TOKEN", "uri": f"agents/{world.org.guid}/legacy"}]
    world.own.save()
    refused = subprocess.run(argv, cwd=backend, env=environment, capture_output=True, text=True, timeout=40)
    assert refused.returncode == 1, "strict readiness CLI did not refuse"
    failed_report = report_from_output(refused.stdout)
    assert failed_report["metadata_ready"] is False
    assert failed_report["typed_ref_locations"] == 4
    assert failed_report["findings"][0]["spec_guid"] == str(world.own.guid)
    assert "owner readiness refused" in refused.stderr
    assert world.store.calls == []
