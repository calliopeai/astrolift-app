"""Tests for #731 — cached TLS cert observability metadata on AppDomain.

The resolver pulls a cached snapshot from CustomDomain columns; on TTL
expiry it dispatches through the TLS driver's ``list_certificates`` and
persists the new snapshot back to the row. Errors swallow so the page
keeps rendering.

We monkeypatch ``core.app_deploy.driver_for_capability`` so we don't
need a real provider plugin standing up. The cache + persistence
behaviour runs against real Postgres.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_lifecycle.models import CustomDomain
from astrolift_lifecycle.schema.queries import LifecycleQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _tenant_for(org, actor):
    return tenant_context(
        TenantContext(organization_id=org.id, actor_user_id=actor.id),
    )


def _bind_default_cluster(app, env):
    app.default_tenant_cluster = env.tenant_cluster
    app.save(update_fields=["default_tenant_cluster"])


@pytest.fixture
def patch_driver(monkeypatch):
    drivers: dict[str, object] = {}

    def _resolver(cluster, capability):  # noqa: ARG001
        if capability not in drivers:
            from core.app_deploy import AppDeployError

            raise AppDeployError(f"no fake driver for {capability!r}")
        return drivers[capability]

    monkeypatch.setattr("core.app_deploy.driver_for_capability", _resolver)

    def _configure(capability: str, driver: object) -> None:
        drivers[capability] = driver

    return _configure


@pytest.fixture
def domain(app):
    """A CustomDomain row in the 'active' cert state — refresh path
    will probe the driver for cert metadata."""
    return CustomDomain.objects.create(
        registered_app=app,
        hostname="api.acme.example",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
    )


# ---- refresh on first read --------------------------------------------------


def test_first_read_pulls_metadata_from_driver(
    org, app, env, actor, domain, fake_info, permission_resolver, patch_driver
):
    permission_resolver.grant(Permission.APP_READ)
    _bind_default_cluster(app, env)

    fake_cert = SimpleNamespace(
        id="arn:aws:acm:us-east-1:123:certificate/abc",
        hostname="api.acme.example",
        issuer="Amazon",
        not_after="2030-01-01T00:00:00Z",
        days_until_expiry=365,
        renewal_status="auto",
    )
    patch_driver("tls", SimpleNamespace(list_certificates=lambda filter_hostname=None: [fake_cert]))

    with _tenant_for(org, actor):
        result = LifecycleQuery().astrolift_app_domains(fake_info, app_slug=app.slug)

    assert len(result) == 1
    row = result[0]
    assert row.cert_expires_at == dt.datetime(2030, 1, 1, 0, 0, tzinfo=dt.UTC)
    assert row.cert_issuer_serial == fake_cert.id
    assert row.cert_observability_status == "auto"
    # Verify persistence.
    domain.refresh_from_db()
    assert domain.cert_metadata_refreshed_at is not None


def test_cached_read_skips_driver_call_within_ttl(
    org, app, env, actor, domain, fake_info, permission_resolver, patch_driver
):
    """A second read within 1h returns cached values without
    re-calling the driver — verified by raising in the stub."""
    permission_resolver.grant(Permission.APP_READ)
    _bind_default_cluster(app, env)

    # Pre-populate the row with a fresh timestamp + values.
    domain.cert_expires_at = dt.datetime(2027, 6, 1, 0, 0, tzinfo=dt.UTC)
    domain.cert_issuer_serial = "cached-serial"
    domain.cert_observability_status = "auto"
    domain.cert_metadata_refreshed_at = timezone.now()
    domain.save(
        update_fields=[
            "cert_expires_at",
            "cert_issuer_serial",
            "cert_observability_status",
            "cert_metadata_refreshed_at",
            "updated_at",
            "version",
        ]
    )

    def _boom(filter_hostname=None):
        raise AssertionError("driver should not be called within TTL")

    patch_driver("tls", SimpleNamespace(list_certificates=_boom))

    with _tenant_for(org, actor):
        result = LifecycleQuery().astrolift_app_domains(fake_info, app_slug=app.slug)
    assert result[0].cert_expires_at == dt.datetime(2027, 6, 1, 0, 0, tzinfo=dt.UTC)
    assert result[0].cert_issuer_serial == "cached-serial"


def test_stale_cache_triggers_refresh(
    org, app, env, actor, domain, fake_info, permission_resolver, patch_driver
):
    permission_resolver.grant(Permission.APP_READ)
    _bind_default_cluster(app, env)

    # Mark the row as having a >1h-old refresh.
    domain.cert_metadata_refreshed_at = timezone.now() - dt.timedelta(hours=2)
    domain.cert_issuer_serial = "old-serial"
    domain.save(
        update_fields=[
            "cert_metadata_refreshed_at",
            "cert_issuer_serial",
            "updated_at",
            "version",
        ]
    )

    fake_cert = SimpleNamespace(
        id="new-serial",
        hostname="api.acme.example",
        issuer="Amazon",
        not_after="2027-12-31T00:00:00Z",
        days_until_expiry=365,
        renewal_status="auto",
    )
    patch_driver("tls", SimpleNamespace(list_certificates=lambda filter_hostname=None: [fake_cert]))

    with _tenant_for(org, actor):
        result = LifecycleQuery().astrolift_app_domains(fake_info, app_slug=app.slug)
    assert result[0].cert_issuer_serial == "new-serial"


# ---- error paths ------------------------------------------------------------


def test_driver_unsupported_swallows_and_returns_row(
    org, app, env, actor, domain, fake_info, permission_resolver, patch_driver
):
    """Driver raising NotImplementedError shouldn't 502 the page —
    the row is still returned, just without fresh metadata."""
    permission_resolver.grant(Permission.APP_READ)
    _bind_default_cluster(app, env)

    def _not_impl(filter_hostname=None):
        raise NotImplementedError("driver doesn't expose certs")

    patch_driver("tls", SimpleNamespace(list_certificates=_not_impl))

    with _tenant_for(org, actor):
        result = LifecycleQuery().astrolift_app_domains(fake_info, app_slug=app.slug)
    assert len(result) == 1
    assert result[0].cert_expires_at is None
    assert result[0].cert_issuer_serial == ""


def test_driver_exception_swallows(
    org, app, env, actor, domain, fake_info, permission_resolver, patch_driver
):
    permission_resolver.grant(Permission.APP_READ)
    _bind_default_cluster(app, env)

    def _explode(filter_hostname=None):
        raise RuntimeError("ACM down")

    patch_driver("tls", SimpleNamespace(list_certificates=_explode))

    with _tenant_for(org, actor):
        result = LifecycleQuery().astrolift_app_domains(fake_info, app_slug=app.slug)
    assert len(result) == 1


def test_not_requested_state_skips_refresh(
    org, app, env, actor, fake_info, permission_resolver, patch_driver
):
    """A domain in 'not_requested' state has no upstream cert; we
    shouldn't probe the driver for it."""
    permission_resolver.grant(Permission.APP_READ)
    _bind_default_cluster(app, env)
    CustomDomain.objects.create(
        registered_app=app,
        hostname="pending.acme.example",
        validation_status=CustomDomain.ValidationStatus.PENDING,
        certificate_state=CustomDomain.CertificateState.NOT_REQUESTED,
    )

    def _boom(filter_hostname=None):
        raise AssertionError("should not call driver for not_requested")

    patch_driver("tls", SimpleNamespace(list_certificates=_boom))

    with _tenant_for(org, actor):
        result = LifecycleQuery().astrolift_app_domains(fake_info, app_slug=app.slug)
    assert any(d.hostname == "pending.acme.example" for d in result)


def test_no_cluster_swallows(org, app, actor, domain, fake_info, permission_resolver):
    """When the app has no resolvable cluster, the refresher skips
    cleanly without raising."""
    permission_resolver.grant(Permission.APP_READ)
    # Intentionally NOT binding a cluster.
    with _tenant_for(org, actor):
        result = LifecycleQuery().astrolift_app_domains(fake_info, app_slug=app.slug)
    assert len(result) == 1
    assert result[0].cert_expires_at is None
