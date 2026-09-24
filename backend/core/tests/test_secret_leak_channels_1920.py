"""Secret values must not leave through logs or error reporting (#1920).

``gql_logger`` (DEBUG only) logged every GraphQL variable by value and,
on any error, the whole response including ``data``. Sentry attached
request bodies (a GraphQL body is the mutation's variables) and frame
locals (a resolver's ``input``) to error events; its default scrubbing
matches key names like "password", not "value" or "rawManifest".
"""

from __future__ import annotations

import json
import logging

import sentry_sdk
from django.http import JsonResponse
from django.test import RequestFactory
from sentry_sdk.transport import Transport

_SECRET = "sk-live-channel-1"
_DATA_SECRET = "sk-live-channel-2"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self) -> str:
        return "\n".join(logging.Formatter().format(record) for record in self.records)


def _log_request(query: str, variables: dict, response: dict) -> str:
    """Run one request through ``gql_logger`` and return what it logged.

    The handler sits on the module logger itself: app loggers here do
    not propagate to the root logger pytest's ``caplog`` listens on."""
    from config.schema import schema
    from core.utils.logger_helper import gql_logger

    body = json.dumps({"operationName": "Op", "query": query, "variables": variables})
    request = RequestFactory().post("/app/gql/config/", data=body, content_type="application/json")
    logger = logging.getLogger("core.utils.logger_helper")
    capture = _Capture()
    previous_level = logger.level
    logger.addHandler(capture)
    logger.setLevel(logging.DEBUG)
    try:
        gql_logger(lambda _request: JsonResponse(response), schema=schema)(request)
    finally:
        logger.removeHandler(capture)
        logger.setLevel(previous_level)
    return capture.text()


def test_gql_logger_masks_a_secret_bound_to_a_client_named_variable(settings) -> None:
    settings.DEBUG = True
    logged = _log_request(
        'mutation Op($s: String!, $v: String!) { setAgentSecretValue(envSpecSlug: $s, envVar: "API_KEY", value: $v) { ok } }',
        {"s": "emr-triage", "v": _SECRET},
        {"errors": [{"message": f"Variable '$v' got invalid value '{_SECRET}'"}], "data": None},
    )
    assert "emr-triage" in logged
    assert _SECRET not in logged


def test_gql_logger_never_logs_response_data(settings) -> None:
    """An ordinary operation's errors are still logged; the response
    ``data`` beside them (a revealAppSecret payload, say) is not."""
    settings.DEBUG = True
    logged = _log_request(
        "mutation Op($k: String!) { setOrganizationModule(input: {key: $k, enabled: true}) { ok } }",
        {"k": "chat_studio_integration"},
        {
            "errors": [{"message": "a sibling field failed"}],
            "data": {"revealAppSecret": {"data": {"value": _DATA_SECRET}}},
        },
    )
    assert "chat_studio_integration" in logged
    assert "a sibling field failed" in logged
    assert _DATA_SECRET not in logged


class _CaptureTransport(Transport):
    def __init__(self, options=None) -> None:
        super().__init__(options)
        self.envelopes: list = []

    def capture_envelope(self, envelope) -> None:
        self.envelopes.append(envelope)


def _sentry_client(options: dict) -> sentry_sdk.Client:
    return sentry_sdk.Client(dsn="https://public@sentry.invalid/1", transport=_CaptureTransport, **options)


def _events(client: sentry_sdk.Client) -> str:
    client.flush()
    return "\n".join(
        item.get_bytes().decode("utf-8", "replace")
        for envelope in client.transport.envelopes
        for item in envelope.items
    )


def _capture_resolver_error(client: sentry_sdk.Client) -> str:
    def resolver(value: str) -> None:
        raise RuntimeError("resolver failed")

    with sentry_sdk.new_scope() as scope:
        scope.set_client(client)
        try:
            resolver(_SECRET)
        except RuntimeError:
            sentry_sdk.capture_exception()
    return _events(client)


def _capture_request_body(client: sentry_sdk.Client) -> str:
    from sentry_sdk.integrations.django import DjangoRequestExtractor

    body = json.dumps({"query": "mutation { x }", "variables": {"value": _SECRET, "m": _DATA_SECRET}})
    request = RequestFactory().post("/app/gql/config/", data=body, content_type="application/json")
    with sentry_sdk.new_scope() as scope:
        scope.set_client(client)
        event: dict = {"message": "request failed"}
        DjangoRequestExtractor(request).extract_into_event(event)
        sentry_sdk.capture_event(event)
    return _events(client)


def test_sentry_error_events_carry_no_frame_locals() -> None:
    from django.conf import settings

    assert _SECRET in _capture_resolver_error(_sentry_client({}))
    assert _SECRET not in _capture_resolver_error(_sentry_client(settings.SENTRY_PRIVACY_OPTIONS))


def test_sentry_error_events_carry_no_request_body() -> None:
    from django.conf import settings

    assert _DATA_SECRET in _capture_request_body(_sentry_client({}))
    captured = _capture_request_body(_sentry_client(settings.SENTRY_PRIVACY_OPTIONS))
    assert _SECRET not in captured
    assert _DATA_SECRET not in captured
