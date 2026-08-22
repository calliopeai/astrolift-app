"""NotificationMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization
from astrolift_operations.models import (
    Notification,
)
from astrolift_operations.schema.mutations.types import (
    MarkNotificationReadInput,
    RegisterMobileDeviceInput,
    RevokeMobileDeviceInput,
    SetNotificationPreferenceInput,
    SetNotificationProfileInput,
    TestNotificationInput,
    _MarkAllReadPayload,
    _RevokeMobileDevicePayload,
)
from astrolift_operations.schema.types import (
    DeviceRegistrationType,
    NotificationPreferenceType,
    NotificationProfileType,
    NotificationType,
    notification_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class NotificationMutations:
    @strawberry.field
    @mutation_audit(action="notification.test")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def test_notification_channel(
        self, info: Info, input: TestNotificationInput
    ) -> MutationResultType[NotificationType]:
        """Create a SYSTEM-kind notification in the caller's inbox
        scoped to the given org.

        The 'channel' here is the in-app inbox — astrolift doesn't
        model standalone NotificationChannel rows; subscribers
        select channels per-AlertRule via ``notify_channels``. The
        test mutation surfaces a row the operator can see
        immediately in their notifications drawer so they know the
        fan-out path is wired."""
        from django.utils import timezone

        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "not authenticated",
            )

        org = Organization.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found", field="id")
        if tenant.organization_id is not None and tenant.organization_id != org.id:
            # Acting tenant must match the target org: operators can't
            # send themselves a test notification scoped to a different
            # tenant than the one they're currently in.
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "active tenant does not match target organization",
                field="id",
            )

        body = (input.message or "").strip() or (
            "This is a test notification from the Astrolift control plane. "
            "If you can see this in your inbox, the notification channel is wired correctly."
        )

        now = timezone.now()
        notif = Notification.objects.create(
            user_id=tenant.actor_user_id,
            organization=org,
            kind=Notification.Kind.SYSTEM,
            title="Test notification",
            body=body,
            link="",
        )
        # Stamp updated_at so it shows up at the top of the inbox.
        Notification.objects.filter(pk=notif.pk).update(updated_at=now)
        notif.refresh_from_db()
        return gql_success(notification_to_type(notif))

    @strawberry.field
    def mark_notification_read(
        self, info: Info, input: MarkNotificationReadInput
    ) -> MutationResultType[NotificationType]:
        from django.utils import timezone

        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")
        notif = Notification.objects.filter(guid=str(input.id), user_id=tenant.actor_user_id).first()
        if notif is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "notification not found")
        notif.read_at = timezone.now()
        notif.save(update_fields=["read_at", "updated_at", "version"])
        return gql_success(notification_to_type(notif))

    @strawberry.field
    def mark_all_notifications_read(self, info: Info) -> MutationResultType[_MarkAllReadPayload]:
        from django.utils import timezone

        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")
        marked = Notification.objects.filter(user_id=tenant.actor_user_id, read_at__isnull=True).update(
            read_at=timezone.now()
        )
        return gql_success(_MarkAllReadPayload(marked=marked))

    # ---- Push device registration (#476 §B) -----------------------

    @strawberry.field
    @mutation_audit(action="mobile_device.register")
    def register_mobile_device(
        self, info: Info, input: RegisterMobileDeviceInput
    ) -> MutationResultType[DeviceRegistrationType]:
        """Register a push-receivable device for the current user.

        Self-scoped — any authenticated user can register a device
        on their own account; no extra permission. The token
        uniqueness constraint covers re-registration: re-presenting
        a token resurrects the existing row (clears soft-delete +
        stale state) rather than inserting a duplicate.

        ``label`` is optional; surfaced in /settings/devices verbatim.
        Tokens up to 512 chars (FCM / APNs / web-push all fit).
        """
        from astrolift_operations.models import DeviceRegistration
        from astrolift_operations.notification_dispatch import (
            default_driver_slug_for_registration,
        )
        from astrolift_operations.schema.types import device_registration_to_type

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        platform = (input.platform or "").strip().lower()
        valid_platforms = {c for c, _ in DeviceRegistration.Platform.choices}
        if platform not in valid_platforms:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"platform must be one of {sorted(valid_platforms)}",
                field="platform",
            )
        token = (input.device_token or "").strip()
        if not token:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "device_token is required",
                field="deviceToken",
            )
        if len(token) > 512:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "device_token exceeds 512 characters",
                field="deviceToken",
            )
        label = (input.label or "").strip()[:200]

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None

        # Bind the registration to the caller's active AstroliftSession
        # row when one exists — the #499 dispatcher uses this to skip
        # echoing back to the device that just signed in.
        session_key = getattr(getattr(request, "session", None), "session_key", None) if request else None
        enrolled_session_id: int | None = None
        if session_key:
            from astrolift_identity.models import AstroliftSession

            row = (
                AstroliftSession.objects.filter(user_id=viewer.pk, session_key=session_key).only("pk").first()
            )
            if row is not None:
                enrolled_session_id = row.pk

        driver_slug = default_driver_slug_for_registration(organization_id=org_id)

        existing = (
            DeviceRegistration.all_objects.filter(user_id=viewer.pk, device_token=token)
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            updates: list[str] = []
            if existing.platform != platform:
                existing.platform = platform
                updates.append("platform")
            if label and existing.label != label:
                existing.label = label
                updates.append("label")
            if existing.driver != driver_slug:
                existing.driver = driver_slug
                updates.append("driver")
            if existing.organization_id != org_id:
                existing.organization_id = org_id
                updates.append("organization")
            if enrolled_session_id and existing.enrolled_session_id != enrolled_session_id:
                existing.enrolled_session_id = enrolled_session_id
                updates.append("enrolled_session")
            # Re-issued after a soft-delete / stale prune — clear both
            # so the row counts as live again.
            if existing.stale_at is not None or existing.deleted_at is not None:
                existing.stale_at = None
                existing.deleted_at = None
                updates += ["stale_at", "deleted_at"]
            if updates:
                existing.save(update_fields=updates + ["updated_at", "version"])
            return gql_success(device_registration_to_type(existing))

        row = DeviceRegistration.objects.create(
            user=viewer,
            device_token=token,
            platform=platform,
            label=label,
            organization_id=org_id,
            enrolled_session_id=enrolled_session_id,
            driver=driver_slug,
        )
        return gql_success(device_registration_to_type(row))

    @strawberry.field
    @mutation_audit(
        action="mobile_device.revoke",
        target=lambda self, info, input: ("device_registration", str(input.id)),
    )
    def revoke_mobile_device(
        self, info: Info, input: RevokeMobileDeviceInput
    ) -> MutationResultType[_RevokeMobileDevicePayload]:
        """Soft-delete one of the caller's push registrations.

        Self-only. Idempotent: revoking an already-revoked row
        returns ``ok: true`` with ``revoked=False``.
        """
        from astrolift_operations.models import DeviceRegistration

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        row = DeviceRegistration.all_objects.filter(guid=str(input.id)).first()
        if row is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "device not found")
        if row.user_id != viewer.pk:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "cannot revoke this device")

        already = row.deleted_at is not None
        if not already:
            row.soft_delete(by=viewer)
        return gql_success(_RevokeMobileDevicePayload(id=input.id, revoked=not already))

    # ---- Notification preferences (#476 §F, #499 §E) --------------

    @strawberry.field
    @mutation_audit(action="notification_preference.set")
    def set_notification_preference(
        self, info: Info, input: SetNotificationPreferenceInput
    ) -> MutationResultType[NotificationPreferenceType]:
        """Upsert one preference row for the caller.

        Self-only. ``enabled=True`` and ``enabled=False`` both create
        an explicit row that overrides the platform default. To
        revert to the default, the caller deletes the row via
        ``revokeNotificationPreference`` (no dedicated mutation
        today — the user just toggles back to the default value).
        """
        from astrolift_operations.models import (
            NotificationChannel,
            NotificationPreference,
        )
        from astrolift_operations.schema.types import notification_preference_to_type

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        channel = (input.channel or "").strip().lower()
        valid_channels = {c for c, _ in NotificationChannel.choices}
        if channel not in valid_channels:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"channel must be one of {sorted(valid_channels)}",
                field="channel",
            )
        event_kind = (input.event_kind or "").strip()
        if not event_kind:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "event_kind is required",
                field="eventKind",
            )
        if len(event_kind) > 64:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "event_kind exceeds 64 characters",
                field="eventKind",
            )

        row = NotificationPreference.objects.filter(
            user_id=viewer.pk,
            channel=channel,
            event_kind=event_kind,
        ).first()
        if row is None:
            row = NotificationPreference.objects.create(
                user=viewer,
                channel=channel,
                event_kind=event_kind,
                enabled=bool(input.enabled),
            )
        else:
            row.enabled = bool(input.enabled)
            row.save(update_fields=["enabled", "updated_at", "version"])
        return gql_success(notification_preference_to_type(row))

    # ---- Notification profile (#490) ------------------------------

    @strawberry.field
    @mutation_audit(action="notification_profile.set")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def set_notification_profile(
        self, info: Info, input: SetNotificationProfileInput
    ) -> MutationResultType[NotificationProfileType]:
        """Install (or replace) the active notification profile for the
        caller's org, shape-checked by the policy module first.

        Validation belongs here because the send path cannot report
        it: ``resolve_driver_for_recipient`` swallows a driver build
        failure and the dispatcher audits ``status=no_driver``, so a
        misspelled driver or a config missing a required key persists
        fine and then silently stops every alert with nothing telling
        the operator why.

        Upsert, not append: one active profile per org is a DB
        constraint, so a re-submit rewrites the live row rather than
        inserting a second one. Rewriting in place also keeps the
        config blob, which can carry plaintext credentials, out of
        dead rows; the trail of who changed what comes from
        ``mutation_audit``.
        """
        from astrolift_operations.models import NotificationProfile
        from astrolift_operations.notification_profile import (
            NotificationProfileError,
            NotificationProfileSpec,
            RetentionConfig,
            validate_profile,
        )
        from astrolift_operations.schema.types import notification_profile_to_type

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        config = input.config
        if not isinstance(config, dict):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "config must be an object",
                field="config",
            )

        # Retention and driver shape fail on different inputs, so they
        # get separate try blocks to attribute the error to the field
        # the operator actually has to fix.
        try:
            retention = (
                RetentionConfig(delivery_days=input.retention_delivery_days)
                if input.retention_delivery_days is not None
                else RetentionConfig()
            )
        except NotificationProfileError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                str(exc),
                field="retentionDeliveryDays",
            )

        driver = (input.driver or "").strip()
        try:
            validate_profile(
                profile=NotificationProfileSpec(
                    driver=driver,
                    config=config,
                    retention=retention,
                ),
            )
        except NotificationProfileError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc), field="config")

        existing = (
            NotificationProfile.objects.filter(organization_id=org_id, is_active=True)
            .order_by("-updated_at")
            .first()
        )
        if existing is not None:
            existing.driver = driver
            existing.config = config
            existing.retention_delivery_days = retention.delivery_days
            existing.save(
                update_fields=[
                    "driver",
                    "config",
                    "retention_delivery_days",
                    "updated_at",
                    "version",
                ]
            )
            return gql_success(notification_profile_to_type(existing))

        row = NotificationProfile.objects.create(
            organization_id=org_id,
            driver=driver,
            config=config,
            retention_delivery_days=retention.delivery_days,
        )
        return gql_success(notification_profile_to_type(row))
