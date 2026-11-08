from __future__ import annotations

from dataclasses import asdict, replace
from unittest.mock import patch

import pytest

from nvke.client import NvkeError
from nvke.contract import _contract
from nvke.infrastructure import (
    NEXT_CHECK_GRACE_NS,
    STALE_AFTER_NS,
    CredentialHealth,
    assess_credentials,
    decode_identity,
    providers,
    timestamp,
)

NOW = timestamp("2026-11-08T06:00:00Z")
assert NOW is not None
NOW_NS = NOW.nanoseconds


def identity(
    *, last="2026-11-08T05:59:00Z", next_check=None, status="True", generation=1, observed=1, name="nico-default"
):
    value = decode_identity(
        {
            "metadata": {
                "namespace": "capnico-system",
                "name": name,
                "generation": generation,
                "creationTimestamp": "2026-11-08T04:00:00Z",
            },
            "spec": {"credentialsRef": {"name": "nico-credentials"}},
            "status": {
                "lastCheckedTime": last,
                "nextCheckTime": next_check,
                "conditions": [
                    {
                        "type": "Ready",
                        "status": status,
                        "observedGeneration": observed,
                        "message": "PRIVATE_PROVIDER_MESSAGE",
                        "reason": "PRIVATE_NATIVE_REASON",
                    }
                ],
            },
        }
    )
    assert value is not None
    return value


def assess(*identities, **options) -> CredentialHealth:
    return assess_credentials(
        identities,
        crd_established=True,
        secret_namespace="capnico-system",
        secret_name="nico-credentials",
        now_ns=NOW_NS,
        **options,
    )


def test_pinned_registry_excludes_provisioned_and_preserves_provider_specific_contracts():
    nico, launch = providers()
    assert (nico.type, launch.type) == ("nico", "launchlayer")
    assert nico.default_secret_name == "nico-credentials"
    assert nico.identity_resource == "nicoidentities"
    assert nico.watch_namespace_flag == "--namespace"
    assert launch.unconfigured_is_valid and not launch.default_secret_name
    assert launch.endpoint_flag == "--base-url" and not launch.identity_resource
    changed = dict(_contract(), sources={})
    with patch("nvke.infrastructure._contract", return_value=changed), pytest.raises(NvkeError) as error:
        providers()
    assert error.value.code == "UPSTREAM_MISMATCH"


@pytest.mark.parametrize(
    "value",
    [
        None,
        123,
        True,
        "2026-11-08",
        "2026-11-08T06:00:00",
        "2026-11-08T06:00:00ZPRIVATE",
        "2026-13-08T06:00:00Z",
        "2026-11-08T06:00:60Z",
        "2026-11-08T06:00:00.1234567890Z",
    ],
)
def test_invalid_native_dates_never_become_fresh_evidence(value):
    assert timestamp(value) is None


def test_timestamp_preserves_nanosecond_order_and_timezone_equivalence():
    a = timestamp("2026-11-08T06:00:00.000000001Z")
    b = timestamp("2026-11-08T07:00:00.000000001+01:00")
    assert a and b and a.nanoseconds == b.nanoseconds == NOW_NS + 1
    assert a.text == "2026-11-08T06:00:00.000000001Z"
    before_epoch = timestamp("1969-12-31T23:59:59.999999999Z")
    assert before_epoch and before_epoch.nanoseconds == -1


@pytest.mark.parametrize(
    ("ready", "health"),
    [("True", "HEALTHY"), ("False", "FAILING"), ("Unknown", "UNKNOWN"), (None, "UNKNOWN"), ("PRIVATE", "UNKNOWN")],
)
def test_native_ready_result_does_not_establish_provisioning_health(ready, health):
    result = assess(identity(status=ready), manager_found=True, manager_available=False)
    assert result.health == health
    assert "PRIVATE" not in repr(result)
    assert result.last_checked_time == "2026-11-08T05:59:00Z"
    assert not hasattr(result, "provisioning_ready")


@pytest.mark.parametrize("observed", [None, 0, True, 1.0, 2, "1", 9223372036854775808])
def test_wrong_or_malformed_generation_cannot_inherit_healthy_check(observed):
    assert assess(identity(observed=observed)).health == "PENDING"


@pytest.mark.parametrize("generation", [None, 0, True, 1.0, "1", 9223372036854775808])
def test_malformed_identity_generation_is_not_reported_healthy(generation):
    assert assess(identity(generation=generation)).health == "PENDING"


