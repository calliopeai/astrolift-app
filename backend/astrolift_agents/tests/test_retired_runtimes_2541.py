"""Retired runtimes cannot start new work; their history and cancellation remain usable."""

from types import SimpleNamespace

import pytest

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask, Brief
from astrolift_agents.schema.mutations import (
    AgentsMutation,
    CreateAgentEnvironmentSpecInput,
    UpdateAgentEnvironmentSpecInput,
)
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.services.agent_package import build_agent_package
from astrolift_agents.services.imported_agent_registration import (
    ImportedAgentRegistrationError,
    persist_imported_agent_package,
)
from astrolift_agents.services.task_preparation import AgentTaskPreparationError, prepare_agent_task
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import Container, RegisteredApp, Workload
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    org = Organization.objects.create(name="Retirement", slug="retirement2541")
    team = Team.objects.create(organization=org, name="Team", slug="team2541")
    project = Project.objects.create(organization=org, team=team, name="Project", slug="project2541")
    app = RegisteredApp.objects.create(
        organization=org, team=team, project=project, name="Agents", slug="agents2541"
    )
    workload = Workload.objects.create(
        registered_app=app, name="Agent", slug="agent2541", kind=Workload.Kind.AGENT.value
    )
    Container.objects.create(
        workload=workload, name="agent", is_primary=True, image_ref="example/active:latest"
    )
    info = SimpleNamespace(
        context=SimpleNamespace(
            user=None, request=SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
        )
    )
    return SimpleNamespace(org=org, project=project, workload=workload, info=info)


def create(world, **fields):
    with tenant_context(TenantContext(organization_id=world.org.pk)):
        return AgentsMutation().create_agent_environment_spec(
            world.info,
            org_id=str(world.org.guid),
            input=CreateAgentEnvironmentSpecInput(name="Spec", slug="spec", agent_type="claude", **fields),
        )


def update(world, **fields):
    with tenant_context(TenantContext(organization_id=world.org.pk)):
        return AgentsMutation().update_agent_environment_spec(
            world.info, slug="spec", input=UpdateAgentEnvironmentSpecInput(**fields)
        )


@pytest.mark.parametrize(
    "fields,field",
    [
        ({"runtime": "aider"}, "runtime"),
        ({"runtime": "openhands", "image_tag": "example/active:pin"}, "runtime"),
        ({"image_tag": "mirror/astrolift-agent-aider-vnc:old"}, "imageTag"),
        ({"image_tag": "mirror/astrolift-agent-openhands@sha256:" + "a" * 64}, "imageTag"),
    ],
)
def test_create_refuses_retired_choices_without_writing_rows(world, permission_resolver, fields, field):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_CREATE)
    result = create(world, **fields)
    assert not result.ok
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == field
    assert not AgentEnvironmentSpec.objects.filter(organization=world.org).exists()


def test_permission_denial_precedes_retirement_validation(world, permission_resolver):
    result = create(world, runtime="aider")
    assert not result.ok
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value


def test_updates_preserve_existing_rows_and_allow_explicit_migration(world, permission_resolver):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_UPDATE)
    spec = AgentEnvironmentSpec.objects.create(
        organization=world.org, name="Spec", slug="spec", agent_type="claude", runtime="aider"
    )
    refused = update(world, name="Renamed")
    assert not refused.ok
    spec.refresh_from_db()
    assert spec.name == "Spec" and spec.runtime == "aider"
    migrated = update(world, runtime="calliope-cli")
    assert migrated.ok, migrated.errors
    spec.refresh_from_db()
    assert spec.runtime == "calliope-cli"
    refused = update(world, image_tag="mirror/astrolift-agent-openhands-vnc:old")
    assert not refused.ok
    spec.refresh_from_db()
    assert spec.image_tag == ""


