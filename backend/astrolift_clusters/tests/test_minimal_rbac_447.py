"""Work beyond the minimal RBAC contract refuses up front (calliope-installer#447).

With ``controllers`` in ``ASTROLIFT_WITHHELD_CAPABILITIES`` the control plane
holds only ``deploy/rbac/control-plane-minimal.yaml`` on AWS clusters. Every
path that would write cluster-scoped objects refuses with the reason before
it builds a driver or reaches the apiserver; registration still works, minus
the platform ClusterRole. Unset is today's behaviour.
"""

from types import SimpleNamespace

import pytest
from _sdk.cluster import BootstrapComponent

from astrolift_clusters.schema.types import bootstrap_plan_to_type
from astrolift_clusters.tests.test_install_restrictions_447 import _set_provider, install, world  # noqa: F401
from core import install_restrictions as ir
from core.cluster_management import ClusterManagementError, deploy_agent_dispatch
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

REASON = "Cluster-wide changes are withheld"
AWS = SimpleNamespace(provider_plugin=SimpleNamespace(slug="aws"))


def test_every_recipe_component_is_refused_on_aws_only(monkeypatch):
    monkeypatch.delenv(ir.ENV_VAR, raising=False)
    assert ir.cluster_scope_refusal(AWS) == ""
    assert ir.controller_refusal("cert-manager", AWS) == ""

    monkeypatch.setenv(ir.ENV_VAR, "controllers")
    for key in ("cert-manager", "flux", "knative-serving", "envoy-gateway", "external-dns"):
        assert REASON in ir.controller_refusal(key, AWS)
    for slug in ("gcp", "azure", "k8s_native"):
        other = SimpleNamespace(provider_plugin=SimpleNamespace(slug=slug))
        assert ir.controller_refusal("cert-manager", other) == ""
        assert ir.cluster_scope_refusal(other) == ""


def test_a_more_specific_reason_wins(monkeypatch):
    monkeypatch.setenv(ir.ENV_VAR, "dns,controllers")
    assert "DNS is withheld" in ir.controller_refusal("external-dns", AWS)
    assert REASON in ir.controller_refusal("cert-manager", AWS)


def test_the_refusal_is_not_retried():
    error = ir.WithheldCapabilityError("withheld")
    assert error.non_retryable and str(error) == "withheld"


def test_installing_the_recipe_is_refused_with_the_reason(world, monkeypatch):  # noqa: F811
    monkeypatch.setenv(ir.ENV_VAR, "controllers")
    result = install(world, ["cert-manager"])
    assert not result.ok and REASON in result.errors[0].message
    assert world.queued == []


def test_the_recipe_offers_every_component_disabled_with_the_reason(world, monkeypatch):  # noqa: F811
    components = [
        BootstrapComponent(key=key, title=key, default_enabled=True, rationale="")
        for key in ("cert-manager", "metrics-server")
    ]
    monkeypatch.setenv(ir.ENV_VAR, "controllers")
    plan = bootstrap_plan_to_type(world.cluster, components).components
    assert all(c.withheld_reason and REASON in c.withheld_reason for c in plan)


def test_the_install_activity_refuses_before_building_a_driver(world, monkeypatch):  # noqa: F811
    from astrolift_workflows.activities.install_prereqs import _install_cluster_prereqs_sync

    built = []
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda c: built.append(c))
    monkeypatch.setenv(ir.ENV_VAR, "controllers")
    with pytest.raises(ir.WithheldCapabilityError, match=REASON):
        _install_cluster_prereqs_sync(world.cluster.pk, ["cert-manager"], {})
    assert built == []


def test_the_edge_install_is_not_started(world, monkeypatch):  # noqa: F811
    from astrolift_clusters import edge_install

    monkeypatch.setattr("providers.k8s_native.edge_gateway.edge_configured", lambda _config: True)
    monkeypatch.setattr(
        "astrolift_clusters.recipe_detection.components_installed_by_recipe", lambda _c: set()
    )
    world.cluster.ingress_class = edge_install.EDGE_INGRESS_CLASS
    assert edge_install.edge_install_wanted(world.cluster)
    monkeypatch.setenv(ir.ENV_VAR, "controllers")
    assert not edge_install.edge_install_wanted(world.cluster)


