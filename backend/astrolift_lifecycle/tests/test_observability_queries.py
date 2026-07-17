"""Resolver tests for the #377 / #1111 observability cards on the app
detail page: ``astrolift_app_dns_records``,
``astrolift_app_certificates``, ``astrolift_app_identity_binding``.

The resolvers dispatch through ``core.app_deploy.driver_for_capability``;
these tests monkeypatch that helper so the test exercises the
strawberry -> SDK-dataclass conversion + the reason-discriminated
degradation paths (#1111) without standing up real provider plugins.
Driver-side behaviour is covered by the AWS driver tests in
``astrolift-providers``.

Each resolver now returns a reason-discriminated envelope
(``AppDnsRecordsResult`` / ``AppCertificatesResult`` /
``AppIdentityBindingResult``) instead of a bare list / null, so the FE
can render one honest empty-state message. These tests pin the branch
-> reason mapping.
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
from core.schema.enums import ObservabilityPanelReason
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
    converts to AppDnsRecordType and the envelope reason is OK."""
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
    assert result.reason == ObservabilityPanelReason.OK
    assert all(isinstance(r, AppDnsRecordType) for r in result.records)
    assert [r.name for r in result.records] == ["api", "worker"]
    assert result.records[0].propagation_status == "propagated"
    assert result.records[1].propagation_status == "unknown"


def test_dns_records_no_data_yet_when_driver_returns_empty(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    """Driver call succeeds but returns zero records — the benign
    NO_DATA_YET state, distinct from "not configured"/"not supported"."""
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)
    patch_driver("dns", SimpleNamespace(list_records_for_app=lambda zone_or_app: []))

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_dns_records(fake_info, app_slug=app.slug)
    assert result.reason == ObservabilityPanelReason.NO_DATA_YET
    assert result.records == []


def test_dns_records_not_configured_when_no_cluster(
    org,
    app,
    actor,
    fake_info,
    permission_resolver,
):
    """App with no default cluster → NOT_CONFIGURED, no error. The FE
    renders "Not configured" with the deep-link to "Set up DNS"."""
    _grant_read(permission_resolver)
    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_dns_records(fake_info, app_slug=app.slug)
    assert result.reason == ObservabilityPanelReason.NOT_CONFIGURED
    assert result.records == []


