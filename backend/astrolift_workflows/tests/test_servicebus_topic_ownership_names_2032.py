"""Real PostgreSQL lifecycle uses installed Azure SDK topic/subscription HTTP seam."""

from __future__ import annotations

import copy
import dataclasses
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from _sdk.azure_ownership import AzureOwnershipError
from _sdk.managed_service import UPDATE_NOT_SUPPORTED_IN_PLACE
from azure.managed.queue_servicebus import AzureServiceBusDriver, AzureServiceBusError

from astrolift_services.models import ManagedServiceBinding
from astrolift_workflows.activities.managed_service_lifecycle import (
    _check_ready_sync,
    _deprovision_sync,
    _finalize_provision_sync,
    _finalize_update_sync,
    _managed_binding_for,
    _provision_sync,
    _update_sync,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service
from providers.tests.azure.test_servicebus_topic_wire_2032 import RecordingTopics, recording_config

pytestmark = pytest.mark.django_db


@pytest.fixture(params=[("queue", "azure_servicebus"), ("topic", "service_bus_topic")])
def cloud(request):
    kind, variant = request.param
    api = RecordingTopics()
    cfg = recording_config(api, kind=kind)
    state = SimpleNamespace(api=api, cfg=cfg, kind=kind, variant=variant)
    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=AzureServiceBusDriver),
        patch("core.cluster_observability.managed_config_for", side_effect=lambda *_, **__: state.cfg),
    ):
        yield state
    cfg.client.close()
    assert api.closed


def service(cloud, org_slug, app_slug="api"):
    row = _service(org_slug=org_slug, plugin_slug="azure", variant=cloud.variant, backend_ref="")
    row.kind = cloud.kind
    row.name = "events"
    row.save(update_fields=["kind", "name"])
    row.registered_app.slug = app_slug
    row.registered_app.save(update_fields=["slug"])
    return row


def owned(cloud, slug="topic-owned"):
    row = service(cloud, slug)
    result = _provision_sync(row.pk)
    assert result["ok"], result
    _finalize_provision_sync(row.pk, result["handle"])
    row.refresh_from_db()
    driver = AzureServiceBusDriver(config=cloud.cfg)
    target = driver._saved_target(row.backend_ref, SimpleNamespace(managed_service_id=str(row.guid)))
    cloud.api.calls.clear()
    return row, target


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_two_org_slug_collisions_create_distinct_full_guid_parent_and_child_using_actual_sdk(
    cloud, collision
):
    if collision == "joined":
        rows = [service(cloud, "alpha-beta", "gamma"), service(cloud, "alpha", "beta-gamma")]
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, topic_name_prefix="p" * 300)
        rows = [service(cloud, "long-alpha"), service(cloud, "long-beta")]
    previous = [
        "-".join(
            (
                cloud.cfg.topic_name_prefix,
                row.registered_app.organization.slug,
                row.registered_app.slug,
                row.app_environment.name,
                row.name,
            )
        )[:260]
        for row in rows
    ]
    assert previous[0] == previous[1]
    results = [_provision_sync(row.pk) for row in rows]
    assert all(result["ok"] for result in results), results
    assert results[0]["handle"] != results[1]["handle"]
    assert len(cloud.api.rows) == 4
    for row, result in zip(rows, results, strict=True):
        _finalize_provision_sync(row.pk, result["handle"])
        row.refresh_from_db()
        assert (
            row.backend_ref == result["handle"]
            and len(row.backend_ref) <= row._meta.get_field("backend_ref").max_length
        )
        binding = _managed_binding_for(row)
        topic = binding.env_vars["SERVICEBUS_TOPIC"].literal
        child = binding.env_vars["SERVICEBUS_SUBSCRIPTION"].literal
        assert topic.endswith(row.guid.hex) and len(topic) <= 42
        assert child == topic + "-default" and len(child) <= 50
        assert _check_ready_sync(row.pk, row.backend_ref) == "available"
        assert _provision_sync(row.pk)["handle"] == row.backend_ref
        assert ManagedServiceBinding.objects.filter(managed_service=row).count() >= 4


