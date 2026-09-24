"""Agent secret refs stay inside their organization's secret namespace (#1921).

An org without its own cluster shares the install's secret store with every
other org, and every driver files a relative ref under one install-wide root:
``managed/rds-orders/url`` and ``astrolift/managed/rds-orders/url`` name the
same AWS secret, another tenant's database URL. A location an operator or a
manifest typed must therefore sit under ``agents/<org guid>/``. The other org
roots hold other things: ``agent-bundles/`` holds bundle payloads and
``services/<org guid>/<owner guid>/`` one app's or project's managed-service
secrets, so a typed agent ref naming either is refused. A managed-service
binding the agent inherits resolves only if the driver minted it or it sits in
its service's namespace.

The predicate is pinned directly. The spawn and agent-box preflight
(``resolve_task_secret_manifest``) is pinned on real rows with an in-memory
store that records every read, including the locations that stay exempt
because the platform minted them.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_dispatch.agent_secrets import AgentSecretResolutionError, resolve_task_secret_manifest

G = "0192f3c4-1111-7aaa-8bbb-111111111111"
OTHER = "0192f3c4-2222-7ccc-8ddd-222222222222"
ORG = SimpleNamespace(guid=uuid.UUID(G))


# ---- the predicate ----------------------------------------------------------


@pytest.mark.parametrize(
    "uri",
    [
        f"agents/{G}/github-token",
        f"agents/{G}/media/replicate",
        f"sm:agents/{G}/gh",
        f"ssm:agents/{G}/gh",
        f"/agents/{G}/gh",
        f"sm:/agents/{G}/gh",
        f"astrolift/agents/{G}/gh",
        f"secret://agents/{G}/gh",
        f"secret://sm:agents/{G}/gh",
        f"  secret://agents/{G}/gh  ",
        f"agents/{G}/bundle#API_KEY",
    ],
)
def test_accepts_a_location_inside_the_org_namespace(uri):
    from astrolift_dispatch.agent_secrets import assert_org_scoped_secret_ref

    assert_org_scoped_secret_ref(uri, organization=ORG)


@pytest.mark.parametrize(
    "uri",
    [
        pytest.param("github-token", id="bare-name-lands-in-the-shared-install-root"),
        pytest.param("sm:github-token", id="bare-name-with-scheme"),
        pytest.param("managed/rds-orders/url", id="relative-spelling-of-another-tenants-managed-secret"),
        pytest.param("astrolift/managed/rds-orders/url", id="absolute-spelling-of-the-same-secret"),
        pytest.param("ssm:managed/rds-orders/url", id="ssm-relative"),
        pytest.param("agents/calliope/anthropic", id="org-slug-is-not-an-owner"),
        pytest.param(f"agents/{OTHER}/gh", id="another-org"),
        pytest.param(f"astrolift/agents/{OTHER}/gh", id="another-org-absolute"),
        pytest.param(f"agent-bundles/{OTHER}/shared", id="another-orgs-bundle"),
        # setAgentSecretValue on this ref would replace the bundle's keys with
        # {"value": ...}.
        pytest.param(f"agent-bundles/{G}/shared-defaults", id="own-bundle-location-holds-a-bundle"),
        pytest.param(f"agents/{G}/../agent-bundles/{G}/shared", id="dot-dot-into-the-bundle-root"),
        # One app's managed-service secrets, which APP_UPDATE on that app does
        # not grant an env-spec editor.
        pytest.param(f"services/{G}/{OTHER}/kafka#password", id="a-managed-service-secret"),
        pytest.param(f"services/{G}/kafka#password", id="the-org-level-services-root"),
        # The canonical form strips the scheme once and the check judges what
        # is left, which still carries one: exactly what the store would get.
        pytest.param(f"secret://secret://agents/{G}/gh", id="scheme-twice"),
        pytest.param(f"sm:secret://agents/{G}/gh", id="scheme-after-the-store-scheme"),
        pytest.param(f"project-bundles/{G}/{OTHER}/jira", id="project-bundle-bypasses-membership"),
        pytest.param(f"agents/{G}-extra/gh", id="guid-prefix-collision"),
        pytest.param(f"agents/{G.upper()}/gh", id="guid-case"),
        pytest.param(f"agents/{G}", id="namespace-root-itself"),
        pytest.param(f"agents/{G}/../managed/rds-orders/url", id="dot-dot"),
        pytest.param(f"agents/{G}/./gh", id="dot"),
        pytest.param(f"agents/{G}//gh", id="empty-segment"),
        pytest.param(f"agents/{G}/a%2F..", id="percent-encoding"),
        pytest.param(f"agents/{G}/gh#", id="empty-field"),
        pytest.param(f"agents/{G}/gh#a#b", id="two-fields"),
        pytest.param(
            "arn:aws:secretsmanager:us-west-2:123456789012:secret:astrolift/managed/rds-orders/master-AbCdEf",
            id="arn-of-another-tenants-secret",
        ),
        pytest.param(
            "sm:arn:aws:secretsmanager:us-west-2:123456789012:secret:astrolift/managed/rds-orders/master",
            id="scheme-prefixed-arn-the-aws-driver-passes-through",
        ),
        pytest.param(
            "/arn:aws:secretsmanager:us-west-2:123456789012:secret:astrolift/managed/rds-orders/master",
            id="slash-prefixed-arn-the-aws-driver-passes-through",
        ),
        pytest.param(f"arn:aws:ssm:us-west-2:123456789012:parameter/astrolift/agents/{G}/gh", id="ssm-arn"),
        # An ARN names a secret in whatever account and region it spells; the
        # relative form always resolves in the driver's own store.
        pytest.param(
            f"arn:aws:secretsmanager:us-west-2:123456789012:secret:astrolift/agents/{G}/gh-AbCdEf",
            id="arn-even-of-an-own-name",
        ),
        pytest.param(
            f"arn:aws:secretsmanager:us-west-2:123456789012:secret:agents/{G}/gh-AbCdEf",
            id="arn-of-a-relative-own-name",
        ),
        pytest.param(
            "azure-kv://platform.vault.azure.net/secrets/astrolift-managed-orders-master",
            id="key-vault-explicit-reference",
        ),
        pytest.param("astrolift-managed-orders-master", id="key-vault-physical-name"),
        pytest.param("", id="empty"),
    ],
)
def test_rejects_a_location_outside_the_org_namespace(uri):
    from astrolift_dispatch.agent_secrets import SecretRefNamespaceError, assert_org_scoped_secret_ref

    with pytest.raises(SecretRefNamespaceError, match=f"agents/{G}/"):
        assert_org_scoped_secret_ref(uri, organization=ORG)


@pytest.mark.parametrize(
    ("backend_ref", "message"),
    [
        pytest.param(f"secret://agent-bundles/{G}/shared", "secret:// reference", id="reference-scheme"),
        pytest.param(f"agents/{G}/shared", "must live under agent-bundles/", id="not-the-bundle-root"),
        pytest.param(f"services/{G}/shared", "must live under agent-bundles/", id="services-root"),
        pytest.param(f"agent-bundles/{OTHER}/shared", "must live under agent-bundles/", id="another-org"),
    ],
)
def test_a_bundle_location_is_a_store_path_under_the_bundle_root(backend_ref, message):
    """A bundle location is handed to the driver as it is, so it names a
    store path, never a ``secret://`` reference, and only under the one root
    whose locations are bundles."""
    from astrolift_dispatch.agent_secrets import SecretRefNamespaceError, assert_org_scoped_bundle_ref

    with pytest.raises(SecretRefNamespaceError, match=message):
        assert_org_scoped_bundle_ref(backend_ref, organization=ORG)
    assert_org_scoped_bundle_ref(f"agent-bundles/{G}/shared", organization=ORG)


# ---- spawn / agent-box preflight ---------------------------------------------


class _Store:
    """In-memory SecretsBackend that records every path it is asked for."""

    def __init__(self, store):
        self.store = dict(store)
        self.reads: list[str] = []

    def get(self, path):
        self.reads.append(path)
        return self.store.get(path)


def _install(monkeypatch, store):
    import core.app_deploy as app_deploy

    monkeypatch.setattr(app_deploy, "driver_for_capability", lambda _cluster, _capability: store)
    return store


def _resolve(spec, **kwargs):
    return resolve_task_secret_manifest(
        cluster=object(), spec=spec, secret_name="task-secrets", namespace="ns", task_guid="t", **kwargs
    )


@pytest.fixture
def org(db):
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Acme", slug="acme-1921")


@pytest.fixture
def other_org(db):
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Globex", slug="globex-1921")


def _spec(org, refs=()):
    from astrolift_agents.models import AgentEnvironmentSpec

    return AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Claude Dev",
        slug="claude-dev",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        secret_refs=list(refs),
    )


def test_spawn_refuses_the_relative_spelling_of_another_tenants_secret(org, monkeypatch):
    """The issue's exploit with the install root dropped: the AWS driver maps
    it straight onto ``astrolift/managed/rds-orders/url``."""
    spec = _spec(org, [{"env_var": "DATABASE_URL", "uri": "managed/rds-orders/url"}])
    store = _install(monkeypatch, _Store({"managed/rds-orders/url": {"value": "postgres://victim"}}))

    with pytest.raises(AgentSecretResolutionError) as caught:
        _resolve(spec)

    assert "DATABASE_URL" in str(caught.value)
    assert f"agents/{org.guid}/" in str(caught.value)
    assert store.reads == []


def test_spawn_refuses_a_stored_binding_override_into_another_org(org, other_org, monkeypatch):
    from astrolift_agents.models.agent_secret_binding import AgentSecretBindingOverride

    spec = _spec(org)
    victim = f"astrolift/agents/{other_org.guid}/gh"
    AgentSecretBindingOverride.objects.create(environment_spec=spec, env_var="GITHUB_TOKEN", uri=victim)
    store = _install(monkeypatch, _Store({victim: {"value": "ghp_victim"}}))

    with pytest.raises(AgentSecretResolutionError, match="GITHUB_TOKEN"):
        _resolve(spec)

    assert store.reads == []


def test_spawn_refuses_a_planted_org_bundle(org, monkeypatch):
    """A bundle whose free-text backendRef names another tenant's secret
    (stored before write-time validation existed) is never read."""
    from astrolift_agents.models.agent_secret_binding import AgentSecretBundleRef
    from astrolift_services.models import SecretBundle

    spec = _spec(org)
    bundle = SecretBundle.objects.create(
        organization=org, name="Planted", slug="planted", backend_ref="managed/rds-orders/master"
    )
    AgentSecretBundleRef.objects.create(environment_spec=spec, secret_bundle=bundle, environment="default")
    store = _install(monkeypatch, _Store({"managed/rds-orders/master": {"PASSWORD": "victim-pw"}}))

    with pytest.raises(AgentSecretResolutionError, match="bundle:planted"):
        _resolve(spec)

    assert store.reads == []


def test_spawn_resolves_refs_and_bundles_inside_the_org_namespace(org, monkeypatch):
    from astrolift_agents.models.agent_secret_binding import AgentSecretBundleRef
    from astrolift_services.models import SecretBundle

    own_ref = f"sm:agents/{org.guid}/gh"
    own_bundle = f"agent-bundles/{org.guid}/shared"
    spec = _spec(org, [{"env_var": "GITHUB_TOKEN", "uri": own_ref}])
    bundle = SecretBundle.objects.create(
        organization=org, name="Shared", slug="shared", backend_ref=own_bundle
    )
    AgentSecretBundleRef.objects.create(environment_spec=spec, secret_bundle=bundle, environment="default")
    _install(monkeypatch, _Store({own_ref: {"value": "ghp_own"}, own_bundle: {"API_KEY": "k"}}))

    manifest = _resolve(spec)

    assert manifest["stringData"] == {"GITHUB_TOKEN": "ghp_own", "API_KEY": "k"}


class _AwsSecretsManager:
    """Enough of boto3's Secrets Manager client for ``AWSSecretsBackend.get``,
    keeping the service's name rule: a name outside ``[A-Za-z0-9/_+=.@-]`` is a
    ``ValidationException``, which is what ``astrolift/secret://...`` gets."""

    class exceptions:  # noqa: N801 - boto3's attribute name
        class ResourceNotFoundException(Exception):
            pass

    def __init__(self, secrets):
        self.secrets = dict(secrets)
        self.secret_ids: list[str] = []

    def get_secret_value(self, *, SecretId):  # noqa: N803 - boto3's keyword
        import re

        self.secret_ids.append(SecretId)
        if not re.fullmatch(r"[A-Za-z0-9/_+=.@-]+", SecretId):
            error = RuntimeError("Invalid name")
            error.response = {"Error": {"Code": "ValidationException", "Message": "Invalid name"}}
            raise error
        if SecretId not in self.secrets:
            raise self.exceptions.ResourceNotFoundException(SecretId)
        return {"SecretString": self.secrets[SecretId]}


