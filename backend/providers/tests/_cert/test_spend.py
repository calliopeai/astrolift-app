"""Tests for the campaign spend ledger (#1417).

The failure worth guarding here is the one that makes a spend report dangerous
rather than merely wrong: quoting a number that reads as a price. Every case
below is about the ledger refusing to invent, or about the report continuing to
agree with the code it is derived from.
"""

from __future__ import annotations

from _cert import billing
from _cert.collection import cells
from _cert.spend import (
    CAMPAIGN_SIZE,
    PASSES,
    TIME_METERED,
    Meter,
    Priceability,
    all_spend,
    bookings,
    cluster_demand,
    dominant_bookings,
    exposure,
    exposures,
    happy_path_spend,
    per_kind_spend,
    priceability_counts,
    provisioned_units,
    stranded_burn,
    window,
)
from _sdk.coverage import CLOUDS
from scripts.campaign_spend import OUTPUT, render

# ---- the ledger refuses to invent -------------------------------------------


def test_no_booking_is_ever_priced():
    """The whole point. A price would have to come from a live pricing API, and
    this module runs offline, so the absence is structural rather than a gap
    somebody forgot to fill."""
    assert all(not item.is_priced for item in bookings())


def test_an_unclassified_variant_gets_no_assumed_meter():
    """``billing.py`` refuses to sweep an unclassified variant into class A.
    This is the same refusal one layer up: the report must not sweep one into
    "cheap" by giving it a shape nobody reviewed."""
    unknown = [item for item in bookings() if item.meter is Meter.UNKNOWN]

    assert unknown, "the fixture for this test is the current state of billing.py"
    for item in unknown:
        assert "no billing class" in item.meter_reason or "no billing classification table" in item.meter_reason


def test_unknown_bookings_are_excluded_from_the_metered_total():
    """Counting them at any assumed shape produces a confident wrong number.
    They are reported as a separate ceiling instead."""
    win = window()
    total = exposures()[-1]

    assert total.hours(win) == total.time_metered * win.campaign_hours
    assert total.unknown_hours(win) == total.unknown * win.campaign_hours
    assert total.unknown > 0


def test_cluster_bookings_are_charged_to_the_stranded_case_not_to_the_run():
    """A tenant cluster bills for the whole campaign whether a cell runs or not,
    so a cell's pods are free at the margin. Stranded, those same pods are what
    stop the node scaling in."""
    assert Meter.CLUSTER not in TIME_METERED

    burn = stranded_burn()
    assert burn.cluster > 0
    assert burn.accruing == burn.hourly_floor + burn.fixed_floor + burn.cluster


def test_per_request_bookings_never_accrue_while_stranded():
    """The distinction the report is built on: a stranded queue costs nothing,
    a stranded database costs until somebody notices."""
    burn = stranded_burn()

    assert burn.per_request > 0
    assert burn.resource_hours_per_day == burn.accruing * 24
    assert burn.resource_hours_per_day < (burn.accruing + burn.per_request) * 24, (
        "per-request bookings must be reported and not summed into the daily burn"
    )


# ---- the window is read, not restated ----------------------------------------


def test_the_window_tracks_the_harness_timeouts(monkeypatch):
    """A retuned poll must move the report. A copy of the number here would keep
    quoting a window the runner no longer waits for."""
    from _cert.harness.runner import LifecycleRunner, Poll

    before = window()
    field = LifecycleRunner.__dataclass_fields__["teardown_poll"]
    monkeypatch.setattr(field, "default_factory", lambda: Poll(timeout_s=1, interval_s=1))

    after = window()

    assert after.teardown_s == 1
    assert after.cycle_s == before.cycle_s - before.teardown_s + 1


def test_the_window_counts_the_rollback_deploys():
    """UPDATE asserts "rolls cleanly (+ rollback works)", which is three deploys.
    Costing one would understate the largest term in the ceiling."""
    from _cert.harness.platform import CliPlatformClient

    deploy_s = CliPlatformClient.__dataclass_fields__["timeout_s"].default

    assert window().update_s == 3 * deploy_s


def test_both_passes_are_counted():
    """Spec 43 §0.1 step 7. A cell that ran once is not certified, so a total
    that counts one pass is not the campaign's total."""
    win = window()

    assert PASSES == 2
    assert win.campaign_hours == win.cycle_hours * PASSES


# ---- the ledger agrees with the collection and the drivers -------------------


def test_every_grid_cell_is_costed():
    """A cell that exists in the manifest collection but not here would run
    unbudgeted, which is the exact hole #1417 has been open on."""
    assert len(per_kind_spend()) == len(cells())
    assert {c.identifier for c in per_kind_spend()} == {f"per-kind/{c.kind}/{c.cloud}" for c in cells()}


def test_the_happy_path_books_four_services_on_every_cloud():
    """The certification target is web + database + cache + queue + object
    storage. Costing it as one booking would understate it fourfold."""
    happy = happy_path_spend()

    assert len(happy) == len(CLOUDS)
    assert all(len(cell.bookings) == 4 for cell in happy)


