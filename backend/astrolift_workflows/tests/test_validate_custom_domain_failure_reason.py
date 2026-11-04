"""The reason a custom-domain validation reports when it does not settle.

The platform's own DNS write errors come first. Without them the operator sees
only the probe's "not found, still propagating", which names the wrong cause
when the platform could not write the record at all.
"""

from __future__ import annotations

from astrolift_workflows.workflows.validate_custom_domain import _failure_reason

PROBE = {
    "records": [
        {"propagated": True, "message": "CNAME record propagated"},
        {"propagated": False, "message": "TXT not found at the authoritative NS (NXDOMAIN)"},
    ]
}


def test_probe_messages_are_joined_when_the_write_succeeded() -> None:
    reason = _failure_reason({"ensured": ["a"], "errors": []}, PROBE)
    assert reason == "TXT not found at the authoritative NS (NXDOMAIN)"


def test_a_failed_platform_write_comes_first() -> None:
    ensure = {
        "ensured": ["ingress.example.com"],
        "errors": ["_astrolift-challenge.example.com.: InvalidChangeBatch"],
    }
    reason = _failure_reason(ensure, PROBE)
    assert reason.startswith(
        "platform DNS write failed: _astrolift-challenge.example.com.: InvalidChangeBatch"
    )
    assert "NXDOMAIN" in reason


def test_several_write_errors_are_all_reported() -> None:
    reason = _failure_reason({"errors": ["a: boom", "b: bang"]}, None)
    assert reason == "platform DNS write failed: a: boom; b: bang"


def test_no_ensure_result_and_no_probe_falls_back_to_the_default() -> None:
    assert _failure_reason(None, None) == "DNS records have not propagated"
