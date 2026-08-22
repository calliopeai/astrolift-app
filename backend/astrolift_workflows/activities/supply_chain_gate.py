"""
Supply-chain gate for a promote (#313).

``RegisteredApp.security_policy_resolved`` has been persistable (via
``updateAstroliftSecurityPolicy``), readable in GraphQL and rendered in
the per-app Security tab for a while, and ``core.events``'
``SupplyChainBlockedPayload`` documents itself as "emitted by the
PromoteDeploymentWorkflow when the policy resolved off
``RegisteredApp.security_policy_resolved`` short-circuits a promote".
Nothing emitted it: the promote workflow had no gate, so an app with
``blockOnCriticalCves: true`` (the platform default) promoted images with
critical CVEs to production anyway. This activity is that gate.

It is the join between three pieces that already existed and never met:

* the persisted per-app policy (bool + count knobs),
* ``activities.image_scan``'s policy engine (severity thresholds,
  fail-open/fail-closed, ``ScanResult``), and
* the CVE findings the app's own registry already produced — the AWS
  driver provisions ECR with ``scanOnPush`` on, and nothing ever read
  the findings back.

Scanner resolution order: a scanner registered under the cluster's
provider-plugin slug via ``image_scan.register_scanner`` wins (that is
the hook for a Trivy / Grype / Snyk deployment), otherwise the registry
driver's own findings are used when it exposes them. When neither is
available the scan is reported as *failed*, never as clean, and
``ScanPolicy.fail_open_on_scanner_error`` decides what that means.

Signature enforcement (``block_on_missing_signature``) is deliberately
NOT evaluated here. ``activities.image_signing`` needs a verifier and an
allowed-signer allow-list; the repo has neither (no cosign/sigstore
dependency, no column or mutation that declares a signer), and with no
verifier every image would report unsigned and block every promote by
default. The knob is reported on the blocked payload's
``signature_required`` and otherwise left to the verifier work.
"""

from __future__ import annotations

import dataclasses
import logging

from temporalio import activity

from astrolift_workflows.activities.image_scan import (
    CVE,
    ScanDecision,
    ScanPolicy,
    ScanResult,
    Severity,
    evaluate_policy,
    get_scanner,
)

log = logging.getLogger("astrolift_workflows.activities.supply_chain_gate")

# The only severity ScanPolicy can express for the app's
# ``block_on_critical_cves`` boolean is "block at CRITICAL"; HIGH becomes
# the warn tier so the decision reason still names what is one step away
# from blocking.
_BLOCK_AT = Severity.CRITICAL
_WARN_AT = Severity.HIGH

_SEVERITY_KEYS = (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW)


def _empty_counts() -> dict[Severity, int]:
    return dict.fromkeys(_SEVERITY_KEYS, 0)


def _failed_scan(image_uri: str, scanner: str, error: str) -> ScanResult:
    return ScanResult(
        image_uri=image_uri,
        scanner=scanner,
        counts=_empty_counts(),
        findings=(),
        scan_succeeded=False,
        scan_error=error,
    )


def scan_result_from_driver_findings(image_uri: str, scanner: str, raw: dict) -> ScanResult:
    """Map an ``ImageRegistryDriver.get_scan_findings`` dict onto ``ScanResult``.

    Only ``status == "COMPLETE"`` is a usable scan. An image that was
    never scanned, or is still being scanned, comes back with its status
    and is reported as a failed scan — reading "no findings yet" as
    "clean" is exactly how a gate stops gating.
    """
    status = str(raw.get("status") or "UNKNOWN")
    if status != "COMPLETE":
        detail = str(raw.get("description") or "").strip()
        return _failed_scan(
            image_uri,
            scanner,
            f"registry scan status {status}" + (f": {detail}" if detail else ""),
        )

    raw_counts = raw.get("severity_counts") or {}
    counts = _empty_counts()
    for severity in _SEVERITY_KEYS:
        counts[severity] = int(raw_counts.get(severity.value, 0) or 0)

    findings: list[CVE] = []
    for item in raw.get("findings") or ():
        severity_value = str(item.get("severity") or "").lower()
        if severity_value not in counts:
            # INFORMATIONAL / UNDEFINED and anything a future driver
            # invents carry no policy meaning — dropping them keeps the
            # findings list comparable against the severity counts below.
            continue
        findings.append(
            CVE(
                cve_id=str(item.get("cve_id") or ""),
                severity=Severity(severity_value),
                package=str(item.get("package_name") or ""),
                fixed_version=str(item.get("fixed_in_version") or ""),
                description=str(item.get("description") or ""),
            )
        )

    result = ScanResult(
        image_uri=image_uri,
        scanner=scanner,
        counts=counts,
        findings=tuple(findings),
    )

    # ``evaluate_policy`` walks ``findings``, not ``counts``. A driver that
    # reports counts but truncates (or omits) the finding detail would
    # therefore read as PASS on an image with criticals in it. Compare the
    # reported counts against the detail we actually got and refuse to
    # treat the difference as clean.
    observed_counts = _empty_counts()
    for finding in findings:
        observed_counts[finding.severity] += 1
    observed = dataclasses.replace(result, counts=observed_counts)
    if result.count_at_or_above(_BLOCK_AT) > observed.count_at_or_above(_BLOCK_AT):
        return _failed_scan(
            image_uri,
            scanner,
            f"scanner reported {result.count_at_or_above(_BLOCK_AT)} finding(s) at "
            f"{_BLOCK_AT.value} or worse but returned detail for only "
            f"{observed.count_at_or_above(_BLOCK_AT)}",
        )
    return result


