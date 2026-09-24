"""
The promote-time supply-chain gate (#313, #146).

``activities/image_scan.py`` was a complete CVE policy engine with no
caller: ``RegisteredApp.security_policy_resolved`` persisted
``block_on_critical_cves`` (default True), ``core.events`` documented a
``deploy.supply_chain.blocked`` payload as coming from the promote
workflow, and the promote workflow had no gate at all — so an image full
of critical CVEs promoted to production regardless of the policy.

These tests pin the join in both directions: the activity's decision
against a real app row, and the workflow's refusal to run the apply path
once the activity says block.
"""

from __future__ import annotations

import asyncio

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_workflows.activities.image_scan import (
    CVE,
    ScanResult,
    Severity,
    register_scanner,
    unregister_scanner,
)
from astrolift_workflows.activities.supply_chain_gate import (
    _evaluate_supply_chain_gate_sync,
    scan_result_from_driver_findings,
)

pytestmark = pytest.mark.django_db


# The conftest cluster is bound to provider plugin "test-provider"; the
# gate looks a scanner up under the plugin slug.
_PLUGIN = "test-provider"


@pytest.fixture
def scanner():
    """Register a scanner for the fixture cluster's provider plugin and
    hand back a mutable holder for what it should return."""
    holder: dict[str, ScanResult] = {}

    def _scan(image_uri: str) -> ScanResult:
        holder["called_with"] = image_uri
        return holder["result"]

    register_scanner(_PLUGIN, _scan)
    yield holder
    unregister_scanner(_PLUGIN)


def _result(*findings: CVE, image_uri: str = "acme/hello-app@sha256:d") -> ScanResult:
    counts: dict[Severity, int] = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return ScanResult(
        image_uri=image_uri,
        scanner="fake",
        counts=counts,
        findings=findings,
    )


def _promotion(app, env, *, digest="sha256:" + "a" * 64):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.PROMOTION.value,
        status=Deployment.Status.PENDING.value,
        image_tag="v2.0.0",
        image_digest=digest,
        config_snapshot={"image": "v2.0.0"},
    )


def _events(event_type: str):
    from astrolift_operations.models import Event

    return list(Event.objects.filter(event_type=event_type))


# ---- the gate decision ------------------------------------------------


def test_critical_cve_blocks_the_promote(app, env, scanner):
    """The default policy (block_on_critical_cves=True) must refuse an
    image carrying a critical finding. Before the gate existed this
    promote ran the whole apply path."""
    deployment = _promotion(app, env)
    scanner["result"] = _result(CVE(cve_id="CVE-2026-1", severity=Severity.CRITICAL, package="openssl"))

    gate = _evaluate_supply_chain_gate_sync(deployment.pk)

    assert gate["decision"] == "block"
    assert gate["reason_code"] == "critical_cves"


def test_block_emits_the_documented_supply_chain_event(app, env, scanner):
    """``SupplyChainBlockedPayload``'s own header says the promote workflow
    emits it; nothing ever did. The payload must carry external-stable ids
    only and one of the three legal reason codes."""
    deployment = _promotion(app, env)
    scanner["result"] = _result(CVE(cve_id="CVE-2026-1", severity=Severity.CRITICAL))

    _evaluate_supply_chain_gate_sync(deployment.pk)

    events = _events("deploy.supply_chain.blocked")
    assert len(events) == 1
    payload = events[0].payload
    assert payload["app_slug"] == app.slug
    assert payload["deployment_guid"] == str(deployment.guid)
    assert payload["reason"] == "critical_cves"
    assert payload["cve_summary"]["critical"] == 1
    assert payload["signature_required"] is True
    assert "id" not in payload


def test_clean_image_passes_and_emits_image_scanned(app, env, scanner):
    """A PASS must not block, and the completed scan must show up as the
    ``image.scanned`` event the per-app Security tab polls for."""
    deployment = _promotion(app, env)
    scanner["result"] = _result()

    gate = _evaluate_supply_chain_gate_sync(deployment.pk)

    assert gate["decision"] == "pass"
    assert _events("deploy.supply_chain.blocked") == []
    scanned = _events("image.scanned")
    assert len(scanned) == 1
    assert scanned[0].payload["image_digest"] == deployment.image_digest
    assert scanned[0].payload["counts"] == {"critical": 0, "high": 0, "medium": 0, "low": 0}


def test_policy_off_skips_the_scan_entirely(app, env, scanner):
    """An operator who cleared both CVE knobs pays for no scan — and the
    scanner must not even be consulted."""
    app.security_policy = {
        "block_on_critical_cves": False,
        "block_on_missing_signature": True,
        "block_on_high_cve_threshold": None,
    }
    app.save(update_fields=["security_policy", "updated_at", "version"])
    deployment = _promotion(app, env)
    scanner["result"] = _result(CVE(cve_id="CVE-2026-1", severity=Severity.CRITICAL))

    gate = _evaluate_supply_chain_gate_sync(deployment.pk)

    assert gate["decision"] == "pass"
    assert gate["scanned"] is False
    assert "called_with" not in scanner


