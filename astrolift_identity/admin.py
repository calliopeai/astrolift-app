"""
Admin registrations for ``astrolift_identity`` models.

We keep these intentionally minimal — the admin is a debugging tool,
not the primary management surface. The GraphQL/REST API is the public
contract; admin pages are for operators reading data after a support
escalation.
"""

from __future__ import annotations

from django.contrib import admin

from astrolift_identity.models import (
    IdentityProvider,
    OrgDomain,
    Organization,
    Project,
    Team,
)


class _TenantAdminMixin:
    """Default to ``all_objects`` so admins can see soft-deleted rows."""

    def get_queryset(self, request):
        return self.model.all_objects.all()


@admin.register(Organization)
class OrganizationAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = ("name", "slug", "guid", "scim_enabled", "deleted_at", "created_at")
    list_filter = ("scim_enabled", "deleted_at")
    search_fields = ("name", "slug", "guid")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(Team)
class TeamAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = ("name", "slug", "organization", "deleted_at")
    list_filter = ("organization",)
    search_fields = ("name", "slug")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(Project)
class ProjectAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = ("name", "slug", "team", "organization", "deleted_at")
    list_filter = ("organization", "team")
    search_fields = ("name", "slug")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(IdentityProvider)
class IdentityProviderAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = ("organization", "kind", "client_id", "is_default", "deleted_at")
    list_filter = ("kind", "is_default")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(OrgDomain)
class OrgDomainAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = ("domain", "organization", "jit_enabled", "deleted_at")
    list_filter = ("jit_enabled",)
    search_fields = ("domain",)
    readonly_fields = ("guid", "created_at", "updated_at", "version")
