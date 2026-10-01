"""Real PostgreSQL lifecycle dispatch reaches Azure's declared SDK recording HTTP seam."""

from __future__ import annotations

import copy
import dataclasses
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from _sdk.azure_ownership import AzureOwnershipError
from _sdk.managed_service import UPDATE_NOT_SUPPORTED_IN_PLACE, DeprovisionSpec
from azure.managed.queue_servicebus import ServiceBusDriver

from astrolift_workflows.activities.managed_service_lifecycle import (
    _check_ready_sync,
    _deprovision_sync,
    _managed_binding_for,
    _provision_sync,
    _update_sync,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service
from providers.tests.azure.test_servicebus_queue_wire_2032 import (
    RecordingServiceBus,
    recording_config,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def cloud():
    api = RecordingServiceBus()
    cfg = recording_config(api)
    state = SimpleNamespace(api=api, cfg=cfg)
    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=ServiceBusDriver),
        patch("core.cluster_observability.managed_config_for", side_effect=lambda *_, **__: state.cfg),
    ):
        yield state
    cfg.client.close()
    assert api.closed


def _queue(org_slug, app_slug="api"):
    svc = _service(org_slug=org_slug, plugin_slug="azure", variant="servicebus", backend_ref="")
    svc.kind = "queue"
    svc.name = "tasks"
    svc.save(update_fields=["kind", "name"])
    svc.registered_app.slug = app_slug
    svc.registered_app.save(update_fields=["slug"])
    return svc


def _owned(cloud, slug):
    svc = _queue(slug)
    result = _provision_sync(svc.pk)
    assert result["ok"], result
    svc.backend_ref = result["handle"]
    svc.save(update_fields=["backend_ref"])
    cloud.api.calls.clear()
    return svc


def _writes(cloud):
    return [verb for verb, *_ in cloud.api.calls if verb != "GET"]


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_two_org_slug_collisions_get_distinct_full_persisted_uuid_targets_through_actual_sdk(
    cloud, collision
):
    if collision == "joined":
        rows = [_queue("alpha-beta", "gamma"), _queue("alpha", "beta-gamma")]
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, queue_name_prefix="p" * 300)
        rows = [_queue("truncated-alpha"), _queue("truncated-beta")]
    old = [
        "-".join(
            (
                cloud.cfg.queue_name_prefix,
                row.registered_app.organization.slug,
                row.registered_app.slug,
                row.app_environment.name,
                row.name,
            )
        )[:260]
        for row in rows
    ]
    assert old[0] == old[1]
    results = [_provision_sync(row.pk) for row in rows]
    assert all(result["ok"] for result in results), results
    assert results[0]["handle"] != results[1]["handle"]
    assert len(cloud.api.rows) == 2
    for row, result in zip(rows, results, strict=True):
        physical = result["handle"].partition("/")[2]
        assert physical.endswith(str(row.guid).replace("-", "")) and len(physical) <= 260
        row.backend_ref = result["handle"]
        row.save(update_fields=["backend_ref"])
        target = next(value for key, value in cloud.api.rows.items() if key.endswith("/" + physical))
        assert f"astrolift-managed-service-id={row.guid}" in target["properties"]["userMetadata"]
        cloud.api.payloads[target["id"]] = [b"controlled queue payload"]
        assert _provision_sync(row.pk)["handle"] == result["handle"]
        assert cloud.api.payloads[target["id"]] == [b"controlled queue payload"]
        binding = _managed_binding_for(row)
        assert binding.env_vars["SERVICEBUS_QUEUE"].literal == physical
        assert binding.iam_grants[0].resource == target["id"]
        assert _check_ready_sync(row.pk, row.backend_ref) == "available"


def test_recorded_long_queue_path_survives_rename_prefix_change_and_reprovision(cloud):
    svc = _owned(cloud, "legacy-servicebus-owner")
    previous_path, row = next(iter(cloud.api.rows.items()))
    legacy = "Legacy/" + "q" * 253
    assert len(legacy) == 260
    path = previous_path.partition("/queues/")[0] + "/queues/" + legacy
    del cloud.api.rows[previous_path]
    row["id"] = path
    row["name"] = legacy
    cloud.api.payloads[path] = [b"retained legacy payload"]
    cloud.api.rows[path] = row
    svc.backend_ref = "queue/" + legacy
    svc.name = "changed-name"
    svc.save(update_fields=["backend_ref", "name"])
    svc.registered_app.slug = "changed-app"
    svc.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, queue_name_prefix="changed-prefix")
    result = _provision_sync(svc.pk)
    assert result["ok"] and result["handle"] == svc.backend_ref
    assert list(cloud.api.rows) == [path]
    assert cloud.api.payloads[path] == [b"retained legacy payload"]
    assert _managed_binding_for(svc).env_vars["SERVICEBUS_QUEUE"].literal == legacy
    # The transport models an in-place ARM configuration update, not queue message deletion.
    assert not any(verb == "DELETE" for verb, *_ in cloud.api.calls)


