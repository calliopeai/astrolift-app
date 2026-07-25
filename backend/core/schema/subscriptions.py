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
from collections.abc import AsyncGenerator

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
class _CoreSubscription:
    """Subscriptions native to ``core``. Merged with per-app
    subscription types into the schema-level :class:`Subscription`
    below so each domain owns its own surface without one root
    file becoming a dumping ground."""

    @strawberry.subscription
    async def astrolift_deployment_lifecycle_stream(
        self, info: Info, app_slug: str | None = None
    ) -> AsyncGenerator[DeploymentLifecycleEventType, None]:
        """Push every Deployment status transition for the current
        org (or for a single app when ``app_slug`` is given).

        The resolver picks the right broker topic:
          - org-wide: ``deployment.lifecycle.<org_id>``
          - per-app:  ``deployment.lifecycle.app.<app_guid>``

        Tenant resolution differs between transports:
          - HTTP: ``TenantContextMiddleware`` set the contextvar
            before the resolver runs.
          - WS: the cookie-aware ASGI handler attached the resolved
            tenant to ``info.context._ws_tenant``; we set the same
            contextvar here so ``@tenant_scoped`` and the broker
            topic key both pick up the right org.
        """
        from asgiref.sync import sync_to_async

        from astrolift_registry.models import RegisteredApp
        from core.pubsub import subscribe
        from core.tenancy import (
            TenantContext,
            get_current_tenant,
            set_current_tenant,
        )

        # If we came in over WS, the cookie-aware handler stashed the
        # tenant on the context. Pin it on the contextvar so the rest
        # of the platform's tenant-aware code sees it.
        ws_tenant: TenantContext | None = getattr(info.context, "_ws_tenant", None)
        if ws_tenant is not None:
            set_current_tenant(ws_tenant)

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return

        topic = f"deployment.lifecycle.{org_id}"
        if app_slug:
            app_guid = await sync_to_async(
                lambda: (
                    RegisteredApp.objects.filter(organization_id=org_id, slug=app_slug)
                    .values_list("guid", flat=True)
                    .first()
                )
            )()
            if app_guid is None:
                return
            topic = f"deployment.lifecycle.app.{app_guid}"

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
        latest = Notification.objects.filter(user=info.context.user).order_by("-created_at").first()
        if latest:
            last_id = latest.pk

        while True:
            await asyncio.sleep(2)  # Poll every 2 seconds
            try:
                qs = Notification.objects.filter(user=info.context.user).order_by("-created_at")
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

        Yields submission IDs as they arrive. Scoped to the caller's
        organization (#1193): ``FormSubmission`` is org-owned (it carries an
        ``organization`` FK, mirroring how ``astrolift_forms`` resolves
        submissions), so the stream is filtered by ``organization_id`` — a
        caller cannot subscribe to another tenant's form submissions.

        Tenant resolution mirrors ``astrolift_deployment_lifecycle_stream``
        (the org-scoped sibling in this file): over WS the cookie-aware ASGI
        handler stashes the resolved tenant on ``info.context._ws_tenant``;
        we pin it on the contextvar and fail closed when no org resolves.
        """
        from asgiref.sync import sync_to_async

        from astrolift_forms.models import FormSubmission
        from core.tenancy import (
            TenantContext,
            get_current_tenant,
            set_current_tenant,
        )

        ws_tenant: TenantContext | None = getattr(info.context, "_ws_tenant", None)
        if ws_tenant is not None:
            set_current_tenant(ws_tenant)

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return

        def _fetch(after_id):
            qs = FormSubmission.objects.filter(
                form__slug=slug,
                organization_id=org_id,
            ).order_by("-submitted_at")
            if after_id:
                qs = qs.filter(pk__gt=after_id)
            return list(qs[:10])

        last_id = None
        latest = await sync_to_async(_fetch)(None)
        if latest:
            last_id = latest[0].pk

        while True:
            await asyncio.sleep(2)
            try:
                for sub in await sync_to_async(_fetch)(last_id):
                    yield f"New submission #{sub.pk}"
                    last_id = max(last_id or 0, sub.pk) if isinstance(sub.pk, int) else sub.pk
            except Exception:
                pass


# ---- Schema-level Subscription -------------------------------------
#
# Per-app subscription types layer onto the core surface by multiple
# inheritance — same pattern Query / Mutation use in config/schema.py.
# Add a new submodule's subscriptions by importing it here and
# extending the bases tuple.

from astrolift_lifecycle.schema.subscriptions import (  # noqa: E402
    LifecycleSubscription,
)


@strawberry.type
class Subscription(_CoreSubscription, LifecycleSubscription):
    """Composed subscription root — see also Query / Mutation in
    ``config.schema``."""
