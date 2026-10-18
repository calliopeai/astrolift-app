"""Current-recipient reads over durable operations notifications (#2457)."""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db.models import Q
from django.utils import timezone
from graphql import GraphQLError

from astrolift_identity.abac import RequestAttributes, current_attributes, request_attributes
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    session_may_act_in,
    set_current_api_token,
    token_scope_allows_permission,
    with_active_org_member,
)
from astrolift_identity.models import ApiToken, Member, Organization, Team
from astrolift_operations.models import Notification, UserAlertSubscription
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import live_app_owners
from astrolift_registry.visibility import visible_registry_apps
from core.permissions import Permission
from core.tenancy import get_current_tenant, tenant_context

CURSOR_SALT = "astrolift.operations.notification-inbox.v1"
MAX_BATCH = 100


def forbidden():
    raise GraphQLError("Notification inbox authorization is unavailable", extensions={"code": "FORBIDDEN"})


def capture_identity(info):
    tenant = get_current_tenant()
    viewer = getattr(info.context, "user", None)
    if viewer is None:
        viewer = getattr(getattr(info.context, "request", None), "user", None)
    if (
        tenant is None
        or tenant.organization_id is None
        or tenant.actor_user_id is None
        or viewer is None
        or not viewer.is_authenticated
        or viewer.pk != tenant.actor_user_id
    ):
        forbidden()
    attributes = current_attributes() or RequestAttributes(actor_user_id=tenant.actor_user_id)
    return tenant, getattr(info.context, "_ws_api_token", get_current_api_token()), attributes


@contextmanager
def current_identity(identity):
    tenant, token, attributes = identity
    user = get_user_model().objects.filter(pk=tenant.actor_user_id, is_active=True).first()
    if (
        user is None
        or not session_may_act_in(user, tenant.organization_id)
        or not Organization.objects.filter(pk=tenant.organization_id).exists()
    ):
        forbidden()
    if token is not None:
        credential_hash = token.token_hash
        token = with_active_org_member(
            ApiToken.objects.filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()),
                pk=token.pk,
                user_id=tenant.actor_user_id,
                organization_id=tenant.organization_id,
                is_revoked=False,
            ),
            user="user",
            organization="organization",
        ).first()
        if (
            token is None
            or not hmac.compare_digest(credential_hash, token.token_hash)
            or not token_scope_allows_permission(token, Permission.APP_READ.value)
        ):
            forbidden()
        if (
            token.team_id is not None
            and not Team.objects.filter(
                pk=token.team_id,
                organization_id=tenant.organization_id,
            ).exists()
        ):
            forbidden()
    marker = set_current_api_token(token)
    try:
        with tenant_context(tenant), request_attributes(dataclasses.replace(attributes, cache={})):
            yield tenant, token
    finally:
        reset_current_api_token(marker)


def _binding(tenant, token):
    database = settings.DATABASES["default"]
    install = os.environ.get("ASTROLIFT_INSTALL_ID") or [
        database.get(key, "") for key in ("ENGINE", "HOST", "PORT", "NAME")
    ]
    value = [
        install,
        tenant.organization_id,
        tenant.actor_user_id,
        [token.pk, token.team_id, sorted(token.scopes or []), token.token_hash]
        if token is not None
        else None,
    ]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _decode(cursor, binding):
    if cursor is None:
        return 0
    try:
        if not isinstance(cursor, str) or len(cursor) > 4096:
            raise signing.BadSignature("oversized cursor")
        value = signing.Signer(salt=CURSOR_SALT).unsign_object(cursor)
    except signing.BadSignature as exc:
        raise GraphQLError("Invalid notification inbox cursor") from exc
    if (
        not isinstance(value, list)
        or len(value) != 2
        or value[0] != binding
        or type(value[1]) is not int
        or value[1] < 0
    ):
        raise GraphQLError("Notification inbox cursor does not match the current identity or credential")
    return value[1]


def _encode(sequence, binding):
    return signing.Signer(salt=CURSOR_SALT).sign_object([binding, sequence], compress=True)


def expiry_provenance(row):
    value = row.expiry_warning
    if row.kind != Notification.Kind.APP_EXPIRING or not isinstance(value, dict):
        return None
    try:
        guid = UUID(value["app_guid"])
        when = datetime.fromisoformat(value["expires_at"])
        slug = value["app_slug"]
        warning = value["warning"]
        if not isinstance(slug, str) or not slug or warning not in {"24h", "1h"} or timezone.is_naive(when):
            return None
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    return {"app_id": str(guid), "app_slug": slug, "expires_at": when, "warning_window": warning}


