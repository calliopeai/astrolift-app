"""A managed service's config names secrets only inside its org's namespace (#1921).

Some drivers copy a config field straight into the binding the platform later
resolves into a pod (``password_secret_ref`` on MSK, MemoryDB, ElastiCache
Serverless and GCP Managed Kafka; the FSx mount credentials), or read the
secret with the platform's own credentials and send it somewhere the tenant
chose (the FSx directory join, Firehose's ``SecretsManagerConfiguration``,
the Amazon MQ LDAP bind). On a cluster several orgs share, a config that
names ``managed/<instance>/url`` names another tenant's database secret.

These pin the write-time half: the config walkers, the predicates, and every
mutation that writes a config (provision and update for app and project
services, adopt, and manifest persist). The update cases use the real AWS
drivers, whose ``editable_fields`` let the field through, so each one is a
change that was accepted before.

The namespace is the owner's, ``services/<org guid>/<owner guid>/``, not the
org's: two apps of one org cannot name each other's secrets, nor an agent
secret, through a config that APP_UPDATE on one of them lets a caller write.
A Google Secret Manager reference (Cloud Functions, Managed Kafka Connect) is
judged as the physical id the GCP secrets driver files that namespace under,
in the install's project.
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
from astrolift_services.secret_ref_config import (
    assert_config_secret_refs_scoped,
    config_secret_refs,
    gcp_secret_refs,
)
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

G = "0192f3c4-1111-7aaa-8bbb-111111111111"
OTHER = "0192f3c4-2222-7ccc-8ddd-222222222222"
APP = "0192f3c4-3333-7eee-8fff-333333333333"
ORG = SimpleNamespace(guid=uuid.UUID(G))
# The app that owns the service: its namespace is services/<G>/<APP>/.
OWNER = SimpleNamespace(guid=uuid.UUID(APP), organization=ORG)
_GCP_CLUSTER = SimpleNamespace(
    slug="gke-1921",
    region="us-central1",
    provider_plugin=SimpleNamespace(slug="gcp"),
    provider_config={"project_id": "acme-prod", "region": "us-central1"},
    auth_config={},
)
# What the GCP secrets driver files services/<G>/<APP>/<name> under.
_OWN_GCP_ID = f"astrolift-services-{G}-{APP}-api-token"
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
        f"services/{G}/{APP}/kafka#password",
        f"services/{G}/{APP}/kafka/client-key",
        f"sm:services/{G}/{APP}/kafka",
        f"/services/{G}/{APP}/kafka",
        f"astrolift/services/{G}/{APP}/kafka",
        f"arn:aws:secretsmanager:us-west-2:123456789012:secret:astrolift/services/{G}/{APP}/db-AbCdEf",
        f"sm:arn:aws:secretsmanager:us-west-2:123456789012:secret:services/{G}/{APP}/db-AbCdEf",
    ],
)
def test_accepts_a_config_ref_inside_the_owners_namespace(ref):
    assert_config_secret_refs_scoped({"password_secret_ref": ref}, owner=OWNER, cluster=None)


@pytest.mark.parametrize(
    "ref",
    [
        pytest.param(_VICTIM, id="relative-spelling-of-another-tenants-secret"),
        pytest.param(f"astrolift/{_VICTIM}", id="absolute-spelling"),
        pytest.param(_VICTIM_ARN, id="arn-of-another-tenants-secret"),
        pytest.param(f"services/{OTHER}/{APP}/kafka", id="another-org"),
        pytest.param(f"project-bundles/{G}/{OTHER}/kafka", id="a-project-bundle-location"),
        pytest.param("kafka/password", id="bare-name-in-the-shared-root"),
        # The owner split: the org's other apps and its agents are not this
        # app's to name, whatever APP_UPDATE on it allows.
        pytest.param(f"services/{G}/{OTHER}/kafka", id="another-app-of-the-same-org"),
        pytest.param(f"services/{G}/kafka", id="the-org-level-services-root"),
        pytest.param(f"services/{G}/{APP}", id="the-owner-root-itself"),
        pytest.param(f"agents/{G}/admin-token", id="an-agent-secret"),
        pytest.param(f"agent-bundles/{G}/kafka#password", id="an-agent-bundle"),
        pytest.param(
            f"arn:aws:secretsmanager:us-west-2:123456789012:secret:astrolift/services/{G}/{OTHER}/db-AbCdEf",
            id="arn-of-another-apps-secret",
        ),
        # A driver reads its config verbatim and none strips the scheme.
        pytest.param(f"secret://services/{G}/{APP}/kafka", id="reference-scheme"),
        pytest.param(f"sm:sm:services/{G}/{APP}/kafka", id="store-scheme-twice"),
    ],
)
def test_refuses_a_config_ref_outside_the_owners_namespace(ref):
    from astrolift_dispatch.agent_secrets import SecretRefNamespaceError

    with pytest.raises(SecretRefNamespaceError, match=f"services/{G}/{APP}/"):
        assert_config_secret_refs_scoped({"users": [{"password_secret_ref": ref}]}, owner=OWNER, cluster=None)


def test_a_service_with_no_owner_can_name_no_secret():
    from astrolift_dispatch.agent_secrets import SecretRefNamespaceError

    with pytest.raises(SecretRefNamespaceError, match="no owning app or project"):
        assert_config_secret_refs_scoped(
            {"password_secret_ref": f"services/{G}/{APP}/x"}, owner=None, cluster=None
        )


# ---- Google Secret Manager references -------------------------------------------


def test_gcp_walker_finds_every_google_secret_manager_reference():
    config = {
        "secret_environment": [
            {"key": "A", "secret": "a", "version": "1", "project_id": "p"},
            {"key": "B", "secret": "b", "version": "2"},
        ],
        "secret_volumes": [{"mount_path": "/s", "secret": "c", "versions": [{"version": "1", "path": "c"}]}],
        "connect_clusters": [{"id": "c", "secret_paths": ["projects/p/secrets/d/versions/3", "not-a-path"]}],
        "service_raw_fields": {"secretEnvironmentVariables": [{"key": "E", "secret": "e", "projectId": "q"}]},
    }

    assert [(ref.path, ref.secret_id, ref.project) for ref in gcp_secret_refs(config)] == [
        ("secret_environment[0].secret", "a", "p"),
        ("secret_environment[1].secret", "b", ""),
        ("secret_volumes[0].secret", "c", ""),
        ("connect_clusters[0].secret_paths[0]", "d", "p"),
        ("connect_clusters[0].secret_paths[1]", "", ""),
        ("service_raw_fields.secretEnvironmentVariables[0].secret", "e", "q"),
    ]
    # Not a platform-store path, so the other walker never saw them (#1921).
    assert config_secret_refs(config) == []


def test_gcp_walker_reads_every_spelling_google_accepts():
    """Google's JSON parser takes a field's proto name as well as its JSON
    name, and a raw-field passthrough can carry either (#1921)."""
    config = {
        "service_raw_fields": {
            "secret_environment_variables": [{"key": "A", "secret": "a", "version": "1", "project_id": "p"}],
            "SecretVolumes": [{"mountPath": "/s", "Secret": "b", "projectId": "q", "versions": []}],
        },
        "connect_clusters": [
            {"id": "c", "raw_fields": {"gcpConfig": {"secret_paths": ["projects/p/secrets/c/versions/1"]}}}
        ],
    }

    assert [(ref.path, ref.secret_id, ref.project) for ref in gcp_secret_refs(config)] == [
        ("service_raw_fields.secret_environment_variables[0].secret", "a", "p"),
        ("service_raw_fields.SecretVolumes[0].secret", "b", "q"),
        ("connect_clusters[0].raw_fields.gcpConfig.secret_paths[0]", "c", "p"),
    ]


def _function_secret(secret, **extra):
    return {"secret_environment": [{"key": "API_TOKEN", "secret": secret, "version": "1", **extra}]}


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(_function_secret(_OWN_GCP_ID), id="function-secret-in-its-own-project"),
        pytest.param(
            _function_secret(_OWN_GCP_ID, project_id="acme-prod"), id="function-secret-install-project"
        ),
        pytest.param(
            {
                "secret_volumes": [
                    {"mount_path": "/s", "secret": _OWN_GCP_ID, "versions": [{"version": "1", "path": "t"}]}
                ]
            },
            id="function-secret-volume",
        ),
        pytest.param(
            {
                "connect_clusters": [
                    {"id": "c", "secret_paths": [f"projects/acme-prod/secrets/{_OWN_GCP_ID}/versions/7"]}
                ]
            },
            id="connect-secret-path",
        ),
    ],
)
def test_accepts_a_google_secret_in_the_owners_namespace(config):
    assert_config_secret_refs_scoped(config, owner=OWNER, cluster=_GCP_CLUSTER)


