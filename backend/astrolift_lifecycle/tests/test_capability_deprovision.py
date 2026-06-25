"""Tests for the standalone capability-deprovision surface (#368).

Five mutations × three cases each (happy / permission / not-found):

* ``deleteAppDnsRecord`` — DnsDriver.delete_record
* ``revokeAppCertificate`` — TlsDriver.revoke_certificate
* ``deleteAppIdentityRole`` — WorkloadIdentityDriver.delete_identity_role
* ``archiveAppRegistryRepo`` — ImageRegistryDriver.delete_repo (archive=True)
* ``deleteAppIngress`` — IngressDriver.delete_ingress (one or all)

The driver is faked via ``monkeypatch`` on
``astrolift_workflows.activities.capability_deprovision._resolve_capability_driver``
— records each call into a list the test asserts on. Real driver
plumbing lives in the provider package and has its own integration
tests.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_lifecycle.models import CustomDomain
from astrolift_lifecycle.schema.mutations import (
    ArchiveAppRegistryRepoInput,
    DeleteAppDnsRecordInput,
    DeleteAppIdentityRoleInput,
    DeleteAppIngressInput,
    LifecycleMutation,
    RevokeAppCertificateInput,
)
from astrolift_registry.models import RegisteredApp
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

CAP_MOD = "astrolift_workflows.activities.capability_deprovision"


def _tenant_for(org, actor):
    return tenant_context(
        TenantContext(organization_id=org.id, actor_user_id=actor.id),
    )


def _grant_delete(resolver):
    resolver.grant(Permission.APP_DELETE)


# ---- Fakes ---------------------------------------------------------


class _CallRecorder:
    """Records ``(method, args, kwargs)`` for every call so tests can
    assert what the driver was asked to do."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def record(self, method):
        def _capture(*args, **kwargs):
            self.calls.append((method, args, kwargs))
            return None

        return _capture


def _install_fake_driver(monkeypatch, *, capability: str, methods: dict[str, callable]):
    """Replace ``_resolve_capability_driver`` with one that returns a
    SimpleNamespace bound to the methods the test expects to be called."""
    fake = SimpleNamespace(**methods)

    def _resolver(cluster, cap):
        # Match the spec'd capability so the activity's getattr probe
        # for the method on the namespace fires the same shape it
        # would against the real driver.
        assert cap == capability, f"expected capability={capability!r}, got {cap!r}"
        return fake

    monkeypatch.setattr(f"{CAP_MOD}._resolve_capability_driver", _resolver)
    return fake


# ---- Bind app to cluster (default_tenant_cluster) -----------------


@pytest.fixture
def app_with_cluster(app, cluster):
    """The activities resolve cluster via ``app.default_tenant_cluster``;
    the conftest's ``app`` fixture doesn't pre-bind one. Bind here so
    every test in this module hits the same path."""
    app.default_tenant_cluster = cluster
    app.save(update_fields=["default_tenant_cluster", "updated_at", "version"])
    return app


# ---- deleteAppDnsRecord -------------------------------------------


@pytest.mark.django_db
def test_delete_app_dns_record_happy_path(
    app_with_cluster,
    cluster,
    fake_info,
    org,
    actor,
    permission_resolver,
    monkeypatch,
):
    _grant_delete(permission_resolver)
    rec = _CallRecorder()
    _install_fake_driver(
        monkeypatch,
        capability="dns",
        methods={"delete_record": rec.record("delete_record")},
    )
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.delete_app_dns_record(
            info=fake_info,
            input=DeleteAppDnsRecordInput(
                app_id=str(app_with_cluster.guid),
                hostname="api.acme.com",
                record_type="CNAME",
            ),
        )
    assert result.ok is True, result.errors
    assert result.data.cluster_slug == cluster.slug
    # Driver received (zone, name, type) — hostname split by parent zone.
    assert len(rec.calls) == 1
    method, args, _ = rec.calls[0]
    assert method == "delete_record"
    assert args == ("acme.com", "api", "CNAME")


