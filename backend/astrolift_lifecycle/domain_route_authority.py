"""Original admitted browser, device-session or standalone token route authority."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from astrolift_identity import abac
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    set_current_api_token,
    token_scope_allows_permission,
)
from astrolift_identity.models import ApiToken, AstroliftSession, DeviceFlowSession, Member, Organization
from astrolift_registry.scopes import _credential_scope
from core.current_session import fresh_authenticated_session
from core.permissions import Permission, PermissionDenied, ScopeKind, check_permission
from core.tenancy import TenantContext, get_current_tenant, tenant_context

P = Permission.APP_UPDATE


def _mac(value):
    return salted_hmac(
        "astrolift.domain-route-authority.v1",
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        algorithm="sha256",
    ).hexdigest()


def _refuse():
    raise PermissionDenied(P, None, "Current original domain-route authority is unavailable.")


def capture(request):
    tenant = get_current_tenant()
    if tenant is None or not tenant.organization_id or not tenant.actor_user_id:
        _refuse()
    if getattr(getattr(request, "user", None), "pk", None) != tenant.actor_user_id:
        _refuse()
    payload = {"schema": 1, "org": tenant.organization_id, "actor": tenant.actor_user_id}
    token = get_current_api_token()
    if token is not None:
        if getattr(getattr(request, "_api_token", None), "pk", None) != token.pk:
            _refuse()
        fresh = ApiToken.objects.filter(
            pk=token.pk,
            user_id=tenant.actor_user_id,
            organization_id=tenant.organization_id,
            is_revoked=False,
        ).first()
        if fresh is None or fresh.token_hash != token.token_hash or fresh.team_id != token.team_id:
            _refuse()
        ceiling = sorted(set(token.scopes or []))
        if any(not isinstance(s, str) or len(s) > 64 for s in ceiling) or len(ceiling) > 64:
            _refuse()
        payload.update(scopes=ceiling, team=token.team_id)
        sessions = list(DeviceFlowSession.objects.filter(api_token_id=token.pk)[:2])
        if len(sessions) > 1:
            _refuse()
        if sessions:
            row = sessions[0]
            payload.update(
                kind="device",
                credential=str(row.guid),
                consumed=row.consumed_at.isoformat() if row.consumed_at else "",
                origin=row.origin,
                client_kind=row.client_kind,
            )
        else:
            payload.update(kind="token", credential=str(token.guid), key_mac=_mac(token.token_hash))
    else:
        fresh_authenticated_session(request, actor_user_id=tenant.actor_user_id, permission=P)
        key = request.session.session_key
        rows = list(AstroliftSession.objects.filter(session_key=key, user_id=tenant.actor_user_id)[:2])
        if len(rows) != 1:
            _refuse()
        payload.update(kind="browser", credential=str(rows[0].guid), key_mac=_mac(key))
    reference = {"payload": payload, "mac": _mac(payload)}
    with current(reference):
        pass
    return reference


@contextmanager
def current(reference, *, environment=None, permissions=()):
    try:
        payload = copy.deepcopy(reference["payload"])
        if (
            set(reference) != {"payload", "mac"}
            or not constant_time_compare(reference["mac"], _mac(payload))
            or payload["schema"] != 1
        ):
            _refuse()
        actor_id, org_id = payload["actor"], payload["org"]
        if type(actor_id) is not int or type(org_id) is not int:
            _refuse()
        user = get_user_model().objects.filter(pk=actor_id, is_active=True).first()
        if user is None or not Organization.objects.filter(pk=org_id).exists():
            _refuse()
        now = timezone.now()
        token = None
        persisted = None
        if payload["kind"] in ("device", "token"):
            if not Member.objects.filter(
                scope_kind="ORG", scope_id=org_id, user_id=actor_id, is_active=True
            ).exists():
                _refuse()
            if payload["kind"] == "device":
                row = DeviceFlowSession.objects.filter(
                    guid=payload["credential"],
                    organization_id=org_id,
                    approved_user_id=actor_id,
                    state=DeviceFlowSession.STATE_CONSUMED,
                ).first()
                if (
                    row is None
                    or row.origin != payload["origin"]
                    or row.client_kind != payload["client_kind"]
                    or row.consumed_at is None
                    or row.consumed_at.isoformat() != payload["consumed"]
                    or not row.refresh_token_hash
                    or row.refresh_token_expires_at is None
                    or row.refresh_token_expires_at <= now
                ):
                    _refuse()
                token = ApiToken.objects.filter(pk=row.api_token_id).first()
            else:
                token = ApiToken.objects.filter(guid=payload["credential"]).first()
                if token is None or not constant_time_compare(_mac(token.token_hash), payload["key_mac"]):
                    _refuse()
            if (
                token is None
                or token.user_id != actor_id
                or token.organization_id != org_id
                or token.team_id != payload["team"]
                or token.is_revoked
                or (token.expires_at is not None and token.expires_at <= now)
            ):
                _refuse()
            accepted = SimpleNamespace(scopes=payload["scopes"])
            for permission, _scope in permissions:
                if not token_scope_allows_permission(accepted, permission.value):
                    _refuse()
        elif payload["kind"] == "browser":
            row = AstroliftSession.objects.filter(
                guid=payload["credential"], user_id=actor_id, revoked_at__isnull=True
            ).first()
            if (
                row is None
                or not constant_time_compare(_mac(row.session_key), payload["key_mac"])
                or (row.expires_at is not None and row.expires_at <= now)
            ):
                _refuse()
            request = SimpleNamespace(user=user, session=SessionStore(row.session_key))
            persisted = fresh_authenticated_session(request, actor_user_id=actor_id, permission=P)
            if (
                not user.is_superuser
                and not Member.objects.filter(
                    scope_kind="ORG", scope_id=org_id, user_id=actor_id, is_active=True
                ).exists()
            ):
                _refuse()
        else:
            _refuse()
    except (KeyError, TypeError, ValueError, AttributeError):
        _refuse()
    state = set_current_api_token(token)
    try:
        with (
            tenant_context(TenantContext(organization_id=org_id, actor_user_id=actor_id)),
            abac.request_attributes(
                abac.RequestAttributes(
                    actor_user_id=actor_id,
                    environment=environment.name if environment else None,
                    region=environment.tenant_cluster.region if environment else None,
                    approvals=0,
                    request=SimpleNamespace(session=persisted) if persisted else None,
                )
            ),
        ):
            for permission, scope in permissions:
                if scope.kind == ScopeKind.APP:
                    _credential_scope(scope, permission)
                elif token is not None and token.team_id is not None:
                    _refuse()
                check_permission(permission, scope=scope)
            yield user
    finally:
        reset_current_api_token(state)