def test_the_keep_alive_agent_is_refused_before_building_a_driver(world, monkeypatch):  # noqa: F811
    built = []
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda c: built.append(c))
    monkeypatch.setenv(ir.ENV_VAR, "controllers")
    with pytest.raises(ClusterManagementError, match=REASON):
        deploy_agent_dispatch(cluster=world.cluster)
    assert built == []


def test_the_server_owned_agent_install_is_refused_at_review(world, monkeypatch, settings):  # noqa: F811
    from astrolift_clusters.agent_install import AgentInstallError, review_install

    settings.APP_BASE_URL = "https://astrolift.example"
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
        _cluster, source = review_install(world.cluster.guid)
        assert source
        monkeypatch.setenv(ir.ENV_VAR, "controllers")
        with pytest.raises(AgentInstallError, match=REASON) as caught:
            review_install(world.cluster.guid)
    assert caught.value.code == "WITHHELD"


def test_the_log_collector_is_refused_with_the_reason(world, monkeypatch):  # noqa: F811
    from astrolift_clusters import log_collector
    from astrolift_clusters.models import TenantCluster

    world.cluster.auth_method = TenantCluster.AuthMethod.EXEC_PLUGIN
    monkeypatch.setenv(ir.ENV_VAR, "controllers")
    with pytest.raises(log_collector.CollectorError, match=REASON) as caught:
        log_collector._support(world.cluster, world.cluster.provider_plugin, 30)
    assert caught.value.code == "WITHHELD"


def _csi_binding(cluster):
    return SimpleNamespace(
        name="data",
        mount_path="/data",
        sub_path="",
        protocol="nfs",
        source_kind="csi",
        csi_driver="efs.csi.aws.com",
        volume_handle="fs-123",
        secret_refs={},
        capacity="10Gi",
        access_modes=["ReadWriteMany"],
        managed_service=SimpleNamespace(name="shared", effective_cluster=cluster),
    )


class _Driver:
    def list_csi_drivers(self, _slug):
        return ["efs.csi.aws.com"]


def test_a_csi_filesystem_binding_is_refused(monkeypatch):
    from astrolift_services.filesystem_bindings import FilesystemBindingError, preflight_bindings

    cluster = SimpleNamespace(slug="c1", provider_plugin=SimpleNamespace(slug="aws"))
    preflight_bindings([_csi_binding(cluster)], cluster_driver=_Driver(), cluster_slug="c1", namespace="ns")
    monkeypatch.setenv(ir.ENV_VAR, "controllers")
    with pytest.raises(FilesystemBindingError, match=REASON):
        preflight_bindings(
            [_csi_binding(cluster)], cluster_driver=_Driver(), cluster_slug="c1", namespace="ns"
        )


# ---- registration ---------------------------------------------------------------


class _Backend:
    def __init__(self):
        self.applied = []

    def apply_manifest(self, *, auth, manifest):
        self.applied.append(manifest["kind"])
        return "created"

    def list_cluster_crds(self, *, auth):
        return []

    def list_namespaced_pods(self, *, auth, namespace):
        return []

    def list_storage_classes(self, *, auth):
        return []


def _context():
    return SimpleNamespace(to_auth=lambda: None, ingress_class="")


def test_registration_skips_the_platform_cluster_role_under_the_contract():
    from k8s_native.management import platform_rbac_manifests, run_bring_into_management

    assert [m["kind"] for m in platform_rbac_manifests()] == [
        "Namespace",
        "ServiceAccount",
        "ClusterRole",
        "ClusterRoleBinding",
    ]
    backend = _Backend()
    report = run_bring_into_management(
        backend=backend, cluster=_context(), run_preflight=False, cluster_rbac=False
    )
    assert report.success and report.rbac_applied
    assert backend.applied == ["Namespace", "ServiceAccount"]


def test_the_eks_driver_reads_the_contract_from_the_install(monkeypatch):
    from aws.cluster_eks import _cluster_scope_withheld

    monkeypatch.delenv(ir.ENV_VAR, raising=False)
    assert not _cluster_scope_withheld()
    monkeypatch.setenv(ir.ENV_VAR, "dns,clusters")
    assert not _cluster_scope_withheld()
    monkeypatch.setenv(ir.ENV_VAR, "clusters, controllers")
    assert _cluster_scope_withheld()
