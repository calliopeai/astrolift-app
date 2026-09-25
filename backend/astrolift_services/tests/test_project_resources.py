"""Project-owned managed resources, attachments, and secret bundles."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_agents.schema.mutations import AgentsMutation
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_dispatch.agent_secrets import (
    agent_container_env,
    effective_secret_refs,
    project_managed_service_env_vars,
)
from astrolift_drivers.registry import PluginManifest, plugins
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_services.models import (
    ManagedService,
    ManagedServiceAttachment,
    ManagedServiceBinding,
    ManagedServiceVolumeBinding,
    SecretBundle,
)
from astrolift_services.schema.mutations import (
    AttachSecretBundleInput,
    CreateProjectSecretBundleInput,
    DetachProjectManagedServiceInput,
    ProjectSecretBundleKeyInput,
    ProvisionProjectManagedServiceInput,
    ServicesMutation,
    UpdateManagedServiceInput,
)
from astrolift_services.schema.queries import ServicesQuery
from astrolift_workflows.activities.app_lifecycle import _managed_services_for_environment
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    request = SimpleNamespace(user=SimpleNamespace(pk=None, email="operator@example.com"), META={})
    return SimpleNamespace(context=SimpleNamespace(request=request, user=request.user))


class _SecretsBackend:
    provider_id = "test-secrets"
    supports_value_reveal = True

    def __init__(self):
        self.values: dict[str, dict[str, str]] = {}

    def get(self, path):
        return self.values.get(path)

    def upsert(self, path, payload):
        self.values[path] = dict(payload)

    def delete(self, path):
        self.values.pop(path, None)


def _graph(suffix: str = "a"):
    org = Organization.objects.create(name=f"Org {suffix}", slug=f"org-{suffix}")
    team = Team.objects.create(organization=org, name="Engineering", slug=f"eng-{suffix}")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Agent project",
        slug=f"agents-{suffix}",
    )
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=f"Kubernetes {suffix}",
                slug=f"kubernetes-{suffix}",
                plugin_version="1.0.0",
            )
        ]
    )
    plugin = ProviderPlugin.objects.get(slug=f"kubernetes-{suffix}")
    cluster = TenantCluster.objects.create(
        organization=org,
        provider_plugin=plugin,
        name="Runtime",
        slug=f"runtime-{suffix}",
        endpoint="https://cluster.example.com",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Agent package",
        slug=f"agent-package-{suffix}",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    workload = Workload.objects.create(
        registered_app=app,
        name="Research agent",
        slug=f"research-{suffix}",
        kind=Workload.Kind.AGENT,
    )
    spec = AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Research agent",
        slug=workload.slug,
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
    )
    return SimpleNamespace(
        org=org,
        team=team,
        project=project,
        cluster=cluster,
        app=app,
        env=env,
        workload=workload,
        spec=spec,
    )


def _project_agent(graph, suffix: str):
    project = Project.objects.create(
        organization=graph.org,
        team=graph.team,
        name=f"Other project {suffix}",
        slug=f"other-{suffix}",
    )
    app = RegisteredApp.objects.create(
        organization=graph.org,
        team=graph.team,
        project=project,
        name=f"Other packet {suffix}",
        slug=f"other-packet-{suffix}",
    )
    spec = AgentEnvironmentSpec.objects.create(
        organization=graph.org,
        name=f"Other agent {suffix}",
        slug=f"other-agent-{suffix}",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
    )
    Workload.objects.create(
        registered_app=app,
        name=spec.name,
        slug=spec.slug,
        kind=Workload.Kind.AGENT,
    )
    return SimpleNamespace(project=project, app=app, spec=spec)


def _second_cluster(graph, suffix: str):
    return TenantCluster.objects.create(
        organization=graph.org,
        provider_plugin=graph.cluster.provider_plugin,
        name=f"Second runtime {suffix}",
        slug=f"second-runtime-{suffix}",
        endpoint="https://second-cluster.example.com",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


def _tenant(graph):
    return tenant_context(TenantContext(organization_id=graph.org.pk))


def test_project_service_requires_exactly_one_owner_scope():
    graph = _graph("constraint")
    with pytest.raises(IntegrityError), transaction.atomic():
        ManagedService.objects.create(
            project=graph.project,
            tenant_cluster=graph.cluster,
            registered_app=graph.app,
            app_environment=graph.env,
            kind=ManagedService.Kind.POSTGRES,
            name="invalid",
        )


def test_project_catalog_is_cluster_derived_and_tenant_scoped(permission_resolver, monkeypatch):
    class _Driver:
        def config_schema(self):
            return {"type": "object", "properties": {}}

        def binding_schema(self):
            return SimpleNamespace(env_vars={"SERVICE_URL": "Endpoint"})

    owner = _graph("catalog-owner")
    other = _graph("catalog-other")
    plugin_slug = owner.cluster.provider_plugin.slug
    monkeypatch.setattr(
        plugins,
        "_plugins",
        {
            plugin_slug: PluginManifest(
                plugin_id=plugin_slug,
                display_name="Test provider",
                version="test",
                drivers={"managed:queue:custom_queue": _Driver},
            )
        },
    )
    permission_resolver.grant(Permission.PROJECT_READ)

    with _tenant(owner):
        visible = ServicesQuery().astrolift_project_managed_service_catalog(
            _info(),
            project_id=GUID(str(owner.project.guid)),
            cluster_id=GUID(str(owner.cluster.guid)),
        )
        hidden = ServicesQuery().astrolift_project_managed_service_catalog(
            _info(),
            project_id=GUID(str(owner.project.guid)),
            cluster_id=GUID(str(other.cluster.guid)),
        )

    assert [(row.kind, row.variant, row.available) for row in visible] == [("queue", "custom_queue", True)]
    assert visible[0].binding_envs == ["SERVICE_URL"]
    assert hidden == []


def test_provision_project_service_creates_shared_attachments_and_starts_workflow(
    permission_resolver,
):
    graph = _graph("provision")
    permission_resolver.grant(Permission.PROJECT_UPDATE)

    with _tenant(graph), patch("astrolift_workflows.client.start_workflow") as start:
        result = ServicesMutation().provision_project_managed_service(
            _info(),
            input=ProvisionProjectManagedServiceInput(
                project_id=GUID(str(graph.project.guid)),
                cluster_id=GUID(str(graph.cluster.guid)),
                environment_name="production",
                kind=ManagedService.Kind.POSTGRES,
                name="triage-db",
                variant="rds",
                config={"size": "small"},
                agent_environment_spec_slugs=[graph.spec.slug],
                app_environment_ids=[GUID(str(graph.env.guid))],
            ),
        )

    assert result.ok, result.errors
    service = ManagedService.objects.get(guid=str(result.data.id))
    assert service.project == graph.project
    assert service.registered_app_id is None
    assert service.tenant_cluster == graph.cluster
    assert service.attachments.filter(agent_environment_spec=graph.spec).exists()
    assert service.attachments.filter(app_environment=graph.env).exists()
    start.assert_called_once()
    assert start.call_args.args[0] == "ProvisionManagedServiceWorkflow"


def test_provision_project_service_rejects_foreign_project_consumer(permission_resolver):
    owner = _graph("owner")
    other = _project_agent(owner, "other")
    permission_resolver.grant(Permission.PROJECT_UPDATE)

    with _tenant(owner), patch("astrolift_workflows.client.start_workflow"):
        result = ServicesMutation().provision_project_managed_service(
            _info(),
            input=ProvisionProjectManagedServiceInput(
                project_id=GUID(str(owner.project.guid)),
                cluster_id=GUID(str(owner.cluster.guid)),
                kind=ManagedService.Kind.REDIS,
                name="cache",
                agent_environment_spec_slugs=[other.spec.slug],
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert not ManagedService.objects.filter(project=owner.project).exists()


def test_project_service_rejects_consumers_on_a_different_runtime_cluster(permission_resolver):
    graph = _graph("cluster-boundary")
    second = _second_cluster(graph, "cluster-boundary")
    second_env = AppEnvironment.objects.create(
        registered_app=graph.app,
        name="staging",
        tenant_cluster=second,
    )
    permission_resolver.grant(Permission.PROJECT_UPDATE)

    with _tenant(graph), patch("astrolift_workflows.client.start_workflow"):
        app_result = ServicesMutation().provision_project_managed_service(
            _info(),
            input=ProvisionProjectManagedServiceInput(
                project_id=GUID(str(graph.project.guid)),
                cluster_id=GUID(str(graph.cluster.guid)),
                kind=ManagedService.Kind.POSTGRES,
                name="cross-cluster-app",
                app_environment_ids=[GUID(str(second_env.guid))],
            ),
        )
        agent_result = ServicesMutation().provision_project_managed_service(
            _info(),
            input=ProvisionProjectManagedServiceInput(
                project_id=GUID(str(graph.project.guid)),
                cluster_id=GUID(str(second.guid)),
                kind=ManagedService.Kind.REDIS,
                name="cross-cluster-agent",
                agent_environment_spec_slugs=[graph.spec.slug],
            ),
        )

    assert app_result.ok is False
    assert app_result.errors[0].code == ErrorCode.VALIDATION.value
    assert agent_result.ok is False
    assert agent_result.errors[0].code == ErrorCode.VALIDATION.value
    assert not ManagedService.objects.filter(project=graph.project).exists()


def test_active_project_service_bindings_feed_agent_without_overriding_manifest():
    graph = _graph("bindings")
    graph.spec.secret_refs = [{"env_var": "DATABASE_URL", "uri": "manifest/database"}]
    graph.spec.save(update_fields=["secret_refs", "updated_at", "version"])
    service = ManagedService.objects.create(
        project=graph.project,
        tenant_cluster=graph.cluster,
        kind=ManagedService.Kind.POSTGRES,
        name="shared-db",
        status=ManagedService.Status.ACTIVE,
    )
    ManagedServiceAttachment.objects.create(
        managed_service=service,
        agent_environment_spec=graph.spec,
    )
    ManagedServiceBinding.objects.create(
        managed_service=service,
        env_key="DATABASE_URL",
        env_value_ref="project/database",
        is_secret=True,
    )
    ManagedServiceBinding.objects.create(
        managed_service=service,
        env_key="DATABASE_HOST",
        env_value_ref="db.internal",
        is_secret=False,
    )

    refs = {row["env_var"]: row["uri"] for row in effective_secret_refs(graph.spec)}
    assert refs["DATABASE_URL"] == "manifest/database"
    assert project_managed_service_env_vars(graph.spec) == {"DATABASE_HOST": "db.internal"}
    graph.spec.env_vars = {"DATABASE_HOST": "manifest.internal"}
    graph.spec.save(update_fields=["env_vars", "updated_at", "version"])
    env = agent_container_env(graph.spec, "task-secrets")
    database_host_entries = [row for row in env if row["name"] == "DATABASE_HOST"]
    assert database_host_entries == [{"name": "DATABASE_HOST", "value": "manifest.internal"}]


def test_project_service_binding_resolves_for_an_agent_outside_its_typed_ref_namespace(monkeypatch):
    """#1921 confines the refs an operator or a manifest types to the org's
    agent namespace. A project managed-service binding is derived by the
    platform under the service's own root, so it keeps resolving into the
    agent's task Secret."""
    import core.app_deploy as app_deploy
    from astrolift_dispatch.agent_secrets import resolve_task_secret_manifest, unscoped_secret_refs

    graph = _graph("binding-scope")
    service = ManagedService.objects.create(
        project=graph.project,
        tenant_cluster=graph.cluster,
        kind=ManagedService.Kind.POSTGRES,
        name="shared-db",
        status=ManagedService.Status.ACTIVE,
    )
    ManagedServiceAttachment.objects.create(managed_service=service, agent_environment_spec=graph.spec)
    ManagedServiceBinding.objects.create(
        managed_service=service,
        env_key="DATABASE_URL",
        env_value_ref="astrolift/managed/shared-db/url",
        is_secret=True,
    )
    store = {"astrolift/managed/shared-db/url": {"value": "postgres://shared"}}
    monkeypatch.setattr(app_deploy, "driver_for_capability", lambda _c, _cap: SimpleNamespace(get=store.get))

    manifest = resolve_task_secret_manifest(
        cluster=object(), spec=graph.spec, secret_name="task-secrets", namespace="ns", task_guid="t"
    )

    assert manifest["stringData"] == {"DATABASE_URL": "postgres://shared"}
    assert unscoped_secret_refs(graph.spec) == {}


