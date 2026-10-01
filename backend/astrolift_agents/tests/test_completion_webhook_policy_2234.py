import hashlib
import hmac
from uuid import UUID

import pytest

from astrolift_agents.completion_webhook import (
    CallbackConfigurationError,
    blocked_address,
    classify_response,
    delivery_headers,
    encoded_body,
    normalize_allowed_hosts,
    retry_delay,
    validate_destination,
    verify_signature,
)


@pytest.mark.parametrize(
    "url", ["https://hooks.internal.example.org/done", "https://hooks.internal.example.org:8443/done"]
)
def test_explicitly_approved_internal_callback_hosts_are_valid(url):
    assert validate_destination(url, ["*.internal.example.org"]) == "hooks.internal.example.org"


@pytest.mark.parametrize("address", ["10.0.0.1", "172.16.0.1", "192.168.1.1", "fd00::1"])
def test_private_vpc_addresses_are_not_blanket_blocked(address):
    assert not blocked_address(address)


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "::1", "::ffff:127.0.0.1", "169.254.169.254", "100.100.100.200", "0.0.0.0", "ff02::1"],
)
def test_local_and_metadata_addresses_are_blocked(address):
    assert blocked_address(address)


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.internal.example.org/done",
        "https://other.example.org/done",
        "https://internal.example.org/done",
        "https://notinternal.example.org/done",
        "https://hooks.internal.example.org.attacker.example/done",
        "https://user:password@hooks.internal.example.org/done",
        "https://hooks.internal.example.org/done#fragment",
        "https://hooks.internal.example.org:0/done",
        "https://hooks.internal.example.org/\r\nbody",
        "https://hooks.internal.example.org\\@attacker.example/done",
    ],
)
def test_unapproved_or_ambiguous_destinations_fail_without_reflecting_url(url):
    with pytest.raises(CallbackConfigurationError) as caught:
        validate_destination(url, ["*.internal.example.org"])
    assert url not in str(caught.value)
    assert "password" not in str(caught.value)


@pytest.mark.parametrize(
    "hosts", [["*"], ["*.com"], ["*.127.0.0.1"], ["https://example.org"], [None], "example.org"]
)
def test_invalid_allowlist_patterns_fail_closed(hosts):
    with pytest.raises(CallbackConfigurationError):
        normalize_allowed_hosts(hosts)


def test_host_normalization_preserves_exact_matching_and_deduplicates():
    assert normalize_allowed_hosts(["HOOKS.Example.org.", "hooks.example.org"]) == ["hooks.example.org"]
    assert (
        validate_destination("https://HOOKS.example.org/done", ["hooks.example.org"]) == "hooks.example.org"
    )


def test_signature_verifies_the_exact_transmitted_bytes_and_not_a_static_key_header():
    body = encoded_body({"event": "agent_task.finished", "result": {"message": "unicode: café"}})
    secret = b"synthetic-test-signing-key"
    headers = delivery_headers(secret, body, timestamp=1000)
    expected = hmac.new(secret, b"1000." + body, hashlib.sha256).hexdigest()
    assert headers["X-Astrolift-Signature"] == "v1=" + expected
    assert headers["X-Astrolift-Event"] == "agent_task.finished"
    assert UUID(headers["X-Astrolift-Delivery"])
    assert verify_signature(secret, "1000", body, headers["X-Astrolift-Signature"], now=1000)
    assert not verify_signature(secret, "1000", body + b" ", headers["X-Astrolift-Signature"], now=1000)
    assert not verify_signature(secret, "1000", body, headers["X-Astrolift-Signature"], now=1301)
    assert not verify_signature(secret, "1000", body, headers["X-Astrolift-Signature"], now=699)
    assert (
        delivery_headers(secret, body, timestamp=1001)["X-Astrolift-Delivery"]
        != headers["X-Astrolift-Delivery"]
    )
    assert all(secret.decode() not in value for value in headers.values())


@pytest.mark.parametrize(
    "code,classification",
    [
        (200, "delivered"),
        (204, "delivered"),
        (299, "delivered"),
        (400, "failed"),
        (401, "failed"),
        (410, "failed"),
        (499, "failed"),
        (408, "retry"),
        (429, "retry"),
        (301, "retry"),
        (500, "retry"),
        (503, "retry"),
        (None, "retry"),
    ],
)
def test_response_retry_classification(code, classification):
    assert classify_response(code) == classification


@pytest.mark.parametrize("jitter", [0, 0.5, 1])
def test_outage_schedule_reaches_24_elapsed_hours_even_with_negative_jitter(jitter):
    elapsed = 0.0
    attempts = 1
    while (delay := retry_delay(attempts, elapsed, jitter=jitter)) is not None:
        assert 0 < delay <= 4320
        elapsed += delay
        attempts += 1
        assert attempts < 100
    assert elapsed == 86400
    assert attempts > 20


@pytest.mark.parametrize("received", ["v1=é", "sha256=abc", "", None])
def test_invalid_signature_encodings_are_refused_without_an_exception(received):
    assert not verify_signature(b"synthetic-test-key", "1000", b"{}", received, now=1000)


def test_receivers_can_verify_both_keys_during_a_rotation_overlap():
    body = encoded_body({"event": "agent_task.finished"})
    old_key, new_key = b"synthetic-old-key", b"synthetic-new-key"
    for secret in (old_key, new_key):
        headers = delivery_headers(secret, body, timestamp=1000)
        assert any(
            verify_signature(key, "1000", body, headers["X-Astrolift-Signature"], now=1001)
            for key in (new_key, old_key)
        )


def test_initial_retry_intervals_have_exponential_backoff_and_hourly_cap():
    assert [retry_delay(attempt, 0, jitter=0.5) for attempt in range(1, 7)] == [
        30,
        120,
        600,
        1800,
        3600,
        3600,
    ]
