"""
Image vulnerability scanning policy + activity contract (#146,
spec 06 §6).

The deploy workflow calls ``scan_image_for_vulns(image_uri, policy)``
between push-to-registry and promote. Returns ``ScanResult`` with
severity counts + CVE list. The policy decides whether the result
**blocks** the deploy, **warns** but proceeds, or **passes**.

Why split policy from execution:

- The actual scanner call (Trivy / Grype / Snyk / cloud API) lives
  in driver land via a registered ``scanner_callable``. This module
  is the policy-and-contract layer that decides what to do with
  whatever the scanner returns.
- Override semantics are subtle: emergency deploys can skip scan,
  but only with an explicit audit-stamped reason. We model the
  override as a separate ``apply_override`` step so the audit log
  always sees the original scan decision plus the override action.
- Tests run without real images.

Timeout handling: the activity has a hard ceiling (default 5 min).
Scanner unavailable for that long → policy decides 'fail open'
(warn, allow deploy) or 'fail closed' (block). Default fail-open;
operators can flip per-org for high-security installs.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from enum import Enum


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ScanDecision(str, Enum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


@dataclasses.dataclass(frozen=True, slots=True)
class CVE:
    """One vulnerability finding."""

    cve_id: str
    severity: Severity
    package: str = ""
    fixed_version: str = ""
    description: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class ScanResult:
    """The scanner output shape — providers map their native output
    onto this. The deploy workflow stores this on the Deployment row."""

    image_uri: str
    scanner: str  # human label, e.g. 'trivy', 'grype'
    counts: dict[Severity, int]
    findings: tuple[CVE, ...]
    scan_succeeded: bool = True
    scan_error: str = ""

    def count_at_or_above(self, threshold: Severity) -> int:
        """Sum of findings at ``threshold`` or worse — handy for
        the policy threshold checks."""
        order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]
        idx = order.index(threshold)
        return sum(self.counts.get(s, 0) for s in order[: idx + 1])


@dataclasses.dataclass(frozen=True, slots=True)
class ScanPolicy:
    """The per-org / per-app policy on what to do with scan results.

    ``block_at`` is the severity level that **blocks** the deploy
    when any finding meets it. ``warn_at`` is the severity that
    triggers a warning but proceeds. ``warn_at`` weaker than or
    equal to ``block_at`` (warn doesn't block at the block level).
    """

    block_at: Severity = Severity.HIGH  # default: block on HIGH+
    warn_at: Severity = Severity.MEDIUM  # default: warn on MEDIUM
    fail_open_on_scanner_error: bool = True
    """When True, a scanner timeout/error returns WARN (deploy goes
    through). When False, returns BLOCK (fail-closed). Default
    True so a flaky scanner doesn't bring deploys down; high-security
    installs flip to False."""

    timeout_seconds: int = 300

    def __post_init__(self) -> None:
        order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]
        if order.index(self.warn_at) < order.index(self.block_at):
            raise ValueError(
                "warn_at cannot be stricter than block_at " "(warn_at must be the same or weaker severity)"
            )
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclasses.dataclass(frozen=True, slots=True)
class PolicyDecision:
    decision: ScanDecision
    blocking_findings: tuple[CVE, ...]
    warning_findings: tuple[CVE, ...]
    reason: str


def evaluate_policy(result: ScanResult, policy: ScanPolicy) -> PolicyDecision:
    """Apply ``policy`` to ``result``. Pure."""
    if not result.scan_succeeded:
        if policy.fail_open_on_scanner_error:
            return PolicyDecision(
                decision=ScanDecision.WARN,
                blocking_findings=(),
                warning_findings=(),
                reason=f"scanner failed (fail-open): {result.scan_error}",
            )
        return PolicyDecision(
            decision=ScanDecision.BLOCK,
            blocking_findings=(),
            warning_findings=(),
            reason=f"scanner failed (fail-closed): {result.scan_error}",
        )

    order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]
    block_idx = order.index(policy.block_at)
    warn_idx = order.index(policy.warn_at)

    blocking: list[CVE] = []
    warning: list[CVE] = []
    for f in result.findings:
        f_idx = order.index(f.severity)
        if f_idx <= block_idx:
            blocking.append(f)
        elif f_idx <= warn_idx:
            warning.append(f)

    if blocking:
        return PolicyDecision(
            decision=ScanDecision.BLOCK,
            blocking_findings=tuple(blocking),
            warning_findings=tuple(warning),
            reason=(f"{len(blocking)} finding(s) at {policy.block_at.value} or worse"),
        )
    if warning:
        return PolicyDecision(
            decision=ScanDecision.WARN,
            blocking_findings=(),
            warning_findings=tuple(warning),
            reason=f"{len(warning)} finding(s) at {policy.warn_at.value} severity",
        )
    return PolicyDecision(
        decision=ScanDecision.PASS,
        blocking_findings=(),
        warning_findings=(),
        reason="no findings at warn or block thresholds",
    )


# ---- override --------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ScanOverride:
    """Operator-applied override on a BLOCK decision. Audit trail
    requires a non-empty reason and the user id; the workflow
    activity refuses to proceed without both."""

    by_user_id: int
    reason: str

    def __post_init__(self) -> None:
        if not self.reason or len(self.reason.strip()) < 10:
            raise ValueError("override reason must be at least 10 characters " "(operator must explain why)")
        if self.by_user_id <= 0:
            raise ValueError("by_user_id must be positive")


def apply_override(decision: PolicyDecision, override: ScanOverride | None) -> PolicyDecision:
    """Apply an override. Returns a new decision; the original
    decision's findings are preserved on the new one so the audit
    log shows both the original BLOCK and the override.

    Override only applies to BLOCK decisions — overriding a PASS
    or WARN is a no-op.
    """
    if override is None or decision.decision != ScanDecision.BLOCK:
        return decision
    return PolicyDecision(
        decision=ScanDecision.WARN,
        blocking_findings=decision.blocking_findings,
        warning_findings=decision.warning_findings,
        reason=(f"BLOCK overridden by user {override.by_user_id}: " f"{override.reason}"),
    )


# ---- scanner registry / activity contract ----------------------------


ScannerCallable = Callable[[str], ScanResult]
"""Scanner contract: ``scanner(image_uri) -> ScanResult``. Live
implementations call out to Trivy / Grype / vendor APIs and map
the response onto our types."""


_SCANNERS: dict[str, ScannerCallable] = {}


def register_scanner(name: str, scanner: ScannerCallable) -> None:
    _SCANNERS[name] = scanner


def get_scanner(name: str) -> ScannerCallable:
    if name not in _SCANNERS:
        raise KeyError(f"no scanner registered for {name!r}; " f"available: {sorted(_SCANNERS)}")
    return _SCANNERS[name]


def unregister_scanner(name: str) -> None:
    _SCANNERS.pop(name, None)
