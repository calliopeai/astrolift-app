"""Fresh installation operator admission for model sources and configuration."""

from contextlib import contextmanager

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import HttpRequest
from django.utils import timezone

from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    session_may_act_in,
    set_current_api_token,
    with_active_org_member,
)
from astrolift_identity.models import ApiToken, Organization
from core.permissions import Permission, PermissionDenied, check_platform_operator
from core.tenancy import get_current_tenant


@contextmanager
def current_host_operator(*, request=None):
    tenant = get_current_tenant()
    user = (
        get_user_model().objects.filter(pk=tenant.actor_user_id, is_active=True).first()
        if tenant is not None
        else None
    )
    if (
        tenant is None
        or user is None
        or not Organization.objects.filter(pk=tenant.organization_id).exists()
        or not session_may_act_in(user, tenant.organization_id)
    ):
        raise PermissionDenied(Permission.ORG_UPDATE, None, "Model hosting authority is unavailable.")
    token = get_current_api_token()
    marker = None
    # Direct non-HTTP service fixtures have no session; actual browser requests
    # must carry and revalidate their persisted authentication after every wait.
    if token is None and (isinstance(request, HttpRequest) or hasattr(request, "session")):
        from core.current_session import fresh_authenticated_session

        fresh_authenticated_session(request, actor_user_id=user.pk, permission=Permission.ORG_UPDATE)
    try:
        if token is not None:
            fresh = with_active_org_member(
                ApiToken.objects.filter(
                    pk=token.pk,
                    guid=token.guid,
                    user_id=user.pk,
                    organization_id=tenant.organization_id,
                    team_id=token.team_id,
                    is_revoked=False,
                ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())),
                user="user",
                organization="organization",
            ).first()
            if fresh is None:
                raise PermissionDenied(
                    Permission.ORG_UPDATE, None, "Model hosting credential is unavailable."
                )
            marker = set_current_api_token(fresh)
        check_platform_operator(user, gate=Permission.ORG_UPDATE)
        yield user
    finally:
        if marker is not None:
            reset_current_api_token(marker)