def test_recorded_exact_child_and_long_topic_survive_rename_and_materialize_real_bindings(cloud):
    row, target = owned(cloud)
    driver = AzureServiceBusDriver(config=cloud.cfg)
    saved = driver._coordinates("L" * 260, "ExplicitChild" + "c" * 37)
    for old_id, new_id in [(target.topic_id, saved.topic_id), (target.child_id, saved.child_id)]:
        entity = cloud.api.rows.pop(old_id)
        entity["id"] = new_id
        cloud.api.rows[new_id] = entity
    cloud.api.payloads[saved.child_id] = [b"controlled retained payload"]
    row.backend_ref = saved.handle(cloud.kind)
    row.name = "renamed-service"
    row.save(update_fields=["backend_ref", "name"])
    cloud.cfg = dataclasses.replace(cloud.cfg, topic_name_prefix="new-prefix")
    result = _provision_sync(row.pk)
    assert result["ok"] and result["handle"] == row.backend_ref
    _finalize_provision_sync(row.pk, result["handle"])
    assert _managed_binding_for(row).env_vars["SERVICEBUS_SUBSCRIPTION"].literal == saved.child
    assert cloud.api.payloads[saved.child_id] == [b"controlled retained payload"]
    assert set(cloud.api.rows) == {saved.topic_id, saved.child_id}
    assert all(verb != "DELETE" for verb, *_ in cloud.api.calls)


@pytest.mark.parametrize("entity", ["topic", "child"])
@pytest.mark.parametrize("replacement", ["foreign", "unlabelled", "different-arm", "missing-arm"])
def test_replaced_or_unproven_pair_refuses_all_production_effects_binding_and_cleanup(
    cloud, entity, replacement
):
    row, target = owned(cloud)
    path = target.topic_id if entity == "topic" else target.child_id
    if replacement == "foreign":
        properties = cloud.api.rows[path]["properties"]
        properties["userMetadata"] = properties["userMetadata"].replace(
            str(row.guid), "018f42f0-4420-7000-8000-000000000099"
        )
    elif replacement == "unlabelled":
        cloud.api.rows[path]["properties"]["userMetadata"] = ""
    elif replacement == "different-arm":
        cloud.api.replaced_ids[path] = path.replace("controlled-rg", "foreign-rg")
    else:
        cloud.api.replaced_ids[path] = ""
    row.config = {"max_size_in_megabytes": 2048}
    row.save(update_fields=["config"])
    before = copy.deepcopy(cloud.api.rows)
    for result in [_provision_sync(row.pk), _update_sync(row.pk), _deprovision_sync(row.pk, True, True)]:
        assert not result["ok"] and set(result["errors"]) & {"ownership_unknown", "ownership_refused"}, result
    assert _check_ready_sync(row.pk, row.backend_ref) == "error"
    with pytest.raises((RuntimeError, AzureOwnershipError)):
        _managed_binding_for(row)
    assert cloud.api.rows == before and all(verb == "GET" for verb, *_ in cloud.api.calls)
    row.refresh_from_db()
    assert row.provider_cleanup_receipt is None


@pytest.mark.parametrize("field", ["subscription_id", "resource_group", "namespace_name"])
def test_reassigned_provider_coordinates_refuse_prior_saved_targets_before_sdk(cloud, field):
    row, _ = owned(cloud)
    changes = {
        "subscription_id": "018f42f0-4420-7000-8000-000000000099",
        "resource_group": "foreign-rg",
        "namespace_name": "foreign-sb",
    }
    cloud.cfg = dataclasses.replace(cloud.cfg, **{field: changes[field]})
    result = _deprovision_sync(row.pk, True, True)
    assert not result["ok"] and "ownership_refused" in result["errors"]
    assert not cloud.api.calls


def test_historical_short_handle_stays_stored_without_legacy_adoption_or_guessed_child(cloud):
    row, target = owned(cloud)
    row.backend_ref = cloud.kind + "/" + target.topic
    row.config = {"max_size_in_megabytes": 2048}
    row.save(update_fields=["backend_ref", "config"])
    previous = row.backend_ref
    for result in [_provision_sync(row.pk), _update_sync(row.pk), _deprovision_sync(row.pk, True, True)]:
        assert not result["ok"] and result["errors"] == ["ownership_unknown"]
    row.refresh_from_db()
    assert row.backend_ref == previous and len(cloud.api.rows) == 2 and not cloud.api.calls
    assert row.provider_cleanup_receipt is None


