"""Variant-specific Cloud Billing Catalog SKU plans (#1318).

``services/{id}/skus`` returns every SKU a Google service bills on.
For Cloud SQL that is one flat list covering three database engines,
two editions, zonal and regional (HA) compute, several storage types,
backup storage, licensing and network egress — in every region. The
estimator used to sum all of it, so adding a service id produced a
plausible-looking number that could be off by an order of magnitude.

This module turns a :class:`CostEstimateRequest` into a
:class:`_sdk.cost_skus.SkuPlan`: one component per dimension the
caller actually provisioned, each carrying the tokens that identify
its single catalog row. The shared selector then resolves them and
refuses anything it cannot pin down.

Deriving vCPU / memory / storage from the request is spec arithmetic,
not pricing: every price still comes from the live Catalog response.

The audit that came with #1318 found every other mapped GCP variant
over-counting the same way. Those are frozen on
``gcp.cost.LEGACY_SUM_VARIANTS`` and label their estimate approximate
until each grows a plan here; a newly mapped variant that is on
neither list is refused rather than summed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from _sdk.cost_skus import SkuComponent, SkuPlan

if TYPE_CHECKING:
    from _sdk.cost import CostEstimateRequest

# Cloud SQL variants, mapped to the engine name as it appears in the
# Catalog description ("Cloud SQL for PostgreSQL: …").
CLOUD_SQL_ENGINE_BY_VARIANT: dict[tuple[str, str], str] = {
    ("postgres", "cloudsql"): "PostgreSQL",
    ("mysql", "cloudsql"): "MySQL",
    ("mssql", "cloudsql_sqlserver"): "SQL Server",
}

# Only custom-machine tiers spell their capacity out. Legacy
# shared-core tiers (db-f1-micro, db-g1-small) bill as a single
# per-instance SKU instead of vCPU + RAM, and the fixed
# db-perf-optimized / db-n1-standard families hide their capacity in a
# machine-type table we would have to hard-code — both refuse instead.
_CUSTOM_TIER = re.compile(r"^db-custom-(\d+)-(\d+)$")

# ``storage_type`` config value -> Catalog description token.
_STORAGE_TOKEN: dict[str, str] = {
    "PD_SSD": "SSD",
    "PD_HDD": "HDD",
    "HYPERDISK_BALANCED": "Hyperdisk",
}

# SQL Server licence is billed per vCPU-hour at an edition-specific
# rate. Express carries no licence charge, so it has no SKU to find;
# every other edition must resolve one or the estimate is refused.
_SQLSERVER_LICENSE_TOKEN: dict[str, str] = {
    "WEB": "Web",
    "STANDARD": "Standard",
    "ENTERPRISE": "Enterprise",
}
_SQLSERVER_FREE_LICENSE = "EXPRESS"

_ENTERPRISE_PLUS = "Enterprise Plus"
_HOURS_PER_MONTH = 720.0

# Catalog ``category.usage_type``. Anything else is a committed-use or
# preemptible rate the control plane cannot promise at preview time.
_ON_DEMAND = {"usage_type": "OnDemand"}


@dataclass(frozen=True)
class PlanUnavailable:
    """No plan could be built — carries a ready-made refusal."""

    reason: str
    message: str


PlanResult = SkuPlan | PlanUnavailable


def plan_for(request: CostEstimateRequest) -> PlanResult:
    """Build the SKU plan for a managed-service estimate request.

    Guard with :func:`has_plan` — a :class:`PlanUnavailable` from here
    means the request itself is unpriceable, not that the variant is
    unplanned."""
    engine = CLOUD_SQL_ENGINE_BY_VARIANT.get((request.kind, request.variant))
    if engine is not None:
        return _cloud_sql_plan(request=request, engine=engine)
    return PlanUnavailable(
        reason="unsupported",
        message=(
            f"no Cloud Billing Catalog SKU plan for "
            f"({request.kind!r}, {request.variant!r}); the estimate is "
            "withheld because the service's SKU list spans unrelated "
            "products that must not be summed (#1318)"
        ),
    )


def _cloud_sql_plan(*, request: CostEstimateRequest, engine: str) -> PlanResult:
    config = request.config
    usage = request.expected_usage

    capacity = _resolve_capacity(request=request)
    if isinstance(capacity, PlanUnavailable):
        return capacity
    vcpu, memory_gib = capacity

    edition = str(config.get("cloudsql_edition") or "ENTERPRISE").upper()
    if edition not in {"ENTERPRISE", "ENTERPRISE_PLUS"}:
        return PlanUnavailable(
            reason="unsupported",
            message=f"unknown Cloud SQL edition {edition!r}",
        )

    storage_type = str(config.get("storage_type") or "PD_SSD").upper()
    storage_token = _STORAGE_TOKEN.get(storage_type)
    if storage_token is None:
        return PlanUnavailable(
            reason="unsupported",
            message=(f"unknown Cloud SQL storage_type {storage_type!r}; expected one of {sorted(_STORAGE_TOKEN)}"),
        )

    storage_gb = _positive_float(config.get("storage_gb")) or usage.get("storage_gb_month", 0.0)
    if storage_gb <= 0.0:
        return PlanUnavailable(
            reason="sku_not_found",
            message=(
                "Cloud SQL always bills provisioned storage; pass "
                "config['storage_gb'] or expected_usage['storage_gb_month']"
            ),
        )

    hours = float(usage.get("hours_per_month", _HOURS_PER_MONTH) or _HOURS_PER_MONTH)

    # HA is not a multiplier the platform applies. Google prices the
    # regional (HA) SKUs separately, so selecting them is the
    # multiplier — and it stays correct when Google reprices.
    high_availability = _as_bool(config.get("high_availability"))
    availability = "Regional" if high_availability else "Zonal"
    other_availability = "Zonal" if high_availability else "Regional"

    engine_token = f"for {engine}"
    other_engines = tuple(f"for {name}" for name in CLOUD_SQL_ENGINE_BY_VARIANT.values() if name != engine)

    # Enterprise Plus is a distinct compute rate; Enterprise must not
    # pick it up. Storage and backup are edition-independent, so the
    # edition tokens are compute-only.
    if edition == "ENTERPRISE_PLUS":
        edition_require: tuple[str, ...] = (_ENTERPRISE_PLUS,)
        edition_exclude: tuple[str, ...] = ()
    else:
        edition_require = ()
        edition_exclude = (_ENTERPRISE_PLUS,)

    series = str(config.get("machine_series") or "").strip()
    series_require = (series,) if series else ()

    compute_require = (engine_token, availability, *edition_require, *series_require)
    compute_exclude = (*other_engines, other_availability, *edition_exclude)

    components: list[SkuComponent] = [
        SkuComponent(
            key="vcpu",
            label=f"Cloud SQL for {engine} {availability} vCPU",
            quantity=vcpu * hours,
            quantity_unit="vCPU-hour",
            require=(*compute_require, "vCPU"),
            exclude=(*compute_exclude, "RAM"),
            attributes=_ON_DEMAND,
        ),
        SkuComponent(
            key="memory",
            label=f"Cloud SQL for {engine} {availability} RAM",
            quantity=memory_gib * hours,
            quantity_unit="GiB-hour",
            require=(*compute_require, "RAM"),
            exclude=(*compute_exclude, "vCPU"),
            attributes=_ON_DEMAND,
        ),
        SkuComponent(
            key="storage",
            label=f"Cloud SQL for {engine} {availability} {storage_token} storage",
            quantity=storage_gb,
            quantity_unit="GiB-month",
            require=(engine_token, availability, storage_token, "storage"),
            exclude=(*other_engines, other_availability, "Backup"),
            attributes=_ON_DEMAND,
        ),
    ]

    backup_gb = usage.get("backup_storage_gb_month", 0.0)
    if backup_gb > 0.0:
        components.append(
            SkuComponent(
                key="backup",
                label=f"Cloud SQL for {engine} backup storage",
                quantity=backup_gb,
                quantity_unit="GiB-month",
                require=(engine_token, "Backup"),
                exclude=other_engines,
                attributes=_ON_DEMAND,
            )
        )

    notes: list[str] = []
    if engine == "SQL Server":
        license_component = _sqlserver_license_component(
            config=config,
            availability=availability,
            other_availability=other_availability,
            engine_token=engine_token,
            other_engines=other_engines,
            vcpu=vcpu,
            hours=hours,
        )
        if isinstance(license_component, PlanUnavailable):
            return license_component
        if license_component is None:
            notes.append("SQL Server Express carries no licence charge; no licence line item.")
        else:
            components.append(license_component)

    egress_gb = usage.get("network_egress_gb", 0.0)
    if egress_gb > 0.0:
        destination = str(config.get("network_egress_destination") or "").strip()
        components.append(
            SkuComponent(
                key="network_egress",
                label="Cloud SQL network egress",
                quantity=egress_gb,
                quantity_unit="GiB",
                require=("Network", "Egress", *((destination,) if destination else ())),
                exclude=("Ingress",),
                attributes=_ON_DEMAND,
            )
        )

    notes.append(
        f"priced as {vcpu:g} vCPU + {memory_gib:g} GiB {edition} "
        f"{availability} with {storage_gb:g} GiB {storage_type} storage"
    )
    return SkuPlan(components=tuple(components), notes=tuple(notes))


def _sqlserver_license_component(
    *,
    config: dict[str, str],
    availability: str,
    other_availability: str,
    engine_token: str,
    other_engines: tuple[str, ...],
    vcpu: float,
    hours: float,
) -> SkuComponent | PlanUnavailable | None:
    """Licence line for Cloud SQL for SQL Server, or None when the
    requested engine edition is licence-free."""
    engine_version = str(config.get("engine_version") or "").upper()
    if not engine_version:
        return PlanUnavailable(
            reason="sku_not_found",
            message=(
                "Cloud SQL for SQL Server bills a per-vCPU licence whose "
                "rate depends on the server edition; pass "
                "config['engine_version'] (e.g. SQLSERVER_2022_STANDARD)"
            ),
        )
    server_edition = engine_version.rsplit("_", 1)[-1]
    if server_edition == _SQLSERVER_FREE_LICENSE:
        return None
    license_token = _SQLSERVER_LICENSE_TOKEN.get(server_edition)
    if license_token is None:
        return PlanUnavailable(
            reason="unsupported",
            message=(
                f"unknown SQL Server edition {server_edition!r} in "
                f"engine_version {engine_version!r}; cannot pick a licence SKU"
            ),
        )
    other_licenses = tuple(token for edition, token in _SQLSERVER_LICENSE_TOKEN.items() if token != license_token)
    return SkuComponent(
        key="license",
        label=f"SQL Server {license_token} licence",
        quantity=vcpu * hours,
        quantity_unit="vCPU-hour",
        require=(engine_token, license_token, "License"),
        exclude=(*other_engines, *other_licenses, other_availability),
        attributes=_ON_DEMAND,
    )


def _resolve_capacity(
    *,
    request: CostEstimateRequest,
) -> tuple[float, float] | PlanUnavailable:
    """vCPU + memory GiB for the requested instance.

    The tier name is authoritative when it spells the capacity out.
    Otherwise the caller has to say, because the alternative is a
    hard-coded machine-type table that goes stale silently.
    """
    tier = str(request.config.get("tier") or "")
    match = _CUSTOM_TIER.match(tier)
    if match is not None:
        return float(match.group(1)), float(match.group(2)) / 1024.0

    vcpu = _positive_float(request.expected_usage.get("vcpu"))
    memory_gib = _positive_float(request.expected_usage.get("memory_gib"))
    if vcpu and memory_gib:
        return vcpu, memory_gib
    return PlanUnavailable(
        reason="sku_not_found",
        message=(
            f"cannot derive vCPU + memory from Cloud SQL tier {tier!r}; "
            "pass a db-custom-<vcpu>-<memory_mib> tier or "
            "expected_usage['vcpu'] + expected_usage['memory_gib']"
        ),
    )


def _positive_float(value: object) -> float:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0.0 else 0.0


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"true", "1", "yes"}


def has_plan(*, kind: str, variant: str) -> bool:
    """Cheap, network-free check used by ``CostEstimator.supported``."""
    return (kind, variant) in CLOUD_SQL_ENGINE_BY_VARIANT
