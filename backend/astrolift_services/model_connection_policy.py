"""Current destination admission and version-bound model connection policy."""

import hashlib
import json
from dataclasses import dataclass, replace
from types import SimpleNamespace

from django.contrib.auth import get_user, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db.models import Q
from django.utils import timezone

from astrolift_identity import abac
from astrolift_identity.api_tokens import get_current_api_token, session_may_act_in
from astrolift_identity.models import Organization
from astrolift_identity.permission_resolver import _candidate_scopes, decide
from astrolift_registry.scopes import app_scope_by_guid
from astrolift_services.models import ModelConnectionPolicy
from core.current_credential import current_dispatch_credential
from core.permissions import Permission, PermissionDenied, _check_permission_decision
from core.tenancy import get_current_tenant


@dataclass(frozen=True)
class EffectivePolicy:
    mode: str
    required_approvals: int
    allow_self_approval: bool
    version: str


def locked_organization():
    tenant = get_current_tenant()
    org = (
        Organization.objects.select_for_update().filter(pk=tenant.organization_id if tenant else None).first()
    )
    if org is None:
        raise PermissionDenied(Permission.APP_UPDATE, None, "organization is unavailable")
    return org


class _ReadOnlyAuthSession(SessionStore):
    key_salt = SessionStore().key_salt

    def cycle_key(self):
        pass

    def flush(self):
        self._session_cache = {}
        self._session_key = None


def fresh_actor(info):
    tenant = get_current_tenant()
    request = getattr(info.context, "request", None)
    actor = (
        get_user_model().objects.filter(pk=tenant.actor_user_id if tenant else None, is_active=True).first()
    )
    if (
        actor is None
        or request is None
        or getattr(getattr(request, "user", None), "pk", None) != actor.pk
        or not session_may_act_in(actor, tenant.organization_id)
    ):
        raise PermissionDenied(Permission.APP_UPDATE, None, "authentication is unavailable")
    if get_current_api_token() is None:
        key = getattr(getattr(request, "session", None), "session_key", None)
        stored = (
            Session.objects.filter(session_key=key, expire_date__gt=timezone.now()).first() if key else None
        )
        if stored is None:
            raise PermissionDenied(Permission.APP_UPDATE, None, "current session is unavailable")
        store = _ReadOnlyAuthSession(key)
        # Supply the freshly decoded DB bag without using the request's cached auth/session.
        store._session_cache = stored.get_decoded()
        user = get_user(SimpleNamespace(session=store))
        if not user.is_authenticated or user.pk != actor.pk or store.session_key != key:
            raise PermissionDenied(Permission.APP_UPDATE, None, "current session is unavailable")
        from astrolift_identity.models import AstroliftSession

        if (
            AstroliftSession.all_objects.filter(session_key=key)
            .filter(Q(deleted_at__isnull=False) | Q(revoked_at__isnull=False))
            .exists()
        ):
            raise PermissionDenied(Permission.APP_UPDATE, None, "current session is unavailable")
        request._model_connection_auth_session = store
    else:
        request._model_connection_auth_session = {}
    return actor


def admission(info, environment, permission=Permission.APP_UPDATE, *, approvals=0, request_only=False):
    """Defer only quorum for intake; effect admission always checks the durable vote count."""
    tenant = get_current_tenant()
    with current_dispatch_credential(permission):
        fresh_actor(info)
        attrs = replace(
            abac.attributes_from_request(
                SimpleNamespace(
                    META=getattr(info.context.request, "META", {}),
                    session=info.context.request._model_connection_auth_session,
                ),
                tenant.actor_user_id,
            ),
            cache={},
            environment=environment.name,
            region=environment.tenant_cluster.region or None,
            approvals=approvals,
            approval_request=request_only,
        )
        with abac.request_attributes(attrs):
            scope = app_scope_by_guid("id", permission=permission)(
                {"id": str(environment.registered_app.guid)}
            )
            decision = decide(tenant, permission, scope)
            # Model connection policy remains enforceable for operators too.
            result = decision.abac
            if decision.superuser:
                result = abac.evaluate(
                    abac.new_subject(
                        organization_id=tenant.organization_id,
                        actor_user_id=tenant.actor_user_id,
                        permission=permission.value,
                        chain=_candidate_scopes(tenant, scope),
                        roles_at_target=[],
                        groups=[],
                        attrs=attrs,
                    )
                )
            _check_permission_decision(
                permission,
                scope,
                lambda: (
                    (decision.rbac_granted or decision.superuser) and not (result and result.denied),
                    "model connection authority is unavailable",
                ),
            )
            required = 0
            if result:
                policies = {str(p.guid): p for p in abac.org_policies(tenant.organization_id, attrs)}
                for outcome in result.applied:
                    policy = policies[outcome.policy_guid]
                    for condition in policy.conditions:
                        if condition.get("kind") == "approval_required":
                            need = condition.get("min_approvers", 1)
                            if type(need) is not int or not 1 <= need <= 16:
                                raise PermissionDenied(permission, scope, "approval quorum is unavailable")
                            required = max(required, need)
            return required


def effective_policy(service, *, approval_minimum=0):
    from constance import config

    from astrolift_identity.models import Policy

    try:
        defaults = [
            config.MODEL_CONNECTION_DEFAULT_MODE,
            config.MODEL_CONNECTION_DEFAULT_QUORUM,
            config.MODEL_CONNECTION_ALLOW_SELF_APPROVAL,
        ]
    except Exception:
        raise PermissionDenied(Permission.APP_UPDATE, None, "connection policy is unavailable") from None
    rows = list(
        ModelConnectionPolicy.objects.filter(organization_id=service.organization_id)
        .filter(Q(model_deployment_id__isnull=True) | Q(model_deployment_id=service.pk))
        .order_by("pk")
    )
    org = next((row for row in rows if row.model_deployment_id is None), None)
    restriction = next((row for row in rows if row.model_deployment_id is not None), None)
    mode, quorum, self_approval = (
        defaults if org is None else [org.mode, org.required_approvals, org.allow_self_approval]
    )
    for values in [
        defaults,
        [mode, quorum, self_approval],
        *(
            [[restriction.mode, restriction.required_approvals, restriction.allow_self_approval]]
            if restriction
            else []
        ),
    ]:
        if (
            values[0] not in ModelConnectionPolicy.Mode.values
            or type(values[1]) is not int
            or not 1 <= values[1] <= 16
            or type(values[2]) is not bool
        ):
            raise PermissionDenied(Permission.APP_UPDATE, None, "connection policy is unavailable")
    if restriction:
        levels = {"AUTO": 0, "REQUIRE_APPROVAL": 1, "DENY": 2}
        mode = max((mode, restriction.mode), key=levels.get)
        quorum = max(quorum, restriction.required_approvals)
        self_approval = self_approval and restriction.allow_self_approval
    if approval_minimum:
        if mode == "AUTO":
            mode = "REQUIRE_APPROVAL"
        quorum = max(quorum, approval_minimum)
    policies = list(Policy.objects.filter(organization_id=service.organization_id).order_by("pk"))
    revision = {
        "defaults": defaults,
        "settings": [
            [str(r.guid), r.version, r.mode, r.required_approvals, r.allow_self_approval] for r in rows
        ],
        "abac": [
            [
                str(r.guid),
                r.version,
                r.scope_level,
                r.scope_id,
                r.effect,
                r.action_pattern,
                r.resource_pattern,
                r.actor_pattern,
                r.conditions,
            ]
            for r in policies
        ],
    }
    return EffectivePolicy(
        mode,
        quorum,
        self_approval,
        hashlib.sha256(json.dumps(revision, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    )
