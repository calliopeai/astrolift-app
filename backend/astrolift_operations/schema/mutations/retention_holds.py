"""Place and release observability retention holds (#1602 step 2).

The operator surface for the table `is_held` never had. A hold is how
someone says "an incident is live, do not delete this window yet", so it
has to exist and be usable before eviction is switched on anywhere --
otherwise the first activation deletes data an incident is depending on and
there is no mechanism to stop it.

Gated on `ORG_UPDATE`, which is the same permission that already guards
editing the retention days themselves. Deliberately not a new `Permission`
member: adding one costs a `resync_system_roles` migration, and "can change
how long we keep data" and "can stop us deleting some of it" are the same
authority.
"""

from __future__ import annotations

import strawberry
from django.contrib.auth import get_user_model
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_operations.models import ObservabilityRetentionHold
from astrolift_operations.schema.mutations.helpers import _caller_org_id
from astrolift_operations.schema.mutations.types import (
    PlaceObservabilityRetentionHoldInput,
    ReleaseObservabilityRetentionHoldInput,
)
from astrolift_operations.schema.types import (
    ObservabilityRetentionHoldType,
    observability_retention_hold_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class RetentionHoldMutations:
    @strawberry.field
    @mutation_audit(action="observability.retention_hold.place")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def place_observability_retention_hold(
        self,
        info: Info,
        input: PlaceObservabilityRetentionHoldInput,
    ) -> MutationResultType[ObservabilityRetentionHoldType]:
        """Hold a window of observability data against eviction.

        The window bounds a slice of *data*, not a period during which the
        hold applies: a hold over last Tuesday stays in force indefinitely.
        That is why there is no TTL here and why releasing is explicit --
        an `AlertMute`-style auto-expiry would delete the exact data the
        hold was placed to keep, on a timer, silently.
        """
        org_id = _caller_org_id()
        if org_id is None:
            # Deny-by-default. `@tenant_scoped()` asserts a tenant exists;
            # it does not filter anything (#1183).
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "no organization in context",
                field="organizationId",
            )

        reason = (input.reason or "").strip()
        if not reason:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "reason is required",
                field="reason",
            )

        if input.ends_at < input.starts_at:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "endsAt must be on or after startsAt",
                field="endsAt",
            )

        # Refused rather than accepted-and-ignored (#1663).
        #
        # The columns exist and the policy's match rules honour them, but
        # nothing between a scoped hold and a delete does: the window
        # subtraction cannot express "some rows in this window and not
        # others" so it skips scoped holds, and `is_held`'s interior probe
        # carries no resource so it does not match one either. The whole
        # stream window survives and the held resource's rows go with it.
        #
        # Accepting the input would be the worst available behaviour: an
        # operator places a hold during an incident, the API says ok, and
        # the data is deleted anyway. Refusing is honest, and points at the
        # form that does work.
        if (input.resource_kind or "").strip() or (input.resource_id or "").strip():
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "resource-scoped holds are not enforced yet (#1663): a hold on "
                "one resource cannot currently exclude its rows from a "
                "stream-wide eviction. Place a stream or wildcard hold "
                "instead, which is enforced.",
                field="resourceKind",
            )

        stream = (input.stream or ObservabilityRetentionHold.Stream.ANY).strip()
        valid = {choice for choice, _ in ObservabilityRetentionHold.Stream.choices}
        if stream not in valid:
            # Listed in the message: the wildcard is the one an operator
            # reaching for this under pressure most wants and least expects
            # to be spelled '*'.
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"stream must be one of {sorted(valid)}",
                field="stream",
            )

        tenant = get_current_tenant()
        actor = None
        if tenant is not None and tenant.actor_user_id is not None:
            actor = get_user_model().objects.filter(pk=tenant.actor_user_id).first()

        hold = ObservabilityRetentionHold.objects.create(
            organization_id=org_id,
            stream=stream,
            starts_at=input.starts_at,
            ends_at=input.ends_at,
            resource_kind=(input.resource_kind or "").strip(),
            resource_id=(input.resource_id or "").strip(),
            reason=reason,
            placed_by=actor,
        )
        return gql_success(observability_retention_hold_to_type(hold))

    @strawberry.field
    @mutation_audit(action="observability.retention_hold.release")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def release_observability_retention_hold(
        self,
        info: Info,
        input: ReleaseObservabilityRetentionHoldInput,
    ) -> MutationResultType[ObservabilityRetentionHoldType]:
        """Release a hold, soft-deleting it.

        Soft delete rather than a field flip, matching unmute: the history
        of past holds stays queryable, which is the point when a
        post-incident review asks why a window was or was not kept.
        """
        org_id = _caller_org_id()
        hold = (
            ObservabilityRetentionHold.objects.select_related("organization")
            .filter(
                guid=str(input.hold_id),
                organization_id=org_id,
                deleted_at__isnull=True,
            )
            .first()
        )
        if hold is None:
            # Org-scoped, so a bare guid from another tenant reads as
            # not-found rather than releasing their hold and letting their
            # incident data be evicted.
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "retention hold not found",
                field="holdId",
            )

        tenant = get_current_tenant()
        actor = None
        if tenant is not None and tenant.actor_user_id is not None:
            actor = get_user_model().objects.filter(pk=tenant.actor_user_id).first()

        # `soft_delete()`, not `delete()`. On this base class `delete()` is a
        # real delete -- I called it first and the row vanished from
        # `all_objects` -- which would destroy the record of a hold having
        # been in force, the single thing a post-incident review most wants.
        # `soft_delete` stamps `deleted_at` and bumps `version`, matching
        # unmute.
        hold.soft_delete(by=actor)
        # `released=True` explicitly rather than re-reading: the default
        # manager excludes soft-deleted rows, so `refresh_from_db()` would
        # raise `DoesNotExist` on the row this just released.
        return gql_success(observability_retention_hold_to_type(hold, released=True))
