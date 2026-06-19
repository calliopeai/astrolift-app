"""
Tests for the keep-alive agent reconcile tick (#808).

The reconcile tick re-applies the keep-alive agent manifests (Namespace +
Deployment) to every eligible managed cluster through the idempotent
server-side-apply path the ``deployClusterAgent`` mutation uses
(``core.cluster_management.deploy_agent_dispatch``), so a stale/failed agent
image (e.g. after ``AGENT_IMAGE`` changes) or any agent-manifest drift
self-heals without a manual re-apply.

These exercise the DB-backed ``_reconcile_agent_deployments_sync`` activity
with ``deploy_agent_dispatch`` SPIED (the canonical
``core.cluster_management`` monkeypatch, same seam ``test_scale_tick`` uses for
``_driver_for_cluster``) so nothing touches a real cluster. The acceptance
cases:

  1. re-applies to an ELIGIBLE cluster — ``deploy_agent_dispatch`` is invoked
     for it and the summary counts it reconciled;
  2. SKIPS ineligible clusters — inactive, soft-deleted, and empty
     ``agent_key_hash`` are each never dispatched;
  3. per-cluster ERROR ISOLATION — one cluster raising
     ``ClusterManagementError`` (and one returning a non-ok ``ApplyResult``)
     does not stop the others, and each failure is recorded to
     ``last_management_error``;
  4. the SUMMARY counts (reconciled / skipped / failed) are accurate.
"""

from __future__ import annotations

import dataclasses

import pytest

# ---- spy plumbing ---------------------------------------------------------


@dataclasses.dataclass
class _FakeApplyResult:
    """Minimal stand-in for the driver ``ApplyResult`` — the reconcile
    activity reads only ``ok`` and ``errors`` (mirrors ``test_scale_tick``'s
    minimal ``_RecordingDriver`` return)."""

    errors: list = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


class _DispatchSpy:
    """Records each ``deploy_agent_dispatch(cluster=...)`` call by cluster slug
    and returns a per-slug scripted outcome:

      * default — an ok ApplyResult (apply succeeded);
      * a slug mapped to an Exception instance — raised (e.g.
        ClusterManagementError, simulating an unbuildable driver);
      * a slug mapped to a non-ok _FakeApplyResult — returned (apply reported
        per-manifest errors).
    """

    def __init__(self, *, raises: dict | None = None, not_ok: set | None = None):
        self.calls: list[str] = []
        self._raises = raises or {}
        self._not_ok = not_ok or set()

    def __call__(self, *, cluster):
        self.calls.append(cluster.slug)
        if cluster.slug in self._raises:
            raise self._raises[cluster.slug]
        if cluster.slug in self._not_ok:
            return _FakeApplyResult(errors=["Deployment/astrolift-agent: boom"])
        return _FakeApplyResult(errors=[])


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


def _install_dispatch_spy(monkeypatch, **kw) -> _DispatchSpy:
    """Patch ``core.cluster_management.deploy_agent_dispatch`` (the source the
    activity imports inside its ``_sync`` helper) with a recording spy."""
    spy = _DispatchSpy(**kw)
    monkeypatch.setattr("core.cluster_management.deploy_agent_dispatch", spy)
    return spy


# ---- cluster fixtures -----------------------------------------------------


@pytest.fixture
def org(db):
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Reconcile Co", slug="reconcile-co")


def _make_cluster(org, *, slug, is_active=True, agent_key_hash="abc123"):
    """An eligible-by-default TenantCluster. Each gets a distinct name + slug
    so the NamedBaseCoreModel slug derivation never collides on the unique
    active-slug constraint."""
    from astrolift_clusters.models import ProviderPlugin, TenantCluster

    # ``ProviderPlugin`` shadows BaseCoreModel's integer ``version`` column with
    # its own CharField, so ``.objects.create()`` / ``.save()`` trips the
    # version-increment in the base save(). Construct + ``bulk_create`` (which
    # bypasses save()) then re-fetch — the canonical pattern from
    # ``test_scale_tick``'s fixture.
    plugin = ProviderPlugin.objects.filter(slug="k8s-reconcile").first()
    if plugin is None:
        ProviderPlugin.objects.bulk_create(
            [
                ProviderPlugin(
                    name="K8s",
                    slug="k8s-reconcile",
                    version="0.0.1",
                    capabilities_manifest={},
                    config_schema={},
                )
            ]
        )
        plugin = ProviderPlugin.objects.get(slug="k8s-reconcile")
    return TenantCluster.objects.create(
        organization=org,
        name=f"cluster {slug}",
        slug=slug,
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        is_active=is_active,
        agent_key_hash=agent_key_hash,
    )


