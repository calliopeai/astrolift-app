"""PostgreSQL lifecycle dispatch through the installed Standard provider wheel."""

import copy
import dataclasses
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from _sdk.azure_ownership import AzureOwnershipError
from azure._event_grid_namespace_ownership import OwnershipUnknown
from azure.managed.event_grid_namespace import AzureEventGridNamespaceDriver, AzureEventGridNamespaceError

from astrolift_workflows.activities.managed_service_lifecycle import (
    _check_ready_sync,
    _deprovision_sync,
    _managed_binding_for,
    _provision_sync,
    _update_sync,
    build_provision_spec,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service
from providers.tests.azure.test_event_grid_namespace_wire_2032 import fixture

pytestmark = pytest.mark.django_db


@pytest.fixture
def cloud():
    driver, api, secrets, _ = fixture()
    state = SimpleNamespace(api=api, secrets=secrets, cfg=driver._config)
    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=AzureEventGridNamespaceDriver),
        patch("core.cluster_observability.managed_config_for", side_effect=lambda *_, **__: state.cfg),
    ):
        yield state
    state.cfg.mgmt_client.close()
    state.cfg.locks_client.close()


def _bus(slug):
    svc = _service(org_slug=slug, plugin_slug="azure", variant="event_grid_namespace", backend_ref="")
    svc.kind = "event_bus"
    svc.name = "events"
    svc.config = {
        "subscriptions": [{"name": "workers", "delivery_mode": "pull"}],
        "access_mode": "publish_pull",
        "subscription_name": "workers",
    }
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
    return [call for call in cloud.api.calls if call[0] != "GET"]


def test_colliding_slug_joins_and_same_app_services_use_database_source_guids(cloud):
    rows = [_bus("alpha-beta"), _bus("alpha")]
    rows[0].registered_app.slug = "gamma"
    rows[1].registered_app.slug = "beta-gamma"
    for row in rows:
        row.registered_app.save(update_fields=["slug"])
    joins = ["-".join((r.registered_app.organization.slug, r.registered_app.slug)) for r in rows]
    assert joins[0] == joins[1]
    from astrolift_services.models import ManagedService

    rows.append(
        ManagedService.objects.create(
            registered_app=rows[0].registered_app,
            app_environment=rows[0].app_environment,
            kind="event_bus",
            variant="event_grid_namespace",
            name="secondary-events",
            config=rows[0].config,
        )
    )
    results = [_provision_sync(row.pk) for row in rows]
    assert all(result["ok"] and result["ready"] for result in results), results
    assert len({result["handle"] for result in results}) == 3
    for row, result in zip(rows, results, strict=True):
        row.backend_ref = result["handle"]
        row.save(update_fields=["backend_ref"])
        source = str(row.guid).replace("-", "")
        namespace, topic = row.backend_ref.rsplit("/", 2)[-2:]
        assert source in namespace and source in topic and len(row.backend_ref) <= 512
        child = next(key for key in cloud.api.rows if f"/topics/{topic}/eventSubscriptions/" in key)
        assert source in child.rsplit("/", 1)[1] and len(child.rsplit("/", 1)[1]) == 50
        cloud.api.calls.clear()
        binding = _managed_binding_for(row)
        assert binding.iam_grants[0].resource.endswith("/topics/" + topic)
        assert binding.env_vars["EVENT_GRID_NAMESPACE_ACCESS_KEY"].secret_ref in cloud.secrets.values
        assert _check_ready_sync(row.pk, row.backend_ref) == "available"
        assert _writes(cloud) == []


@pytest.mark.parametrize("damage", ["source", "namespace-arm", "child-receipt", "inventory"])
def test_current_database_source_and_complete_ownership_gate_all_lifecycle_paths(cloud, damage):
    svc = _owned(cloud, "standard-source-proof")
    namespace = next(key for key in cloud.api.rows if key.endswith(svc.backend_ref.rsplit("/", 2)[-2]))
    if damage == "source":
        cloud.api.rows[namespace]["tags"]["astrolift-managed-service-id"] = (
            "018f42f0-4420-7000-8000-000000000099"
        )
    elif damage == "namespace-arm":
        cloud.api.rows[namespace]["id"] = namespace.replace("controlled-rg", "foreign-rg")
    elif damage == "child-receipt":
        cloud.secrets.values.clear()
    elif damage == "inventory":
        cloud.api.denied.add(namespace + "/clients")
    before = copy.deepcopy(cloud.api.rows)
    assert not _provision_sync(svc.pk)["ok"]
    assert not _update_sync(svc.pk)["ok"]
    assert _check_ready_sync(svc.pk, svc.backend_ref) == "error"
    with pytest.raises((OwnershipUnknown, AzureOwnershipError), match=".+"):
        _managed_binding_for(svc)
    for force in (False, True):
        assert not _deprovision_sync(svc.pk, True, force)["ok"]
    assert _writes(cloud) == [] and cloud.api.rows == before
    svc.refresh_from_db()
    assert svc.provider_cleanup_receipt is None


