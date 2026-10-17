"""Request logs keep no sign-in code, token or invitation token (#2429).

uvicorn's access log writes every request's path and query, and CloudWatch
kept them: IdP authorization codes on ``/app/auth1/callback``, and invitation
tokens inside the ``next`` of ``/app/auth1/login``. The session keys the old
``?token=`` hand-off put on the frontend's URL reached the logs the same way.
"""

from __future__ import annotations

import io
import logging

import pytest
from uvicorn.logging import AccessFormatter

from core.logging import UrlSecretFilter, redact_url_secrets

R = "***REDACTED***"


@pytest.mark.parametrize(
    ("raw", "redacted"),
    [
        # The IdP's authorization code on the callback, beside its state.
        (
            "/app/auth1/callback?code=4f1c9e2a-77&state=Zq9x",
            f"/app/auth1/callback?code={R}&state=Zq9x",
        ),
        # The old hand-off, and the new one.
        (
            "/auth/callback?next=%2Fdashboard&token=Session+k3y",
            f"/auth/callback?next=%2Fdashboard&token={R}",
        ),
        ("/auth/callback?code=AbC_-123", f"/auth/callback?code={R}"),
        ("/x?access_token=a.b.c&id_token=d.e.f", f"/x?access_token={R}&id_token={R}"),
        ("/x?Code=A&TOKEN=B", f"/x?Code={R}&TOKEN={R}"),
        # An invitation link, as a path and inside a sign-in's next, once and twice encoded.
        ("/auth/invitation/alft_Xy-9_z", f"/auth/invitation/{R}"),
        (
            "/app/auth1/login?next=https%3A%2F%2Fastro.example%2Fauth%2Fcallback%3Fnext%3D%252Fauth%252Finvitation%252Falft_Xy",
            f"/app/auth1/login?next=https%3A%2F%2Fastro.example%2Fauth%2Fcallback%3Fnext%3D%252Fauth%252Finvitation%252F{R}",
        ),
        ("next=%2Fauth%2Finvitation%2Falft_Xy&x=1", f"next=%2Fauth%2Finvitation%2F{R}&x=1"),
        # A code inside an encoded URL loses everything after it up to the next raw separator.
        (
            "/l?next=https%3A%2F%2Fh%2Fauth%2Fcallback%3Fcode%3DSECRET%26next%3D%252Fd&z=1",
            f"/l?next=https%3A%2F%2Fh%2Fauth%2Fcallback%3Fcode%3D{R}&z=1",
        ),
        (
            "/l?next=%2Fauth%2Fcallback%253Ftoken%253DSession%2520k",
            f"/l?next=%2Fauth%2Fcallback%253Ftoken%253D{R}",
        ),
    ],
)
def test_url_credentials_are_redacted(raw, redacted):
    assert redact_url_secrets(raw) == redacted


@pytest.mark.parametrize(
    "text",
    [
        "/app/gql/config/",
        "/auth/login?error=no_account&next=%2Fdashboard",
        "/x?zipcode=12345&postcode=1&code_verifier=v",
        "process exited with code=1",
        "/auth/invitation",
        "GET /app/cli/auth/device/abc/ HTTP/1.1",
    ],
)
def test_text_without_url_credentials_is_unchanged(text):
    assert redact_url_secrets(text) == text


def _access_record(path: str) -> logging.LogRecord:
    # The shape uvicorn's HTTP protocols log a finished request with.
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("203.0.113.7:0", "GET", path, "1.1", 302),
        None,
    )


def test_uvicorns_access_log_line_keeps_its_shape_without_the_credentials():
    import config.asgi  # noqa: F401 -- attaches the filter, as uvicorn's import of the app does

    logger = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, UrlSecretFilter) for f in logger.filters)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        AccessFormatter(
            '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False
        )
    )
    record = _access_record("/app/auth1/callback?code=4f1c9e2a&state=Zq9x")
    assert logger.filter(record)
    handler.handle(record)

    assert (
        stream.getvalue()
        == f'INFO:     203.0.113.7:0 - "GET /app/auth1/callback?code={R}&state=Zq9x HTTP/1.1" 302 Found\n'
    )


def test_the_apps_own_log_handler_redacts_urls_in_any_message():
    # The console handler Django's LOGGING installed, as the running app has it.
    logger = logging.getLogger("django.request")
    handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
    assert len(handlers) == 1
    handler = handlers[0]
    assert any(isinstance(f, UrlSecretFilter) for f in handler.filters)

    stream = io.StringIO()
    previous = handler.setStream(stream)
    try:
        logger.warning("Not Found: %s", "/auth/invitation/alft_SECRETtoken")
        logger.warning("Redirecting to %s", "https://astro.example/auth/callback?code=SECRETcode")
    finally:
        handler.setStream(previous)

    written = stream.getvalue()
    assert "Not Found: /auth/invitation/" in written and "Redirecting to https://astro.example" in written
    assert "SECRET" not in written
