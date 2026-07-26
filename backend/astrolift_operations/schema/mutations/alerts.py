"""AlertMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization
from astrolift_operations.models import (
    AlertEvent,
    AlertMute,
    AlertRule,
)
from astrolift_operations.schema.mutations.helpers import (
    _caller_org_id,
)
from astrolift_operations.schema.mutations.types import (
    AcknowledgeAlertEventInput,
    CreateAlertRuleInput,
    DeleteAlertRuleInput,
    MuteAlertRuleInput,
    UnmuteAlertRuleInput,
    UpdateAlertRuleInput,
    _AlertRuleDeletedPayload,
)
from astrolift_operations.schema.types import (
    AlertEventType,
    AlertRuleType,
    alert_event_to_type,
    alert_rule_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class AlertMutations:
    # ---- Alert rules + events (#282) ------------------------------

    @strawberry.field
    @mutation_audit(action="alert_rule.create")
    @require_permission(Permission.WEBHOOK_CREATE)
    @tenant_scoped()
    def create_alert_rule(
        self,
        info: Info,
        input: CreateAlertRuleInput,
    ) -> MutationResultType[AlertRuleType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "no active organization",
            )
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "organization not found",
            )
        valid_targets = {t for t, _ in AlertRule.Target.choices}
        if input.target not in valid_targets:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"target must be one of {sorted(valid_targets)}",
                field="target",
            )
        valid_sev = {s for s, _ in AlertRule.Severity.choices}
        severity = (input.severity or "warn").lower()
        if severity not in valid_sev:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"severity must be one of {sorted(valid_sev)}",
                field="severity",
            )
        if input.target == AlertRule.Target.GLOBAL and input.target_id:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "global rules must not carry a target_id",
                field="targetId",
            )
        if AlertRule.objects.filter(
            organization=org,
            name=input.name.strip(),
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"alert rule {input.name!r} already exists",
                field="name",
            )
        managed_service = None
        if input.managed_service_id is not None:
            from astrolift_services.models import ManagedService

            managed_service = ManagedService.objects.filter(
                guid=str(input.managed_service_id),
                deleted_at__isnull=True,
                registered_app__organization=org,
            ).first()
            if managed_service is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    "managed service not found",
                    field="managedServiceId",
                )
        rule = AlertRule.objects.create(
            organization=org,
            name=input.name.strip(),
            target=input.target,
            target_id=input.target_id or "",
            severity=severity,
            predicate=dict(input.predicate or {}),
            notify_channels=list(input.notify_channels or []),
            is_active=(True if input.is_active is None else bool(input.is_active)),
            managed_service=managed_service,
        )
        return gql_success(alert_rule_to_type(rule))

    @strawberry.field
    @mutation_audit(action="alert_rule.update")
    @require_permission(Permission.WEBHOOK_UPDATE)
    @tenant_scoped()
    def update_alert_rule(
        self,
        info: Info,
        input: UpdateAlertRuleInput,
    ) -> MutationResultType[AlertRuleType]:
        # Scope to the caller's org (#1183): a bare guid let any tenant
        # edit another tenant's alert rule (predicate, notify channels,
        # active). org_id None → deny-by-default (not-found).
        rule = AlertRule.objects.filter(
            guid=str(input.id), organization_id=_caller_org_id(), deleted_at__isnull=True
        ).first()
        if rule is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "alert rule not found",
            )
        if input.name is not None:
            rule.name = input.name.strip()
        if input.severity is not None:
            valid_sev = {s for s, _ in AlertRule.Severity.choices}
            if input.severity not in valid_sev:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"severity must be one of {sorted(valid_sev)}",
                    field="severity",
                )
            rule.severity = input.severity
        if input.predicate is not None:
            rule.predicate = dict(input.predicate)
        if input.notify_channels is not None:
            rule.notify_channels = list(input.notify_channels)
        if input.is_active is not None:
            rule.is_active = input.is_active
        if input.managed_service_id is not None:
            from astrolift_services.models import ManagedService

            tenant = get_current_tenant()
            org_id = tenant.organization_id if tenant else None
            service = ManagedService.objects.filter(
                guid=str(input.managed_service_id),
                deleted_at__isnull=True,
                registered_app__organization_id=org_id,
            ).first()
            if service is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    "managed service not found",
                    field="managedServiceId",
                )
            rule.managed_service = service
        rule.save()
        return gql_success(alert_rule_to_type(rule))

    @strawberry.field
    @mutation_audit(action="alert_rule.delete")
    @require_permission(Permission.WEBHOOK_DELETE)
    @tenant_scoped()
    def delete_alert_rule(
        self,
        info: Info,
        input: DeleteAlertRuleInput,
    ) -> MutationResultType[_AlertRuleDeletedPayload]:
        # Scope to the caller's org (#1183): without it any tenant could
        # soft-delete another tenant's alert rule by guid. org_id None →
        # deny-by-default (not-found).
        rule = AlertRule.objects.filter(
            guid=str(input.id), organization_id=_caller_org_id(), deleted_at__isnull=True
        ).first()
        if rule is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "alert rule not found",
            )
        rule.soft_delete()
        return gql_success(
            _AlertRuleDeletedPayload(
                id=input.id,
                deleted=True,
            )
        )

    @strawberry.field
    @mutation_audit(action="alert_event.acknowledge")
    @require_permission(Permission.WEBHOOK_UPDATE)
    @tenant_scoped()
    def acknowledge_alert_event(
        self,
        info: Info,
        input: AcknowledgeAlertEventInput,
    ) -> MutationResultType[AlertEventType]:
        # Scope through the owning rule's org (#1183): without it any
        # tenant could acknowledge another tenant's firing by guid.
        # org_id None → deny-by-default (not-found).
        event = AlertEvent.objects.filter(
            guid=str(input.id), rule__organization_id=_caller_org_id(), deleted_at__isnull=True
        ).first()
        if event is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "alert event not found",
            )
        if event.acknowledged_at is None:
            tenant = get_current_tenant()
            from django.contrib.auth import get_user_model
            from django.utils import timezone

            actor = None
            if tenant is not None and tenant.actor_user_id is not None:
                actor = get_user_model().objects.filter(pk=tenant.actor_user_id).first()
            event.acknowledged_at = timezone.now()
            event.acknowledged_by = actor
            event.save(
                update_fields=[
                    "acknowledged_at",
                    "acknowledged_by",
                    "updated_at",
                    "version",
                ]
            )
        return gql_success(alert_event_to_type(event))

    # ---- Alert mute (#434 scope C) --------------------------------

    @strawberry.field
    @mutation_audit(action="alert_rule.mute")
    @require_permission(Permission.WEBHOOK_UPDATE)
    @tenant_scoped()
    def mute_alert_rule(
        self,
        info: Info,
        input: MuteAlertRuleInput,
    ) -> MutationResultType[AlertRuleType]:
        """Silence an alert rule for the requested duration.

        Returns the rule with its ``activeMute`` field populated so
        the client can update the UI without a refetch. Reason is
        required so the audit log carries a human-readable answer
        to "why was this silenced?"."""
        from datetime import timedelta

        from django.contrib.auth import get_user_model
        from django.utils import timezone

        reason = (input.reason or "").strip()
        if not reason:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "reason is required",
                field="reason",
            )

        duration = int(input.duration_seconds or 0)
        if duration <= 0:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "durationSeconds must be positive",
                field="durationSeconds",
            )
        # Cap at 7 days so a forgotten mute can't silently outlive
        # the team's interest. Operators wanting longer should disable
        # the rule entirely.
        if duration > 7 * 24 * 60 * 60:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "durationSeconds cannot exceed 7 days (604800)",
                field="durationSeconds",
            )

        # Scope to the caller's org (#1183): muting is a cross-tenant
        # denial-of-visibility if a bare guid lets one tenant silence
        # another tenant's rule. org_id None → deny-by-default.
        rule = (
            AlertRule.objects.select_related("organization")
            .filter(guid=str(input.rule_id), organization_id=_caller_org_id(), deleted_at__isnull=True)
            .first()
        )
        if rule is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "alert rule not found",
                field="ruleId",
            )

        tenant = get_current_tenant()
        actor = None
        if tenant is not None and tenant.actor_user_id is not None:
            actor = get_user_model().objects.filter(pk=tenant.actor_user_id).first()

        AlertMute.objects.create(
            rule=rule,
            organization=rule.organization,
            ttl_until=timezone.now() + timedelta(seconds=duration),
            reason=reason,
            muted_by=actor,
        )
        return gql_success(alert_rule_to_type(rule))

    @strawberry.field
    @mutation_audit(action="alert_rule.unmute")
    @require_permission(Permission.WEBHOOK_UPDATE)
    @tenant_scoped()
    def unmute_alert_rule(
        self,
        info: Info,
        input: UnmuteAlertRuleInput,
    ) -> MutationResultType[AlertRuleType]:
        """Immediate unmute — soft-deletes every active mute on the
        rule so the next firing fans out to channels.

        Idempotent: returns ok=true even when no active mute exists.
        Mute history rows stay around for the audit log; only the
        *active* mutes are cleared."""
        from django.utils import timezone

        # Scope to the caller's org (#1183): unmuting another tenant's
        # rule would re-arm their alert fan-out. org_id None →
        # deny-by-default (not-found).
        rule = (
            AlertRule.objects.select_related("organization")
            .filter(guid=str(input.rule_id), organization_id=_caller_org_id(), deleted_at__isnull=True)
            .first()
        )
        if rule is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "alert rule not found",
                field="ruleId",
            )
        active_mutes = AlertMute.objects.filter(
            rule=rule,
            deleted_at__isnull=True,
            ttl_until__gt=timezone.now(),
        )
        for mute in active_mutes:
            # ``soft_delete`` stamps deleted_at + bumps version; the
            # delivery worker's ``is_rule_muted`` filters those out.
            mute.soft_delete()
        return gql_success(alert_rule_to_type(rule))
