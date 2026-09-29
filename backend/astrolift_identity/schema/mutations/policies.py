"""PolicyMutations — split from the monolithic mutations module."""

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
from astrolift_identity.models import (
    Organization,
    Policy,
)
from astrolift_identity.policy_validation import validate_policy_shape
from astrolift_identity.schema.mutations.helpers import (
    _actor,
)
from astrolift_identity.schema.mutations.types import (
    CreatePolicyInput,
    SoftDeleteByGuidInput,
    UpdatePolicyInput,
    _SoftDeletePayload,
)
from astrolift_identity.schema.types import (
    PolicyType,
    policy_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.optimistic import check_version_match as _check_version_match
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class PolicyMutations:
    # ---- ABAC Policy -------------------------------------------------

    @strawberry.field
    @mutation_audit(action="policy.create")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def create_policy(self, info: Info, input: CreatePolicyInput) -> MutationResultType[PolicyType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        if input.scope_level not in {"ORG", "TEAM", "PROJECT", "APP"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "scopeLevel must be ORG / TEAM / PROJECT / APP",
                field="scopeLevel",
            )
        if input.effect not in {"ALLOW", "DENY"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "effect must be ALLOW or DENY",
                field="effect",
            )
        if Policy.objects.filter(organization=org, slug=input.slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"policy with slug {input.slug!r} already exists",
                field="slug",
            )

        invalid = validate_policy_shape(
            effect=input.effect,
            conditions=input.conditions if input.conditions is not None else [],
            resource_pattern=input.resource_pattern if input.resource_pattern is not None else {},
            actor_pattern=input.actor_pattern if input.actor_pattern is not None else {},
        )
        if invalid:
            field, message = invalid
            return gql_failure(ErrorCode.VALIDATION.value, message, field=field)

        # Stamp the creator / updater off the active tenant context so
        # the policies table (#415) can render a Created-by column
        # without a follow-up audit-log join. Both columns are set to
        # the same actor at create time — first edit will rotate
        # ``updated_by`` only.
        actor = _actor()
        policy = Policy.objects.create(
            organization=org,
            name=input.name.strip(),
            slug=input.slug,
            description=input.description or "",
            scope_level=input.scope_level,
            scope_id=input.scope_id,
            effect=input.effect,
            action_pattern=input.action_pattern or "*",
            resource_pattern=input.resource_pattern or {},
            conditions=input.conditions or [],
            actor_pattern=input.actor_pattern or {},
            created_by=actor,
            updated_by=actor,
        )
        return gql_success(policy_to_type(policy))

    @strawberry.field
    @mutation_audit(action="policy.update")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def update_policy(self, info: Info, input: UpdatePolicyInput) -> MutationResultType[PolicyType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        policy = Policy.objects.filter(guid=str(input.id), organization_id=org_id).first()
        if policy is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "policy not found")

        # #497 — optimistic-concurrency gate. Detect concurrent edits
        # before we overwrite a peer admin's policy change.
        mismatch = _check_version_match(policy, if_match_version=input.if_match_version, kind="Policy")
        if mismatch is not None:
            return mismatch

        invalid = validate_policy_shape(
            **{
                field: getattr(input, field) if getattr(input, field) is not None else getattr(policy, field)
                for field in ("effect", "conditions", "resource_pattern", "actor_pattern")
            }
        )
        if invalid:
            field, message = invalid
            return gql_failure(ErrorCode.VALIDATION.value, message, field=field)

        for field in (
            "name",
            "description",
            "effect",
            "action_pattern",
            "resource_pattern",
            "conditions",
            "actor_pattern",
        ):
            new_value = getattr(input, field)
            if new_value is not None:
                setattr(policy, field, new_value)
        # Rotate the updater so the "last modified by" column on the
        # policies table reflects this edit even if the creator was
        # someone else (#466).
        policy.updated_by = _actor()
        policy.save()
        return gql_success(policy_to_type(policy))

    @strawberry.field
    @mutation_audit(action="policy.delete")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def soft_delete_policy(
        self, info: Info, input: SoftDeleteByGuidInput
    ) -> MutationResultType[_SoftDeletePayload]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        policy = Policy.objects.filter(guid=str(input.id), organization_id=org_id).first()
        if policy is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "policy not found")
        policy.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))