class NotFound(Exception):  # noqa: N818 - google.api_core's class name, which the driver matches
    pass


class _GcpSecretManager:
    """Enough of the Secret Manager client for ``GCPSecretsBackend.get``."""

    def __init__(self, secrets):
        self.secrets = dict(secrets)
        self.names: list[str] = []

    def access_secret_version(self, *, name):
        self.names.append(name)
        if name not in self.secrets:
            raise NotFound(name)
        return SimpleNamespace(payload=SimpleNamespace(data=self.secrets[name].encode("utf-8")))


@pytest.mark.parametrize("spelling", ["agents/{guid}/github", "secret://agents/{guid}/github"])
def test_a_secret_reference_resolves_to_the_same_aws_secret_as_its_location(org, monkeypatch, spelling):
    """Company-agent manifests write ``secret://agents/<org guid>/<name>``.
    The scheme is stripped once, so the check and the real AWS driver both see
    the relative location: Secrets Manager is asked for
    ``astrolift/agents/<org guid>/github``, never ``astrolift/secret://...``."""
    from aws.secrets import AWSSecretsBackend, SecretsConfig

    name = f"astrolift/agents/{org.guid}/github"
    client = _AwsSecretsManager({name: "ghp_own"})
    _install(
        monkeypatch,
        AWSSecretsBackend(config=SecretsConfig(region="us-west-2"), sm_client=client, ssm_client=object()),
    )
    spec = _spec(org, [{"env_var": "GITHUB_TOKEN", "uri": spelling.format(guid=org.guid)}])

    manifest = _resolve(spec)

    assert manifest["stringData"] == {"GITHUB_TOKEN": "ghp_own"}
    assert client.secret_ids == [name]