def test_dns_records_not_supported_on_not_implemented(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    """Non-AWS driver raising NotImplementedError → the panel reports
    NOT_SUPPORTED_BY_PROVIDER (only ever surfaced off-AWS), not the
    hedged "either/or" empty state."""
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    def _not_impl(zone_or_app):
        raise NotImplementedError("not implemented for this driver")

    patch_driver("dns", SimpleNamespace(list_records_for_app=_not_impl))

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_dns_records(fake_info, app_slug=app.slug)
    assert result.reason == ObservabilityPanelReason.NOT_SUPPORTED_BY_PROVIDER
    assert result.records == []


def test_dns_records_error_on_driver_exception(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    """A boto3 timeout / API error during the driver call surfaces as
    ERROR (logged), not swallowed to an indistinguishable empty list."""
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    def _explode(zone_or_app):
        raise RuntimeError("connection refused")

    patch_driver("dns", SimpleNamespace(list_records_for_app=_explode))

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_dns_records(fake_info, app_slug=app.slug)
    assert result.reason == ObservabilityPanelReason.ERROR
    assert result.records == []


def test_dns_records_not_configured_when_no_dns_capability(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    """Cluster's plugin doesn't register a 'dns' driver — NOT_CONFIGURED,
    not an error (the capability just isn't wired on this cluster)."""
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)
    # patch_driver registers nothing for 'dns', so the resolver path
    # raises AppDeployError under the hood.
    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_dns_records(fake_info, app_slug=app.slug)
    assert result.reason == ObservabilityPanelReason.NOT_CONFIGURED
    assert result.records == []


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
    assert result.reason == ObservabilityPanelReason.OK
    assert len(result.certificates) == 1
    assert isinstance(result.certificates[0], AppCertificateType)
    assert result.certificates[0].hostname == "api.acme.example"
    assert result.certificates[0].renewal_status == "auto"
    assert result.certificates[0].days_until_expiry == 365


def test_certificates_passes_resolved_host_not_slug(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    """#1111 bug: the resolver used to pass the app *slug* as the cert
    filter, which never matched a real cert (a ``*.zone`` wildcard or a
    ``zone``-suffixed SAN covers ``<subdomain>.<zone>``, not the slug).
    It must now pass the app's resolved public host — here the ``env``
    fixture's URL host ``hello.example.com``."""
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
    assert saw["filter_hostname"] == "hello.example.com"
    assert saw["filter_hostname"] != app.slug


def test_certificates_not_configured_when_no_public_host(
    org,
    app,
    cluster,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    """The app has a cluster but no env URL and no managed subdomain, so
    there's no FQDN to match a cert against — NOT_CONFIGURED, and the
    driver is never called."""
    _grant_read(permission_resolver)
    app.default_tenant_cluster = cluster
    app.save(update_fields=["default_tenant_cluster"])

    called = {"n": 0}

    def _list(filter_hostname=None):
        called["n"] += 1
        return []

    patch_driver("tls", SimpleNamespace(list_certificates=_list))

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_certificates(fake_info, app_slug=app.slug)
    assert result.reason == ObservabilityPanelReason.NOT_CONFIGURED
    assert result.certificates == []
    assert called["n"] == 0


def test_certificates_no_data_yet_when_empty(
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
    patch_driver("tls", SimpleNamespace(list_certificates=lambda filter_hostname=None: []))

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_certificates(fake_info, app_slug=app.slug)
    assert result.reason == ObservabilityPanelReason.NO_DATA_YET
    assert result.certificates == []


def test_certificates_not_supported_on_not_implemented(
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
    assert result.reason == ObservabilityPanelReason.NOT_SUPPORTED_BY_PROVIDER
    assert result.certificates == []


def test_certificates_not_configured_when_no_cluster(
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
    assert result.reason == ObservabilityPanelReason.NOT_CONFIGURED
    assert result.certificates == []


def test_certificates_error_on_driver_exception(
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
    assert result.reason == ObservabilityPanelReason.ERROR
    assert result.certificates == []


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
    assert result.reason == ObservabilityPanelReason.OK
    assert isinstance(result.binding, AppIdentityBindingType)
    assert result.binding.kind == "irsa"
    assert app.slug in result.binding.role_arn_or_principal
    assert "system:serviceaccount" in result.binding.trust_policy_summary
    assert result.binding.last_used_at == "2026-05-15T12:00:00Z"


def test_identity_binding_no_data_yet_when_driver_returns_none(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    patch_driver,
):
    """Driver returns None (no role provisioned) → NO_DATA_YET so the FE
    renders "No workload identity yet" (benign), not a hedged empty."""
    _grant_read(permission_resolver)
    _bind_default_cluster(app, env)

    patch_driver("identity", SimpleNamespace(describe_identity=lambda app_slug: None))

    q = LifecycleQuery()
    with _tenant_for(org, actor):
        result = q.astrolift_app_identity_binding(fake_info, app_slug=app.slug)
    assert result.reason == ObservabilityPanelReason.NO_DATA_YET
    assert result.binding is None


def test_identity_binding_not_supported_on_not_implemented(
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
    assert result.reason == ObservabilityPanelReason.NOT_SUPPORTED_BY_PROVIDER
    assert result.binding is None


def test_identity_binding_not_configured_when_no_cluster(
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
    assert result.reason == ObservabilityPanelReason.NOT_CONFIGURED
    assert result.binding is None


def test_identity_binding_error_on_driver_exception(
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
    assert result.reason == ObservabilityPanelReason.ERROR
    assert result.binding is None


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
