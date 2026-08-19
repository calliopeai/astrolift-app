"""What the certification campaign costs, and what it refuses to guess (#1417).

#1417 asked for a spend decision and stalled because nobody produced a number.
This module produces what can honestly be produced offline, and is explicit
about the rest -- because the alternative that kept happening is "it depends",
and a decision-maker cannot approve that.

Three things are computable here without touching a cloud, and all three are
read out of the tree rather than typed in:

*The metered window.* How long a cell holds a resource is bounded by the
harness's own declared timeouts (:mod:`_cert.harness.runner`,
:mod:`_cert.harness.platform`). Those are ceilings, not expectations, and the
report says so every time it prints one. A ceiling is still the right shape for
an approval: "up to N resource-hours" is a thing a human can sign.

*The billing shape.* :mod:`_cert.billing` already classifies a variant by how it
meters -- per request, small fixed floor, hourly capacity floor. That is the
half of the decision that actually matters, because an hourly floor is the only
shape whose cost scales with how long the campaign takes.

*What each cell books.* Every driver resolves ``ProvisionSpec.size`` through a
module-level table, and the campaign's manifests declare no size, so every cell
books ``small`` (``managed_service_lifecycle.py`` defaults it). Reading those
tables gives the shopping list -- ``db.t4g.small``, 20 GB, one node -- which is
the estimate's whole quantity side. Only the unit price is missing.

What is deliberately NOT computable here is the price. Estimates come from the
cloud's live pricing API and never from a table in this repository
(``_sdk/cost.py``), nothing is vendored or cached in the tree, and this module
runs offline. So **no cell is priced in dollars**, every cell carries the reason
it is not, and the report leads with that count rather than burying it. An
estimate that hides its own coverage is worse than no estimate.

The second thing the report carries is the priceability verdict: whether the
existing machinery *could* price the cell once credentials exist. That is a
finding in its own right -- spec 43 §4.3 says a cell the preview cannot price is
a finding, not a rounding error -- and it is the difference between "we have not
run the estimator yet" and "the estimator has no path for this cell at all".
"""

from __future__ import annotations

import importlib
import tomllib
from dataclasses import MISSING, dataclass, fields
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from _cert import billing
from _cert.collection import (
    CAMPAIGN,
    HAPPY_PATH_SERVICES,
    VARIANTS,
    Cell,
    cells,
    rendered,
)
from _cert.harness.platform import CliPlatformClient
from _cert.harness.runner import LifecycleRunner
from _sdk.coverage import CLOUDS
from aws.cost import SERVICE_CODE_BY_VARIANT
from azure.cost import SERVICE_NAME_BY_VARIANT
from gcp.cost import (
    LEGACY_SUM_VARIANTS,
    SERVICE_DISPLAY_NAME_BY_VARIANT,
    SERVICE_ID_BY_VARIANT,
)
from gcp.cost_plans import has_plan

if TYPE_CHECKING:
    from collections.abc import Iterator

CAMPAIGN_SIZE = "small"
"""Every campaign manifest omits ``size``, and
``astrolift_workflows/activities/managed_service_lifecycle.py`` defaults an
absent size to ``small``. So this is what the grid books, not a preference."""

PASSES = 2
"""Spec 43 §0.1 step 7: a cell is GREEN only when an unattended second run
reproduces it. Everything metered is metered twice, which is why the report
never quotes a single-pass total on its own."""

SIZE_KEYS = frozenset({"small", "medium", "large", "xlarge"})
"""``ProvisionSpec.size``'s vocabulary. A driver's size table is recognised by
having exactly these keys, which is how the shopping list is read out of the
drivers instead of transcribed into a table here that would rot the first time
somebody retuned a tier."""


class Meter(StrEnum):
    """How a booking accrues cost over time.

    This is the axis the decision turns on. A queue billed per message costs the
    same whether the campaign takes an hour or a week; a Kafka cluster billed by
    the hour does not.
    """

    PER_REQUEST = "per-request"
    FIXED_FLOOR = "fixed-floor"
    HOURLY_FLOOR = "hourly-floor"
    CLUSTER = "cluster-capacity"
    UNKNOWN = "unknown"


