"""Resolver tests for the #377 observability cards on the app
detail page: ``astrolift_app_dns_records``,
``astrolift_app_certificates``, ``astrolift_app_identity_binding``.

The resolvers dispatch through ``core.app_deploy.driver_for_capability``;
these tests monkeypatch that helper so the test exercises the
strawberry → SDK-dataclass conversion + the not-implemented degradation
path without standing up real provider plugins. Driver-side behaviour
is covered by the AWS driver tests in ``astrolift-providers``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_lifecycle.schema.types import (
    AppCertificateType,
    AppDnsRecordType,
    AppIdentityBindingType,
)
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _tenant_for(org, actor):
    return tenant_context(
        TenantContext(organization_id=org.id, actor_user_id=actor.id),
    )


def _grant_read(resolver):
    resolver.grant(Permission.APP_READ)


def _bind_default_cluster(app, env):
    """Wire the env's cluster as the app's default so the resolver
    has a cluster to dispatch through. Mirrors the same shape the
    pod-resolver tests use (``app_pods_returns_stub_pods``)."""
    app.default_tenant_cluster = env.tenant_cluster
    app.save(update_fields=["default_tenant_cluster"])


@pytest.fixture
def patch_driver(monkeypatch):
    """Replace ``driver_for_capability`` with a recorder.

    Returns a ``configure(capability, driver)`` helper the test calls
    with the driver shim it wants. Multiple capabilities can be
    configured in a single test; the recorder dispatches by the
    capability the resolver asks for."""
    drivers: dict[str, object] = {}

    def _resolver(cluster, capability):  # noqa: ARG001 — cluster unused in tests
        if capability not in drivers:
            from core.app_deploy import AppDeployError

            raise AppDeployError(f"no fake driver for capability={capability!r}")
        return drivers[capability]

    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability",
        _resolver,
    )

    def _configure(capability: str, driver: object) -> None:
        drivers[capability] = driver

    return _configure


# ---------------------------------------------------------------------------
# astrolift_app_dns_records
# ---------------------------------------------------------------------------


def test_dns_records_returns_converted_shape(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    """Happy path — driver returns DnsRecord dataclasses, resolver
    converts to AppDnsRecordType with camelCase fields preserved
    (strawberry handles the wire conversion)."""
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    fake = SimpleNamespace(
        list_records_for_app=lambda zone_or_app: [
            SimpleNamespace(
                name="api",
                type="A",
                value="192.0.2.1",
                ttl=60,
                propagation_status="propagated",
            ),
            SimpleNamespace(
                name="worker",
                type="A",
                value="192.0.2.2",
                ttl=60,
                propagation_status="unknown",
            ),
        ],
    )
    patch_driver("dns", fake)

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_dns_records(fake_info, app_slug=app.slug)
    assert all(isinstance(r, AppDnsRecordType) for r in result)
    assert [r.name for r in result] == ["api", "worker"]
    assert result[0].propagation_status == "propagated"
    assert result[1].propagation_status == "unknown"


def test_dns_records_empty_when_no_cluster(
    org,
    app,
    actor,
    fake_info,
    permission_resolver,
):
    """App with no default cluster → empty list, no error. The FE
    renders the empty state with the deep-link to "Set up DNS"."""
    _grant_read(permission_resolver)
    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_dns_records(fake_info, app_slug=app.slug)
    assert result == []


def test_dns_records_degrades_on_not_implemented(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    """Non-AWS driver raising NotImplementedError degrades to an empty
    list — the FE's empty state copy says "not yet supported on this
    cloud"."""
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    def _not_impl(zone_or_app):
        raise NotImplementedError("not implemented for this driver")

    fake = SimpleNamespace(list_records_for_app=_not_impl)
    patch_driver("dns", fake)

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_dns_records(fake_info, app_slug=app.slug)
    assert result == []


