"""RoleMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import (
    MutationResultType,
)
from astrolift_graphql import (
    failure as gql_failure,
)
from astrolift_graphql import (
    success as gql_success,
)
from astrolift_identity.grants import require_grantable
from astrolift_identity.models import (
    Role,
)
from astrolift_identity.schema.mutations.helpers import (
    _actor,
)
from astrolift_identity.schema.mutations.types import (
    CreateRoleInput,
    DeleteRoleInput,
    UpdateRoleInput,
    _SoftDeletePayload,
)
from astrolift_identity.schema.types import (
    RoleType,
    role_to_type,
)
from astrolift_identity.scopes import identity_organization_scope
from astrolift_identity.step_up import requires_elevation
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class RoleMutations:
    # ---- Custom roles ----------------------------------------------------

    @strawberry.field
    @mutation_audit(action="role.create")
    @require_permission(
        Permission.ORG_MANAGE_MEMBERS, scope=identity_organization_scope(Permission.ORG_MANAGE_MEMBERS)
    )
    @tenant_scoped()
    def create_role(self, info: Info, input: CreateRoleInput) -> MutationResultType[RoleType]:
        """Define a custom role for the current org.

        System roles ship with the platform (``is_system=True``) and
        are not editable here — operators clone-and-prune by creating
        a new custom role with the subset of permissions they want.
        Permissions are validated against the catalog at the model
        level (``Role.clean()``); a typo surfaces as VALIDATION.
        """
        from django.core.exceptions import ValidationError

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        scope = input.scope_level.upper()
        if scope not in {"ORG", "TEAM", "PROJECT", "APP"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"scope_level must be ORG/TEAM/PROJECT/APP, got {scope!r}",
                field="scopeLevel",
            )

        slug = input.slug.strip().lower()
        if not slug:
            return gql_failure(ErrorCode.VALIDATION.value, "slug required", field="slug")

        if Role.objects.filter(organization_id=org_id, slug=slug, deleted_at__isnull=True).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"a role with slug {slug!r} already exists in this org",
                field="slug",
            )

        source = None
        if input.duplicated_from_id is not None:
            from django.db.models import Q

            source = (
                Role.objects.filter(guid=str(input.duplicated_from_id))
                .filter(Q(organization_id=org_id) | Q(organization__isnull=True))
                .first()
            )
            if source is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value, "role to duplicate not found", field="duplicatedFromId"
                )

        role = Role(
            organization_id=org_id,
            duplicated_from=source,
            slug=slug,
            name=input.name.strip(),
            description=input.description or "",
            scope_level=scope,
            permissions=list(input.permissions or []),
            is_system=False,
        )
        try:
            role.full_clean()
        except ValidationError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "; ".join(f"{k}: {v[0]}" for k, v in exc.message_dict.items()),
                field="permissions" if "permissions" in exc.message_dict else None,
            )
        # Whoever grants this role later trusts the name and slug its author
        # chose, so a role wider than its author would hand out, through that
        # grant, permissions the author could never grant (#1964).
        require_grantable(
            role.permissions,
            scope_kind="ORG",
            scope_id=org_id,
            gate=Permission.ORG_MANAGE_MEMBERS,
        )
        role.save()
        return gql_success(role_to_type(role))

    @strawberry.field
    @mutation_audit(action="role.update")
    @requires_elevation(action_label="role.update")
    @require_permission(
        Permission.ORG_MANAGE_MEMBERS, scope=identity_organization_scope(Permission.ORG_MANAGE_MEMBERS)
    )
    @tenant_scoped()
    def update_role(self, info: Info, input: UpdateRoleInput) -> MutationResultType[RoleType]:
        """Update a custom role. System roles are read-only here.

        Lookup is by guid first so we can return PRECONDITION for the
        is_system case explicitly. Cross-org access then falls through
        to NOT_FOUND.
        """
        from django.core.exceptions import ValidationError

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        role = Role.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
        if role is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role not found")
        if role.is_system:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "system roles cannot be edited; clone into a custom role instead",
            )
        if role.organization_id != org_id:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role not found")

        fields_to_update = ["updated_at", "version"]
        before = set(role.permissions or ())
        if input.name is not None:
            role.name = input.name.strip()
            fields_to_update.append("name")
        if input.description is not None:
            role.description = input.description
            fields_to_update.append("description")
        if input.permissions is not None:
            role.permissions = list(input.permissions)
            fields_to_update.append("permissions")

        try:
            role.full_clean()
        except ValidationError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "; ".join(f"{k}: {v[0]}" for k, v in exc.message_dict.items()),
                field="permissions" if "permissions" in exc.message_dict else None,
            )
        # Everyone bound to the role gains what is added, the editor included
        # when bound to it, so adding is granting org-wide (#1964). Removing
        # takes it from everyone bound, and a rename changes what later
        # granters read, so the role's existing permissions count as well.
        require_grantable(
            before | set(role.permissions or ()),
            scope_kind="ORG",
            scope_id=org_id,
            gate=Permission.ORG_MANAGE_MEMBERS,
        )
        role.save(update_fields=fields_to_update)
        return gql_success(role_to_type(role))

    @strawberry.field
    @mutation_audit(action="role.delete")
    @requires_elevation(action_label="role.delete")
    @require_permission(
        Permission.ORG_MANAGE_MEMBERS, scope=identity_organization_scope(Permission.ORG_MANAGE_MEMBERS)
    )
    @tenant_scoped()
    def soft_delete_role(self, info: Info, input: DeleteRoleInput) -> MutationResultType[_SoftDeletePayload]:
        """Soft-delete a custom role. Bindings to it stay in place
        until separately revoked — clearing them in the same call
        would silently kick users out of access; deliberate.

        Same lookup pattern as update_role: by guid first so the
        is_system case returns PRECONDITION explicitly.
        """
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        role = Role.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
        if role is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role not found")
        if role.is_system:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "system roles cannot be deleted",
            )
        if role.organization_id != org_id:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role not found")
        # Deleting takes the role away from everyone bound to it, so it is
        # capped like handing it out (#1964).
        require_grantable(
            role.permissions,
            scope_kind="ORG",
            scope_id=org_id,
            gate=Permission.ORG_MANAGE_MEMBERS,
        )
        role.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))
