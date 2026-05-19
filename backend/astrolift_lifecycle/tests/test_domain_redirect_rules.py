"""Tests for the ``setDomainRedirects`` mutation + ``DomainRedirectRule``
model that back the Redirects sub-section under the Domains page (#742).

Exercises the replace-all semantics, input validation, soft-delete trail,
and tenant-scoped lookup."""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import CustomDomain, DomainRedirectRule
from astrolift_lifecycle.schema.mutations import (
    DomainRedirectRuleInput,
    LifecycleMutation,
    SetDomainRedirectsInput,
)
from astrolift_lifecycle.schema.types import app_domain_to_type
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context


def _grant_update(resolver):
    resolver.grant(Permission.APP_UPDATE)


def _tenant_for(org, actor):
    return tenant_context(
        TenantContext(organization_id=org.id, actor_user_id=actor.id),
    )


@pytest.fixture
def custom_domain(app):
    return CustomDomain.objects.create(
        registered_app=app,
        hostname="acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        validation_method=CustomDomain.ValidationMethod.DNS_TXT,
        is_active=True,
    )


@pytest.mark.django_db
def test_set_redirects_creates_initial_set(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_redirects(
            info=fake_info,
            input=SetDomainRedirectsInput(
                domain_id=str(custom_domain.guid),
                rules=[
                    DomainRedirectRuleInput(
                        kind=DomainRedirectRule.Kind.HTTP_TO_HTTPS.value,
                        priority=0,
                    ),
                    DomainRedirectRuleInput(
                        kind=DomainRedirectRule.Kind.APEX_TO_WWW.value,
                        priority=10,
                    ),
                ],
            ),
        )
    assert result.ok is True, result.errors
    rows = list(
        DomainRedirectRule.objects.filter(
            custom_domain=custom_domain,
            deleted_at__isnull=True,
        ).order_by("priority")
    )
    assert [r.kind for r in rows] == [
        DomainRedirectRule.Kind.HTTP_TO_HTTPS.value,
        DomainRedirectRule.Kind.APEX_TO_WWW.value,
    ]
    assert rows[0].http_status == 301
    assert rows[0].preserve_query_string is True
    assert result.data is not None
    assert {r.kind for r in result.data.redirect_rules} == {
        DomainRedirectRule.Kind.HTTP_TO_HTTPS.value,
        DomainRedirectRule.Kind.APEX_TO_WWW.value,
    }


@pytest.mark.django_db
def test_set_redirects_replaces_existing_set(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    """Calling setDomainRedirects soft-deletes the prior set and bulk-
    creates the new one — both halves run inside one transaction."""
    _grant_update(permission_resolver)
    pre_existing = DomainRedirectRule.objects.create(
        custom_domain=custom_domain,
        kind=DomainRedirectRule.Kind.WWW_TO_APEX.value,
        priority=5,
    )
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_redirects(
            info=fake_info,
            input=SetDomainRedirectsInput(
                domain_id=str(custom_domain.guid),
                rules=[
                    DomainRedirectRuleInput(
                        kind=DomainRedirectRule.Kind.CUSTOM.value,
                        source_pattern="/old/(.*)",
                        destination_url="https://acme.com/new/$1",
                        http_status=308,
                        preserve_query_string=False,
                        priority=1,
                    ),
                ],
            ),
        )
    assert result.ok is True, result.errors
    pre_existing.refresh_from_db()
    assert pre_existing.deleted_at is not None
    active = list(
        DomainRedirectRule.objects.filter(
            custom_domain=custom_domain,
            deleted_at__isnull=True,
        )
    )
    assert len(active) == 1
    assert active[0].kind == DomainRedirectRule.Kind.CUSTOM.value
    assert active[0].source_pattern == "/old/(.*)"
    assert active[0].destination_url == "https://acme.com/new/$1"
    assert active[0].http_status == 308
    assert active[0].preserve_query_string is False


@pytest.mark.django_db
def test_set_redirects_empty_list_clears_all(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    DomainRedirectRule.objects.create(
        custom_domain=custom_domain,
        kind=DomainRedirectRule.Kind.HTTP_TO_HTTPS.value,
    )
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_redirects(
            info=fake_info,
            input=SetDomainRedirectsInput(
                domain_id=str(custom_domain.guid),
                rules=[],
            ),
        )
    assert result.ok is True
    assert (
        DomainRedirectRule.objects.filter(
            custom_domain=custom_domain,
            deleted_at__isnull=True,
        ).count()
        == 0
    )


@pytest.mark.django_db
def test_set_redirects_rejects_unknown_kind(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_redirects(
            info=fake_info,
            input=SetDomainRedirectsInput(
                domain_id=str(custom_domain.guid),
                rules=[
                    DomainRedirectRuleInput(kind="bogus_kind"),
                ],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert "rules" in (result.errors[0].field or "")
    assert DomainRedirectRule.objects.filter(custom_domain=custom_domain).count() == 0


@pytest.mark.django_db
def test_set_redirects_rejects_invalid_http_status(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_redirects(
            info=fake_info,
            input=SetDomainRedirectsInput(
                domain_id=str(custom_domain.guid),
                rules=[
                    DomainRedirectRuleInput(
                        kind=DomainRedirectRule.Kind.HTTP_TO_HTTPS.value,
                        http_status=418,
                    ),
                ],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value


@pytest.mark.django_db
def test_set_redirects_custom_kind_requires_destination(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_redirects(
            info=fake_info,
            input=SetDomainRedirectsInput(
                domain_id=str(custom_domain.guid),
                rules=[
                    DomainRedirectRuleInput(
                        kind=DomainRedirectRule.Kind.CUSTOM.value,
                        source_pattern="/old",
                        destination_url="",
                    ),
                ],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value


@pytest.mark.django_db
def test_set_redirects_alias_kind_requires_destination(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_redirects(
            info=fake_info,
            input=SetDomainRedirectsInput(
                domain_id=str(custom_domain.guid),
                rules=[
                    DomainRedirectRuleInput(
                        kind=DomainRedirectRule.Kind.ALIAS.value,
                    ),
                ],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value


@pytest.mark.django_db
def test_set_redirects_domain_not_found(
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_redirects(
            info=fake_info,
            input=SetDomainRedirectsInput(
                domain_id="00000000-0000-0000-0000-000000000000",
                rules=[
                    DomainRedirectRuleInput(
                        kind=DomainRedirectRule.Kind.HTTP_TO_HTTPS.value,
                    ),
                ],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value


@pytest.mark.django_db
def test_set_redirects_denied_without_permission(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    """Resolver requires APP_UPDATE — without it the mutation returns
    a PERMISSION_DENIED envelope (no raise, per the platform's
    deny-by-default contract)."""
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_redirects(
            info=fake_info,
            input=SetDomainRedirectsInput(
                domain_id=str(custom_domain.guid),
                rules=[
                    DomainRedirectRuleInput(
                        kind=DomainRedirectRule.Kind.HTTP_TO_HTTPS.value,
                    ),
                ],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    assert DomainRedirectRule.objects.filter(custom_domain=custom_domain).count() == 0


@pytest.mark.django_db
def test_set_redirects_isolates_other_orgs(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    """Cross-tenant write attempt: the tenant context's org_id pins the
    CustomDomain lookup via ``registered_app__organization_id``, so a
    different org's tenant can't write rules on this row."""
    _grant_update(permission_resolver)
    from astrolift_identity.models import Organization

    other_org = Organization.objects.create(name="Other", slug="other-test")
    mutation = LifecycleMutation()
    with tenant_context(
        TenantContext(organization_id=other_org.id, actor_user_id=actor.id),
    ):
        result = mutation.set_domain_redirects(
            info=fake_info,
            input=SetDomainRedirectsInput(
                domain_id=str(custom_domain.guid),
                rules=[
                    DomainRedirectRuleInput(
                        kind=DomainRedirectRule.Kind.HTTP_TO_HTTPS.value,
                    ),
                ],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value


@pytest.mark.django_db
def test_app_domain_to_type_surfaces_active_rules_ordered(custom_domain):
    """The AppDomain GraphQL type's ``redirect_rules`` must:
    - omit soft-deleted rows
    - order by ``priority`` ascending"""
    DomainRedirectRule.objects.create(
        custom_domain=custom_domain,
        kind=DomainRedirectRule.Kind.APEX_TO_WWW.value,
        priority=5,
    )
    DomainRedirectRule.objects.create(
        custom_domain=custom_domain,
        kind=DomainRedirectRule.Kind.HTTP_TO_HTTPS.value,
        priority=0,
    )
    stale = DomainRedirectRule.objects.create(
        custom_domain=custom_domain,
        kind=DomainRedirectRule.Kind.WWW_TO_APEX.value,
        priority=3,
    )
    stale.soft_delete()

    out = app_domain_to_type(custom_domain)
    assert [r.kind for r in out.redirect_rules] == [
        DomainRedirectRule.Kind.HTTP_TO_HTTPS.value,
        DomainRedirectRule.Kind.APEX_TO_WWW.value,
    ]