def test_project_service_attachment_feeds_app_lifecycle_selection():
    graph = _graph("app-binding")
    project_service = ManagedService.objects.create(
        project=graph.project,
        tenant_cluster=graph.cluster,
        kind=ManagedService.Kind.POSTGRES,
        name="shared-db",
        status=ManagedService.Status.ACTIVE,
    )
    other_service = ManagedService.objects.create(
        project=graph.project,
        tenant_cluster=graph.cluster,
        kind=ManagedService.Kind.REDIS,
        name="not-attached",
        status=ManagedService.Status.ACTIVE,
    )
    ManagedServiceAttachment.objects.create(
        managed_service=project_service,
        app_environment=graph.env,
    )

    selected = list(_managed_services_for_environment(graph.env))

    assert project_service in selected
    assert other_service not in selected


def test_project_bundle_is_visible_and_attachable_only_inside_owning_project(permission_resolver):
    owner = _graph("bundle-owner")
    other = _project_agent(owner, "bundle-other")
    bundle = SecretBundle.objects.create(
        organization=owner.org,
        project=owner.project,
        tenant_cluster=owner.cluster,
        name="Shared Jira",
        slug="shared-jira",
        backend_ref="projects/shared-jira",
    )
    permission_resolver.grant(Permission.SECRET_WRITE)
    permission_resolver.grant(Permission.SECRET_LIST)
    permission_resolver.grant(Permission.PROJECT_READ)

    with _tenant(owner):
        attached = AgentsMutation().attach_agent_secret_bundle(
            _info(), env_spec_slug=owner.spec.slug, bundle_id=str(bundle.guid)
        )
        rejected = AgentsMutation().attach_agent_secret_bundle(
            _info(), env_spec_slug=other.spec.slug, bundle_id=str(bundle.guid)
        )
        visible = AgentsQuery().agent_secret_bundles(_info(), env_spec_slug=owner.spec.slug)
        hidden = AgentsQuery().agent_secret_bundles(_info(), env_spec_slug=other.spec.slug)
        project_bundles = ServicesQuery().astrolift_project_secret_bundles(
            _info(), project_id=GUID(str(owner.project.guid))
        )

    assert attached.ok is True
    assert rejected.ok is False
    assert rejected.errors[0].code == ErrorCode.NOT_FOUND.value
    assert [row.slug for row in visible] == [bundle.slug]
    assert all(row.slug != bundle.slug for row in hidden)
    assert [row.consumer_slug for row in project_bundles[0].consumers] == [owner.spec.slug]


