"""Live-pricing preview for one managed-service desired state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ManagedServiceCostPreview:
    available: bool
    reason: str = ""
    message: str = ""
    monthly_total: float | None = None
    currency: str = "USD"
    line_items: tuple[dict[str, Any], ...] = ()
    pricing_source_url: str = ""
    pricing_fetched_at: str = ""
    notes: tuple[str, ...] = ()
    approximate: bool = False


def preview_managed_service_cost(service) -> ManagedServiceCostPreview:
    """Ask the bound cloud's live pricing API; never synthesize a fallback."""
    from _sdk.cost import (
        CostEstimate,
        CostEstimateRequest,
        CostEstimateUnavailable,
    )

    from astrolift_lifecycle.preview_cost import cost_estimator_for_cluster

    cluster = service.effective_cluster
    if cluster is None:
        return ManagedServiceCostPreview(False, reason="no_cluster", message="resource has no bound cluster")
    estimator = cost_estimator_for_cluster(cluster)
    if estimator is None:
        return ManagedServiceCostPreview(
            False,
            reason="no_pricing_api",
            message="this provider has no live pricing estimator installed",
        )

    desired = dict(service.config or {})
    usage_raw = desired.pop("expected_usage", {}) or {}
    expected_usage: dict[str, float] = {}
    if isinstance(usage_raw, dict):
        for key, value in usage_raw.items():
            try:
                expected_usage[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    request_config = {str(key): str(value) for key, value in desired.items() if value is not None}
    provider_config = dict(getattr(cluster, "provider_config", None) or {})
    if provider_config.get("subscription_id"):
        request_config.setdefault("subscription_id", str(provider_config["subscription_id"]))
    request = CostEstimateRequest(
        kind=service.kind,
        variant=service.variant or "",
        region=str(getattr(cluster, "region", "") or ""),
        size=str(desired.get("size", "small")),
        config=request_config,
        expected_usage=expected_usage,
    )
    try:
        result = estimator.estimate(request)
    except Exception as exc:  # noqa: BLE001 - absence is a first-class response
        return ManagedServiceCostPreview(False, reason="api_error", message=str(exc))
    if isinstance(result, CostEstimateUnavailable):
        return ManagedServiceCostPreview(False, reason=result.reason, message=result.message)
    if not isinstance(result, CostEstimate):
        return ManagedServiceCostPreview(False, reason="unsupported", message="estimator returned no price")
    return ManagedServiceCostPreview(
        True,
        monthly_total=float(result.monthly_total),
        currency=result.currency,
        line_items=tuple(
            {
                "label": item.label,
                "sku": item.sku,
                "monthly_amount": item.monthly_amount,
                "currency": item.currency,
                "notes": item.notes,
            }
            for item in result.line_items
        ),
        pricing_source_url=result.pricing_source_url,
        pricing_fetched_at=result.pricing_fetched_at,
        notes=tuple(result.notes),
        approximate=result.approximate,
    )


__all__ = ["ManagedServiceCostPreview", "preview_managed_service_cost"]
