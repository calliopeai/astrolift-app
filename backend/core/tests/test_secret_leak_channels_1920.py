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

import pytest
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


# ---- Sentry's StrawberryIntegration bypasses the schema entirely (#1944) ----
#
# sentry-sdk auto-enables an integration the moment its target library is
# importable -- true for strawberry-graphql here always -- and does so by
# monkeypatching a class method the first time any Client in the process
# processes that integration's identifier. Neither is undone by a later
# Client's own ``disabled_integrations``: sentry_sdk tracks "have we ever
# processed this identifier" and "was it ever installed" in two
# process-global sets, so an earlier client (in this file, the bare
# ``_sentry_client({})`` calls above) permanently decides the outcome for
# every client built afterward, this test's included. These fixtures force
# a clean slate so the scenario is provable regardless of test order.


@pytest.fixture
def _fresh_strawberry_integration():
    from sentry_sdk.integrations import _installed_integrations, _processed_integrations
    from strawberry.http import async_base_view, sync_base_view

    original_sync = sync_base_view.SyncBaseHTTPView._handle_errors
    original_async = async_base_view.AsyncBaseHTTPView._handle_errors
    had_processed = "strawberry" in _processed_integrations
    had_installed = "strawberry" in _installed_integrations
    _processed_integrations.discard("strawberry")
    _installed_integrations.discard("strawberry")
    try:
        yield
    finally:
        sync_base_view.SyncBaseHTTPView._handle_errors = original_sync
        async_base_view.AsyncBaseHTTPView._handle_errors = original_async
        _processed_integrations.discard("strawberry")
        _installed_integrations.discard("strawberry")
        if had_processed:
            _processed_integrations.add("strawberry")
        if had_installed:
            _installed_integrations.add("strawberry")


def test_sentry_strawberry_integration_does_not_leak_a_coerced_variable_value(
    _fresh_strawberry_integration,
) -> None:
    """A real request through CoreStrawberryView: setAgentSecretValue's
    ``value`` argument is a plain String, so a variable of the wrong shape
    fails GraphQL's variable-coercion step -- before any resolver, any
    permission check, or SecretSafeSchema.process_errors ever runs -- and
    the rejected literal lands in the coercion error's own message.
    StrawberryIntegration patches _handle_errors to report every such
    error via ``event_from_exception``, bypassing the schema completely.

    No DB, session or permissions needed: coercion happens before any of
    that is looked at, exactly why it slips past the schema's own guard."""
    from django.conf import settings

    from config.schema import schema
    from core.schema.views import CoreStrawberryView

    sentry_client = _sentry_client(settings.SENTRY_PRIVACY_OPTIONS)
    query = (
        "mutation Op($s: String!, $e: String!, $v: String!) {"
        " setAgentSecretValue(envSpecSlug: $s, envVar: $e, value: $v) { ok } }"
    )
    body = json.dumps({"query": query, "variables": {"s": "spec", "e": "API_KEY", "v": {"nested": _SECRET}}})
    request = RequestFactory().post("/app/gql/config/", data=body, content_type="application/json")
    view = CoreStrawberryView.as_view(schema=schema)
    with sentry_sdk.new_scope() as scope:
        scope.set_client(sentry_client)
        response = view(request)

    assert response.status_code == 200, response.content
    assert b"errors" in response.content
    assert _SECRET not in _events(sentry_client)
