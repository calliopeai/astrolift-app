"""The cross-domain session handoff token (#1631).

This row is the only thing between "logged in at the auth host" and
"logged in at customer.com", so the rejections are the deliverable and the
happy path is the easy part. Each test below names the attack the property
prevents, because a property whose reason is not written down is a property
someone relaxes later to make a test pass.

Against real Postgres, and the replay race against real concurrent
connections -- a single-use guarantee proven only sequentially is the one
that fails in production.
"""

from __future__ import annotations

import datetime as dt
import threading

import pytest
from django.db import connection, connections
from django.utils import timezone

from astrolift_lifecycle.models import CustomDomain
from astrolift_lifecycle.models.domain_handoff import (
    HANDOFF_TTL,
    DomainSessionHandoff,
)
from core.domain_handoff import (
    HandoffRejection,
    consume_domain_handoff,
    mint_domain_handoff,
    purge_expired_handoffs,
)

pytestmark = pytest.mark.django_db

SUBJECT = "auth0|deadbeef"


@pytest.fixture
def domain(app):
    return CustomDomain.objects.create(
        registered_app=app,
        hostname="shop.customer.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        is_active=True,
    )


@pytest.fixture
def other_domain(app):
    return CustomDomain.objects.create(
        registered_app=app,
        hostname="portal.elsewhere.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        is_active=True,
    )


# ---------------------------------------------------------------------------
# The grant
# ---------------------------------------------------------------------------


def test_the_plaintext_is_returned_once_and_never_stored(domain):
    """A stored plaintext turns a database read into a session for every
    custom domain on the install."""
    plaintext = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)

    row = DomainSessionHandoff.objects.get()
    assert plaintext not in row.token_hash
    assert len(row.token_hash) == 64
    # Nothing anywhere on the row holds it.
    stored = {v for v in row.__dict__.values() if isinstance(v, str)}
    assert plaintext not in stored


def test_a_fresh_token_yields_the_verified_identity(domain):
    plaintext = mint_domain_handoff(custom_domain=domain, subject=SUBJECT, email="a@b.com")

    identity, reason = consume_domain_handoff(plaintext, hostname="shop.customer.com")

    assert reason is None
    assert identity is not None
    assert identity.subject == SUBJECT
    assert identity.email == "a@b.com"
    assert identity.hostname == "shop.customer.com"


def test_the_ttl_is_one_redirect_not_one_session(domain):
    """60 seconds. TaskToken's 72h is right for an agent run and would be a
    long window in which to replay a session grant."""
    assert HANDOFF_TTL == dt.timedelta(seconds=60)

    before = timezone.now()
    mint_domain_handoff(custom_domain=domain, subject=SUBJECT)
    row = DomainSessionHandoff.objects.get()

    assert row.expires_at - before <= dt.timedelta(seconds=61)


def test_a_blank_subject_is_refused_at_mint(domain):
    """A grant for nobody is still a grant: the domain's proxy would set a
    session for that nobody rather than refusing."""
    with pytest.raises(ValueError):
        mint_domain_handoff(custom_domain=domain, subject="   ")

    assert not DomainSessionHandoff.objects.exists()


# ---------------------------------------------------------------------------
# The rejections
# ---------------------------------------------------------------------------


def test_an_unknown_token_is_rejected(domain):
    identity, reason = consume_domain_handoff("not-a-token", hostname="shop.customer.com")

    assert identity is None
    assert reason == HandoffRejection.UNKNOWN_TOKEN


def test_an_expired_token_is_rejected(domain):
    plaintext = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)
    DomainSessionHandoff.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))

    identity, reason = consume_domain_handoff(plaintext, hostname="shop.customer.com")

    assert identity is None
    assert reason == HandoffRejection.EXPIRED


def test_a_token_for_one_host_is_rejected_at_another(domain, other_domain):
    """Without this, one gated domain is a session-minting oracle for every
    other domain on the cluster."""
    plaintext = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)

    identity, reason = consume_domain_handoff(plaintext, hostname="portal.elsewhere.com")

    assert identity is None
    assert reason == HandoffRejection.WRONG_AUDIENCE


def test_a_wrong_audience_does_not_burn_the_token(domain):
    """Order of checks, and the reason it is not arbitrary.

    If the consume ran before the audience check, anyone who could guess or
    replay a token at the wrong host would spend a legitimate user's grant
    and lock them out of their own login -- a denial of service wearing a
    security check's clothes.
    """
    plaintext = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)

    consume_domain_handoff(plaintext, hostname="attacker.example")

    assert DomainSessionHandoff.objects.get().consumed_at is None
    identity, reason = consume_domain_handoff(plaintext, hostname="shop.customer.com")
    assert reason is None, "the rightful owner must still be able to spend it"
    assert identity is not None


def test_a_replayed_token_is_rejected(domain):
    plaintext = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)

    first, _ = consume_domain_handoff(plaintext, hostname="shop.customer.com")
    second, reason = consume_domain_handoff(plaintext, hostname="shop.customer.com")

    assert first is not None
    assert second is None
    assert reason == HandoffRejection.ALREADY_CONSUMED


