import logging

from config import settings
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)


class CoreSystem:

    @classmethod
    def user_system(cls):
        return get_user_model().objects.get(username=settings.API_SYSTEM_USER)

    @classmethod
    def notify(cls, created_by, user, message, *, subject: str = "Notification"):
        """Persist an in-app notification for ``user``.

        #538 — the prior body called
        ``Notification.objects.get_or_create(created_by=created_by)``
        which created at most ONE row per creator (dedup'd across all
        recipients + messages) and stored neither the recipient nor
        the message body. Callers thought they were sending a
        notification; nothing useful was persisted.

        Args:
            created_by: actor who triggered the notification (None
                allowed for system-fired events).
            user: recipient. Required — a notification with no target
                user can't reach any inbox surface.
            message: body text. Required for the same reason.
            subject: display title; defaults to a generic "Notification"
                so existing callers keep working without arg changes.

        Returns the persisted ``Notification`` row (replaces the
        legacy ``None`` return so callers can chain). Raises
        ``ValueError`` on missing ``user`` or empty ``message`` —
        fail-loud beats silently dropping the call.

        Out of scope (separate ticket #699): dispatching through a
        delivery channel (websocket fan-out, email digest, etc.). The
        Notification model is consumed by the in-app inbox surface
        which queries ``Notification.objects.filter(user=...)``, so
        persistence-only is sufficient for the inbox view today.
        """
        from core.models import Notification

        if user is None:
            raise ValueError("CoreSystem.notify requires a recipient user")
        if not message:
            raise ValueError("CoreSystem.notify requires a non-empty message")

        return Notification.objects.create(
            user=user,
            subject=subject,
            message=message,
            created_by=created_by,
        )
