from __future__ import annotations

import re

import pytest

from _sdk.k8s_naming import app_namespace, dns_label


def test_short_canonical_names_remain_compatible() -> None:
    assert dns_label("api", "production", "primary") == "api-production-primary"
    assert app_namespace(organization_slug="acme", app_slug="api") == "acme-api"


def test_long_names_are_bounded_stable_and_collision_resistant() -> None:
    left = app_namespace(organization_slug="organization-" + "a" * 40, app_slug="service-" + "b" * 32)
    same = app_namespace(organization_slug="organization-" + "a" * 40, app_slug="service-" + "b" * 32)
    right = app_namespace(organization_slug="organization-" + "a" * 40, app_slug="service-" + "c" * 32)

    assert left == same
    assert left != right
    assert len(left) <= 63
    assert re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", left)


def test_normalization_cannot_collapse_distinct_inputs() -> None:
    assert dns_label("billing_api") != dns_label("billing-api")
    assert dns_label("Billing-API") != dns_label("billing-api")


def test_custom_budget_preserves_suffix_room() -> None:
    value = dns_label("mssql", "app" * 30, "production", max_length=50)
    assert len(value) <= 50


@pytest.mark.parametrize("max_length", [0, 10, 64])
def test_invalid_budgets_fail(max_length: int) -> None:
    with pytest.raises(ValueError, match="max_length"):
        dns_label("api", max_length=max_length)