#: How a billing class meters. Class D shares class C's shape -- it is separated
#: in ``billing.py`` by lifecycle latency, which is a certifiability decision
#: rather than a different meter.
_METER_BY_CLASS: dict[billing.BillingClass, Meter] = {
    billing.BillingClass.NO_IDLE_COST: Meter.PER_REQUEST,
    billing.BillingClass.SMALL_FIXED_FLOOR: Meter.FIXED_FLOOR,
    billing.BillingClass.HOURLY_CAPACITY_FLOOR: Meter.HOURLY_FLOOR,
    billing.BillingClass.HOURLY_FLOOR_SLOW_LIFECYCLE: Meter.HOURLY_FLOOR,
}

#: Meters whose cost is a function of how long the *cell* runs. The campaign's
#: quantifiable exposure is these, times the window, times two passes.
#:
#: :attr:`Meter.CLUSTER` is excluded. A tenant cluster bills for the campaign's
#: whole wall clock whether a given cell runs or not, so charging its pod-hours
#: to the cell would double-count against a floor that is already paid. It comes
#: back in the stranded case, where a pod that is never removed is exactly what
#: stops a node from scaling in.
TIME_METERED = frozenset({Meter.HOURLY_FLOOR, Meter.FIXED_FLOOR})


class Priceability(StrEnum):
    """Whether the existing cost machinery can price this booking, and how well.

    None of these means "priced". Every one of them means "not priced here",
    because pricing needs the live API. They rank what a run *with* credentials
    would get back.
    """

    EXACT = "exact"
    """A SKU plan resolves one catalog row per billable dimension
    (``_sdk/cost_skus.py``). This is the only verdict that yields a number an
    operator should act on."""

    APPROXIMATE = "approximate"
    """The estimator sums whatever the service's catalog returns. That is the
    #1318 over-counting bug: a service's SKU list spans engines, editions and
    storage classes the resource never books, so the number is confident and
    materially wrong."""

    NO_ESTIMATOR = "no-estimator"
    """The cloud's estimator has no entry for this (kind, variant). Credentials
    would not help; the mapping does not exist."""

    NOT_A_CLOUD_SKU = "not-a-cloud-sku"
    """An in-cluster variant. There is no managed-service SKU to look up: it
    consumes the tenant cluster's capacity, which the campaign is already
    paying for by the hour whether the cell runs or not."""


@dataclass(frozen=True)
class Booking:
    """One managed service a cell provisions.

    A per-kind cell books one; a happy-path cell books four. Cost accrues per
    booking, so this rather than the cell is the unit the totals sum over.
    """

    kind: str
    cloud: str
    variant: str
    plugin_id: str
    meter: Meter
    meter_reason: str
    priceability: Priceability
    priceability_reason: str
    units: tuple[tuple[str, str], ...] = ()
    """``(constant, value)`` pairs read from the driver's own size table at
    ``small``. Empty when the driver declares no size table, which usually means
    the resource has no provisioned capacity to declare."""

    @property
    def is_time_metered(self) -> bool:
        return self.meter in TIME_METERED

    @property
    def is_priced(self) -> bool:
        """Always false, and named so the report cannot quietly imply otherwise.

        Kept as a property rather than a constant because the day a price does
        become available offline, every caller that asks this question is
        already asking it in the right place.
        """
        return False


@dataclass(frozen=True)
class CellSpend:
    """One matrix cell: what it books and for how long."""

    identifier: str
    cloud: str
    bookings: tuple[Booking, ...]

    @property
    def hourly_floor_bookings(self) -> tuple[Booking, ...]:
        return tuple(b for b in self.bookings if b.meter is Meter.HOURLY_FLOOR)

    @property
    def unknown_bookings(self) -> tuple[Booking, ...]:
        return tuple(b for b in self.bookings if b.meter is Meter.UNKNOWN)


