"""The two API channels that feed the isolation resolver (#16).

``ProvisionSpec.isolation`` is resolved at provision time from the row's own
request and the org's compliance floor. Neither had anywhere to be stored, so
neither could be asked for; these cover the boundary that now records them,
including the refusal, because an unparseable mode reaching the resolver would
surface as a retrying Temporal activity rather than a message to whoever typed
it.

The request is deliberately not part of ``config``: driver config schemas are
per-variant and several close themselves to extra keys, so a portable request
cannot travel inside one.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_identity.schema.mutations import IdentityMutation, UpdateOrganizationInput
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_services.schema.mutations import (
    ProvisionManagedServiceInput,
    ServicesMutation,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username="iso-request",
        defaults={"email": "iso-request@example.com"},
    )
    return SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user, META={})))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme-iso-req")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-iso-req")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-iso-req")
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="K8s Native",
                slug="k8s-native",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="local-iso-req",
        name="Local",
        provider_plugin=ProviderPlugin.objects.get(slug="k8s-native"),
        endpoint="http://localhost:8443",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Iso App",
        slug="iso-app",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    return org, app, env


def _provision(org, app, env, isolation):
    with _ctx(org), patch("astrolift_workflows.client.start_workflow"):
        return ServicesMutation().provision_managed_service(
            _info(),
            input=ProvisionManagedServiceInput(
                app_slug=app.slug,
                environment_name=env.name,
                kind="postgres",
                name="records",
                variant="rds",
                isolation=isolation,
            ),
        )


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def test_provision_records_the_requested_mode(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    result = _provision(org, app, env, "dedicated")

    assert result.ok, result.errors
    svc = ManagedService.objects.get(registered_app=app, name="records")
    assert svc.isolation == "dedicated"
    assert result.data is not None
    assert result.data.isolation == "dedicated"


def test_provision_without_a_request_leaves_the_mode_unspecified(permission_resolver):
    # Blank, not "shared": the org's floor still gets to decide at provision
    # time, and storing "shared" here would silently outvote it.
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    result = _provision(org, app, env, None)

    assert result.ok, result.errors
    assert ManagedService.objects.get(registered_app=app, name="records").isolation == ""


def test_provision_refuses_a_mode_it_cannot_parse(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    result = _provision(org, app, env, "sort-of-dedicated")

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "isolation"
    assert not ManagedService.objects.filter(registered_app=app, name="records").exists()


def test_org_policy_stores_a_normalized_floor(permission_resolver):
    org, _app, _env = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)

    with _ctx(org):
        result = IdentityMutation().update_organization(
            _info(),
            input=UpdateOrganizationInput(
                id=str(org.guid),
                managed_service_isolation_policy={"postgres": "dedicated"},
            ),
        )

    assert result.ok, result.errors
    org.refresh_from_db()
    assert org.managed_service_isolation_policy == {"postgres": "dedicated"}


@pytest.mark.parametrize(
    ("policy", "reason"),
    [
        ({"postgres": "isolated"}, "mode is not a mode"),
        ({"postgres": None}, "mode is missing"),
        ({"pastgres": "dedicated"}, "kind is a typo"),
        ("dedicated", "policy is not a map"),
    ],
)
def test_org_policy_refuses_what_the_resolver_could_not_read(permission_resolver, policy, reason):
    org, _app, _env = _scaffold()
    permission_resolver.grant(Permission.ORG_UPDATE)

    with _ctx(org):
        result = IdentityMutation().update_organization(
            _info(),
            input=UpdateOrganizationInput(
                id=str(org.guid),
                managed_service_isolation_policy=policy,
            ),
        )

    assert not result.ok, reason
    assert result.errors[0].field == "managedServiceIsolationPolicy"
    org.refresh_from_db()
    assert org.managed_service_isolation_policy == {}
