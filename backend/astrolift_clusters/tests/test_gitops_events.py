"""Tests for GitOps sync event ingestion (#274, spec 07 §12)."""

from __future__ import annotations

import pytest

from astrolift_clusters.gitops_events import (
    ANNOTATION_DEPLOYMENT_ID,
    DEDUP_WINDOW_SECONDS,
    DEFAULT_PULL_INTERVAL_SECONDS,
    EventSeverity,
    GitopsEventError,
    GitopsProvider,
    IncomingEvent,
    PlatformEventType,
    fingerprint_for,
    is_duplicate,
    is_for_us,
    map_argocd_event,
    map_flux_event,
    resolve,
    resolve_deployment_id,
    severity_for,
    validate_pull_interval,
)

# ---- severity ------------------------------------------------------


def test_failure_is_warning():
    assert (
        severity_for(
            event_type=PlatformEventType.GITOPS_SYNC_FAILED,
        )
        == EventSeverity.WARNING
    )


def test_health_degraded_is_warning():
    assert (
        severity_for(
            event_type=PlatformEventType.GITOPS_HEALTH_DEGRADED,
        )
        == EventSeverity.WARNING
    )


def test_succeeded_is_info():
    assert (
        severity_for(
            event_type=PlatformEventType.GITOPS_SYNC_SUCCEEDED,
        )
        == EventSeverity.INFO
    )


def test_started_is_info():
    """Sync starts shouldn't page anyone — informational only."""
    assert (
        severity_for(
            event_type=PlatformEventType.GITOPS_SYNC_STARTED,
        )
        == EventSeverity.INFO
    )


# ---- ArgoCD event mapping ------------------------------------------


@pytest.mark.parametrize(
    "template,expected",
    [
        ("on-sync-started", PlatformEventType.GITOPS_SYNC_STARTED),
        ("on-sync-running", PlatformEventType.GITOPS_SYNC_STARTED),
        ("on-sync-succeeded", PlatformEventType.GITOPS_SYNC_SUCCEEDED),
        ("on-sync-failed", PlatformEventType.GITOPS_SYNC_FAILED),
        ("on-sync-status-unknown", PlatformEventType.GITOPS_SYNC_FAILED),
        ("on-health-degraded", PlatformEventType.GITOPS_HEALTH_DEGRADED),
    ],
)
def test_argocd_event_mapping(template, expected):
    assert map_argocd_event(template_name=template) == expected


def test_argocd_unknown_template_returns_none():
    """Unknown template = handler acks 200 and moves on."""
    assert map_argocd_event(template_name="on-something-custom") is None


# ---- Flux event mapping --------------------------------------------


def test_flux_progressing_is_started():
    assert (
        map_flux_event(
            severity="info",
            reason="Progressing",
        )
        == PlatformEventType.GITOPS_SYNC_STARTED
    )


def test_flux_reconciliation_succeeded():
    assert (
        map_flux_event(
            severity="info",
            reason="ReconciliationSucceeded",
        )
        == PlatformEventType.GITOPS_SYNC_SUCCEEDED
    )


def test_flux_reconciliation_failed():
    assert (
        map_flux_event(
            severity="error",
            reason="ReconciliationFailed",
        )
        == PlatformEventType.GITOPS_SYNC_FAILED
    )


def test_flux_health_check_failed():
    assert (
        map_flux_event(
            severity="error",
            reason="HealthCheckFailed",
        )
        == PlatformEventType.GITOPS_HEALTH_DEGRADED
    )


def test_flux_severity_case_insensitive():
    """Flux capitalization varies in the wild."""
    assert (
        map_flux_event(
            severity="INFO",
            reason="Progressing",
        )
        == PlatformEventType.GITOPS_SYNC_STARTED
    )


def test_flux_unknown_combination_returns_none():
    """Many Flux reasons (SourceRefNotReady etc.) are part of
    natural progression — don't bubble them up."""
    assert (
        map_flux_event(
            severity="info",
            reason="SourceRefNotReady",
        )
        is None
    )


# ---- deployment resolution -----------------------------------------


def test_resolve_deployment_id_basic():
    annotations = {ANNOTATION_DEPLOYMENT_ID: "42"}
    assert resolve_deployment_id(annotations=annotations) == 42


def test_resolve_deployment_id_missing():
    """Missing annotation → 400-class error so handler returns
    bad-request rather than 500."""
    with pytest.raises(GitopsEventError, match="missing"):
        resolve_deployment_id(annotations={})


def test_resolve_deployment_id_unparseable():
    with pytest.raises(GitopsEventError, match="not a valid integer"):
        resolve_deployment_id(
            annotations={ANNOTATION_DEPLOYMENT_ID: "not-a-number"},
        )


def test_resolve_deployment_id_zero_rejected():
    with pytest.raises(GitopsEventError, match="must be > 0"):
        resolve_deployment_id(
            annotations={ANNOTATION_DEPLOYMENT_ID: "0"},
        )


