"""SDK no longer exposes active estimators; native actual adapters remain private."""

import importlib

import pytest


@pytest.mark.parametrize(
    "module,symbol",
    [
        ("_sdk", "CostEstimator"),
        ("_sdk.cost", "CostEstimator"),
        ("aws.cost", "AWSCostEstimator"),
        ("gcp.cost", "GCPCostEstimator"),
        ("azure.cost", "AzureCostEstimator"),
    ],
)
def test_estimation_entrypoints_are_removed(module, symbol):
    assert not hasattr(importlib.import_module(module), symbol)


@pytest.mark.parametrize("module", ["_sdk.cost_skus", "gcp.cost_plans"])
def test_sku_rate_and_estimate_plan_modules_are_retired(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)