# ---- the metered window ------------------------------------------------------


@dataclass(frozen=True)
class Window:
    """The ceiling on how long one pass of the cycle holds a resource.

    Every term is a timeout the harness already declares, so this cannot drift
    away from what the runner will actually wait for. It is emphatically an
    upper bound: a cell that provisions in four minutes bills for four minutes.
    The bound is what a spend approval needs, because the approval has to cover
    the bad day.
    """

    buildout_s: float
    update_s: float
    teardown_s: float

    @property
    def cycle_s(self) -> float:
        return self.buildout_s + self.update_s + self.teardown_s

    @property
    def cycle_hours(self) -> float:
        return self.cycle_s / 3600.0

    @property
    def campaign_hours(self) -> float:
        """Both passes. The definition of GREEN requires the second one."""
        return self.cycle_hours * PASSES


def _default(cls: type, name: str) -> Any:
    """A dataclass field's default, factory or not.

    Read rather than repeated: these are the numbers the runner will really use,
    and a copy of them here would be wrong the first time one is tuned.
    """
    field = next(f for f in fields(cls) if f.name == name)
    if field.default_factory is not MISSING:
        return field.default_factory()
    return field.default


def window() -> Window:
    """The declared ceiling for one pass, composed from the harness's timeouts.

    BUILDOUT waits out two separate polls -- the app reaching a terminal
    provisioning status, then every managed service reaching one -- and then
    runs a deploy bounded by the platform client's own timeout. UPDATE runs
    three deploys when rollback verification is on (forward, back, forward).
    TEARDOWN is one poll, whose floor is the deregister workflow's grace window.

    The five HTTP probes in VERIFY-UP and VERIFY-UPD are bounded at 60s each and
    are left out: 300s against a five-hour ceiling is below the resolution of
    anything this report is used to decide.
    """
    provision_s = _default(LifecycleRunner, "provision_poll").timeout_s
    teardown_s = _default(LifecycleRunner, "teardown_poll").timeout_s
    deploy_s = _default(CliPlatformClient, "timeout_s")
    deploys_in_update = 3 if _default(LifecycleRunner, "include_rollback") else 1
    return Window(
        buildout_s=2 * provision_s + deploy_s,
        update_s=deploys_in_update * deploy_s,
        teardown_s=teardown_s,
    )


# ---- what a booking books ----------------------------------------------------


def _driver_module(kind: str, variant: str, plugin_id: str) -> Any:
    plugin = importlib.import_module(f"{plugin_id}.plugin").PLUGIN
    drivers = plugin.managed_service_drivers
    driver = drivers.get((kind, variant)) or drivers.get(kind)
    if driver is None:
        raise LookupError(f"{plugin_id} registers no driver for {kind}:{variant}")
    return importlib.import_module(driver.__module__)


def provisioned_units(kind: str, variant: str, plugin_id: str) -> tuple[tuple[str, str], ...]:
    """What the driver resolves ``size=small`` to, read off the driver itself.

    Reflection rather than a table here on purpose. A transcribed shopping list
    is wrong the first time somebody retunes a tier, and it would be wrong
    silently -- the report would keep quoting a capacity nobody provisions. A
    driver that grows a size table gets picked up with no edit to this module.
    """
    module = _driver_module(kind, variant, plugin_id)
    found = []
    for name, value in sorted(vars(module).items()):
        if isinstance(value, dict) and set(value) == SIZE_KEYS:
            found.append((name, _render_unit(value[CAMPAIGN_SIZE])))
    return tuple(found)


def _render_unit(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items())
    if isinstance(value, tuple | list):
        return " / ".join(str(v) for v in value)
    return str(value)