@pytest.mark.django_db
def test_delete_app_dns_record_requires_permission(
    app_with_cluster,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    # No grant → first-line resolver denies.
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.delete_app_dns_record(
            info=fake_info,
            input=DeleteAppDnsRecordInput(
                app_id=str(app_with_cluster.guid),
                hostname="api.acme.com",
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.PERMISSION_DENIED.value for e in result.errors)


@pytest.mark.django_db
def test_delete_app_dns_record_not_found(
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_delete(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.delete_app_dns_record(
            info=fake_info,
            input=DeleteAppDnsRecordInput(
                app_id="00000000-0000-7000-8000-000000000000",
                hostname="api.acme.com",
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.NOT_FOUND.value for e in result.errors)


# ---- revokeAppCertificate -----------------------------------------


@pytest.fixture
def custom_domain_with_cert(app_with_cluster):
    return CustomDomain.objects.create(
        registered_app=app_with_cluster,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        certificate_id="acm-arn-abc123",
        is_active=True,
    )


@pytest.mark.django_db
def test_revoke_app_certificate_happy_path(
    custom_domain_with_cert,
    cluster,
    fake_info,
    org,
    actor,
    permission_resolver,
    monkeypatch,
):
    _grant_delete(permission_resolver)
    rec = _CallRecorder()
    _install_fake_driver(
        monkeypatch,
        capability="tls",
        methods={"revoke_certificate": rec.record("revoke_certificate")},
    )
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.revoke_app_certificate(
            info=fake_info,
            input=RevokeAppCertificateInput(
                custom_domain_id=str(custom_domain_with_cert.guid),
            ),
        )
    assert result.ok is True, result.errors
    assert result.data.cluster_slug == cluster.slug
    # Driver received the cert id we stored on the row.
    assert rec.calls == [("revoke_certificate", ("acm-arn-abc123",), {})]
    # Row state cleared so the renderer stops emitting the Ingress.
    custom_domain_with_cert.refresh_from_db()
    assert custom_domain_with_cert.certificate_id == ""
    assert custom_domain_with_cert.certificate_state == CustomDomain.CertificateState.NOT_REQUESTED
    assert custom_domain_with_cert.byo_certificate_pem == ""


@pytest.mark.django_db
def test_revoke_app_certificate_requires_permission(
    custom_domain_with_cert,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.revoke_app_certificate(
            info=fake_info,
            input=RevokeAppCertificateInput(
                custom_domain_id=str(custom_domain_with_cert.guid),
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.PERMISSION_DENIED.value for e in result.errors)
    # Row state must not have been touched on permission denial.
    custom_domain_with_cert.refresh_from_db()
    assert custom_domain_with_cert.certificate_state == CustomDomain.CertificateState.ACTIVE
    assert custom_domain_with_cert.certificate_id == "acm-arn-abc123"


@pytest.mark.django_db
def test_revoke_app_certificate_not_found(
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_delete(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.revoke_app_certificate(
            info=fake_info,
            input=RevokeAppCertificateInput(
                custom_domain_id="00000000-0000-7000-8000-000000000000",
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.NOT_FOUND.value for e in result.errors)


# ---- deleteAppIdentityRole ----------------------------------------


@pytest.mark.django_db
def test_delete_app_identity_role_happy_path(
    app_with_cluster,
    cluster,
    fake_info,
    org,
    actor,
    permission_resolver,
    monkeypatch,
):
    _grant_delete(permission_resolver)
    rec = _CallRecorder()
    _install_fake_driver(
        monkeypatch,
        capability="identity",
        methods={"delete_identity_role": rec.record("delete_identity_role")},
    )
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.delete_app_identity_role(
            info=fake_info,
            input=DeleteAppIdentityRoleInput(
                app_id=str(app_with_cluster.guid),
            ),
        )
    assert result.ok is True, result.errors
    assert result.data.cluster_slug == cluster.slug
    # Deletes BOTH per-app roles: the runtime workload-identity role
    # (``astrolift-<org>-<app>``) and the platform-build role
    # (``astrolift-build-<org>-<app>``, #978) — otherwise the build role
    # orphans on teardown.
    assert rec.calls == [
        (
            "delete_identity_role",
            (f"astrolift-{org.slug}-{app_with_cluster.slug}",),
            {},
        ),
        (
            "delete_identity_role",
            (f"astrolift-build-{org.slug}-{app_with_cluster.slug}",),
            {},
        ),
    ]


@pytest.mark.django_db
def test_delete_app_identity_role_requires_permission(
    app_with_cluster,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.delete_app_identity_role(
            info=fake_info,
            input=DeleteAppIdentityRoleInput(
                app_id=str(app_with_cluster.guid),
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.PERMISSION_DENIED.value for e in result.errors)


@pytest.mark.django_db
def test_delete_app_identity_role_not_found(
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_delete(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.delete_app_identity_role(
            info=fake_info,
            input=DeleteAppIdentityRoleInput(
                app_id="00000000-0000-7000-8000-000000000000",
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.NOT_FOUND.value for e in result.errors)


# ---- archiveAppRegistryRepo ---------------------------------------


@pytest.fixture
def app_with_registry_uri(app_with_cluster):
    app_with_cluster.registry_repo_uri = "123.dkr.ecr.us-west-2.amazonaws.com/astrolift/hello-app"
    app_with_cluster.save(
        update_fields=["registry_repo_uri", "updated_at", "version"],
    )
    return app_with_cluster


@pytest.mark.django_db
def test_archive_app_registry_repo_happy_path(
    app_with_registry_uri,
    cluster,
    fake_info,
    org,
    actor,
    permission_resolver,
    monkeypatch,
):
    _grant_delete(permission_resolver)
    rec = _CallRecorder()
    _install_fake_driver(
        monkeypatch,
        capability="registry",
        methods={"delete_repo": rec.record("delete_repo")},
    )
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.archive_app_registry_repo(
            info=fake_info,
            input=ArchiveAppRegistryRepoInput(
                app_id=str(app_with_registry_uri.guid),
                archive=True,
            ),
        )
    assert result.ok is True, result.errors
    assert result.data.cluster_slug == cluster.slug
    # Driver gets the path-stripped repo name + the archive flag.
    assert rec.calls == [
        ("delete_repo", ("astrolift/hello-app",), {"archive": True}),
    ]
    # registry_repo_uri cleared so a future provision starts fresh.
    app_with_registry_uri.refresh_from_db()
    assert app_with_registry_uri.registry_repo_uri == ""


@pytest.mark.django_db
def test_archive_app_registry_repo_requires_permission(
    app_with_registry_uri,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.archive_app_registry_repo(
            info=fake_info,
            input=ArchiveAppRegistryRepoInput(
                app_id=str(app_with_registry_uri.guid),
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.PERMISSION_DENIED.value for e in result.errors)
    # URI must not have been cleared on permission denial.
    app_with_registry_uri.refresh_from_db()
    assert app_with_registry_uri.registry_repo_uri != ""


@pytest.mark.django_db
def test_archive_app_registry_repo_not_found(
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_delete(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.archive_app_registry_repo(
            info=fake_info,
            input=ArchiveAppRegistryRepoInput(
                app_id="00000000-0000-7000-8000-000000000000",
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.NOT_FOUND.value for e in result.errors)


# ---- deleteAppIngress ---------------------------------------------


@pytest.fixture
def ingress_app(app_with_cluster):
    """App with two custom domains so the all-ingresses path has
    something to delete."""
    CustomDomain.objects.create(
        registered_app=app_with_cluster,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        is_active=True,
    )
    CustomDomain.objects.create(
        registered_app=app_with_cluster,
        hostname="checkout.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        is_active=True,
    )
    return app_with_cluster


@pytest.mark.django_db
def test_delete_app_ingress_per_hostname_soft_deletes_domain(
    ingress_app,
    cluster,
    fake_info,
    org,
    actor,
    permission_resolver,
    monkeypatch,
):
    """Per-hostname path: soft-delete the matching CustomDomain row;
    do NOT call the IngressDriver — the renderer + cluster GC handle
    the per-domain Ingress drop on next deploy."""
    _grant_delete(permission_resolver)
    rec = _CallRecorder()
    # Driver shouldn't be touched in the per-hostname branch — install
    # one that explodes if asked, just in case.
    _install_fake_driver(
        monkeypatch,
        capability="ingress",
        methods={"delete_ingress": rec.record("delete_ingress")},
    )
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.delete_app_ingress(
            info=fake_info,
            input=DeleteAppIngressInput(
                app_id=str(ingress_app.guid),
                hostname="api.acme.com",
            ),
        )
    assert result.ok is True, result.errors
    assert result.data.cluster_slug == cluster.slug
    # Driver was NOT called — per-host path stays in DB land.
    assert rec.calls == []
    # The matching CustomDomain is soft-deleted.
    domain = CustomDomain.all_objects.get(
        registered_app=ingress_app,
        hostname="api.acme.com",
    )
    assert domain.deleted_at is not None
    # The other domain stays active.
    other = CustomDomain.objects.get(
        registered_app=ingress_app,
        hostname="checkout.acme.com",
    )
    assert other.deleted_at is None


@pytest.mark.django_db
def test_delete_app_ingress_all_calls_driver(
    ingress_app,
    cluster,
    fake_info,
    org,
    actor,
    permission_resolver,
    monkeypatch,
):
    """No hostname → IngressDriver.delete_ingress fires once per
    active CustomDomain, each row is soft-deleted in turn."""
    _grant_delete(permission_resolver)
    rec = _CallRecorder()
    _install_fake_driver(
        monkeypatch,
        capability="ingress",
        methods={"delete_ingress": rec.record("delete_ingress")},
    )
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.delete_app_ingress(
            info=fake_info,
            input=DeleteAppIngressInput(
                app_id=str(ingress_app.guid),
                hostname=None,
            ),
        )
    assert result.ok is True, result.errors
    assert result.data.cluster_slug == cluster.slug
    # Driver called once per active CustomDomain.
    assert len(rec.calls) == 2
    expected_namespace = f"{org.slug}-{ingress_app.slug}"
    for method, args, _kwargs in rec.calls:
        assert method == "delete_ingress"
        assert args == (cluster.slug, expected_namespace, ingress_app.slug, ingress_app.slug)
    # All matching CustomDomains soft-deleted.
    remaining = CustomDomain.objects.filter(registered_app=ingress_app).count()
    assert remaining == 0


@pytest.mark.django_db
def test_delete_app_ingress_requires_permission(
    ingress_app,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.delete_app_ingress(
            info=fake_info,
            input=DeleteAppIngressInput(
                app_id=str(ingress_app.guid),
                hostname="api.acme.com",
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.PERMISSION_DENIED.value for e in result.errors)
    # No CustomDomain should have been soft-deleted on denial.
    assert CustomDomain.objects.filter(registered_app=ingress_app).count() == 2


@pytest.mark.django_db
def test_delete_app_ingress_not_found(
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_delete(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.delete_app_ingress(
            info=fake_info,
            input=DeleteAppIngressInput(
                app_id="00000000-0000-7000-8000-000000000000",
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.NOT_FOUND.value for e in result.errors)


# ---- Driver missing capability surfaces PRECONDITION --------------


@pytest.mark.django_db
def test_unsupported_driver_returns_precondition(
    app_with_cluster,
    fake_info,
    org,
    actor,
    permission_resolver,
    monkeypatch,
):
    """When the driver doesn't expose the requested method (e.g. a
    minimal impl that doesn't support delete_record), the activity
    raises ``CapabilityDeprovisionError`` and the mutation returns a
    ``PRECONDITION`` failure rather than letting it bubble."""
    _grant_delete(permission_resolver)
    # Install a driver namespace WITHOUT delete_record — getattr probe
    # in the activity returns None.
    _install_fake_driver(monkeypatch, capability="dns", methods={})
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.delete_app_dns_record(
            info=fake_info,
            input=DeleteAppDnsRecordInput(
                app_id=str(app_with_cluster.guid),
                hostname="api.acme.com",
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.PRECONDITION.value for e in result.errors)


# ---- Bare not-found path on app-with-no-cluster ------------------


@pytest.mark.django_db
def test_app_without_default_cluster_returns_precondition(
    fake_info,
    org,
    actor,
    project,
    team,
    permission_resolver,
):
    """Mutations that resolve the cluster via ``app.default_tenant_cluster``
    return PRECONDITION when no cluster is bound. The activity raises
    ``CapabilityDeprovisionError`` with the operator-facing message."""
    _grant_delete(permission_resolver)
    standalone = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Solo",
        slug="solo-app",
    )
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.delete_app_identity_role(
            info=fake_info,
            input=DeleteAppIdentityRoleInput(app_id=str(standalone.guid)),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.PRECONDITION.value for e in result.errors)
