from __future__ import annotations

from types import SimpleNamespace

from _sdk.cost import CostEstimate, CostEstimateRequest, CostLineItem

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