def _meter(kind: str, variant: str, cloud: str, plugin_id: str) -> tuple[Meter, str]:
    """The billing shape, or a refusal to assume one.

    An unclassified variant stays unclassified. ``billing.py`` refuses to sweep
    one into class A and this report refuses to sweep one into "cheap" -- the
    same argument, one layer up. Guessing the shape here would let the report
    quote a total that the spend gate itself would not let anybody run.
    """
    if plugin_id != cloud:
        return (
            Meter.CLUSTER,
            f"in-cluster variant on the {cloud} tenant cluster; no managed-service meter, it consumes cluster capacity",
        )
    try:
        classification = billing.classify(cloud, kind, variant)
    except billing.UnclassifiedVariant as exc:
        return Meter.UNKNOWN, str(exc)
    return (
        _METER_BY_CLASS[classification.billing_class],
        f"class {classification.billing_class.value}: {classification.rationale}",
    )


def _priceability(kind: str, variant: str, plugin_id: str) -> tuple[Priceability, str]:
    """What the cloud's own estimator would return for this booking.

    Each branch reads the estimator's real dispatch table rather than a summary
    of it, so a variant that gains or loses a pricing path changes this report
    in the same commit.
    """
    pair = (kind, variant)
    if plugin_id == "k8s_native":
        return (
            Priceability.NOT_A_CLOUD_SKU,
            "in-cluster: cost is tenant-cluster capacity, which no managed-service estimator prices",
        )
    if plugin_id == "aws":
        if pair not in SERVICE_CODE_BY_VARIANT:
            return (
                Priceability.NO_ESTIMATOR,
                "aws/cost.py SERVICE_CODE_BY_VARIANT has no entry, so estimate() returns unsupported",
            )
        return (
            Priceability.APPROXIMATE,
            "aws/cost.py filters on location only and sums every price dimension of the "
            "first 20 products the Pricing API returns; the SKU-plan projection "
            "_sdk/cost_skus.py exists but the AWS estimator has not adopted it (#1318)",
        )
    if plugin_id == "gcp":
        if pair not in SERVICE_ID_BY_VARIANT and pair not in SERVICE_DISPLAY_NAME_BY_VARIANT:
            return (
                Priceability.NO_ESTIMATOR,
                "gcp/cost.py maps no Cloud Billing service id for this variant",
            )
        if has_plan(kind=kind, variant=variant):
            return (
                Priceability.EXACT,
                "gcp/cost_plans.py resolves one catalog row per billable dimension and refuses on ambiguity",
            )
        if pair in LEGACY_SUM_VARIANTS:
            return (
                Priceability.APPROXIMATE,
                "on gcp/cost.py LEGACY_SUM_VARIANTS: priced by summing every region-matching "
                "SKU in the service, which over-counts by construction (#1318)",
            )
        return (
            Priceability.NO_ESTIMATOR,
            "mapped to a service id but on neither the plan list nor LEGACY_SUM_VARIANTS, so gcp/cost.py refuses it",
        )
    if plugin_id == "azure":
        if pair not in SERVICE_NAME_BY_VARIANT:
            return (
                Priceability.NO_ESTIMATOR,
                "azure/cost.py SERVICE_NAME_BY_VARIANT has no entry for this variant",
            )
        return (
            Priceability.APPROXIMATE,
            "azure/cost.py sums the Retail Prices rows its OData filter returns for the service",
        )
    raise LookupError(f"no priceability rule for plugin {plugin_id!r}")


def booking(*, kind: str, cloud: str, variant: str, plugin_id: str) -> Booking:
    meter, meter_reason = _meter(kind, variant, cloud, plugin_id)
    priceability, priceability_reason = _priceability(kind, variant, plugin_id)
    return Booking(
        kind=kind,
        cloud=cloud,
        variant=variant,
        plugin_id=plugin_id,
        meter=meter,
        meter_reason=meter_reason,
        priceability=priceability,
        priceability_reason=priceability_reason,
        units=provisioned_units(kind, variant, plugin_id),
    )


