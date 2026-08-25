"""The sweep that makes the orphan module live (#1602 step 5).

`observability_retention.py` has been written, tested and green since it
landed, with no production caller: five public functions and four
`Organization` columns an operator could set while the data lived forever.
This is the chain that connects them.

The tests worth reading are the accounting ones. A retention sweep's whole
value is that an operator can believe its numbers before switching it on --
so "unsupported" must not read as "evicted nothing", and "held" must not
read as "nothing to do".
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from astrolift_operations.models import ObservabilityRetentionHold
from astrolift_operations.observability_retention_sweep import (
    OLDEST_HORIZON,
    STREAM_COLUMNS,
    _delete_windows,
    run_observability_retention_sweep,
)
from core.cluster_observability_eviction import (
    reset_eviction_driver_for_tests,
    set_eviction_driver_for_tests,
)

pytestmark = pytest.mark.django_db

NOW = timezone.now()


class RecordingDriver:
    """Records what it was asked to evict."""

    def __init__(self, *, supported=True, requested=1):
        self._supported = supported
        self._requested = requested
        self.calls: list[tuple[str, dt.datetime, dt.datetime, bool]] = []

    def evict_before(self, request):
        from _sdk.observability.eviction import EvictionOutcome

        self.calls.append((request.stream, request.start, request.end, request.dry_run))
        return EvictionOutcome(
            supported=self._supported,
            requested=self._requested if self._supported else 0,
            detail="recorded" if self._supported else "backend has no delete API",
        )


@pytest.fixture(autouse=True)
def _clean_override():
    reset_eviction_driver_for_tests()
    yield
    reset_eviction_driver_for_tests()


@pytest.fixture
def org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Sweep", slug="sweep-org")


@pytest.fixture
def cluster(org):
    from astrolift_clusters.models import ProviderPlugin, TenantCluster

    # bulk_create, matching the lifecycle conftest: this model's save()
    # path expects fields a bare create() here does not supply.
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Test Provider",
                slug="sweep-provider",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    plugin = ProviderPlugin.objects.get(slug="sweep-provider")
    return TenantCluster.objects.create(
        organization=org,
        name="sweep-cluster",
        slug="sweep-cluster",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://sweep.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
    )


def _hold(org, **kw):
    defaults = {
        "organization": org,
        "stream": "*",
        "starts_at": NOW - OLDEST_HORIZON,
        "ends_at": NOW,
        "reason": "incident",
    }
    return ObservabilityRetentionHold.objects.create(**{**defaults, **kw})


# ---------------------------------------------------------------------------
# The column -> stream join that did not exist
# ---------------------------------------------------------------------------


def test_every_stream_maps_to_a_real_organization_column():
    """The join this module exists to make. A stream mapped to a column
    that does not exist would silently fall back to the platform default,
    so the org override would be settable and ignored -- which is the state
    this issue is about."""
    from astrolift_identity.models import Organization
    from astrolift_operations.observability_retention import ALL_STREAMS

    fields = {f.name for f in Organization._meta.get_fields() if getattr(f, "attname", None)}

    assert set(STREAM_COLUMNS) == set(ALL_STREAMS)
    for stream, column in STREAM_COLUMNS.items():
        assert column in fields, f"{stream} maps to a nonexistent column {column!r}"


def test_the_org_override_reaches_the_cutoff(org, cluster):
    """A short override must produce a later cutoff than a long one, or the
    column is decorative."""
    driver = RecordingDriver()
    set_eviction_driver_for_tests(driver)
    org.log_retention_days_default = 7
    org.save()

    run_observability_retention_sweep(dry_run=True)

    log_calls = [c for c in driver.calls if c[0] == "log"]
    assert log_calls
    # cutoff = now - 7 days, so the window's end sits about a week back.
    end = log_calls[0][2]
    assert dt.timedelta(days=6) < (NOW - end) < dt.timedelta(days=8)


# ---------------------------------------------------------------------------
# Accounting: the numbers an operator has to be able to believe
# ---------------------------------------------------------------------------


def test_dry_run_is_the_default(org, cluster):
    """A sweep that deletes tenant data by default is one accidental call
    away from an unrecoverable mistake."""
    driver = RecordingDriver()
    set_eviction_driver_for_tests(driver)

    run_observability_retention_sweep()

    assert driver.calls
    assert all(is_dry for (_, _, _, is_dry) in driver.calls)


def test_a_real_run_passes_dry_run_false(org, cluster):
    driver = RecordingDriver()
    set_eviction_driver_for_tests(driver)

    run_observability_retention_sweep(dry_run=False)

    assert all(is_dry is False for (_, _, _, is_dry) in driver.calls)


def test_unsupported_is_counted_apart_from_evicted(org, cluster):
    """The #1594 failure mode. "0 evicted" for a backend that cannot delete
    is indistinguishable from "nothing to delete", so an operator reads an
    unenforced policy as enforced."""
    set_eviction_driver_for_tests(RecordingDriver(supported=False))

    counts = run_observability_retention_sweep()

    assert counts["skipped_unsupported"] > 0
    assert counts["evicted"] == 0
    assert counts["errors"] == 0, "an unsupported backend is not a failure"


def test_a_fully_held_stream_is_counted_as_held_not_skipped(org, cluster):
    """ "Nothing to do" and "an operator is protecting this" are different
    states, and the operator needs to see the second."""
    driver = RecordingDriver()
    set_eviction_driver_for_tests(driver)
    _hold(org, stream="*")

    counts = run_observability_retention_sweep()

    assert counts["held"] == 4, "all four streams held"
    assert counts["windows"] == 0
    assert driver.calls == [], "nothing may be sent to the backend"


def test_a_hold_carves_the_window_rather_than_cancelling_it(org, cluster):
    """A hold over part of the evictable range must leave the rest
    evictable, or one incident suspends retention indefinitely."""
    driver = RecordingDriver()
    set_eviction_driver_for_tests(driver)
    _hold(
        org,
        stream="log",
        starts_at=NOW - dt.timedelta(days=200),
        ends_at=NOW - dt.timedelta(days=100),
    )

    run_observability_retention_sweep()

    log_calls = [c for c in driver.calls if c[0] == "log"]
    assert len(log_calls) == 2, "the window either side of the hold"


def test_no_cluster_means_nothing_is_attempted(org):
    """An org with no cluster has no backend to evict from. Counted as an
    org so the operator can see it was considered, but no windows."""
    driver = RecordingDriver()
    set_eviction_driver_for_tests(driver)

    counts = run_observability_retention_sweep()

    assert counts["orgs"] == 1
    assert counts["windows"] == 0
    assert driver.calls == []


def test_one_bad_org_does_not_abort_the_sweep(org, cluster, monkeypatch):
    """This sweep is the only thing enforcing retention for every other
    org, so one misconfigured org must not stop it."""
    from astrolift_identity.models import Organization

    other = Organization.objects.create(name="Other", slug="other-sweep-org")
    set_eviction_driver_for_tests(RecordingDriver())

    import astrolift_operations.observability_retention_sweep as mod

    real = mod.active_holds_for

    def explode(organization):
        if organization.pk == org.pk:
            raise RuntimeError("boom")
        return real(organization)

    monkeypatch.setattr(mod, "active_holds_for", explode)

    counts = run_observability_retention_sweep()

    assert counts["errors"] == 1
    assert counts["orgs"] == 1, f"the other org ({other.slug}) still swept"


def test_an_event_is_emitted_only_on_a_real_eviction(org, cluster):
    """Emitting on a dry run would put "we deleted your data" in an org's
    event feed on a run that deleted nothing."""
    from astrolift_operations.models import Event

    set_eviction_driver_for_tests(RecordingDriver())

    run_observability_retention_sweep(dry_run=True)
    assert not Event.objects.filter(event_type="observability.retention_evicted").exists()

    run_observability_retention_sweep(dry_run=False)
    assert Event.objects.filter(event_type="observability.retention_evicted").exists()


# ---------------------------------------------------------------------------
# The window arithmetic
# ---------------------------------------------------------------------------


def _win(**kw):
    defaults = {
        "stream": "log",
        "oldest": NOW - dt.timedelta(days=365),
        "cutoff": NOW - dt.timedelta(days=30),
        "holds": [],
    }
    return _delete_windows(**{**defaults, **kw})


def test_with_no_holds_the_whole_range_is_one_window():
    assert len(_win()) == 1


def test_a_cutoff_at_or_before_the_horizon_yields_nothing():
    """Retention longer than the horizon means nothing is old enough."""
    assert _win(cutoff=NOW - dt.timedelta(days=400)) == []


def test_a_hold_for_another_stream_is_ignored():
    hold = _policy_hold(stream="trace")

    assert len(_win(holds=[hold])) == 1


def test_a_wildcard_hold_applies():
    hold = _policy_hold(stream="*")

    assert _win(holds=[hold]) == []


def test_a_resource_scoped_hold_leaves_the_whole_stream_window_intact():
    """Recording what the code actually does, having gone looking for the
    opposite.

    I expected the window to be refused, on the reasoning that a hold over
    one App inside it cannot be expressed as time arithmetic. It is not:
    the subtraction skips resource-scoped holds, and `is_held` does not
    match them either, because the window's edges carry no resource. So the
    whole stream window survives and the held App's rows go with it.

    That is a real gap and it belongs to the driver layer rather than here
    -- the selector passed to `evict_before` is what could exclude one
    App's rows, and building a selector is backend-specific. Pinned as it
    stands so the behaviour is documented rather than assumed, and noted on
    the issue.
    """
    hold = _policy_hold(stream="log", resource_kind="App", resource_id="42")

    assert len(_win(holds=[hold])) == 1


def _policy_hold(**kw):
    from astrolift_operations.observability_retention import RetentionHold

    defaults = {
        "stream": "log",
        "starts_at": NOW - dt.timedelta(days=400),
        "ends_at": NOW,
    }
    return RetentionHold(**{**defaults, **kw})
