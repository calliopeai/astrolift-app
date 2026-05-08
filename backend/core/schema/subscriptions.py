"""Strawberry GraphQL subscriptions.

Real-time updates via WebSocket. Strawberry's AsyncGenerator pattern
yields events into the WS frame stream; the broker
(``core.pubsub``) is the fan-out layer.

Connect to ``ws://<host>/app/gql/config/ws/`` with the graphql-ws
sub-protocol. Phase-1 transport uses Strawberry's built-in WS
handler when ASGI is wired (config/asgi.py); phase-2 layers
client-side reconnect + auth on the handshake.
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Optional

import strawberry
from strawberry.types import Info


@strawberry.type(name="AstroliftDeploymentLifecycleEvent")
class DeploymentLifecycleEventType:
    """One push from the deployment lifecycle stream.

    The shape mirrors the broker payload — ``deployment_id`` is the
    GUID of the row that transitioned, ``status`` is the new value,
    plus enough context for the UI to update the row in place
    without an extra query."""

    deployment_id: str
    registered_app_slug: str
    environment_name: str
    status: str
    occurred_at: str


@strawberry.type
class Subscription:

    @strawberry.subscription
    async def astrolift_deployment_lifecycle_stream(
        self, info: Info, app_slug: Optional[str] = None
    ) -> AsyncGenerator[DeploymentLifecycleEventType, None]:
        """Push every Deployment status transition for the current
        org (or for a single app when ``app_slug`` is given).

        The resolver picks the right broker topic:
          - org-wide: ``deployment.lifecycle.<org_id>``
          - per-app:  ``deployment.lifecycle.app.<app_guid>``

        On client disconnect the broker subscriber is cleaned up
        in ``core.pubsub.subscribe``'s ``finally`` block.
        """
        from astrolift_registry.models import RegisteredApp
        from core.pubsub import subscribe
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return

        topic = f"deployment.lifecycle.{org_id}"
        if app_slug:
            app = (
                RegisteredApp.objects.filter(
                    organization_id=org_id, slug=app_slug
                )
                .only("guid")
                .first()
            )
            if app is None:
                return
            topic = f"deployment.lifecycle.app.{app.guid}"

        async for event in subscribe(topic):
            yield DeploymentLifecycleEventType(
                deployment_id=event.get("deployment_id", ""),
                registered_app_slug=event.get("registered_app_slug", ""),
                environment_name=event.get("environment_name", ""),
                status=event.get("status", ""),
                occurred_at=event.get("occurred_at", ""),
            )

    @strawberry.subscription
    async def notification_received(self, info: Info) -> AsyncGenerator[str, None]:
        """Subscribe to new notifications for the current user.

        Yields notification subjects as they arrive. Still polling-based
        until the notification model gets a publish hook; tracked under
        the same broker swap as deployment.lifecycle.
        """
        from core.models import Notification
        last_id = None

        # Get initial last notification ID
        latest = Notification.objects.filter(
            user=info.context.user
        ).order_by('-created_at').first()
        if latest:
            last_id = latest.pk

        while True:
            await asyncio.sleep(2)  # Poll every 2 seconds
            try:
                qs = Notification.objects.filter(user=info.context.user).order_by('-created_at')
                if last_id:
                    qs = qs.filter(pk__gt=last_id)
                for notif in qs[:10]:
                    yield notif.subject
                    last_id = max(last_id or 0, notif.pk)
            except Exception:
                pass

    @strawberry.subscription
    async def form_submission_received(self, info: Info, slug: str) -> AsyncGenerator[str, None]:
        """Subscribe to new submissions for a specific form.

        Yields submission IDs as they arrive.
        """
        from forms.models import FormSubmission
        last_id = None

        latest = FormSubmission.objects.filter(
            form__slug=slug
        ).order_by('-submitted_at').first()
        if latest:
            last_id = latest.pk

        while True:
            await asyncio.sleep(2)
            try:
                qs = FormSubmission.objects.filter(form__slug=slug).order_by('-submitted_at')
                if last_id:
                    qs = qs.filter(pk__gt=last_id)
                for sub in qs[:10]:
                    yield f'New submission #{sub.pk}'
                    last_id = max(last_id or 0, sub.pk) if isinstance(sub.pk, int) else sub.pk
            except Exception:
                pass