def _from_cell(cell: Cell) -> CellSpend:
    return CellSpend(
        identifier=f"per-kind/{cell.kind}/{cell.cloud}",
        cloud=cell.cloud,
        bookings=(booking(kind=cell.kind, cloud=cell.cloud, variant=cell.variant, plugin_id=cell.plugin_id),),
    )


def per_kind_spend() -> tuple[CellSpend, ...]:
    """The 69-cell grid, one booking each."""
    return tuple(_from_cell(cell) for cell in cells())


def happy_path_spend() -> tuple[CellSpend, ...]:
    """The certification target: three cells, four managed services each.

    Costed separately from the grid because it is a different decision. The grid
    is coverage and could be trimmed; the happy path is what certifies the beta,
    so its spend is not optional.
    """
    out = []
    for cloud in CLOUDS:
        bookings = tuple(
            booking(kind=kind, cloud=cloud, variant=VARIANTS[kind][cloud], plugin_id=cloud)
            for kind, _ in HAPPY_PATH_SERVICES
        )
        out.append(CellSpend(identifier=f"happy-path/{cloud}", cloud=cloud, bookings=bookings))
    return tuple(out)


def all_spend() -> tuple[CellSpend, ...]:
    return happy_path_spend() + per_kind_spend()


def bookings() -> Iterator[Booking]:
    for cell in all_spend():
        yield from cell.bookings


# ---- cluster capacity --------------------------------------------------------


@dataclass(frozen=True)
class ClusterDemand:
    """Pod capacity the campaign's own workloads request, per cloud.

    Read out of the committed manifests rather than assumed, because the
    manifests are the thing that will actually be applied. It is small on
    purpose: the fixture is a web app, not a benchmark. Its value in this report
    is the contrast -- the tenant cluster is an hourly floor that dwarfs what
    the campaign puts on it, so the cluster costs what it costs whether the grid
    runs fast or slow.
    """

    cloud: str
    replicas: int
    cpu_request_millicores: int
    memory_request_mib: int

    @property
    def vcpu(self) -> float:
        return self.cpu_request_millicores / 1000.0

    @property
    def memory_gib(self) -> float:
        return self.memory_request_mib / 1024.0


def _millicores(value: str) -> int:
    return int(value[:-1]) if value.endswith("m") else int(float(value) * 1000)


def _mib(value: str) -> int:
    if value.endswith("Mi"):
        return int(value[:-2])
    if value.endswith("Gi"):
        return int(value[:-2]) * 1024
    raise ValueError(f"unhandled memory quantity {value!r}")


def cluster_demand() -> tuple[ClusterDemand, ...]:
    """Aggregate pod requests per cloud across every committed manifest."""
    totals: dict[str, list[int]] = {cloud: [0, 0, 0] for cloud in CLOUDS}
    for text in rendered().values():
        manifest = tomllib.loads(text)
        cloud = manifest["campaign"]["cloud"]
        for workload in manifest["workloads"]:
            replicas = int(workload["replicas"])
            totals[cloud][0] += replicas
            totals[cloud][1] += replicas * _millicores(workload["cpu_request"])
            totals[cloud][2] += replicas * _mib(workload["memory_request"])
    return tuple(
        ClusterDemand(
            cloud=cloud,
            replicas=values[0],
            cpu_request_millicores=values[1],
            memory_request_mib=values[2],
        )
        for cloud, values in totals.items()
    )


# ---- aggregates --------------------------------------------------------------


