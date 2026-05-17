"""Tests for the audit-payload scrubber (#433 scope A)."""

from __future__ import annotations

from astrolift_operations.audit_redaction import (
    REDACTED_SENTINEL,
    scrub_payload,
)


def test_redacts_secret_at_top_level():
    out = scrub_payload({"secret": "abc", "ok": "yes"})
    assert out["secret"] == REDACTED_SENTINEL
    assert out["ok"] == "yes"


def test_redacts_password_token_apikey():
    out = scrub_payload({"password": "p", "token": "t", "api_key": "k", "ApiKey": "k2"})
    assert out["password"] == REDACTED_SENTINEL
    assert out["token"] == REDACTED_SENTINEL
    assert out["api_key"] == REDACTED_SENTINEL
    assert out["ApiKey"] == REDACTED_SENTINEL


def test_case_insensitive_match():
    out = scrub_payload({"PASSWORD": "x", "Authorization": "y"})
    assert out["PASSWORD"] == REDACTED_SENTINEL
    assert out["Authorization"] == REDACTED_SENTINEL


def test_substring_match_catches_compound_keys():
    out = scrub_payload({"plaintext_secret": "x", "authToken": "y", "x-api-key": "z"})
    assert out["plaintext_secret"] == REDACTED_SENTINEL
    assert out["authToken"] == REDACTED_SENTINEL
    assert out["x-api-key"] == REDACTED_SENTINEL


def test_recurses_into_dicts():
    out = scrub_payload({"outer": {"secret": "leak", "ok": 1}})
    assert out["outer"]["secret"] == REDACTED_SENTINEL
    assert out["outer"]["ok"] == 1


def test_recurses_into_lists_of_dicts():
    out = scrub_payload({"items": [{"password": "x"}, {"name": "y"}]})
    assert out["items"][0]["password"] == REDACTED_SENTINEL
    assert out["items"][1]["name"] == "y"


def test_sensitive_list_value_kept_as_marker_list():
    out = scrub_payload({"tokens": ["a", "b", "c"]})
    assert out["tokens"] == [REDACTED_SENTINEL]


def test_sensitive_dict_value_kept_as_marker_dict():
    out = scrub_payload({"credentials": {"u": "alice", "p": "p"}})
    assert out["credentials"] == {"__redacted__": True}


def test_passthrough_for_safe_scalars():
    assert scrub_payload("plain") == "plain"
    assert scrub_payload(42) == 42
    assert scrub_payload(None) is None
    assert scrub_payload(True) is True


def test_passthrough_for_lists_of_scalars():
    assert scrub_payload([1, 2, "x", None]) == [1, 2, "x", None]


def test_deep_nested_redaction():
    payload = {
        "before": {"app": {"slug": "x", "secret_key": "old"}},
        "after": {"app": {"slug": "x", "secret_key": "new"}},
        "actor": "alice",
    }
    out = scrub_payload(payload)
    assert out["before"]["app"]["secret_key"] == REDACTED_SENTINEL
    assert out["after"]["app"]["secret_key"] == REDACTED_SENTINEL
    assert out["before"]["app"]["slug"] == "x"
    assert out["actor"] == "alice"


def test_recursion_depth_cap():
    # Build a long nested chain — the scrubber must not blow the stack.
    payload: dict = {"ok": 1}
    cur = payload
    for _ in range(40):
        cur["next"] = {"ok": 1}
        cur = cur["next"]
    # No exception, no recursion error
    scrub_payload(payload)


def test_unsupported_type_coerces_to_string():
    class Weird:
        def __str__(self):
            return "weird"

    out = scrub_payload({"x": Weird()})
    assert out["x"] == "weird"
