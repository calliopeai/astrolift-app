"""
Compliance report templates (#271, spec 12 §11).

Pure-Python policy. Defines the SOC 2 / HIPAA / ISO 27001
templates and the per-template evidence-checking rules.

Templates pick subsets of the platform's continuously-collected
evidence (audit retention, hash chain, rotation compliance,
encryption status, residency policy). This module declares what
each template requires; the evidence-collection activities live
in the operations layer.

* **Template registry** — pre-built SOC 2 / HIPAA / ISO 27001.
* **Evidence checks** — per coverage area, the threshold each
  template demands (e.g. SOC 2: audit log retention >= 365 days).
* **Pass/fail evaluation** — given a snapshot of platform state,
  emit per-check status + overall verdict.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from enum import StrEnum


class ComplianceError(ValueError):
    pass


# ---- coverage areas ------------------------------------------------


class CoverageArea(StrEnum):
    """Spec 12 §11: the categories a template can require."""

    ACCESS_CONTROLS = "access_controls"
    """Org/team/role/scope filter + ABAC policy presence."""

    ENCRYPTION = "encryption"
    """Secrets backend kind, TLS issuance, at-rest scheme."""

    AUDIT_LOG = "audit_log"
    """Append-only triggers active, retention >= required days,
    hash chain valid."""

    SECRET_ROTATION = "secret_rotation"
    """% of secrets within rotation window per #150."""

    WORKFLOW_AUDIT_TRAIL = "workflow_audit_trail"
    """mutation_audit decorator coverage."""

    REGION_CONSTRAINTS = "region_constraints"
    """Per-org residency policy presence per #152."""


# ---- template registry ---------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class TemplateRequirement:
    """One requirement within a template. Each compliance regime
    chooses thresholds based on its own framework."""

    area: CoverageArea
    threshold: dict
    """Per-area threshold values. Schema varies per area:
    - audit_log: {"min_retention_days": int, "hash_chain_required": bool}
    - secret_rotation: {"min_pct_within_window": int}
    - region_constraints: {"residency_policy_required": bool}
    - encryption: {"min_tls_version": str, "secrets_backend_kinds": list}
    - access_controls: {"abac_required": bool, "rbac_required": bool}
    - workflow_audit_trail: {"min_coverage_pct": int}
    """


@dataclasses.dataclass(frozen=True, slots=True)
class ComplianceTemplate:
    """Pre-built template for a regime."""

    name: str
    """SOC 2, HIPAA, ISO 27001."""

    requirements: tuple[TemplateRequirement, ...]


SOC_2 = ComplianceTemplate(
    name="SOC 2",
    requirements=(
        TemplateRequirement(
            area=CoverageArea.AUDIT_LOG,
            threshold={
                "min_retention_days": 365,
                "hash_chain_required": True,
            },
        ),
        TemplateRequirement(
            area=CoverageArea.SECRET_ROTATION,
            threshold={"min_pct_within_window": 90},
        ),
        TemplateRequirement(
            area=CoverageArea.ACCESS_CONTROLS,
            threshold={"rbac_required": True, "abac_required": False},
        ),
        TemplateRequirement(
            area=CoverageArea.WORKFLOW_AUDIT_TRAIL,
            threshold={"min_coverage_pct": 95},
        ),
        TemplateRequirement(
            area=CoverageArea.ENCRYPTION,
            threshold={
                "min_tls_version": "1.2",
                "secrets_backend_kinds": [
                    "aws_secrets_manager",
                    "gcp_secret_manager",
                    "azure_key_vault",
                    "hashicorp_vault",
                ],
            },
        ),
    ),
)

HIPAA = ComplianceTemplate(
    name="HIPAA",
    requirements=(
        # HIPAA: 6-year retention.
        TemplateRequirement(
            area=CoverageArea.AUDIT_LOG,
            threshold={
                "min_retention_days": 365 * 6,
                "hash_chain_required": True,
            },
        ),
        # ePHI must be encrypted in transit + at rest.
        TemplateRequirement(
            area=CoverageArea.ENCRYPTION,
            threshold={
                "min_tls_version": "1.2",
                "secrets_backend_kinds": [
                    "aws_secrets_manager",
                    "gcp_secret_manager",
                    "azure_key_vault",
                    "hashicorp_vault",
                ],
            },
        ),
        TemplateRequirement(
            area=CoverageArea.ACCESS_CONTROLS,
            threshold={"rbac_required": True, "abac_required": True},
        ),
        # HIPAA emphasizes data residency for ePHI.
        TemplateRequirement(
            area=CoverageArea.REGION_CONSTRAINTS,
            threshold={"residency_policy_required": True},
        ),
        TemplateRequirement(
            area=CoverageArea.SECRET_ROTATION,
            threshold={"min_pct_within_window": 95},
        ),
    ),
)

