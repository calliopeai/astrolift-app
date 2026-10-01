"""Real PostgreSQL lifecycle dispatch through a privately installed declared Azure SDK wheel."""

from __future__ import annotations

import copy
import dataclasses
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from _sdk.managed_service import DeprovisionSpec
from azure.managed.event_grid import AzureEventGridDriver, AzureEventGridError

from astrolift_workflows.activities.managed_service_lifecycle import (
    _check_ready_sync,
    _deprovision_sync,
    _managed_binding_for,
    _provision_sync,
    _update_sync,
    build_provision_spec,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service
from providers.tests.azure.test_event_grid_wire_2032 import (
    RecordingEventGrid,
    declared,
    recording_config,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def cloud():
    api = RecordingEventGrid()
    cfg = recording_config(api)
    state = SimpleNamespace(api=api, cfg=cfg)
    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=AzureEventGridDriver),
        patch("core.cluster_observability.managed_config_for", side_effect=lambda *_, **__: state.cfg),
    ):
        yield state
    cfg.mgmt_client.close()
    cfg.locks_client.close()
    assert api.closed


def _bus(slug, *, config=None):
    svc = _service(org_slug=slug, plugin_slug="azure", variant="event_grid", backend_ref="")
    svc.kind = "event_bus"
    svc.name = "events"
    svc.config = config if config is not None else {"subscriptions": [declared()]}
    svc.save(update_fields=["kind", "name", "config"])
    return svc


def _owned(cloud, slug):
    svc = _bus(slug)
    result = _provision_sync(svc.pk)
    assert result["ok"] and result["ready"], result
    svc.backend_ref = result["handle"]
    svc.save(update_fields=["backend_ref"])
    cloud.api.calls.clear()
    return svc


def _writes(cloud):
    return [method for method, *_ in cloud.api.calls if method != "GET"]


def test_distinct_org_and_same_app_service_ids_have_immutable_parent_and_child_names(cloud):
    rows = [_bus("alpha-beta"), _bus("alpha")]
    rows[0].registered_app.slug = "gamma"
    rows[1].registered_app.slug = "beta-gamma"
    for row in rows:
        row.registered_app.save(update_fields=["slug"])
    # Human slug joins collide; independent legitimate services in one app/env also retain distinct UUIDs.
    old = [
        "-".join((r.registered_app.organization.slug, r.registered_app.slug, r.app_environment.name, r.name))
        for r in rows
    ]
    assert old[0] == old[1]
    from astrolift_services.models import ManagedService

    sibling = ManagedService.objects.create(
        registered_app=rows[0].registered_app,
        app_environment=rows[0].app_environment,
        kind="event_bus",
        variant="event_grid",
        name="secondary-events",
        config=rows[0].config,
    )
    rows.append(sibling)
    results = [_provision_sync(row.pk) for row in rows]
    assert all(result["ok"] and result["ready"] for result in results), results
    assert len({result["handle"] for result in results}) == 3 and len(cloud.api.rows) == 6
    for row, result in zip(rows, results, strict=True):
        physical = result["handle"].rsplit("/", 1)[1]
        assert physical.endswith(str(row.guid).replace("-", "")) and len(physical) <= 50
        children = [
            value for key, value in cloud.api.rows.items() if f"/topics/{physical}/eventSubscriptions/" in key
        ]
        assert len(children) == 1 and str(row.guid).replace("-", "") in children[0]["name"]
        row.backend_ref = result["handle"]
        row.save(update_fields=["backend_ref"])
        binding = _managed_binding_for(row)
        assert binding.env_vars["EVENT_BUS_NAME"].literal == physical
        assert binding.iam_grants[0].resource.endswith("/topics/" + physical)
        assert _check_ready_sync(row.pk, row.backend_ref) == "available"
        assert _provision_sync(row.pk)["handle"] == row.backend_ref


@pytest.mark.parametrize("observation", ["remaining-child", "denied-inventory"])
def test_new_lifecycle_source_cannot_create_over_unknown_child_inventory(cloud, observation):
    svc = _owned(cloud, "new-grid-child-proof")
    parent = next(key for key in cloud.api.rows if "/eventSubscriptions/" not in key)
    del cloud.api.rows[parent]
    svc.backend_ref = ""
    svc.save(update_fields=["backend_ref"])
    if observation == "denied-inventory":
        cloud.api.rows.clear()
        cloud.api.failures["GET", "children"] = (403, "AuthorizationFailed")
    result = _provision_sync(svc.pk)
    assert not result["ok"] and result["errors"] == ["ownership_unknown"]
    assert _writes(cloud) == []
    svc.refresh_from_db()
    assert svc.backend_ref == "" and svc.provider_cleanup_receipt is None


