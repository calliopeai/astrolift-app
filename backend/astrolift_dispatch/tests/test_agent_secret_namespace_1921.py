"""Agent secret refs stay inside their organization's secret namespace (#1921).

An org without its own cluster shares the install's secret store with every
other org, and every driver files a relative ref under one install-wide root:
``managed/rds-orders/url`` and ``astrolift/managed/rds-orders/url`` name the
same AWS secret, another tenant's database URL. A location an operator or a
manifest typed must therefore sit under ``agents/<org guid>/`` or
``agent-bundles/<org guid>/``.

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
        f"agent-bundles/{G}/shared-defaults",
        f"agents/{G}/bundle#API_KEY",
        f"arn:aws:secretsmanager:us-west-2:123456789012:secret:astrolift/agents/{G}/gh-AbCdEf",
        f"arn:aws:secretsmanager:us-west-2:123456789012:secret:agents/{G}/gh-AbCdEf",
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


def test_spawn_keeps_resolving_app_bindings_that_share_a_typed_refs_name(org, monkeypatch):
    """An app's managed-service binding is derived by the platform and lives
    under the service's own root. Only the spec's own ref is checked, even when
    both carry the same env var (the spec's ref wins, as before)."""
    import astrolift_dispatch.agent_secrets as agent_secrets

    own_ref = f"agents/{org.guid}/db"
    spec = _spec(org, [{"env_var": "DATABASE_URL", "uri": own_ref}])
    monkeypatch.setattr(
        agent_secrets,
        "app_managed_service_secret_refs",
        lambda _workload: [
            {"env_var": "DATABASE_URL", "uri": "astrolift/managed/app-db/url"},
            {"env_var": "OBJECT_STORE_KEY", "uri": "astrolift/managed/app-bucket/key"},
        ],
    )
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

    manifest = _resolve(spec, workload=object())

    assert manifest["stringData"] == {"DATABASE_URL": "postgres://own", "OBJECT_STORE_KEY": "bucket-key"}