def test_resolve_deployment_id_negative_rejected():
    with pytest.raises(GitopsEventError):
        resolve_deployment_id(
            annotations={ANNOTATION_DEPLOYMENT_ID: "-5"},
        )


# ---- is_for_us -----------------------------------------------------


def test_is_for_us_with_annotation():
    """Pre-filter so handler can ack 200 and skip rather than 400
    on third-party Applications in mixed-ArgoCD setups."""
    assert (
        is_for_us(
            annotations={ANNOTATION_DEPLOYMENT_ID: "1"},
        )
        is True
    )


def test_is_for_us_without_annotation():
    assert is_for_us(annotations={}) is False
    assert (
        is_for_us(
            annotations={"app.kubernetes.io/name": "third-party"},
        )
        is False
    )


# ---- resolve full event --------------------------------------------


def test_resolve_full_argocd_failure():
    event = IncomingEvent(
        provider=GitopsProvider.ARGOCD,
        connection_id=1,
        event_type=PlatformEventType.GITOPS_SYNC_FAILED,
        annotations={ANNOTATION_DEPLOYMENT_ID: "42"},
        revision="abc1234",
        message="image pull failed: registry unreachable",
    )
    resolved = resolve(event=event)
    assert resolved.deployment_id == 42
    assert resolved.event_type == PlatformEventType.GITOPS_SYNC_FAILED
    assert resolved.severity == EventSeverity.WARNING
    assert resolved.revision == "abc1234"
    assert "image pull failed" in resolved.message


def test_resolve_truncates_long_message():
    """Bound event size so a single noisy reconciler can't blow
    up the events table."""
    long_msg = "x" * 5000
    event = IncomingEvent(
        provider=GitopsProvider.FLUX,
        connection_id=1,
        event_type=PlatformEventType.GITOPS_SYNC_SUCCEEDED,
        annotations={ANNOTATION_DEPLOYMENT_ID: "1"},
        revision="r",
        message=long_msg,
    )
    resolved = resolve(event=event)
    assert len(resolved.message) <= 1024


def test_resolve_short_message_passes_through():
    event = IncomingEvent(
        provider=GitopsProvider.ARGOCD,
        connection_id=1,
        event_type=PlatformEventType.GITOPS_SYNC_SUCCEEDED,
        annotations={ANNOTATION_DEPLOYMENT_ID: "1"},
        revision="r",
        message="ok",
    )
    resolved = resolve(event=event)
    assert resolved.message == "ok"


# ---- pull mode interval --------------------------------------------


def test_pull_interval_default():
    assert DEFAULT_PULL_INTERVAL_SECONDS == 60


def test_pull_interval_below_min_rejected():
    """5s polling would hammer reconciler API."""
    with pytest.raises(GitopsEventError, match="below minimum"):
        validate_pull_interval(seconds=5)


def test_pull_interval_above_max_rejected():
    """1h polling = state too stale."""
    with pytest.raises(GitopsEventError, match="exceeds maximum"):
        validate_pull_interval(seconds=3600)


def test_pull_interval_within_bounds():
    assert validate_pull_interval(seconds=60) == 60
    assert validate_pull_interval(seconds=300) == 300


# ---- deduplication -------------------------------------------------


def _resolved(rev="abc"):
    return resolve(
        event=IncomingEvent(
            provider=GitopsProvider.ARGOCD,
            connection_id=1,
            event_type=PlatformEventType.GITOPS_SYNC_SUCCEEDED,
            annotations={ANNOTATION_DEPLOYMENT_ID: "1"},
            revision=rev,
        )
    )


def test_first_event_not_duplicate():
    fp = fingerprint_for(event=_resolved())
    assert (
        is_duplicate(
            fingerprint=fp,
            last_seen_unix=None,
            now_unix=1_700_000_000,
        )
        is False
    )


def test_within_window_is_duplicate():
    fp = fingerprint_for(event=_resolved())
    assert (
        is_duplicate(
            fingerprint=fp,
            last_seen_unix=1_700_000_000,
            now_unix=1_700_000_010,
        )
        is True
    )


def test_after_window_not_duplicate():
    """ArgoCD/Flux can re-emit later as a separate transition;
    the dedup window only suppresses the immediate flap."""
    fp = fingerprint_for(event=_resolved())
    assert (
        is_duplicate(
            fingerprint=fp,
            last_seen_unix=1_700_000_000,
            now_unix=1_700_000_000 + DEDUP_WINDOW_SECONDS + 1,
        )
        is False
    )


def test_fingerprint_includes_revision():
    """Different revisions = distinct events; same revision
    deduped within window."""
    a = fingerprint_for(event=_resolved(rev="abc"))
    b = fingerprint_for(event=_resolved(rev="def"))
    assert a != b