@pytest.mark.parametrize(
    ("config", "message"),
    [
        pytest.param(
            _function_secret(_OWN_GCP_ID, project_id="victim-project"),
            "install project",
            id="another-project",
        ),
        pytest.param(
            _function_secret(_OWN_GCP_ID, projectId="victim-project"),
            "install project",
            id="another-project-json-name",
        ),
        # A driver sends the entry in its JSON spelling, where one spelling
        # would win while the other was checked.
        pytest.param(
            _function_secret(_OWN_GCP_ID, project_id=None, projectId="victim-project"),
            "as one field",
            id="project-spelled-twice",
        ),
        pytest.param(
            {
                "service_raw_fields": {
                    "secret_environment_variables": [
                        {"key": "T", "secret": _OWN_GCP_ID, "version": "1", "project_id": "victim-project"}
                    ]
                }
            },
            "install project",
            id="proto-named-raw-field",
        ),
        pytest.param(
            _function_secret(_OWN_GCP_ID, project_id="123456789012"), "install project", id="project-number"
        ),
        pytest.param(_function_secret(f"astrolift-services-{G}-{OTHER}-db"), APP, id="another-apps-secret"),
        pytest.param(_function_secret(f"astrolift-services-{OTHER}-{APP}-db"), APP, id="another-orgs-secret"),
        pytest.param(_function_secret(f"astrolift-agents-{G}-admin-token"), APP, id="an-agent-secret"),
        pytest.param(
            _function_secret("astrolift-cloudsql-orders-master"), APP, id="a-minted-database-secret"
        ),
        pytest.param(_function_secret(f"astrolift-services-{G}-{APP}-"), APP, id="the-owner-root-itself"),
        pytest.param(
            _function_secret(f"projects/acme-prod/secrets/{_OWN_GCP_ID}"), APP, id="a-resource-path-not-an-id"
        ),
        pytest.param(_function_secret({"name": _OWN_GCP_ID}), APP, id="not-a-string"),
        pytest.param(
            {
                "connect_clusters": [
                    {"id": "c", "secret_paths": [f"projects/victim/secrets/{_OWN_GCP_ID}/versions/1"]}
                ]
            },
            "install project",
            id="connect-path-in-another-project",
        ),
        pytest.param(
            {"connect_clusters": [{"id": "c", "secret_paths": [_OWN_GCP_ID]}]},
            APP,
            id="connect-path-not-a-path",
        ),
    ],
)
def test_refuses_a_google_secret_outside_the_owners_namespace(config, message):
    from astrolift_dispatch.agent_secrets import SecretRefNamespaceError

    with pytest.raises(SecretRefNamespaceError, match=message):
        assert_config_secret_refs_scoped(config, owner=OWNER, cluster=_GCP_CLUSTER)


