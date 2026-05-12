"""Tests for compliance report templates (#271, spec 12 §11)."""

from __future__ import annotations

import pytest

from astrolift_compliance.templates import (
    HIPAA,
    ISO_27001,
    SOC_2,
    TEMPLATE_REGISTRY,
    CheckStatus,
    ComplianceError,
    CoverageArea,
    evaluate_template,
    get_template,
)

# ---- registry -----------------------------------------------------


def test_three_templates_registered():
    """SOC 2, HIPAA, ISO 27001."""
    assert len(TEMPLATE_REGISTRY) == 3
    assert "soc2" in TEMPLATE_REGISTRY
    assert "hipaa" in TEMPLATE_REGISTRY
    assert "iso27001" in TEMPLATE_REGISTRY


def test_get_template_by_slug():
    assert get_template(slug="soc2") == SOC_2
    assert get_template(slug="hipaa") == HIPAA
    assert get_template(slug="iso27001") == ISO_27001


def test_get_template_unknown_slug():
    with pytest.raises(ComplianceError, match="unknown"):
        get_template(slug="pci-dss")


# ---- HIPAA-specific requirements ----------------------------------


def test_hipaa_requires_6_year_retention():
    """HIPAA: 6-year retention is the regime's requirement."""
    audit_req = next(r for r in HIPAA.requirements if r.area == CoverageArea.AUDIT_LOG)
    assert audit_req.threshold["min_retention_days"] == 365 * 6


def test_hipaa_requires_residency_policy():
    """HIPAA emphasizes data residency for ePHI."""
    region_req = next(
        (r for r in HIPAA.requirements if r.area == CoverageArea.REGION_CONSTRAINTS),
        None,
    )
    assert region_req is not None
    assert region_req.threshold["residency_policy_required"] is True


def test_hipaa_requires_abac():
    """ABAC required for ePHI access."""
    access_req = next(r for r in HIPAA.requirements if r.area == CoverageArea.ACCESS_CONTROLS)
    assert access_req.threshold["abac_required"] is True


# ---- SOC 2-specific requirements ----------------------------------


def test_soc2_audit_retention_one_year():
    audit_req = next(r for r in SOC_2.requirements if r.area == CoverageArea.AUDIT_LOG)
    assert audit_req.threshold["min_retention_days"] == 365


# ---- ISO 27001-specific requirements -------------------------------


def test_iso27001_audit_retention_three_years():
    audit_req = next(r for r in ISO_27001.requirements if r.area == CoverageArea.AUDIT_LOG)
    assert audit_req.threshold["min_retention_days"] == 365 * 3


# ---- evaluation: audit log ----------------------------------------


def _full_evidence(*, retention_days=365 * 10, hash_chain_valid=True):
    """Build a compliant evidence map for any template."""
    return {
        CoverageArea.AUDIT_LOG: {
            "retention_days": retention_days,
            "hash_chain_valid": hash_chain_valid,
        },
        CoverageArea.SECRET_ROTATION: {"pct_within_window": 99},
        CoverageArea.ACCESS_CONTROLS: {
            "rbac_active": True,
            "abac_active": True,
        },
        CoverageArea.WORKFLOW_AUDIT_TRAIL: {"coverage_pct": 99},
        CoverageArea.ENCRYPTION: {
            "tls_min_version": "1.3",
            "secrets_backend_kind": "aws_secrets_manager",
        },
        CoverageArea.REGION_CONSTRAINTS: {
            "residency_policy_present": True,
        },
    }


def test_soc2_passes_with_full_evidence():
    report = evaluate_template(
        template=SOC_2,
        evidence_by_area=_full_evidence(),
    )
    assert report.overall == CheckStatus.PASS


def test_hipaa_passes_with_full_evidence():
    report = evaluate_template(
        template=HIPAA,
        evidence_by_area=_full_evidence(),
    )
    assert report.overall == CheckStatus.PASS


def test_hipaa_fails_with_one_year_retention():
    """HIPAA needs 6 years; 1 year fails."""
    evidence = _full_evidence(retention_days=365)
    report = evaluate_template(
        template=HIPAA,
        evidence_by_area=evidence,
    )
    assert report.overall == CheckStatus.FAIL


def test_audit_fails_without_hash_chain():
    evidence = _full_evidence(hash_chain_valid=False)
    report = evaluate_template(
        template=SOC_2,
        evidence_by_area=evidence,
    )
    assert report.overall == CheckStatus.FAIL


def test_audit_inconclusive_without_evidence():
    """No retention evidence at all → INCONCLUSIVE for that
    check (data-collection gap, not actual non-compliance)."""
    evidence = _full_evidence()
    evidence[CoverageArea.AUDIT_LOG] = {}  # remove evidence
    report = evaluate_template(
        template=SOC_2,
        evidence_by_area=evidence,
    )
    audit_check = next(c for c in report.checks if c.area == CoverageArea.AUDIT_LOG)
    assert audit_check.status == CheckStatus.INCONCLUSIVE