def test_high_count_threshold_blocks_on_its_own(app, env, scanner):
    """``block_on_high_cve_threshold`` is a count gate with no severity
    equivalent in ScanPolicy — it has to be evaluated separately, and it
    has to work with critical blocking switched off."""
    app.security_policy = {
        "block_on_critical_cves": False,
        "block_on_missing_signature": False,
        "block_on_high_cve_threshold": 2,
    }
    app.save(update_fields=["security_policy", "updated_at", "version"])
    deployment = _promotion(app, env)
    scanner["result"] = _result(
        CVE(cve_id="CVE-2026-2", severity=Severity.HIGH),
        CVE(cve_id="CVE-2026-3", severity=Severity.HIGH),
    )

    gate = _evaluate_supply_chain_gate_sync(deployment.pk)

    assert gate["decision"] == "block"
    assert gate["reason_code"] == "high_cve_threshold_exceeded"
    assert _events("deploy.supply_chain.blocked")[0].payload["reason"] == "high_cve_threshold_exceeded"


def test_criticals_do_not_satisfy_the_high_count_gate(app, env, scanner):
    """With critical blocking explicitly off, criticals must not be folded
    into the high count — that would block exactly the promote the
    operator chose to allow."""
    app.security_policy = {
        "block_on_critical_cves": False,
        "block_on_missing_signature": False,
        "block_on_high_cve_threshold": 2,
    }
    app.save(update_fields=["security_policy", "updated_at", "version"])
    deployment = _promotion(app, env)
    scanner["result"] = _result(
        CVE(cve_id="CVE-2026-4", severity=Severity.CRITICAL),
        CVE(cve_id="CVE-2026-5", severity=Severity.CRITICAL),
        CVE(cve_id="CVE-2026-6", severity=Severity.HIGH),
    )

    gate = _evaluate_supply_chain_gate_sync(deployment.pk)

    assert gate["decision"] == "warn"
    assert "reason_code" not in gate


def test_unpinned_image_cannot_be_scanned_and_fails_open(app, env, scanner):
    """No digest means no scan target. The policy's default is fail-open,
    so this warns rather than blocking — but it must not report a clean
    scan, and it must not emit image.scanned."""
    deployment = _promotion(app, env, digest="")
    scanner["result"] = _result()

    gate = _evaluate_supply_chain_gate_sync(deployment.pk)

    assert gate["decision"] == "warn"
    assert gate["scanned"] is False
    assert "no pinned image digest" in gate["reason"]
    assert _events("image.scanned") == []


def test_unresolvable_registry_driver_is_a_failed_scan(app, env):
    """The fixture plugin registers no scanner and no registry driver at
    all, so the driver lookup raises. That is "unknown", not "clean" — the
    gate must route it through the scanner-error branch."""
    deployment = _promotion(app, env)

    gate = _evaluate_supply_chain_gate_sync(deployment.pk)

    assert gate["decision"] == "warn"
    assert gate["scanned"] is False
    assert "scanner failed" in gate["reason"]
    assert "registry" in gate["reason"]


def test_registry_driver_without_scan_support_is_a_failed_scan(app, env, monkeypatch):
    """A provider whose registry driver can't read findings (GCP, Azure,
    plain OCI today) must not promote as if the image came back clean."""
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, capability: object())
    deployment = _promotion(app, env)

    gate = _evaluate_supply_chain_gate_sync(deployment.pk)

    assert gate["decision"] == "warn"
    assert gate["scanned"] is False
    assert "reads no scan findings" in gate["reason"]
    assert _events("image.scanned") == []


def test_registry_driver_findings_block_the_promote(app, env, monkeypatch):
    """The production AWS path end to end: no scanner registered, the ECR
    driver's own scanOnPush findings are read back and gated on."""

    class _Driver:
        def get_scan_findings(self, *, repo, digest):
            assert repo == f"{app.organization.slug}/{app.slug}"
            assert digest == deployment.image_digest
            return {
                "status": "COMPLETE",
                "severity_counts": {"critical": 1, "high": 0, "medium": 0, "low": 0},
                "findings": [
                    {
                        "cve_id": "CVE-2026-10",
                        "severity": "critical",
                        "package_name": "openssl",
                        "package_version": "1.1.1",
                        "fixed_in_version": "1.1.1w",
                    }
                ],
            }

    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, capability: _Driver())
    deployment = _promotion(app, env)

    gate = _evaluate_supply_chain_gate_sync(deployment.pk)

    assert gate["decision"] == "block"
    assert gate["reason_code"] == "critical_cves"
    # The driver's own rows reach the event, so the version ECR reported
    # isn't lost on the way through the policy types.
    scanned = _events("image.scanned")[0]
    assert scanned.payload["findings"][0]["package_version"] == "1.1.1"


