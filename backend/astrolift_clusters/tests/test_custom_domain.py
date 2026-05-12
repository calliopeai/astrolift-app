"""Tests for custom domain lifecycle policy (#57, spec 13 §2.2-2.3)."""

from __future__ import annotations

import pytest

from astrolift_clusters.custom_domain import (
    TXT_CHALLENGE_NAME_PREFIX,
    VALIDATION_TIMEOUT_SECONDS,
    DomainError,
    DomainState,
    assert_transition,
    can_transition,
    dns_instruction,
    is_apex,
    is_valid_hostname,
    issue_txt_challenge,
    verify_txt_challenge,
)

# ---- hostname validation -------------------------------------------


@pytest.mark.parametrize(
    "hostname",
    [
        "acme.com",
        "api.acme.com",
        "deep.api.acme.com",
        "abc-def.acme.com",
        "a.io",
        "long.subdomain.with.many.labels.acme.com",
    ],
)
def test_valid_hostnames(hostname):
    assert is_valid_hostname(hostname) is True


@pytest.mark.parametrize(
    "hostname",
    [
        "",
        "not-a-domain",
        ".acme.com",  # leading dot
        "acme.com.",  # trailing dot
        "-acme.com",  # leading hyphen on label
        "acme-.com",  # trailing hyphen on label
        "acme..com",  # empty label
        "acme.c",  # TLD too short (1 char)
        "a" * 254,  # too long
    ],
)
def test_invalid_hostnames(hostname):
    assert is_valid_hostname(hostname) is False


def test_hostname_validation_is_case_insensitive():
    """RFC 1035 hostnames are case-insensitive; tenant might paste
    ``ACME.COM``. Validity check accepts mixed case — caller
    lowercases before storage."""
    assert is_valid_hostname("ACME.COM") is True
    assert is_valid_hostname("API.Acme.com") is True


# ---- apex detection ------------------------------------------------


@pytest.mark.parametrize("apex_host", ["acme.com", "example.io", "test.org"])
def test_apex_hosts_detected(apex_host):
    assert is_apex(apex_host) is True


@pytest.mark.parametrize(
    "subdomain_host",
    [
        "api.acme.com",
        "www.acme.com",
        "deep.nested.acme.com",
    ],
)
def test_subdomain_hosts_detected(subdomain_host):
    assert is_apex(subdomain_host) is False


def test_apex_check_rejects_invalid_hostname():
    with pytest.raises(DomainError):
        is_apex("not-a-domain")


# ---- TXT challenge -------------------------------------------------


def test_issue_challenge_format():
    out = issue_txt_challenge(hostname="api.acme.com")
    assert out.record_name == f"{TXT_CHALLENGE_NAME_PREFIX}.api.acme.com"
    assert out.record_value
    assert len(out.record_value) >= 30  # 32 bytes base64url ≈ 43 chars


def test_issue_challenge_for_apex():
    out = issue_txt_challenge(hostname="acme.com")
    assert out.record_name == f"{TXT_CHALLENGE_NAME_PREFIX}.acme.com"


def test_issue_challenge_lowercases_hostname():
    """Tenant might enter mixed-case; record name normalizes."""
    out = issue_txt_challenge(hostname="API.Acme.com")
    assert out.record_name == f"{TXT_CHALLENGE_NAME_PREFIX}.api.acme.com"


def test_issue_challenge_distinct_per_call():
    """Each issue produces a fresh token."""
    a = issue_txt_challenge(hostname="api.acme.com")
    b = issue_txt_challenge(hostname="api.acme.com")
    assert a.record_value != b.record_value


def test_issue_challenge_rejects_invalid_hostname():
    with pytest.raises(DomainError):
        issue_txt_challenge(hostname="invalid")


def test_verify_challenge_matches_in_set():
    """Multiple TXT records at the same name is normal — match
    against any in the set."""
    challenge = issue_txt_challenge(hostname="api.acme.com")
    observed = [
        "google-site-verification=abc",
        "v=spf1 -all",
        challenge.record_value,
    ]
    assert (
        verify_txt_challenge(
            expected=challenge,
            observed_records=observed,
        )
        is True
    )


def test_verify_challenge_rejects_when_missing():
    challenge = issue_txt_challenge(hostname="api.acme.com")
    assert (
        verify_txt_challenge(
            expected=challenge,
            observed_records=["unrelated"],
        )
        is False
    )


def test_verify_challenge_rejects_when_no_records():
    challenge = issue_txt_challenge(hostname="api.acme.com")
    assert (
        verify_txt_challenge(
            expected=challenge,
            observed_records=[],
        )
        is False
    )


# ---- state transitions ---------------------------------------------


def test_pending_can_progress_to_validating_or_error():
    assert (
        can_transition(
            from_state=DomainState.PENDING_VALIDATION,
            to_state=DomainState.VALIDATING,
        )
        is True
    )
    assert (
        can_transition(
            from_state=DomainState.PENDING_VALIDATION,
            to_state=DomainState.ERROR,
        )
        is True
    )


def test_pending_cannot_skip_to_active():
    """Must validate first."""
    assert (
        can_transition(
            from_state=DomainState.PENDING_VALIDATION,
            to_state=DomainState.ACTIVE,
        )
        is False
    )


def test_active_can_expire_or_error():
    assert (
        can_transition(
            from_state=DomainState.ACTIVE,
            to_state=DomainState.EXPIRED,
        )
        is True
    )
    assert (
        can_transition(
            from_state=DomainState.ACTIVE,
            to_state=DomainState.ERROR,
        )
        is True
    )


def test_error_can_retry_to_pending():
    """Tenant can fix their DNS and retry."""
    assert (
        can_transition(
            from_state=DomainState.ERROR,
            to_state=DomainState.PENDING_VALIDATION,
        )
        is True
    )


def test_idempotent_same_state_allowed():
    """Re-firing the same state (workflow retry) is a no-op."""
    for s in DomainState:
        assert can_transition(from_state=s, to_state=s) is True


def test_active_cannot_go_back_to_validating():
    """Once active, must go through expired/error first to retry."""
    assert (
        can_transition(
            from_state=DomainState.ACTIVE,
            to_state=DomainState.VALIDATING,
        )
        is False
    )


def test_assert_transition_raises_on_invalid():
    with pytest.raises(DomainError, match="invalid domain state"):
        assert_transition(
            from_state=DomainState.PENDING_VALIDATION,
            to_state=DomainState.ACTIVE,
        )


def test_validation_timeout_matches_spec():
    """Spec 13 §2.2: 10 minutes from CNAME setup to validation
    completion."""
    assert VALIDATION_TIMEOUT_SECONDS == 600


# ---- DNS instruction copy ------------------------------------------


def test_subdomain_instruction_uses_cname():
    out = dns_instruction(
        hostname="api.acme.com",
        ingress_target="ingress.platform.example.com",
    )
    assert "CNAME" in out
    assert "api.acme.com" in out
    assert "ingress.platform.example.com" in out


def test_apex_instruction_uses_alias_aname():
    """Spec 13 §2.3: apex needs ALIAS/ANAME (RFC 1034 forbids
    plain CNAME at zone apex)."""
    out = dns_instruction(
        hostname="acme.com",
        ingress_target="ingress.platform.example.com",
    )
    assert "ALIAS" in out or "ANAME" in out
    assert "Cloudflare" in out  # workaround mentioned


def test_dns_instruction_rejects_invalid_hostname():
    with pytest.raises(DomainError):
        dns_instruction(hostname="bad", ingress_target="x")