# ---- acceptance (1): re-applies to an eligible cluster --------------------


@pytest.mark.django_db
def test_reconcile_applies_to_eligible_cluster(org, monkeypatch):
    """Acceptance (1): an active, non-deleted cluster with an issued agent key
    is re-applied — ``deploy_agent_dispatch`` is invoked for it and the
    summary counts it reconciled."""
    from astrolift_workflows.activities.cron_deploy import (
        _reconcile_agent_deployments_sync,
    )

    cluster = _make_cluster(org, slug="eligible-1")
    spy = _install_dispatch_spy(monkeypatch)

    summary = _reconcile_agent_deployments_sync()

    assert spy.calls == ["eligible-1"]
    assert summary.reconciled_count == 1
    assert summary.reconciled_cluster_slugs == ("eligible-1",)
    assert summary.failed_count == 0
    assert summary.skipped_count == 0

    # A successful apply clears any prior error (none here, stays empty).
    cluster.refresh_from_db()
    assert cluster.last_management_error == ""


@pytest.mark.django_db
def test_reconcile_clears_stale_error_on_success(org, monkeypatch):
    """A cluster that previously failed (last_management_error set) has it
    cleared on a successful re-apply — mirrors the mutation's success path so a
    recovered cluster stops surfacing the old failure."""
    from astrolift_workflows.activities.cron_deploy import (
        _reconcile_agent_deployments_sync,
    )

    cluster = _make_cluster(org, slug="recovered-1")
    cluster.last_management_error = "ImagePullBackOff last time"
    cluster.save(update_fields=["last_management_error", "updated_at", "version"])
    _install_dispatch_spy(monkeypatch)

    summary = _reconcile_agent_deployments_sync()

    assert summary.reconciled_count == 1
    cluster.refresh_from_db()
    assert cluster.last_management_error == ""


# ---- acceptance (2): skips ineligible clusters ----------------------------


@pytest.mark.django_db
def test_reconcile_skips_inactive_cluster(org, monkeypatch):
    """Acceptance (2): an ``is_active=False`` cluster is never dispatched (the
    mutation refuses an inactive cluster; the tick excludes it)."""
    from astrolift_workflows.activities.cron_deploy import (
        _reconcile_agent_deployments_sync,
    )

    _make_cluster(org, slug="inactive-1", is_active=False)
    spy = _install_dispatch_spy(monkeypatch)

    summary = _reconcile_agent_deployments_sync()

    assert spy.calls == []
    assert summary.reconciled_count == 0
    # An inactive cluster is filtered before the agent_key_hash split, so it
    # is neither reconciled nor counted as a skipped (no-key) candidate.
    assert summary.skipped_count == 0


@pytest.mark.django_db
def test_reconcile_skips_soft_deleted_cluster(org, monkeypatch):
    """Acceptance (2): a soft-deleted cluster is never dispatched (it is not a
    deploy target)."""
    from astrolift_workflows.activities.cron_deploy import (
        _reconcile_agent_deployments_sync,
    )

    cluster = _make_cluster(org, slug="deleted-1")
    cluster.soft_delete()
    spy = _install_dispatch_spy(monkeypatch)

    summary = _reconcile_agent_deployments_sync()

    assert spy.calls == []
    assert summary.reconciled_count == 0
    assert summary.skipped_count == 0


