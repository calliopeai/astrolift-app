from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest
from _sdk.cost import CostEstimate, CostEstimateRequest, CostEstimateUnavailable, CostLineItem

from astrolift_services.cost_preview import preview_managed_service_cost
from astrolift_services.provider_links import provider_portal_url


def _service(*, plugin="azure", backend_ref=""):
    cluster = SimpleNamespace(
        provider_plugin=SimpleNamespace(slug=plugin),
        provider_config={"subscription_id": "sub-123", "project_id": "gcp-project"},
        region="eastus",
    )
    return SimpleNamespace(
        guid="service-guid",
        kind="mssql",
        variant="azure_sql_database",
        name="records",
        config={"size": "small"},
        backend_ref=backend_ref,
        effective_cluster=cluster,
    )


def test_azure_arm_id_gets_an_exact_portal_resource_link():
    resource_id = "/subscriptions/sub-123/resourceGroups/rg/providers/Microsoft.Sql/servers/db"

    url = provider_portal_url(_service(backend_ref=resource_id))

    assert url.startswith("https://portal.azure.com/#resource/subscriptions/sub-123/")
    assert url.endswith("/overview")


def test_cost_preview_returns_live_estimator_evidence(monkeypatch):
    class Estimator:
        def estimate(self, request: CostEstimateRequest):
            assert request.config["subscription_id"] == "sub-123"
            return CostEstimate(
                request=request,
                line_items=[CostLineItem(label="Compute", sku="sku-1", monthly_amount=42.5)],
                monthly_total=42.5,
                pricing_source_url="https://prices.example.test/sku-1",
                pricing_fetched_at="2026-08-23T00:00:00Z",
                notes=["Live list price"],
            )

    monkeypatch.setattr(
        "astrolift_lifecycle.preview_cost.cost_estimator_for_cluster",
        lambda cluster: Estimator(),
    )

    result = preview_managed_service_cost(_service())

    assert result.available is True
    assert result.monthly_total == 42.5
    assert result.line_items[0]["sku"] == "sku-1"
    assert result.pricing_source_url == "https://prices.example.test/sku-1"


@pytest.mark.parametrize(
    "change",
    [
        {"monthly_total": None},
        {"monthly_total": float("nan")},
        {"monthly_total": float("inf")},
        {"monthly_total": -1},
        {"currency": ""},
        {"pricing_source_url": ""},
        {"pricing_fetched_at": ""},
        {"pricing_fetched_at": "2026-10-02T00:00:00"},
    ],
)
def test_missing_or_invalid_price_evidence_is_unavailable(monkeypatch, change):
    class Estimator:
        def estimate(self, request):
            return dataclasses.replace(
                CostEstimate(
                    request=request,
                    line_items=[],
                    monthly_total=2,
                    pricing_source_url="https://prices.example.test/sku",
                    pricing_fetched_at="2026-10-02T00:00:00Z",
                ),
                **change,
            )

    monkeypatch.setattr("astrolift_lifecycle.preview_cost.cost_estimator_for_cluster", lambda _: Estimator())
    result = preview_managed_service_cost(_service())
    assert result.available is False
    assert result.monthly_total is None
    assert result.reason == "incomplete_price"


def test_explicit_zero_price_with_source_time_and_currency_is_valid(monkeypatch):
    class Estimator:
        def estimate(self, request):
            return CostEstimate(
                request=request,
                line_items=[],
                monthly_total=0,
                pricing_source_url="https://prices.example.test/sku",
                pricing_fetched_at="2026-10-02T00:00:00Z",
            )

    monkeypatch.setattr("astrolift_lifecycle.preview_cost.cost_estimator_for_cluster", lambda _: Estimator())
    result = preview_managed_service_cost(_service())
    assert result.available is True
    assert result.monthly_total == 0


@pytest.mark.parametrize("raised", [True, False])
def test_pricing_errors_do_not_echo_private_provider_diagnostics(monkeypatch, raised):
    class Estimator:
        def estimate(self, request):
            if raised:
                raise RuntimeError("PRIVATE_PROVIDER_MARKER")
            return CostEstimateUnavailable(
                request=request, reason="api_error", message="PRIVATE_PROVIDER_MARKER"
            )

    monkeypatch.setattr("astrolift_lifecycle.preview_cost.cost_estimator_for_cluster", lambda _: Estimator())
    result = preview_managed_service_cost(_service())
    assert result.available is False
    assert "PRIVATE_PROVIDER_MARKER" not in repr(result)