@pytest.mark.parametrize("spelling", ["agents/{guid}/github", "secret://agents/{guid}/github"])
def test_a_secret_reference_resolves_to_the_same_gcp_secret_as_its_location(org, monkeypatch, spelling):
    """Unstripped, the GCP driver would map the scheme into the id
    (``astrolift-secret---agents-...``), a different secret."""
    from gcp.secrets import GCPSecretsBackend, GCPSecretsConfig

    name = f"projects/acme-prod/secrets/astrolift-agents-{org.guid}-github/versions/latest"
    client = _GcpSecretManager({name: "ghp_own"})
    _install(monkeypatch, GCPSecretsBackend(config=GCPSecretsConfig(project_id="acme-prod", client=client)))
    spec = _spec(org, [{"env_var": "GITHUB_TOKEN", "uri": spelling.format(guid=org.guid)}])

    manifest = _resolve(spec)

    assert manifest["stringData"] == {"GITHUB_TOKEN": "ghp_own"}
    assert client.names == [name]


def test_a_binding_override_with_the_scheme_resolves_to_its_location(org, monkeypatch):
    from astrolift_agents.models.agent_secret_binding import AgentSecretBindingOverride

    spec = _spec(org)
    AgentSecretBindingOverride.objects.create(
        environment_spec=spec, env_var="GITHUB_TOKEN", uri=f"secret://agents/{org.guid}/github"
    )
    store = _install(monkeypatch, _Store({f"agents/{org.guid}/github": {"value": "ghp_own"}}))

    assert _resolve(spec)["stringData"] == {"GITHUB_TOKEN": "ghp_own"}
    assert store.reads == [f"agents/{org.guid}/github"]