def visible_rows(rows, tenant, token):
    warnings = {row.pk: expiry_provenance(row) for row in rows if row.kind == Notification.Kind.APP_EXPIRING}
    guids = {value["app_id"] for value in warnings.values() if value is not None}
    teams = Member.objects.filter(
        user_id=tenant.actor_user_id,
        scope_kind=Member.ScopeKind.TEAM,
        is_active=True,
        deleted_at__isnull=True,
    ).values("scope_id")
    apps = visible_registry_apps(
        live_app_owners(RegisteredApp.objects.filter(guid__in=guids)).filter(
            Q(created_by_id=tenant.actor_user_id) | Q(team_id__in=teams)
        ),
        Permission.APP_READ,
    )
    muted = UserAlertSubscription.objects.filter(
        user_id=tenant.actor_user_id,
        alert_kind=UserAlertSubscription.AlertKind.APP_EXPIRING,
        enabled=False,
    ).values("registered_app_id")
    allowed = {str(guid) for guid in apps.exclude(pk__in=muted).values_list("guid", flat=True)}
    # Old, unstructured warnings have no trustworthy app identity to authorize.
    return [
        row
        for row in rows
        if (row.pk not in warnings and (token is None or token.team_id is None))
        or (row.pk in warnings and warnings[row.pk] is not None and warnings[row.pk]["app_id"] in allowed)
    ]


def notification_page(identity, *, after=None, limit=50):
    from astrolift_operations.schema.notification_inbox import NotificationPageType
    from astrolift_operations.schema.types import notification_to_type

    with current_identity(identity) as (tenant, token):
        binding = _binding(tenant, token)
        sequence = _decode(after, binding)
        size = max(1, min(limit, MAX_BATCH))
        rows = list(
            Notification.objects.filter(
                organization_id=tenant.organization_id,
                user_id=tenant.actor_user_id,
                inbox_sequence__gt=sequence,
            ).order_by("inbox_sequence")[: size + 1]
        )
        scanned = rows[:size]
        return NotificationPageType(
            items=[notification_to_type(row) for row in visible_rows(scanned, tenant, token)],
            end_cursor=_encode(scanned[-1].inbox_sequence if scanned else sequence, binding),
            has_more=len(rows) > size,
        )


def latest_notifications(identity, *, unread_only=False, limit=50):
    from astrolift_operations.schema.types import notification_to_type

    with current_identity(identity) as (tenant, token):
        rows = Notification.objects.filter(
            organization_id=tenant.organization_id, user_id=tenant.actor_user_id
        )
        if unread_only:
            rows = rows.filter(read_at__isnull=True)
        return [
            notification_to_type(row)
            for row in visible_rows(
                list(rows.order_by("-inbox_sequence")[: max(1, min(limit, 200))]),
                tenant,
                token,
            )
        ]


def readable_notification(identity, notification_id):
    with current_identity(identity) as (tenant, token):
        row = Notification.objects.filter(
            guid=str(notification_id),
            organization_id=tenant.organization_id,
            user_id=tenant.actor_user_id,
        ).first()
        return row if row is not None and visible_rows([row], tenant, token) else None


def mark_all_read(identity):
    from astrolift_operations.models import NotificationInboxCounter

    with current_identity(identity) as (tenant, _token):
        ceiling = (
            NotificationInboxCounter.objects.filter(
                organization_id=tenant.organization_id,
                user_id=tenant.actor_user_id,
            )
            .values_list("last_sequence", flat=True)
            .first()
            or 0
        )
    marked = 0
    sequence = 0
    while sequence < ceiling:
        with current_identity(identity) as (tenant, token):
            rows = list(
                Notification.objects.filter(
                    organization_id=tenant.organization_id,
                    user_id=tenant.actor_user_id,
                    read_at__isnull=True,
                    inbox_sequence__gt=sequence,
                    inbox_sequence__lte=ceiling,
                ).order_by("inbox_sequence")[:MAX_BATCH]
            )
            if not rows:
                return marked
            sequence = rows[-1].inbox_sequence
            marked += Notification.objects.filter(
                pk__in=[row.pk for row in visible_rows(rows, tenant, token)]
            ).update(read_at=timezone.now())
    return marked