def test_existing_server_owned_placement_identity_is_not_bypassed_by_azure_dispatch(cloud):
    row, _ = owned(cloud)
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


@pytest.mark.parametrize("kind", ["topic", "child", "inventory"])
def test_access_denial_resource_not_found_diagnostic_never_becomes_cleanup_success(cloud, kind):
    row, _ = owned(cloud)
    cloud.api.failures[("GET", kind)] = 403, "ForbiddenResourceNotFound"
    result = _deprovision_sync(row.pk, True, True)
    assert not result["ok"] and result["errors"] == ["ownership_unknown"]
    assert len(cloud.api.rows) == 2 and all(verb == "GET" for verb, *_ in cloud.api.calls)
    row.refresh_from_db()
    assert row.provider_cleanup_receipt is None


def test_other_attached_child_refuses_production_parent_effects(cloud):
    row, target = owned(cloud)
    foreign = target.topic_id + "/subscriptions/foreign"
    cloud.api.rows[foreign] = copy.deepcopy(cloud.api.rows[target.child_id])
    cloud.api.rows[foreign]["id"] = foreign
    row.config = {"max_size_in_megabytes": 2048}
    row.save(update_fields=["config"])
    for result in [_provision_sync(row.pk), _update_sync(row.pk), _deprovision_sync(row.pk, True, True)]:
        assert not result["ok"] and "ownership_refused" in result["errors"]
    assert len(cloud.api.rows) == 3 and all(verb == "GET" for verb, *_ in cloud.api.calls)


def test_supported_update_observation_then_finalize_keeps_exact_opaque_handle(cloud):
    row, target = owned(cloud)
    row.config = {"max_size_in_megabytes": 2048, "default_message_ttl": "P7D"}
    row.save(update_fields=["config"])
    result = _update_sync(row.pk)
    assert result["ok"] and result["handle"] == row.backend_ref
    _finalize_update_sync(row.pk, result["handle"])
    row.refresh_from_db()
    assert row.applied_config == row.config
    assert cloud.api.rows[target.topic_id]["properties"]["maxSizeInMegabytes"] == 2048
    assert _managed_binding_for(row).iam_grants[1].resource == target.child_id


def test_permanently_unsupported_update_is_sdk_free_through_production_dispatch(cloud):
    row, _ = owned(cloud)
    row.config = {"max_delivery_count": 3}
    row.backend_ref = "probe/handle"
    row.save(update_fields=["config", "backend_ref"])
    result = _update_sync(row.pk)
    assert (
        not result["ok"] and not result["retryable"] and result["errors"] == [UPDATE_NOT_SUPPORTED_IN_PLACE]
    )
    assert cloud.api.calls == []


@pytest.mark.parametrize("force", [False, True])
def test_no_retained_snapshot_drain_or_cleanup_then_genuine_delete_converges(cloud, force):
    row, _ = owned(cloud)
    before = copy.deepcopy(cloud.api.rows)
    with pytest.raises(AzureServiceBusError, match="snapshot"):
        _deprovision_sync(row.pk, False, force)
    assert cloud.api.calls == [] and cloud.api.rows == before
    assert _deprovision_sync(row.pk, True, force)["ok"]
    assert not cloud.api.rows
    cloud.api.calls.clear()
    assert _deprovision_sync(row.pk, True, True)["ok"]
    assert all(verb == "GET" for verb, *_ in cloud.api.calls)


def test_cross_org_copied_complete_handle_is_not_owner_or_exclusivity_proof(cloud):
    owner, _ = owned(cloud, "actual-owner")
    foreign = service(cloud, "foreign-claim")
    foreign.backend_ref = owner.backend_ref
    foreign.config = {"max_size_in_megabytes": 2048}
    foreign.save(update_fields=["backend_ref", "config"])
    before = copy.deepcopy(cloud.api.rows)
    for result in [
        _provision_sync(foreign.pk),
        _update_sync(foreign.pk),
        _deprovision_sync(foreign.pk, True, True),
    ]:
        assert not result["ok"] and "ownership_refused" in result["errors"]
    with pytest.raises(AzureOwnershipError):
        _managed_binding_for(foreign)
    assert cloud.api.rows == before and all(verb == "GET" for verb, *_ in cloud.api.calls)
    assert _managed_binding_for(owner).iam_grants[0].actions == ["Azure Service Bus Data Sender"]


