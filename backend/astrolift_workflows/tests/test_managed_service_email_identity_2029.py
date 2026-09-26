"""AWS SES: a tenant cannot adopt another org's sending identity (#2029).

The driver's own ownership-tag check (#1961's ``adoption_refusal``) only sees
AWS's state, so it refuses to *adopt* a foreign identity once
``create_email_identity`` / ``get_email_identity`` actually runs. This is the
earlier gate: refuse dispatching a provision at all when a different,
still-live ``ManagedService`` already holds the identity the spec derives (or
the operator typed), so a same-account collision never reaches AWS.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_workflows.activities.managed_service_lifecycle import (
    ManagedServicePreflightError,
    _provision_sync,
)

pytestmark = pytest.mark.django_db


class _ReachedDriverResolution(Exception):
    """Marker raised in place of the real driver resolution so a test can
    prove execution got past the identity-ownership preflight."""


def _driver_must_not_run(*_args, **_kwargs):
    raise AssertionError("the driver was resolved for an identity another live service already holds")


def _reached_driver_resolution(*_args, **_kwargs):
    raise _ReachedDriverResolution()


def _world(*, slug: str):
    org = Organization.objects.create(name=slug, slug=slug)
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{slug}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{slug}")
    ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="AWS 2029", slug="aws-2029", plugin_version="0.0.1")],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"{slug}-cluster",
        name="AWS",
        provider_plugin=ProviderPlugin.objects.get(slug="aws-2029"),
        endpoint="https://cluster.invalid",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug=f"app-{slug}",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    return SimpleNamespace(org=org, team=team, project=project, app=app, env=env, cluster=cluster)


def _email_service(
    world, *, identity: str, name: str, backend_ref: str = "", status=ManagedService.Status.PENDING
):
    return ManagedService.objects.create(
        registered_app=world.app,
        app_environment=world.env,
        kind=ManagedService.Kind.EMAIL,
        variant="ses",
        name=name,
        config={"identity": identity},
        backend_ref=backend_ref,
        status=status,
    )


def test_provision_refuses_an_identity_another_live_service_holds():
    victim = _world(slug="acme-2029")
    _email_service(
        victim,
        identity="shared.example.com",
        name="victim",
        backend_ref="email/shared.example.com",
        status=ManagedService.Status.ACTIVE,
    )

    attacker = _world(slug="villain-2029")
    attacker_svc = _email_service(attacker, identity="shared.example.com", name="attacker")

    with (
        patch(
            "astrolift_drivers.managed_resolution.resolve_managed_driver",
            side_effect=_driver_must_not_run,
        ),
        pytest.raises(ManagedServicePreflightError, match="shared.example.com"),
    ):
        _provision_sync(attacker_svc.pk)


def test_provision_refuses_the_platforms_own_base_domain_identity():
    """A live row need not belong to a *tenant* org -- an install-owned
    ``ManagedService`` row for the platform's own base sending domain
    refuses a tenant naming it too."""
    platform = _world(slug="platform-2029")
    _email_service(
        platform,
        identity="mail.astrolift.example",
        name="platform-sender",
        backend_ref="email/mail.astrolift.example",
        status=ManagedService.Status.ACTIVE,
    )

    tenant = _world(slug="tenant-2029")
    tenant_svc = _email_service(tenant, identity="mail.astrolift.example", name="tenant-sender")

    with (
        patch(
            "astrolift_drivers.managed_resolution.resolve_managed_driver",
            side_effect=_driver_must_not_run,
        ),
        pytest.raises(ManagedServicePreflightError, match="mail.astrolift.example"),
    ):
        _provision_sync(tenant_svc.pk)


def test_provision_does_not_refuse_when_no_other_service_holds_the_identity():
    """No collision: the preflight must not block a normal provision. Proven
    by patching driver resolution to raise a distinct marker -- reaching it
    means the identity-ownership check let this call through."""
    world = _world(slug="solo-2029")
    svc = _email_service(world, identity="solo.example.com", name="solo")

    with (
        patch(
            "astrolift_drivers.managed_resolution.resolve_managed_driver",
            side_effect=_reached_driver_resolution,
        ),
        pytest.raises(_ReachedDriverResolution),
    ):
        _provision_sync(svc.pk)


def test_provision_does_not_refuse_a_services_own_previously_held_identity():
    """A row reprovisioning the identity it already holds must not be
    refused against itself (``.exclude(pk=svc.pk)``)."""
    world = _world(slug="reprovision-2029")
    svc = _email_service(
        world,
        identity="own.example.com",
        name="own",
        backend_ref="email/own.example.com",
        status=ManagedService.Status.ACTIVE,
    )

    with (
        patch(
            "astrolift_drivers.managed_resolution.resolve_managed_driver",
            side_effect=_reached_driver_resolution,
        ),
        pytest.raises(_ReachedDriverResolution),
    ):
        _provision_sync(svc.pk)


def test_provision_ignores_a_soft_deleted_holder():
    """A holder that has been soft-deleted is not live; it must not refuse
    a fresh service for the same identity."""
    former = _world(slug="former-2029")
    stale = _email_service(
        former,
        identity="recycled.example.com",
        name="stale",
        backend_ref="email/recycled.example.com",
        status=ManagedService.Status.ACTIVE,
    )
    stale.soft_delete()

    new_owner = _world(slug="new-owner-2029")
    svc = _email_service(new_owner, identity="recycled.example.com", name="fresh")

    with (
        patch(
            "astrolift_drivers.managed_resolution.resolve_managed_driver",
            side_effect=_reached_driver_resolution,
        ),
        pytest.raises(_ReachedDriverResolution),
    ):
        _provision_sync(svc.pk)


def test_provision_ignores_other_kinds_sharing_the_same_backend_ref_string():
    """The guard is scoped to (kind, variant) == (email, ses); it must not
    misfire by matching a same-named handle stored under a different
    managed-service kind."""
    world = _world(slug="cross-kind-2029")
    ManagedService.objects.create(
        registered_app=world.app,
        app_environment=world.env,
        kind=ManagedService.Kind.QUEUE,
        variant="sqs",
        name="not-email",
        config={},
        backend_ref="email/not-really-an-email-identity",
        status=ManagedService.Status.ACTIVE,
    )
    svc = _email_service(world, identity="not-really-an-email-identity", name="fresh")

    with (
        patch(
            "astrolift_drivers.managed_resolution.resolve_managed_driver",
            side_effect=_reached_driver_resolution,
        ),
        pytest.raises(_ReachedDriverResolution),
    ):
        _provision_sync(svc.pk)