def test_recorded_names_survive_rename_prefix_changes_and_long_declared_children(cloud):
    svc = _bus(
        "recorded-grid-owner", config={"subscriptions": [declared("a" * 63 + "b"), declared("a" * 63 + "c")]}
    )
    result = _provision_sync(svc.pk)
    assert result["ok"], result
    svc.backend_ref = result["handle"]
    svc.name = "renamed"
    svc.save(update_fields=["backend_ref", "name"])
    svc.registered_app.slug = "renamed-app"
    svc.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, topic_name_prefix="changed-prefix")
    before = copy.deepcopy(cloud.api.rows)
    cloud.api.calls.clear()
    result = _provision_sync(svc.pk)
    assert result["ok"] and result["handle"] == svc.backend_ref
    assert cloud.api.rows == before and _writes(cloud) == []
    assert (
        build_provision_spec(svc, cluster=svc.app_environment.tenant_cluster).recorded_handle
        == svc.backend_ref
    )
    assert len([key for key in cloud.api.rows if "/eventSubscriptions/" in key]) == 2


@pytest.mark.parametrize("part", ["topic", "child"])
@pytest.mark.parametrize(
    "damage", ["foreign", "unknown", "arm", "duplicate", "missing-source", "platform-alias"]
)
def test_production_owner_and_recorded_identity_fail_closed_before_mutation_or_binding(cloud, part, damage):
    svc = _owned(cloud, "replaced-grid-owner")
    key = next(k for k in cloud.api.rows if ("/eventSubscriptions/" in k) == (part == "child"))
    row = cloud.api.rows[key]
    if damage == "arm":
        cloud.api.replaced_ids[key] = key.replace("controlled-rg", "foreign-rg")
    elif part == "topic":
        if damage == "foreign":
            row["tags"]["astrolift-managed-service-id"] = "018f42f0-4420-7000-8000-000000000099"
        elif damage == "duplicate":
            row["tags"]["astrolift_managed_service_id"] = "018f42f0-4420-7000-8000-000000000099"
        elif damage == "missing-source":
            del row["tags"]["astrolift-managed-service-id"]
        elif damage == "platform-alias":
            row["tags"]["astrolift_managed_by"] = "foreign"
        else:
            row["tags"] = {}
    elif damage == "foreign":
        row["properties"]["labels"][1] = "astrolift-owner-foreign"
    elif damage == "duplicate":
        row["properties"]["labels"].append(row["properties"]["labels"][1])
    elif damage == "missing-source":
        row["properties"]["labels"].pop(1)
    elif damage == "platform-alias":
        row["properties"]["labels"].append("astrolift-managed-by-foreign")
    else:
        row["properties"]["labels"] = []
    before = copy.deepcopy(cloud.api.rows)
    assert not _provision_sync(svc.pk)["ok"]
    assert not _update_sync(svc.pk)["ok"]
    assert _check_ready_sync(svc.pk, svc.backend_ref) == "error"
    with pytest.raises(AzureEventGridError):
        _managed_binding_for(svc)
    for force in (False, True):
        result = _deprovision_sync(svc.pk, True, force)
        assert not result["ok"] and result["errors"][0] in {"ownership_unknown", "ownership_refused"}
    assert _writes(cloud) == [] and cloud.api.rows == before
    svc.refresh_from_db()
    assert svc.provider_cleanup_receipt is None


@pytest.mark.parametrize("context", ["short", "512-invalid", "group", "subscription", "receipt"])
def test_recorded_placement_and_storage_boundaries_refuse_without_sdk(cloud, context):
    svc = _owned(cloud, "grid-placement-proof")
    if context == "short":
        svc.backend_ref = "event_bus/legacy-topic"
    elif context == "512-invalid":
        svc.backend_ref = "event_bus/" + ("x" * 502)
        assert len(svc.backend_ref) == 512
    elif context == "group":
        cloud.cfg = dataclasses.replace(cloud.cfg, resource_group="different-rg")
    elif context == "subscription":
        cloud.cfg = dataclasses.replace(cloud.cfg, subscription_id="018f42f0-4420-7000-8000-000000000099")
    else:
        svc.provider_placement_identity = {
            "version": 1,
            "service_id": str(svc.guid),
            "provider_id": "foreign",
        }
    svc.save(update_fields=["backend_ref", "provider_placement_identity"])
    svc.refresh_from_db()
    original = svc.backend_ref
    before = copy.deepcopy(cloud.api.rows)
    if context == "receipt":
        for function, args in (
            (_provision_sync, (svc.pk,)),
            (_update_sync, (svc.pk,)),
            (_deprovision_sync, (svc.pk, True, True)),
            (_managed_binding_for, (svc,)),
        ):
            with pytest.raises(ValueError, match="placement"):
                function(*args)
    else:
        assert not _provision_sync(svc.pk)["ok"]
        assert not _update_sync(svc.pk)["ok"]
        result = _deprovision_sync(svc.pk, True, True)
        assert not result["ok"] and result["errors"][0] in {"ownership_unknown", "ownership_refused"}
        with pytest.raises(AzureEventGridError):
            _managed_binding_for(svc)
    assert cloud.api.calls == [] and cloud.api.rows == before
    svc.refresh_from_db()
    assert svc.backend_ref == original


@pytest.mark.parametrize("category", ["topic", "child", "children", "locks"])
@pytest.mark.parametrize("status", [403, 500])
def test_denied_http_never_becomes_central_already_gone_cleanup(cloud, category, status):
    svc = _owned(cloud, "grid-denied-observation")
    cloud.api.failures["GET", category] = (status, "ForbiddenResourceNotFound")
    result = _deprovision_sync(svc.pk, True, True)
    assert not result["ok"] and result["errors"] == ["ownership_unknown"]
    assert _writes(cloud) == []
    svc.refresh_from_db()
    assert svc.provider_cleanup_receipt is None


