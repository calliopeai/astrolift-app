"""``require_platform_operator`` bearer-token scope ceiling (#1949).

The legacy boilerworks surfaces this gate protects (generic hard delete,
editing another user's profile, Django group membership) have no
``Permission`` of their own, so ``check_permission``'s api-token ceiling
(``token_scope_allows_permission``) never applies to them. Without an
explicit check here, a read-only token minted for the operator -- a
mobile enrollment token, say -- would carry full operator power. Session
callers (no API token in the request context) are unaffected.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from astrolift_identity.api_tokens import (
    SCOPE_ADMIN,
    SCOPE_READ_APPS,
    reset_current_api_token,
    set_current_api_token,
)
from core.permissions import require_platform_operator

User = get_user_model()
pytestmark = pytest.mark.django_db


def _operator():
    return User.objects.create_superuser(username="op-1949", email="op-1949@t.test", password="x")


def test_session_operator_is_unaffected():
    """No API token in context -- the session case -- is unchanged."""
    require_platform_operator(_operator())


def test_bearer_operator_with_admin_scope_is_allowed():
    marker = set_current_api_token(SimpleNamespace(scopes=[SCOPE_ADMIN]))
    try:
        require_platform_operator(_operator())
    finally:
        reset_current_api_token(marker)


def test_bearer_operator_without_admin_scope_is_refused():
    marker = set_current_api_token(SimpleNamespace(scopes=[SCOPE_READ_APPS]))
    try:
        with pytest.raises(PermissionDenied):
            require_platform_operator(_operator())
    finally:
        reset_current_api_token(marker)


def test_bearer_non_operator_is_refused_regardless_of_scope():
    """The ceiling only narrows an operator's token; a non-operator stays
    refused whatever the token carries. Not the regression this finding is
    about, but worth pinning alongside it."""
    plain = User.objects.create_user(username="plain-1949", email="plain-1949@t.test", password="x")
    marker = set_current_api_token(SimpleNamespace(scopes=[SCOPE_ADMIN]))
    try:
        with pytest.raises(PermissionDenied):
            require_platform_operator(plain)
    finally:
        reset_current_api_token(marker)
