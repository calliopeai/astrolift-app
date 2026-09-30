"""Reviewed secret-owner plans copy complete payloads before PostgreSQL ref commits."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_agents.services.secret_owner_namespaces import agent_secret_prefix
from astrolift_agents.tests import secret_owner_migration_support as owner_support
from core.tests.utils.scope_world import make_user

pytestmark = pytest.mark.django_db
world = owner_support.world
no_index = owner_support.no_index


# Store boundary is faked; ownership, plans, row locks and audit commits use PostgreSQL.


def legacy(world, *, spec=None, path="legacy", fragment=""):
    spec = spec or world.own
    source = f"agents/{world.org.guid}/{path}"
    spec.secret_refs = [{"env_var": "TOKEN", "uri": source + fragment}]
    spec.save()
    world.store.data[source] = {
        "password": "private-password",
        "username": "service-user",
        "value": "private-value",
    }
    return source, agent_secret_prefix(spec) + path


def migration(world):
    from astrolift_agents.services import secret_owner_migration

    world.operator = make_user("2102-operator")
    world.operator.is_superuser = True
    world.operator.save()
    return secret_owner_migration


def apply(world, service, plan):
    return service.apply_plan(world.org, plan, operator=world.operator, writers_paused=True)


def test_migration_preview_reads_metadata_only_and_full_payload_copy_retains_source(world):
    import json

    from astrolift_operations.models import AuditEvent

    service = migration(world)
    source, destination = legacy(world, fragment="#password")
    plan = service.build_plan(world.org)
    assert world.store.calls == []
    assert "private-password" not in json.dumps(plan)
    assert apply(world, service, plan) == 1
    assert world.store.data[destination] == world.store.data[source]
    assert set(world.store.data[destination]) == {"password", "username", "value"}
    world.own.refresh_from_db()
    assert world.own.secret_refs[0]["uri"] == destination + "#password"
    assert all(action != "delete" for action, _ in world.store.calls)
    event = AuditEvent.objects.get(action="agents.secret.owner_migrate")
    assert "private-password" not in json.dumps(event.data)
    before = list(world.store.calls)
    assert apply(world, service, plan) == 1
    assert world.store.calls == before
    assert AuditEvent.objects.filter(action="agents.secret.owner_migrate").count() == 1


def test_maintenance_runtime_resolves_legacy_and_migrated_refs_without_cutover(world):
    from astrolift_dispatch.agent_secrets import resolve_task_secret_manifest, unscoped_secret_refs

    service = migration(world)
    legacy(world, fragment="#password")
    assert unscoped_secret_refs(world.own) == {}
    before = resolve_task_secret_manifest(
        cluster=world.cluster, spec=world.own, secret_name="task", namespace="ns", task_guid="t"
    )
    plan = service.build_plan(world.org)
    apply(world, service, plan)
    world.own.refresh_from_db()
    assert unscoped_secret_refs(world.own) == {}
    after = resolve_task_secret_manifest(
        cluster=world.cluster, spec=world.own, secret_name="task", namespace="ns", task_guid="t"
    )
    assert after == before
    assert service.build_plan(world.org)["specs"] == []


def test_migration_copies_override_locations_but_not_removed_tombstones(world):
    from astrolift_agents.models.agent_secret_binding import AgentSecretBindingOverride

    service = migration(world)
    source, destination = legacy(world)
    override_source = f"agents/{world.org.guid}/override"
    removed_source = f"agents/{world.org.guid}/removed"
    world.store.data[override_source] = {"TOKEN": "override-private", "other": "also-private"}
    active = AgentSecretBindingOverride.objects.create(
        environment_spec=world.own, env_var="OVERRIDE", uri=override_source + "#TOKEN"
    )
    removed = AgentSecretBindingOverride.objects.create(
        environment_spec=world.own, env_var="REMOVED", uri=removed_source, removed=True
    )
    active_version, removed_version = active.version, removed.version
    plan = service.build_plan(world.org)
    assert apply(world, service, plan) == 1
    active.refresh_from_db()
    removed.refresh_from_db()
    assert active.uri == agent_secret_prefix(world.own) + "override#TOKEN"
    assert removed.uri == agent_secret_prefix(world.own) + "removed"
    assert removed.removed
    assert active.version == active_version + 1
    assert removed.version == removed_version + 1
    assert active.updated_by_id == world.operator.pk
    assert removed.updated_by_id == world.operator.pk
    assert all(key != removed_source for _, key in world.store.calls)
    assert destination in world.store.data and source in world.store.data


@pytest.mark.parametrize(
    "kind", ["destination_conflict", "missing_source", "provider_failure", "failed_verification"]
)
def test_failed_copy_never_switches_spec_metadata_or_deletes_source(world, monkeypatch, kind):
    service = migration(world)
    source, destination = legacy(world)
    plan = service.build_plan(world.org)
    before = list(world.own.secret_refs)
    if kind == "destination_conflict":
        world.store.data[destination] = {"value": "existing-different"}
    elif kind == "missing_source":
        world.store.data.pop(source)
    elif kind == "provider_failure":

        def failed(*args):
            raise RuntimeError("provider error private-password")

        monkeypatch.setattr(world.store, "upsert", failed)
    else:
        monkeypatch.setattr(world.store, "upsert", lambda *args: None)
    with pytest.raises(service.SecretOwnerMigrationError) as caught:
        apply(world, service, plan)
    assert "private-password" not in str(caught.value)
    world.own.refresh_from_db()
    assert world.own.secret_refs == before
    assert all(action != "delete" for action, _ in world.store.calls)


def test_retry_after_copy_and_process_failure_accepts_identical_existing_payload(world, monkeypatch):
    service = migration(world)
    source, destination = legacy(world)
    plan = service.build_plan(world.org)
    original = world.store.upsert

    def copy_then_fail(key, value):
        original(key, value)
        raise RuntimeError("process interrupted after provider accepted copy")

    monkeypatch.setattr(world.store, "upsert", copy_then_fail)
    with pytest.raises(service.SecretOwnerMigrationError):
        apply(world, service, plan)
    world.own.refresh_from_db()
    assert world.own.secret_refs[0]["uri"] == source
    assert world.store.data[destination] == world.store.data[source]
    monkeypatch.setattr(world.store, "upsert", original)
    assert apply(world, service, plan) == 1
    assert sum(action == "upsert" for action, _ in world.store.calls) == 1


@pytest.mark.parametrize("tamper", ["owner", "refs", "cluster", "plan_mapping", "duplicate", "foreign_org"])
def test_reviewed_plan_refuses_changed_metadata_before_opening_store(world, tamper):
    service = migration(world)
    legacy(world)
    plan = service.build_plan(world.org)
    if tamper == "owner":
        world.own.project = world.platform_project
        world.own.team = world.platform
        world.own.save()
    elif tamper == "refs":
        world.own.secret_refs = [{"env_var": "TOKEN", "uri": f"agents/{world.org.guid}/different"}]
        world.own.save()
    elif tamper == "cluster":
        world.cluster.region = "us-east-1"
        world.cluster.save()
    elif tamper == "plan_mapping":
        plan["specs"][0]["copies"][0]["source"] = world.sibling.secret_refs[0]["uri"]
    elif tamper == "duplicate":
        plan["specs"].append(plan["specs"][0])
    else:
        plan["organization_guid"] = "another-org"
    with pytest.raises(service.SecretOwnerMigrationError):
        apply(world, service, plan)
    assert world.store.calls == []


@pytest.mark.parametrize("foreign", ["sibling", "team_spec", "shared"])
def test_migration_never_adopts_an_existing_other_owner_namespace(world, foreign):
    service = migration(world)
    world.own.secret_refs = getattr(world, foreign).secret_refs
    world.own.save()
    with pytest.raises(service.SecretOwnerMigrationError):
        service.build_plan(world.org)
    assert world.store.calls == []


@pytest.mark.parametrize("operator,paused", [(False, True), (True, False)])
def test_migration_requires_operator_and_paused_writers_before_store(world, operator, paused):
    service = migration(world)
    legacy(world)
    plan = service.build_plan(world.org)
    world.operator.is_superuser = operator
    with pytest.raises(service.SecretOwnerMigrationError):
        service.apply_plan(world.org, plan, operator=world.operator, writers_paused=paused)
    assert world.store.calls == []


@pytest.mark.parametrize("stale", ["project", "project_team", "team_mismatch", "foreign_project"])
def test_preview_refuses_stale_or_incoherent_owners_without_store_access(world, stale):
    from astrolift_dispatch.agent_secrets import SecretRefNamespaceError

    service = migration(world)
    legacy(world)
    if stale == "project":
        world.medops_project.soft_delete()
    elif stale == "project_team":
        world.medops.soft_delete()
    elif stale == "team_mismatch":
        world.own.team = world.platform
        world.own.save()
    else:
        foreign = owner_support.ScopeWorld("foreign-maintenance-2102")
        world.own.project = foreign.medops_project
        world.own.team = foreign.medops
        world.own.save()
    with pytest.raises(SecretRefNamespaceError):
        service.build_plan(world.org)
    assert world.store.calls == []


def test_command_private_metadata_preview_requires_exact_reviewed_digest(world, tmp_path):
    import io
    import json
    import stat

    service = migration(world)
    legacy(world)
    path = tmp_path / "plan.json"
    output = io.StringIO()
    call_command("migrate_agent_secret_owners", org=world.org.slug, plan_file=str(path), stdout=output)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "private-password" not in path.read_text() + output.getvalue()
    assert world.store.calls == []
    plan = json.loads(path.read_text())
    with pytest.raises(CommandError):
        call_command(
            "migrate_agent_secret_owners",
            org=world.org.slug,
            plan_file=str(path),
            apply=True,
            confirm_plan="wrong",
            operator=str(world.operator.pk),
            writers_paused=True,
        )
    assert world.store.calls == []
    call_command(
        "migrate_agent_secret_owners",
        org=world.org.slug,
        plan_file=str(path),
        apply=True,
        confirm_plan=service.plan_digest(plan),
        operator=str(world.operator.pk),
        writers_paused=True,
        stdout=output,
    )
    assert "Completed 1" in output.getvalue()


@pytest.mark.parametrize(
    "spelling",
    [
        "Projects/{project}/credential",
        "projects-{project}-credential",
        "SHARED/credential",
        "teams-{team}-credential",
    ],
)
def test_migration_refuses_provider_normalized_aliases_of_other_owner_namespaces(world, spelling):
    service = migration(world)
    world.own.secret_refs = [
        {
            "env_var": "TOKEN",
            "uri": f"agents/{world.org.guid}/"
            + spelling.format(project=world.platform_project.guid, team=world.platform.guid),
        }
    ]
    world.own.save()
    with pytest.raises(service.SecretOwnerMigrationError):
        service.build_plan(world.org)
    assert world.store.calls == []


def test_migration_commits_completed_specs_and_resumes_after_later_failure(world, monkeypatch):
    service = migration(world)
    for spec in (world.own, world.sibling):
        legacy(world, spec=spec, path="common")
    plan = service.build_plan(world.org)
    assert len(plan["specs"]) == 2
    assert len(plan["sources_used_by_multiple_owners"]) == 1
    first, second = plan["specs"]
    blocked = second["copies"][0]["destination"]
    original = world.store.upsert

    def fail_second(key, payload):
        if key == blocked:
            raise RuntimeError("copy interrupted")
        original(key, payload)

    monkeypatch.setattr(world.store, "upsert", fail_second)
    with pytest.raises(service.SecretOwnerMigrationError):
        apply(world, service, plan)
    assert AgentEnvironmentSpec.objects.get(guid=first["spec_guid"]).secret_refs == first["after"]["refs"]
    assert AgentEnvironmentSpec.objects.get(guid=second["spec_guid"]).secret_refs == second["before"]["refs"]
    monkeypatch.setattr(world.store, "upsert", original)
    assert apply(world, service, plan) == 2
