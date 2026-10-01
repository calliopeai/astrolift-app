"""Real PostgreSQL lifecycle through installed providers and actual SDK12 transport."""

from __future__ import annotations

import copy
import dataclasses
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from azure.managed.event_hubs import AzureEventHubsDriver

from astrolift_workflows.activities.managed_service_lifecycle import (
    _check_ready_sync,
    _deprovision_sync,
    _finalize_provision_sync,
    _managed_binding_for,
    _provision_sync,
    _update_sync,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service
from providers.tests.azure.test_event_hubs_wire_2032 import RecordingEventHubs, recording_config

pytestmark = pytest.mark.django_db


@pytest.fixture(params=[("stream", "event_hubs"), ("event_stream", "event_hubs_kafka")])
def cloud(request):
    kind, variant = request.param
    api = RecordingEventHubs()
    cfg = recording_config(api, variant=variant)
    state = SimpleNamespace(api=api, cfg=cfg, kind=kind, variant=variant)
    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=AzureEventHubsDriver),
        patch("core.cluster_observability.managed_config_for", side_effect=lambda *_, **__: state.cfg),
    ):
        yield state
    cfg.mgmt_client.close()
    cfg.locks_client.close()


def service(cloud, slug, app_slug="api"):
    row = _service(org_slug=slug, plugin_slug="azure", variant=cloud.variant, backend_ref="")
    row.kind, row.name = cloud.kind, "events"
    row.save(update_fields=["kind", "name"])
    row.registered_app.slug = app_slug
    row.registered_app.save(update_fields=["slug"])
    return row


def owned(cloud):
    row = service(cloud, "event-owned")
    result = _provision_sync(row.pk)
    assert result["ok"] and result["ready"], result
    _finalize_provision_sync(row.pk, result["handle"])
    row.refresh_from_db()
    target = AzureEventHubsDriver(config=cloud.cfg)._saved_target(
        row.backend_ref, SimpleNamespace(managed_service_id=str(row.guid))
    )
    cloud.api.calls.clear()
    return row, target


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_production_two_org_collisions_use_full_guid_and_materialize_actual_binding(cloud, collision):
    if collision == "joined":
        rows = [service(cloud, "alpha-beta", "gamma"), service(cloud, "alpha", "beta-gamma")]
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, namespace_name_prefix="p" * 300)
        rows = [service(cloud, "long-alpha"), service(cloud, "long-beta")]
    prior = [
        "-".join(
            (
                cloud.cfg.namespace_name_prefix,
                row.registered_app.organization.slug,
                row.registered_app.slug,
                row.app_environment.name,
                row.name,
            )
        )[:39]
        for row in rows
    ]
    assert prior[0] == prior[1]
    results = [_provision_sync(row.pk) for row in rows]
    assert all(r["ok"] and r["ready"] for r in results), results
    assert results[0]["handle"] != results[1]["handle"]
    for row, result in zip(rows, results, strict=True):
        _finalize_provision_sync(row.pk, result["handle"])
        row.refresh_from_db()
        binding = _managed_binding_for(row)
        for key in ("EVENTHUB_NAMESPACE", "EVENTHUB_NAME"):
            assert row.guid.hex in binding.env_vars[key].literal
        if cloud.kind == "stream":
            assert row.guid.hex in binding.env_vars["EVENTHUB_CONSUMER_GROUP"].literal
        assert _check_ready_sync(row.pk, row.backend_ref) == "available"
        assert _provision_sync(row.pk)["handle"] == row.backend_ref


@pytest.mark.parametrize("entity", ["namespace", "hub", "group"])
@pytest.mark.parametrize("replacement", ["foreign", "unlabelled", "wrong-arm", "missing-arm"])
def test_current_owner_and_exact_arm_refuse_every_production_effect_binding_and_cleanup(
    cloud, entity, replacement
):
    row, target = owned(cloud)
    if entity == "group" and target.group == "-":
        path = target.group_id("custom")
        cloud.api.rows[path] = {
            "id": path,
            "name": "custom",
            "properties": {"userMetadata": cloud.api.rows[target.hub_id]["properties"]["userMetadata"]},
        }
    else:
        path = (
            target.namespace_id
            if entity == "namespace"
            else target.hub_id
            if entity == "hub"
            else target.group_id(target.group)
        )
    if replacement in {"foreign", "unlabelled"}:
        if entity == "namespace":
            cloud.api.rows[path]["tags"] = (
                {}
                if replacement == "unlabelled"
                else {
                    **cloud.api.rows[path]["tags"],
                    "astrolift-managed-service-id": "018f42f0-4420-7000-8000-000000000099",
                }
            )
        else:
            blob = cloud.api.rows[path]["properties"]["userMetadata"]
            cloud.api.rows[path]["properties"]["userMetadata"] = (
                ""
                if replacement == "unlabelled"
                else blob.replace(str(row.guid), "018f42f0-4420-7000-8000-000000000099")
            )
    else:
        cloud.api.replaced_ids[path] = (
            "" if replacement == "missing-arm" else path.replace("controlled-rg", "foreign-rg")
        )
    row.config = {"capacity": 2}
    row.save(update_fields=["config"])
    before = copy.deepcopy(cloud.api.rows)
    for result in [_provision_sync(row.pk), _update_sync(row.pk), _deprovision_sync(row.pk, True, True)]:
        assert not result["ok"] and set(result["errors"]) & {"ownership_unknown", "ownership_refused"}, result
    assert _check_ready_sync(row.pk, row.backend_ref) == "error"
    with pytest.raises(ValueError):
        _managed_binding_for(row)
    row.refresh_from_db()
    assert row.provider_cleanup_receipt is None and cloud.api.rows == before
    assert all(c[0] == "GET" for c in cloud.api.calls)


