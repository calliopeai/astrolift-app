from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import strawberry
from asgiref.sync import sync_to_async
from strawberry.types import Info

from astrolift_operations.notification_inbox import capture_identity, notification_page
from astrolift_operations.schema.types import NotificationType
from core.decorators import tenant_scoped
from core.schema.ws_auth import ws_identity

POLL_SECONDS = 2


@strawberry.type(name="AstroliftNotificationPage")
class NotificationPageType:
    items: list[NotificationType]
    end_cursor: str
    has_more: bool


@strawberry.type
class NotificationInboxQuery:
    @strawberry.field
    @tenant_scoped()
    def astrolift_my_notifications_page(
        self,
        info: Info,
        after: str | None = None,
        limit: int = 50,
    ) -> NotificationPageType:
        return notification_page(capture_identity(info), after=after, limit=limit)


@strawberry.type
class NotificationInboxSubscription:
    @strawberry.subscription
    @ws_identity
    async def astrolift_my_notification_stream(
        self,
        info: Info,
        after: str | None = None,
    ) -> AsyncGenerator[NotificationPageType, None]:
        identity = capture_identity(info)
        cursor = after
        while True:
            page = await sync_to_async(notification_page)(identity, after=cursor, limit=100)
            if page.end_cursor != cursor:
                cursor = page.end_cursor
                yield page
            if not page.has_more:
                await asyncio.sleep(POLL_SECONDS)