def test_project_bundle_rejects_consumers_on_a_different_secrets_cluster(permission_resolver):
    graph = _graph("bundle-cluster")
    second = _second_cluster(graph, "bundle-cluster")
    bundle = SecretBundle.objects.create(
        organization=graph.org,
        project=graph.project,
        tenant_cluster=second,
        name="Other cluster bundle",
        slug="other-cluster-bundle",
        backend_ref="projects/other-cluster-bundle",
    )
    permission_resolver.grant(Permission.SECRET_WRITE)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _tenant(graph):
        agent_result = AgentsMutation().attach_agent_secret_bundle(
            _info(), env_spec_slug=graph.spec.slug, bundle_id=str(bundle.guid)
        )
        app_result = ServicesMutation().attach_secret_bundle(
            _info(),
            input=AttachSecretBundleInput(
                app_slug=graph.app.slug,
                environment_name=graph.env.name,
                bundle_slug=bundle.slug,
            ),
        )

    assert agent_result.ok is False
    assert agent_result.errors[0].code == ErrorCode.PRECONDITION.value
    assert app_result.ok is False
    assert app_result.errors[0].code == ErrorCode.PRECONDITION.value


def test_project_bundle_crud_reads_real_backend_and_is_tenant_scoped(permission_resolver, monkeypatch):
    graph = _graph("bundle-crud")
    other = _graph("bundle-crud-other")
    backend = _SecretsBackend()
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda _cluster, _cap: backend)
    for permission in (
        Permission.PROJECT_UPDATE,
        Permission.PROJECT_READ,
        Permission.SECRET_WRITE,
        Permission.SECRET_READ,
    ):
        permission_resolver.grant(permission)

    with _tenant(graph):
        rejected_path = ServicesMutation().create_project_secret_bundle(
            _info(),
            input=CreateProjectSecretBundleInput(
                project_id=GUID(str(graph.project.guid)),
                cluster_id=GUID(str(graph.cluster.guid)),
                name="Unsafe shared",
                slug="unsafe-shared",
                backend_ref="organization-secrets/another-project",
            ),
        )
        created = ServicesMutation().create_project_secret_bundle(
            _info(),
            input=CreateProjectSecretBundleInput(
                project_id=GUID(str(graph.project.guid)),
                cluster_id=GUID(str(graph.cluster.guid)),
                name="Triage shared",
                slug="triage-shared",
            ),
        )
        assert created.ok, created.errors
        bundle_id = created.data.id
        written = ServicesMutation().set_project_bundle_secret_value(
            _info(),
            input=ProjectSecretBundleKeyInput(
                bundle_id=bundle_id,
                key="JIRA_TOKEN",
                value="secret-value",
            ),
        )
        revealed = ServicesMutation().reveal_project_bundle_secret_value(
            _info(),
            input=ProjectSecretBundleKeyInput(bundle_id=bundle_id, key="JIRA_TOKEN"),
        )
        listed = ServicesQuery().astrolift_project_secret_bundles(
            _info(), project_id=GUID(str(graph.project.guid))
        )

    assert rejected_path.ok is False
    assert rejected_path.errors[0].field == "backendRef"
    assert written.ok is True
    assert revealed.ok is True
    assert revealed.data.value == "secret-value"
    assert listed[0].key_names == ["JIRA_TOKEN"]

    with _tenant(other):
        assert (
            ServicesQuery().astrolift_project_secret_bundles(
                _info(), project_id=GUID(str(graph.project.guid))
            )
            == []
        )