@pytest.mark.parametrize("scope", ["subscription", "group", "topic", "child"])
def test_app_lifecycle_force_cannot_remove_inherited_or_operator_locks(cloud, scope):
    svc = _owned(cloud, "grid-locked-target")
    parent = next(k for k in cloud.api.rows if "/eventSubscriptions/" not in k)
    lock_scope = (
        parent.partition("/resourceGroups/")[0]
        if scope == "subscription"
        else parent.partition("/providers/Microsoft.EventGrid/")[0]
        if scope == "group"
        else next(k for k in cloud.api.rows if "/eventSubscriptions/" in k)
        if scope == "child"
        else parent
    )
    cloud.api.locks = [
        {
            "id": lock_scope + "/providers/Microsoft.Authorization/locks/operator-owned",
            "name": "operator-owned",
            "properties": {"level": "ReadOnly"},
        }
    ]
    before = copy.deepcopy(cloud.api.rows)
    assert not _update_sync(svc.pk)["ok"]
    result = _deprovision_sync(svc.pk, True, True)
    assert not result["ok"] and result["errors"] == ["resource_lock_present"]
    assert _writes(cloud) == [] and cloud.api.rows == before


@pytest.mark.parametrize("force", [False, True])
def test_retention_is_not_a_fake_snapshot_or_message_drain(cloud, force):
    svc = _owned(cloud, "grid-retained-events")
    driver = AzureEventGridDriver(config=cloud.cfg)
    result = driver.deprovision(
        DeprovisionSpec(svc.backend_ref, managed_service_id=str(svc.guid)), force_destroy=force
    )
    assert not result.ok and result.errors == ["delete_data_required"]
    with pytest.raises(AzureEventGridError, match="no snapshot"):
        _deprovision_sync(svc.pk, False, force)
    assert _writes(cloud) == [] and len(cloud.api.rows) == 2


def test_pending_create_and_delete_keep_actual_applied_and_cleanup_state_truthful(cloud):
    svc = _bus("grid-pending-target")
    cloud.api.pending_child = True
    result = _provision_sync(svc.pk)
    assert not result["ok"] and not result["ready"] and result["errors"] == ["provision_pending"]
    svc.refresh_from_db()
    assert not svc.backend_ref
    cloud.api.pending_child = False
    for row in cloud.api.rows.values():
        row["properties"]["provisioningState"] = "Succeeded"
    result = _provision_sync(svc.pk)
    assert result["ok"]
    svc.backend_ref = result["handle"]
    svc.save(update_fields=["backend_ref"])
    cloud.api.pending_delete = True
    result = _deprovision_sync(svc.pk, True, True)
    assert not result["ok"] and result["errors"] == ["delete_pending"]
    svc.refresh_from_db()
    assert svc.provider_cleanup_receipt is None
    cloud.api.pending_delete = False
    assert _deprovision_sync(svc.pk, True, False)["ok"]
    cloud.api.calls.clear()
    assert _deprovision_sync(svc.pk, True, True)["ok"] and _writes(cloud) == []


def test_whole_desired_config_noop_and_supported_update_have_actual_sdk_observation(cloud):
    svc = _owned(cloud, "grid-full-desired-config")
    svc.config = {
        **svc.config,
        "topic_name": svc.backend_ref.rsplit("/", 1)[1],
        "input_schema": "CloudEventSchemaV1_0",
        "access_mode": "manage",
    }
    svc.save(update_fields=["config"])
    assert _update_sync(svc.pk)["ok"] and _writes(cloud) == []
    svc.config = {**svc.config, "minimum_tls_version_allowed": "1.1"}
    svc.save(update_fields=["config"])
    assert _update_sync(svc.pk)["ok"]
    assert _writes(cloud) == ["PATCH"]
    cloud.api.calls.clear()
    svc.config = {**svc.config, "input_schema": "EventGridSchema"}
    svc.save(update_fields=["config"])
    assert not _update_sync(svc.pk)["ok"] and _writes(cloud) == []


@pytest.mark.parametrize("failure", ["unknown-child", "pagination"])
def test_complete_inventory_unknowns_precede_every_parent_effect(cloud, failure):
    svc = _owned(cloud, "grid-inventory-proof")
    child = next(v for k, v in cloud.api.rows.items() if "/eventSubscriptions/" in k)
    if failure == "unknown-child":
        child["properties"]["labels"] = []
    else:
        collection = child["id"].rsplit("/", 1)[0]
        cloud.api.pages["children"] = [
            {
                "value": [child],
                "nextLink": f"https://management.azure.com{collection}?api-version=2025-02-15&$skip=1",
            },
            (403, "ForbiddenResourceNotFound"),
        ]
    assert not _update_sync(svc.pk)["ok"]
    assert not _deprovision_sync(svc.pk, True, True)["ok"]
    assert _writes(cloud) == []
