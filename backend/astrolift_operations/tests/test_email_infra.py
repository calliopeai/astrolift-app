"""Tests for platform email infrastructure (#135)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from astrolift_operations.email_infra import (
    SUPPORTED_TRANSPORTS,
    Email,
    EmailError,
    EmailKind,
    SuppressionEntry,
    SuppressionReason,
    TransportKind,
    clear_transport,
    configured_transport,
    is_configured,
    is_suppressed,
    is_valid_email,
    send,
    set_transport,
)


@pytest.fixture(autouse=True)
def _clean():
    clear_transport()
    yield
    clear_transport()


def _email(**kw) -> Email:
    base = dict(
        to_address="ops@acme.com",
        subject="Approval needed",
        html_body="<p>x</p>",
        plain_body="x",
        from_address="bot@acme.platform",
        kind=EmailKind.DEPLOY_APPROVAL,
    )
    base.update(kw)
    return Email(**base)


# ---- email validation ----------------------------------------------


@pytest.mark.parametrize("addr", [
    "ops@acme.com",
    "first.last+tag@acme.com",
    "x@y.io",
])
def test_valid_emails(addr):
    assert is_valid_email(addr) is True


@pytest.mark.parametrize("bad", [
    "",
    "not-an-email",
    "@missing.local",
    "missing@.com",
    "two@at@signs.com",
    "no-tld@acme",
])
def test_invalid_emails(bad):
    assert is_valid_email(bad) is False


def test_email_construction_validates_to_and_from():
    with pytest.raises(EmailError, match="to_address"):
        _email(to_address="not-an-email")
    with pytest.raises(EmailError, match="from_address"):
        _email(from_address="not-an-email")


def test_email_requires_subject():
    with pytest.raises(EmailError, match="subject"):
        _email(subject="")


def test_email_requires_plain_body():
    """RFC 8551 multipart/alternative — plain text is mandatory
    for screen readers + plain-text MUAs."""
    with pytest.raises(EmailError, match="plain_body"):
        _email(plain_body="")


# ---- transport registry --------------------------------------------


def test_send_without_transport_raises():
    """Self-hosted install without configured provider — refuse
    the send so admin sees the gap, never silently drop."""
    with pytest.raises(EmailError, match="no email transport"):
        send(email=_email(), suppression_lookup=lambda a: None)


def test_set_transport_then_send():
    sent: list[Email] = []
    set_transport(
        kind=TransportKind.AWS_SES,
        fn=lambda e: sent.append(e),
    )
    assert is_configured() is True
    assert configured_transport() == TransportKind.AWS_SES

    send(email=_email(), suppression_lookup=lambda a: None)
    assert len(sent) == 1
    assert sent[0].to_address == "ops@acme.com"


def test_cant_set_none_transport():
    """NONE is the unconfigured state, not a transport you can
    register."""
    with pytest.raises(EmailError):
        set_transport(kind=TransportKind.NONE, fn=lambda e: None)


def test_unsupported_transport_kind_rejected():
    """If a future enum value gets added but no transport is
    written for it, the registry rejects it loudly."""
    with pytest.raises(EmailError, match="unsupported"):
        set_transport(
            kind="weird-future-thing",  # type: ignore[arg-type]
            fn=lambda e: None,
        )


def test_supported_transports_locked():
    """Lock-test the supported list — adding a transport requires
    a deliberate code review."""
    assert SUPPORTED_TRANSPORTS == (
        TransportKind.AWS_SES, TransportKind.SENDGRID,
        TransportKind.POSTMARK, TransportKind.SMTP,
    )


# ---- suppression list ----------------------------------------------


def _entry(addr: str, reason: SuppressionReason) -> SuppressionEntry:
    return SuppressionEntry(
        address=addr, reason=reason,
        suppressed_at=datetime(2026, 5, 1, tzinfo=UTC),
    )


def test_hard_bounce_suppresses_all_kinds():
    """Provider rate-limits us if we keep hammering bounced
    addresses."""
    entry = _entry("dead@nowhere.com", SuppressionReason.HARD_BOUNCE)
    lookup = lambda a: entry if a == "dead@nowhere.com" else None
    for kind in EmailKind:
        assert is_suppressed(
            address="dead@nowhere.com", kind=kind,
            suppression_lookup=lookup,
        ) is True


def test_complaint_suppresses_all_kinds():
    entry = _entry("noreply@acme.com", SuppressionReason.COMPLAINT)
    lookup = lambda a: entry if a == "noreply@acme.com" else None
    for kind in EmailKind:
        assert is_suppressed(
            address="noreply@acme.com", kind=kind,
            suppression_lookup=lookup,
        ) is True


def test_unsubscribe_suppresses_non_transactional():
    """Marketing-shaped sends respect unsubscribe."""
    entry = _entry("user@acme.com", SuppressionReason.UNSUBSCRIBE)
    lookup = lambda a: entry if a == "user@acme.com" else None

    # Failure alerts are non-transactional? Spec says
    # DEPLOY_APPROVAL is transactional (security signal). Other
    # kinds (failure alerts, invitations) respect unsubscribe.
    for non_transactional in (
        EmailKind.MEMBER_INVITATION,
        EmailKind.DEPLOY_FAILURE,
        EmailKind.SCHEDULED_JOB_FAILURE,
    ):
        assert is_suppressed(
            address="user@acme.com", kind=non_transactional,
            suppression_lookup=lookup,
        ) is True


def test_unsubscribe_does_not_suppress_deploy_approval():
    """Deploy approval is a security signal — user can't opt out
    while keeping their account active."""
    entry = _entry("user@acme.com", SuppressionReason.UNSUBSCRIBE)
    lookup = lambda a: entry if a == "user@acme.com" else None
    assert is_suppressed(
        address="user@acme.com",
        kind=EmailKind.DEPLOY_APPROVAL,
        suppression_lookup=lookup,
    ) is False


def test_no_suppression_when_address_not_in_list():
    lookup = lambda a: None
    assert is_suppressed(
        address="any@acme.com",
        kind=EmailKind.DEPLOY_FAILURE,
        suppression_lookup=lookup,
    ) is False


# ---- send + suppression interaction --------------------------------


def test_send_short_circuits_on_suppression():
    """Suppressed addresses don't reach the transport. Saves
    provider quota + reputation."""
    sent: list[Email] = []
    set_transport(
        kind=TransportKind.AWS_SES, fn=lambda e: sent.append(e),
    )
    entry = _entry("dead@nowhere.com", SuppressionReason.HARD_BOUNCE)
    lookup = lambda a: entry if a == "dead@nowhere.com" else None

    send(
        email=_email(to_address="dead@nowhere.com"),
        suppression_lookup=lookup,
    )
    assert sent == []  # transport never invoked


def test_send_passes_through_for_unsuppressed_address():
    sent: list[Email] = []
    set_transport(
        kind=TransportKind.AWS_SES, fn=lambda e: sent.append(e),
    )
    send(email=_email(), suppression_lookup=lambda a: None)
    assert len(sent) == 1
