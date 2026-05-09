"""Tests for session cookie + sign-out policy (#272, spec 12 §2.2 + §2.4)."""

from __future__ import annotations

import pytest

from auth1.session_cookies import (
    CookieError,
    CookieKind,
    SignoutPlan,
    SignoutTrigger,
    access_cookie_name,
    build_clear_cookie,
    build_cookie_attributes,
    parent_domain,
    plan_signout,
    refresh_cookie_name,
)


# ---- cookie names --------------------------------------------------


def test_access_cookie_default_name():
    assert access_cookie_name() == "alft_access"


def test_access_cookie_with_install_slug():
    """Multiple installs on overlapping zones: distinguish by
    install_slug to avoid shadow cookies."""
    assert access_cookie_name(install_slug="acme") == "alft_acme_access"


def test_refresh_cookie_default_name():
    assert refresh_cookie_name() == "alft_refresh"


def test_refresh_cookie_with_install_slug():
    assert refresh_cookie_name(install_slug="acme") == "alft_acme_refresh"


# ---- parent domain -------------------------------------------------


def test_parent_domain_basic():
    assert parent_domain(
        install_zone="acme.platform.example",
    ) == "acme.platform.example"


def test_parent_domain_lowercases_and_strips_dots():
    assert parent_domain(
        install_zone="ACME.platform.example.",
    ) == "acme.platform.example"


def test_parent_domain_rejects_short_zone():
    """Single-label zone would set Domain= broader than safe."""
    with pytest.raises(CookieError, match="2 labels"):
        parent_domain(install_zone="example")


def test_parent_domain_rejects_empty():
    with pytest.raises(CookieError):
        parent_domain(install_zone="")


def test_parent_domain_rejects_invalid_label():
    """Underscore not RFC 1035-valid."""
    with pytest.raises(CookieError, match="invalid label"):
        parent_domain(install_zone="my_zone.example.com")


# ---- cookie attributes ---------------------------------------------


def test_access_cookie_basic():
    attrs = build_cookie_attributes(
        kind=CookieKind.ACCESS,
        token_value="alft_access_xyz",
        install_zone="acme.platform.example",
        ttl_seconds=600,
    )
    assert attrs.name == "alft_access"
    assert attrs.value == "alft_access_xyz"
    assert attrs.path == "/"
    assert attrs.secure is True
    assert attrs.http_only is True
    assert attrs.same_site == "Lax"
    assert attrs.max_age_seconds == 600


def test_refresh_cookie_path_scoped():
    """Refresh cookie only ships to the refresh endpoint."""
    attrs = build_cookie_attributes(
        kind=CookieKind.REFRESH,
        token_value="alft_refresh_xyz",
        install_zone="acme.platform.example",
        ttl_seconds=86400,
    )
    assert attrs.path == "/api/v1/auth/refresh"


def test_cookie_with_install_slug():
    attrs = build_cookie_attributes(
        kind=CookieKind.ACCESS,
        token_value="x",
        install_zone="z.example.com",
        install_slug="acme",
        ttl_seconds=60,
    )
    assert attrs.name == "alft_acme_access"


def test_cookie_secure_overridable_for_tests():
    """secure=False is for local dev / tests over plain HTTP only."""
    attrs = build_cookie_attributes(
        kind=CookieKind.ACCESS,
        token_value="x",
        install_zone="local.example.com",
        ttl_seconds=60,
        secure=False,
    )
    assert attrs.secure is False


def test_cookie_rejects_empty_token():
    with pytest.raises(CookieError, match="token_value"):
        build_cookie_attributes(
            kind=CookieKind.ACCESS,
            token_value="",
            install_zone="z.example.com",
            ttl_seconds=60,
        )


def test_cookie_rejects_zero_ttl():
    with pytest.raises(CookieError, match="ttl"):
        build_cookie_attributes(
            kind=CookieKind.ACCESS,
            token_value="x",
            install_zone="z.example.com",
            ttl_seconds=0,
        )


# ---- clear cookie --------------------------------------------------


def test_clear_cookie_max_age_zero():
    attrs = build_clear_cookie(
        kind=CookieKind.ACCESS,
        install_zone="acme.platform.example",
    )
    assert attrs.max_age_seconds == 0
    assert attrs.value == ""


def test_clear_cookie_preserves_path():
    """Differing path = browser keeps the original cookie.
    Clear must use same Domain + Path as the set-cookie."""
    refresh_clear = build_clear_cookie(
        kind=CookieKind.REFRESH,
        install_zone="acme.platform.example",
    )
    assert refresh_clear.path == "/api/v1/auth/refresh"


# ---- signout cascade ----------------------------------------------


def test_signout_user_initiated_local_only():
    plan = plan_signout(
        trigger=SignoutTrigger.USER_INITIATED,
        is_oidc_session=False,
    )
    assert plan.revoke_local_session is True
    assert plan.revoke_all_user_sessions is False
    assert plan.redirect_to_idp_logout is False


def test_signout_user_initiated_oidc_redirects():
    """OIDC-backed session: redirect to IdP logout for full
    sign-out flow."""
    plan = plan_signout(
        trigger=SignoutTrigger.USER_INITIATED,
        is_oidc_session=True,
    )
    assert plan.revoke_local_session is True
    assert plan.redirect_to_idp_logout is True


def test_signout_logout_all_revokes_all():
    plan = plan_signout(
        trigger=SignoutTrigger.LOGOUT_ALL, is_oidc_session=False,
    )
    assert plan.revoke_all_user_sessions is True
    # Don't redirect to IdP for logout-all (might not be OIDC for
    # all sessions)
    assert plan.redirect_to_idp_logout is False


def test_signout_replay_revokes_chain():
    """Refresh token replay = entire chain compromised → revoke
    parent + descendants per #147's revoke_chain."""
    plan = plan_signout(
        trigger=SignoutTrigger.REPLAY_DETECTED,
        is_oidc_session=False,
    )
    assert plan.revoke_chain is True
    assert "replay" in plan.audit_reason


def test_signout_back_channel_revokes_local_only():
    """IdP notified us; we revoke the session, no need to bounce
    user to IdP again."""
    plan = plan_signout(
        trigger=SignoutTrigger.BACK_CHANNEL_OIDC,
        is_oidc_session=True,
    )
    assert plan.revoke_local_session is True
    assert plan.redirect_to_idp_logout is False


def test_signout_session_expired():
    plan = plan_signout(
        trigger=SignoutTrigger.SESSION_EXPIRED,
        is_oidc_session=False,
    )
    assert plan.revoke_local_session is True
    assert plan.audit_reason == "session expired"


def test_audit_reason_present_for_every_trigger():
    """Every sign-out emits an audit event; reason must be
    non-empty so the event is meaningful."""
    for trigger in SignoutTrigger:
        plan = plan_signout(trigger=trigger, is_oidc_session=False)
        assert plan.audit_reason