@pytest.mark.parametrize("change", ["legacy", "subscription", "resource-group", "receipt", "copied-source"])
def test_saved_target_placement_receipt_and_source_cannot_be_reconstructed_or_reassigned(cloud, change):
    row, target = owned(cloud)
    if change == "legacy":
        row.backend_ref = f"{cloud.kind}/{target.namespace}/{target.hub}"
        row.save(update_fields=["backend_ref"])
    elif change == "subscription":
        cloud.cfg = dataclasses.replace(cloud.cfg, subscription_id="018f42f0-4420-7000-8000-000000000099")
    elif change == "resource-group":
        cloud.cfg = dataclasses.replace(cloud.cfg, resource_group="foreign-rg")
    elif change == "copied-source":
        foreign = service(cloud, "copied-source")
        foreign.backend_ref = row.backend_ref
        foreign.save(update_fields=["backend_ref"])
        row = foreign
    else:
        row.provider_placement_identity = {
            "version": 1,
            "driver": "gcp.managed.graph_spanner.SpannerGraphDriver",
            "project_id": "historical-placement",
        }
        row.save(update_fields=["provider_placement_identity"])
        with pytest.raises(ValueError, match="observed placement"):
            _provision_sync(row.pk)
        with pytest.raises(ValueError, match="observed placement"):
            _managed_binding_for(row)
        assert cloud.api.calls == []
        return
    previous = row.backend_ref
    for result in [_provision_sync(row.pk), _deprovision_sync(row.pk, True, True)]:
        assert not result["ok"] and set(result["errors"]) & {"ownership_unknown", "ownership_refused"}, result
    row.refresh_from_db()
    assert row.backend_ref == previous and row.provider_cleanup_receipt is None
    assert all(c[0] == "GET" for c in cloud.api.calls)
    if change != "copied-source":
        assert not cloud.api.calls


def test_whole_config_and_exact_names_survive_renamed_source_and_install_hints(cloud):
    row, target = owned(cloud)
    row.registered_app.slug = "renamed-app"
    row.registered_app.save(update_fields=["slug"])
    row.name = "renamed-service"
    row.config = {
        "sku": "Standard",
        "kafka_enabled": cloud.kind == "event_stream",
        "zone_redundant": False,
        "namespace_name": target.namespace,
        "event_hub_name": target.hub,
        "cleanup_policy": "Delete",
        "capacity": 2,
    }
    row.save(update_fields=["name", "config"])
    cloud.cfg = dataclasses.replace(
        cloud.cfg,
        namespace_name_prefix="new-prefix",
        event_hub_name_prefix="new-prefix",
        default_consumer_group="new-prefix",
    )
    assert _provision_sync(row.pk)["handle"] == row.backend_ref
    assert _update_sync(row.pk)["ok"]
    assert _managed_binding_for(row).env_vars["EVENTHUB_RESOURCE_ID"].literal == target.hub_id
    assert all(c[0] != "DELETE" for c in cloud.api.calls)


@pytest.mark.parametrize("denied", ["namespace", "hub", "group", "hubs", "groups", "locks"])
def test_denied_notfound_diagnostic_never_records_cleanup_success(cloud, denied):
    row, _ = owned(cloud)
    cloud.api.failures["GET", denied] = 403, "ForbiddenResourceNotFound"
    result = _deprovision_sync(row.pk, True, True)
    assert not result["ok"] and result["errors"] == ["ownership_unknown"], result
    row.refresh_from_db()
    assert row.provider_cleanup_receipt is None and all(c[0] == "GET" for c in cloud.api.calls)


def test_retention_and_lock_refusal_then_typed_absence_cleanup_converges(cloud):
    row, target = owned(cloud)
    with pytest.raises(ValueError, match="no exact stream snapshot"):
        _deprovision_sync(row.pk, False, True)
    assert not cloud.api.calls
    cloud.api.locks = [
        {
            "id": target.namespace_id + "/providers/Microsoft.Authorization/locks/operator",
            "name": "operator",
            "properties": {"level": "CanNotDelete"},
        }
    ]
    result = _deprovision_sync(row.pk, True, True)
    assert not result["ok"] and result["errors"] == ["resource_lock_present"]
    assert all(c[0] == "GET" for c in cloud.api.calls)
    cloud.api.locks.clear()
    assert _deprovision_sync(row.pk, True, True)["ok"]
    assert not cloud.api.rows
    cloud.api.calls.clear()
    assert _deprovision_sync(row.pk, True, True)["ok"]
    assert all(c[0] == "GET" for c in cloud.api.calls)


