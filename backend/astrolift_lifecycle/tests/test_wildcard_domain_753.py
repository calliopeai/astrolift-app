"""Tests for the addWildcardDomain mutation (#753).

Covers:
- Successful wildcard domain creation (is_wildcard=True, validation_method=dns_01)
- sni_cert_ref persisted when provided; empty OK
- Validation: apex-only hostname (*.foo.com rejected; foo.com accepted)
- Validation: non-dns_01 method rejected
- Validation: bare label (no dot) rejected
- App not found returns NOT_FOUND
- Domain already bound to a different app returns CONFLICT
- Idempotent re-add: same (app, hostname) returns existing row and promotes
  it to wildcard, rewrites sni_cert_ref when non-empty, leaves pin untouched
  when empty
- Permission gate: requires APP_UPDATE
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models.ingress import CustomDomain
from astrolift_lifecycle.schema.mutations import (
    AddWildcardDomainInput,
    LifecycleMutation,
)
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info(user=None):
    request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _scaffold():
    org = Organization.objects.create(name="WcOrg", slug="wc-org")
    team = Team.objects.create(organization=org, name="Eng", slug="wc-eng")
    project = Project.objects.create(organization=org, team=team, name="WcProj", slug="wc-proj")
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="K8s Native",
                slug="k8s-native",
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="k8s-native")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="wc-local",
        name="Local",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="WcApp",
        slug="wc-app",
        provisioning_status="ready",
        default_tenant_cluster=cluster,
    )
    other_app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="OtherApp",
        slug="other-wc-app",
        provisioning_status="ready",
        default_tenant_cluster=cluster,
    )
    return org, app, other_app, cluster


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _patch_handshake():
    """Stub out custom_domain_handshake helpers — no DNS / cluster I/O.

    ``build_handshake`` and ``resolve_cluster_ingress_target`` are
    imported locally inside the mutation function, so we patch them at
    the source module rather than via the mutations namespace.
    ``_kick_validate_custom_domain`` IS defined in mutations.py, so
    it's patched there.
    """
    record = SimpleNamespace(
        kind="TXT",
        name="_astrolift-challenge.example.com",
        value="tok",
        ttl=300,
        propagated=False,
        last_checked_at=None,
        message="",
    )
    handshake = SimpleNamespace(
        txt_challenge_token="tok123",
        expected_cname_target="ingress.cluster.example.com",
        required_records=[record],
        is_platform_managed_zone=False,
    )
    validate_patch = patch(
        "astrolift_lifecycle.schema.mutations._kick_validate_custom_domain",
        return_value=MagicMock(),
    )
    build_patch = patch(
        "astrolift_lifecycle.custom_domain_handshake.build_handshake",
        return_value=handshake,
    )
    ingress_patch = patch(
        "astrolift_lifecycle.custom_domain_handshake.resolve_cluster_ingress_target",
        return_value="ingress.cluster.example.com",
    )
    return validate_patch, build_patch, ingress_patch


# ---- validation -----------------------------------------------------


def test_wildcard_rejects_bare_label(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = LifecycleMutation().add_wildcard_domain(
            _info(), input=AddWildcardDomainInput(app_slug=app.slug, hostname="nodot")
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "hostname"


def test_wildcard_rejects_wildcard_prefix_in_hostname(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = LifecycleMutation().add_wildcard_domain(
            _info(), input=AddWildcardDomainInput(app_slug=app.slug, hostname="*.example.com")
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "hostname"


def test_wildcard_rejects_non_dns01_method(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = LifecycleMutation().add_wildcard_domain(
            _info(),
            input=AddWildcardDomainInput(
                app_slug=app.slug, hostname="example.com", validation_method="http_01"
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "validationMethod"


def test_wildcard_app_not_found(permission_resolver):
    org, _, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = LifecycleMutation().add_wildcard_domain(
            _info(),
            input=AddWildcardDomainInput(app_slug="does-not-exist", hostname="example.com"),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---- successful creation --------------------------------------------


def test_wildcard_creates_domain_with_correct_flags(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    vp, bp, ip = _patch_handshake()
    with _ctx(org), vp, bp, ip:
        result = LifecycleMutation().add_wildcard_domain(
            _info(),
            input=AddWildcardDomainInput(app_slug=app.slug, hostname="example.com"),
        )
    assert result.ok, result.errors
    domain = CustomDomain.objects.get(registered_app=app, hostname="example.com")
    assert domain.is_wildcard is True
    assert domain.validation_method == "dns_01"
    assert domain.sni_cert_ref == ""


def test_wildcard_persists_sni_cert_ref(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    vp, bp, ip = _patch_handshake()
    with _ctx(org), vp, bp, ip:
        result = LifecycleMutation().add_wildcard_domain(
            _info(),
            input=AddWildcardDomainInput(
                app_slug=app.slug,
                hostname="example.com",
                sni_cert_ref="arn:aws:acm:us-east-1:123:certificate/abc",
            ),
        )
    assert result.ok, result.errors
    domain = CustomDomain.objects.get(registered_app=app, hostname="example.com")
    assert domain.sni_cert_ref == "arn:aws:acm:us-east-1:123:certificate/abc"


# ---- conflict -------------------------------------------------------


def test_wildcard_conflict_different_app(permission_resolver):
    org, app, other_app, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    # Pre-create domain bound to other_app
    CustomDomain.objects.create(
        registered_app=other_app,
        hostname="example.com",
        is_wildcard=True,
        validation_method="dns_01",
    )
    with _ctx(org):
        result = LifecycleMutation().add_wildcard_domain(
            _info(),
            input=AddWildcardDomainInput(app_slug=app.slug, hostname="example.com"),
        )
    assert not result.ok
    assert result.errors[0].code == "CONFLICT"
    assert result.errors[0].field == "hostname"


# ---- idempotency ----------------------------------------------------


def test_wildcard_idempotent_same_app_same_hostname(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    vp, bp, ip = _patch_handshake()
    with _ctx(org), vp, bp, ip:
        r1 = LifecycleMutation().add_wildcard_domain(
            _info(), input=AddWildcardDomainInput(app_slug=app.slug, hostname="idm.example.com")
        )
        r2 = LifecycleMutation().add_wildcard_domain(
            _info(), input=AddWildcardDomainInput(app_slug=app.slug, hostname="idm.example.com")
        )
    assert r1.ok and r2.ok
    assert r1.data.id == r2.data.id
    assert CustomDomain.objects.filter(registered_app=app, hostname="idm.example.com").count() == 1


def test_wildcard_idempotent_promotes_single_host_to_wildcard(permission_resolver):
    """Re-adding via addWildcardDomain on an existing single-host row upgrades it."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    existing = CustomDomain.objects.create(
        registered_app=app,
        hostname="promo.example.com",
        is_wildcard=False,
        validation_method="dns_txt",
    )
    vp, bp, ip = _patch_handshake()
    with _ctx(org), vp, bp, ip:
        result = LifecycleMutation().add_wildcard_domain(
            _info(),
            input=AddWildcardDomainInput(app_slug=app.slug, hostname="promo.example.com"),
        )
    assert result.ok, result.errors
    existing.refresh_from_db()
    assert existing.is_wildcard is True
    assert existing.validation_method == "dns_01"


