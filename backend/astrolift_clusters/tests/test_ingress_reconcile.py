"""Tests for the cluster Ingress auth-gate reconcile (#851).

Two layers exercised:

1. ``core.ingress_reconcile`` — the annotation-delta builder and the
   per-namespace patch sweep, against a recording fake kubernetes
   client. Asserts the enabled keys match what ``ALBIngressDriver``
   renders, the disabled path sets every auth key to ``None`` (so the
   strategic-merge patch deletes it), and that skip / error counting
   behaves.
2. ``reconcileClusterIngresses`` mutation resolver — permission gate,
   NOT_FOUND, the non-alb PRECONDITION guard, and the happy-path
   envelope carrying the reconciled count.

The cluster driver is faked by monkeypatching
``core.cluster_management._driver_for_cluster`` to return a stand-in
that hands back a recording client — the same shape the live-ops tests
(``astrolift_lifecycle.tests.test_restart_scale_workload``) use for the
patch path, so the reconcile exercises the real
``_driver_for_cluster`` → ``driver._k8s`` → client call chain without a
provider plugin loaded.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, TenantCluster
from astrolift_clusters.schema.mutations import (
    ClustersMutation,
    ReconcileClusterIngressesInput,
)
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from core.ingress_reconcile import (
    AUTH_ANNOTATION_KEYS,
    MANAGED_SUBDOMAIN_SELECTOR,
    _auth_annotation_patch,
    reconcile_cluster_ingresses,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

AUTH_CONFIG = {
    "user_pool_arn": "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_abc",
    "user_pool_client_id": "client-abc",
    "user_pool_domain": "acme-auth",
}


# ---- fixtures -----------------------------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Suppress the User -> Profile -> OpenSearch indexing chain that
    fires on Organization/User create (same pattern the management-
    mutations test uses)."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def team(org):
    return Team.objects.create(organization=org, name="Platform", slug=f"plat-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def plugin():
    # Seeded directly; the plugin row is scaffolding for this test.
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="aws",
                slug="aws",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return p


@pytest.fixture
def cluster(org, plugin):
    return TenantCluster.objects.create(
        organization=org,
        slug=f"c-{uuid.uuid4().hex[:6]}",
        name="dev",
        provider_plugin=plugin,
        provider_config={},
        region="us-west-2",
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        ingress_class="alb",
        alb_auth_config=AUTH_CONFIG,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


@pytest.fixture
def domain(org):
    return ManagedDomain.objects.create(organization=org, zone="apps.example.com", dns_driver="route53")


def _make_app(org, team, slug: str) -> RegisteredApp:
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        name=slug,
        slug=slug,
        provisioning_status="ready",
    )


def _bind_env(app: RegisteredApp, cluster: TenantCluster, *, domain: ManagedDomain | None) -> AppEnvironment:
    return AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        managed_domain=domain,
        name="prod",
        required_approvals=0,
    )


# ---- recording fake k8s client + driver ---------------------------


class _FakeIngressItem:
    def __init__(self, name: str) -> None:
        self.metadata = SimpleNamespace(name=name)


class _FakeListing:
    def __init__(self, items: list[Any]) -> None:
        self.items = items


class _RecordingK8sClient:
    """Stand-in for ``KubernetesDynamicClient`` — records the
    list/patch calls and returns the configured Ingress items per
    namespace. Behaviour switches set per-test."""

    def __init__(
        self,
        *,
        ingresses_by_ns: dict[str, list[str]] | None = None,
        list_raises: Exception | None = None,
        patch_raises: Exception | None = None,
    ) -> None:
        self.ingresses_by_ns = ingresses_by_ns or {}
        self.list_calls: list[tuple[str, str]] = []
        self.patch_calls: list[dict[str, Any]] = []
        self._list_raises = list_raises
        self._patch_raises = patch_raises

    def list_namespaced_ingress(self, namespace: str, **kwargs: Any) -> Any:
        self.list_calls.append((namespace, kwargs.get("label_selector", "")))
        if self._list_raises is not None:
            raise self._list_raises
        names = self.ingresses_by_ns.get(namespace, [])
        return _FakeListing([_FakeIngressItem(n) for n in names])

    def merge_patch_ingress(self, *, namespace: str, name: str, patch: dict[str, Any]) -> dict[str, Any]:
        self.patch_calls.append({"namespace": namespace, "name": name, "patch": patch})
        if self._patch_raises is not None:
            raise self._patch_raises
        return {"metadata": {"name": name, "namespace": namespace}}


class _RecordingDriver:
    """Minimal ClusterDriver stand-in exposing ``_k8s`` the way the
    reconcile helper reaches it."""

    def __init__(self, client: _RecordingK8sClient) -> None:
        self._client = client
        self.k8s_calls: list[str] = []

    def _k8s(self, cluster_slug: str) -> Any:
        self.k8s_calls.append(cluster_slug)
        return self._client


@pytest.fixture
def install_driver(monkeypatch):
    """Install ``driver`` in place of the cluster-management resolver.
    The reconcile helper imports ``_driver_for_cluster`` from
    ``core.cluster_management`` locally, so patching the source module
    attribute covers it."""

    def _install(driver: Any) -> None:
        monkeypatch.setattr(
            "core.cluster_management._driver_for_cluster",
            lambda cluster: driver,  # noqa: ARG005 — signature parity
        )

    return _install


# ---- annotation-delta builder -------------------------------------


def test_auth_patch_enabled_matches_driver_keys():
    """Enabled config produces exactly the six auth annotations the ALB
    driver renders, with the IDP value JSON-encoded identically."""
    patch = _auth_annotation_patch(AUTH_CONFIG)
    assert set(patch) == set(AUTH_ANNOTATION_KEYS)
    assert patch["alb.ingress.kubernetes.io/auth-type"] == "cognito"
    assert patch["alb.ingress.kubernetes.io/auth-on-unauthenticated-request"] == "authenticate"
    assert patch["alb.ingress.kubernetes.io/auth-scope"] == "openid email profile"
    assert patch["alb.ingress.kubernetes.io/auth-session-cookie"] == "AWSELBAuthSessionCookie"
    assert patch["alb.ingress.kubernetes.io/auth-session-timeout"] == "86400"
    idp = json.loads(patch["alb.ingress.kubernetes.io/auth-idp-cognito"])
    assert idp == {
        "UserPoolArn": AUTH_CONFIG["user_pool_arn"],
        "UserPoolClientId": AUTH_CONFIG["user_pool_client_id"],
        "UserPoolDomain": AUTH_CONFIG["user_pool_domain"],
    }


def test_auth_patch_renders_same_idp_json_as_alb_driver():
    """The reconcile must emit byte-identical annotations to a fresh
    render, otherwise the LBC sees two managers fighting over the
    Ingress. Pin against the driver's own ``_render_annotations``."""
    from providers.aws.ingress_alb import ALBConfig, ALBIngressDriver, CognitoAuthConfig

    driver = ALBIngressDriver(
        config=ALBConfig(
            region="us-west-2",
            cognito_auth=CognitoAuthConfig(
                user_pool_arn=AUTH_CONFIG["user_pool_arn"],
                user_pool_client_id=AUTH_CONFIG["user_pool_client_id"],
                user_pool_domain=AUTH_CONFIG["user_pool_domain"],
            ),
        )
    )
    rendered = driver._render_annotations(tls_strategy="acm_dns_validated")
    patch = _auth_annotation_patch(AUTH_CONFIG)
    for key in AUTH_ANNOTATION_KEYS:
        assert patch[key] == rendered[key], key


