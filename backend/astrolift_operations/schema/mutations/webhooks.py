"""WebhookMutations — split from the monolithic mutations module."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization, Team
from astrolift_operations.models import (
    WebhookSubscription,
)
from astrolift_operations.schema.mutations.helpers import (
    _caller_org_id,
    _deliver_test_webhook,
    log,
)
from astrolift_operations.schema.mutations.types import (
    CreateWebhookSubscriptionInput,
    DeleteWebhookSubscriptionInput,
    RotateOutboundWebhookSecretInput,
    TestWebhookInput,
    UpdateWebhookSubscriptionInput,
    WebhookSecretReveal,
    _SoftDeletePayload,
)
from astrolift_operations.schema.types import (
    WebhookSubscriptionType,
    WebhookTestResultType,
    webhook_to_type,
)
from astrolift_operations.scopes import apps, webhook_creation_scope, webhook_scope
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.optimistic import check_version_match as _check_version_match
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class WebhookMutations:
    @strawberry.field
    @mutation_audit(action="webhook.create")
    @require_permission(Permission.WEBHOOK_CREATE, scope=webhook_creation_scope(Permission.WEBHOOK_CREATE))
    @tenant_scoped()
    def create_webhook_subscription(
        self, info: Info, input: CreateWebhookSubscriptionInput
    ) -> MutationResultType[WebhookSecretReveal]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        team = None
        if input.team_slug:
            team = Team.objects.filter(
                organization=org, slug=input.team_slug, deleted_at__isnull=True
            ).first()
            if team is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "team not found", field="teamSlug")

        registered_app = None
        if input.app_slug:
            registered_app = apps().filter(organization=org, slug=input.app_slug).first()
            if registered_app is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    f"app {input.app_slug!r} not found",
                    field="appSlug",
                )

        plaintext_secret = "alfthk_" + secrets.token_urlsafe(24)
        digest = hashlib.sha256(plaintext_secret.encode()).hexdigest()

        fmt = (input.format or WebhookSubscription.Format.GENERIC).lower()
        valid_formats = {c for c, _ in WebhookSubscription.Format.choices}
        if fmt not in valid_formats:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"format must be one of {sorted(valid_formats)}",
                field="format",
            )

        sub = WebhookSubscription.objects.create(
            organization=org,
            team=team,
            registered_app=registered_app,
            url=input.url.strip(),
            secret_hash=digest,
            events=list(input.events or []),
            is_active=True,
            format=fmt,
        )
        return gql_success(
            WebhookSecretReveal(
                subscription=webhook_to_type(sub),
                plaintext_secret=plaintext_secret,
            )
        )

    @strawberry.field
    @mutation_audit(action="webhook.update")
    @require_permission(Permission.WEBHOOK_UPDATE, scope=webhook_scope(Permission.WEBHOOK_UPDATE))
    @tenant_scoped()
    def update_webhook_subscription(
        self, info: Info, input: UpdateWebhookSubscriptionInput
    ) -> MutationResultType[WebhookSubscriptionType]:
        # Scope to the caller's org (#1183): a bare guid lookup let any
        # tenant edit another tenant's webhook (retarget the URL, flip
        # active). org_id None → deny-by-default (not-found).
        sub = WebhookSubscription.objects.filter(guid=str(input.id), organization_id=_caller_org_id()).first()
        if sub is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "subscription not found")
        # #497 — optimistic-concurrency gate.
        mismatch = _check_version_match(
            sub, if_match_version=input.if_match_version, kind="WebhookSubscription"
        )
        if mismatch is not None:
            return mismatch
        if input.url is not None:
            sub.url = input.url
        if input.events is not None:
            sub.events = list(input.events)
        if input.is_active is not None:
            sub.is_active = input.is_active
            # Operator re-enable also clears the auto-disable bookkeeping
            # so the next failure doesn't immediately re-trip the
            # threshold from a stale counter. Manual disable leaves the
            # counter alone — operators see history when they look.
            if input.is_active:
                sub.disabled_at = None
                sub.disabled_reason = ""
                sub.failure_count = 0
            else:
                if not sub.disabled_at:
                    from django.utils import timezone

                    sub.disabled_at = timezone.now()
                    sub.disabled_reason = "operator-disabled"
        if input.format is not None:
            fmt = input.format.lower()
            valid_formats = {c for c, _ in WebhookSubscription.Format.choices}
            if fmt not in valid_formats:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"format must be one of {sorted(valid_formats)}",
                    field="format",
                )
            sub.format = fmt
        sub.save()
        return gql_success(webhook_to_type(sub))

    @strawberry.field
    @mutation_audit(action="webhook.delete")
    @require_permission(Permission.WEBHOOK_DELETE, scope=webhook_scope(Permission.WEBHOOK_DELETE))
    @tenant_scoped()
    def delete_webhook_subscription(
        self, info: Info, input: DeleteWebhookSubscriptionInput
    ) -> MutationResultType[_SoftDeletePayload]:
        # Scope to the caller's org (#1183): without it any tenant could
        # soft-delete another tenant's webhook by guid. org_id None →
        # deny-by-default (not-found).
        sub = WebhookSubscription.objects.filter(guid=str(input.id), organization_id=_caller_org_id()).first()
        if sub is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "subscription not found")
        sub.soft_delete()
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    @strawberry.field
    @mutation_audit(action="webhook.test")
    @require_permission(Permission.WEBHOOK_UPDATE, scope=webhook_scope(Permission.WEBHOOK_UPDATE))
    @tenant_scoped()
    def test_webhook_subscription(
        self, info: Info, input: TestWebhookInput
    ) -> MutationResultType[WebhookTestResultType]:
        """Synthesize a ``webhook.test`` event, sign it with the
        subscription's secret derivation key, and POST it
        synchronously so the operator gets an immediate
        verdict (status + latency + body excerpt).

        Distinct from the async DeliverWebhookWorkflow: no retries,
        no auto-disable bookkeeping — this is a one-shot probe, not
        traffic. The subscription's ``failure_count`` is left alone.
        """
        from datetime import datetime

        # Scope to the caller's org (#1183): a test-fire POSTs a signed
        # payload to the subscription's URL, so a cross-org guid would
        # let a tenant probe another tenant's endpoint. org_id None →
        # deny-by-default (not-found).
        sub = WebhookSubscription.objects.filter(
            guid=str(input.id), organization_id=_caller_org_id(), deleted_at__isnull=True
        ).first()
        if sub is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "subscription not found", field="id")
        if not sub.is_active:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "subscription is disabled; re-enable before testing",
                field="id",
            )
        if not sub.url:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "subscription has no url configured",
                field="id",
            )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        payload = {
            "kind": "webhook.test",
            "subscription_id": str(sub.guid),
            "organization_id": org_id,
            "delivered_at": datetime.now(tz=UTC).isoformat(),
            "message": "test delivery from the Astrolift control plane",
        }

        outcome = _deliver_test_webhook(
            url=sub.url,
            secret=(sub.secret_hash or "").encode("utf-8"),
            payload=payload,
            event_type="webhook.test",
            format=sub.format or "generic",
        )

        # Persist a WebhookDelivery row so the test-fire shows up in
        # the operator UI's history table. Test rows carry is_test=True
        # so health widgets can exclude probe traffic. We bypass
        # record_delivery_outcome on purpose — tests must not touch
        # the subscription's failure_count or auto-disable bookkeeping.
        try:
            from astrolift_operations.models import WebhookDelivery

            WebhookDelivery.objects.create(
                subscription=sub,
                event_type="webhook.test",
                retry_attempt=1,
                status_code=outcome["status_code"],
                latency_ms=int(outcome["duration_ms"] or 0),
                success=bool(
                    outcome["delivered"] and outcome["status_code"] and 200 <= outcome["status_code"] < 300
                ),
                is_test=True,
                request_payload_excerpt=json.dumps(payload, separators=(",", ":"))[:8192],
                response_body_excerpt=(outcome["response_body_excerpt"] or "")[:8192],
                error=(outcome["error"] or "")[:512],
                delivery_id=(outcome["delivery_id"] or "")[:64],
                delivered_at=datetime.fromtimestamp(outcome["timestamp_unix"], tz=UTC),
            )
        except Exception:
            log.exception(
                "failed to persist test webhook delivery row",
                extra={"subscription_id": str(sub.guid)},
            )

        return gql_success(
            WebhookTestResultType(
                subscription_id=input.id,
                url=sub.url,
                delivered=outcome["delivered"],
                status_code=outcome["status_code"],
                duration_ms=outcome["duration_ms"],
                response_body_excerpt=outcome["response_body_excerpt"],
                error=outcome["error"],
                delivery_id=outcome["delivery_id"],
                timestamp=datetime.fromtimestamp(outcome["timestamp_unix"], tz=UTC),
            )
        )

    @strawberry.field
    @mutation_audit(action="webhook.rotate_secret")
    @require_permission(Permission.WEBHOOK_UPDATE, scope=webhook_scope(Permission.WEBHOOK_UPDATE))
    @tenant_scoped()
    def rotate_outbound_webhook_secret(
        self, info: Info, input: RotateOutboundWebhookSecretInput
    ) -> MutationResultType[WebhookSecretReveal]:
        """Rotate the HMAC secret. Returns the new plaintext exactly
        once; the previous hash stays valid for the Constance grace
        window so subscribers can roll out without dropping
        deliveries.

        Audit-logged via ``mutation_audit`` so the rotation appears
        in the audit log; ``mutation_audit`` records the action +
        actor + target without leaking the plaintext.
        """
        from django.utils import timezone

        from astrolift_operations.webhook_rotation import (
            grace_seconds_from_constance,
            plan_rotation,
        )

        # Scope to the caller's org (#1183): this returns the new HMAC
        # secret plaintext once. A cross-org guid would hand a tenant
        # another tenant's fresh signing secret. org_id None →
        # deny-by-default (not-found).
        sub = WebhookSubscription.objects.filter(
            guid=str(input.id), organization_id=_caller_org_id(), deleted_at__isnull=True
        ).first()
        if sub is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "subscription not found",
                field="id",
            )

        grace_seconds = grace_seconds_from_constance()
        plan = plan_rotation(
            current_secret_hash=sub.secret_hash or "",
            now=timezone.now(),
            grace_seconds=grace_seconds,
        )

        sub.secret_hash_previous = plan.previous_secret_hash
        sub.secret_hash = plan.new_secret_hash
        sub.secret_rotated_at = plan.rotated_at
        sub.save(
            update_fields=[
                "secret_hash",
                "secret_hash_previous",
                "secret_rotated_at",
                "updated_at",
                "version",
            ]
        )

        return gql_success(
            WebhookSecretReveal(
                subscription=webhook_to_type(sub),
                plaintext_secret=plan.plaintext_secret,
            )
        )