@pytest.mark.parametrize("context", ["legacy", "group", "subscription"])
def test_recorded_placement_never_guesses_or_moves_to_current_provider(cloud, context):
    svc = _owned(cloud, "standard-placement-proof")
    if context == "legacy":
        svc.backend_ref = "event_bus/old-namespace/old-topic"
        svc.save(update_fields=["backend_ref"])
    elif context == "group":
        cloud.cfg = dataclasses.replace(cloud.cfg, resource_group="foreign-rg")
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, subscription_id="018f42f0-4420-7000-8000-000000000099")
    assert not _provision_sync(svc.pk)["ok"]
    assert not _update_sync(svc.pk)["ok"]
    assert not _deprovision_sync(svc.pk, True, True)["ok"]
    with pytest.raises(OwnershipUnknown, match=".+"):
        _managed_binding_for(svc)
    assert cloud.api.calls == []


def test_recorded_identity_survives_display_and_prefix_changes_and_updates_observed_state(cloud):
    svc = _owned(cloud, "standard-recorded-proof")
    namespace, topic = svc.backend_ref.rsplit("/", 2)[-2:]
    svc.name = "renamed"
    svc.config.update(
        namespace_name=namespace,
        topic_name=topic,
        capacity=3,
        topic_retention_days=5,
        input_schema="CloudEventSchemaV1_0",
        is_zone_redundant=False,
        minimum_tls_version_allowed="1.2",
    )
    svc.save(update_fields=["name", "config"])
    cloud.cfg = dataclasses.replace(cloud.cfg, namespace_name_prefix="changed", topic_name_prefix="changed")
    assert (
        build_provision_spec(svc, cluster=svc.app_environment.tenant_cluster).recorded_handle
        == svc.backend_ref
    )
    result = _update_sync(svc.pk)
    assert result["ok"], result
    namespace_row = next(
        row for key, row in cloud.api.rows.items() if key.endswith("/namespaces/" + namespace)
    )
    assert namespace_row["sku"]["capacity"] == 3
    cloud.api.calls.clear()
    assert _check_ready_sync(svc.pk, svc.backend_ref) == "available"
    assert _managed_binding_for(svc).env_vars["EVENT_BUS_NAME"].literal == topic
    assert _writes(cloud) == []


def test_retention_and_ancestor_lock_refuse_force_without_cascading(cloud):
    svc = _owned(cloud, "standard-delete-proof")
    with pytest.raises(AzureEventGridNamespaceError, match="snapshot"):
        _deprovision_sync(svc.pk, False, True)
    namespace = next(key for key in cloud.api.rows if key.endswith(svc.backend_ref.rsplit("/", 2)[-2]))
    cloud.api.locks = [
        {
            "id": namespace + "/providers/Microsoft.Authorization/locks/operator",
            "name": "operator",
            "properties": {"level": "CanNotDelete"},
        }
    ]
    assert not _deprovision_sync(svc.pk, True, True)["ok"]
    assert _writes(cloud) == []
    cloud.api.locks.clear()
    result = _deprovision_sync(svc.pk, True, False)
    assert result["ok"], result
    assert not cloud.api.rows and not cloud.secrets.values


def test_missing_recorded_child_refuses_readiness_binding_and_update_but_can_confirm_teardown(cloud):
    svc = _owned(cloud, "standard-absent-child")
    child = next(key for key in cloud.api.rows if "/eventSubscriptions/" in key)
    cloud.api.rows.pop(child)
    assert not _provision_sync(svc.pk)["ok"]
    assert not _update_sync(svc.pk)["ok"]
    assert _check_ready_sync(svc.pk, svc.backend_ref) == "error"
    with pytest.raises(OwnershipUnknown):
        _managed_binding_for(svc)
    assert _writes(cloud) == []
    result = _deprovision_sync(svc.pk, True, True)
    assert result["ok"], result
    assert not cloud.api.rows and not cloud.secrets.values
    assert not any(method == "DELETE" and path == child for method, path, *_ in cloud.api.calls)
