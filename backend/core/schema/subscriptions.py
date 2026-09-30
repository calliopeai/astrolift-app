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

from astrolift_forms.scopes import form_org_scope
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.schema.ws_auth import ws_identity


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


def _lifecycle_stream_app(org_id, slug):
    from astrolift_lifecycle.visibility import live_app_rows
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.visibility import visible_registry_apps

    return (
        visible_registry_apps(
            live_app_rows(RegisteredApp.objects.filter(organization_id=org_id, slug=slug)),
            Permission.APP_READ,
        )
        .values_list("pk", "guid")
        .first()
    )


def _lifecycle_stream_event(org_id, event, app_id):
    from uuid import UUID

    from astrolift_identity.abac import operation_attributes
    from astrolift_identity.operation_context import deployment_approval_count, environment_context
    from astrolift_lifecycle.models import Deployment
    from astrolift_lifecycle.scopes import deployment_app_scope
    from astrolift_lifecycle.visibility import live_lifecycle_rows
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.scopes import live_app_owners
    from core.permissions import PermissionDenied, check_permission

    if not isinstance(event, dict):
        return None
    try:
        guid = UUID(str(event.get("deployment_id", "")))
    except (ValueError, TypeError, AttributeError):
        return None
    row = (
        live_lifecycle_rows(Deployment.objects.all())
        .filter(
            guid=guid,
            registered_app__organization_id=org_id,
            registered_app__in=live_app_owners(RegisteredApp.objects.filter(organization_id=org_id)),
        )
        .select_related("registered_app", "app_environment__tenant_cluster")
        .first()
    )
    if row is None or (app_id is not None and row.registered_app_id != app_id):
        return None
    env = row.app_environment
    if env is not None and (env.deleted_at is not None or env.registered_app_id != row.registered_app_id):
        return None
    if (
        env is not None
        and env.tenant_cluster is not None
        and (
            env.tenant_cluster.deleted_at is not None
            or not env.tenant_cluster.is_active
            or env.tenant_cluster.organization_id not in (None, org_id)
        )
    ):
        return None
    facts = environment_context(env, approvals=deployment_approval_count(row))
    try:
        with operation_attributes(**facts.attributes()):
            scope = deployment_app_scope("id", permission=Permission.APP_READ)({"id": guid})
            check_permission(Permission.APP_READ, scope=scope)
    except PermissionDenied:
        return None
    return {
        "deployment_id": str(row.guid),
        "registered_app_slug": row.registered_app.slug,
        "environment_name": env.name if env is not None else "",
        "status": str(event.get("status", "")),
        "occurred_at": str(event.get("occurred_at", "")),
    }


def _lifecycle_event_for_identity(tenant, token, event, app_id, attributes):
    """Each event rechecks the pinned identity with a fresh permission cache."""
    import dataclasses

    from django.contrib.auth import get_user_model
    from django.db.models import Q
    from django.utils import timezone

    from astrolift_identity.abac import request_attributes
    from astrolift_identity.api_tokens import (
        reset_current_api_token,
        session_may_act_in,
        set_current_api_token,
        with_active_org_member,
    )
    from astrolift_identity.models import ApiToken
    from core.tenancy import tenant_context

    user = get_user_model().objects.filter(pk=tenant.actor_user_id, is_active=True).first()
    if user is None or not session_may_act_in(user, tenant.organization_id):
        return None
    if token is not None:
        token = with_active_org_member(
            ApiToken.objects.filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()),
                pk=token.pk,
                organization_id=tenant.organization_id,
                user_id=tenant.actor_user_id,
                is_revoked=False,
            ),
            user="user",
            organization="organization",
        ).first()
        if token is None:
            return None
    attrs = dataclasses.replace(attributes, cache={})
    marker = set_current_api_token(token)
    try:
        with tenant_context(tenant), request_attributes(attrs):
            return _lifecycle_stream_event(tenant.organization_id, event, app_id)
    finally:
        reset_current_api_token(marker)


@strawberry.type
class _CoreSubscription:
    """Subscriptions native to ``core``. Merged with per-app
    subscription types into the schema-level :class:`Subscription`
    below so each domain owns its own surface without one root
    file becoming a dumping ground."""

    @strawberry.subscription
    @ws_identity
    @require_permission(Permission.APP_READ, any_scope=True)
    @tenant_scoped()
    async def astrolift_deployment_lifecycle_stream(
        self, info: Info, app_slug: str | None = None
    ) -> AsyncGenerator[DeploymentLifecycleEventType, None]:
        """Stream transitions for permitted live deployments in the active org."""
        from asgiref.sync import sync_to_async

        from astrolift_identity.abac import RequestAttributes, current_attributes
        from astrolift_identity.api_tokens import get_current_api_token
        from core.pubsub import subscribe
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        token = get_current_api_token()
        attributes = current_attributes() or RequestAttributes(actor_user_id=tenant.actor_user_id)
        if token is not None and token.organization_id != org_id:
            return
        topic = f"deployment.lifecycle.{org_id}"
        app_id = None
        if app_slug:
            target = await sync_to_async(_lifecycle_stream_app)(org_id, app_slug)
            if target is None:
                return
            app_id, app_guid = target
            topic = f"deployment.lifecycle.app.{app_guid}"

        inner = subscribe(topic)
        try:
            async for event in inner:
                payload = await sync_to_async(_lifecycle_event_for_identity)(
                    tenant, token, event, app_id, attributes
                )
                if payload is not None:
                    yield DeploymentLifecycleEventType(**payload)
        finally:
            await inner.aclose()

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
    @ws_identity
    @require_permission(Permission.FORM_READ, scope=form_org_scope(Permission.FORM_READ))
    @tenant_scoped()
    async def form_submission_received(self, info: Info, slug: str) -> AsyncGenerator[str, None]:
        """Subscribe to new submissions for a specific form.

        Yields submission IDs as they arrive. Scoped to the caller's
        organization (#1193): ``FormSubmission`` is org-owned (it carries an
        ``organization`` FK, mirroring how ``astrolift_forms`` resolves
        submissions), so the stream is filtered by ``organization_id`` — a
        caller cannot subscribe to another tenant's form submissions.

        WebSocket identity is pinned before the organization ``form.read``
        gate. Refused callers complete silently. The stream retains the live
        form's ID so a replacement reusing its slug does not enter the stream.
        """
        from asgiref.sync import sync_to_async

        from astrolift_forms.models import FormDefinition, FormSubmission
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return

        form_id = await sync_to_async(
            lambda: FormDefinition.objects.filter(organization_id=org_id, slug=slug)
            .values_list("pk", flat=True)
            .first()
        )()
        if form_id is None:
            return

        def _fetch(after_id):
            qs = FormSubmission.objects.filter(
                form_id=form_id,
                form__organization_id=org_id,
                form__deleted_at__isnull=True,
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
