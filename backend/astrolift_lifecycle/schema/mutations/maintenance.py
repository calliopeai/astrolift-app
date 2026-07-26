"""MaintenanceMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_lifecycle.schema.mutations.types import (
    ReapCloudOrphanInput,
    ReapCloudOrphanPayload,
)
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission


@strawberry.type
class MaintenanceMutations:
    @strawberry.field
    @mutation_audit(
        action="orphan.reap",
        target=lambda root, info, input: ("orphan", input.reap_key),
    )
    @require_permission(Permission.CLUSTER_MANAGE)
    def reap_cloud_orphan(
        self, info: Info, input: ReapCloudOrphanInput
    ) -> MutationResultType[ReapCloudOrphanPayload]:
        """Reap one detected cloud orphan THROUGH the provider drivers (#995).

        Install-wide operator action (orphans have no org owner) — gated on
        ``CLUSTER_MANAGE`` and deliberately NOT ``@tenant_scoped``. The reaper
        re-checks ownership and refuses anything that still has a live owner;
        only detection-proven orphans are reapable. Idempotent: an already-gone
        resource returns success so a re-run converges. ``force_destroy``
        carries through to the driver for deletion-protected resources.

        Reaping never issues a raw cloud API delete — it reuses the same
        idempotent driver deprovision path teardown uses (#1034).
        """
        from astrolift_operations.services.orphan_reaper import ALL_KINDS, reap_orphan

        kind = (input.kind or "").strip()
        reap_key = (input.reap_key or "").strip()
        if kind not in ALL_KINDS:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown orphan kind {kind!r}",
                field="kind",
            )
        if not reap_key:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "reap_key is required",
                field="reapKey",
            )

        result = reap_orphan(
            kind=kind,
            reap_key=reap_key,
            cluster_slug=(input.cluster_slug or "").strip(),
            force_destroy=bool(input.force_destroy),
        )
        if not result.ok:
            # A refusal (live owner) is a precondition failure, not a crash.
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.message,
                field="reapKey",
            )
        return gql_success(
            ReapCloudOrphanPayload(
                kind=result.kind,
                identifier=result.identifier,
                reaped=not result.already_gone,
                already_gone=result.already_gone,
                message=result.message,
            )
        )
