"""Quiet windows suppress personal push/email; inbox and org events remain visible."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from astrolift_operations.models.notification_quiet_hours import NotificationQuietHours


def active_at(preference: NotificationQuietHours, *, now=None) -> bool:
    if not preference.enabled:
        return False
    try:
        local = (now or timezone.now()).astimezone(ZoneInfo(preference.timezone))
    except (ZoneInfoNotFoundError, ValueError):
        return False
    minute = local.hour * 60 + local.minute
    start, end = preference.start_minute, preference.end_minute
    return start <= minute < end if start < end else minute >= start or minute < end


def for_user(user_id: int) -> NotificationQuietHours:
    return NotificationQuietHours.objects.filter(user_id=user_id).first() or NotificationQuietHours(
        user_id=user_id
    )


def active_users(user_ids, *, now=None) -> set[int]:
    now = now or timezone.now()
    return {
        row.user_id
        for row in NotificationQuietHours.objects.filter(user_id__in=user_ids, enabled=True)
        if active_at(row, now=now)
    }


def validate_window(*, timezone_name: str, start_minute: int, end_minute: int) -> tuple[str, str] | None:
    try:
        if len(timezone_name) > 64:
            raise ValueError
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return "timezone", "timezone must be an IANA time zone"
    for field, minute in (("startMinute", start_minute), ("endMinute", end_minute)):
        if type(minute) is not int or not 0 <= minute < 1440:
            return field, "time must be a minute of the day from 0 to 1439"
    if start_minute == end_minute:
        return "endMinute", "quiet hours must have distinct start and end times"
    return None