def test_dns_records_swallows_driver_errors(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    """A boto3 timeout / API error during the driver call must not
    break the resolver — the platform-event log carries the
    diagnostic. Same swallow shape as ``astrolift_app_pods``."""
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    def _explode(zone_or_app):
        raise RuntimeError("connection refused")

    fake = SimpleNamespace(list_records_for_app=_explode)
    patch_driver("dns", fake)

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_dns_records(fake_info, app_slug=app.slug)
    assert result == []


def test_dns_records_returns_empty_when_no_dns_capability(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    """Cluster's plugin doesn't register a 'dns' driver — empty list,
    not an error. The FE renders the empty state."""
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)
    # patch_driver registers nothing for 'dns', so the resolver path
    # raises AppDeployError under the hood.
    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_dns_records(fake_info, app_slug=app.slug)
    assert result == []


def test_dns_records_denies_without_permission(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
):
    """Resolver-entry permission check fires before any driver work."""
    _bind_default_cluster(app, env)
    q = LifecycleQuery()
    with _tenant_for(org, actor), pytest.raises(PermissionDenied):
        q.astrolift_app_dns_records(fake_info, app_slug=app.slug)


def test_dns_records_uses_environment_cluster_when_given(
    org,
    app,
    env,
    env_requires_approval,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    """Naming an environment switches the cluster the resolver
    dispatches against."""
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    saw: dict[str, object] = {}

    def _list(zone_or_app):
        saw["zone_or_app"] = zone_or_app
        return []

    patch_driver("dns", SimpleNamespace(list_records_for_app=_list))

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        q.astrolift_app_dns_records(
            fake_info,
            app_slug=app.slug,
            environment_name=env_requires_approval.name,
        )
    assert saw["zone_or_app"] == app.slug


# ---------------------------------------------------------------------------
# astrolift_app_certificates
# ---------------------------------------------------------------------------


def test_certificates_returns_converted_shape(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    fake = SimpleNamespace(
        list_certificates=lambda filter_hostname=None: [
            SimpleNamespace(
                id="arn:aws:acm:us-east-1:123:certificate/abc",
                hostname="api.acme.example",
                issuer="Amazon",
                not_after="2030-01-01T00:00:00Z",
                days_until_expiry=365,
                renewal_status="auto",
            ),
        ],
    )
    patch_driver("tls", fake)

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_certificates(fake_info, app_slug=app.slug)
    assert len(result) == 1
    assert isinstance(result[0], AppCertificateType)
    assert result[0].hostname == "api.acme.example"
    assert result[0].renewal_status == "auto"
    assert result[0].days_until_expiry == 365


def test_certificates_passes_hostname_filter(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    """The resolver passes the app slug as the hostname filter so a
    multi-tenant ACM account doesn't dump every cert into the card."""
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    saw: dict[str, object] = {}

    def _list(filter_hostname=None):
        saw["filter_hostname"] = filter_hostname
        return []

    patch_driver("tls", SimpleNamespace(list_certificates=_list))

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        q.astrolift_app_certificates(fake_info, app_slug=app.slug)
    assert saw["filter_hostname"] == app.slug


def test_certificates_degrades_on_not_implemented(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    def _not_impl(filter_hostname=None):
        raise NotImplementedError("not implemented for this driver")

    patch_driver("tls", SimpleNamespace(list_certificates=_not_impl))

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_certificates(fake_info, app_slug=app.slug)
    assert result == []


def test_certificates_empty_when_no_cluster(
    org,
    app,
    actor,
    fake_info,
    permission_resolver,
):
    _grant_read(permission_resolver)
    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_certificates(fake_info, app_slug=app.slug)
    assert result == []


def test_certificates_swallows_driver_errors(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    def _explode(filter_hostname=None):
        raise RuntimeError("acm 5xx")

    patch_driver("tls", SimpleNamespace(list_certificates=_explode))

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_certificates(fake_info, app_slug=app.slug)
    assert result == []


def test_certificates_denies_without_permission(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
):
    _bind_default_cluster(app, env)
    q = LifecycleQuery()
    with _tenant_for(org, actor), pytest.raises(PermissionDenied):
        q.astrolift_app_certificates(fake_info, app_slug=app.slug)


# ---------------------------------------------------------------------------
# astrolift_app_identity_binding
# ---------------------------------------------------------------------------


def test_identity_binding_returns_converted_shape(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    fake = SimpleNamespace(
        describe_identity=lambda app_slug: SimpleNamespace(
            kind="irsa",
            role_arn_or_principal=f"arn:aws:iam::123:role/astrolift-{app_slug}",
            trust_policy_summary="OIDC trust: system:serviceaccount:acme:api",
            last_used_at="2026-05-15T12:00:00Z",
        ),
    )
    patch_driver("identity", fake)

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_identity_binding(fake_info, app_slug=app.slug)
    assert isinstance(result, AppIdentityBindingType)
    assert result.kind == "irsa"
    assert app.slug in result.role_arn_or_principal
    assert "system:serviceaccount" in result.trust_policy_summary
    assert result.last_used_at == "2026-05-15T12:00:00Z"


def test_identity_binding_returns_none_when_driver_returns_none(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    """Driver returns None (no role provisioned) → resolver returns
    None so the FE renders the "Configure WI" empty state."""
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    patch_driver("identity", SimpleNamespace(describe_identity=lambda app_slug: None))

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_identity_binding(fake_info, app_slug=app.slug)
    assert result is None


def test_identity_binding_degrades_on_not_implemented(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    def _not_impl(app_slug):
        raise NotImplementedError("not implemented for this driver")

    patch_driver("identity", SimpleNamespace(describe_identity=_not_impl))

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_identity_binding(fake_info, app_slug=app.slug)
    assert result is None


def test_identity_binding_empty_when_no_cluster(
    org,
    app,
    actor,
    fake_info,
    permission_resolver,
):
    _grant_read(permission_resolver)
    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_identity_binding(fake_info, app_slug=app.slug)
    assert result is None


def test_identity_binding_swallows_driver_errors(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    def _explode(app_slug):
        raise RuntimeError("iam 5xx")

    patch_driver("identity", SimpleNamespace(describe_identity=_explode))

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_identity_binding(fake_info, app_slug=app.slug)
    assert result is None


def test_identity_binding_denies_without_permission(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
):
    _bind_default_cluster(app, env)
    q = LifecycleQuery()
    with _tenant_for(org, actor), pytest.raises(PermissionDenied):
        q.astrolift_app_identity_binding(fake_info, app_slug=app.slug)