def test_auth_patch_disabled_sets_keys_to_none():
    """Null config => every auth key set to None so the patch deletes
    it from the live Ingress."""
    patch = _auth_annotation_patch(None)
    assert set(patch) == set(AUTH_ANNOTATION_KEYS)
    assert all(v is None for v in patch.values())


def test_auth_patch_partial_config_treated_as_disabled():
    """A config missing one of the three required fields can't render a
    valid IDP block, so it's treated as disabled (keys removed) rather
    than emitting a half-formed annotation."""
    patch = _auth_annotation_patch({"user_pool_arn": "arn:...", "user_pool_client_id": "c"})
    assert all(v is None for v in patch.values())


# ---- reconcile sweep ----------------------------------------------


def test_reconcile_enabled_patches_managed_ingresses(org, team, cluster, domain, install_driver):
    """Two bound envs each with a managed-subdomain Ingress -> two
    patches carrying the cognito annotations; list is scoped by the
    managed-subdomain label selector."""
    app1 = _make_app(org, team, "alpha")
    app2 = _make_app(org, team, "beta")
    _bind_env(app1, cluster, domain=domain)
    _bind_env(app2, cluster, domain=domain)

    client = _RecordingK8sClient(
        ingresses_by_ns={
            f"{org.slug}-alpha": ["alpha-web"],
            f"{org.slug}-beta": ["beta-web"],
        }
    )
    install_driver(_RecordingDriver(client))

    result = reconcile_cluster_ingresses(cluster)

    assert result == {"reconciled": 2, "skipped": 0, "errors": []}
    # Both namespaces listed with the managed-subdomain selector.
    assert {ns for ns, _ in client.list_calls} == {f"{org.slug}-alpha", f"{org.slug}-beta"}
    assert all(sel == MANAGED_SUBDOMAIN_SELECTOR for _, sel in client.list_calls)
    # Each patch carries the enabled annotation set under metadata.
    assert len(client.patch_calls) == 2
    for call in client.patch_calls:
        annotations = call["patch"]["metadata"]["annotations"]
        assert annotations["alb.ingress.kubernetes.io/auth-type"] == "cognito"


