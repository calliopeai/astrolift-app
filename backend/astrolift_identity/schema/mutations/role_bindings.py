"""RoleBindingMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import (
    GUID,
    MutationErrorType,
    MutationResultType,
)
from astrolift_graphql import (
    failure as gql_failure,
)
from astrolift_graphql import (
    success as gql_success,
)
from astrolift_identity.grants import REFUSAL, GrantCeiling, grant_ceiling, require_grantable
from astrolift_identity.models import (
    GroupRoleMapping,
    Member,
    Organization,
    Role,
    RoleBinding,
)
from astrolift_identity.permission_resolver import _scope_ancestry
from astrolift_identity.schema.mutations.helpers import (
    _actor,
    _resolve_scope_pk_in_org,
    _resolve_team,
)
from astrolift_identity.schema.mutations.types import (
    BulkAssignTeamMemberRolesInput,
    BulkRevokeRoleBindingsInput,
    CreateGroupRoleMappingInput,
    DeleteGroupRoleMappingInput,
    GrantRoleInput,
    RevokeRoleBindingInput,
    UpdateRoleBindingInput,
    _BulkAssignTeamMemberRolesPayload,
    _BulkOpItemResult,
    _BulkRevokeRoleBindingsPayload,
    _SoftDeletePayload,
)
from astrolift_identity.schema.types import (
    GroupRoleMappingType,
    RoleBindingType,
    role_binding_to_type,
)
from astrolift_identity.scopes import (
    binding_owner_scope,
    grant_destination_scope,
    identity_organization_scope,
    role_binding_scope,
    team_scope_by_guid,
)
from astrolift_identity.step_up import requires_elevation
from astrolift_identity.visibility import visible_identity_bindings
from core.decorators import tenant_scoped
from core.mutations import AuditEntry, ErrorCode, emit_audit, mutation_audit
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    ScopeKind,
    check_permission,
    require_permission,
)
from core.tenancy import TenantContext, get_current_tenant

LAST_OWNER_REFUSAL = "only the platform operator can remove an organization's last owner"


def _removes_last_owner(binding: RoleBinding) -> bool:
    """Whether revoking ``binding`` leaves its org with no live owner.

    Only the stock ``org_owner`` at org scope counts, since a custom role's
    slug proves nothing. A binding that is expired, held by a deactivated
    account, or held by someone without an active ORG membership (who
    cannot act in the org at all) is no owner. Callers hold
    :func:`_lock_org` so two concurrent revokes can't each see the other
    owner and leave none.
    """

    role = binding.role
    if binding.scope_kind != RoleBinding.ScopeKind.ORG or not (role.is_system and role.slug == "org_owner"):
        return False
    return set(_live_owner_binding_pks(binding.scope_id, limit=2)) == {binding.pk}


def _live_owner_binding_pks(org_id: int, *, limit: int | None = None) -> list[int]:
    membership = Member.objects.filter(
        user_id=OuterRef("user_id"),
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org_id,
        is_active=True,
        deleted_at__isnull=True,
    )
    live = RoleBinding.objects.filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()),
        Exists(membership),
        role__is_system=True,
        role__slug="org_owner",
        scope_kind=RoleBinding.ScopeKind.ORG,
        scope_id=org_id,
        user__is_active=True,
    ).values_list("pk", flat=True)
    return list(live[:limit] if limit else live)


def _lock_org(org_id: int) -> None:
    """Serialize owner-removing writes in one org (#1986 review): take the
    org row lock inside the caller's transaction before counting owners."""

    Organization.all_objects.select_for_update().filter(pk=org_id).first()


def _revoke_refusal(
    binding: RoleBinding, org_id: int, ceilings: dict[tuple[str, int], GrantCeiling]
) -> str | None:
    """Why the caller may not revoke ``binding``, or ``None`` (#1977).

    Revoking takes a role's permissions away at the binding's scope, so it
    is capped like granting them there (#1964). ``ceilings`` memoizes one
    ceiling per scope across a bulk call.
    """

    key = (binding.scope_kind, binding.scope_id)
    if key not in ceilings:
        owner = binding_owner_scope(binding)
        try:
            check_permission(Permission.ORG_MANAGE_MEMBERS, scope=owner)
        except PermissionDenied as exc:
            return exc.reason
        tenant = get_current_tenant() or TenantContext()
        # The gate and the ceiling must use the same coherent owner. A
        # stale owner falls back to ORG and cannot borrow a former team's
        # wider permissions when its leftover binding is cleared.
        ceilings[key] = grant_ceiling(tenant, scope_kind=owner.kind.value, scope_id=owner.id)
    ceiling = ceilings[key]
    if not ceiling.allows(binding.role.permissions):
        return REFUSAL
    if not ceiling.unrestricted and _removes_last_owner(binding):
        return LAST_OWNER_REFUSAL
    return None


def _input_of(args, kwargs):
    payload = kwargs.get("input")
    if payload is None and len(args) >= 3:
        payload = args[2]
    return payload


def _grant_target(*args, **kwargs):
    """File a grant under the user it grants to, so a person's page can list it (#2151)."""
    user_id = getattr(_input_of(args, kwargs), "user_id", "")
    return ("user", str(user_id)) if user_id else None


def _revoke_target(*args, **kwargs):
    """File a revoke under the binding it revokes; the subject filter maps it to the user (#2151)."""
    binding_id = getattr(_input_of(args, kwargs), "id", "")
    return ("role_binding", str(binding_id)) if binding_id else None


@strawberry.type
class RoleBindingMutations:
    # ---- RBAC --------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="role_binding.grant", target=_grant_target)
    @requires_elevation(action_label="role_binding.grant")
    @require_permission(Permission.ORG_MANAGE_MEMBERS, scope=grant_destination_scope)
    @tenant_scoped()
    def grant_role(self, info: Info, input: GrantRoleInput) -> MutationResultType[RoleBindingType]:
        from django.contrib.auth import get_user_model

        # #1183 privilege-escalation fix. Without a caller-org constraint
        # on the scope, role, and target user, a caller who holds
        # ORG_MANAGE_MEMBERS in their own org could grant any role, on any
        # scope, to any user in any other org — cross-tenant account
        # takeover. Every lookup below is bound to the caller's org and
        # fails closed.
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "scope not found", field="scopeGuid")

        # Exactly one principal (#2157): a member of this org, or an IdP
        # group named by its external id.
        user_arg = (input.user_id or "").strip()
        group_id = (input.group_external_id or "").strip()
        if bool(user_arg) == bool(group_id):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "give exactly one of userId or groupExternalId",
                field="userId",
            )
        if len(group_id) > 255:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "groupExternalId may be at most 255 characters",
                field="groupExternalId",
            )
        if input.expires_at is not None and input.expires_at <= timezone.now():
            return gql_failure(
                ErrorCode.VALIDATION.value, "expiresAt must be in the future", field="expiresAt"
            )

        user = None
        if user_arg:
            try:
                target_user_pk = int(user_arg)
            except ValueError:
                return gql_failure(ErrorCode.VALIDATION.value, "userId must be a numeric pk", field="userId")

            # The target must already be a member of the caller's org.
            # Checking org membership (rather than global user existence)
            # both enforces the tenant boundary and avoids leaking whether an
            # arbitrary user pk exists anywhere on the install.
            if not Member.objects.filter(
                user_id=target_user_pk,
                scope_kind=Member.ScopeKind.ORG,
                scope_id=org_id,
                deleted_at__isnull=True,
            ).exists():
                return gql_failure(ErrorCode.NOT_FOUND.value, "user not found", field="userId")

            User = get_user_model()
            user = User.objects.filter(pk=target_user_pk).first()
            if user is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "user not found", field="userId")

        # Restrict the role to the caller's own custom roles or a
        # system/null-org role — another org's custom role reads as
        # not-found.
        role = (
            Role.objects.filter(guid=str(input.role_id))
            .filter(Q(organization_id=org_id) | Q(organization__isnull=True))
            .first()
        )
        if role is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role not found", field="roleId")

        # Resolve the scope guid WITHIN the caller org — a scope owned by
        # another org resolves to None and reads as not-found.
        scope_kind = input.scope_kind.upper()
        scope_id = _resolve_scope_pk_in_org(scope_kind, str(input.scope_guid), org_id)
        if scope_id is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "scope not found", field="scopeGuid")

        # #1964: the gate above says the caller may grant roles, not which
        # ones. Without this, org.manage_members alone reaches org_owner.
        require_grantable(
            role.permissions,
            scope_kind=scope_kind,
            scope_id=scope_id,
            gate=Permission.ORG_MANAGE_MEMBERS,
        )

        principal = (
            {"user": user} if user is not None else {"user__isnull": True, "group_external_id": group_id}
        )
        if RoleBinding.objects.filter(
            role=role, scope_kind=scope_kind, scope_id=scope_id, **principal
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                "this user already has this role on this scope"
                if user is not None
                else "this group already has this role on this scope",
            )

        binding = RoleBinding.objects.create(
            user=user,
            group_external_id=group_id,
            role=role,
            scope_kind=scope_kind,
            scope_id=scope_id,
            granted_by=_actor(),
            expires_at=input.expires_at,
        )

        # Auto-add a Member row at the target scope so middleware can
        # resolve the tenant when this user logs in. A group has no
        # Member row: its members already belong to the org.
        if user is not None:
            Member.objects.get_or_create(
                user=user,
                scope_kind=scope_kind,
                scope_id=scope_id,
                defaults={"is_active": True, "lifecycle": "active"},
            )

        # Resolve a single source-scope label inline so the FE can show
        # the role-source tooltip on the freshly-granted binding without
        # an extra refetch.
        from astrolift_identity.schema.queries import _resolve_source_scope_labels

        label = _resolve_source_scope_labels([binding]).get((binding.scope_kind, binding.scope_id), "")
        return gql_success(role_binding_to_type(binding, source_scope_label=label))

    @strawberry.field
    @mutation_audit(action="role_binding.update")
    @requires_elevation(action_label="role_binding.update")
    @require_permission(Permission.ORG_MANAGE_MEMBERS, scope=role_binding_scope)
    @tenant_scoped()
    def update_role_binding(
        self, info: Info, input: UpdateRoleBindingInput
    ) -> MutationResultType[RoleBindingType]:
        """Change a binding's role or its expiry (#2157).

        Capped like a revoke of the old role and a grant of the new one at
        the binding's scope, and an org keeps its last owner: moving the
        last owner to another role, or putting an expiry on that binding,
        is refused unless the caller is the platform operator.
        """
        from astrolift_identity.schema.queries import _org_scope_q, _resolve_source_scope_labels

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role binding not found")
        binding = (
            RoleBinding.objects.select_related("role", "user")
            .filter(guid=str(input.id))
            .filter(_org_scope_q(org_id))
            .first()
        )
        if binding is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role binding not found")

        new_role = None
        if input.role_id is not strawberry.UNSET and input.role_id is not None:
            new_role = (
                Role.objects.filter(guid=str(input.role_id))
                .filter(Q(organization_id=org_id) | Q(organization__isnull=True))
                .first()
            )
            if new_role is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "role not found", field="roleId")
        expiry_given = input.expires_at is not strawberry.UNSET
        if expiry_given and input.expires_at is not None and input.expires_at <= timezone.now():
            return gql_failure(
                ErrorCode.VALIDATION.value, "expiresAt must be in the future", field="expiresAt"
            )
        if new_role is None and not expiry_given:
            return gql_failure(ErrorCode.VALIDATION.value, "give roleId or expiresAt", field="roleId")
        role_changes = new_role is not None and new_role.pk != binding.role_id

        with transaction.atomic():
            _lock_org(org_id)
            ceilings: dict[tuple[str, int], GrantCeiling] = {}
            # Changing either field can take the old role's access away, so
            # it is capped like a revoke, last owner included.
            refusal = _revoke_refusal(binding, org_id, ceilings)
            takes_owner_away = role_changes or (expiry_given and input.expires_at is not None)
            if refusal == LAST_OWNER_REFUSAL and not takes_owner_away:
                refusal = None
            if refusal is None and role_changes:
                if not ceilings[(binding.scope_kind, binding.scope_id)].allows(new_role.permissions):
                    refusal = REFUSAL
            if refusal is not None:
                raise PermissionDenied(
                    Permission.ORG_MANAGE_MEMBERS,
                    PermissionScope(kind=ScopeKind(binding.scope_kind), id=binding.scope_id),
                    refusal,
                )
            if role_changes:
                duplicate = RoleBinding.objects.filter(
                    role=new_role, scope_kind=binding.scope_kind, scope_id=binding.scope_id
                ).exclude(pk=binding.pk)
                duplicate = (
                    duplicate.filter(user_id=binding.user_id)
                    if binding.user_id is not None
                    else duplicate.filter(user__isnull=True, group_external_id=binding.group_external_id)
                )
                if duplicate.exists():
                    return gql_failure(
                        ErrorCode.CONFLICT.value, "this principal already has that role on this scope"
                    )
                binding.role = new_role
            if expiry_given:
                binding.expires_at = input.expires_at
            binding.save()

        label = _resolve_source_scope_labels([binding]).get((binding.scope_kind, binding.scope_id), "")
        return gql_success(role_binding_to_type(binding, source_scope_label=label))

    # ---- IdP group mappings (#2157) -----------------------------------

    @strawberry.field
    @mutation_audit(action="group_role_mapping.create")
    @requires_elevation(action_label="group_role_mapping.create")
    @require_permission(
        Permission.ORG_MANAGE_MEMBERS, scope=identity_organization_scope(Permission.ORG_MANAGE_MEMBERS)
    )
    @tenant_scoped()
    def create_group_role_mapping(
        self, info: Info, input: CreateGroupRoleMappingInput
    ) -> MutationResultType[GroupRoleMappingType]:
        """Map an IdP group to a role on a scope of this org. Applies to
        every member the IdP puts in the group, exactly like a group
        binding, and is capped by the caller's own reach there (#1964)."""
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "scope not found", field="scopeGuid")
        group_id = (input.group_external_id or "").strip()
        if not group_id or len(group_id) > 255:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "groupExternalId is required and at most 255 characters",
                field="groupExternalId",
            )
        role = (
            Role.objects.filter(guid=str(input.role_id))
            .filter(Q(organization_id=org_id) | Q(organization__isnull=True))
            .first()
        )
        if role is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role not found", field="roleId")
        scope_kind = input.scope_kind.upper()
        scope_id = _resolve_scope_pk_in_org(scope_kind, str(input.scope_guid), org_id)
        if scope_id is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "scope not found", field="scopeGuid")
        require_grantable(
            role.permissions,
            scope_kind=scope_kind,
            scope_id=scope_id,
            gate=Permission.ORG_MANAGE_MEMBERS,
        )
        if GroupRoleMapping.objects.filter(
            organization_id=org_id,
            group_external_id=group_id,
            role=role,
            scope_kind=scope_kind,
            scope_id=scope_id,
        ).exists():
            return gql_failure(ErrorCode.CONFLICT.value, "this group already maps to this role on this scope")
        mapping = GroupRoleMapping.objects.create(
            organization_id=org_id,
            group_external_id=group_id,
            role=role,
            scope_kind=scope_kind,
            scope_id=scope_id,
        )
        from astrolift_identity.schema.queries import group_role_mapping_types

        return gql_success(group_role_mapping_types([mapping], org_id)[0])

    @strawberry.field
    @mutation_audit(action="group_role_mapping.delete")
    @requires_elevation(action_label="group_role_mapping.delete")
    @require_permission(
        Permission.ORG_MANAGE_MEMBERS, scope=identity_organization_scope(Permission.ORG_MANAGE_MEMBERS)
    )
    @tenant_scoped()
    def delete_group_role_mapping(
        self, info: Info, input: DeleteGroupRoleMappingInput
    ) -> MutationResultType[_SoftDeletePayload]:
        """Remove a group mapping. Capped like revoking a binding: the
        mapping's role must be within the caller's reach at its scope."""
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        mapping = (
            GroupRoleMapping.objects.select_related("role")
            .filter(guid=str(input.id), organization_id=org_id)
            .first()
            if org_id is not None
            else None
        )
        if mapping is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "group mapping not found")
        kind, scope_id = mapping.scope_kind, mapping.scope_id
        if not _scope_ancestry(tenant, PermissionScope(kind=ScopeKind(kind), id=scope_id)):
            # The scope was deleted; the org's own ceiling decides.
            kind, scope_id = "ORG", org_id
        if not grant_ceiling(tenant, scope_kind=kind, scope_id=scope_id).allows(mapping.role.permissions):
            raise PermissionDenied(
                Permission.ORG_MANAGE_MEMBERS,
                PermissionScope(kind=ScopeKind(kind), id=scope_id),
                REFUSAL,
            )
        mapping.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    @strawberry.field
    @mutation_audit(action="role_binding.revoke", target=_revoke_target)
    @requires_elevation(action_label="role_binding.revoke")
    @require_permission(Permission.ORG_MANAGE_MEMBERS, scope=role_binding_scope)
    @tenant_scoped()
    def revoke_role_binding(
        self, info: Info, input: RevokeRoleBindingInput
    ) -> MutationResultType[_SoftDeletePayload]:
        from astrolift_identity.schema.queries import _org_scope_q

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role binding not found")
        # Only bindings on the caller org's own scopes are revocable; a
        # foreign-org binding guid reads as not-found.
        binding = (
            RoleBinding.objects.select_related("role")
            .filter(guid=str(input.id))
            .filter(_org_scope_q(org_id))
            .first()
        )
        if binding is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role binding not found")
        with transaction.atomic():
            _lock_org(org_id)
            refusal = _revoke_refusal(binding, org_id, {})
            if refusal is not None:
                raise PermissionDenied(
                    Permission.ORG_MANAGE_MEMBERS,
                    PermissionScope(kind=ScopeKind(binding.scope_kind), id=binding.scope_id),
                    refusal,
                )
            binding.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- Bulk RBAC ---------------------------------------------------
    #
    # Bulk operations stay in the same MutationResult envelope as their
    # single-item counterparts. The top-level ``ok`` flips false only on
    # input-shape errors (empty list, missing team, etc.); per-id outcomes
    # (already-revoked, not-found, partial denies) live in
    # ``data.results`` so the UI can render row-level state without
    # losing the rows that succeeded. Each item also emits an individual
    # audit row via ``emit_audit`` so an operator who revokes 50
    # bindings at once still gets 50 audit entries — one per affected
    # subject — to satisfy compliance review.

    @strawberry.field
    @mutation_audit(action="role_binding.bulk_revoke")
    @requires_elevation(action_label="role_binding.bulk_revoke")
    @require_permission(Permission.ORG_MANAGE_MEMBERS, any_scope=True)
    @tenant_scoped()
    def bulk_revoke_astrolift_role_bindings(
        self, info: Info, input: BulkRevokeRoleBindingsInput
    ) -> MutationResultType[_BulkRevokeRoleBindingsPayload]:
        ids = list(input.binding_ids or [])
        if not ids:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "bindingIds must contain at least one id",
                field="bindingIds",
            )
        # Cap the batch size so a runaway operator can't lock the table
        # for minutes; matches the largest realistic offboarding wave.
        if len(ids) > 500:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "bindingIds may not exceed 500 entries per call",
                field="bindingIds",
            )

        # De-duplicate while preserving submission order so duplicate
        # selections don't double-count in the audit log.
        seen: set[str] = set()
        ordered_unique: list[str] = []
        for gid in ids:
            gid_s = str(gid)
            if gid_s in seen:
                continue
            seen.add(gid_s)
            ordered_unique.append(gid_s)

        actor = _actor()
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None

        from astrolift_identity.schema.queries import _org_scope_q

        # Pre-fetch in one query so a 500-id batch is one trip, not 500.
        # Key by ``str(guid)`` because ``binding.guid`` is a ``UUID``
        # instance, not a string — a dict lookup with the operator-
        # supplied string would otherwise miss every row. Scoped to the
        # caller org's own scopes so a foreign-org binding guid reads as
        # not-found (fail closed) instead of being revocable cross-tenant.
        bindings_by_guid = {
            str(b.guid): b
            for b in visible_identity_bindings(
                RoleBinding.objects.select_related("user", "role")
                .filter(guid__in=ordered_unique, deleted_at__isnull=True)
                .filter(_org_scope_q(org_id))
            )
        }

        results: list[_BulkOpItemResult] = []
        revoked = 0
        failed = 0
        ceilings: dict[tuple[str, int], GrantCeiling] = {}

        # One lock for the whole batch: the last-owner check below must see
        # every revoke that lands before it, including concurrent ones.
        with transaction.atomic():
            if org_id is not None:
                _lock_org(org_id)
            for gid_s in ordered_unique:
                binding = bindings_by_guid.get(gid_s)
                if binding is None:
                    failed += 1
                    results.append(
                        _BulkOpItemResult(
                            id=GUID(gid_s),
                            ok=False,
                            errors=[
                                MutationErrorType(
                                    code=ErrorCode.NOT_FOUND.value,
                                    message="role binding not found or already revoked",
                                    field=None,
                                )
                            ],
                        )
                    )
                    continue

                # Checked per binding and after the earlier ones in this batch
                # are revoked, so a batch cannot remove every owner at once.
                refusal = _revoke_refusal(binding, org_id, ceilings)
                if refusal is not None:
                    failed += 1
                    results.append(
                        _BulkOpItemResult(
                            id=GUID(gid_s),
                            ok=False,
                            errors=[
                                MutationErrorType(
                                    code=ErrorCode.PERMISSION_DENIED.value,
                                    message=refusal,
                                    field=None,
                                )
                            ],
                        )
                    )
                    emit_audit(
                        AuditEntry(
                            actor_user_id=actor.pk if actor else None,
                            organization_id=org_id,
                            action="role_binding.revoke",
                            decision="DENY",
                            target_kind="role_binding",
                            target_id=str(binding.guid),
                            duration_ms=0,
                            permissions=(Permission.ORG_MANAGE_MEMBERS.value,),
                            error_code=ErrorCode.PERMISSION_DENIED.value,
                            error_message=refusal,
                            extra={
                                "bulk": True,
                                "user_id": binding.user_id,
                                "role_id": binding.role_id,
                                "scope_kind": binding.scope_kind,
                                "scope_id": binding.scope_id,
                            },
                        )
                    )
                    continue

                binding.soft_delete(by=actor)
                revoked += 1
                results.append(_BulkOpItemResult(id=GUID(gid_s), ok=True, errors=[]))

                # Per-binding audit row so the trail names *which* subject /
                # role lost access, not just "50 bindings revoked".
                emit_audit(
                    AuditEntry(
                        actor_user_id=actor.pk if actor else None,
                        organization_id=org_id,
                        action="role_binding.revoke",
                        decision="ALLOW",
                        target_kind="role_binding",
                        target_id=str(binding.guid),
                        duration_ms=0,
                        permissions=(Permission.ORG_MANAGE_MEMBERS.value,),
                        error_code=None,
                        error_message=None,
                        extra={
                            "bulk": True,
                            "user_id": binding.user_id,
                            "role_id": binding.role_id,
                            "scope_kind": binding.scope_kind,
                            "scope_id": binding.scope_id,
                        },
                    )
                )

        return gql_success(
            _BulkRevokeRoleBindingsPayload(
                results=results,
                revoked_count=revoked,
                failed_count=failed,
            )
        )

    @strawberry.field
    @mutation_audit(action="role_binding.bulk_assign_team")
    @requires_elevation(action_label="role_binding.bulk_assign_team")
    @require_permission(
        Permission.TEAM_MANAGE_MEMBERS,
        scope=team_scope_by_guid("input.team_id", permission=Permission.TEAM_MANAGE_MEMBERS),
    )
    @tenant_scoped()
    def bulk_assign_astrolift_team_member_roles(
        self, info: Info, input: BulkAssignTeamMemberRolesInput
    ) -> MutationResultType[_BulkAssignTeamMemberRolesPayload]:
        member_ids = list(input.member_ids or [])
        if not member_ids:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "memberIds must contain at least one id",
                field="memberIds",
            )
        if len(member_ids) > 500:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "memberIds may not exceed 500 entries per call",
                field="memberIds",
            )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # The team must belong to the caller's org — a foreign team guid
        # reads as not-found so a caller can't bulk-assign roles into
        # another tenant's team.
        team = _resolve_team(input.team_id, org_id)
        if team is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "team not found", field="teamId")

        # Restrict the role to the caller's own custom roles or a
        # system/null-org role — another org's custom role reads as
        # not-found. Without this a caller could attach a foreign org's
        # custom role to their own team's members (same cross-tenant grant
        # class grant_role guards against). Mirrors the grant_role fix (#1183).
        role = (
            Role.objects.filter(guid=str(input.role_id), deleted_at__isnull=True)
            .filter(Q(organization_id=org_id) | Q(organization__isnull=True))
            .first()
        )
        if role is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role not found", field="roleId")

        # A team-scoped binding only makes sense with a role whose
        # scope_level is TEAM (or ORG-level promoted down). We accept
        # ORG/TEAM here because some installs treat their ORG-level
        # role catalog as the union of all scopes; PROJECT/APP scoped
        # roles are deliberately rejected so we don't create bindings
        # the resolver chain can't ever evaluate.
        if role.scope_level not in {"ORG", "TEAM"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"role scope_level {role.scope_level!r} cannot be granted on a team scope",
                field="roleId",
            )

        # #1964: team.manage_members must not reach roles wider than the
        # caller's own reach on this team; org_owner is an ORG-level role and
        # passes the scope_level check above.
        require_grantable(
            role.permissions,
            scope_kind="TEAM",
            scope_id=team.pk,
            gate=Permission.TEAM_MANAGE_MEMBERS,
        )

        seen: set[str] = set()
        ordered_unique: list[str] = []
        for gid in member_ids:
            gid_s = str(gid)
            if gid_s in seen:
                continue
            seen.add(gid_s)
            ordered_unique.append(gid_s)

        # Pull members in one query. Restrict to TEAM-scope members of
        # *this* team so the operator can't slip in a member guid from a
        # sibling team and grant a role into a scope they didn't intend.
        members_by_guid = {
            str(m.guid): m
            for m in Member.objects.select_related("user").filter(
                guid__in=ordered_unique,
                scope_kind=Member.ScopeKind.TEAM,
                scope_id=team.pk,
                deleted_at__isnull=True,
            )
        }

        # Existing bindings on this team for this role keyed by user_id
        # so the per-id loop can answer "already had it?" in O(1).
        existing_user_ids = set(
            RoleBinding.objects.filter(
                role=role,
                scope_kind=RoleBinding.ScopeKind.TEAM,
                scope_id=team.pk,
                deleted_at__isnull=True,
            ).values_list("user_id", flat=True)
        )

        actor = _actor()

        results: list[_BulkOpItemResult] = []
        assigned = 0
        already = 0
        failed = 0

        for gid_s in ordered_unique:
            member = members_by_guid.get(gid_s)
            if member is None:
                failed += 1
                results.append(
                    _BulkOpItemResult(
                        id=GUID(gid_s),
                        ok=False,
                        errors=[
                            MutationErrorType(
                                code=ErrorCode.NOT_FOUND.value,
                                message="team member not found",
                                field=None,
                            )
                        ],
                    )
                )
                continue
            if member.user_id is None:
                failed += 1
                results.append(
                    _BulkOpItemResult(
                        id=GUID(gid_s),
                        ok=False,
                        errors=[
                            MutationErrorType(
                                code=ErrorCode.PRECONDITION.value,
                                message="member is not bound to a user",
                                field=None,
                            )
                        ],
                    )
                )
                continue

            if member.user_id in existing_user_ids:
                already += 1
                results.append(
                    _BulkOpItemResult(
                        id=GUID(gid_s),
                        ok=True,
                        already_existed=True,
                        errors=[],
                    )
                )
                continue

            binding = RoleBinding.objects.create(
                user_id=member.user_id,
                role=role,
                scope_kind=RoleBinding.ScopeKind.TEAM,
                scope_id=team.pk,
                granted_by=actor,
            )
            existing_user_ids.add(member.user_id)
            assigned += 1
            results.append(_BulkOpItemResult(id=GUID(gid_s), ok=True, errors=[]))

            emit_audit(
                AuditEntry(
                    actor_user_id=actor.pk if actor else None,
                    organization_id=org_id,
                    action="role_binding.grant",
                    decision="ALLOW",
                    target_kind="role_binding",
                    target_id=str(binding.guid),
                    duration_ms=0,
                    permissions=(Permission.TEAM_MANAGE_MEMBERS.value,),
                    error_code=None,
                    error_message=None,
                    extra={
                        "bulk": True,
                        "user_id": member.user_id,
                        "role_id": role.pk,
                        "team_id": team.pk,
                        "scope_kind": "TEAM",
                    },
                )
            )

        return gql_success(
            _BulkAssignTeamMemberRolesPayload(
                results=results,
                assigned_count=assigned,
                already_assigned_count=already,
                failed_count=failed,
            )
        )
