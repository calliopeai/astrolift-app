"""Tests for image scan policy + override (#146, spec 06 §6)."""

from __future__ import annotations

import pytest

from astrolift_workflows.activities.image_scan import (
    CVE,
    PolicyDecision,
    ScanDecision,
    ScanOverride,
    ScanPolicy,
    ScanResult,
    Severity,
    apply_override,
    evaluate_policy,
    get_scanner,
    register_scanner,
    unregister_scanner,
)


def _result(findings: list[CVE], succeeded: bool = True, error: str = "") -> ScanResult:
    counts: dict[Severity, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    return ScanResult(
        image_uri="registry.acme.com/api:abc",
        scanner="trivy",
        counts=counts,
        findings=tuple(findings),
        scan_succeeded=succeeded,
        scan_error=error,
    )


# ---- policy guards ---------------------------------------------------


def test_policy_rejects_warn_stricter_than_block():
    """warn_at MEDIUM + block_at LOW would mean 'warn at high but
    block at low' — backwards. Reject at construction."""
    with pytest.raises(ValueError, match="warn_at"):
        ScanPolicy(block_at=Severity.LOW, warn_at=Severity.HIGH)


def test_policy_rejects_zero_timeout():
    with pytest.raises(ValueError):
        ScanPolicy(timeout_seconds=0)


# ---- happy paths ----------------------------------------------------


def test_pass_when_no_findings():
    out = evaluate_policy(_result([]), ScanPolicy())
    assert out.decision == ScanDecision.PASS


def test_pass_when_only_low_findings():
    """Default policy blocks at HIGH+, warns at MEDIUM. LOW
    findings shouldn't raise either."""
    cves = [CVE(cve_id="CVE-1", severity=Severity.LOW)]
    out = evaluate_policy(_result(cves), ScanPolicy())
    assert out.decision == ScanDecision.PASS


def test_warn_at_medium_default():
    cves = [CVE(cve_id="CVE-2", severity=Severity.MEDIUM)]
    out = evaluate_policy(_result(cves), ScanPolicy())
    assert out.decision == ScanDecision.WARN
    assert len(out.warning_findings) == 1
    assert out.blocking_findings == ()


def test_block_at_high_default():
    cves = [CVE(cve_id="CVE-3", severity=Severity.HIGH)]
    out = evaluate_policy(_result(cves), ScanPolicy())
    assert out.decision == ScanDecision.BLOCK
    assert len(out.blocking_findings) == 1


def test_block_at_critical_when_default_block_is_high():
    """CRITICAL is implicitly captured by 'block_at HIGH' since
    CRITICAL is worse than HIGH."""
    cves = [CVE(cve_id="CVE-4", severity=Severity.CRITICAL)]
    out = evaluate_policy(_result(cves), ScanPolicy())
    assert out.decision == ScanDecision.BLOCK


def test_mixed_severity_partitions_findings():
    cves = [
        CVE(cve_id="CVE-A", severity=Severity.CRITICAL),
        CVE(cve_id="CVE-B", severity=Severity.MEDIUM),
        CVE(cve_id="CVE-C", severity=Severity.LOW),
    ]
    out = evaluate_policy(_result(cves), ScanPolicy())
    assert out.decision == ScanDecision.BLOCK
    assert {f.cve_id for f in out.blocking_findings} == {"CVE-A"}
    assert {f.cve_id for f in out.warning_findings} == {"CVE-B"}


def test_strict_policy_blocks_on_medium():
    cves = [CVE(cve_id="CVE-X", severity=Severity.MEDIUM)]
    strict = ScanPolicy(block_at=Severity.MEDIUM, warn_at=Severity.LOW)
    out = evaluate_policy(_result(cves), strict)
    assert out.decision == ScanDecision.BLOCK


# ---- scanner failure -------------------------------------------------


def test_fail_open_returns_warn_on_scanner_error():
    """Default fail-open lets deploys through when the scanner is
    down — flaky scanner shouldn't bring deploys down."""
    out = evaluate_policy(
        _result([], succeeded=False, error="scanner timeout"),
        ScanPolicy(),
    )
    assert out.decision == ScanDecision.WARN
    assert "fail-open" in out.reason
    assert "timeout" in out.reason


def test_fail_closed_returns_block_on_scanner_error():
    out = evaluate_policy(
        _result([], succeeded=False, error="auth denied"),
        ScanPolicy(fail_open_on_scanner_error=False),
    )
    assert out.decision == ScanDecision.BLOCK
    assert "fail-closed" in out.reason


# ---- override --------------------------------------------------------


def test_override_rejects_short_reason():
    """Operator must explain why — 'fix it' isn't enough."""
    with pytest.raises(ValueError, match="reason"):
        ScanOverride(by_user_id=1, reason="fix")


def test_override_rejects_zero_user_id():
    with pytest.raises(ValueError):
        ScanOverride(by_user_id=0, reason="emergency: prod down at 3am")


def test_override_converts_block_to_warn():
    decision = PolicyDecision(
        decision=ScanDecision.BLOCK,
        blocking_findings=(CVE(cve_id="CVE-1", severity=Severity.CRITICAL),),
        warning_findings=(),
        reason="1 critical finding",
    )
    override = ScanOverride(by_user_id=42, reason="emergency hotfix for prod outage")
    new_decision = apply_override(decision, override)
    assert new_decision.decision == ScanDecision.WARN
    # Original findings preserved on the new decision so the audit
    # log shows both the BLOCK and the override
    assert len(new_decision.blocking_findings) == 1
    assert "user 42" in new_decision.reason
    assert "emergency hotfix" in new_decision.reason


def test_override_does_not_modify_pass_or_warn():
    pass_decision = PolicyDecision(
        decision=ScanDecision.PASS,
        blocking_findings=(),
        warning_findings=(),
        reason="ok",
    )
    override = ScanOverride(by_user_id=1, reason="just to confirm")
    assert apply_override(pass_decision, override) == pass_decision

    warn_decision = PolicyDecision(
        decision=ScanDecision.WARN,
        blocking_findings=(),
        warning_findings=(CVE(cve_id="CVE-1", severity=Severity.MEDIUM),),
        reason="warn",
    )
    assert apply_override(warn_decision, override) == warn_decision


def test_override_none_passes_decision_through():
    decision = PolicyDecision(
        decision=ScanDecision.BLOCK,
        blocking_findings=(),
        warning_findings=(),
        reason="x",
    )
    assert apply_override(decision, None) == decision


# ---- scanner registry -----------------------------------------------


def test_scanner_registry_round_trip():
    def fake_trivy(image_uri: str) -> ScanResult:
        return _result([])

    register_scanner("trivy", fake_trivy)
    try:
        scanner = get_scanner("trivy")
        result = scanner("registry.acme.com/api:abc")
        assert result.scan_succeeded is True
    finally:
        unregister_scanner("trivy")


def test_get_scanner_raises_when_unregistered():
    with pytest.raises(KeyError, match="no scanner registered"):
        get_scanner("not-a-real-scanner-xyz")


# ---- ScanResult helper ---------------------------------------------


def test_count_at_or_above_aggregates_severities():
    result = _result(
        [
            CVE(cve_id=f"CVE-{i}", severity=s)
            for i, s in enumerate(
                [
                    Severity.CRITICAL,
                    Severity.HIGH,
                    Severity.HIGH,
                    Severity.MEDIUM,
                    Severity.LOW,
                ]
            )
        ]
    )
    assert result.count_at_or_above(Severity.HIGH) == 3  # 1 critical + 2 high
    assert result.count_at_or_above(Severity.MEDIUM) == 4
    assert result.count_at_or_above(Severity.LOW) == 5
    assert result.count_at_or_above(Severity.CRITICAL) == 1
