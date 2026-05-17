"""Tests for ``astroliftWorkloadScalingStatus`` (#430).

Composes manifest-side HPA fields with the live ``WorkloadStatus``
read from the cluster driver. We monkeypatch
``_read_live_replicas`` to keep the tests deterministic — the driver-
side path is covered by the existing
``astrolift_lifecycle.services.k8s_ops`` test suite.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.schema.queries import RegistryQuery
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _scaffold(
    *,
    hpa_min: int | None = None,
    hpa_max: int | None = None,
    hpa_target: int = 75,
    replicas: int = 3,
) -> tuple[Organization, RegisteredApp, Workload]:
    org = Organization.objects.create(name="Acme Scale", slug="acme-scale")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-scale")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-scale")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Local",
                slug="local-scale",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name="prod",
        slug="prod-scale",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-scale",
        k8s_namespace="acme-hello",
        provisioning_status="ready",
        default_tenant_cluster=cluster,
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://hello.example.com",
    )
    workload = Workload.objects.create(
        registered_app=app,
        name="api",
        slug="api",
        kind="deployment",
        replicas=replicas,
        hpa_min_replicas=hpa_min,
        hpa_max_replicas=hpa_max,
        hpa_target_cpu_pct=hpa_target,
    )
    return org, app, workload


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ----------------------------------------------------------------------
# Resolver
# ----------------------------------------------------------------------


def test_scaling_status_unknown_workload_returns_null(permission_resolver):
    org, _app, _w = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with _tenant(org):
        result = RegistryQuery().astrolift_workload_scaling_status(
            _info(),
            app_slug="nope",
            workload_slug="nope",
        )
    assert result is None


def test_scaling_status_without_hpa(permission_resolver):
    """No HPA fields → ``hpa_enabled`` False, manifest replicas
    populate live fields when driver lookup fails."""
    org, app, w = _scaffold(hpa_min=None, hpa_max=None, replicas=3)
    permission_resolver.grant(Permission.APP_READ)
    # Force the live-read path to fail so we exercise the manifest
    # fallback.
    with (
        patch(
            "astrolift_registry.schema.queries._read_live_replicas",
            side_effect=RuntimeError("cluster unwired"),
        ),
        _tenant(org),
    ):
        result = RegistryQuery().astrolift_workload_scaling_status(
            _info(),
            app_slug=app.slug,
            workload_slug=w.slug,
        )
    assert result is not None
    assert result.hpa_enabled is False
    assert result.hpa_min_replicas is None
    assert result.hpa_max_replicas is None
    assert result.current_replicas == 3
    assert result.desired_replicas == 3
    assert result.is_scaling is False
    # Default ceiling = 20 (DEFAULT_MAX_REPLICAS); manifest doesn't
    # tighten it so we expect the full default.
    assert result.replica_upper_bound == 20


def test_scaling_status_with_hpa_caps_slider_at_hpa_max(permission_resolver):
    """When HPA is enabled the slider cap is the HPA max so manual
    scales don't immediately get reconciled away."""
    org, app, w = _scaffold(hpa_min=2, hpa_max=5)
    permission_resolver.grant(Permission.APP_READ)
    with (
        patch(
            "astrolift_registry.schema.queries._read_live_replicas",
            return_value=(3, 3),
        ),
        _tenant(org),
    ):
        result = RegistryQuery().astrolift_workload_scaling_status(
            _info(),
            app_slug=app.slug,
            workload_slug=w.slug,
        )
    assert result is not None
    assert result.hpa_enabled is True
    assert result.hpa_min_replicas == 2
    assert result.hpa_max_replicas == 5
    assert result.hpa_target_cpu_pct == 75
    assert result.replica_upper_bound == 5  # capped at HPA max
    assert result.replica_lower_bound == 0  # scale-to-zero allowed


def test_scaling_status_is_scaling_when_current_differs_from_desired(permission_resolver):
    """``is_scaling`` flips True on a current/desired delta."""
    org, app, w = _scaffold(hpa_min=2, hpa_max=8)
    permission_resolver.grant(Permission.APP_READ)
    with (
        patch(
            "astrolift_registry.schema.queries._read_live_replicas",
            return_value=(2, 5),
        ),
        _tenant(org),
    ):
        result = RegistryQuery().astrolift_workload_scaling_status(
            _info(),
            app_slug=app.slug,
            workload_slug=w.slug,
        )
    assert result is not None
    assert result.current_replicas == 2
    assert result.desired_replicas == 5
    assert result.is_scaling is True


def test_scaling_status_live_read_failure_falls_back_to_manifest(permission_resolver):
    """Driver-side exception is swallowed — the resolver returns the
    manifest replica count so the UI stays renderable during a
    cluster outage."""
    org, app, w = _scaffold(replicas=4)
    permission_resolver.grant(Permission.APP_READ)
    with (
        patch(
            "astrolift_registry.schema.queries._read_live_replicas",
            side_effect=Exception("cluster timeout"),
        ),
        _tenant(org),
    ):
        result = RegistryQuery().astrolift_workload_scaling_status(
            _info(),
            app_slug=app.slug,
            workload_slug=w.slug,
        )
    assert result is not None
    assert result.current_replicas == 4
    assert result.desired_replicas == 4
    assert result.is_scaling is False


def test_scaling_status_permission_denied_raises(permission_resolver):
    """Without ``APP_READ`` the decorator raises — the resolver itself
    never sees the call."""
    org, app, w = _scaffold()
    # No grant.
    with _tenant(org), pytest.raises(PermissionDenied):
        RegistryQuery().astrolift_workload_scaling_status(
            _info(),
            app_slug=app.slug,
            workload_slug=w.slug,
        )
