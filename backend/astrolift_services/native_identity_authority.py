"""Original app credentials for private native identity work, never ambient grants."""

import hashlib
import hmac
import json
import re
from contextlib import contextmanager
from dataclasses import asdict, replace
from types import SimpleNamespace
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user, get_user_model
from django.contrib.sessions.models import Session
from django.db.models import Q
from django.http import HttpRequest
from django.utils import timezone

from astrolift_identity import abac
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    session_may_act_in,
    set_current_api_token,
    token_scope_allows_permission,
    with_active_org_member,
)
from astrolift_identity.models import ApiToken, AstroliftSession, Organization
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.visibility import live_lifecycle_rows
from astrolift_registry.scopes import _app_scope
from astrolift_workflows.native_identity_inputs import AcceptedAppIdentityAuthority
from core.current_session import _ReadOnlyAuthSession, fresh_authenticated_session
from core.permissions import Permission, PermissionDenied, ScopeKind, check_permission
from core.tenancy import TenantContext, get_current_tenant, tenant_context

_PERMISSIONS = frozenset({Permission.APP_UPDATE, Permission.APP_DEPLOY})
_HEX = re.compile(r"[a-f0-9]{64}\Z")


def _refuse(permission=Permission.APP_UPDATE):
    raise PermissionDenied(permission, None, "Original native identity authority is unavailable.")


def _mac(key, label, value):
    return hmac.new(str(key).encode(), (label + "." + value).encode(), hashlib.sha256).hexdigest()


def _signature(reference, key):
    value = asdict(reference)
    del value["signature"]
    return _mac(
        key, "astrolift.native-app-authority.v1", json.dumps(value, sort_keys=True, separators=(",", ":"))
    )


def _guid(value):
    return str(value.guid) if value is not None else None


def _environment(organization, environment_guid):
    return (
        live_lifecycle_rows(AppEnvironment.objects.all(), org_id=organization.pk)
        .filter(guid=environment_guid, registered_app__organization=organization)
        .select_related("registered_app__team", "registered_app__project", "tenant_cluster__provider_plugin")
        .first()
    )


def _owner_tuple(environment):
    app, cluster = environment.registered_app, environment.tenant_cluster
    if (
        cluster is None
        or not cluster.is_active
        or cluster.deleted_at is not None
        or cluster.provider_plugin.deleted_at is not None
        or not cluster.provider_plugin.is_enabled
    ):
        _refuse()
    return (_guid(app), _guid(app.team), _guid(app.project), _guid(cluster), _guid(cluster.provider_plugin))


def _token(organization, user_id, credential_guid):
    return (
        with_active_org_member(
            ApiToken.objects.filter(
                guid=credential_guid,
                organization=organization,
                user_id=user_id,
                user__is_active=True,
                is_revoked=False,
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())),
            user="user",
            organization="organization",
        )
        .select_related("team")
        .first()
    )


def _session(sidecar, user_id):
    now = timezone.now()
    if (
        sidecar.user_id != user_id
        or sidecar.api_token_id is not None
        or not sidecar.session_key
        or sidecar.deleted_at is not None
        or sidecar.revoked_at is not None
        or sidecar.expires_at is not None
        and sidecar.expires_at <= now
    ):
        _refuse()
    stored = Session.objects.filter(session_key=sidecar.session_key, expire_date__gt=now).first()
    if stored is None:
        _refuse()
    store = _ReadOnlyAuthSession(sidecar.session_key)
    store._session_cache = stored.get_decoded()
    # Django's authentication backend reads a session carrier, not a permission request.
    user = get_user(SimpleNamespace(session=store))
    if not user.is_authenticated or user.pk != user_id or store.session_key != sidecar.session_key:
        _refuse()
    invalid = (
        Q(deleted_at__isnull=False)
        | Q(revoked_at__isnull=False)
        | Q(expires_at__lte=now)
        | ~Q(user_id=user_id)
    )
    if AstroliftSession.all_objects.filter(session_key=sidecar.session_key).filter(invalid).exists():
        _refuse()
    return store