# ---- evaluation: secret rotation ----------------------------------


def test_rotation_below_threshold_fails():
    evidence = _full_evidence()
    evidence[CoverageArea.SECRET_ROTATION] = {"pct_within_window": 80}
    report = evaluate_template(
        template=SOC_2,
        evidence_by_area=evidence,
    )
    rotation_check = next(c for c in report.checks if c.area == CoverageArea.SECRET_ROTATION)
    assert rotation_check.status == CheckStatus.FAIL


def test_rotation_at_threshold_passes():
    """SOC 2 needs >= 90%; exactly 90% passes."""
    evidence = _full_evidence()
    evidence[CoverageArea.SECRET_ROTATION] = {"pct_within_window": 90}
    report = evaluate_template(
        template=SOC_2,
        evidence_by_area=evidence,
    )
    rotation_check = next(c for c in report.checks if c.area == CoverageArea.SECRET_ROTATION)
    assert rotation_check.status == CheckStatus.PASS


# ---- evaluation: region constraints -------------------------------


def test_region_constraints_required_for_hipaa():
    evidence = _full_evidence()
    evidence[CoverageArea.REGION_CONSTRAINTS] = {
        "residency_policy_present": False,
    }
    report = evaluate_template(
        template=HIPAA,
        evidence_by_area=evidence,
    )
    assert report.overall == CheckStatus.FAIL


def test_region_constraints_passthrough_when_not_required():
    """SOC 2 doesn't require residency policy; even when absent
    the check passes."""
    evidence = _full_evidence()
    evidence[CoverageArea.REGION_CONSTRAINTS] = {
        "residency_policy_present": False,
    }
    report = evaluate_template(
        template=SOC_2,
        evidence_by_area=evidence,
    )
    # SOC 2 doesn't require region constraints
    assert report.overall == CheckStatus.PASS


# ---- evaluation: access controls ----------------------------------


def test_hipaa_fails_without_abac():
    evidence = _full_evidence()
    evidence[CoverageArea.ACCESS_CONTROLS] = {
        "rbac_active": True,
        "abac_active": False,
    }
    report = evaluate_template(
        template=HIPAA,
        evidence_by_area=evidence,
    )
    assert report.overall == CheckStatus.FAIL


def test_soc2_passes_without_abac():
    """SOC 2 only requires RBAC."""
    evidence = _full_evidence()
    evidence[CoverageArea.ACCESS_CONTROLS] = {
        "rbac_active": True,
        "abac_active": False,
    }
    report = evaluate_template(
        template=SOC_2,
        evidence_by_area=evidence,
    )
    assert report.overall == CheckStatus.PASS


# ---- evaluation: encryption ---------------------------------------


def test_encryption_fails_with_tls_10():
    evidence = _full_evidence()
    evidence[CoverageArea.ENCRYPTION] = {
        "tls_min_version": "1.0",
        "secrets_backend_kind": "aws_secrets_manager",
    }
    report = evaluate_template(
        template=SOC_2,
        evidence_by_area=evidence,
    )
    assert report.overall == CheckStatus.FAIL


def test_encryption_fails_with_unrecognized_backend():
    """Plaintext or unsanctioned backend → fail."""
    evidence = _full_evidence()
    evidence[CoverageArea.ENCRYPTION] = {
        "tls_min_version": "1.3",
        "secrets_backend_kind": "filesystem",
    }
    report = evaluate_template(
        template=SOC_2,
        evidence_by_area=evidence,
    )
    assert report.overall == CheckStatus.FAIL


# ---- overall verdict resolution ------------------------------------


def test_overall_fail_when_any_fail():
    """One FAIL beats all PASS."""
    evidence = _full_evidence()
    evidence[CoverageArea.SECRET_ROTATION] = {"pct_within_window": 50}
    report = evaluate_template(
        template=SOC_2,
        evidence_by_area=evidence,
    )
    assert report.overall == CheckStatus.FAIL


def test_overall_inconclusive_when_no_fail_but_inconclusive():
    """Missing evidence on a non-failing template overall is
    INCONCLUSIVE (data-collection gap; treated as fail for
    audit-evidence purposes but rendered separately)."""
    evidence = _full_evidence()
    evidence[CoverageArea.AUDIT_LOG] = {}
    report = evaluate_template(
        template=SOC_2,
        evidence_by_area=evidence,
    )
    assert report.overall == CheckStatus.INCONCLUSIVE


def test_overall_pass_when_all_pass():
    report = evaluate_template(
        template=SOC_2,
        evidence_by_area=_full_evidence(),
    )
    assert all(c.status == CheckStatus.PASS for c in report.checks)
    assert report.overall == CheckStatus.PASS