ISO_27001 = ComplianceTemplate(
    name="ISO 27001",
    requirements=(
        TemplateRequirement(
            area=CoverageArea.AUDIT_LOG,
            threshold={
                "min_retention_days": 365 * 3,
                "hash_chain_required": True,
            },
        ),
        TemplateRequirement(
            area=CoverageArea.ACCESS_CONTROLS,
            threshold={"rbac_required": True, "abac_required": True},
        ),
        TemplateRequirement(
            area=CoverageArea.WORKFLOW_AUDIT_TRAIL,
            threshold={"min_coverage_pct": 90},
        ),
        TemplateRequirement(
            area=CoverageArea.SECRET_ROTATION,
            threshold={"min_pct_within_window": 90},
        ),
        TemplateRequirement(
            area=CoverageArea.ENCRYPTION,
            threshold={
                "min_tls_version": "1.2",
                "secrets_backend_kinds": [
                    "aws_secrets_manager",
                    "gcp_secret_manager",
                    "azure_key_vault",
                    "hashicorp_vault",
                ],
            },
        ),
    ),
)


TEMPLATE_REGISTRY: dict[str, ComplianceTemplate] = {
    "soc2": SOC_2,
    "hipaa": HIPAA,
    "iso27001": ISO_27001,
}


def get_template(*, slug: str) -> ComplianceTemplate:
    r"""Look up a template by slug. The GraphQL
    \`compliance.reportTemplates\` query enumerates the keys."""
    if slug not in TEMPLATE_REGISTRY:
        raise ComplianceError(
            f"unknown compliance template {slug!r}; known: {sorted(TEMPLATE_REGISTRY.keys())}"
        )
    return TEMPLATE_REGISTRY[slug]


# ---- evidence evaluation -------------------------------------------


class CheckStatus(StrEnum):
    """Per-requirement outcome."""

    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    """Evidence missing or malformed; treated as failure for the
    overall verdict but rendered separately so operators know
    the gap is data-collection vs actual non-compliance."""


@dataclasses.dataclass(frozen=True, slots=True)
class CheckResult:
    """One requirement's result for one org snapshot."""

    area: CoverageArea
    status: CheckStatus
    detail: str
    """Human-readable: 'audit retention 90 days < required 365' /
    'hash chain valid' / 'secret rotation 88% < required 90%'."""


def evaluate_audit_log(
    *,
    threshold: Mapping,
    evidence: Mapping,
) -> CheckResult:
    """Evaluate audit-log requirement. Evidence shape:
    ``{retention_days: int, hash_chain_valid: bool}``."""
    if "retention_days" not in evidence:
        return CheckResult(
            area=CoverageArea.AUDIT_LOG,
            status=CheckStatus.INCONCLUSIVE,
            detail="audit retention evidence missing",
        )

    min_days = threshold.get("min_retention_days", 0)
    if evidence["retention_days"] < min_days:
        return CheckResult(
            area=CoverageArea.AUDIT_LOG,
            status=CheckStatus.FAIL,
            detail=(f"audit retention {evidence['retention_days']}d < required {min_days}d"),
        )

    if threshold.get("hash_chain_required") and not evidence.get(
        "hash_chain_valid",
        False,
    ):
        return CheckResult(
            area=CoverageArea.AUDIT_LOG,
            status=CheckStatus.FAIL,
            detail="audit hash chain required but invalid/absent",
        )

    return CheckResult(
        area=CoverageArea.AUDIT_LOG,
        status=CheckStatus.PASS,
        detail=(f"audit retention {evidence['retention_days']}d, hash chain valid"),
    )


def evaluate_secret_rotation(
    *,
    threshold: Mapping,
    evidence: Mapping,
) -> CheckResult:
    """Evidence: ``{pct_within_window: int}``."""
    if "pct_within_window" not in evidence:
        return CheckResult(
            area=CoverageArea.SECRET_ROTATION,
            status=CheckStatus.INCONCLUSIVE,
            detail="secret rotation evidence missing",
        )

    min_pct = threshold.get("min_pct_within_window", 0)
    pct = evidence["pct_within_window"]
    if pct < min_pct:
        return CheckResult(
            area=CoverageArea.SECRET_ROTATION,
            status=CheckStatus.FAIL,
            detail=f"secret rotation {pct}% < required {min_pct}%",
        )
    return CheckResult(
        area=CoverageArea.SECRET_ROTATION,
        status=CheckStatus.PASS,
        detail=f"secret rotation {pct}% within window",
    )


def evaluate_region_constraints(
    *,
    threshold: Mapping,
    evidence: Mapping,
) -> CheckResult:
    """Evidence: ``{residency_policy_present: bool}``."""
    if not threshold.get("residency_policy_required"):
        return CheckResult(
            area=CoverageArea.REGION_CONSTRAINTS,
            status=CheckStatus.PASS,
            detail="not required by template",
        )
    if evidence.get("residency_policy_present"):
        return CheckResult(
            area=CoverageArea.REGION_CONSTRAINTS,
            status=CheckStatus.PASS,
            detail="org residency policy present",
        )
    return CheckResult(
        area=CoverageArea.REGION_CONSTRAINTS,
        status=CheckStatus.FAIL,
        detail="org residency policy required but absent",
    )


