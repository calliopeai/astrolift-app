"""Refresh a request credential before a locked external dispatch recheck."""

from contextlib import contextmanager

from django.db.models import Q
from django.utils import timezone

from core.permissions import PermissionDenied
from core.tenancy import get_current_tenant


@contextmanager
def current_dispatch_credential(permission):
    from astrolift_identity.api_tokens import (
        get_current_api_token,
        reset_current_api_token,
        set_current_api_token,
    )
    from astrolift_identity.models import ApiToken

    credential = get_current_api_token()
    state = None
    tenant = get_current_tenant()
    if tenant is not None and tenant.actor_user_id is not None:
        from django.contrib.auth import get_user_model

        from astrolift_identity.models import Organization

        if (
            not get_user_model().objects.filter(pk=tenant.actor_user_id, is_active=True).exists()
            or not Organization.objects.filter(pk=tenant.organization_id).exists()
        ):
            raise PermissionDenied(permission, None, "the dispatch actor or organization is unavailable")
    if credential is not None:
        fresh = (
            ApiToken.objects.filter(
                pk=getattr(credential, "pk", None),
                organization_id=tenant.organization_id if tenant else None,
                user_id=tenant.actor_user_id if tenant else None,
                user__is_active=True,
                is_revoked=False,
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
            .first()
        )
        if fresh is None:
            raise PermissionDenied(permission, None, "api token is no longer valid")
        state = set_current_api_token(fresh)
    try:
        yield
    finally:
        if state is not None:
            reset_current_api_token(state)
