"""Tests for ``manage.py register_tenant_cluster`` (#1474).

The command is the only non-UI registration path, so "it saved a row" is
not the bar. Each cloud's test drives the row it wrote back through the
code the platform actually uses to reach a cluster:

  * ``core.cluster_observability._config_for`` — the driver config the
    observability / management paths build from ``provider_config``;
  * the cloud driver's own ``exec_plugin`` resolution, which reads
    ``auth_config`` and mints the kubeconfig the shared k8s_native
    backend dials;
  * ``k8s_native.observability.build_api_client``, which turns that
    kubeconfig into a configured ``ApiClient``.

If registration writes the wrong keys, one of those three fails even
though the row looks fine in the admin.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass, field
from io import StringIO
from typing import Any

import pytest
import yaml
from django.core.management import call_command
from django.core.management.base import CommandError

from astrolift_clusters.management.commands import register_tenant_cluster as cmd
from astrolift_clusters.models import ProviderPlugin, TenantCluster

pytestmark = pytest.mark.django_db


AKS_FQDN = "aks-conflict-9f3c1a.hcp.eastus.azmk8s.io"
AKS_CA = base64.b64encode(b"-----BEGIN CERTIFICATE-----\naks\n-----END CERTIFICATE-----\n").decode()
GKE_CA = base64.b64encode(b"-----BEGIN CERTIFICATE-----\ngke\n-----END CERTIFICATE-----\n").decode()


def _plugin(slug: str) -> ProviderPlugin:
    existing = ProviderPlugin.objects.filter(slug=slug).first()
    if existing is not None:
        return existing
    # Seeded directly; the plugin row is scaffolding for this test.
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name=slug, slug=slug, capabilities_manifest={}, config_schema={})],
    )
    return plugin


# ---- Azure test doubles -------------------------------------------
#
# Shapes mirror azure-mgmt-containerservice: ``ManagedCluster`` with
# fqdn / location / identity / oidc_issuer_profile, and
# ``CredentialResults.kubeconfigs[].value`` as bytes.


def _admin_kubeconfig_blob(*, server: str, ca_data: str) -> bytes:
    return yaml.safe_dump(
        {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {
                    "name": "aks",
                    "cluster": {"server": server, "certificate-authority-data": ca_data},
                }
            ],
            "users": [{"name": "clusterAdmin", "user": {"token": "admin-token"}}],
            "contexts": [{"name": "aks", "context": {"cluster": "aks", "user": "clusterAdmin"}}],
            "current-context": "aks",
        },
        sort_keys=False,
    ).encode()


@dataclass
class _FakeIdentity:
    tenant_id: str = "tenant-abc"


@dataclass
class _FakeOidcProfile:
    enabled: bool = True
    issuer_url: str = "https://eastus.oic.prod-aks.azure.com/tenant-abc/9f3c1a/"


@dataclass
class _FakeManagedCluster:
    name: str = "aks-conflict"
    id: str = (
        "/subscriptions/sub-1/resourceGroups/rg-astrolift/providers/"
        "Microsoft.ContainerService/managedClusters/aks-conflict"
    )
    fqdn: str = AKS_FQDN
    location: str = "eastus"
    identity: _FakeIdentity = field(default_factory=_FakeIdentity)
    oidc_issuer_profile: _FakeOidcProfile = field(default_factory=_FakeOidcProfile)


@dataclass
class _FakeCredentialResult:
    name: str
    value: bytes


@dataclass
class _FakeCredentialResults:
    kubeconfigs: list[_FakeCredentialResult]


class _FakeManagedClusters:
    def __init__(
        self,
        *,
        clusters: list[_FakeManagedCluster] | None = None,
        credentials_error: Exception | None = None,
    ) -> None:
        self._clusters = clusters if clusters is not None else [_FakeManagedCluster()]
        self._credentials_error = credentials_error
        self.get_calls: list[tuple[str, str]] = []
        self.credential_calls: list[tuple[str, str]] = []
        self.list_calls = 0

    def list(self) -> list[_FakeManagedCluster]:
        self.list_calls += 1
        return list(self._clusters)

    def get(self, *, resource_group_name: str, resource_name: str) -> _FakeManagedCluster:
        self.get_calls.append((resource_group_name, resource_name))
        for managed in self._clusters:
            if managed.name == resource_name:
                return managed
        raise LookupError(f"no such cluster {resource_name}")

    def list_cluster_admin_credentials(
        self,
        *,
        resource_group_name: str,
        resource_name: str,
    ) -> _FakeCredentialResults:
        self.credential_calls.append((resource_group_name, resource_name))
        if self._credentials_error is not None:
            raise self._credentials_error
        return _FakeCredentialResults(
            kubeconfigs=[
                _FakeCredentialResult(
                    name="clusterAdmin",
                    value=_admin_kubeconfig_blob(
                        server=f"https://{AKS_FQDN}:443",
                        ca_data=AKS_CA,
                    ),
                )
            ]
        )


@dataclass
class _FakeAKS:
    managed_clusters: _FakeManagedClusters


@dataclass
class _RecordingPodBackend:
    seen: list[Any] = field(default_factory=list)

    def list_pods(self, *, auth: Any, namespace: str, app_slug: str) -> list[Any]:
        self.seen.append(auth)
        return []


# ---- GCP test doubles ---------------------------------------------


@dataclass
class _FakeMasterAuth:
    cluster_ca_certificate: str = GKE_CA


@dataclass
class _FakeGKECluster:
    endpoint: str = "34.10.20.30"
    master_auth: _FakeMasterAuth = field(default_factory=_FakeMasterAuth)


class _FakeContainerClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_cluster(self, *, name: str) -> _FakeGKECluster:
        self.calls.append(name)
        return _FakeGKECluster()


class _FakeGoogleCredentials:
    def __init__(self) -> None:
        self.token = ""

    def refresh(self, _request: Any) -> None:
        self.token = "wi-bearer"


# ---- Azure ---------------------------------------------------------


def _register_aks(monkeypatch, *, slug: str, extra: list[str] | None = None) -> _FakeManagedClusters:
    _plugin("azure")
    managed_clusters = _FakeManagedClusters()
    monkeypatch.setattr(
        cmd,
        "_azure_containerservice_client",
        lambda subscription_id: _FakeAKS(managed_clusters=managed_clusters),
    )
    call_command(
        "register_tenant_cluster",
        "--slug",
        slug,
        "--plugin-slug",
        "azure",
        "--auto-discover-azure",
        "--azure-cluster-name",
        "aks-conflict",
        "--azure-subscription-id",
        "sub-1",
        *(extra or []),
        stdout=StringIO(),
    )
    return managed_clusters


def test_azure_registration_produces_an_addressable_cluster(monkeypatch):
    """The AKS row the command writes must survive the whole reach
    chain: driver config, ``exec_plugin`` resolution, ApiClient."""
    from k8s_native.observability import build_api_client

    from core.cluster_observability import _auth_for_cluster, _config_for

    managed_clusters = _register_aks(
        monkeypatch, slug="aks-prd", extra=["--azure-resource-group", "rg-astrolift"]
    )
    cluster = TenantCluster.objects.get(slug="aks-prd")

    assert cluster.endpoint == f"https://{AKS_FQDN}"
    assert cluster.ca_cert == AKS_CA
    assert cluster.region == "eastus"
    assert cluster.auth_method == "exec_plugin"

    # The observability / management path reads provider_config. Without
    # subscription + resource group the AKS driver talks to nothing.
    config = _config_for("azure", cluster)
    assert (config.subscription_id, config.resource_group, config.cluster_name) == (
        "sub-1",
        "rg-astrolift",
        "aks-conflict",
    )
    assert cluster.provider_config["tenant_id"] == "tenant-abc"
    assert cluster.provider_config["cluster_oidc_issuer"] == _FakeOidcProfile().issuer_url

    # The driver resolves exec_plugin out of auth_config. Feed it the
    # row exactly as the resolver layer would.
    from azure.cluster_aks import AKSClusterDriver, AKSConfig

    backend = _RecordingPodBackend()
    driver = AKSClusterDriver(
        config=AKSConfig(
            subscription_id=config.subscription_id,
            resource_group="",
            cluster_name="",
            container_service_client=_FakeAKS(managed_clusters=managed_clusters),
        ),
        pod_backend=backend,
    )
    driver.list_pods(auth=_auth_for_cluster(cluster), namespace="acme-web", app_slug="web")

    # Empty driver config on purpose: the row's auth_config is the only
    # thing that can have told the driver which cluster to credential.
    assert managed_clusters.credential_calls[-1] == ("rg-astrolift", "aks-conflict")

    [resolved] = backend.seen
    assert resolved.auth_method == "kubeconfig"
    dialed = yaml.safe_load(resolved.auth_config["kubeconfig"])
    server = dialed["clusters"][0]["cluster"]["server"]
    assert server.startswith(cluster.endpoint)
    assert dialed["clusters"][0]["cluster"]["certificate-authority-data"] == cluster.ca_cert

    api_client = build_api_client(resolved)
    assert api_client.configuration.host == server


def test_azure_registration_resolves_the_resource_group_from_the_subscription(monkeypatch):
    """An installer knows the cluster name it created; making it also
    thread the resource group through is what this avoids."""
    managed_clusters = _register_aks(monkeypatch, slug="aks-inferred")

    cluster = TenantCluster.objects.get(slug="aks-inferred")
    assert managed_clusters.list_calls == 1
    assert cluster.auth_config["resource_group"] == "rg-astrolift"
    assert cluster.provider_config["resource_group"] == "rg-astrolift"


def test_azure_registration_refuses_an_ambiguous_cluster_name(monkeypatch):
    """AKS names are unique per resource group, not per subscription.
    Guessing would register a row pointed at the wrong cluster."""
    _plugin("azure")
    other = _FakeManagedCluster(
        id=(
            "/subscriptions/sub-1/resourceGroups/rg-staging/providers/"
            "Microsoft.ContainerService/managedClusters/aks-conflict"
        ),
    )
    managed_clusters = _FakeManagedClusters(clusters=[_FakeManagedCluster(), other])
    monkeypatch.setattr(
        cmd,
        "_azure_containerservice_client",
        lambda subscription_id: _FakeAKS(managed_clusters=managed_clusters),
    )

    with pytest.raises(CommandError, match="rg-astrolift"):
        call_command(
            "register_tenant_cluster",
            "--slug",
            "aks-ambiguous",
            "--plugin-slug",
            "azure",
            "--auto-discover-azure",
            "--azure-cluster-name",
            "aks-conflict",
            "--azure-subscription-id",
            "sub-1",
            stdout=StringIO(),
        )
    assert not TenantCluster.all_objects.filter(slug="aks-ambiguous").exists()


def test_azure_registration_survives_disabled_admin_credentials(monkeypatch):
    """A cluster with local admin accounts disabled still registers;
    the CA is the only thing lost, and the operator gets told."""
    _plugin("azure")
    managed_clusters = _FakeManagedClusters(
        credentials_error=RuntimeError("BadRequest: managed cluster has local accounts disabled"),
    )
    monkeypatch.setattr(
        cmd,
        "_azure_containerservice_client",
        lambda subscription_id: _FakeAKS(managed_clusters=managed_clusters),
    )
    out = StringIO()
    call_command(
        "register_tenant_cluster",
        "--slug",
        "aks-noadmin",
        "--plugin-slug",
        "azure",
        "--auto-discover-azure",
        "--azure-cluster-name",
        "aks-conflict",
        "--azure-subscription-id",
        "sub-1",
        "--azure-resource-group",
        "rg-astrolift",
        stdout=out,
    )

    cluster = TenantCluster.objects.get(slug="aks-noadmin")
    assert cluster.endpoint == f"https://{AKS_FQDN}"
    assert cluster.ca_cert == ""
    assert "local accounts disabled" in out.getvalue()


def test_azure_env_vocabulary_is_wired(monkeypatch):
    """``AKS_CLUSTER_NAME`` was inert before #1474 — the installer sets
    it and nothing read it."""
    _plugin("azure")
    managed_clusters = _FakeManagedClusters()
    monkeypatch.setattr(
        cmd,
        "_azure_containerservice_client",
        lambda subscription_id: _FakeAKS(managed_clusters=managed_clusters),
    )
    monkeypatch.setenv("ASTROLIFT_CLUSTER_SLUG", "aks-from-env")
    monkeypatch.setenv("ASTROLIFT_CLUSTER_PLUGIN_SLUG", "azure")
    monkeypatch.setenv("ASTROLIFT_CLUSTER_AUTO_DISCOVER_AZURE", "true")
    monkeypatch.setenv("AKS_CLUSTER_NAME", "aks-conflict")
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-1")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg-astrolift")

    call_command("register_tenant_cluster", stdout=StringIO())

    cluster = TenantCluster.objects.get(slug="aks-from-env")
    assert cluster.auth_config["cluster_name"] == "aks-conflict"
    assert managed_clusters.get_calls == [("rg-astrolift", "aks-conflict")]


def test_azure_registration_is_idempotent_and_restores_a_soft_deleted_row(monkeypatch):
    _register_aks(monkeypatch, slug="aks-prd", extra=["--azure-resource-group", "rg-astrolift"])
    cluster = TenantCluster.objects.get(slug="aks-prd")
    original_id = cluster.id

    from django.utils import timezone

    TenantCluster.all_objects.filter(pk=cluster.pk).update(deleted_at=timezone.now(), is_active=False)

    _register_aks(monkeypatch, slug="aks-prd", extra=["--azure-resource-group", "rg-astrolift"])

    restored = TenantCluster.objects.get(slug="aks-prd")
    assert restored.id == original_id
    assert restored.deleted_at is None
    assert restored.is_active
    assert TenantCluster.all_objects.filter(slug="aks-prd").count() == 1


# ---- GCP -----------------------------------------------------------


def test_gcp_registration_produces_an_addressable_cluster(monkeypatch):
    """Same chain for GKE: config builder, exec_plugin materialization,
    ApiClient."""
    from k8s_native.observability import build_api_client

    from core.cluster_observability import _auth_for_cluster, _config_for

    _plugin("gcp")
    container = _FakeContainerClient()
    monkeypatch.setattr(cmd, "_gcp_container_client", lambda: container)
    call_command(
        "register_tenant_cluster",
        "--slug",
        "gke-prd",
        "--plugin-slug",
        "gcp",
        "--auto-discover-gcp",
        "--gcp-cluster-name",
        "gke-conflict",
        "--gcp-project-id",
        "conflict-prd",
        "--gcp-location",
        "us-central1",
        stdout=StringIO(),
    )

    cluster = TenantCluster.objects.get(slug="gke-prd")
    assert container.calls == ["projects/conflict-prd/locations/us-central1/clusters/gke-conflict"]
    assert cluster.endpoint == "https://34.10.20.30"
    assert cluster.ca_cert == GKE_CA
    assert cluster.region == "us-central1"

    config = _config_for("gcp", cluster)
    assert (config.project_id, config.location, config.cluster_name) == (
        "conflict-prd",
        "us-central1",
        "gke-conflict",
    )

    from gcp.cluster_gke import GKEClusterDriver, GKEConfig

    backend = _RecordingPodBackend()
    driver = GKEClusterDriver(
        config=GKEConfig(project_id="", location="", cluster_name="", container_client=container),
        pod_backend=backend,
        credentials_factory=lambda: (_FakeGoogleCredentials(), "conflict-prd"),
    )
    monkeypatch.setattr("gcp.cluster_gke._refresh_credentials", lambda creds: creds.refresh(None))
    driver.list_pods(auth=_auth_for_cluster(cluster), namespace="acme-web", app_slug="web")

    # Empty driver config: only the row's auth_config can have named the
    # project / location / cluster the driver just described.
    assert container.calls[-1] == "projects/conflict-prd/locations/us-central1/clusters/gke-conflict"

    [resolved] = backend.seen
    assert resolved.auth_method == "kubeconfig"
    dialed = yaml.safe_load(resolved.auth_config["kubeconfig"])
    assert dialed["clusters"][0]["cluster"]["server"] == cluster.endpoint
    assert dialed["clusters"][0]["cluster"]["certificate-authority-data"] == cluster.ca_cert

    api_client = build_api_client(resolved)
    assert api_client.configuration.host == cluster.endpoint


# ---- Cross-cloud guards --------------------------------------------


@pytest.mark.parametrize(
    ("flag", "plugin_slug"),
    [("--auto-discover-azure", "aws"), ("--auto-discover-gcp", "azure"), ("--auto-discover-aws", "gcp")],
)
def test_discovery_flag_aimed_at_the_wrong_plugin_is_fatal(monkeypatch, flag, plugin_slug):
    """Silently skipping discovery is how an operator ends up with a row
    that saved cleanly and cannot reach anything."""
    _plugin(plugin_slug)
    slug = f"mismatch-{uuid.uuid4().hex[:6]}"

    with pytest.raises(CommandError, match="requires --plugin-slug"):
        call_command(
            "register_tenant_cluster",
            "--slug",
            slug,
            "--plugin-slug",
            plugin_slug,
            flag,
            stdout=StringIO(),
        )
    assert not TenantCluster.all_objects.filter(slug=slug).exists()


@pytest.mark.parametrize(
    ("plugin_slug", "expected"),
    [("aws", "alb"), ("azure", "nginx"), ("gcp", "nginx"), ("k8s_native", "nginx")],
)
def test_ingress_class_default_follows_the_plugin(plugin_slug, expected):
    """An 'alb' row on AKS/GKE renders ALB-only ingress annotations
    (core.app_deploy branches on the class)."""
    _plugin(plugin_slug)
    slug = f"ing-{plugin_slug.replace('_', '-')}"

    call_command(
        "register_tenant_cluster",
        "--slug",
        slug,
        "--plugin-slug",
        plugin_slug,
        stdout=StringIO(),
    )

    assert TenantCluster.objects.get(slug=slug).ingress_class == expected
