"""Tests for the ``add_wildcard_domain`` mutation + the
``is_wildcard`` / ``sni_cert_ref`` surface (#753).

Covers:
  - happy path: row lands with ``is_wildcard=True``, ``validation_method=dns_01``,
    optional ``sni_cert_ref`` persisted, handshake token populated
  - rejection: missing/invalid hostname, leading ``*.`` apex
  - rejection: validation_method override away from dns_01
  - rejection: hostname already bound to a different app (CONFLICT)
  - idempotent re-add on the same hostname promotes single-host → wildcard
  - permission gate denies without ``app.update``
  - GraphQL type passes both fields through ``app_domain_to_type``
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import CustomDomain
from astrolift_lifecycle.schema.mutations import (
    AddWildcardDomainInput,
    LifecycleMutation,
)
from astrolift_lifecycle.schema.types import app_domain_to_type
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def test_add_wildcard_happy_path(org, app, actor, fake_info, permission_resolver, no_temporal):
    permission_resolver.grant(Permission.APP_UPDATE)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.add_wildcard_domain(
            fake_info,
            input=AddWildcardDomainInput(
                app_slug=app.slug,
                hostname="example.com",
                sni_cert_ref="arn:aws:acm:us-west-2:123:certificate/abc",
            ),
        )
    assert result.ok, result.errors
    domain = CustomDomain.objects.get(hostname="example.com", deleted_at__isnull=True)
    assert domain.is_wildcard is True
    assert domain.validation_method == "dns_01"
    assert domain.sni_cert_ref == "arn:aws:acm:us-west-2:123:certificate/abc"
    # Handshake handshake token was generated.
    assert domain.txt_challenge_token


def test_add_wildcard_defaults_sni_to_empty(org, app, actor, fake_info, permission_resolver, no_temporal):
    """``sni_cert_ref`` is optional — empty means the renderer
    auto-picks."""
    permission_resolver.grant(Permission.APP_UPDATE)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.add_wildcard_domain(
            fake_info,
            input=AddWildcardDomainInput(app_slug=app.slug, hostname="acme.io"),
        )
    assert result.ok, result.errors
    domain = CustomDomain.objects.get(hostname="acme.io", deleted_at__isnull=True)
    assert domain.sni_cert_ref == ""


def test_add_wildcard_rejects_empty_hostname(org, app, actor, fake_info, permission_resolver, no_temporal):
    permission_resolver.grant(Permission.APP_UPDATE)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.add_wildcard_domain(
            fake_info,
            input=AddWildcardDomainInput(app_slug=app.slug, hostname="   "),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"


def test_add_wildcard_rejects_non_fqdn(org, app, actor, fake_info, permission_resolver, no_temporal):
    permission_resolver.grant(Permission.APP_UPDATE)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.add_wildcard_domain(
            fake_info,
            input=AddWildcardDomainInput(app_slug=app.slug, hostname="not-a-domain"),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"


def test_add_wildcard_rejects_apex_with_star_prefix(
    org, app, actor, fake_info, permission_resolver, no_temporal
):
    """The platform stores the apex; the wildcard ``*.`` is implied —
    operators passing ``*.example.com`` hit a validation error rather
    than landing a ``*.*.example.com`` SAN downstream."""
    permission_resolver.grant(Permission.APP_UPDATE)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.add_wildcard_domain(
            fake_info,
            input=AddWildcardDomainInput(app_slug=app.slug, hostname="*.example.com"),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert "apex" in result.errors[0].message.lower()


def test_add_wildcard_rejects_non_dns01_validation(
    org, app, actor, fake_info, permission_resolver, no_temporal
):
    """Wildcards require DNS-01 by CA policy."""
    permission_resolver.grant(Permission.APP_UPDATE)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.add_wildcard_domain(
            fake_info,
            input=AddWildcardDomainInput(
                app_slug=app.slug,
                hostname="example.com",
                validation_method="http_01",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert "dns_01" in result.errors[0].message


def test_add_wildcard_idempotent_promotes_single_host(
    org, app, actor, fake_info, permission_resolver, no_temporal
):
    """An existing single-host row for the same hostname converges on
    the wildcard contract — ``is_wildcard`` flips to True, validation
    method becomes ``dns_01``, ``sni_cert_ref`` is updated if a non-
    empty value is passed."""
    permission_resolver.grant(Permission.APP_UPDATE)
    CustomDomain.objects.create(
        registered_app=app,
        hostname="legacy.example.com",
        validation_method="dns_txt",
        is_wildcard=False,
        sni_cert_ref="",
    )
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.add_wildcard_domain(
            fake_info,
            input=AddWildcardDomainInput(
                app_slug=app.slug,
                hostname="legacy.example.com",
                sni_cert_ref="acm-arn-new",
            ),
        )
    assert result.ok, result.errors
    domain = CustomDomain.objects.get(hostname="legacy.example.com", deleted_at__isnull=True)
    assert domain.is_wildcard is True
    assert domain.validation_method == "dns_01"
    assert domain.sni_cert_ref == "acm-arn-new"


def test_add_wildcard_conflict_when_bound_to_other_app(
    org, app, project, team, actor, fake_info, permission_resolver, no_temporal
):
    """Wildcard ownership is exclusive per apex — adding a wildcard
    for a hostname already bound to a different app must fail with
    CONFLICT."""
    from astrolift_registry.models import RegisteredApp

    other = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Other",
        slug="other-app",
        provisioning_status="ready",
    )
    CustomDomain.objects.create(
        registered_app=other,
        hostname="shared.example.com",
        validation_method="dns_txt",
    )
    permission_resolver.grant(Permission.APP_UPDATE)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.add_wildcard_domain(
            fake_info,
            input=AddWildcardDomainInput(app_slug=app.slug, hostname="shared.example.com"),
        )
    assert not result.ok
    assert result.errors[0].code == "CONFLICT"


def test_add_wildcard_not_found(org, actor, fake_info, permission_resolver, no_temporal):
    permission_resolver.grant(Permission.APP_UPDATE)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.add_wildcard_domain(
            fake_info,
            input=AddWildcardDomainInput(app_slug="nope", hostname="example.com"),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_add_wildcard_requires_permission(org, app, actor, fake_info, permission_resolver, no_temporal):
    """Without ``app.update`` the request must be denied."""
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.add_wildcard_domain(
            fake_info,
            input=AddWildcardDomainInput(app_slug=app.slug, hostname="denied.example.com"),
        )
    assert not result.ok
    # No row should have been created.
    assert not CustomDomain.objects.filter(hostname="denied.example.com", deleted_at__isnull=True).exists()


def test_app_domain_to_type_carries_wildcard_and_sni_ref(app):
    """The GraphQL serializer surfaces both new fields so the FE can
    render the wildcard chip + SNI cert pin."""
    domain = CustomDomain.objects.create(
        registered_app=app,
        hostname="wild.example.com",
        validation_method="dns_01",
        is_wildcard=True,
        sni_cert_ref="gcp-cert-foo",
    )
    out = app_domain_to_type(domain)
    assert out.is_wildcard is True
    assert out.sni_cert_ref == "gcp-cert-foo"


def test_app_domain_to_type_defaults_for_legacy_rows(app):
    """Existing rows pre-migration have the default field values; the
    serializer surfaces them as ``False`` / empty string."""
    domain = CustomDomain.objects.create(
        registered_app=app,
        hostname="legacy-default.example.com",
        validation_method="dns_txt",
    )
    out = app_domain_to_type(domain)
    assert out.is_wildcard is False
    assert out.sni_cert_ref == ""