@pytest.mark.parametrize(
    "replacement", ["foreign", "no-owner", "no-platform", "duplicate", "alias", "foreign-arm", "missing-arm"]
)
def test_production_lifecycle_refuses_foreign_or_ambiguous_identity_and_never_mutates(cloud, replacement):
    svc = _owned(cloud, "replaced-servicebus-target")
    row = next(iter(cloud.api.rows.values()))
    metadata = row["properties"]["userMetadata"]
    if replacement == "foreign":
        metadata = metadata.replace(str(svc.guid), "018f42f0-4420-7000-8000-000000000099")
    elif replacement == "no-owner":
        metadata = ";".join(
            part for part in metadata.split(";") if not part.startswith("astrolift-managed-service-id=")
        )
    elif replacement == "no-platform":
        metadata = metadata.replace("astrolift-managed-by=platform", "astrolift-managed-by=foreign")
    elif replacement == "duplicate":
        metadata += f";astrolift-managed-service-id={svc.guid}"
    elif replacement == "alias":
        metadata += ";astrolift_managed_service_id=018f42f0-4420-7000-8000-000000000099"
    elif replacement == "foreign-arm":
        cloud.api.replaced_id = row["id"].replace("controlled-rg", "foreign-rg")
    else:
        cloud.api.replaced_id = ""
    row["properties"]["userMetadata"] = metadata
    before = copy.deepcopy(cloud.api.rows)
    assert not _provision_sync(svc.pk)["ok"]
    before_update = len(cloud.api.calls)
    update = _update_sync(svc.pk)
    assert (
        not update["ok"] and not update["retryable"] and update["errors"] == [UPDATE_NOT_SUPPORTED_IN_PLACE]
    )
    assert len(cloud.api.calls) == before_update
    assert _check_ready_sync(svc.pk, svc.backend_ref) == "error"
    with pytest.raises((AzureOwnershipError, RuntimeError)):
        _managed_binding_for(svc)
    for force in (False, True):
        result = _deprovision_sync(svc.pk, True, force)
        assert not result["ok"] and set(result["errors"]) & {"ownership_refused", "ownership_unknown"}
    assert _writes(cloud) == [] and cloud.api.rows == before
    svc.refresh_from_db()
    assert svc.provider_cleanup_receipt is None


@pytest.mark.parametrize("method", ["GET", "DELETE"])
@pytest.mark.parametrize("status", [401, 403, 409, 500])
def test_real_http_denial_diagnostic_never_turns_into_central_cleanup_success(cloud, method, status):
    svc = _owned(cloud, "denied-servicebus-target")
    cloud.api.failures[method] = (status, "ForbiddenResourceNotFound")
    result = _deprovision_sync(svc.pk, True, True)
    assert not result["ok"] and result["errors"] == ["ownership_unknown"]
    assert len(cloud.api.rows) == 1
    assert _writes(cloud) == ([] if method == "GET" else ["DELETE"])
    svc.refresh_from_db()
    assert svc.provider_cleanup_receipt is None


@pytest.mark.parametrize("force", [False, True])
def test_retention_never_discards_messages_or_claims_fake_snapshot(cloud, force):
    svc = _owned(cloud, "retained-servicebus-target")
    before = copy.deepcopy(cloud.api.rows)
    driver = ServiceBusDriver(config=cloud.cfg)
    outcome = driver.deprovision(
        DeprovisionSpec(svc.backend_ref, managed_service_id=str(svc.guid)), force_destroy=force
    )
    assert not outcome.ok and outcome.errors == ["delete_data_required"]
    with pytest.raises(NotImplementedError, match="snapshot"):
        _deprovision_sync(svc.pk, False, force)
    assert _writes(cloud) == [] and cloud.api.rows == before


def test_concrete_sdk_missing_and_owned_destructive_delete_reach_production_convergence(cloud):
    svc = _owned(cloud, "deleted-servicebus-target")
    assert _deprovision_sync(svc.pk, True, False)["ok"]
    assert cloud.api.rows == {}
    cloud.api.calls.clear()
    assert _deprovision_sync(svc.pk, True, True)["ok"]
    assert _writes(cloud) == []


def test_org_custom_tag_delimiter_cannot_forge_owner_through_actual_spec_builder(cloud):
    svc = _queue("hostile-servicebus-tag")
    org = svc.registered_app.organization
    org.default_resource_tags = {"note": ";astrolift-managed-service-id=foreign"}
    org.save(update_fields=["default_resource_tags"])
    result = _provision_sync(svc.pk)
    assert not result["ok"] and "ownership_refused" in result["errors"]
    assert cloud.api.calls == []


@pytest.mark.parametrize("source", ["foreign", "unavailable", "invalid-handle"])
def test_production_unsupported_update_keeps_permanent_contract_without_http(cloud, source):
    svc = _owned(cloud, "unsupported-servicebus-update")
    if source == "foreign":
        row = next(iter(cloud.api.rows.values()))
        row["properties"]["userMetadata"] = row["properties"]["userMetadata"].replace(
            str(svc.guid), "foreign"
        )
    elif source == "unavailable":
        cloud.api.failures["GET"] = (403, "UnavailableResourceNotFound")
    else:
        svc.backend_ref = "probe/handle"
        svc.save(update_fields=["backend_ref"])
    cloud.api.calls.clear()
    before = copy.deepcopy(cloud.api.rows)
    result = _update_sync(svc.pk)
    assert (
        not result["ok"] and not result["retryable"] and result["errors"] == [UPDATE_NOT_SUPPORTED_IN_PLACE]
    )
    assert cloud.api.calls == [] and cloud.api.rows == before
