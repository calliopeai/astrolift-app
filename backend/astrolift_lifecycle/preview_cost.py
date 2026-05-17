"""Per-preview resource + cost aggregation (#431).

Sums the CPU and memory requests of every pod currently running in a
preview environment's namespace and turns the total into a daily-cost
estimate by asking the cluster's provider plugin for a *live* compute
SKU price.

Hard rule (workspace policy): no hard-coded prices anywhere. If the
driver can't reach its pricing API — or doesn't implement the cost-
estimator capability for compute — the aggregator returns ``None`` for
``estimated_daily_cost_usd``. The UI renders that as a dash so we
never show a fabricated number.

The CPU + memory totals are still useful on their own (operators want
to spot a 32-pod preview even when pricing is unavailable), so they
surface independently of the cost number.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---- units ---------------------------------------------------------


_CPU_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)(m?)\s*$")
_MEM_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|Pi|Ei|K|M|G|T|P|E)?\s*$",
)

_MEM_UNIT_BYTES: dict[str, float] = {
    "": 1.0,
    "Ki": 1024.0,
    "Mi": 1024.0**2,
    "Gi": 1024.0**3,
    "Ti": 1024.0**4,
    "Pi": 1024.0**5,
    "Ei": 1024.0**6,
    "K": 1000.0,
    "M": 1000.0**2,
    "G": 1000.0**3,
    "T": 1000.0**4,
    "P": 1000.0**5,
    "E": 1000.0**6,
}


def parse_cpu_cores(value: str) -> float:
    """Parse a k8s CPU quantity string to fractional cores.

    Accepts ``"100m"`` (millicores), ``"0.5"``, ``"2"``, empty string
    (returns 0). Anything unrecognised raises ``ValueError`` so a
    badly-formed pod spec surfaces immediately instead of silently
    counting as zero (which would under-report cost)."""
    if not value:
        return 0.0
    match = _CPU_RE.match(value)
    if not match:
        raise ValueError(f"unparseable CPU quantity: {value!r}")
    number = float(match.group(1))
    if match.group(2) == "m":
        number /= 1000.0
    return number


def parse_memory_bytes(value: str) -> float:
    """Parse a k8s memory quantity string to bytes.

    Accepts power-of-two suffixes (``Ki`` / ``Mi`` / ``Gi`` / …) and
    SI suffixes (``K`` / ``M`` / ``G`` / …) per the k8s convention."""
    if not value:
        return 0.0
    match = _MEM_RE.match(value)
    if not match:
        raise ValueError(f"unparseable memory quantity: {value!r}")
    number = float(match.group(1))
    suffix = match.group(2) or ""
    return number * _MEM_UNIT_BYTES[suffix]


def format_cpu_cores(cores: float) -> str:
    """Render fractional cores for the UI label.

    < 1 core → ``"500m"`` style (matches the k8s convention operators
    already read in pod specs); ≥ 1 core → ``"1.5 CPU"`` so the unit
    is unambiguous."""
    if cores <= 0:
        return "0 CPU"
    if cores < 1:
        return f"{int(round(cores * 1000))}m CPU"
    return f"{cores:.2f}".rstrip("0").rstrip(".") + " CPU"


def format_memory_bytes(byte_count: float) -> str:
    """Render bytes in the largest unit that keeps the integer ≤ 1024.

    Operators read k8s manifests in ``Mi`` / ``Gi``; mirroring that
    keeps the column aligned with what they see in ``kubectl describe``.
    """
    if byte_count <= 0:
        return "0 Mi"
    units = [("Gi", 1024.0**3), ("Mi", 1024.0**2), ("Ki", 1024.0)]
    for label, divisor in units:
        if byte_count >= divisor:
            value = byte_count / divisor
            if value >= 100:
                return f"{int(round(value))} {label}"
            return f"{value:.1f} {label}"
    return f"{int(round(byte_count))} B"


# ---- aggregation ---------------------------------------------------


@dataclass(frozen=True)
class PreviewAggregateResources:
    """Sum of CPU + memory requests across every container in every pod
    in a preview namespace.

    ``cpu_cores`` is fractional cores (e.g. ``0.75``).
    ``memory_bytes`` is raw bytes — caller formats for display.
    """

    cpu_cores: float
    memory_bytes: float
    pod_count: int


def aggregate_pod_resources(pods: list[Any]) -> PreviewAggregateResources:
    """Sum container requests over the list of pods.

    Skips pods in terminal phases (``Succeeded`` / ``Failed``) because
    they're not consuming cluster compute. Sidecars + init containers
    are summed too — they cost the same as the primary container.

    Any unparseable resource string is logged + skipped rather than
    raising, so one bad pod doesn't wipe the whole aggregate.
    """
    cpu = 0.0
    mem = 0.0
    counted = 0
    for pod in pods:
        phase = (getattr(pod, "phase", "") or "").lower()
        if phase in {"succeeded", "failed"}:
            continue
        counted += 1
        for cs in getattr(pod, "container_statuses", []) or []:
            res = getattr(cs, "resources", None)
            if res is None:
                continue
            try:
                cpu += parse_cpu_cores(getattr(res, "cpu_request", "") or "")
                mem += parse_memory_bytes(getattr(res, "memory_request", "") or "")
            except ValueError as exc:
                logger.debug(
                    "preview_cost: skipping pod=%s container=%s — %s",
                    getattr(pod, "name", "?"),
                    getattr(cs, "name", "?"),
                    exc,
                )
                continue
    return PreviewAggregateResources(
        cpu_cores=cpu,
        memory_bytes=mem,
        pod_count=counted,
    )


# ---- pricing -------------------------------------------------------


# Kind / variant identifier we hand the driver's CostEstimator when
# asking for compute-time pricing. Drivers that implement the cost
# capability must recognise this kind; otherwise they should return
# ``CostEstimateUnavailable`` and the aggregator surfaces ``None``.
_COMPUTE_KIND = "compute"
_COMPUTE_VARIANT = "node_hour"


def cost_estimator_for_cluster(cluster: Any) -> Any | None:
    """Resolve the live-pricing CostEstimator for ``cluster``.

    Cost is not a registered ``drivers[<role>]`` capability on the
    upstream ProviderPlugin manifest — the estimators live in
    ``aws.cost`` / ``gcp.cost`` / ``azure.cost`` as separate classes
    with their own ``*CostConfig``. So instead of going through
    ``driver_for_capability`` (which routes through ``_config_for``
    and only knows about cluster/managed-service configs), this
    helper instantiates the right estimator directly based on the
    plugin slug, reading the row's ``provider_config`` for the
    fields each cost driver needs.

    Returns ``None`` for plugins without a live-pricing path
    (``k8s_native`` — bare metal has no cloud-side SKU to ask),
    for any provisional row where the plugin slug is empty, and
    for any import error (e.g. boto3 missing in a stripped image).
    Callers branch on ``None`` to render 'Cost unavailable' rather
    than fabricating a number.
    """
    plugin_slug = ""
    try:
        provider_plugin = getattr(cluster, "provider_plugin", None)
        if provider_plugin is not None:
            plugin_slug = getattr(provider_plugin, "slug", "") or ""
    except Exception:  # noqa: BLE001
        return None
    if not plugin_slug:
        return None

    provider_config: dict[str, Any] = getattr(cluster, "provider_config", {}) or {}

    if plugin_slug == "aws":
        try:
            from aws.cost import AWSCostConfig, AWSCostEstimator
        except ImportError:
            return None
        try:
            return AWSCostEstimator(config=AWSCostConfig())
        except Exception:  # noqa: BLE001
            return None
    if plugin_slug == "gcp":
        try:
            from gcp.cost import GCPCostConfig, GCPCostEstimator
        except ImportError:
            return None
        try:
            return GCPCostEstimator(config=GCPCostConfig())
        except Exception:  # noqa: BLE001
            return None
    if plugin_slug == "azure":
        try:
            from azure.cost import AzureCostConfig, AzureCostEstimator
        except ImportError:
            return None
        # The default Azure VM-size lookup needs a subscription_id;
        # the request layer threads it through via CostEstimateRequest
        # .config['subscription_id'] when present on the row.
        try:
            return AzureCostEstimator(config=AzureCostConfig())
        except Exception:  # noqa: BLE001
            return None
    # k8s_native + unknown plugins -> no live pricing path; the UI
    # renders 'Cost unavailable'.
    _ = provider_config
    return None


def estimate_daily_cost_usd(
    *,
    cluster: Any,
    aggregate: PreviewAggregateResources,
    region: str = "",
) -> float | None:
    """Ask the cluster's provider plugin for a live compute SKU price
    and turn the aggregate into a daily $ figure.

    Returns ``None`` when:
      - the cluster has no cost driver registered
      - the driver doesn't support the ``(compute, node_hour)`` pair
      - the pricing API call fails or returns no line items
      - the aggregate has no CPU + memory (nothing to cost)

    NEVER falls back to a hard-coded SKU rate — workspace rule
    (see ``vendor/astrolift-providers/_sdk/cost.py``). The UI renders
    the missing number as a dash with a tooltip; a fabricated estimate
    would erode operator trust in the column."""
    if aggregate.cpu_cores <= 0 and aggregate.memory_bytes <= 0:
        return None

    driver = cost_estimator_for_cluster(cluster)
    if driver is None:
        return None

    try:
        from _sdk.cost import (
            CostEstimate,
            CostEstimateRequest,
        )
    except ImportError:
        return None

    # Per-plugin extra request config: Azure's compute pricing needs
    # the subscription_id so the default VM-size lookup can talk to
    # ARM. Other plugins ignore extras.
    request_config: dict[str, str] = {}
    provider_config: dict[str, Any] = getattr(cluster, "provider_config", {}) or {}
    sub_id = provider_config.get("subscription_id")
    if sub_id:
        request_config["subscription_id"] = str(sub_id)

    request = CostEstimateRequest(
        kind=_COMPUTE_KIND,
        variant=_COMPUTE_VARIANT,
        region=region or getattr(cluster, "region", "") or "",
        size="custom",
        config=request_config,
        expected_usage={
            "cpu_cores": aggregate.cpu_cores,
            "memory_gib": aggregate.memory_bytes / (1024.0**3),
            # 24h * 30 days ~= 720 hours/month — drivers translate
            # this to per-hour SKU pricing internally.
            "hours_per_month": 720.0,
        },
    )

    try:
        result = driver.estimate(request)
    except NotImplementedError:
        return None
    except Exception:  # noqa: BLE001 — pricing API can fail in many ways
        return None

    if not isinstance(result, CostEstimate):
        # CostEstimateUnavailable + anything else: surface as missing.
        return None
    monthly = float(getattr(result, "monthly_total", 0.0) or 0.0)
    if monthly <= 0.0:
        return None
    # 30-day month — matches the AWS Pricing API convention (720h)
    # so the daily / monthly arithmetic stays consistent end-to-end.
    daily = monthly / 30.0
    return round(daily, 2)