def _scan(deployment, app) -> tuple[ScanResult, list[dict]]:
    """Produce a ``ScanResult`` plus the raw per-finding rows for the event.

    ``ScanResult``'s ``CVE`` carries no package *version* (the policy has
    no use for one), so the driver's own finding rows are carried alongside
    it rather than round-tripped through the policy types and lost.
    """
    from core.app_deploy import AppDeployError, cluster_for_deployment, driver_for_capability

    digest = deployment.image_digest or ""
    repo = f"{app.organization.slug}/{app.slug}"
    image_uri = f"{app.registry_repo_uri or repo}@{digest}" if digest else (app.registry_repo_uri or repo)

    if not digest:
        # An unpinned image can't be scanned by digest, and scanning the
        # tag would scan whatever the tag points at now rather than what
        # is being promoted.
        return _failed_scan(image_uri, "none", "deployment has no pinned image digest"), []

    try:
        cluster = cluster_for_deployment(deployment)
    except AppDeployError as exc:
        return _failed_scan(image_uri, "none", str(exc)), []

    plugin_slug = cluster.provider_plugin.slug
    try:
        scanner = get_scanner(plugin_slug)
    except KeyError:
        scanner = None
    if scanner is not None:
        try:
            result = scanner(image_uri)
        except Exception as exc:  # noqa: BLE001 — a scanner blowing up is a scan failure
            return _failed_scan(image_uri, plugin_slug, f"scanner raised: {exc}"), []
        return result, [_finding_row(f) for f in result.findings]

    try:
        driver = driver_for_capability(cluster, "registry")
    except AppDeployError as exc:
        return _failed_scan(image_uri, "none", str(exc)), []
    get_scan_findings = getattr(driver, "get_scan_findings", None)
    if get_scan_findings is None:
        return (
            _failed_scan(
                image_uri,
                plugin_slug,
                f"provider plugin {plugin_slug!r} registry driver reads no scan findings "
                "and no scanner is registered for it",
            ),
            [],
        )
    try:
        raw = get_scan_findings(repo=repo, digest=digest)
    except Exception as exc:  # noqa: BLE001 — a registry API error is a scan failure
        return _failed_scan(image_uri, plugin_slug, f"registry scan read failed: {exc}"), []
    result = scan_result_from_driver_findings(image_uri, plugin_slug, raw)
    if not result.scan_succeeded:
        return result, []
    return result, [dict(row) for row in raw.get("findings") or ()]


def _finding_row(finding: CVE) -> dict:
    """Event-payload shape for one finding, for scanners that hand back
    policy types rather than driver rows."""
    return {
        "cve_id": finding.cve_id,
        "severity": finding.severity.value,
        "package_name": finding.package,
        "fixed_in_version": finding.fixed_version,
        "description": finding.description,
    }


def _cve_summary(result: ScanResult) -> dict[str, int]:
    return {severity.value: int(result.counts.get(severity, 0)) for severity in _SEVERITY_KEYS}


def _emit_scanned(deployment, app, result: ScanResult, findings: list[dict]) -> None:
    from core.events import Event

    Event.emit(
        "image.scanned",
        payload={
            "image_digest": deployment.image_digest,
            "image_tag": deployment.image_tag,
            "scanner": result.scanner,
            "counts": _cve_summary(result),
            "findings": findings,
        },
        resource_kind="deployment",
        resource_id=str(deployment.guid),
        organization_id=app.organization_id,
        registered_app_id=app.pk,
    )