@pytest.mark.parametrize("runtime", ["aider", "openhands"])
def test_retired_history_remains_readable_and_queued_work_cancellable(world, permission_resolver, runtime):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_READ)
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    spec = AgentEnvironmentSpec.objects.create(
        organization=world.org, name="Spec", slug="spec", agent_type="claude", runtime=runtime
    )
    task = AgentTask.objects.create(
        organization=world.org,
        agent_definition=world.workload,
        environment_spec=spec,
        status=AgentTask.Status.QUEUED,
    )
    with tenant_context(TenantContext(organization_id=world.org.pk)):
        history = AgentsQuery().agent_environment_spec(world.info, slug="spec")
        assert history.runtime == runtime
        result = AgentsMutation().cancel_task(world.info, id=str(task.guid))
    assert result.ok, result.errors
    task.refresh_from_db()
    assert task.status == AgentTask.Status.CANCELLED
    assert AgentEnvironmentSpec.objects.filter(pk=spec.pk, deleted_at__isnull=True).exists()


@pytest.mark.parametrize(
    "image", ["mirror/astrolift-agent-aider:old", "mirror/astrolift-agent-openhands-vnc:old"]
)
def test_workload_fallback_is_refused_before_a_brief_is_created(world, image):
    world.workload.containers.update(image_ref=image)
    task = AgentTask.objects.create(
        organization=world.org, agent_definition=world.workload, status=AgentTask.Status.DRAFT
    )
    with pytest.raises(AgentTaskPreparationError, match="retired"):
        prepare_agent_task(task)
    task.refresh_from_db()
    assert task.status == AgentTask.Status.DRAFT and task.brief_id is None
    assert not Brief.objects.filter(organization=world.org).exists()


@pytest.mark.parametrize(
    "image", ["mirror/astrolift-agent-aider:old", "mirror/astrolift-agent-openhands-vnc:old"]
)
def test_import_refuses_retired_images_before_registration(world, image):
    package = build_agent_package(
        agent_name="Imported",
        manifest_path="astrolift.toml",
        package_config={},
        brief_text="",
        context_files={},
        skills=[],
        tools=[],
        environment={},
        secret_refs=[],
        runtime={"image": image},
    )
    before = RegisteredApp.objects.count()
    with pytest.raises(ImportedAgentRegistrationError, match="retired"):
        persist_imported_agent_package(project=world.project, package=package, slug="retired-import")
    assert RegisteredApp.objects.count() == before


@pytest.mark.parametrize("runtime", ["aider", "openhands"])
def test_inline_box_refuses_before_creating_rows_or_contacting_a_cluster(world, runtime):
    from astrolift_agents.models import AgentBox
    from astrolift_agents.services.agent_box import AgentBoxEnsureError, ensure_agent_box

    with pytest.raises(AgentBoxEnsureError, match="retired"):
        ensure_agent_box(organization=world.org, image=f"mirror/astrolift-agent-{runtime}:old")
    assert not AgentBox.objects.filter(organization=world.org).exists()


@pytest.mark.parametrize("runtime", ["aider", "openhands"])
def test_repository_registration_refuses_before_persisting_an_app(world, runtime):
    from astrolift_agents.runtime_catalog import RetiredAgentRuntime
    from astrolift_manifest.discover import DiscoveredAgentManifest
    from astrolift_registry.services.manifest_sync import _register_one_agent

    raw = (
        'name = "retired"\n'
        '[[workloads]]\nname = "retired"\nkind = "agent"\n'
        '[[workloads.containers]]\nname = "agent"\nis_primary = true\n'
        f'image_ref = "mirror/astrolift-agent-{runtime}:old"\n'
    )
    discovered = DiscoveredAgentManifest("astrolift.toml", "retired", "retired", "agent", raw)
    before = RegisteredApp.objects.count()
    with pytest.raises(RetiredAgentRuntime, match="retired"):
        _register_one_agent(
            project=world.project,
            discovered=discovered,
            source_kind="github",
            source_repo="example/retired",
            source_url="https://example.invalid/retired",
            default_branch="main",
            deploy_branch="main",
            default_cluster=None,
            agent_repo_tree={},
            catalogue_tree={},
            org_repo_tree_cache={},
            source_archive=None,
        )
    assert RegisteredApp.objects.count() == before
