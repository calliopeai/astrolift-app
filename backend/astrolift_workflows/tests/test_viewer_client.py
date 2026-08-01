"""Disabled-mode coverage for the workflow viewer helpers (#437).

The viewer's three reads + two writes added in #437 (``list_workflow_
instances``, ``describe_workflow_instance``, ``workflow_history``,
``cancel_workflow``, and the reused ``signal_workflow`` /
``terminate_workflow``) must degrade gracefully when Temporal is off
so the UI surface renders without a live cluster. Live-mode happy
path is exercised by the workflow integration tests that already
spin up the Temporal time-skipping environment.
"""

from __future__ import annotations

import pytest

from astrolift_workflows.client import (
    _decode_cursor,
    _encode_cursor,
    cancel_workflow,
    describe_workflow_instance,
    list_workflow_instances,
    workflow_history,
)


@pytest.fixture
def disabled(settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    yield


def test_list_workflow_instances_disabled_returns_empty(disabled):
    assert list_workflow_instances() == ([], None)
    assert list_workflow_instances(workflow_type="DeployAppWorkflow") == ([], None)
    assert list_workflow_instances(status="RUNNING", limit=10) == ([], None)


def test_describe_workflow_instance_disabled_returns_none(disabled):
    assert describe_workflow_instance("any-id") is None


def test_workflow_history_disabled_returns_empty(disabled):
    assert workflow_history("any-id") == []


def test_cancel_workflow_disabled_returns_false(disabled):
    assert cancel_workflow("any-id") is False


def test_list_workflow_instances_clamps_limit(disabled):
    # Disabled returns ([], None) regardless, but the clamp logic must
    # not raise on extreme inputs — keeps GraphQL-layer guards thin.
    assert list_workflow_instances(limit=0) == ([], None)
    assert list_workflow_instances(limit=99999) == ([], None)
    assert list_workflow_instances(limit=-1) == ([], None)


# ----------------------------------------------------------------------
# cursor pagination (#1236)
# ----------------------------------------------------------------------
#
# The SDL advertised ``after`` while the resolver ran ``del after``, so a
# client that paged looped on page one forever with no error. These pin the
# two halves of the contract: a real token round-trips, and a token we
# didn't mint is refused rather than silently answered with page one.


@pytest.fixture
def enabled(settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    yield


def test_list_workflow_instances_round_trips_the_page_token(enabled, monkeypatch):
    captured: dict = {}

    def _fake(workflow_type, status, limit, after_token):
        captured["after_token"] = after_token
        captured["limit"] = limit
        return [{"workflow_id": "w1"}], "cursor-for-page-2"

    monkeypatch.setattr("astrolift_workflows.client._list_instances_async", _fake)

    rows, cursor = list_workflow_instances(limit=10, after=_encode_cursor(b"\x01\x02page-token"))

    # The GraphQL cursor is base64 for transport; Temporal wants the raw bytes.
    assert captured["after_token"] == b"\x01\x02page-token"
    assert captured["limit"] == 10
    assert rows == [{"workflow_id": "w1"}]
    assert cursor == "cursor-for-page-2"


def test_cursor_encoding_survives_non_ascii_tokens():
    # Temporal's token is arbitrary bytes, not text — a naive str() would
    # corrupt it and the next page would silently be wrong.
    token = bytes(range(256))
    assert _decode_cursor(_encode_cursor(token)) == token


def test_encode_cursor_treats_an_empty_token_as_the_last_page():
    # Temporal signals "no more pages" with an empty token, which must not
    # become a cursor string the client would dutifully follow.
    assert _encode_cursor(b"") is None
    assert _encode_cursor(None) is None
    assert _decode_cursor(None) is None
    assert _decode_cursor("") is None


def test_undecodable_cursor_refuses_rather_than_serving_page_one(enabled, monkeypatch):
    called = False

    def _fake(*args, **kwargs):
        nonlocal called
        called = True
        return [{"workflow_id": "page-one"}], None

    monkeypatch.setattr("astrolift_workflows.client._list_instances_async", _fake)

    rows, cursor = list_workflow_instances(after="!!! not base64 !!!")

    # The bug being fixed was answering a page-five request with page one.
    # Refusing is the honest answer; falling through would reintroduce it.
    assert rows == []
    assert cursor is None
    assert called is False