def test_a_google_secret_id_follows_the_installs_secret_id_prefix():
    """The id is mapped by the GCP secrets driver's own ``secret_id_for``
    with the prefix the install configured, not a copy of either."""
    from astrolift_dispatch.agent_secrets import SecretRefNamespaceError

    cluster = SimpleNamespace(
        **{**vars(_GCP_CLUSTER), "provider_config": {"project_id": "acme-prod", "secret_id_prefix": "smd"}}
    )

    assert_config_secret_refs_scoped(
        _function_secret(f"smd-services-{G}-{APP}-api-token"), owner=OWNER, cluster=cluster
    )
    with pytest.raises(SecretRefNamespaceError, match=f"smd-services-{G}-{APP}-"):
        assert_config_secret_refs_scoped(_function_secret(_OWN_GCP_ID), owner=OWNER, cluster=cluster)


def test_a_google_secret_on_a_cluster_without_a_gcp_store_is_refused():
    from astrolift_dispatch.agent_secrets import SecretRefNamespaceError

    aws = SimpleNamespace(**{**vars(_GCP_CLUSTER), "provider_plugin": SimpleNamespace(slug="aws")})
    with pytest.raises(SecretRefNamespaceError, match="no GCP secrets project"):
        assert_config_secret_refs_scoped(_function_secret(_OWN_GCP_ID), owner=OWNER, cluster=aws)


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
    return SimpleNamespace(org=org, team=team, project=project, cluster=cluster, app=app, env=env)


