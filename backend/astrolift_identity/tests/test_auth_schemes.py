"""Tests for Control API auth scheme classifier (#89, spec 27 §9)."""

from __future__ import annotations

import pytest

from astrolift_identity.auth_schemes import (
    AuthScheme,
    AuthSchemeError,
    classify,
    is_introspection_authorized,
)

# ---- single-scheme rule --------------------------------------------


def test_no_credentials_rejected():
    with pytest.raises(AuthSchemeError, match="no credential"):
        classify(path="/api/v1/apps", headers={}, cookies={})


def test_multiple_credentials_rejected():
    """Cookie + Bearer is ambiguous (CSRF risk if we tolerate);
    reject."""
    with pytest.raises(AuthSchemeError, match="multiple credentials"):
        classify(
            path="/api/v1/apps",
            headers={"Authorization": "Bearer alft_at_xxx"},
            cookies={"astrolift_session": "abc"},
        )


# ---- mTLS ----------------------------------------------------------


def test_mtls_classified_when_cert_fingerprint_present():
    out = classify(
        path="/api/internal/v1/probe",
        headers={},
        cookies={},
        client_cert_fingerprint="sha256:abc1234",
    )
    assert out.scheme == AuthScheme.MTLS
    assert out.raw_credential == "sha256:abc1234"


def test_mtls_takes_precedence_when_combined_rejected():
    """Cert + bearer is still 'multiple credentials'."""
    with pytest.raises(AuthSchemeError):
        classify(
            path="/api/v1/x",
            headers={"Authorization": "Bearer alft_at_xxx"},
            cookies={},
            client_cert_fingerprint="sha256:abc",
        )


# ---- session cookie ------------------------------------------------


def test_session_cookie_classified():
    out = classify(
        path="/api/v1/apps",
        headers={},
        cookies={"astrolift_session": "session-bytes"},
    )
    assert out.scheme == AuthScheme.SESSION_COOKIE
    assert out.is_browser_request is True


def test_other_cookies_dont_count():
    """Some unrelated cookie shouldn't classify as session."""
    with pytest.raises(AuthSchemeError, match="no credential"):
        classify(
            path="/api/v1/apps",
            headers={},
            cookies={"unrelated": "x"},
        )


# ---- bearer disambiguation -----------------------------------------


def test_api_token_prefix_classified():
    out = classify(
        path="/api/v1/apps",
        headers={"Authorization": "Bearer alft_at_secret123"},
    )
    assert out.scheme == AuthScheme.API_TOKEN


def test_deploy_token_prefix_classified():
    out = classify(
        path="/api/v1/apps",
        headers={"Authorization": "Bearer alft_dt_secret123"},
    )
    assert out.scheme == AuthScheme.DEPLOY_TOKEN


def test_oidc_jwt_classified_by_shape():
    """JWT structural pattern (3 base64url segments) → OIDC.
    Our local tokens never look JWT-shaped."""
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.signature_segment"
    out = classify(
        path="/api/v1/apps",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert out.scheme == AuthScheme.OIDC


def test_unknown_token_format_rejected():
    """No prefix match, not JWT-shape — reject so we don't
    silently fall through to a default."""
    with pytest.raises(AuthSchemeError, match="not recognized"):
        classify(
            path="/api/v1/apps",
            headers={"Authorization": "Bearer random-bytes"},
        )


def test_authorization_header_case_insensitive():
    """RFC 7230: HTTP headers case-insensitive."""
    out = classify(
        path="/api/v1/apps",
        headers={"authorization": "Bearer alft_at_x"},
    )
    assert out.scheme == AuthScheme.API_TOKEN


def test_bearer_prefix_required():
    """Other auth schemes (Basic, Digest) → not classified as
    bearer; rejected as 'no credential' (or other code path)."""
    with pytest.raises(AuthSchemeError):
        classify(
            path="/api/v1/x",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )


# ---- SCIM path scoping ---------------------------------------------


def test_scim_token_at_scim_path_classified():
    out = classify(
        path="/api/scim/v2/Users",
        headers={"Authorization": "Bearer alft_st_scimsecret"},
    )
    assert out.scheme == AuthScheme.SCIM


def test_scim_token_at_non_scim_path_rejected():
    """SCIM tokens must be path-scoped — at any other endpoint
    they're rejected. Catches misuse of the dedicated SCIM
    credential."""
    with pytest.raises(AuthSchemeError, match="SCIM token"):
        classify(
            path="/api/v1/apps",
            headers={"Authorization": "Bearer alft_st_scimsecret"},
        )


def test_non_scim_token_at_scim_path_rejected():
    """The reverse: an API token shouldn't be valid against the
    SCIM endpoints. Tightens the credential separation."""
    with pytest.raises(AuthSchemeError, match="non-SCIM token"):
        classify(
            path="/api/scim/v2/Users",
            headers={"Authorization": "Bearer alft_at_apitoken"},
        )


# ---- introspection authorization -----------------------------------


def test_introspection_admin_authorized():
    """Admin elevation is sufficient regardless of MFA."""
    assert is_introspection_authorized(
        requester_amr=("pwd",), requester_is_admin=True,
    ) is True


def test_introspection_strong_mfa_authorized():
    """Strong MFA without admin is also sufficient."""
    assert is_introspection_authorized(
        requester_amr=("otp",), requester_is_admin=False,
    ) is True
    assert is_introspection_authorized(
        requester_amr=("webauthn",), requester_is_admin=False,
    ) is True


def test_introspection_password_only_rejected():
    """Plain password session can't introspect tokens. The
    operation reveals which app/user a token belongs to —
    sensitive enough to require elevated context."""
    assert is_introspection_authorized(
        requester_amr=("pwd",), requester_is_admin=False,
    ) is False


def test_introspection_sms_not_strong_enough():
    """Spec considers SMS not strong; only otp/webauthn/hwk
    qualify as 'strong MFA' (matches #148 step-up policy)."""
    assert is_introspection_authorized(
        requester_amr=("sms",), requester_is_admin=False,
    ) is False