@dataclass(frozen=True)
class Exposure:
    """Time-metered resource-hours for a set of cells, both passes included.

    "Resource-hours" and not dollars: the hours are known, the rate is not. A
    reader multiplies by whatever the live pricing API says and gets the answer
    the campaign is waiting on.
    """

    label: str
    cells: int
    bookings: int
    hourly_floor: int
    fixed_floor: int
    per_request: int
    cluster: int
    unknown: int

    @property
    def time_metered(self) -> int:
        return self.hourly_floor + self.fixed_floor + self.cluster

    def hours(self, window_: Window) -> float:
        """Worst-case metered resource-hours for the time-metered bookings.

        Excludes the unknowns, deliberately. Folding them in at any assumed
        shape would produce exactly the confident wrong number this whole
        exercise exists to avoid; they are reported as a separate count instead.
        Cluster bookings are excluded too, for the reason on :data:`TIME_METERED`.
        """
        return self.time_metered * window_.campaign_hours

    def unknown_hours(self, window_: Window) -> float:
        """What the unknowns would add if every one of them metered hourly.

        The ceiling on the ignorance, not an estimate of it.
        """
        return self.unknown * window_.campaign_hours


def exposure(label: str, spend: tuple[CellSpend, ...]) -> Exposure:
    counts = dict.fromkeys(Meter, 0)
    total = 0
    for cell in spend:
        for item in cell.bookings:
            counts[item.meter] += 1
            total += 1
    return Exposure(
        label=label,
        cells=len(spend),
        bookings=total,
        hourly_floor=counts[Meter.HOURLY_FLOOR],
        fixed_floor=counts[Meter.FIXED_FLOOR],
        per_request=counts[Meter.PER_REQUEST],
        cluster=counts[Meter.CLUSTER],
        unknown=counts[Meter.UNKNOWN],
    )


def exposures() -> tuple[Exposure, ...]:
    """Per cloud, then the whole campaign."""
    spend = all_spend()
    per_cloud = tuple(exposure(cloud, tuple(c for c in spend if c.cloud == cloud)) for cloud in CLOUDS)
    return (*per_cloud, exposure("all clouds", spend))


def priceability_counts() -> dict[Priceability, int]:
    counts = dict.fromkeys(Priceability, 0)
    for item in bookings():
        counts[item.priceability] += 1
    return counts


def dominant_bookings() -> tuple[Booking, ...]:
    """The bookings that decide the total: everything on an hourly floor.

    Ranked no finer than that. Ranking the hourly-floor set against each other
    needs unit prices, and inventing a ranking would be inventing the prices it
    implies. What the report can say without a cloud is which cells are in the
    set at all -- and that set is the decision, because it is the only one whose
    cost moves when the campaign runs long, runs twice, or fails to tear down.
    """
    seen: dict[tuple[str, str, str], Booking] = {}
    for item in bookings():
        if item.meter is Meter.HOURLY_FLOOR:
            seen.setdefault((item.cloud, item.kind, item.variant), item)
    return tuple(seen.values())


@dataclass(frozen=True)
class StrandedBurn:
    """Daily accrual if the whole grid is left running.

    The campaign's real financial risk is not the run. A run is bounded by the
    window above and stops. A teardown that fails is unbounded: it bills until
    somebody notices, and the thing that makes somebody notice is the orphan
    scanner. This is the number that makes the scanner's value legible.

    Both passes are irrelevant here -- stranded resources do not care how many
    times the harness meant to visit them -- so this counts each distinct
    booking once.
    """

    hourly_floor: int
    fixed_floor: int
    per_request: int
    cluster: int
    unknown: int

    @property
    def accruing(self) -> int:
        """Cluster bookings count here and not in :meth:`Exposure.hours`.

        During a run the cluster is up regardless, so a cell's pods are free at
        the margin. Stranded forever, those same pods are what hold the node
        they sit on, so they are the reason the floor never drops.
        """
        return self.hourly_floor + self.fixed_floor + self.cluster

    @property
    def resource_hours_per_day(self) -> int:
        return self.accruing * 24

    @property
    def unknown_hours_per_day(self) -> int:
        return self.unknown * 24


def stranded_burn() -> StrandedBurn:
    total = exposure("stranded", all_spend())
    return StrandedBurn(
        hourly_floor=total.hourly_floor,
        fixed_floor=total.fixed_floor,
        per_request=total.per_request,
        cluster=total.cluster,
        unknown=total.unknown,
    )


def campaign_slug() -> str:
    return CAMPAIGN.slug