def test_a_deactivated_domain_stops_minting_sessions_immediately(domain):
    """Not at the end of the TTL of tokens already issued."""
    plaintext = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)
    CustomDomain.objects.filter(pk=domain.pk).update(is_active=False)

    identity, reason = consume_domain_handoff(plaintext, hostname="shop.customer.com")

    assert identity is None
    assert reason == HandoffRejection.DOMAIN_INACTIVE


def test_the_audience_is_compared_normalised(domain):
    """A trailing dot and a capital are the same hostname to a browser, and
    a comparison that disagrees would refuse a legitimate redirect."""
    plaintext = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)

    identity, reason = consume_domain_handoff(plaintext, hostname="SHOP.Customer.com.")

    assert reason is None
    assert identity is not None


def test_renaming_the_domain_row_does_not_widen_an_issued_tokens_audience(domain):
    """The hostname is denormalised at mint time for exactly this reason: a
    token minted for the old hostname must not become valid for the new one
    because an unrelated row was edited."""
    plaintext = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)
    CustomDomain.objects.filter(pk=domain.pk).update(hostname="renamed.customer.com")

    identity, reason = consume_domain_handoff(plaintext, hostname="renamed.customer.com")

    assert identity is None
    assert reason == HandoffRejection.WRONG_AUDIENCE


# ---------------------------------------------------------------------------
# The race
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_only_one_of_two_concurrent_exchanges_wins():
    """The reason single-use is a conditional UPDATE and not a read-then-write.

    Two threads on two real connections. A `SELECT` then `save()` lets both
    pass the "not consumed" check and both get a session, which is the
    failure a sequential replay test cannot see. Exactly one identity, and
    exactly one ALREADY_CONSUMED.
    """
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_registry.models import RegisteredApp

    org = Organization.objects.create(name="Race", slug="race-org")
    team = Team.objects.create(organization=org, name="T", slug="race-t")
    project = Project.objects.create(organization=org, team=team, name="P", slug="race-p")
    app = RegisteredApp.objects.create(
        organization=org, project=project, team=team, name="R", slug="race-app"
    )
    cd = CustomDomain.objects.create(registered_app=app, hostname="race.customer.com", is_active=True)
    plaintext = mint_domain_handoff(custom_domain=cd, subject=SUBJECT)

    results: list[tuple] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def attempt():
        try:
            barrier.wait(timeout=10)
            out = consume_domain_handoff(plaintext, hostname="race.customer.com")
            with lock:
                results.append(out)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 2, "both threads must have completed"
    granted = [identity for identity, _ in results if identity is not None]
    refused = [reason for _, reason in results if reason is not None]

    assert len(granted) == 1, f"exactly one session, got {len(granted)}"
    assert refused == [HandoffRejection.ALREADY_CONSUMED]


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------


def test_purge_removes_expired_rows_and_leaves_live_ones(domain, other_domain):
    live = mint_domain_handoff(custom_domain=domain, subject=SUBJECT)
    mint_domain_handoff(custom_domain=other_domain, subject=SUBJECT)
    DomainSessionHandoff.objects.filter(hostname=other_domain.hostname).update(
        expires_at=timezone.now() - dt.timedelta(minutes=5)
    )

    assert purge_expired_handoffs() == 1
    identity, reason = consume_domain_handoff(live, hostname="shop.customer.com")
    assert reason is None and identity is not None


def test_the_consume_is_a_single_conditional_update():
    """Guards the mechanism, not just its effect.

    A future refactor to `SELECT ... then save()` keeps every test above
    green -- the sequential replay still fails, the race test is the only
    one that catches it, and a flaky-looking concurrency test is the kind
    that gets marked skip. So assert the SQL shape directly.
    """
    import inspect

    import core.domain_handoff as mod

    source = inspect.getsource(mod.consume_domain_handoff)
    assert "consumed_at__isnull=True" in source, (
        "the consume must filter on consumed_at being null inside the UPDATE, "
        "not read the row and then write it"
    )
    assert ".update(consumed_at=" in source
    assert "select_for_update" not in source, (
        "a row lock would serialise the login path; the conditional UPDATE "
        "already makes the database the single decider"
    )


def test_the_grant_carries_no_identity_in_what_the_browser_sees(domain):
    """The browser gets an opaque id and nothing else. A token that encodes
    the subject is a token whose holder learns who it is for, and a URL in
    a proxy log or a Referer header then leaks an identity as well as a
    grant."""
    plaintext = mint_domain_handoff(custom_domain=domain, subject=SUBJECT, email="a@b.com")

    assert SUBJECT not in plaintext
    assert "a@b.com" not in plaintext
    assert "customer.com" not in plaintext
    # urlsafe base64 only -- it has to survive a query string intact.
    assert all(c.isalnum() or c in "-_" for c in plaintext)


def test_the_table_is_indexed_for_the_lookup_the_login_path_makes():
    """token_hash is the hot lookup and it is on the login path, so a
    sequential scan here is a login-latency regression at scale."""
    field = DomainSessionHandoff._meta.get_field("token_hash")
    assert field.unique and field.db_index
    assert connection is not None