@pytest.mark.parametrize(
    "location",
    [
        pytest.param("agent-bundles/{guid}/shared", id="a-bundle-payload"),
        pytest.param("services/{guid}/{other}/db-password", id="a-managed-service-secret"),
        pytest.param("secret://secret://agents/{guid}/github", id="a-scheme-left-after-canonicalizing"),
    ],
)
def test_spawn_refuses_a_stored_typed_ref_outside_the_agent_root(org, monkeypatch, location):
    """A row written before typed refs were narrowed to ``agents/`` is never
    read: a bundle's payload and a service's secret belong to their holders."""
    uri = location.format(guid=org.guid, other=OTHER)
    spec = _spec(org, [{"env_var": "LEAKED", "uri": uri}])
    store = _install(monkeypatch, _Store({uri: {"value": "not-yours"}}))

    with pytest.raises(AgentSecretResolutionError, match="LEAKED"):
        _resolve(spec)

    assert store.reads == []


def test_spawn_resolves_a_project_bundle_the_platform_minted(org, monkeypatch):
    """Project bundles live at ``project-bundles/<org>/<project>/...``,
    outside the agent roots, and are exempt: the platform pins that path."""
    from astrolift_agents.models.agent_secret_binding import AgentSecretBundleRef
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Project, Team
    from astrolift_services.models import SecretBundle

    team = Team.objects.create(organization=org, name="Eng", slug="eng-1921")
    project = Project.objects.create(organization=org, team=team, name="Agents", slug="agents-1921")
    ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="K8s 1921", slug="k8s-1921", plugin_version="1.0.0")]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        provider_plugin=ProviderPlugin.objects.get(slug="k8s-1921"),
        name="Runtime",
        slug="runtime-1921",
        endpoint="https://cluster.example.com",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    location = f"project-bundles/{org.guid}/{project.guid}/jira"
    bundle = SecretBundle.objects.create(
        organization=org,
        project=project,
        tenant_cluster=cluster,
        name="Jira",
        slug="jira",
        backend_ref=location,
    )
    spec = _spec(org)
    AgentSecretBundleRef.objects.create(environment_spec=spec, secret_bundle=bundle, environment="default")
    _install(monkeypatch, _Store({location: {"JIRA_TOKEN": "j"}}))

    assert _resolve(spec)["stringData"] == {"JIRA_TOKEN": "j"}