def test_current_org_label_injection_cannot_forge_parent_or_child_owner(cloud):
    row = service(cloud, "hostile-owner-metadata")
    org = row.registered_app.organization
    org.default_resource_tags = {"note": ";astrolift-managed-service-id=foreign"}
    org.save(update_fields=["default_resource_tags"])
    outcome = _provision_sync(row.pk)
    assert not outcome["ok"] and "ownership_refused" in outcome["errors"]
    assert not cloud.api.calls and not cloud.api.rows


def test_existing_full_desired_snapshot_accepted_only_when_noneditable_values_match_actual_sdk(cloud):
    row, target = owned(cloud)
    cfg = {
        "max_size_in_megabytes": 2048,
        "default_message_ttl": "P14D",
        "enable_partitioning": False,
        "lock_duration": "PT30S",
        "max_delivery_count": 10,
        "dead_lettering_on_message_expiration": True,
    }
    row.config = cfg
    row.save(update_fields=["config"])
    assert _update_sync(row.pk)["ok"]
    assert cloud.api.rows[target.topic_id]["properties"]["maxSizeInMegabytes"] == 2048
    cloud.api.calls.clear()
    row.config = {**cfg, "max_delivery_count": 3}
    row.save(update_fields=["config"])
    result = _update_sync(row.pk)
    assert (
        not result["ok"] and not result["retryable"] and result["errors"] == [UPDATE_NOT_SUPPORTED_IN_PLACE]
    )
    assert all(verb == "GET" for verb, *_ in cloud.api.calls)


def test_topic_arm_literal_storage_limit_is_checked_before_any_production_sdk_call(cloud):
    if cloud.kind != "topic":
        pytest.skip("Only topic alias materializes TOPIC_ARN_OR_ID")
    row = service(cloud, "oversized-topic-binding")
    cloud.cfg = dataclasses.replace(cloud.cfg, resource_group="r" * 90, namespace_name="n" * 50)
    row.backend_ref = "/".join(
        (
            "topic",
            "arm-v1",
            cloud.cfg.subscription_id,
            cloud.cfg.resource_group,
            cloud.cfg.namespace_name,
            "t" * 260,
            "c" * 50,
        )
    )
    assert len(row.backend_ref) <= row._meta.get_field("backend_ref").max_length
    row.save(update_fields=["backend_ref"])
    result = _provision_sync(row.pk)
    assert not result["ok"] and "ownership_refused" in result["errors"]
    with pytest.raises(AzureOwnershipError, match="binding storage limit"):
        _managed_binding_for(row)
    assert cloud.api.calls == [] and not cloud.api.rows


def test_maximum_representable_topic_arm_literal_persists_without_truncation(cloud):
    if cloud.kind != "topic":
        pytest.skip("Only topic alias materializes TOPIC_ARN_OR_ID")
    row, original = owned(cloud, "exact-topic-binding-limit")
    cloud.cfg = dataclasses.replace(cloud.cfg, resource_group="r" * 85, namespace_name="n" * 50)
    driver = AzureServiceBusDriver(config=cloud.cfg)
    exact = driver._coordinates("t" * 260, "c" * 50)
    assert len(exact.topic_id) == 512 and len(exact.handle("topic")) <= 512
    for old, current in [(original.topic_id, exact.topic_id), (original.child_id, exact.child_id)]:
        entity = cloud.api.rows.pop(old)
        entity["id"] = current
        cloud.api.rows[current] = entity
    row.backend_ref = exact.handle("topic")
    row.save(update_fields=["backend_ref"])
    result = _provision_sync(row.pk)
    assert result["ok"] and result["handle"] == row.backend_ref
    _finalize_provision_sync(row.pk, result["handle"])
    binding = ManagedServiceBinding.objects.get(managed_service=row, env_key="TOPIC_ARN_OR_ID")
    assert binding.env_value_ref == exact.topic_id and len(binding.env_value_ref) == 512
    assert _managed_binding_for(row).env_vars["TOPIC_ARN_OR_ID"].literal == binding.env_value_ref
