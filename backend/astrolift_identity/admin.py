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
    ApiToken,
    DeviceFlowSession,
    GroupRoleMapping,
    IdentityProvider,
    Invitation,
    Member,
    Organization,
    OrganizationAllowlistedDomain,
    OrganizationModule,
    OrgDomain,
    Policy,
    Project,
    Role,
    RoleBinding,
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


@admin.register(OrganizationAllowlistedDomain)
class OrganizationAllowlistedDomainAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = ("domain", "organization", "default_role", "requires_review", "deleted_at")
    list_filter = ("requires_review",)
    search_fields = ("domain",)
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(OrganizationModule)
class OrganizationModuleAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = ("key", "organization", "enabled", "deleted_at")
    list_filter = ("key", "enabled")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(Role)
class RoleAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = ("name", "slug", "scope_level", "is_system", "organization")
    list_filter = ("scope_level", "is_system")
    search_fields = ("name", "slug")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(RoleBinding)
class RoleBindingAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = ("user", "group_external_id", "role", "scope_kind", "scope_id", "expires_at")
    list_filter = ("scope_kind", "role")
    search_fields = ("user__username", "user__email", "group_external_id")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(Member)
class MemberAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = ("user", "scope_kind", "scope_id", "lifecycle", "is_active")
    list_filter = ("scope_kind", "lifecycle", "is_active")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(Invitation)
class InvitationAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = ("email", "scope_kind", "scope_id", "status", "expires_at")
    list_filter = ("status", "scope_kind")
    search_fields = ("email",)
    readonly_fields = ("guid", "created_at", "updated_at", "version", "token_hash")


@admin.register(GroupRoleMapping)
class GroupRoleMappingAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = ("organization", "group_external_id", "role", "scope_kind", "scope_id")
    list_filter = ("scope_kind",)
    search_fields = ("group_external_id",)
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(ApiToken)
class ApiTokenAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = ("name", "user", "organization", "team", "is_revoked", "expires_at", "last_used_at")
    list_filter = ("is_revoked",)
    search_fields = ("name", "user__username", "user__email")
    readonly_fields = ("guid", "created_at", "updated_at", "version", "token_hash")


@admin.register(DeviceFlowSession)
class DeviceFlowSessionAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "client_label",
        "client_kind",
        "state",
        "approved_user",
        "organization",
        "expires_at",
        "consumed_at",
    )
    list_filter = ("state", "client_kind")
    search_fields = (
        "client_label",
        "session_guid",
        "approved_user__email",
        "approved_user__username",
    )
    readonly_fields = (
        "guid",
        "session_guid",
        "created_at",
        "updated_at",
        "version",
        "refresh_token_hash",
    )


@admin.register(Policy)
class PolicyAdmin(_TenantAdminMixin, admin.ModelAdmin):
    list_display = ("name", "slug", "scope_level", "scope_id", "effect", "action_pattern")
    list_filter = ("scope_level", "effect")
    search_fields = ("name", "slug", "action_pattern")
    readonly_fields = ("guid", "created_at", "updated_at", "version")