def _second_app(world):
    """Another app of the same org, on the same cluster: its service secrets
    are its own, not the first app's to name."""
    other = RegisteredApp.objects.create(
        organization=world.org,
        team=world.team,
        project=world.project,
        name="Billing",
        slug="billing-1921",
        provisioning_status="ready",
    )
    AppEnvironment.objects.create(registered_app=other, name="production", tenant_cluster=world.cluster)
    return other


def _app_ns(world, name, app=None):
    return f"services/{world.org.guid}/{(app or world.app).guid}/{name}"


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
    assert f"services/{world.org.guid}/{world.app.guid}/" in result.errors[0].message
    assert not ManagedService.objects.filter(registered_app=world.app, name="events").exists()
    start.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["another-app", "an-agent-secret", "the-org-level-root"])
def test_provision_refuses_a_config_naming_another_owners_secret_in_the_same_org(permission_resolver, target):
    """APP_UPDATE on one app must not reach another app's service secret, or an
    agent secret, by having the driver copy it into this app's binding."""
    world = _scaffold()
    other = _second_app(world)
    permission_resolver.grant(Permission.APP_UPDATE)
    ref = {
        "another-app": _app_ns(world, "db-password", app=other),
        "an-agent-secret": f"agents/{world.org.guid}/admin-token",
        "the-org-level-root": f"services/{world.org.guid}/db-password",
    }[target]

    result, start = _provision(world, {"password_secret_ref": ref})

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "config"
    assert _app_ns(world, "") in result.errors[0].message
    assert not ManagedService.objects.filter(registered_app=world.app, name="events").exists()
    start.assert_not_called()


@pytest.mark.django_db
def test_provision_accepts_a_config_naming_a_secret_inside_its_apps_namespace(permission_resolver):
    world = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    ref = f"{_app_ns(world, 'kafka')}#password"

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


def _provision_project_cache(world, name, ref):
    with _ctx(world.org), patch("astrolift_workflows.client.start_workflow") as start:
        result = ServicesMutation().provision_project_managed_service(
            _info(),
            input=ProvisionProjectManagedServiceInput(
                project_id=GUID(str(world.project.guid)),
                cluster_id=GUID(str(world.cluster.guid)),
                kind="redis",
                name=name,
                config={"auth_mode": "external", "password_secret_ref": ref},
            ),
        )
    return result, start


@pytest.mark.django_db
def test_a_project_service_names_the_projects_namespace_not_one_of_its_apps(permission_resolver):
    world = _scaffold()
    permission_resolver.grant(Permission.PROJECT_UPDATE)
    project_ref = f"services/{world.org.guid}/{world.project.guid}/cache-password"

    refused, refused_start = _provision_project_cache(world, "cache", _app_ns(world, "cache-password"))
    accepted, accepted_start = _provision_project_cache(world, "cache-2", project_ref)

    assert refused.ok is False
    assert refused.errors[0].code == ErrorCode.VALIDATION.value
    assert f"services/{world.org.guid}/{world.project.guid}/" in refused.errors[0].message
    refused_start.assert_not_called()
    assert accepted.ok is True, accepted.errors
    assert (
        ManagedService.objects.get(project=world.project, name="cache-2").config["password_secret_ref"]
        == project_ref
    )
    accepted_start.assert_called_once()