def test_reconcile_disabled_removes_annotations(org, team, cluster, domain, install_driver):
    """When the cluster's config is cleared, the patch sets every auth
    key to None so the live Ingress loses the annotations."""
    cluster.alb_auth_config = None
    cluster.save(update_fields=["alb_auth_config"])
    app = _make_app(org, team, "alpha")
    _bind_env(app, cluster, domain=domain)

    client = _RecordingK8sClient(ingresses_by_ns={f"{org.slug}-alpha": ["alpha-web"]})
    install_driver(_RecordingDriver(client))

    result = reconcile_cluster_ingresses(cluster)

    assert result["reconciled"] == 1
    annotations = client.patch_calls[0]["patch"]["metadata"]["annotations"]
    assert set(annotations) == set(AUTH_ANNOTATION_KEYS)
    assert all(v is None for v in annotations.values())


def test_reconcile_skips_envs_without_managed_subdomain_ingress(org, team, cluster, domain, install_driver):
    """An env bound to the cluster but with no managed-subdomain Ingress
    yet (not deployed) counts as skipped, not patched."""
    app = _make_app(org, team, "alpha")
    _bind_env(app, cluster, domain=domain)

    client = _RecordingK8sClient(ingresses_by_ns={})  # nothing in the namespace
    install_driver(_RecordingDriver(client))

    result = reconcile_cluster_ingresses(cluster)

    assert result == {"reconciled": 0, "skipped": 1, "errors": []}
    assert client.patch_calls == []


def test_reconcile_ignores_envs_without_managed_domain(org, team, cluster, domain, install_driver):
    """Envs with no ManagedDomain don't render a managed-subdomain
    Ingress, so the sweep must not even list their namespace."""
    app_with = _make_app(org, team, "alpha")
    app_without = _make_app(org, team, "beta")
    _bind_env(app_with, cluster, domain=domain)
    _bind_env(app_without, cluster, domain=None)

    client = _RecordingK8sClient(ingresses_by_ns={f"{org.slug}-alpha": ["alpha-web"]})
    install_driver(_RecordingDriver(client))

    result = reconcile_cluster_ingresses(cluster)

    assert result["reconciled"] == 1
    # Only the domain-bound env's namespace was listed.
    assert {ns for ns, _ in client.list_calls} == {f"{org.slug}-alpha"}


def test_reconcile_captures_per_namespace_list_error(org, team, cluster, domain, install_driver):
    """A namespace that fails to list doesn't abort the sweep — the
    error is recorded and the remaining envs still process."""
    app = _make_app(org, team, "alpha")
    _bind_env(app, cluster, domain=domain)

    client = _RecordingK8sClient(list_raises=RuntimeError("apiserver unreachable"))
    install_driver(_RecordingDriver(client))

    result = reconcile_cluster_ingresses(cluster)

    assert result["reconciled"] == 0
    assert result["skipped"] == 0
    assert len(result["errors"]) == 1
    assert "apiserver unreachable" in result["errors"][0]


