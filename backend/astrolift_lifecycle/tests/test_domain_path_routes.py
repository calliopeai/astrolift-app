"""Tests for the ``setDomainPathRoutes`` mutation + ``DomainPathRoute``
model that back the path-based routing sub-section under Domains (#740).

Exercises the replace-all semantics, input validation, soft-delete trail,
and tenant-scoped lookup."""

from __future__ import annotations

import inspect

import pytest

from astrolift_lifecycle.models import CustomDomain, DomainPathRoute
from astrolift_lifecycle.schema.mutations import (
    DomainPathRouteInput,
    LifecycleMutation,
    SetDomainPathRoutesInput,
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
def test_set_path_routes_creates_initial_set(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_path_routes(
            info=fake_info,
            input=SetDomainPathRoutesInput(
                domain_id=str(custom_domain.guid),
                routes=[
                    DomainPathRouteInput(
                        path_prefix="/api",
                        target_workload_slug="api-worker",
                        target_port=8080,
                        strip_prefix=True,
                        priority=0,
                    ),
                    DomainPathRouteInput(
                        path_prefix="/",
                        target_workload_slug="web",
                        target_port=3000,
                        priority=10,
                    ),
                ],
            ),
        )
    assert result.ok is True, result.errors
    rows = list(
        DomainPathRoute.objects.filter(
            custom_domain=custom_domain,
            deleted_at__isnull=True,
        ).order_by("priority")
    )
    assert len(rows) == 2
    assert rows[0].path_prefix == "/api"
    assert rows[0].target_workload_slug == "api-worker"
    assert rows[0].target_port == 8080
    assert rows[0].strip_prefix is True
    assert rows[1].path_prefix == "/"
    assert rows[1].target_port == 3000
    assert result.data is not None
    assert {r.path_prefix for r in result.data.path_routes} == {"/api", "/"}


@pytest.mark.django_db
def test_set_path_routes_replaces_existing_set(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    pre_existing = DomainPathRoute.objects.create(
        custom_domain=custom_domain,
        path_prefix="/old",
        target_workload_slug="old-worker",
        target_port=9000,
    )
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_path_routes(
            info=fake_info,
            input=SetDomainPathRoutesInput(
                domain_id=str(custom_domain.guid),
                routes=[
                    DomainPathRouteInput(
                        path_prefix="/new",
                        target_workload_slug="new-worker",
                        target_port=4000,
                    ),
                ],
            ),
        )
    assert result.ok is True, result.errors
    pre_existing.refresh_from_db()
    assert pre_existing.deleted_at is not None
    active = list(
        DomainPathRoute.objects.filter(
            custom_domain=custom_domain,
            deleted_at__isnull=True,
        )
    )
    assert len(active) == 1
    assert active[0].path_prefix == "/new"


@pytest.mark.django_db
def test_set_path_routes_empty_list_clears_all(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    DomainPathRoute.objects.create(
        custom_domain=custom_domain,
        path_prefix="/api",
        target_workload_slug="api",
        target_port=8080,
    )
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_path_routes(
            info=fake_info,
            input=SetDomainPathRoutesInput(
                domain_id=str(custom_domain.guid),
                routes=[],
            ),
        )
    assert result.ok is True
    assert (
        DomainPathRoute.objects.filter(
            custom_domain=custom_domain,
            deleted_at__isnull=True,
        ).count()
        == 0
    )


@pytest.mark.django_db
def test_set_path_routes_rejects_missing_leading_slash(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_path_routes(
            info=fake_info,
            input=SetDomainPathRoutesInput(
                domain_id=str(custom_domain.guid),
                routes=[
                    DomainPathRouteInput(
                        path_prefix="api",  # missing leading /
                        target_workload_slug="api",
                        target_port=8080,
                    ),
                ],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert "routes" in (result.errors[0].field or "")


@pytest.mark.django_db
def test_set_path_routes_rejects_invalid_port(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_path_routes(
            info=fake_info,
            input=SetDomainPathRoutesInput(
                domain_id=str(custom_domain.guid),
                routes=[
                    DomainPathRouteInput(
                        path_prefix="/api",
                        target_workload_slug="api",
                        target_port=99999,
                    ),
                ],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value


@pytest.mark.django_db
def test_set_path_routes_rejects_empty_workload_slug(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_path_routes(
            info=fake_info,
            input=SetDomainPathRoutesInput(
                domain_id=str(custom_domain.guid),
                routes=[
                    DomainPathRouteInput(
                        path_prefix="/api",
                        target_workload_slug="",
                        target_port=8080,
                    ),
                ],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value


@pytest.mark.django_db
def test_set_path_routes_domain_not_found(
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_path_routes(
            info=fake_info,
            input=SetDomainPathRoutesInput(
                domain_id="00000000-0000-0000-0000-000000000000",
                routes=[
                    DomainPathRouteInput(
                        path_prefix="/api",
                        target_workload_slug="api",
                        target_port=8080,
                    ),
                ],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value


@pytest.mark.django_db
def test_set_path_routes_denied_without_permission(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.set_domain_path_routes(
            info=fake_info,
            input=SetDomainPathRoutesInput(
                domain_id=str(custom_domain.guid),
                routes=[
                    DomainPathRouteInput(
                        path_prefix="/api",
                        target_workload_slug="api",
                        target_port=8080,
                    ),
                ],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    assert DomainPathRoute.objects.filter(custom_domain=custom_domain).count() == 0


@pytest.mark.django_db
def test_set_path_routes_isolates_other_orgs(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    from astrolift_identity.models import Organization

    other_org = Organization.objects.create(name="Other", slug="other-path-test")
    mutation = LifecycleMutation()
    with tenant_context(
        TenantContext(organization_id=other_org.id, actor_user_id=actor.id),
    ):
        result = mutation.set_domain_path_routes(
            info=fake_info,
            input=SetDomainPathRoutesInput(
                domain_id=str(custom_domain.guid),
                routes=[
                    DomainPathRouteInput(
                        path_prefix="/api",
                        target_workload_slug="api",
                        target_port=8080,
                    ),
                ],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value


@pytest.mark.django_db
def test_set_path_routes_null_org_fails_closed(custom_domain, fake_info, actor):
    """#1192 defense-in-depth: ``@tenant_scoped`` already blocks a null org,
    but the resolver body must ALSO fail closed if reached with org_id=None —
    otherwise it falls through to an UNSCOPED by-guid fetch and rewrites routes.
    Unwrap past the decorators to exercise that branch directly."""
    pre_existing = DomainPathRoute.objects.create(
        custom_domain=custom_domain,
        path_prefix="/keep",
        target_workload_slug="keep",
        target_port=8080,
        priority=5,
    )
    raw = inspect.unwrap(LifecycleMutation.__dict__["set_domain_path_routes"])
    with tenant_context(TenantContext(organization_id=None, actor_user_id=actor.id)):
        result = raw(
            LifecycleMutation(),
            info=fake_info,
            input=SetDomainPathRoutesInput(
                domain_id=str(custom_domain.guid),
                routes=[
                    DomainPathRouteInput(
                        path_prefix="/api",
                        target_workload_slug="api-worker",
                        target_port=8080,
                        priority=0,
                    ),
                ],
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
    # No write: the pre-existing route survives and no new route was created.
    live = list(DomainPathRoute.objects.filter(custom_domain=custom_domain, deleted_at__isnull=True))
    assert live == [pre_existing]


@pytest.mark.django_db
def test_app_domain_to_type_surfaces_active_path_routes_ordered(custom_domain):
    """AppDomainType.path_routes must omit soft-deleted rows and order by priority."""
    DomainPathRoute.objects.create(
        custom_domain=custom_domain,
        path_prefix="/api",
        target_workload_slug="api",
        target_port=8080,
        priority=5,
    )
    DomainPathRoute.objects.create(
        custom_domain=custom_domain,
        path_prefix="/",
        target_workload_slug="web",
        target_port=3000,
        priority=0,
    )
    stale = DomainPathRoute.objects.create(
        custom_domain=custom_domain,
        path_prefix="/old",
        target_workload_slug="old",
        target_port=9000,
        priority=3,
    )
    stale.soft_delete()

    out = app_domain_to_type(custom_domain)
    assert [r.path_prefix for r in out.path_routes] == ["/", "/api"]
