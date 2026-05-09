"""Tests for SyncAppDomainWorkflow policy (#143, spec 06 §4.17)."""

from __future__ import annotations

import pytest

from astrolift_workflows.sync_domain import (
    CERT_ISSUANCE_MAX_SECONDS,
    DNS_PROPAGATION_MAX_SECONDS,
    SYNC_ORDER,
    HostnameAction,
    SyncDomainError,
    SyncProgress,
    SyncStep,
    diff_hostnames,
    is_covered_by_wildcard,
    is_rollback_needed,
    needs_per_domain_cert,
    rollback_steps,
    step_deadline_seconds,
)

# ---- step ordering -------------------------------------------------


def test_sync_order_locked():
    """Spec §4.17: DNS first, verification last."""
    assert SYNC_ORDER[0] == SyncStep.UPDATE_DNS
    assert SYNC_ORDER[-1] == SyncStep.VERIFY_E2E
    # Cert request before propagation wait
    assert (
        SYNC_ORDER.index(SyncStep.REQUEST_CERT)
        < SYNC_ORDER.index(SyncStep.WAIT_PROPAGATION)
    )


# ---- hostname diff -------------------------------------------------


def test_diff_no_change():
    out = diff_hostnames(
        old=["api.acme.com"], new=["api.acme.com"],
    )
    assert len(out) == 1
    assert out[0].action == HostnameAction.KEEP


def test_diff_add_only():
    out = diff_hostnames(old=[], new=["api.acme.com"])
    assert len(out) == 1
    assert out[0].action == HostnameAction.ADD
    assert out[0].hostname == "api.acme.com"


def test_diff_remove_only():
    out = diff_hostnames(old=["old.acme.com"], new=[])
    assert len(out) == 1
    assert out[0].action == HostnameAction.REMOVE


def test_diff_removes_before_adds():
    """Spec rationale: free routing-table capacity by removing
    first, so adds don't hit per-controller route caps."""
    out = diff_hostnames(
        old=["old.acme.com"], new=["new.acme.com"],
    )
    assert len(out) == 2
    assert out[0].action == HostnameAction.REMOVE
    assert out[1].action == HostnameAction.ADD


def test_diff_lowercases():
    """RFC 1035 case-insensitive; canonicalize."""
    out = diff_hostnames(
        old=["API.ACME.COM"], new=["api.acme.com"],
    )
    # Same hostname → KEEP
    assert len(out) == 1
    assert out[0].action == HostnameAction.KEEP


def test_diff_deterministic_order():
    """Multiple adds: alphabetical for stability."""
    out = diff_hostnames(
        old=[], new=["c.acme.com", "a.acme.com", "b.acme.com"],
    )
    assert [d.hostname for d in out] == [
        "a.acme.com", "b.acme.com", "c.acme.com",
    ]


# ---- wildcard cert coverage ----------------------------------------


def test_wildcard_one_label_deeper_matches():
    """RFC 6125: *.example.com matches one label deep."""
    assert is_covered_by_wildcard(
        hostname="api.acme.com",
        wildcard_sans=["*.acme.com"],
    ) is True


def test_wildcard_two_labels_deeper_does_not_match():
    """RFC 6125: *.acme.com does NOT match foo.bar.acme.com."""
    assert is_covered_by_wildcard(
        hostname="foo.bar.acme.com",
        wildcard_sans=["*.acme.com"],
    ) is False


def test_wildcard_apex_does_not_match():
    """*.acme.com matches subdomains, NOT the apex acme.com."""
    assert is_covered_by_wildcard(
        hostname="acme.com",
        wildcard_sans=["*.acme.com"],
    ) is False


def test_exact_san_match():
    """Non-wildcard SAN matches exactly."""
    assert is_covered_by_wildcard(
        hostname="acme.com",
        wildcard_sans=["acme.com"],
    ) is True


def test_no_wildcards_covered():
    assert is_covered_by_wildcard(
        hostname="anything.com", wildcard_sans=[],
    ) is False


def test_needs_per_domain_cert_when_no_match():
    assert needs_per_domain_cert(
        hostname="api.acme.com",
        wildcard_sans=["*.globex.com"],
    ) is True


def test_needs_per_domain_cert_false_when_covered():
    assert needs_per_domain_cert(
        hostname="api.acme.com",
        wildcard_sans=["*.acme.com"],
    ) is False


def test_needs_per_domain_cert_no_hostname_rejected():
    with pytest.raises(SyncDomainError):
        needs_per_domain_cert(hostname="")


# ---- rollback ------------------------------------------------------


def test_rollback_reverses_completed_state_changing_steps():
    """Three steps done; rollback reverses in REVERSE order."""
    progress = SyncProgress(
        completed_steps=(
            SyncStep.UPDATE_DNS,
            SyncStep.PATCH_INGRESS,
            SyncStep.REQUEST_CERT,
        ),
        failed_step=SyncStep.WAIT_PROPAGATION,
    )
    rb = rollback_steps(progress=progress)
    assert rb == (
        SyncStep.REQUEST_CERT,
        SyncStep.PATCH_INGRESS,
        SyncStep.UPDATE_DNS,
    )


def test_rollback_skips_observation_steps():
    """WAIT + VERIFY are observation; nothing to undo."""
    progress = SyncProgress(
        completed_steps=(
            SyncStep.UPDATE_DNS, SyncStep.WAIT_PROPAGATION,
        ),
        failed_step=SyncStep.VERIFY_E2E,
    )
    rb = rollback_steps(progress=progress)
    assert rb == (SyncStep.UPDATE_DNS,)


def test_rollback_not_needed_when_no_completed():
    """Failed at the very first step — nothing to roll back."""
    progress = SyncProgress(
        completed_steps=(), failed_step=SyncStep.UPDATE_DNS,
    )
    assert is_rollback_needed(progress=progress) is False


def test_rollback_not_needed_when_no_failure():
    """Workflow succeeded; no rollback."""
    progress = SyncProgress(
        completed_steps=SYNC_ORDER, failed_step=None,
    )
    assert is_rollback_needed(progress=progress) is False


def test_rollback_needed_after_state_change_step():
    progress = SyncProgress(
        completed_steps=(SyncStep.UPDATE_DNS,),
        failed_step=SyncStep.PATCH_INGRESS,
    )
    assert is_rollback_needed(progress=progress) is True


# ---- step deadlines -----------------------------------------------


def test_cert_step_gets_longest_budget():
    """Cert issuance is the slow path; ensure it has the
    longest budget."""
    cert = step_deadline_seconds(step=SyncStep.REQUEST_CERT)
    other_steps = [
        SyncStep.UPDATE_DNS, SyncStep.PATCH_INGRESS, SyncStep.VERIFY_E2E,
    ]
    other_max = max(step_deadline_seconds(step=s) for s in other_steps)
    assert cert >= other_max
    assert cert == CERT_ISSUANCE_MAX_SECONDS


def test_propagation_deadline_locked():
    assert (
        step_deadline_seconds(step=SyncStep.WAIT_PROPAGATION)
        == DNS_PROPAGATION_MAX_SECONDS
    )


def test_step_deadline_unknown_step():
    class Fake:
        pass
    with pytest.raises(SyncDomainError):
        step_deadline_seconds(step=Fake())  # type: ignore[arg-type]
