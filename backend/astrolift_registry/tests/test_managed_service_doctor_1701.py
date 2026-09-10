"""The doctor names a managed service that was never provisioned (#1701).

An unprovisioned ``ManagedService`` produces two silent consequences: its
binding secret is created empty, so the app gets no host or bucket name,
and the workload's identity role is created with no policy attached,
because grants are read off the binding a provisioned service would have.
The app then fails at runtime with AccessDenied or a missing env var, one
layer away from the cause.

``backend_ref`` is the platform's own record that the cloud resource
exists, so the state is knowable from the row -- no live call.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_registry.services.app_doctor import (
    CHECK_MANAGED_SERVICES,
    _check_managed_services,
)
from astrolift_services.models import ManagedService

pytestmark = pytest.mark.django_db


@pytest.fixture
def app(db):
    org = Organization.objects.create(name="Acme", slug="acme-1701")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-1701")
    project = Project.objects.create(organization=org, team=team, name="P", slug="p-1701")
    row = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Quake Dash",
        slug="quake-dash-1701",
        provisioning_status="ready",
    )
    row.environment = AppEnvironment.objects.create(
        registered_app=row,
        tenant_cluster=make_cluster_for(org),
        name="prod",
    )
    return row


def make_cluster_for(org):
    from astrolift_clusters.models import ProviderPlugin, TenantCluster

    plugin = ProviderPlugin(
        name="K8s",
        slug="k8s-1701",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    return TenantCluster.objects.create(
        organization=org,
        name="prod",
        slug="prod-1701",
        provider_plugin=ProviderPlugin.objects.get(slug="k8s-1701"),
        provider_config={},
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )


def _service(app, *, name, kind="object_store", backend_ref=""):
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=app.environment,
        kind=kind,
        name=name,
        backend_ref=backend_ref,
    )


def test_an_app_with_no_managed_services_is_skipped(app):
    check = _check_managed_services(app)
    assert (check.key, check.status) == (CHECK_MANAGED_SERVICES, "skip")


def test_a_provisioned_service_passes(app):
    _service(app, name="assets", backend_ref="arn:aws:s3:::acme-assets")

    check = _check_managed_services(app)

    assert check.status == "pass"


def test_an_unprovisioned_service_fails_and_names_it(app):
    _service(app, name="assets")

    check = _check_managed_services(app)

    assert check.status == "fail"
    assert "assets" in check.detail
    assert "identity" in check.detail


def test_a_partially_provisioned_app_reports_only_the_missing_ones(app):
    _service(app, name="assets", backend_ref="arn:aws:s3:::acme-assets")
    _service(app, name="db", kind="postgres")

    check = _check_managed_services(app)

    assert check.status == "fail"
    assert "1 of 2" in check.detail
    assert "db" in check.detail
    assert "assets" not in check.detail


def test_a_soft_deleted_service_is_not_counted(app):
    from django.utils import timezone

    row = _service(app, name="gone")
    row.deleted_at = timezone.now()
    row.save(update_fields=["deleted_at"])

    check = _check_managed_services(app)

    assert check.status == "skip"