def _app_with_service(org, *, config=None):
    """An app with one environment, an agent workload, and a managed service
    whose config is ``config``."""
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Project, Team
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp, Workload
    from astrolift_services.models import ManagedService

    team = Team.objects.create(organization=org, name="Eng", slug="eng-app-1921")
    project = Project.objects.create(organization=org, team=team, name="P", slug="p-app-1921")
    ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="K8s app 1921", slug="k8s-app-1921", plugin_version="1.0.0")]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        provider_plugin=ProviderPlugin.objects.get(slug="k8s-app-1921"),
        name="Prod",
        slug="prod-app-1921",
        endpoint="https://cluster.example.com",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Orders",
        slug="orders-1921",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="production")
    service = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind="event_stream",
        name="events",
        config=dict(config or {}),
        backend_ref="event_stream/events",
        status="active",
    )
    agent = Workload.objects.create(
        registered_app=app, name="Brief", slug="orders-brief", kind=Workload.Kind.AGENT
    )
    return service, agent


def _bind(service, env_key, ref):
    from astrolift_services.models import ManagedServiceBinding

    ManagedServiceBinding.objects.create(
        managed_service=service, env_key=env_key, env_value_ref=ref, is_secret=True
    )


def test_spawn_keeps_resolving_app_bindings_that_share_a_typed_refs_name(org, monkeypatch):
    """An app's managed-service binding the driver minted resolves where the
    driver put it. Only the spec's own ref is checked as typed, even when both
    carry the same env var (the spec's ref wins, as before)."""
    own_ref = f"agents/{org.guid}/db"
    spec = _spec(org, [{"env_var": "DATABASE_URL", "uri": own_ref}])
    service, agent = _app_with_service(org)
    _bind(service, "DATABASE_URL", "astrolift/managed/app-db/url")
    _bind(service, "OBJECT_STORE_KEY", "astrolift/managed/app-bucket/key")
    _install(
        monkeypatch,
        _Store(
            {
                "astrolift/managed/app-db/url": {"value": "postgres://app"},
                "astrolift/managed/app-bucket/key": {"value": "bucket-key"},
                own_ref: {"value": "postgres://own"},
            }
        ),
    )

    manifest = _resolve(spec, workload=agent)

    assert manifest["stringData"] == {"DATABASE_URL": "postgres://own", "OBJECT_STORE_KEY": "bucket-key"}


