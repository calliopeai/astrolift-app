"""Cross-driver Azure managed-resource tag contract."""

from __future__ import annotations

import ast
import pathlib
from dataclasses import replace

import pytest

from _sdk.azure_tags import AzureTagError
from _sdk.managed_service import ProvisionSpec
from _sdk.managed_service_tags import canonical_key, ownership_key
from azure.managed import (
    cache_redis,
    cosmos,
    cosmos_api,
    email_acs,
    event_grid,
    event_grid_namespace,
    event_hubs,
    managed_redis,
    model_endpoint_aoai,
    mssql_sql,
    mysql_flexible,
    postgres_flexible,
    queue_servicebus,
    search_aisearch,
    timeseries_monitor,
    vector_search,
)
from azure.managed import tags as tags_module
from azure.managed.tags import arm_tags_for

MANAGED_ROOT = pathlib.Path(tags_module.__file__).parent


def _spec() -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-id",
        organization_slug="acme",
        app_id="app-id",
        app_slug="api",
        environment_id="env-id",
        environment_name="prod",
        tenant_cluster_id="cluster-id",
        service_handle_hint="shared",
        size="small",
        binding_id="binding-id",
        managed_service_id="service-id",
        tags={"Cost Center": "engineering", "cost/center": "operations"},
    )


def test_every_arm_managed_driver_uses_the_shared_codec() -> None:
    for module in (
        cache_redis,
        cosmos,
        cosmos_api,
        email_acs,
        event_grid,
        event_grid_namespace,
        event_hubs,
        managed_redis,
        model_endpoint_aoai,
        mssql_sql,
        mysql_flexible,
        postgres_flexible,
        search_aisearch,
        timeseries_monitor,
        vector_search,
    ):
        assert module.tags_for is arm_tags_for
    assert queue_servicebus._tags_for is arm_tags_for


def test_the_envelope_is_written_under_the_keys_ownership_reads() -> None:
    """The round-trip #1431 broke.

    Five drivers built the canonical dict and skipped serialization, so the
    keys they sent ARM were not the keys anything could read back. Pinning the
    written names to the declared ownership keys is what keeps write and read
    from drifting apart again.
    """
    tags = arm_tags_for(_spec())

    assert tags[canonical_key("azure")] == "service-id"
    assert {
        ownership_key("azure", "managed_by"): "platform",
        ownership_key("azure", "binding"): "binding-id",
        ownership_key("azure", "org"): "acme",
        ownership_key("azure", "app"): "api",
        ownership_key("azure", "env"): "prod",
        ownership_key("azure", "cluster"): "cluster-id",
        ownership_key("azure", "isolation"): "shared",
    }.items() <= tags.items()


def test_no_managed_driver_ships_an_unserialized_key_to_azure() -> None:
    """The statically detectable half of #1431.

    Every surface an ``azure/managed`` driver writes to rejects ``/`` in a key:
    ARM tag names outright, blob and file-share metadata too. So the canonical
    ``astrolift.io/`` namespace only ever belongs on the input side of
    ``tags.py``. A literal anywhere else is a key that cannot land, on the write
    side, or one that can never match, on the read side.
    """
    offenders: dict[str, list[str]] = {}
    for path in sorted(MANAGED_ROOT.glob("*.py")):
        if path.name == "tags.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            ast.get_docstring(node, clean=False)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if "astrolift.io/" in node.value and node.value not in docstrings:
                offenders.setdefault(path.name, []).append(node.value)

    assert not offenders, (
        "azure/managed drivers name tags in the pre-serialization namespace: "
        + "; ".join(f"{name}: {sorted(set(keys))}" for name, keys in sorted(offenders.items()))
        + ". Build the canonical dict in tags.py and let arm_tags_for serialize it; "
        "read through _sdk.managed_service_tags."
    )


def test_managed_tag_envelope_is_arm_safe_and_cost_joinable() -> None:
    tags = arm_tags_for(
        _spec(),
        platform_tags={"retention-days": 30},
    )
    assert tags["astrolift-managed-by"] == "platform"
    assert tags["astrolift-org"] == "acme"
    assert tags["astrolift-env"] == "prod"
    assert tags["astrolift-binding"] == "binding-id"
    assert tags["astrolift-managed-service-id"] == "service-id"
    assert tags["astrolift-retention-days"] == "30"
    assert len({key.casefold() for key in tags}) == len(tags)
    assert all(not set("<>%&\\?/") & set(key) for key in tags)


def test_managed_tag_limits_fail_before_a_driver_can_build_a_request() -> None:
    with pytest.raises(AzureTagError, match="at most 50"):
        arm_tags_for(replace(_spec(), tags={f"tag-{index}": "value" for index in range(43)}))
    with pytest.raises(AzureTagError, match="256"):
        arm_tags_for(replace(_spec(), tags={"oversized": "x" * 257}))