def test_reconcile_captures_patch_error(org, team, cluster, domain, install_driver):
    """A patch failure on one Ingress is captured without taking down
    the operation."""
    app = _make_app(org, team, "alpha")
    _bind_env(app, cluster, domain=domain)

    client = _RecordingK8sClient(
        ingresses_by_ns={f"{org.slug}-alpha": ["alpha-web"]},
        patch_raises=RuntimeError("conflict"),
    )
    install_driver(_RecordingDriver(client))

    result = reconcile_cluster_ingresses(cluster)

    assert result["reconciled"] == 0
    assert len(result["errors"]) == 1
    assert "alpha-web" in result["errors"][0]


def test_reconcile_driver_without_k8s_returns_error(org, team, cluster, domain, install_driver):
    """A driver that doesn't expose ``_k8s`` (e.g. a provider without a
    live kubernetes client) can't reconcile — the helper returns an
    error rather than raising."""
    app = _make_app(org, team, "alpha")
    _bind_env(app, cluster, domain=domain)

    class _NoK8sDriver:
        pass

    install_driver(_NoK8sDriver())

    result = reconcile_cluster_ingresses(cluster)

    assert result["reconciled"] == 0
    assert len(result["errors"]) == 1
    assert "no kubernetes client" in result["errors"][0]


# ---- mutation resolver --------------------------------------------


def _info():
    request = SimpleNamespace(user=None)
    return SimpleNamespace(context=SimpleNamespace(request=request, user=None))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def test_mutation_denied_without_cluster_manage(cluster, org, permission_resolver):
    with _ctx(org):
        result = ClustersMutation().reconcile_cluster_ingresses(
            _info(),
            ReconcileClusterIngressesInput(cluster_id=GUID(str(cluster.guid))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "PERMISSION_DENIED"


def test_mutation_not_found_on_unknown_cluster(org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().reconcile_cluster_ingresses(
            _info(),
            ReconcileClusterIngressesInput(cluster_id=GUID(str(uuid.uuid4()))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "NOT_FOUND"


def test_mutation_reconciles_a_non_alb_cluster(
    org, team, cluster, domain, install_driver, permission_resolver
):
    """Non-ALB clusters used to be refused with PRECONDITION, which left
    the central auth host with no way to push a gate onto Ingresses
    already running -- the reason Stage B of #1539 had to hand-annotate
    them. The class now selects the annotation keys instead of gating
    the whole mutation."""
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    cluster.ingress_class = "nginx"
    cluster.oidc_auth_config = {
        "discovery_url": "https://issuer.example",
        "client_id": "astrolift-proxy",
        "auth_proxy_host": "auth.apps.example.net",
    }
    cluster.save(update_fields=["ingress_class", "oidc_auth_config"])
    app = _make_app(org, team, "alpha")
    _bind_env(app, cluster, domain=domain)

    client = _RecordingK8sClient(ingresses_by_ns={f"{org.slug}-alpha": ["alpha-web"]})
    install_driver(_RecordingDriver(client))

    with _ctx(org):
        result = ClustersMutation().reconcile_cluster_ingresses(
            _info(),
            ReconcileClusterIngressesInput(cluster_id=GUID(str(cluster.guid))),
        )

    assert result.ok is True, result.errors
    assert result.data.reconciled_count == 1
    patched = client.patch_calls[0]["patch"]["metadata"]["annotations"]
    assert patched["nginx.ingress.kubernetes.io/auth-url"] == ("https://auth.apps.example.net/oauth2/auth")


def test_mutation_happy_path_returns_reconciled_count(
    org, team, cluster, domain, install_driver, permission_resolver
):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    app = _make_app(org, team, "alpha")
    _bind_env(app, cluster, domain=domain)

    client = _RecordingK8sClient(ingresses_by_ns={f"{org.slug}-alpha": ["alpha-web"]})
    install_driver(_RecordingDriver(client))

    with _ctx(org):
        result = ClustersMutation().reconcile_cluster_ingresses(
            _info(),
            ReconcileClusterIngressesInput(cluster_id=GUID(str(cluster.guid))),
        )

    assert result.ok is True
    assert result.data is not None
    assert result.data.reconciled_count == 1
    assert result.data.skipped_count == 0
    assert result.data.errors == []