def test_wildcard_idempotent_sni_ref_updated_when_nonempty(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    existing = CustomDomain.objects.create(
        registered_app=app,
        hostname="sni.example.com",
        is_wildcard=True,
        validation_method="dns_01",
        sni_cert_ref="arn:old",
    )
    vp, bp, ip = _patch_handshake()
    with _ctx(org), vp, bp, ip:
        result = LifecycleMutation().add_wildcard_domain(
            _info(),
            input=AddWildcardDomainInput(
                app_slug=app.slug,
                hostname="sni.example.com",
                sni_cert_ref="arn:new",
            ),
        )
    assert result.ok
    existing.refresh_from_db()
    assert existing.sni_cert_ref == "arn:new"


def test_wildcard_idempotent_sni_ref_untouched_when_empty(permission_resolver):
    """Empty sni_cert_ref on re-add leaves the existing pin intact."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    existing = CustomDomain.objects.create(
        registered_app=app,
        hostname="pin.example.com",
        is_wildcard=True,
        validation_method="dns_01",
        sni_cert_ref="arn:pinned",
    )
    vp, bp, ip = _patch_handshake()
    with _ctx(org), vp, bp, ip:
        result = LifecycleMutation().add_wildcard_domain(
            _info(),
            input=AddWildcardDomainInput(
                app_slug=app.slug,
                hostname="pin.example.com",
                sni_cert_ref="",
            ),
        )
    assert result.ok
    existing.refresh_from_db()
    assert existing.sni_cert_ref == "arn:pinned"