def _browser_attributes(user_id, store, sidecar):
    from datetime import UTC, datetime

    from astrolift_identity.session_elevation import get_status
    from astrolift_identity.sessions import SESSION_LOGIN_METHOD_KEY
    from astrolift_identity.step_up_sso import SESSION_SSO_AUTH_TIME_KEY

    raw = store.get(SESSION_SSO_AUTH_TIME_KEY)
    try:
        authenticated_at = (
            datetime.fromtimestamp(int(raw), tz=UTC) if isinstance(raw, (int, float)) else sidecar.created_at
        )
    except (ValueError, OverflowError, OSError):
        _refuse()
    factors = {str(store[SESSION_LOGIN_METHOD_KEY]).lower()} if store.get(SESSION_LOGIN_METHOD_KEY) else set()
    status = get_status(store)
    if status.elevated and status.method:
        factors.add(str(status.method).lower())
    return abac.RequestAttributes(
        actor_user_id=user_id, authenticated_at=authenticated_at, auth_factors=frozenset(factors) or None
    )


def _validate_reference(reference):
    if (
        type(reference) is not AcceptedAppIdentityAuthority
        or type(reference.schema) is not int
        or reference.schema != 1
    ):
        _refuse()
    try:
        permission = Permission(reference.permission)
        values = asdict(reference)
        if (
            permission not in _PERMISSIONS
            or type(reference.actor_user_id) is not int
            or reference.actor_user_id <= 0
        ):
            raise ValueError
        for field in (
            "organization_guid",
            "environment_guid",
            "app_guid",
            "cluster_guid",
            "provider_guid",
            "credential_guid",
        ):
            if str(UUID(values[field])) != values[field] or UUID(values[field]).int == 0:
                raise ValueError
        for field in ("team_guid", "project_guid", "token_team_guid"):
            if values[field] is not None and (
                str(UUID(values[field])) != values[field] or UUID(values[field]).int == 0
            ):
                raise ValueError
        if not _HEX.fullmatch(reference.signature) or not _HEX.fullmatch(reference.credential_binding):
            raise ValueError
        if reference.credential_kind not in {"api_token", "browser_session"}:
            raise ValueError
        if type(reference.accepted_token_scopes) is not tuple or len(reference.accepted_token_scopes) > 64:
            raise ValueError
        if any(not isinstance(s, str) or not 1 <= len(s) <= 64 for s in reference.accepted_token_scopes):
            raise ValueError
        if reference.credential_kind == "browser_session" and (
            reference.accepted_token_scopes or reference.token_team_guid is not None
        ):
            raise ValueError
        for key in [settings.SECRET_KEY, *getattr(settings, "SECRET_KEY_FALLBACKS", [])]:
            if hmac.compare_digest(reference.signature, _signature(reference, key)):
                return permission, key
    except (ValueError, TypeError, AttributeError):
        pass
    _refuse()