@pytest.mark.django_db
def test_reconcile_skips_cluster_without_agent_key(org, monkeypatch):
    """Acceptance (2): an active cluster whose ``agent_key_hash`` is empty is
    skipped — no key means the agent Secret was never provisioned, so the
    Deployment's secretKeyRefs would fail. It IS counted as skipped."""
    from astrolift_workflows.activities.cron_deploy import (
        _reconcile_agent_deployments_sync,
    )

    _make_cluster(org, slug="no-key-1", agent_key_hash="")
    spy = _install_dispatch_spy(monkeypatch)

    summary = _reconcile_agent_deployments_sync()

    assert spy.calls == []
    assert summary.reconciled_count == 0
    assert summary.skipped_count == 1


@pytest.mark.django_db
def test_reconcile_dispatches_only_eligible_among_mixed(org, monkeypatch):
    """Acceptance (1+2) together: with one eligible cluster and three
    ineligible (inactive / soft-deleted / no-key), only the eligible one is
    dispatched."""
    from astrolift_workflows.activities.cron_deploy import (
        _reconcile_agent_deployments_sync,
    )

    _make_cluster(org, slug="ok-1")
    _make_cluster(org, slug="inactive-2", is_active=False)
    deleted = _make_cluster(org, slug="deleted-2")
    deleted.soft_delete()
    _make_cluster(org, slug="nokey-2", agent_key_hash="")
    spy = _install_dispatch_spy(monkeypatch)

    summary = _reconcile_agent_deployments_sync()

    assert spy.calls == ["ok-1"]
    assert summary.reconciled_count == 1
    assert summary.reconciled_cluster_slugs == ("ok-1",)
    assert summary.skipped_count == 1  # only the no-key (active, undeleted) one
    assert summary.failed_count == 0


# ---- acceptance (3): per-cluster error isolation --------------------------


@pytest.mark.django_db
def test_reconcile_isolates_cluster_management_error(org, monkeypatch):
    """Acceptance (3): a cluster whose dispatch raises ``ClusterManagementError``
    (unbuildable driver / no apply_manifests) does NOT abort the tick — the
    other eligible clusters still reconcile, and the failure is recorded to
    ``last_management_error``."""
    from astrolift_workflows.activities.cron_deploy import (
        _reconcile_agent_deployments_sync,
    )
    from core.cluster_management import ClusterManagementError

    good_a = _make_cluster(org, slug="good-a")
    bad = _make_cluster(org, slug="bad-driver")
    good_b = _make_cluster(org, slug="good-b")
    spy = _install_dispatch_spy(
        monkeypatch,
        raises={"bad-driver": ClusterManagementError("driver does not implement apply_manifests")},
    )

    summary = _reconcile_agent_deployments_sync()

    # All three were attempted (the raise did not short-circuit the loop).
    assert set(spy.calls) == {"good-a", "bad-driver", "good-b"}
    assert summary.reconciled_count == 2
    assert set(summary.reconciled_cluster_slugs) == {"good-a", "good-b"}
    assert summary.failed_count == 1
    assert summary.failed_cluster_slugs == ("bad-driver",)

    # The failure was recorded; the good clusters carry no error.
    bad.refresh_from_db()
    assert "apply_manifests" in bad.last_management_error
    good_a.refresh_from_db()
    good_b.refresh_from_db()
    assert good_a.last_management_error == ""
    assert good_b.last_management_error == ""


@pytest.mark.django_db
def test_reconcile_isolates_non_ok_apply_result(org, monkeypatch):
    """Acceptance (3): a cluster whose apply returns a non-ok ``ApplyResult``
    (per-manifest errors) is recorded as failed and does not stop the others —
    mirrors the mutation's ``not result.ok`` branch + message shape."""
    from astrolift_workflows.activities.cron_deploy import (
        _reconcile_agent_deployments_sync,
    )

    good = _make_cluster(org, slug="apply-good")
    failing = _make_cluster(org, slug="apply-failed")
    spy = _install_dispatch_spy(monkeypatch, not_ok={"apply-failed"})

    summary = _reconcile_agent_deployments_sync()

    assert set(spy.calls) == {"apply-good", "apply-failed"}
    assert summary.reconciled_count == 1
    assert summary.reconciled_cluster_slugs == ("apply-good",)
    assert summary.failed_count == 1
    assert summary.failed_cluster_slugs == ("apply-failed",)

    failing.refresh_from_db()
    assert failing.last_management_error.startswith("agent deploy failed:")
    assert "Deployment/astrolift-agent" in failing.last_management_error
    good.refresh_from_db()
    assert good.last_management_error == ""