def _emit_blocked(deployment, app, *, reason: str, result: ScanResult, signature_required: bool) -> None:
    from django.utils import timezone

    from core.events import Event, SupplyChainBlockedPayload

    payload = SupplyChainBlockedPayload(
        app_slug=app.slug,
        deployment_guid=str(deployment.guid),
        image_digest=deployment.image_digest or "",
        reason=reason,
        cve_summary=_cve_summary(result) if result.scan_succeeded else None,
        signature_required=signature_required,
        blocked_at=timezone.now(),
    )
    Event.emit(
        "deploy.supply_chain.blocked",
        payload=payload.as_payload(),
        resource_kind="deployment",
        resource_id=str(deployment.guid),
        organization_id=app.organization_id,
        registered_app_id=app.pk,
    )


def _evaluate_supply_chain_gate_sync(deployment_id: int) -> dict:
    from astrolift_lifecycle.models import Deployment

    deployment = Deployment.all_objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster__provider_plugin",
    ).get(pk=deployment_id)
    app = deployment.registered_app
    resolved = app.security_policy_resolved
    block_critical = bool(resolved["block_on_critical_cves"])
    high_threshold = resolved["block_on_high_cve_threshold"]
    signature_required = bool(resolved["block_on_missing_signature"])

    if not block_critical and high_threshold is None:
        # Neither CVE knob is armed, so there is nothing a scan could
        # change about this promote.
        return {
            "decision": ScanDecision.PASS.value,
            "reason": "no CVE gate armed for this app",
            "scanned": False,
        }

    policy = ScanPolicy(block_at=_BLOCK_AT, warn_at=_WARN_AT)
    result, findings = _scan(deployment, app)
    decision = evaluate_policy(result, policy)

    if result.scan_succeeded:
        _emit_scanned(deployment, app, result, findings)

    reason_code = ""
    if decision.decision is ScanDecision.BLOCK and block_critical:
        reason_code = "critical_cves"
    elif (
        result.scan_succeeded
        and high_threshold is not None
        and result.counts.get(Severity.HIGH, 0) >= int(high_threshold)
    ):
        # Count gate, not a severity gate: the app's own field is
        # "block when the high-CVE count is >= N", so criticals are not
        # folded in — an operator who turned block_on_critical_cves off
        # chose to let those through.
        reason_code = "high_cve_threshold_exceeded"

    if not reason_code:
        effective = decision.decision
        if effective is ScanDecision.BLOCK:
            # The only BLOCK ``evaluate_policy`` can return here is the
            # CRITICAL severity gate, and we are past the branch that acts
            # on it — so this app has critical blocking switched off.
            # Downgrade rather than leak a block its policy never asked for.
            effective = ScanDecision.WARN
        log.info(
            "supply-chain gate %s for deploy %s (app=%s): %s",
            effective.value,
            deployment_id,
            app.slug,
            decision.reason,
        )
        return {
            "decision": effective.value,
            "reason": decision.reason,
            "scanned": result.scan_succeeded,
        }

    reason = (
        decision.reason
        if reason_code == "critical_cves"
        else f"{result.counts.get(Severity.HIGH, 0)} high-severity finding(s), threshold {high_threshold}"
    )
    _emit_blocked(
        deployment,
        app,
        reason=reason_code,
        result=result,
        signature_required=signature_required,
    )
    log.warning(
        "supply-chain gate BLOCKED deploy %s (app=%s): %s (%s)",
        deployment_id,
        app.slug,
        reason_code,
        reason,
    )
    return {
        "decision": ScanDecision.BLOCK.value,
        "reason": f"supply-chain policy blocked this promote ({reason_code}): {reason}",
        "reason_code": reason_code,
        "scanned": result.scan_succeeded,
    }


@activity.defn(name="astrolift.deploy.evaluate_supply_chain_gate")
async def evaluate_supply_chain_gate(deployment_id: int) -> dict:
    """Run the app's supply-chain policy against the promote candidate.

    Returns ``{"decision": "pass"|"warn"|"block", "reason": ...}``. The
    workflow fails the promote on ``block``; ``warn`` (including every
    fail-open scanner failure) proceeds. A BLOCK also emits the
    ``deploy.supply_chain.blocked`` event, and any completed scan emits
    ``image.scanned`` — the two event types the per-app Security tab
    reads and that nothing had ever written.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_evaluate_supply_chain_gate_sync)(deployment_id)
