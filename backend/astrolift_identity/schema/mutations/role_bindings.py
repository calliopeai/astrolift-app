"""RoleBindingMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.db.models import Q
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
from astrolift_identity.models import (
    Member,
    Role,
    RoleBinding,
)
from astrolift_identity.schema.mutations.helpers import (
    _actor,
    _resolve_scope_pk_in_org,
    _resolve_team,
)
from astrolift_identity.schema.mutations.types import (
    BulkAssignTeamMemberRolesInput,
    BulkRevokeRoleBindingsInput,
    GrantRoleInput,
    RevokeRoleBindingInput,
    _BulkAssignTeamMemberRolesPayload,
    _BulkOpItemResult,
    _BulkRevokeRoleBindingsPayload,
    _SoftDeletePayload,
)
from astrolift_identity.schema.types import (
    RoleBindingType,
    role_binding_to_type,
)
from astrolift_identity.step_up import requires_elevation
from core.decorators import tenant_scoped
from core.mutations import AuditEntry, ErrorCode, emit_audit, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class RoleBindingMutations:
    # ---- RBAC --------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="role_binding.grant")
    @requires_elevation(action_label="role_binding.grant")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
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

        try:
            target_user_pk = int(input.user_id)
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

        if RoleBinding.objects.filter(
            user=user, role=role, scope_kind=scope_kind, scope_id=scope_id
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                "this user already has this role on this scope",
            )

        binding = RoleBinding.objects.create(
            user=user,
            role=role,
            scope_kind=scope_kind,
            scope_id=scope_id,
            granted_by=_actor(),
        )

        # Auto-add a Member row at the target scope so middleware can
        # resolve the tenant when this user logs in.
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
    @mutation_audit(action="role_binding.revoke")
    @requires_elevation(action_label="role_binding.revoke")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
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
        binding = RoleBinding.objects.filter(guid=str(input.id)).filter(_org_scope_q(org_id)).first()
        if binding is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role binding not found")
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
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
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
            for b in RoleBinding.objects.select_related("user", "role")
            .filter(guid__in=ordered_unique, deleted_at__isnull=True)
            .filter(_org_scope_q(org_id))
        }

        results: list[_BulkOpItemResult] = []
        revoked = 0
        failed = 0

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
    @require_permission(Permission.TEAM_MANAGE_MEMBERS)
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