# ---- driver findings -> ScanResult ------------------------------------


def test_incomplete_registry_scan_is_not_clean():
    """An image ECR has not finished scanning has zero findings. Reading
    that as PASS is how the gate would silently stop gating."""
    result = scan_result_from_driver_findings(
        "acme/hello@sha256:d",
        "aws",
        {"status": "IN_PROGRESS", "description": "scan in progress", "findings": []},
    )

    assert result.scan_succeeded is False
    assert "IN_PROGRESS" in result.scan_error


def test_counts_without_finding_detail_is_a_failed_scan():
    """``evaluate_policy`` walks findings, not counts. A driver reporting
    two criticals with an empty finding list would evaluate to PASS, so the
    mapper refuses the result instead."""
    result = scan_result_from_driver_findings(
        "acme/hello@sha256:d",
        "aws",
        {
            "status": "COMPLETE",
            "severity_counts": {"critical": 2, "high": 0, "medium": 0, "low": 0},
            "findings": [],
        },
    )

    assert result.scan_succeeded is False
    assert "returned detail for only 0" in result.scan_error


def test_complete_registry_scan_maps_onto_the_policy_types():
    result = scan_result_from_driver_findings(
        "acme/hello@sha256:d",
        "aws",
        {
            "status": "COMPLETE",
            "severity_counts": {"critical": 1, "high": 0, "medium": 1, "low": 0},
            "findings": [
                {
                    "cve_id": "CVE-2026-7",
                    "severity": "critical",
                    "package_name": "openssl",
                    "package_version": "1.1.1",
                    "fixed_in_version": "1.1.1w",
                    "description": "boom",
                },
                {"cve_id": "CVE-2026-8", "severity": "medium", "package_name": "zlib"},
                # INFORMATIONAL is not a policy severity and is dropped.
                {"cve_id": "CVE-2026-9", "severity": "informational"},
            ],
        },
    )

    assert result.scan_succeeded is True
    assert result.counts[Severity.CRITICAL] == 1
    assert [f.cve_id for f in result.findings] == ["CVE-2026-7", "CVE-2026-8"]
    assert result.findings[0].fixed_version == "1.1.1w"


# ---- the workflow honours the gate ------------------------------------


def _run_promote(monkeypatch, gate_result: dict) -> tuple[object, list[str]]:
    from temporalio import workflow as temporalio_workflow

    from astrolift_workflows.activities import (
        create_promotion_deployment,
        evaluate_supply_chain_gate,
    )
    from astrolift_workflows.inputs import Actor, PromoteInput
    from astrolift_workflows.workflows.promote_deployment import PromoteDeploymentWorkflow

    schedule_log: list[str] = []

    async def _fake_execute_activity(activity_fn, *args, **kwargs):
        schedule_log.append(getattr(activity_fn, "__name__", str(activity_fn)))
        if activity_fn is create_promotion_deployment:
            return 4242
        if activity_fn is evaluate_supply_chain_gate:
            return gate_result
        return None

    monkeypatch.setattr(temporalio_workflow, "execute_activity", _fake_execute_activity)
    # run() is called outside a workflow, so there is no history for
    # patched() to consult; answer as a fresh run does.
    monkeypatch.setattr(temporalio_workflow, "patched", lambda patch_id: True)

    result = asyncio.new_event_loop().run_until_complete(
        PromoteDeploymentWorkflow().run(
            PromoteInput(
                source_deployment_id=1,
                target_app_environment_id=2,
                actor=Actor(kind="user", user_id=1, display="tester"),
            ),
        ),
    )
    return result, schedule_log


def test_workflow_stops_the_apply_path_on_block(monkeypatch):
    """The point of the gate: a blocked promote must not reach pre_flight,
    render, or apply, and the row must land failed rather than sitting
    pending forever."""
    result, schedule_log = _run_promote(
        monkeypatch,
        {"decision": "block", "reason": "critical CVEs", "reason_code": "critical_cves"},
    )

    assert result.ok is False
    assert result.message == "critical CVEs"
    assert schedule_log == [
        "create_promotion_deployment",
        "evaluate_supply_chain_gate",
        "mark_failed",
    ]


def test_workflow_gates_before_it_deploys_and_proceeds_on_pass(monkeypatch):
    """A passing gate changes nothing about the apply path — but it has to
    be consulted before the first step that touches the cluster."""
    result, schedule_log = _run_promote(monkeypatch, {"decision": "pass", "reason": "clean"})

    assert result.ok is True
    assert "mark_failed" not in schedule_log
    assert schedule_log.index("evaluate_supply_chain_gate") < schedule_log.index("pre_flight")
    assert schedule_log.index("evaluate_supply_chain_gate") < schedule_log.index("apply_manifests")