@pytest.mark.parametrize(
    ("last", "next_check", "health"),
    [
        ("2026-11-08T05:50:00Z", None, "HEALTHY"),
        ("2026-11-08T05:49:59.999999999Z", None, "STALE"),
        ("2026-11-08T05:48:00Z", "2026-11-08T05:55:00Z", "HEALTHY"),
        ("2026-11-08T05:48:00Z", "2026-11-08T05:54:59.999999999Z", "STALE"),
        ("2026-11-08T05:48:00Z", "2026-11-08T05:48:00Z", "STALE"),
        ("2026-11-08T05:48:00Z", "2026-11-08T06:48:00Z", "STALE"),
        ("2026-11-08T05:48:00Z", "invalid", "STALE"),
        ("2026-11-08T06:01:00Z", None, "HEALTHY"),
        ("2026-11-08T06:01:00.000000001Z", None, "UNKNOWN"),
        ("invalid", None, "UNKNOWN"),
        (None, None, "STALE"),
    ],
)
def test_schedules_future_tolerance_and_age_limits_match_native_boundaries(last, next_check, health):
    assert assess(identity(last=last, next_check=next_check)).health == health
    assert STALE_AFTER_NS == 600_000_000_000
    assert NEXT_CHECK_GRACE_NS == 300_000_000_000


def test_never_checked_identity_is_pending_until_creation_age_expires():
    recent = timestamp("2026-11-08T05:55:00Z")
    value = replace(identity(last=None), created=recent, ready_status=None, ready_generation=None)
    assert assess(value).health == "PENDING"
    assert assess(replace(value, created=None)).health == "STALE"


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({}, "provider_manager_not_found"),
        ({"manager_found": True, "watch_namespace": "other"}, "identity_outside_manager_namespace"),
        ({"manager_found": True, "watch_filter": "managed"}, "identity_outside_manager_filter"),
        ({"manager_found": True}, "provider_manager_unavailable"),
        ({"manager_found": True, "manager_available": True}, "provider_check_overdue"),
    ],
)
def test_stale_checks_explain_manager_watch_scope_without_echoing_flags(options, reason):
    result = assess(identity(last="2026-11-08T05:00:00Z"), **options)
    assert result.health == "STALE" and result.reason == reason


@pytest.mark.parametrize(
    ("error", "health"),
    [
        ("NOT_FOUND", "UNSUPPORTED"),
        ("FORBIDDEN", "UNREADABLE"),
        ("TRANSPORT_ERROR", "UNREADABLE"),
        ("PRIVATE_PROVIDER_ERROR", "UNREADABLE"),
    ],
)
def test_identity_read_failures_are_distinct_from_provider_failure(error, health):
    result = assess(identity(), list_error=error)
    assert result.health == health and "PRIVATE" not in repr(result)


def test_no_identity_and_absent_api_are_distinct_and_do_not_read_credentials():
    assert assess().health == "NO-IDENTITY"
    assert assess(replace(identity(), secret_name="another-secret")).health == "NO-IDENTITY"
    assert assess(replace(identity(), namespace="another-org")).health == "NO-IDENTITY"
    assert assess(replace(identity(), deleting=True)).health == "NO-IDENTITY"
    assert (
        assess_credentials(
            (), crd_established=False, secret_namespace="capnico-system", secret_name="nico-credentials", now_ns=NOW_NS
        ).health
        == "UNSUPPORTED"
    )


def test_selection_prefers_current_fresh_then_latest_check_then_name_and_preserves_additional_refs():
    current = identity(name="current")
    wrong_generation = identity(last="2026-11-08T06:00:00Z", observed=2, name="new-but-not-current")
    stale = identity(last="2026-11-08T05:00:00Z", name="stale")
    assert assess(wrong_generation, stale, current).identity_name == "current"
    assert assess(stale, wrong_generation).identity_name == "new-but-not-current"
    a, b = replace(current, name="a"), replace(current, name="b")
    result = assess(b, a)
    assert result.identity_name == "a" and result.additional_identities == (("capnico-system", "b"),)


def test_private_native_fields_are_never_retained_in_health_projection():
    result = assess(identity())
    assert "PRIVATE" not in repr(asdict(result))
    assert set(asdict(result)) == {
        "health",
        "reason",
        "identity_namespace",
        "identity_name",
        "identity_generation",
        "ready_status",
        "observed_generation",
        "last_checked_time",
        "next_check_time",
        "additional_identities",
    }
    assert decode_identity({"metadata": {"name": "PRIVATE/INVALID", "namespace": "capnico-system"}}) is None
