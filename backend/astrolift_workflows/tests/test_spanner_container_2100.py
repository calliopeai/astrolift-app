"""Persisted Spanner contenders and server-owned cleanup prove container scope."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from gcp.managed.graph_spanner import (
    _OWNERSHIP_DDL,
    SpannerGraphConfig,
    SpannerGraphDriver,
    _dynamic_graph_ddl,
    _owner_ddl,
)
from tests.gcp.test_managed_graph_spanner import FakeSpannerClient

from astrolift_services.models import ManagedService
from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _finalize_sync,
    _managed_binding_for,
    _provision_sync,
    _update_sync,
)
from astrolift_workflows.spanner_ownership import container_exclusive
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db
HANDLE = "graph_db/legacy-container/knowledge"
MUTATIONS = {
    "create_instance",
    "patch_instance",
    "delete_instance",
    "create_database",
    "patch_database",
    "drop_database",
    "update_ddl",
    "create_backup",
    "restore_database",
}


@pytest.fixture
def world():
    cloud = FakeSpannerClient()

    class RecordingSpanner(SpannerGraphDriver):
        def __init__(self, *, config):
            super().__init__(config=config, client=cloud, sleep=lambda _: None)

    class OtherDriver:
        pass

    def drivers(plugin, role):
        return OtherDriver if plugin != "gcp" or role.endswith(":other") else RecordingSpanner

    def config(_plugin, cluster, **_kwargs):
        values = cluster.provider_config or {}
        if values.get("broken"):
            raise ValueError("unplaceable configuration")
        return SpannerGraphConfig(
            project_id=values.get("project_id", ""),
            region="us-central1",
            shared_instance_id=values.get("shared_instance_id", ""),
            poll_interval_seconds=0,
        )

    with (
        patch("astrolift_drivers.registry.plugins.get", side_effect=drivers),
        patch("core.cluster_observability.managed_config_for", side_effect=config),
    ):
        svc = _service(org_slug="spanner-owner-2100", backend_ref=HANDLE)
        cfg = config("gcp", svc.app_environment.tenant_cluster)
        resolved = SimpleNamespace(plugin_slug="gcp", driver_cls=RecordingSpanner)
        instance = "projects/shared-project/instances/legacy-container"
        database = f"{instance}/databases/knowledge"
        cloud.instances[instance] = {
            "name": instance,
            "labels": {"astrolift-managed-by": "platform"},
            "config": "projects/shared-project/instanceConfigs/regional-us-central1",
            "edition": "ENTERPRISE",
            "state": "READY",
            "processingUnits": 100,
        }
        cloud.databases[database] = {"name": database, "state": "READY", "enableDropProtection": False}
        cloud.ddl[database] = [_OWNERSHIP_DDL, _owner_ddl(str(svc.guid)), *_dynamic_graph_ddl("Graph")]
        yield SimpleNamespace(
            svc=svc, cloud=cloud, cfg=cfg, resolved=resolved, instance=instance, database=database
        )


def _exclusive(world):
    world.svc.refresh_from_db()
    return container_exclusive(world.svc, resolved=world.resolved, cfg=world.cfg)


def _no_writes(world):
    assert not [action for action, _ in world.cloud.calls if action in MUTATIONS]


def test_real_lifecycle_backfills_only_uncontested_legacy_org_and_passes_org_to_binding(world) -> None:
    assert _exclusive(world)
    with CaptureQueriesContext(connection) as queries:
        result = _update_sync(world.svc.pk)
    assert result["ok"]
    assert any("pg_advisory_xact_lock" in query["sql"] for query in queries)
    assert world.cloud.instances[world.instance]["labels"]["astrolift-organization-id"] == str(
        world.svc.registered_app.organization.guid
    )
    assert _managed_binding_for(world.svc).env_vars["GCP_SPANNER_DATABASE"].literal == "knowledge"


@pytest.mark.parametrize("state", ["live", "deleted", "unplaced", "broken", "failed-no-handle"])
def test_live_and_unreconciled_or_unknown_contenders_block_before_cloud_mutation(world, state: str) -> None:
    other = _service(org_slug=f"spanner-{state}-2100", backend_ref="graph_db/legacy-container/other")
    if state == "deleted":
        other.status = ManagedService.Status.DEPROVISIONING
        other.save(update_fields=["status"])
        other.soft_delete()
    elif state == "unplaced":
        cluster = other.app_environment.tenant_cluster
        cluster.provider_config = {"project_id": ""}
        cluster.save(update_fields=["provider_config"])
    elif state == "broken":
        cluster = other.app_environment.tenant_cluster
        cluster.provider_config = {"broken": True}
        cluster.save(update_fields=["provider_config"])
    elif state == "failed-no-handle":
        other.backend_ref = ""
        other.status = ManagedService.Status.FAILED
        other.config = {"instance_id": "legacy-container"}
        other.save(update_fields=["backend_ref", "status", "config"])
    assert not _exclusive(world)
    world.svc.config = {"manage_instance_capacity": True, "processing_units": 500}
    world.svc.save(update_fields=["config"])
    assert not _update_sync(world.svc.pk)["ok"]
    _no_writes(world)
    assert world.database in world.cloud.databases


@pytest.mark.parametrize("different", ["project", "instance", "driver"])
def test_known_independent_driver_project_or_container_does_not_contest(world, different: str) -> None:
    kwargs = (
        {"project_id": "other-project"}
        if different == "project"
        else {"variant": "other"}
        if different == "driver"
        else {"backend_ref": "graph_db/other-instance/knowledge"}
    )
    _service(org_slug=f"spanner-other-{different}-2100", **kwargs)
    assert _exclusive(world)
    assert _update_sync(world.svc.pk)["ok"]


def test_cleanup_receipt_is_actual_success_exact_incarnation_and_not_teardown_intent(world) -> None:
    assert _update_sync(world.svc.pk)["ok"]
    world.svc.refresh_from_db()
    observed = deepcopy(world.svc.provider_placement_identity)
    owner_id = str(world.svc.registered_app.organization.guid)
    world.cloud.instances[world.instance]["labels"]["astrolift-organization-id"] = owner_id
    assert _deprovision_sync(world.svc.pk, True, True)["ok"]
    world.svc.refresh_from_db()
    receipt = world.svc.provider_cleanup_receipt
    assert receipt["handle"] == HANDLE and receipt["organization_id"] == owner_id
    assert receipt["project_id"] == "shared-project" and receipt["service_id"] == str(world.svc.guid)
    assert receipt["cluster_id"] == str(world.svc.app_environment.tenant_cluster.guid)
    assert receipt["provider_id"] == str(world.svc.app_environment.tenant_cluster.provider_plugin.guid)
    assert receipt["observed_at"]
    _finalize_sync(world.svc.pk)
    replacement = _service(org_slug="replacement-2100", backend_ref="graph_db/legacy-container/other")
    assert container_exclusive(replacement, resolved=world.resolved, cfg=world.cfg)
    # Reprovisioning the exact old GUID clears the receipt before any provider
    # mutation, even when a contender then makes the operation refuse.
    world.svc.deleted_at = None
    world.svc.config = {"manage_instance_capacity": True, "processing_units": 500}
    world.svc.save(update_fields=["deleted_at", "config"])
    assert not _provision_sync(world.svc.pk)["ok"]
    world.svc.refresh_from_db()
    assert world.svc.provider_cleanup_receipt is None
    assert world.svc.provider_placement_identity == observed
    assert not container_exclusive(replacement, resolved=world.resolved, cfg=world.cfg)


def test_caller_policy_and_config_cannot_forge_cleanup_receipt_or_cross_incarnation(world) -> None:
    other = _service(org_slug="receipt-forger-2100", backend_ref="graph_db/legacy-container/other")
    forged = {
        "version": 1,
        "service_id": str(world.svc.guid),
        "handle": HANDLE,
        "observed_at": timezone.now().isoformat(),
    }
    other.lifecycle_policy = {
        "provider_cleanup_receipt": forged,
        "retention": {"provider_cleanup_receipt": forged},
    }
    other.config = {"provider_cleanup_receipt": forged}
    other.status = ManagedService.Status.DEPROVISIONING
    other.save(update_fields=["lifecycle_policy", "config", "status"])
    other.soft_delete()
    assert not _exclusive(world)
    assert other.provider_cleanup_receipt is None
    # Even trusted stale data cannot authorize a different service/handle.
    other.provider_cleanup_receipt = deepcopy(forged)
    other.save(update_fields=["provider_cleanup_receipt"])
    assert not _exclusive(world)
    assert not ManagedService._meta.get_field("provider_cleanup_receipt").editable
    request = SimpleNamespace(user=SimpleNamespace(has_perm=lambda _perm: True))
    assert (
        "provider_cleanup_receipt" not in admin.site._registry[ManagedService].get_form(request).base_fields
    )


def test_cloud_inventory_still_refuses_despite_confirmed_other_cleanup(world) -> None:
    world.cloud.instances[world.instance]["labels"]["astrolift-organization-id"] = str(
        world.svc.registered_app.organization.guid
    )
    world.svc.config = {"delete_empty_instance": True}
    world.svc.save(update_fields=["config"])
    extra = f"{world.instance}/databases/unknown"
    world.cloud.databases[extra] = {"name": extra}
    assert _exclusive(world)
    result = _deprovision_sync(world.svc.pk, True, True)
    assert not result["ok"]
    _no_writes(world)
    world.svc.refresh_from_db()
    assert world.svc.provider_cleanup_receipt is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("changed", ["project", "organization", "provider"])
def test_real_lock_wait_rechecks_current_placement_before_any_provider_work(world, changed: str) -> None:
    import hashlib
    import threading

    from django.db import connections, transaction

    from astrolift_clusters.models import ProviderPlugin
    from astrolift_identity.models import Organization

    waiting = threading.Event()
    errors: list[Exception] = []
    lock_id = int.from_bytes(
        hashlib.sha256(b"spanner:shared-project:legacy-container").digest()[:8], "big", signed=True
    )

    def worker():
        def watch(execute, sql, params, many, context):
            if "pg_advisory_xact_lock" in sql:
                waiting.set()
            return execute(sql, params, many, context)

        try:
            with connection.execute_wrapper(watch):
                _update_sync(world.svc.pk)
        except Exception as exc:
            errors.append(exc)
        finally:
            connections.close_all()

    thread = threading.Thread(target=worker)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])
        thread.start()
        assert waiting.wait(timeout=10), "worker did not reach its actual container lock"
        cluster = world.svc.app_environment.tenant_cluster
        if changed == "project":
            cluster.provider_config = {"project_id": "replaced-project"}
            cluster.save(update_fields=["provider_config"])
        elif changed == "organization":
            cluster.organization = Organization.objects.create(name="Foreign", slug="foreign-lock-2100")
            cluster.save(update_fields=["organization"])
        else:
            cluster.provider_plugin = ProviderPlugin.objects.create(
                name="Replacement", slug="replacement-lock-2100"
            )
            cluster.save(update_fields=["provider_plugin"])
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], ValueError)
    assert "changed" in str(errors[0]) or "another organization" in str(errors[0])
    _no_writes(world)


@pytest.mark.parametrize("retired", ["service", "app", "environment", "cluster", "provider"])
def test_retired_current_source_never_reprovisions_or_updates(world, retired: str) -> None:
    svc = world.svc
    row = {
        "service": svc,
        "app": svc.registered_app,
        "environment": svc.app_environment,
        "cluster": svc.app_environment.tenant_cluster,
        "provider": svc.app_environment.tenant_cluster.provider_plugin,
    }[retired]
    row.soft_delete()
    for action in (_provision_sync, _update_sync):
        with pytest.raises(ValueError, match="retired"):
            action(svc.pk)
    _no_writes(world)


@pytest.mark.parametrize("changed", ["project", "provider", "handle", "organization"])
def test_observed_placement_refuses_preexisting_reassignment_before_config_or_cloud(world, changed):
    from astrolift_clusters.models import ProviderPlugin
    from astrolift_identity.models import Organization

    assert _update_sync(world.svc.pk)["ok"]
    world.svc.refresh_from_db()
    observed = deepcopy(world.svc.provider_placement_identity)
    assert observed["project_id"] == "shared-project" and observed["handle"] == HANDLE
    cluster = world.svc.app_environment.tenant_cluster
    if changed == "project":
        cluster.provider_config = {"project_id": "other-project"}
        cluster.save(update_fields=["provider_config"])
    elif changed == "provider":
        cluster.provider_plugin = ProviderPlugin.objects.create(name="New", slug="other-provider-2100")
        cluster.save(update_fields=["provider_plugin"])
    elif changed == "organization":
        world.svc.registered_app.organization = Organization.objects.create(name="New", slug="new-org-2100")
        world.svc.registered_app.save(update_fields=["organization"])
    else:
        world.svc.backend_ref = "graph_db/new-container/knowledge"
        world.svc.save(update_fields=["backend_ref"])
    world.cloud.calls.clear()
    with patch("core.cluster_observability.managed_config_for") as config:
        for action in (_provision_sync, _update_sync):
            with pytest.raises(ValueError, match="observed placement changed"):
                action(world.svc.pk)
        with pytest.raises(ValueError, match="observed placement changed"):
            _deprovision_sync(world.svc.pk, True, True)
        world.svc = ManagedService.all_objects.select_related(
            "registered_app__organization", "app_environment__tenant_cluster__provider_plugin"
        ).get(pk=world.svc.pk)
        with pytest.raises(ValueError, match="observed placement changed"):
            _managed_binding_for(world.svc)
        config.assert_not_called()
    assert not world.cloud.calls
    world.svc.refresh_from_db()
    assert world.svc.provider_placement_identity == observed


def test_failed_reprovision_retains_actual_observation_and_original_timestamp(world):
    assert world.svc.provider_placement_identity is None
    # Neither a failed legacy request nor caller intent invents provenance.
    world.svc.config = {"invalid_field": "refuse"}
    world.svc.save(update_fields=["config"])
    assert not _provision_sync(world.svc.pk)["ok"]
    world.svc.refresh_from_db()
    assert world.svc.provider_placement_identity is None
    world.svc.config = {}
    world.svc.save(update_fields=["config"])
    assert _provision_sync(world.svc.pk)["ok"]
    world.svc.refresh_from_db()
    observed = deepcopy(world.svc.provider_placement_identity)
    assert observed["handle"] == world.svc.backend_ref == HANDLE
    assert observed["observed_at"]
    world.svc.config = {"invalid_field": "refuse"}
    world.svc.save(update_fields=["config"])
    assert not _provision_sync(world.svc.pk)["ok"]
    world.svc.refresh_from_db()
    assert world.svc.provider_placement_identity == observed


def test_config_policy_and_admin_cannot_supply_observed_placement(world):
    identity = {"version": 1, "service_id": str(world.svc.guid), "project_id": "forged"}
    world.svc.config = {"provider_placement_identity": identity}
    world.svc.lifecycle_policy = {"provider_placement_identity": identity}
    world.svc.save(update_fields=["config", "lifecycle_policy"])
    assert not _provision_sync(world.svc.pk)["ok"]
    world.svc.refresh_from_db()
    assert world.svc.provider_placement_identity is None
    assert not ManagedService._meta.get_field("provider_placement_identity").editable
    request = SimpleNamespace(user=SimpleNamespace(has_perm=lambda _perm: True))
    assert (
        "provider_placement_identity"
        not in admin.site._registry[ManagedService].get_form(request).base_fields
    )


def test_contender_inventory_is_bounded_and_has_no_per_row_database_queries(world):
    for i in range(3):
        _service(org_slug=f"independent-bounded-{i}-2100", backend_ref=f"graph_db/independent-{i}/knowledge")
    world.svc = ManagedService.all_objects.select_related(
        "registered_app__organization", "app_environment__tenant_cluster__provider_plugin"
    ).get(pk=world.svc.pk)
    # The actual related rows come from one joined query; driver config and
    # immutable target derivation do not issue owner/policy queries per row.
    with CaptureQueriesContext(connection) as queries:
        assert container_exclusive(world.svc, resolved=world.resolved, cfg=world.cfg)
    assert len(queries) == 1
    with patch("astrolift_workflows.spanner_ownership._MAX_CONTENDERS", 2):
        assert not _exclusive(world)


def test_additive_nullable_authority_fields_accept_preupgrade_insert_shape(world):
    import uuid

    # Execute the old writer's complete column shape, omitting both new fields.
    # This exercises the actual migrated PostgreSQL table rather than ORM defaults.
    fields = [
        field
        for field in ManagedService._meta.concrete_fields
        if not field.primary_key
        and field.name not in {"provider_cleanup_receipt", "provider_placement_identity"}
    ]
    quote = connection.ops.quote_name
    columns = ", ".join(quote(field.column) for field in fields)
    expressions = []
    parameters = []
    for field in fields:
        if field.name in {"guid", "name"}:
            expressions.append("%s")
            parameters.append(uuid.uuid4() if field.name == "guid" else "old-writer-2100")
        else:
            expressions.append(quote(field.column))
    parameters.append(world.svc.pk)
    table = quote(ManagedService._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {table} ({columns}) SELECT {', '.join(expressions)} "
            f"FROM {table} WHERE id = %s RETURNING id",
            parameters,
        )
        pk = cursor.fetchone()[0]
    inserted = ManagedService.all_objects.get(pk=pk)
    assert inserted.provider_cleanup_receipt is None
    assert inserted.provider_placement_identity is None
