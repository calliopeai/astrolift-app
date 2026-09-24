"""A managed service's config names secrets only inside its org's namespace (#1921).

Some drivers copy a config field straight into the binding the platform later
resolves into a pod (``password_secret_ref`` on MSK, MemoryDB, ElastiCache
Serverless and GCP Managed Kafka; the FSx mount credentials), or read the
secret with the platform's own credentials and send it somewhere the tenant
chose (the FSx directory join, Firehose's ``SecretsManagerConfiguration``,
the Amazon MQ LDAP bind). On a cluster several orgs share, a config that
names ``managed/<instance>/url`` names another tenant's database secret.

These pin the write-time half: the config walker, the predicate, and every
mutation that writes a config (provision and update for app and project
services, adopt, and manifest persist). The update cases use the real AWS
drivers, whose ``editable_fields`` let the field through, so each one is a
change that was accepted before.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_services.schema.mutations import ServicesMutation
from astrolift_services.schema.mutations.types import (
    AdoptManagedResourceInput,
    ProvisionManagedServiceInput,
    ProvisionProjectManagedServiceInput,
    UpdateManagedServiceInput,
)
from astrolift_services.secret_ref_config import assert_config_secret_refs_scoped, config_secret_refs
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

G = "0192f3c4-1111-7aaa-8bbb-111111111111"
OTHER = "0192f3c4-2222-7ccc-8ddd-222222222222"
ORG = SimpleNamespace(guid=uuid.UUID(G))
# The AWS secrets driver resolves this to astrolift/managed/rds-orders/url:
# another tenant's database URL.
_VICTIM = "managed/rds-orders/url"
_VICTIM_ARN = "arn:aws:secretsmanager:us-west-2:123456789012:secret:astrolift/rds/orders/master-AbCdEf"


# ---- the walker ---------------------------------------------------------------


def test_walker_finds_every_driver_spelling_of_a_secret_ref():
    config = {
        "password_secret_ref": "a",
        "users": [{"username": "u", "password_secret_ref": "b"}],
        "auth": [{"secret_arn": "c"}],
        "secret_arns": ["d", "e"],
        "notification_channels": [
            {
                "label_secret_refs": {"token": {"secret_ref": "f", "field": "value"}, "user": "g#name"},
                "verification_code_secret_ref": {"secret_ref": "h", "field": "code"},
                "body_secret_refs": {"auth.password": "i"},
            }
        ],
        "credential_bundle": "j",
        "destination": {"configuration": {"SecretsManagerConfiguration": {"SecretARN": "k", "RoleARN": "r"}}},
        "file_system": {
            "WindowsConfiguration": {
                "SelfManagedActiveDirectoryConfiguration": {"DomainJoinServiceAccountSecret": "l"}
            }
        },
    }

    assert [ref for _path, ref in config_secret_refs(config)] == "a b c d e f g#name h i j k l".split()
    paths = dict(config_secret_refs(config))
    assert paths["users[0].password_secret_ref"] == "b"
    assert paths["destination.configuration.SecretsManagerConfiguration.SecretARN"] == "k"


def test_walker_leaves_kubernetes_secret_names_and_non_refs_alone():
    """A Kubernetes ``secretRef`` names a Secret in the workload's own
    namespace, and the rest are literals, flags or selectors."""
    config = {
        "inference_spec": {"predictor": {"model": {"envFrom": [{"secretRef": {"name": "hf-token"}}]}}},
        "workflow_spec": {"passwordSecretRef": {"name": "db", "key": "password"}},
        "secret_env": {"API_KEY": {"secret_name": "api", "key": "token"}},
        "image_pull_secrets": ["regcred"],
        "access_key_field": "accessKey",
        "admin_password_secret_kms_key_id": "alias/k",
        "prune_scram_secrets": True,
        # MSK SCRAM secrets must be named AmazonMSK_*, and MSK never returns
        # the value, so this one is waived.
        "scram_secret_arns": ["arn:aws:secretsmanager:us-west-2:123456789012:secret:AmazonMSK_app-AbCdEf"],
    }

    assert config_secret_refs(config) == []


@pytest.mark.parametrize(
    "ref",
    [
        f"services/{G}/kafka#password",
        f"agents/{G}/token",
        f"agent-bundles/{G}/kafka#password",
        f"sm:services/{G}/kafka",
        f"astrolift/services/{G}/kafka",
        f"arn:aws:secretsmanager:us-west-2:123456789012:secret:astrolift/services/{G}/db-AbCdEf",
    ],
)
def test_accepts_a_config_ref_inside_the_org_namespace(ref):
    assert_config_secret_refs_scoped({"password_secret_ref": ref}, organization=ORG)


@pytest.mark.parametrize(
    "ref",
    [
        pytest.param(_VICTIM, id="relative-spelling-of-another-tenants-secret"),
        pytest.param(f"astrolift/{_VICTIM}", id="absolute-spelling"),
        pytest.param(_VICTIM_ARN, id="arn-of-another-tenants-secret"),
        pytest.param(f"services/{OTHER}/kafka", id="another-org"),
        pytest.param(f"project-bundles/{G}/{OTHER}/kafka", id="a-project-bundle-location"),
        pytest.param("kafka/password", id="bare-name-in-the-shared-root"),
    ],
)
def test_refuses_a_config_ref_outside_the_org_namespace(ref):
    from astrolift_dispatch.agent_secrets import SecretRefNamespaceError

    with pytest.raises(SecretRefNamespaceError, match=f"services/{G}/"):
        assert_config_secret_refs_scoped({"users": [{"password_secret_ref": ref}]}, organization=ORG)


# ---- the mutations -------------------------------------------------------------


def _info():
    request = SimpleNamespace(user=None, META={})
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


def _scaffold(plugin_slug="k8s-1921"):
    org = Organization.objects.create(name="Acme", slug="acme-1921")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-1921")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-1921")
    ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name=plugin_slug, slug=plugin_slug, plugin_version="0.0.1")],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="cluster-1921",
        name="Cluster",
        provider_plugin=ProviderPlugin.objects.get(slug=plugin_slug),
        endpoint="https://cluster.invalid",
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug="app-1921",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    return SimpleNamespace(org=org, project=project, cluster=cluster, app=app, env=env)


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _provision(world, config):
    with _ctx(world.org), patch("astrolift_workflows.client.start_workflow") as start:
        result = ServicesMutation().provision_managed_service(
            _info(),
            input=ProvisionManagedServiceInput(
                app_slug=world.app.slug,
                environment_name=world.env.name,
                kind="event_stream",
                name="events",
                variant="msk",
                config=config,
            ),
        )
    return result, start


_PLANTED_CONFIGS = [
    pytest.param({"password_secret_ref": _VICTIM}, id="msk-password"),
    pytest.param({"users": [{"username": "app", "password_secret_ref": _VICTIM}]}, id="mq-user-password"),
    pytest.param(
        {"destination": {"configuration": {"SecretsManagerConfiguration": {"SecretARN": _VICTIM_ARN}}}},
        id="firehose-secrets-manager-configuration",
    ),
    pytest.param(
        {
            "file_system": {
                "WindowsConfiguration": {
                    "SelfManagedActiveDirectoryConfiguration": {"DomainJoinServiceAccountSecret": _VICTIM_ARN}
                }
            }
        },
        id="fsx-domain-join-secret",
    ),
]


@pytest.mark.django_db
@pytest.mark.parametrize("config", _PLANTED_CONFIGS)
def test_provision_refuses_a_config_naming_a_secret_outside_the_org(permission_resolver, config):
    world = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    result, start = _provision(world, config)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "config"
    assert f"services/{world.org.guid}/" in result.errors[0].message
    assert not ManagedService.objects.filter(registered_app=world.app, name="events").exists()
    start.assert_not_called()


@pytest.mark.django_db
def test_provision_accepts_a_config_naming_a_secret_inside_the_org(permission_resolver):
    world = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    ref = f"services/{world.org.guid}/kafka#password"

    result, start = _provision(world, {"password_secret_ref": ref})

    assert result.ok is True, result.errors
    assert ManagedService.objects.get(registered_app=world.app, name="events").config == {
        "password_secret_ref": ref
    }
    start.assert_called_once()


@pytest.mark.django_db
def test_project_provision_refuses_a_config_naming_a_secret_outside_the_org(permission_resolver):
    world = _scaffold()
    permission_resolver.grant(Permission.PROJECT_UPDATE)

    with _ctx(world.org), patch("astrolift_workflows.client.start_workflow") as start:
        result = ServicesMutation().provision_project_managed_service(
            _info(),
            input=ProvisionProjectManagedServiceInput(
                project_id=GUID(str(world.project.guid)),
                cluster_id=GUID(str(world.cluster.guid)),
                kind="redis",
                name="cache",
                config={"auth_mode": "external", "password_secret_ref": _VICTIM},
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "config"
    assert not ManagedService.objects.filter(project=world.project, name="cache").exists()
    start.assert_not_called()


def _active_service(world, *, kind, variant, config=None, project=False):
    if project:
        owner = {"project": world.project, "tenant_cluster": world.cluster}
    else:
        owner = {"registered_app": world.app, "app_environment": world.env}
    return ManagedService.objects.create(
        kind=kind,
        variant=variant,
        name=f"{kind}-svc",
        config=dict(config or {}),
        applied_config=dict(config or {}),
        backend_ref=f"{kind}/{variant}-1921",
        status=ManagedService.Status.ACTIVE,
        **owner,
    )


def _update(world, svc, config, *, project=False):
    mutation = ServicesMutation()
    update = mutation.update_project_managed_service if project else mutation.update_managed_service
    with _ctx(world.org), patch("astrolift_workflows.client.start_workflow") as start:
        start.return_value = SimpleNamespace(enqueued=True, run_id="run-1921")
        result = update(_info(), input=UpdateManagedServiceInput(id=GUID(str(svc.guid)), config=config))
    return result, start


# Real AWS drivers: each field is in the driver's editable_fields, so before
# #1921 the update was accepted and the workflow started.
_EDITABLE_PLANTS = [
    pytest.param(
        "stream",
        "firehose",
        "destination_update",
        {"destination_update": {"SecretsManagerConfiguration": {"Enabled": True, "SecretARN": _VICTIM_ARN}}},
        id="firehose-destination-update",
    ),
    pytest.param(
        "filesystem",
        "fsx_windows",
        "file_system_update",
        {
            "file_system_update": {
                "WindowsConfiguration": {
                    "SelfManagedActiveDirectoryConfiguration": {"DomainJoinServiceAccountSecret": _VICTIM_ARN}
                }
            }
        },
        id="fsx-file-system-update",
    ),
    pytest.param(
        "filesystem",
        "fsx_windows",
        "mount_password_secret_ref",
        {"mount_password_secret_ref": _VICTIM},
        id="fsx-mount-password",
    ),
    pytest.param(
        "event_stream", "msk", "password_secret_ref", {"password_secret_ref": _VICTIM}, id="msk-password"
    ),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("kind", "variant", "field", "config"), _EDITABLE_PLANTS)
def test_update_refuses_an_editable_field_naming_a_secret_outside_the_org(
    permission_resolver, kind, variant, field, config
):
    from astrolift_services.schema.types import _editable_fields_for

    world = _scaffold(plugin_slug="aws")
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = _active_service(world, kind=kind, variant=variant)
    assert field in _editable_fields_for(svc)

    result, start = _update(world, svc, config)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "config"
    svc.refresh_from_db()
    assert svc.config == {}
    assert svc.status == ManagedService.Status.ACTIVE
    start.assert_not_called()


@pytest.mark.django_db
def test_update_accepts_an_editable_field_naming_a_secret_inside_the_org(permission_resolver):
    world = _scaffold(plugin_slug="aws")
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = _active_service(world, kind="event_stream", variant="msk")
    ref = f"agent-bundles/{world.org.guid}/kafka#password"

    result, start = _update(world, svc, {"password_secret_ref": ref})

    assert result.ok is True, result.errors
    svc.refresh_from_db()
    assert svc.config == {"password_secret_ref": ref}
    start.assert_called_once()


@pytest.mark.django_db
def test_project_update_refuses_a_field_naming_a_secret_outside_the_org(permission_resolver):
    world = _scaffold(plugin_slug="aws")
    permission_resolver.grant(Permission.PROJECT_UPDATE)
    svc = _active_service(world, kind="event_stream", variant="msk", project=True)

    result, start = _update(world, svc, {"password_secret_ref": _VICTIM}, project=True)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    svc.refresh_from_db()
    assert svc.config == {}
    start.assert_not_called()


@pytest.mark.django_db
def test_adopt_refuses_a_stored_config_naming_a_secret_outside_the_org(permission_resolver):
    """Adoption builds the provision spec from the stored config, so one
    written before #1921 is fixed first rather than bound to a resource."""
    world = _scaffold(plugin_slug="azure")
    permission_resolver.grant(Permission.MANAGED_SERVICE_ADOPT)
    svc = _active_service(
        world, kind="redis", variant="azure_cache_redis", config={"password_secret_ref": _VICTIM}
    )

    with (
        _ctx(world.org),
        patch("astrolift_services.managed_resource_adoption.adopt_managed_resource") as adopt,
    ):
        result = ServicesMutation().adopt_managed_resource(
            _info(),
            input=AdoptManagedResourceInput(
                id=GUID(str(svc.guid)),
                resource_id="/subscriptions/s/resourceGroups/r/providers/Microsoft.Cache/redis/c",
                reason="take over",
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert result.errors[0].field == "config"
    adopt.assert_not_called()


@pytest.mark.django_db
def test_manifest_persist_refuses_a_config_naming_a_secret_outside_the_org():
    from astrolift_manifest.persist import reconcile_managed_services
    from astrolift_manifest.types import ManagedServiceManifest

    world = _scaffold()
    service = ManagedServiceManifest(
        kind="event_stream",
        name="events",
        environment="production",
        config={"password_secret_ref": _VICTIM},
    )

    with pytest.raises(ValueError, match=f"services/{world.org.guid}/"):
        reconcile_managed_services(world.app, (service,))

    assert not ManagedService.objects.filter(registered_app=world.app).exists()