def capture_app_identity_authority(request, *, environment_guid, permission):
    """Called only at an actual accepted HTTP boundary; the caller persists this reference."""
    tenant = get_current_tenant()
    if (
        not isinstance(request, HttpRequest)
        or type(permission) is not Permission
        or permission not in _PERMISSIONS
        or tenant is None
    ):
        _refuse()
    user = get_user_model().objects.filter(pk=tenant.actor_user_id, is_active=True).first()
    organization = Organization.objects.filter(pk=tenant.organization_id).first()
    if (
        user is None
        or organization is None
        or not getattr(request.user, "is_authenticated", False)
        or getattr(request.user, "pk", None) != user.pk
    ):
        _refuse(permission)
    environment = _environment(organization, environment_guid)
    if environment is None:
        _refuse(permission)
    token = get_current_api_token()
    authenticated_token = getattr(request, "_api_token", None)
    if getattr(authenticated_token, "pk", None) != getattr(token, "pk", None):
        _refuse(permission)
    scopes, token_team_guid = (), None
    if token is not None:
        fresh = _token(organization, user.pk, token.guid)
        if (
            fresh is None
            or authenticated_token.guid != fresh.guid
            or not hmac.compare_digest(authenticated_token.token_hash, fresh.token_hash)
            or authenticated_token.team_id != fresh.team_id
            or not isinstance(authenticated_token.scopes, list)
            or len(authenticated_token.scopes) > 64
            or any(not isinstance(s, str) or not 1 <= len(s) <= 64 for s in authenticated_token.scopes)
        ):
            _refuse(permission)
        scopes, token_team_guid = tuple(sorted(set(authenticated_token.scopes))), _guid(fresh.team)
        kind, credential_guid, binding_value = "api_token", str(fresh.guid), fresh.token_hash
    else:
        fresh_authenticated_session(request, actor_user_id=user.pk, permission=permission)
        rows = list(AstroliftSession.all_objects.filter(session_key=request.session.session_key)[:2])
        if len(rows) != 1:
            _refuse(permission)
        sidecar = rows[0]
        _session(sidecar, user.pk)
        kind, credential_guid, binding_value = "browser_session", str(sidecar.guid), sidecar.session_key
    app, team, project, cluster, provider = _owner_tuple(environment)
    reference = AcceptedAppIdentityAuthority(
        str(organization.guid),
        user.pk,
        str(environment.guid),
        app,
        team,
        project,
        cluster,
        provider,
        permission.value,
        kind,
        credential_guid,
        _mac(settings.SECRET_KEY, "astrolift.native-original-credential.v1", binding_value),
        scopes,
        token_team_guid,
        "",
    )
    reference = replace(reference, signature=_signature(reference, settings.SECRET_KEY))
    with current_app_identity_authority(reference):
        pass
    return reference


@contextmanager
def current_app_identity_authority(reference, *, deployment_guid=None, deployment_execution=False):
    """Re-read original identity and current RBAC/ABAC; no request or system actor is invented."""
    permission, key = _validate_reference(reference)
    if deployment_execution and deployment_guid is None:
        _refuse(permission)
    organization = Organization.objects.filter(guid=reference.organization_guid).first()
    user = get_user_model().objects.filter(pk=reference.actor_user_id, is_active=True).first()
    if organization is None or user is None:
        _refuse(permission)
    token = None
    if reference.credential_kind == "api_token":
        token = _token(organization, user.pk, reference.credential_guid)
        if token is None or _guid(token.team) != reference.token_team_guid:
            _refuse(permission)
        binding_value = token.token_hash
        if not token_scope_allows_permission(
            SimpleNamespace(scopes=reference.accepted_token_scopes), permission.value
        ):
            _refuse(permission)
        attrs = abac.RequestAttributes(actor_user_id=user.pk, authenticated_at=None, auth_factors=None)
    else:
        sidecar = AstroliftSession.all_objects.filter(guid=reference.credential_guid).first()
        if sidecar is None or not session_may_act_in(user, organization.pk):
            _refuse(permission)
        store = _session(sidecar, user.pk)
        binding_value = sidecar.session_key
        attrs = _browser_attributes(user.pk, store, sidecar)
    if not hmac.compare_digest(
        reference.credential_binding, _mac(key, "astrolift.native-original-credential.v1", binding_value)
    ):
        _refuse(permission)
    marker = set_current_api_token(token)
    try:
        with tenant_context(TenantContext(organization_id=organization.pk, actor_user_id=user.pk)):
            environment = _environment(organization, reference.environment_guid)
            if environment is None or _owner_tuple(environment) != (
                reference.app_guid,
                reference.team_guid,
                reference.project_guid,
                reference.cluster_guid,
                reference.provider_guid,
            ):
                _refuse(permission)
            attrs.environment = environment.name
            attrs.region = environment.tenant_cluster.region or None
            attrs.approvals = 0
            if deployment_guid is not None:
                from astrolift_lifecycle.deployment_identity_origin import approval_context

                attrs.approvals, attrs.approval_request = approval_context(
                    reference, deployment_guid, execution=deployment_execution
                )
            with abac.request_attributes(attrs):
                scope = _app_scope(pk=environment.registered_app_id, permission=permission)
                if scope.kind != ScopeKind.APP or scope.id != environment.registered_app_id:
                    _refuse(permission)
                check_permission(permission, scope=scope)
                yield environment
    finally:
        reset_current_api_token(marker)