@pytest.mark.django_db
def test_provision_holds_a_cloud_function_secret_to_the_apps_google_secret_id(permission_resolver):
    """Cloud Functions hands ``secret_environment[].secret`` to Google, which
    reads it with the function's identity: the id must be the one the GCP
    secrets driver gives this app's namespace, in the install project."""
    world = _scaffold(plugin_slug="gcp")
    world.cluster.provider_config = {"project_id": "acme-prod", "region": "us-central1"}
    world.cluster.save(update_fields=["provider_config"])
    other = _second_app(world)
    permission_resolver.grant(Permission.APP_UPDATE)

    def provision(name, secret, **extra):
        config = {"secret_environment": [{"key": "API_TOKEN", "secret": secret, "version": "1", **extra}]}
        with _ctx(world.org), patch("astrolift_workflows.client.start_workflow") as start:
            result = ServicesMutation().provision_managed_service(
                _info(),
                input=ProvisionManagedServiceInput(
                    app_slug=world.app.slug,
                    environment_name=world.env.name,
                    kind="faas",
                    name=name,
                    variant="cloud_functions_gen2",
                    config=config,
                ),
            )
        return result, start

    own = f"astrolift-services-{world.org.guid}-{world.app.guid}-api-token"
    another_app, another_app_start = provision(
        "fn-a", f"astrolift-services-{world.org.guid}-{other.guid}-api-token"
    )
    another_project, _ = provision("fn-b", own, project_id="victim-project")
    accepted, accepted_start = provision("fn-c", own, project_id="acme-prod")

    assert another_app.ok is False
    assert another_app.errors[0].field == "config"
    assert f"astrolift-services-{world.org.guid}-{world.app.guid}-" in another_app.errors[0].message
    another_app_start.assert_not_called()
    assert another_project.ok is False
    assert "install project 'acme-prod'" in another_project.errors[0].message
    assert accepted.ok is True, accepted.errors
    accepted_start.assert_called_once()


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
def test_update_refuses_an_editable_field_naming_another_apps_secret(permission_resolver):
    world = _scaffold(plugin_slug="aws")
    other = _second_app(world)
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = _active_service(world, kind="event_stream", variant="msk")

    result, start = _update(world, svc, {"password_secret_ref": _app_ns(world, "kafka", app=other)})

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert _app_ns(world, "") in result.errors[0].message
    svc.refresh_from_db()
    assert svc.config == {}
    start.assert_not_called()


@pytest.mark.django_db
def test_update_accepts_an_editable_field_naming_a_secret_inside_its_apps_namespace(permission_resolver):
    world = _scaffold(plugin_slug="aws")
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = _active_service(world, kind="event_stream", variant="msk")
    ref = f"{_app_ns(world, 'kafka')}#password"

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

    with pytest.raises(ValueError, match=f"services/{world.org.guid}/{world.app.guid}/"):
        reconcile_managed_services(world.app, (service,))

    assert not ManagedService.objects.filter(registered_app=world.app).exists()


@pytest.mark.django_db
def test_manifest_persist_holds_each_service_to_its_owners_namespace():
    """An app-scoped service names the app's namespace and a project-scoped
    one the project's; neither may name the other's."""
    from astrolift_manifest.persist import reconcile_managed_services
    from astrolift_manifest.types import ManagedServiceManifest

    world = _scaffold()
    project_ns = f"services/{world.org.guid}/{world.project.guid}"

    def persist(scope, ref):
        service = ManagedServiceManifest(
            kind="event_stream",
            name=f"events-{scope}",
            environment="production",
            owner_scope=scope,
            config={"password_secret_ref": ref},
        )
        return reconcile_managed_services(world.app, (service,))

    with pytest.raises(ValueError, match=f"{project_ns}/"):
        persist("project", _app_ns(world, "kafka"))
    with pytest.raises(ValueError, match=_app_ns(world, "")):
        persist("app", f"{project_ns}/kafka")
    assert not ManagedService.objects.filter(project=world.project).exists()

    from astrolift_manifest.persist import allow_project_attach

    with allow_project_attach():
        persist("project", f"{project_ns}/kafka")

    assert (
        ManagedService.objects.get(project=world.project, name="events-project").config["password_secret_ref"]
        == f"{project_ns}/kafka"
    )
