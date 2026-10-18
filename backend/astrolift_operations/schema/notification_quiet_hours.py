"""Authenticated self-service settings, alongside NotificationPreference (#2434)."""

import strawberry
from django.contrib.auth import get_user_model
from django.db import transaction
from graphql import GraphQLError
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_operations.models.notification_quiet_hours import NotificationQuietHours
from astrolift_operations.notification_quiet_hours import active_at, for_user, validate_window
from core.mutations import ErrorCode, mutation_audit


@strawberry.type(name="AstroliftNotificationQuietHours")
class NotificationQuietHoursType:
    """Personal daily [start,end) window for agent-message push/email. No deferral or digest; inbox and organization webhooks are unaffected."""

    enabled: bool
    timezone: str
    start_minute: int
    end_minute: int
    active: bool


@strawberry.input
class SetNotificationQuietHoursInput:
    enabled: bool
    timezone: str
    start_minute: int
    end_minute: int


def to_type(row: NotificationQuietHours) -> NotificationQuietHoursType:
    return NotificationQuietHoursType(
        enabled=row.enabled,
        timezone=row.timezone,
        start_minute=row.start_minute,
        end_minute=row.end_minute,
        active=active_at(row),
    )


def _viewer(info):
    request = getattr(info.context, "request", None)
    viewer = getattr(request, "user", None) if request else None
    return viewer if viewer is not None and viewer.is_authenticated and viewer.is_active else None


@strawberry.type
class NotificationQuietHoursQuery:
    @strawberry.field
    def astrolift_my_notification_quiet_hours(self, info: Info) -> NotificationQuietHoursType:
        viewer = _viewer(info)
        if viewer is None:
            raise GraphQLError("not authenticated", extensions={"code": "UNAUTHENTICATED"})
        return to_type(for_user(viewer.pk))


@strawberry.type
class NotificationQuietHoursMutation:
    @strawberry.field
    @mutation_audit(action="notification_quiet_hours.set")
    def set_notification_quiet_hours(
        self, info: Info, input: SetNotificationQuietHoursInput
    ) -> MutationResultType[NotificationQuietHoursType]:
        viewer = _viewer(info)
        if viewer is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")
        timezone_name = input.timezone.strip()
        error = validate_window(
            timezone_name=timezone_name, start_minute=input.start_minute, end_minute=input.end_minute
        )
        if error is not None:
            field, message = error
            return gql_failure(ErrorCode.VALIDATION.value, message, field=field)
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=viewer.pk)
            row, _ = NotificationQuietHours.objects.update_or_create(
                user=viewer,
                defaults={
                    "enabled": input.enabled,
                    "timezone": timezone_name,
                    "start_minute": input.start_minute,
                    "end_minute": input.end_minute,
                },
            )
        return gql_success(to_type(row))