def evaluate_access_controls(
    *,
    threshold: Mapping,
    evidence: Mapping,
) -> CheckResult:
    """Evidence: ``{rbac_active: bool, abac_active: bool}``."""
    if threshold.get("rbac_required") and not evidence.get("rbac_active"):
        return CheckResult(
            area=CoverageArea.ACCESS_CONTROLS,
            status=CheckStatus.FAIL,
            detail="RBAC required but not active",
        )
    if threshold.get("abac_required") and not evidence.get("abac_active"):
        return CheckResult(
            area=CoverageArea.ACCESS_CONTROLS,
            status=CheckStatus.FAIL,
            detail="ABAC required but not active",
        )
    return CheckResult(
        area=CoverageArea.ACCESS_CONTROLS,
        status=CheckStatus.PASS,
        detail="access controls active per template",
    )


def evaluate_workflow_audit_trail(
    *,
    threshold: Mapping,
    evidence: Mapping,
) -> CheckResult:
    """Evidence: ``{coverage_pct: int}`` — % of mutations with
    mutation_audit decorator."""
    if "coverage_pct" not in evidence:
        return CheckResult(
            area=CoverageArea.WORKFLOW_AUDIT_TRAIL,
            status=CheckStatus.INCONCLUSIVE,
            detail="workflow audit coverage evidence missing",
        )
    min_pct = threshold.get("min_coverage_pct", 0)
    pct = evidence["coverage_pct"]
    if pct < min_pct:
        return CheckResult(
            area=CoverageArea.WORKFLOW_AUDIT_TRAIL,
            status=CheckStatus.FAIL,
            detail=f"workflow audit coverage {pct}% < {min_pct}%",
        )
    return CheckResult(
        area=CoverageArea.WORKFLOW_AUDIT_TRAIL,
        status=CheckStatus.PASS,
        detail=f"workflow audit coverage {pct}%",
    )


def evaluate_encryption(
    *,
    threshold: Mapping,
    evidence: Mapping,
) -> CheckResult:
    """Evidence: ``{tls_min_version: str, secrets_backend_kind: str}``."""
    required_tls = threshold.get("min_tls_version", "")
    actual_tls = evidence.get("tls_min_version", "")
    if required_tls and actual_tls < required_tls:
        return CheckResult(
            area=CoverageArea.ENCRYPTION,
            status=CheckStatus.FAIL,
            detail=(f"min TLS {actual_tls} < required {required_tls}"),
        )

    allowed_backends = threshold.get("secrets_backend_kinds", [])
    actual_backend = evidence.get("secrets_backend_kind", "")
    if allowed_backends and actual_backend not in allowed_backends:
        return CheckResult(
            area=CoverageArea.ENCRYPTION,
            status=CheckStatus.FAIL,
            detail=(f"secrets backend {actual_backend!r} not in allowed set {sorted(allowed_backends)}"),
        )

    return CheckResult(
        area=CoverageArea.ENCRYPTION,
        status=CheckStatus.PASS,
        detail=(f"TLS >= {actual_tls}, secrets backend {actual_backend!r}"),
    )


_AREA_EVALUATORS = {
    CoverageArea.AUDIT_LOG: evaluate_audit_log,
    CoverageArea.SECRET_ROTATION: evaluate_secret_rotation,
    CoverageArea.REGION_CONSTRAINTS: evaluate_region_constraints,
    CoverageArea.ACCESS_CONTROLS: evaluate_access_controls,
    CoverageArea.WORKFLOW_AUDIT_TRAIL: evaluate_workflow_audit_trail,
    CoverageArea.ENCRYPTION: evaluate_encryption,
}


@dataclasses.dataclass(frozen=True, slots=True)
class TemplateReport:
    """Aggregated output for one (template, org) pair."""

    template_name: str
    overall: CheckStatus
    checks: tuple[CheckResult, ...]


def evaluate_template(
    *,
    template: ComplianceTemplate,
    evidence_by_area: Mapping[CoverageArea, Mapping],
) -> TemplateReport:
    """Run every requirement against the org's collected evidence.

    Overall verdict:
    - PASS: every check passed
    - FAIL: any FAIL check
    - INCONCLUSIVE: no FAIL but at least one INCONCLUSIVE
    """
    checks: list[CheckResult] = []
    for req in template.requirements:
        evaluator = _AREA_EVALUATORS.get(req.area)
        if evaluator is None:
            checks.append(
                CheckResult(
                    area=req.area,
                    status=CheckStatus.INCONCLUSIVE,
                    detail=f"no evaluator for area {req.area.value!r}",
                )
            )
            continue
        evidence = evidence_by_area.get(req.area, {})
        checks.append(evaluator(threshold=req.threshold, evidence=evidence))

    if any(c.status == CheckStatus.FAIL for c in checks):
        overall = CheckStatus.FAIL
    elif any(c.status == CheckStatus.INCONCLUSIVE for c in checks):
        overall = CheckStatus.INCONCLUSIVE
    else:
        overall = CheckStatus.PASS

    return TemplateReport(
        template_name=template.name,
        overall=overall,
        checks=tuple(checks),
    )