def test_partial_namespace_is_incomplete_and_same_uuid_retry_finishes(cloud):
    row = service(cloud, "pending")
    cloud.api.pending_create = True
    result = _provision_sync(row.pk)
    assert not result["ok"] and not result["ready"] and result["errors"] == ["provision_pending"]
    row.refresh_from_db()
    assert row.backend_ref == "" and row.applied_config is None
    target = AzureEventHubsDriver(config=cloud.cfg)._saved_target(
        result["handle"], SimpleNamespace(managed_service_id=str(row.guid))
    )
    assert set(cloud.api.rows) == {target.namespace_id}
    assert not any("background" in c[1] for c in cloud.api.calls)
    cloud.api.rows[target.namespace_id]["properties"]["provisioningState"] = "Succeeded"
    cloud.api.pending_create = False
    completed = _provision_sync(row.pk)
    assert completed["ok"] and completed["ready"] and completed["handle"] == result["handle"]
    _finalize_provision_sync(row.pk, completed["handle"])
    assert _check_ready_sync(row.pk, completed["handle"]) == "available"


def test_exact_512_arm_binding_stores_without_truncation_and_overflow_refuses_before_sdk(cloud):
    row, old = owned(cloud)
    original = cloud.cfg
    cloud.cfg = dataclasses.replace(cloud.cfg, resource_group="r" * 90)
    driver = AzureEventHubsDriver(config=cloud.cfg)
    group = "-" if cloud.kind == "event_stream" else "g" * 50
    stub = driver._coordinates("n" + "s" * 49, "h", group)
    target = driver._coordinates(stub.namespace, "h" * (512 - len(stub.hub_id) + 1), group)
    for old_id, new_id in [
        (old.namespace_id, target.namespace_id),
        (old.hub_id, target.hub_id),
        (old.group_id("$Default"), target.group_id("$Default")),
    ] + ([] if group == "-" else [(old.group_id(old.group), target.group_id(group))]):
        obj = cloud.api.rows.pop(old_id)
        obj["id"], obj["name"] = new_id, new_id.rsplit("/", 1)[-1]
        cloud.api.rows[new_id] = obj
    row.backend_ref = target.handle(cloud.kind)
    row.save(update_fields=["backend_ref"])
    assert len(target.hub_id) == 512
    _finalize_provision_sync(row.pk, row.backend_ref)
    from astrolift_services.models import ManagedServiceBinding

    saved = ManagedServiceBinding.objects.get(managed_service=row, env_key="EVENTHUB_RESOURCE_ID")
    assert saved.env_value_ref == target.hub_id
    cloud.api.calls.clear()
    row.backend_ref = row.backend_ref.replace(target.hub + "/", target.hub + "h/")
    row.save(update_fields=["backend_ref"])
    result = _provision_sync(row.pk)
    assert not result["ok"] and result["errors"] == ["ownership_unknown"] and not cloud.api.calls
    cloud.cfg = original


def test_partial_child_inventory_denial_cannot_mutate_parent_or_record_cleanup(cloud):
    row, target = owned(cloud)
    collection = target.namespace_id + "/eventhubs"
    cloud.api.pages["hubs"] = [
        {
            "value": [copy.deepcopy(cloud.api.rows[target.hub_id])],
            "nextLink": "https://management.azure.com" + collection + "?api-version=2024-01-01&$skip=1",
        },
        (403, "ForbiddenResourceNotFound"),
    ]
    row.config = {"capacity": 2}
    row.save(update_fields=["config"])
    before = copy.deepcopy(cloud.api.rows)
    for result in [_provision_sync(row.pk), _update_sync(row.pk), _deprovision_sync(row.pk, True, True)]:
        assert not result["ok"] and result["errors"] == ["ownership_unknown"]
    row.refresh_from_db()
    assert (
        row.provider_cleanup_receipt is None
        and cloud.api.rows == before
        and all(c[0] == "GET" for c in cloud.api.calls)
    )


def test_actual_pg_documented_namespace_shape_and_explicit_refusal(cloud):
    row = service(cloud, "documented-ns")
    cloud.api.omit_namespace_status = True
    result = _provision_sync(row.pk)
    assert result["ok"] and result["ready"], result
    _finalize_provision_sync(row.pk, result["handle"])
    row.refresh_from_db()
    target = AzureEventHubsDriver(config=cloud.cfg)._saved_target(
        row.backend_ref, SimpleNamespace(managed_service_id=str(row.guid))
    )
    assert "status" not in cloud.api.rows[target.namespace_id]["properties"]
    assert _check_ready_sync(row.pk, row.backend_ref) == "available"
    assert _managed_binding_for(row).env_vars["EVENTHUB_RESOURCE_ID"].literal == target.hub_id
    cloud.api.rows[target.namespace_id]["properties"]["status"] = "Disabled"
    assert _check_ready_sync(row.pk, row.backend_ref) == "error"
    with pytest.raises(ValueError, match="active"):
        _managed_binding_for(row)