def test_spawn_refuses_an_app_binding_copied_from_a_config_outside_the_org(org, monkeypatch):
    """The binding exemption is for refs the platform minted. A driver copies
    ``password_secret_ref`` into the row, so a service configured with another
    tenant's secret must not carry it into the agent's pod."""
    spec = _spec(org)
    service, agent = _app_with_service(org, config={"password_secret_ref": "managed/rds-orders/url"})
    _bind(service, "EVENT_STREAM_PASSWORD", "managed/rds-orders/url")
    store = _install(monkeypatch, _Store({"managed/rds-orders/url": {"value": "postgres://victim"}}))

    with pytest.raises(AgentSecretResolutionError, match="EVENT_STREAM_PASSWORD"):
        _resolve(spec, workload=agent)

    assert store.reads == []


def test_spawn_refuses_a_project_binding_copied_from_a_config_outside_the_org(org, monkeypatch):
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Project, Team
    from astrolift_services.models import ManagedService, ManagedServiceAttachment

    team = Team.objects.create(organization=org, name="Eng", slug="eng-proj-1921")
    project = Project.objects.create(organization=org, team=team, name="P", slug="p-proj-1921")
    ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="K8s proj 1921", slug="k8s-proj-1921", plugin_version="1.0.0")]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        provider_plugin=ProviderPlugin.objects.get(slug="k8s-proj-1921"),
        name="Shared",
        slug="shared-proj-1921",
        endpoint="https://cluster.example.com",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
    )
    service = ManagedService.objects.create(
        project=project,
        tenant_cluster=cluster,
        kind="redis",
        name="cache",
        config={"auth_mode": "external", "password_secret_ref": "managed/rds-orders/url"},
        backend_ref="redis/cache",
        status="active",
    )
    _bind(service, "REDIS_PASSWORD", "managed/rds-orders/url")
    spec = _spec(org)
    ManagedServiceAttachment.objects.create(managed_service=service, agent_environment_spec=spec)
    store = _install(monkeypatch, _Store({"managed/rds-orders/url": {"value": "victim-pw"}}))

    with pytest.raises(AgentSecretResolutionError, match="REDIS_PASSWORD"):
        _resolve(spec)

    assert store.reads == []
