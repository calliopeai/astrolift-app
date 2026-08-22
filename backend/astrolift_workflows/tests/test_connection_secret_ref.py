"""Provision finalize writes ``connection_secret_ref`` (#25).

The column had no production writer, so ``revealManagedServiceConnection``
took its else-branch and answered ``placeholder:pending`` for every key of
every managed service in every org: an operator could provision a Postgres,
watch it go ready, ask the platform where its credentials live, and be told
"pending" forever. The disclosure audit row emitted alongside recorded an
empty ref too, so the trail could not say which secret was revealed.

``_sync_binding_rows`` is patched out throughout: it needs a live driver for
the (kind, variant) pair, and the env/volume envelope it writes is covered by
its own tests.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_services.schema.mutations import (
    RevealManagedServiceConnectionInput,
    ServicesMutation,
)
from astrolift_workflows.activities.managed_service_lifecycle import _finalize_provision_sync
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

_SYNC_BINDINGS = "astrolift_workflows.activities.managed_service_lifecycle._sync_binding_rows"


def _plugin() -> ProviderPlugin:
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    return ProviderPlugin.objects.get(slug="aws")


def _app_service(suffix: str) -> ManagedService:
    org = Organization.objects.create(name=f"Acme {suffix}", slug=f"acme-csr-{suffix}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-csr-{suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-csr-{suffix}")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"eks-csr-{suffix}",
        name="EKS",
        provider_plugin=_plugin(),
        endpoint="https://eks.example.com",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Shop",
        slug=f"shop-csr-{suffix}",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        variant="rds",
        status=ManagedService.Status.PROVISIONING,
    )


def test_finalize_records_where_the_connection_material_lives():
    svc = _app_service("a")

    with patch(_SYNC_BINDINGS):
        _finalize_provision_sync(svc.pk, "postgres/primary")

    svc.refresh_from_db()
    assert svc.connection_secret_ref == "astrolift/production/acme-csr-a/shop-csr-a/managed-services/primary"


def test_a_project_owned_service_is_pathed_under_its_project():
    org = Organization.objects.create(name="Acme P", slug="acme-csr-p")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-csr-p")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-csr-p")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="eks-csr-p",
        name="EKS",
        provider_plugin=_plugin(),
        endpoint="https://eks.example.com",
    )
    svc = ManagedService.objects.create(
        project=project,
        tenant_cluster=cluster,
        environment_name="staging",
        kind=ManagedService.Kind.REDIS,
        name="shared-cache",
        variant="elasticache",
        status=ManagedService.Status.PROVISIONING,
    )

    with patch(_SYNC_BINDINGS):
        _finalize_provision_sync(svc.pk, "redis/shared-cache")

    svc.refresh_from_db()
    assert (
        svc.connection_secret_ref == "astrolift/staging/acme-csr-p/demo-csr-p/managed-services/shared-cache"
    )


def test_reveal_points_at_the_secret_instead_of_pending(permission_resolver):
    svc = _app_service("r")
    org = svc.registered_app.organization
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="csr-reveal", defaults={"email": "csr@example.com"})
    info = SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user, META={})))

    with patch(_SYNC_BINDINGS):
        _finalize_provision_sync(svc.pk, "postgres/primary")

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ServicesMutation().reveal_managed_service_connection(
            info,
            input=RevealManagedServiceConnectionInput(managed_service_id=GUID(str(svc.guid))),
        )

    assert result.ok, result.errors
    assert result.data is not None
    expected = "astrolift/production/acme-csr-r/shop-csr-r/managed-services/primary"
    assert result.data.connection_secret_ref == expected
    password = next(k for k in result.data.keys if k.key == "POSTGRES_PASSWORD")
    assert password.value == f"secret-ref:{expected}#POSTGRES_PASSWORD"
