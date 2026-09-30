"""Account gates for legacy fields that have no tenant permission slug."""

from django.core.exceptions import PermissionDenied


def require_account_access(info, *, write=False):
    from astrolift_identity.api_tokens import SCOPE_ADMIN, get_current_api_token, has_scope

    user = info.context.user
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Authentication required")
    if not getattr(user, "is_active", False):
        raise PermissionDenied("Active account required")
    token = get_current_api_token()
    if write and token is not None and not has_scope(token, SCOPE_ADMIN):
        raise PermissionDenied("Legacy account writes require an admin token")
    return user


def is_operator_with_credential(user):
    from django.core.exceptions import PermissionDenied

    from core.permissions import require_platform_operator

    try:
        require_platform_operator(user)
    except PermissionDenied:
        return False
    return True
