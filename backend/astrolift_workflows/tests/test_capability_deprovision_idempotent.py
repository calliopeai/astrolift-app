"""#1100 — per-capability deprovision activities must be idempotent for an
already-absent cloud resource.

Repro: DeregisterAppWorkflow hangs in ``tearing_down`` when a capability the
prior (partial) teardown already removed, or an async delete finished
out-of-band, makes the driver raise ``NotFoundError``. The activity re-raised →
Temporal retried to exhaustion → the workflow never reached
``mark_deregistered``.

Each deprovision helper must treat a driver ``NotFoundError`` (already gone) as
success/skipped so a resumed teardown converges, while a genuine driver error
(perms, throttling) still propagates so Temporal honours the RetryPolicy.

Real Postgres; ``_resolve_capability_driver`` is patched so we exercise the
activity's not-found handling without a live cloud.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import CustomDomain
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.activities.capability_deprovision import (
    _deprovision_certificate_sync,
    _deprovision_dns_record_sync,
    _deprovision_identity_role_sync,
    _deprovision_ingress_sync,
    _deprovision_registry_repo_sync,
)

pytestmark = pytest.mark.django_db

_RESOLVE = "astrolift_workflows.activities.capability_deprovision._resolve_capability_driver"


def _not_found(*_a, **_k):
    from aws._errors import NotFoundError

    raise NotFoundError("the specified resource does not exist")


def _permission_denied(*_a, **_k):
    from aws._errors import PermissionError as ProviderPermissionError

    raise ProviderPermissionError("AccessDenied: not authorized")


class _Driver:
    """Minimal driver whose one delete/revoke method is set per-test."""

    def __init__(self, **methods) -> None:
        for name, fn in methods.items():
            setattr(self, name, fn)


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme-cap")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-cap")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-cap")
    # bulk_create bypasses BaseCoreModel.save (ProviderPlugin.version is a
    # CharField the base's int version-bump would choke on).
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws-cap",
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="aws-cap")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="aws-prod-cap",
        name="AWS Prod",
        provider_plugin=plugin,
        endpoint="https://example.invalid",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Web",
        slug="web-cap",
        default_tenant_cluster=cluster,
        registry_repo_uri="123.dkr.ecr.us-west-2.amazonaws.com/astrolift/web-cap",
        provisioning_status="tearing_down",
    )
    return org, cluster, app


# ---- DNS record ----------------------------------------------------


def test_dns_record_not_found_is_skipped():
    _, _, app = _scaffold()
    driver = _Driver(delete_record=_not_found)
    with patch(_RESOLVE, return_value=driver):
        result = _deprovision_dns_record_sync(
            registered_app_id=app.pk,
            hostname="web.example.com",
        )
    assert result["skipped"] is True, result


def test_dns_record_real_error_propagates():
    from aws._errors import PermissionError as ProviderPermissionError

    _, _, app = _scaffold()
    driver = _Driver(delete_record=_permission_denied)
    with patch(_RESOLVE, return_value=driver), pytest.raises(ProviderPermissionError):
        _deprovision_dns_record_sync(
            registered_app_id=app.pk,
            hostname="web.example.com",
        )


# ---- TLS certificate ----------------------------------------------


def _domain_with_cert(app):
    return CustomDomain.objects.create(
        registered_app=app,
        hostname="web.example.com",
        certificate_id="cert-abc",
        certificate_state=CustomDomain.CertificateState.ACTIVE,
    )


def test_certificate_not_found_is_skipped_and_state_reset():
    _, _, app = _scaffold()
    domain = _domain_with_cert(app)
    driver = _Driver(revoke_certificate=_not_found)
    with patch(_RESOLVE, return_value=driver):
        result = _deprovision_certificate_sync(custom_domain_id=domain.pk)
    assert result["skipped"] is True, result
    assert result["revoked"] is False
    domain.refresh_from_db()
    # State is reset regardless so the renderer stops emitting the Ingress.
    assert domain.certificate_id == ""
    assert domain.certificate_state == CustomDomain.CertificateState.NOT_REQUESTED


def test_certificate_real_error_propagates():
    from aws._errors import PermissionError as ProviderPermissionError

    _, _, app = _scaffold()
    domain = _domain_with_cert(app)
    driver = _Driver(revoke_certificate=_permission_denied)
    with patch(_RESOLVE, return_value=driver), pytest.raises(ProviderPermissionError):
        _deprovision_certificate_sync(custom_domain_id=domain.pk)


# ---- Identity role -------------------------------------------------


def test_identity_role_not_found_is_skipped():
    _, _, app = _scaffold()
    driver = _Driver(delete_identity_role=_not_found)
    with patch(_RESOLVE, return_value=driver):
        result = _deprovision_identity_role_sync(registered_app_id=app.pk)
    # Every per-app role was already gone → all skipped, none deleted, no raise.
    assert result["roles_deleted"] == [], result
    assert len(result["roles_skipped"]) == 4, result


def test_identity_role_real_error_propagates():
    from aws._errors import PermissionError as ProviderPermissionError

    _, _, app = _scaffold()
    driver = _Driver(delete_identity_role=_permission_denied)
    with patch(_RESOLVE, return_value=driver), pytest.raises(ProviderPermissionError):
        _deprovision_identity_role_sync(registered_app_id=app.pk)


# ---- Registry repo ------------------------------------------------


def test_registry_repo_not_found_is_skipped_and_uri_cleared():
    _, _, app = _scaffold()
    driver = _Driver(delete_repo=_not_found)
    with patch(_RESOLVE, return_value=driver):
        result = _deprovision_registry_repo_sync(registered_app_id=app.pk)
    assert result["skipped"] is True, result
    app.refresh_from_db()
    assert app.registry_repo_uri == ""


def test_registry_repo_real_error_propagates():
    from aws._errors import PermissionError as ProviderPermissionError

    _, _, app = _scaffold()
    driver = _Driver(delete_repo=_permission_denied)
    with patch(_RESOLVE, return_value=driver), pytest.raises(ProviderPermissionError):
        _deprovision_registry_repo_sync(registered_app_id=app.pk)


# ---- Ingress -------------------------------------------------------


def test_ingress_not_found_counts_as_deleted():
    _, _, app = _scaffold()
    domain = CustomDomain.objects.create(registered_app=app, hostname="web.example.com")
    driver = _Driver(delete_ingress=_not_found)
    with patch(_RESOLVE, return_value=driver):
        result = _deprovision_ingress_sync(registered_app_id=app.pk)
    # Already-gone Ingress → hostname counted deleted, no error surfaced, row
    # soft-deleted so the binding is revoked.
    assert result["deleted_hostnames"] == ["web.example.com"], result
    assert result["errors"] == []
    domain.refresh_from_db()
    assert domain.deleted_at is not None


def test_ingress_real_error_surfaces_and_is_not_counted_deleted():
    _, _, app = _scaffold()
    CustomDomain.objects.create(registered_app=app, hostname="web.example.com")

    def _boom(*_a, **_k):
        raise RuntimeError("ELB throttled")

    driver = _Driver(delete_ingress=_boom)
    with patch(_RESOLVE, return_value=driver):
        result = _deprovision_ingress_sync(registered_app_id=app.pk)
    # A genuine failure is NOT swallowed as success: the host stays live and
    # the error is reported so the operator/workflow can retry.
    assert result["deleted_hostnames"] == [], result
    assert result["errors"] and "web.example.com" in result["errors"][0]