def test_provisioned_units_come_from_the_driver(monkeypatch):
    """The shopping list is the report's substance. Reading it off the driver is
    what keeps it true after somebody retunes a tier."""
    import aws.managed.postgres_rds as rds

    assert provisioned_units("postgres", "rds", "aws") == (
        ("_SIZE_TO_INSTANCE_CLASS", "db.t4g.small"),
        ("_SIZE_TO_STORAGE_GB", "20"),
    )

    monkeypatch.setitem(rds._SIZE_TO_INSTANCE_CLASS, CAMPAIGN_SIZE, "db.m5.24xlarge")
    assert ("_SIZE_TO_INSTANCE_CLASS", "db.m5.24xlarge") in provisioned_units("postgres", "rds", "aws")


def test_the_campaign_books_the_size_the_provision_activity_defaults_to():
    """No manifest declares a size, so this constant is the whole reason the
    shopping list says what it says."""
    assert CAMPAIGN_SIZE == "small"


def test_a_classified_variant_takes_its_meter_from_billing():
    """The meter is derived from the spend gate's own table, so the report and
    the gate can never disagree about what a variant is."""
    classified = [item for item in bookings() if item.meter is not Meter.UNKNOWN and item.plugin_id == item.cloud]

    assert classified
    for item in classified:
        expected = billing.classify(item.cloud, item.kind, item.variant)
        assert expected.billing_class.value in item.meter_reason


def test_dominant_bookings_are_exactly_the_hourly_floor_ones():
    """Ranking finer than this needs unit prices, and a finer ranking would
    imply prices nobody fetched."""
    dominant = dominant_bookings()

    assert dominant
    assert all(item.meter is Meter.HOURLY_FLOOR for item in dominant)
    assert len(dominant) == len({(b.cloud, b.kind, b.variant) for b in dominant})


def test_cluster_demand_is_read_from_the_committed_manifests():
    """The pods that will actually be applied, not an assumed footprint."""
    demand = {d.cloud: d for d in cluster_demand()}

    assert set(demand) == set(CLOUDS)
    for value in demand.values():
        assert value.replicas > 0
        assert value.vcpu > 0


# ---- priceability is a finding, not a footnote -------------------------------


def test_no_booking_claims_to_be_priceable_offline():
    """Every verdict ranks what a *live* run would return. None of them means
    this report produced a number."""
    counts = priceability_counts()

    assert sum(counts.values()) == sum(1 for _ in bookings())
    assert set(counts) == set(Priceability)


def test_azure_has_no_pricing_path_for_any_cell_it_books():
    """``azure/cost.py`` keys on variant names the availability matrix no longer
    carries. Recorded as a test because it is the kind of drift that otherwise
    only surfaces when somebody runs the estimator with credentials and gets
    "unsupported" for a whole cloud."""
    azure = [item for item in bookings() if item.plugin_id == "azure"]

    assert azure
    assert all(item.priceability is Priceability.NO_ESTIMATOR for item in azure)


def test_the_only_exact_estimates_are_the_ones_with_a_sku_plan():
    """``_sdk/cost_skus.py`` is what makes an estimate exact. Everything without
    a plan is summing SKUs the resource does not book."""
    exact = [item for item in bookings() if item.priceability is Priceability.EXACT]

    assert exact
    assert {item.plugin_id for item in exact} == {"gcp"}
    assert all("cloudsql" in item.variant for item in exact)


# ---- the report ---------------------------------------------------------------


def test_generated_report_is_current():
    """The committed report is a generated artifact. Regenerate it with
    `make campaign-spend` rather than hand-editing."""
    assert OUTPUT.read_text(encoding="utf-8") == render(), "docs/campaign_spend.md is stale; run `make campaign-spend`"


def test_the_report_leads_with_its_own_coverage():
    """An estimate that hides how much of the grid it could price is worse than
    no estimate. The unpriced count has to be above the fold."""
    text = render()
    head = text[: text.index("## What a real number requires")]
    total = sum(1 for _ in bookings())

    assert f"{total} of {total} bookings are unpriced" in head
    assert f"{exposures()[-1].unknown} of {total} bookings have no billing class" in head


def test_the_report_names_every_booking():
    text = render()

    for item in bookings():
        assert f"`{item.variant}`" in text


def test_the_report_lists_every_unclassified_variant():
    """The blocking list has to be complete: anything missing from it reads as
    ready to run when the gate will refuse it."""
    text = render()
    section = text[text.index("## Bookings with no billing class") : text.index("## Every booking")]

    for item in bookings():
        if item.meter is Meter.UNKNOWN:
            assert f"| {item.cloud} | `{item.kind}` | `{item.variant}` |" in section


def test_exposure_sums_bookings_not_cells():
    """A happy-path cell books four services. Counting cells would understate
    the certification target, which is the one part of the grid that is not
    optional."""
    happy = exposure("happy", happy_path_spend())

    assert happy.cells == len(CLOUDS)
    assert happy.bookings == len(CLOUDS) * 4


def test_all_spend_covers_the_happy_path_and_the_grid():
    assert len(all_spend()) == len(happy_path_spend()) + len(per_kind_spend())