def test_project_resource_mutation_denies_without_project_update(permission_resolver):
    graph = _graph("denied")
    with _tenant(graph):
        result = ServicesMutation().provision_project_managed_service(
            _info(),
            input=ProvisionProjectManagedServiceInput(
                project_id=GUID(str(graph.project.guid)),
                cluster_id=GUID(str(graph.cluster.guid)),
                kind=ManagedService.Kind.QUEUE,
                name="denied",
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value


def test_project_resource_query_exposes_mount_readiness_without_secret_refs(permission_resolver):
    graph = _graph("volume-query")
    service = ManagedService.objects.create(
        project=graph.project,
        tenant_cluster=graph.cluster,
        kind=ManagedService.Kind.FILESYSTEM,
        name="shared-files",
        status=ManagedService.Status.ACTIVE,
    )
    ManagedServiceVolumeBinding.objects.create(
        managed_service=service,
        name="shared-files",
        mount_path="/mnt/shared",
        source_kind=ManagedServiceVolumeBinding.SourceKind.CSI,
        protocol="smb3",
        csi_driver="smb.csi.k8s.io",
        volume_handle="files.example##share",
        secret_refs={"username": "secret/path#username", "password": "secret/path#password"},
        access_modes=["ReadWriteMany"],
    )
    permission_resolver.grant(Permission.PROJECT_READ)

    with _tenant(graph):
        rows = ServicesQuery().astrolift_project_managed_services(
            _info(), project_id=GUID(str(graph.project.guid))
        )

    assert len(rows) == 1
    mount = rows[0].volume_bindings[0]
    assert mount.mount_path == "/mnt/shared"
    assert mount.csi_driver == "smb.csi.k8s.io"
    assert mount.credential_reference_count == 2
    assert "secret/path" not in repr(mount)


def test_project_resource_graphql_is_scoped_to_the_active_team(permission_resolver):
    graph = _graph("team-scope")
    other = _project_agent(graph, "team-scope-other")
    other_team = Team.objects.create(
        organization=graph.org,
        name="Other engineering",
        slug="other-eng-team-scope",
    )
    other.project.team = other_team
    other.project.save(update_fields=["team", "updated_at", "version"])
    other.app.team = other_team
    other.app.save(update_fields=["team", "updated_at", "version"])
    service = ManagedService.objects.create(
        project=other.project,
        tenant_cluster=graph.cluster,
        kind=ManagedService.Kind.OBJECT_STORE,
        name="other-team-bucket",
    )
    attachment = ManagedServiceAttachment.objects.create(
        managed_service=service,
        agent_environment_spec=other.spec,
    )
    for permission in (Permission.PROJECT_READ, Permission.PROJECT_UPDATE):
        permission_resolver.grant(permission)

    with tenant_context(
        TenantContext(
            organization_id=graph.org.pk,
            team_id=graph.team.pk,
        )
    ):
        listed = ServicesQuery().astrolift_project_managed_services(
            _info(), project_id=GUID(str(other.project.guid))
        )
        updated = ServicesMutation().update_project_managed_service(
            _info(),
            input=UpdateManagedServiceInput(
                id=GUID(str(service.guid)),
                name="cross-team-write",
            ),
        )
        detached = ServicesMutation().detach_project_managed_service(
            _info(),
            input=DetachProjectManagedServiceInput(
                attachment_id=GUID(str(attachment.guid)),
            ),
        )

    assert listed == []
    assert updated.ok is False
    assert updated.errors[0].code == ErrorCode.NOT_FOUND.value
    assert detached.ok is False
    assert detached.errors[0].code == ErrorCode.NOT_FOUND.value
    service.refresh_from_db()
    attachment.refresh_from_db()
    assert service.name == "other-team-bucket"
    assert attachment.deleted_at is None