@pytest.mark.django_db
def test_reconcile_isolates_unexpected_exception(org, monkeypatch):
    """Acceptance (3), defensive: an unexpected (non-ClusterManagementError)
    exception — e.g. a cluster with no resolvable managed runtime — is isolated
    per cluster, recorded, and never aborts the tick."""
    from astrolift_workflows.activities.cron_deploy import (
        _reconcile_agent_deployments_sync,
    )

    good = _make_cluster(org, slug="surv-good")
    boom = _make_cluster(org, slug="surv-boom")
    spy = _install_dispatch_spy(
        monkeypatch,
        raises={"surv-boom": RuntimeError("no managed runtime")},
    )

    summary = _reconcile_agent_deployments_sync()

    assert set(spy.calls) == {"surv-good", "surv-boom"}
    assert summary.reconciled_count == 1
    assert summary.failed_count == 1
    assert summary.failed_cluster_slugs == ("surv-boom",)

    boom.refresh_from_db()
    assert "no managed runtime" in boom.last_management_error
    good.refresh_from_db()
    assert good.last_management_error == ""


# ---- acceptance (4): summary counts ---------------------------------------


@pytest.mark.django_db
def test_reconcile_summary_counts_full_shape(org, monkeypatch):
    """Acceptance (4): with reconciled + skipped + failed clusters all present,
    the summary's three counts and two slug tuples are exact."""
    from astrolift_workflows.activities.cron_deploy import (
        _reconcile_agent_deployments_sync,
    )
    from core.cluster_management import ClusterManagementError

    # 2 eligible-and-ok, 1 no-key (skipped), 1 raising (failed), 1 non-ok (failed).
    _make_cluster(org, slug="sum-ok-1")
    _make_cluster(org, slug="sum-ok-2")
    _make_cluster(org, slug="sum-nokey", agent_key_hash="")
    _make_cluster(org, slug="sum-raise")
    _make_cluster(org, slug="sum-notok")
    spy = _install_dispatch_spy(
        monkeypatch,
        raises={"sum-raise": ClusterManagementError("boom")},
        not_ok={"sum-notok"},
    )

    summary = _reconcile_agent_deployments_sync()

    # Only the 4 eligible (non-skipped) clusters were dispatched.
    assert set(spy.calls) == {"sum-ok-1", "sum-ok-2", "sum-raise", "sum-notok"}
    assert summary.reconciled_count == 2
    assert set(summary.reconciled_cluster_slugs) == {"sum-ok-1", "sum-ok-2"}
    assert summary.skipped_count == 1
    assert summary.failed_count == 2
    assert set(summary.failed_cluster_slugs) == {"sum-raise", "sum-notok"}


@pytest.mark.django_db
def test_reconcile_empty_fleet_is_noop(org, monkeypatch):
    """No clusters at all → a clean zero-count summary, no dispatch."""
    from astrolift_workflows.activities.cron_deploy import (
        _reconcile_agent_deployments_sync,
    )

    spy = _install_dispatch_spy(monkeypatch)

    summary = _reconcile_agent_deployments_sync()

    assert spy.calls == []
    assert summary.reconciled_count == 0
    assert summary.skipped_count == 0
    assert summary.failed_count == 0
    assert summary.reconciled_cluster_slugs == ()
    assert summary.failed_cluster_slugs == ()


# ---- schedule_registry catalog entry --------------------------------------


def test_reconcile_tick_is_registered_at_300_seconds():
    from astrolift_workflows.schedule_registry import (
        DEFAULT_SCHEDULES,
        ScheduleKind,
        get_schedule,
    )

    sched = get_schedule(kind=ScheduleKind.AGENT_RECONCILE_TICK)
    assert sched.interval_seconds == 300
    assert sched.workflow_name == "AgentReconcileTickWorkflow"
    assert any(s.kind == ScheduleKind.AGENT_RECONCILE_TICK for s in DEFAULT_SCHEDULES)
