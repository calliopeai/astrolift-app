"""Sentry events keep no credential a URL carries (#2440).

The IdP callback (``/app/auth1/callback?code=...``), a sign-in hand-off code,
a ``token=`` or ``*_token=`` parameter and an invitation link reach Sentry in
the request's query string, its URL, its Referer, breadcrumbs and a
transaction's spans. ``before_send`` and ``before_send_transaction`` redact
them as the request logs are redacted (#2429).
"""

from __future__ import annotations

import sentry_sdk
from django.conf import settings

from core.logging import redact_event_url_secrets
from core.tests.test_secret_leak_channels_1920 import _events, _sentry_client

SECRET = "url-secret-canary"


def _capture_callback_error(client: sentry_sdk.Client) -> str:
    with sentry_sdk.new_scope() as scope:
        scope.set_client(client)
        sentry_sdk.add_breadcrumb(category="http", data={"url": f"/auth/callback?token=Session%20{SECRET}"})
        # The request as the Django integration's event processor records it.
        event: dict = {
            "message": f"callback failed for /login?next=%2Fauth%2Finvitation%2F{SECRET}",
            "request": {
                "url": "https://astro.example/app/auth1/callback",
                "query_string": f"code={SECRET}&state=s",
                "headers": {"Referer": f"https://astro.example/auth/invitation/{SECRET}"},
            },
        }
        sentry_sdk.capture_event(event)
    return _events(client)


def _capture_transaction(client: sentry_sdk.Client) -> str:
    with sentry_sdk.new_scope() as scope:
        scope.set_client(client)
        with sentry_sdk.start_transaction(name="/app/auth1/callback", sampled=True) as transaction:
            transaction.set_data("url", f"https://astro.example/app/auth1/callback?code={SECRET}")
            with sentry_sdk.start_span(op="http.client", name=f"GET /x?refresh_token={SECRET}"):
                pass
    return _events(client)


def test_error_events_carry_no_url_credential() -> None:
    assert SECRET in _capture_callback_error(_sentry_client({}))
    sent = _capture_callback_error(_sentry_client(settings.SENTRY_PRIVACY_OPTIONS))
    assert SECRET not in sent
    assert "state=s" in sent


def test_transactions_carry_no_url_credential() -> None:
    assert SECRET in _capture_transaction(_sentry_client({"traces_sample_rate": 1.0}))
    sent = _capture_transaction(
        _sentry_client({"traces_sample_rate": 1.0, **settings.SENTRY_PRIVACY_OPTIONS})
    )
    assert SECRET not in sent
    assert "/app/auth1/callback" in sent


def test_the_query_string_is_redacted_without_its_question_mark() -> None:
    event = {"request": {"query_string": f"code={SECRET}&page=2"}, "extra": {"exit": "code=1"}}
    assert redact_event_url_secrets(event, {}) == {
        "request": {"query_string": "code=***REDACTED***&page=2"},
        "extra": {"exit": "code=1"},
    }
