"""Original HTTP authority, real SDK codecs and separately visible PostgreSQL commits."""

import base64
import json
import subprocess
from dataclasses import replace
from threading import Event, Thread
from uuid import uuid4

import pytest
from django.db import connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from gcp.identity_source import IdentitySourceError
from google.cloud import aiplatform_v1beta1 as vertex

from astrolift_services.gcp_app_identity_plan import EndpointAppPlanError
from astrolift_services.gcp_identity_source import (
    SourceOperation,
    _declaration,
    bootstrap_original_identity,
    original_identity_context,
    source_mutex,
)
from astrolift_services.models import GCPAppIdentitySource, GCPClusterIdentitySource, ManagedService
from astrolift_services.tests.test_gcp_app_identity_plan_2278 import world as endpoint_world
from core.permissions import PermissionDenied
from providers.tests.gcp.test_identity_source_2278 import NUMBER, UID, SourceWire

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world(monkeypatch, client, tmp_path):
    w = endpoint_world.__wrapped__(monkeypatch, client)
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=source-fixture",
        ],
        capture_output=True,
        check=True,
    )
    w.cluster.ca_cert = base64.b64encode(cert.read_bytes()).decode()
    w.cluster.endpoint = "34.1.2.3"
    w.cluster.region = "us-central1-a"
    w.cluster.provider_config.update(location="us-central1-a", cluster_name="owned-cluster")
    w.cluster.save()
    declaration, _ = _declaration(w.authority)
    w.native = SourceWire(declaration)
    w.native.cluster.master_auth.cluster_ca_certificate = w.cluster.ca_cert
    w.native.endpoint = vertex.Endpoint(type(w.source.endpoint).to_dict(w.source.endpoint))
    w.operation = SourceOperation(str(uuid4()), "DeployAppWorkflow-original", "original-run")
    w.factory = lambda declaration: w.native.port(declaration)
    return w


def bootstrap(w, operation=None):
    return bootstrap_original_identity(w.authority, operation or w.operation, native_factory=w.factory)


def independent_row(source_id):
    db = connection.Database.connect(**connection.get_connection_params())
    try:
        db.autocommit = True
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT state,unique_id,operation_id,create_request_sha256 FROM astrolift_services_gcpappidentitysource WHERE guid=%s",
                [source_id],
            )
            return cursor.fetchone()
    finally:
        db.close()


def test_http_bootstrap_one_create_committed_numeric_uid_and_read_only_current_context(world):
    states = []

    def response(name):
        if name == "CreateServiceAccount":
            row = GCPAppIdentitySource.objects.get()
            assert independent_row(str(row.guid))[0] == "SENT" and not connection.in_atomic_block
            states.append(row.state)

    world.native.after = response
    result = bootstrap(world)
    assert result.identity.service_account_unique_id == UID
    assert result.identity.region == "us-central1" and result.cluster.location == "us-central1-a"
    assert independent_row(result.source_id)[0:2] == ("OBSERVED", UID)
    assert states == ["SENT"] and world.native.writes == 1
    row = GCPAppIdentitySource.objects.get()
    assert row.account_id != world.identity.service_account_id
    assert row.authority_reference["credential_guid"] == str(world.token.guid)
    assert len(json.dumps(row.original_snapshot)) < 8192 and world.private_key not in json.dumps(
        row.authority_reference
    )
    before = (row.version, row.updated_at)
    world.native.after = None
    assert original_identity_context(world.authority, native_factory=world.factory) == result
    row.refresh_from_db()
    assert (row.version, row.updated_at) == before and world.native.writes == 1
    assert bootstrap(world, SourceOperation(str(uuid4()), "later", "later-run")) == result
    assert world.native.writes == 1


@pytest.mark.parametrize("boundary", ["after_create", "after_readback"])
def test_withdrawal_retains_only_original_numeric_effect_evidence(world, boundary):
    def withdraw(name):
        selected = (
            name == "CreateServiceAccount"
            if boundary == "after_create"
            else name == "GetServiceAccount" and world.native.writes
        )
        if selected:
            type(world.token).objects.filter(pk=world.token.pk).update(is_revoked=True)

    world.native.after = withdraw
    with pytest.raises(PermissionDenied):
        bootstrap(world)
    row = GCPAppIdentitySource.objects.get()
    assert row.state == "EVIDENCE" and row.unique_id == UID and row.observed_at is None
    assert independent_row(str(row.guid))[0:2] == ("EVIDENCE", UID) and world.native.writes == 1
    with pytest.raises(PermissionDenied):
        bootstrap(world)
    assert world.native.writes == 1


def test_lost_reply_unknown_blocks_retry_new_operation_and_same_email_adoption(world):
    world.native.lost = True
    with pytest.raises(IdentitySourceError, match="CREATE_UNKNOWN"):
        bootstrap(world)
    row = GCPAppIdentitySource.objects.get()
    assert independent_row(str(row.guid))[0:2] == ("UNKNOWN", None)
    assert world.native.account is not None and world.native.writes == 1
    for operation in (world.operation, SourceOperation(str(uuid4()), "new", "new-run")):
        with pytest.raises(IdentitySourceError, match="UNKNOWN|PENDING_OPERATION"):
            bootstrap(world, operation)
    assert world.native.writes == 1


def test_lost_sent_commit_ack_blocks_effect_and_never_reopens(world, monkeypatch):
    from astrolift_services.gcp_identity_source import SourceStore

    original = SourceStore.sent

    def lost(store, *args, **kwargs):
        original(store, *args, **kwargs)
        raise IdentitySourceError("COMMIT_ACK_LOST")

    monkeypatch.setattr(SourceStore, "sent", lost)
    with pytest.raises(IdentitySourceError, match="COMMIT_ACK_LOST"):
        bootstrap(world)
    row = GCPAppIdentitySource.objects.get()
    assert independent_row(str(row.guid))[0] == "UNKNOWN" and world.native.writes == 0
    with pytest.raises(IdentitySourceError, match="UNKNOWN"):
        bootstrap(world)
    assert world.native.writes == 0


def test_evidence_ack_loss_retains_uid_and_fresh_read_recovers_without_new_send(world, monkeypatch):
    from astrolift_services.gcp_identity_source import SourceStore

    original = SourceStore.evidence

    def lost(store, *args, **kwargs):
        original(store, *args, **kwargs)
        raise IdentitySourceError("EVIDENCE_ACK_LOST")

    monkeypatch.setattr(SourceStore, "evidence", lost)
    with pytest.raises(IdentitySourceError, match="EVIDENCE_ACK_LOST"):
        bootstrap(world)
    row = GCPAppIdentitySource.objects.get()
    assert independent_row(str(row.guid))[0:2] == ("EVIDENCE", UID)
    monkeypatch.setattr(SourceStore, "evidence", original)
    assert bootstrap(world).identity.service_account_unique_id == UID and world.native.writes == 1


@pytest.mark.parametrize(
    "change",
    [
        "vertex_region_absent",
        "zonal_vertex_region",
        "certificate",
        "mixed_union",
        "role",
        "endpoint_owner",
        "endpoint_version",
        "endpoint_traffic",
    ],
)
def test_pre_gsa_complete_admission_refuses_without_create(world, change):
    if change == "vertex_region_absent":
        world.cluster.provider_config.pop("vertex_region")
        world.cluster.save()
    elif change == "zonal_vertex_region":
        world.cluster.provider_config["vertex_region"] = "us-central1-a"
        world.cluster.save()
    elif change == "certificate":
        world.cluster.ca_cert = "not-a-certificate"
        world.cluster.save()
    elif change == "mixed_union":
        ManagedService.objects.create(
            registered_app=world.medops_app, app_environment=world.env, kind="postgres", variant="cloudsql"
        )
    elif change == "role":
        world.native.role.included_permissions.append("aiplatform.endpoints.update")
    elif change == "endpoint_owner":
        world.native.endpoint.labels.clear()
    elif change == "endpoint_version":
        world.native.endpoint.deployed_models[0].model_version_id = ""
    else:
        world.native.endpoint.traffic_split.clear()
    with pytest.raises((IdentitySourceError, EndpointAppPlanError)):
        bootstrap(world)
    assert world.native.writes == 0 and not GCPAppIdentitySource.objects.exists()


@pytest.mark.parametrize(
    "change", ["native_cluster", "native_project", "provider", "credential", "source_config", "gsa_uid"]
)
def test_original_identity_never_retargets_or_adopts_recreated_source(world, change):
    result = bootstrap(world)
    if change == "native_cluster":
        world.native.cluster.id = "recreated-incarnation"
    elif change == "native_project":
        world.native.project.name = "projects/999999999999"
    elif change == "provider":
        from astrolift_clusters.models import ProviderPlugin

        other = ProviderPlugin.objects.create(slug="gcp-copy", name="other")
        type(world.cluster).objects.filter(pk=world.cluster.pk).update(provider_plugin=other)
    elif change == "credential":
        world.cluster.provider_config["project_id"] = NUMBER
        world.cluster.save()
    elif change == "source_config":
        world.cluster.provider_config["location"] = "us-central1-b"
        world.cluster.save()
    else:
        world.native.account.unique_id = "110999999999999999999"
    with pytest.raises((IdentitySourceError, PermissionDenied, EndpointAppPlanError)):
        original_identity_context(world.authority, native_factory=world.factory)
    assert (
        world.native.writes == 1 and GCPAppIdentitySource.objects.get(guid=result.source_id).unique_id == UID
    )


@pytest.mark.parametrize("action", ["bootstrap", "read", "mutex"])
def test_outer_atomic_refused_before_any_native_or_reservation(world, action):
    with transaction.atomic(), pytest.raises(ValueError, match="COMMITTED_DATABASE|ENCLOSING_TRANSACTION"):
        if action == "bootstrap":
            bootstrap(world)
        elif action == "read":
            original_identity_context(world.authority, native_factory=world.factory)
        else:
            with source_mutex(world.authority):
                pass
    assert world.native.calls == [] and not GCPClusterIdentitySource.objects.exists()


def test_original_app_mutex_blocks_parallel_create_before_native(world):
    with source_mutex(world.authority):
        with pytest.raises(IdentitySourceError, match="SOURCE_BUSY"):
            bootstrap(world)
    assert world.native.calls == [] and world.native.writes == 0


def test_existing_cluster_app_writer_nowait_collision_then_fresh_retry(world):
    entered, release, failures = Event(), Event(), []

    def writer():
        try:
            with transaction.atomic():
                type(world.cluster)._unscoped.select_for_update().get(pk=world.cluster.pk)
                type(world.medops_app)._unscoped.select_for_update().get(pk=world.medops_app.pk)
                entered.set()
                assert release.wait(10)
        except Exception as error:
            failures.append(error)
        finally:
            connections.close_all()

    thread = Thread(target=writer)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(ValueError, match="JOURNAL_BUSY"):
            bootstrap(world)
        assert world.native.writes == 0
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not failures
    assert bootstrap(world).identity.service_account_unique_id == UID


def test_physical_first_pin_preserves_actual_scope_and_never_repins_incarnation(world):
    _, physical = _declaration(world.authority)
    _, native = world.native.port().observe_cluster(current=lambda: None)
    with source_mutex(world.authority) as store:
        first = store.pin(world.operation, physical, native)
        with pytest.raises(IdentitySourceError, match="INCARNATION_CHANGED"):
            store.pin(world.operation, physical, replace(native, native_cluster_id="other-incarnation"))
    assert GCPClusterIdentitySource.objects.count() == 1
    row = GCPClusterIdentitySource.objects.get(guid=first)
    assert row.organization_id == world.cluster.organization_id
    assert row.original_snapshot["native"]["location"] == "us-central1-a"
    assert row.original_snapshot["native"]["project_number"] == NUMBER


def test_protected_and_retired_history_blocks_empty_replacement(world):
    result = bootstrap(world)
    row = GCPAppIdentitySource.objects.get()
    with pytest.raises(ValueError, match="RETIREMENT"):
        row.soft_delete()
    with pytest.raises(ProtectedError):
        world.medops_app.delete()
    from django.db import IntegrityError
    from django.utils import timezone

    with transaction.atomic(), pytest.raises(IntegrityError, match="IMMUTABLE"):
        GCPAppIdentitySource._unscoped.filter(pk=row.pk).update(deleted_at=timezone.now())
    assert (
        world.native.writes == 1
        and GCPAppIdentitySource._unscoped.get(guid=result.source_id).unique_id == UID
    )


def test_reverse_denial_preserves_rows_and_migration_record_and_complete_graph(world):
    bootstrap(world)
    executor = MigrationExecutor(connection)
    original = executor.loader.graph.leaf_nodes()
    try:
        with pytest.raises(RuntimeError, match="EMPTY_HISTORY"):
            executor.migrate([("astrolift_services", "0039_gcp_gke_preparation_journal")])
        executor = MigrationExecutor(connection)
        assert ("astrolift_services", "0040_gcp_identity_sources") in executor.loader.applied_migrations
        assert GCPAppIdentitySource.objects.get().unique_id == UID
        assert GCPClusterIdentitySource.objects.count() == 1
    finally:
        MigrationExecutor(connection).migrate(original)


def test_empty_source_down_up_restores_full_original_graph():
    executor = MigrationExecutor(connection)
    original = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([("astrolift_services", "0039_gcp_gke_preparation_journal")])
        assert "astrolift_services_gcpappidentitysource" not in connection.introspection.table_names()
        MigrationExecutor(connection).migrate(original)
        assert GCPAppIdentitySource.objects.count() == 0
    finally:
        MigrationExecutor(connection).migrate(original)


@pytest.mark.parametrize("numeric", [False, True])
def test_configured_project_id_or_number_pins_the_exact_verified_pair(world, numeric):
    if numeric:
        world.cluster.provider_config["project_id"] = NUMBER
        world.cluster.save()
        world.native.declaration = _declaration(world.authority)[0]
    result = bootstrap(world)
    assert result.identity.project_id == "fixture-project" and result.identity.project_number == NUMBER
    assert result.identity.credential.declared_account == (NUMBER if numeric else "fixture-project")
    assert world.native.writes == 1


@pytest.mark.parametrize("move", ["provider", "organization", "team"])
def test_post_send_owner_reassignment_retains_only_original_effect_evidence(world, move):
    from astrolift_clusters.models import ProviderPlugin

    def changed(name):
        if name != "CreateServiceAccount":
            return
        if move == "provider":
            provider = ProviderPlugin.objects.create(slug="gcp-other", name="different")
            type(world.cluster)._unscoped.filter(pk=world.cluster.pk).update(provider_plugin=provider)
        elif move == "organization":
            type(world.medops_app)._unscoped.filter(pk=world.medops_app.pk).update(
                organization=world.other_org
            )
        else:
            type(world.medops_app)._unscoped.filter(pk=world.medops_app.pk).update(team=world.platform)

    world.native.after = changed
    with pytest.raises((PermissionDenied, IdentitySourceError)):
        bootstrap(world)
    row = GCPAppIdentitySource.objects.get()
    assert row.state == "EVIDENCE" and row.unique_id == UID and row.organization_id == world.org.pk
    assert row.provider_plugin_id == world.cluster.provider_plugin_id
    assert independent_row(str(row.guid))[0:2] == ("EVIDENCE", UID)
    assert world.native.writes == 1
    with pytest.raises((PermissionDenied, IdentitySourceError)):
        original_identity_context(world.authority, native_factory=world.factory)
    assert world.native.writes == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation_id", uuid4()),
        ("account_id", "foreign-source"),
        ("original_sha256", "0" * 64),
        ("unique_id", "1234567"),
    ],
)
def test_original_fields_are_immutable_in_normal_save_and_bulk_update(world, field, value):
    from django.db import IntegrityError

    bootstrap(world)
    row = GCPAppIdentitySource.objects.get()
    setattr(row, field, value)
    with pytest.raises(ValueError, match="IMMUTABLE"):
        row.save()
    with transaction.atomic(), pytest.raises(IntegrityError, match="IMMUTABLE"):
        GCPAppIdentitySource._unscoped.filter(pk=row.pk).update(**{field: value})
    row.refresh_from_db()
    assert row.unique_id == UID and row.state == "OBSERVED"


def test_bulk_delete_and_state_regression_cannot_erase_remote_history(world):
    from django.db import IntegrityError

    bootstrap(world)
    with transaction.atomic(), pytest.raises(IntegrityError, match="HISTORY_RETAINED"):
        GCPAppIdentitySource._unscoped.all().delete()
    with transaction.atomic(), pytest.raises(IntegrityError, match="STATE_REGRESSION"):
        GCPAppIdentitySource._unscoped.update(state="EVIDENCE")
    assert GCPAppIdentitySource.objects.get().unique_id == UID


@pytest.mark.parametrize("move", ["provider", "organization"])
def test_post_send_ambiguous_create_and_owner_move_retains_unknown_under_original_owner(world, move):
    from astrolift_clusters.models import ProviderPlugin

    # The generated method closure is already bound; inject ambiguity at the actual response seam.
    def changed(name):
        if name != "CreateServiceAccount":
            return
        if move == "provider":
            provider = ProviderPlugin.objects.create(slug="gcp-late", name="late")
            type(world.cluster)._unscoped.filter(pk=world.cluster.pk).update(provider_plugin=provider)
        else:
            type(world.medops_app)._unscoped.filter(pk=world.medops_app.pk).update(
                organization=world.other_org
            )
        raise RuntimeError("PRIVATE_LOST_NATIVE_REPLY")

    world.native.after = changed
    with pytest.raises(IdentitySourceError, match="CREATE_UNKNOWN"):
        bootstrap(world)
    row = GCPAppIdentitySource.objects.get()
    assert row.state == "UNKNOWN" and row.unique_id is None and row.organization_id == world.org.pk
    assert independent_row(str(row.guid))[0:2] == ("UNKNOWN", None) and world.native.writes == 1


def test_current_numeric_declared_project_mismatch_refuses_before_pin_or_create(world):
    world.cluster.provider_config["project_id"] = "999999999999"
    world.cluster.save()
    world.native.declaration = _declaration(world.authority)[0]
    # Allow exactly the configured numeric request in the controlled native protocol.
    with pytest.raises(IdentitySourceError, match="PROJECT_IDENTITY|REGISTERED_CLUSTER|NATIVE_SOURCE"):
        bootstrap(world)
    assert world.native.writes == 0 and not GCPClusterIdentitySource.objects.exists()


def test_nonidentity_settings_and_prediction_role_change_reuse_original_observed_gsa(world):
    result = bootstrap(world)
    old = GCPAppIdentitySource.objects.get()
    world.cluster.provider_config.update(
        metrics={"interval": 60},
        endpoint_prediction_role="projects/fixture-project/roles/OtherEndpointPredict",
    )
    world.cluster.save()
    world.native.role.name = world.cluster.provider_config["endpoint_prediction_role"]
    fresh = original_identity_context(world.authority, native_factory=world.factory)
    assert (
        fresh.identity == result.identity and fresh.source_id == result.source_id and world.native.writes == 1
    )
    current = GCPAppIdentitySource.objects.get()
    assert (
        current.original_sha256 == old.original_sha256
        and current.authority_reference == old.authority_reference
    )


def test_nonidentity_settings_change_during_native_wait_invalidates_current_operation(world):
    def changed(name):
        if name == "GetCluster":
            world.cluster.provider_config["metrics"] = {"interval": 15}
            type(world.cluster)._unscoped.filter(pk=world.cluster.pk).update(
                provider_config=world.cluster.provider_config
            )

    world.native.after = changed
    with pytest.raises(IdentitySourceError, match="CURRENT_REGISTERED_SOURCE"):
        bootstrap(world)
    assert world.native.writes == 0 and not GCPClusterIdentitySource.objects.exists()


def test_native_model_version_drift_after_create_retains_evidence_not_context(world):
    def changed(name):
        if name == "CreateServiceAccount":
            world.native.endpoint.deployed_models[0].model_version_id = "8"

    world.native.after = changed
    with pytest.raises(IdentitySourceError, match="ACCEPTED_NATIVE_SOURCE_CHANGED"):
        bootstrap(world)
    row = GCPAppIdentitySource.objects.get()
    assert row.state == "EVIDENCE" and row.unique_id == UID and world.native.writes == 1
    with pytest.raises(IdentitySourceError, match="PENDING_OPERATION"):
        bootstrap(world)
    assert world.native.writes == 1


def test_last_cluster_observation_changed_incarnation_never_returns_context(world):
    reads = []

    def changed(name):
        if name == "GetCluster":
            reads.append(name)
            if world.native.writes:
                world.native.cluster.id = "recreated-original-name"

    world.native.after = changed
    with pytest.raises(IdentitySourceError, match="INCARNATION|IDENTITY_CHANGED"):
        bootstrap(world)
    assert GCPAppIdentitySource.objects.get().state == "EVIDENCE" and world.native.writes == 1


def test_two_real_http_apps_share_one_concurrent_null_org_original_cluster_pin(world, client, monkeypatch):
    from contextlib import contextmanager
    from types import ModuleType

    from django.http import JsonResponse
    from django.test import override_settings
    from django.urls import path

    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_services.gcp_identity_source import SourceStore
    from astrolift_services.native_identity_authority import capture_app_identity_authority
    from astrolift_services.tests.test_cluster_model_queries_2213 import grant
    from astrolift_services.tests.test_model_connection_2270 import http_token
    from core.permissions import Permission

    type(world.cluster)._unscoped.filter(pk=world.cluster.pk).update(organization=None)
    grant(world, Permission.APP_UPDATE, "APP", world.platform_app.pk)
    env = AppEnvironment.objects.create(
        registered_app=world.platform_app, tenant_cluster=world.cluster, name="production"
    )
    refs = []

    def capture_second(request):
        refs.append(
            capture_app_identity_authority(
                request, environment_guid=env.guid, permission=Permission.APP_UPDATE
            )
        )
        return JsonResponse({"ok": True})

    _, headers = http_token(world, scopes=("read:apps", "write:apps"))
    urls = ModuleType("original_source_test_urls")
    urls.urlpatterns = [path("capture-source/", capture_second)]
    with override_settings(ROOT_URLCONF=urls):
        assert client.get("/capture-source/", **headers).json() == {"ok": True}
    assert len(refs) == 1 and refs[0].app_guid != world.authority.app_guid
    first_physical = _declaration(world.authority)[1]
    assert first_physical == _declaration(refs[0])[1] and first_physical["organization_id"] is None
    native = world.native.port().observe_cluster(current=lambda: None)[1]
    entered, release, failures, first = Event(), Event(), [], []
    locked = SourceStore._locked

    @contextmanager
    def held(store, **kwargs):
        with locked(store, **kwargs) as values:
            if store.authority == world.authority:
                entered.set()
                assert release.wait(10)
            yield values

    monkeypatch.setattr(SourceStore, "_locked", held)

    def create_pin():
        try:
            with source_mutex(world.authority) as store:
                first.append(store.pin(world.operation, first_physical, native))
        except Exception as error:
            failures.append(error)
        finally:
            connections.close_all()

    thread = Thread(target=create_pin)
    thread.start()
    try:
        assert entered.wait(5)
        with source_mutex(refs[0]) as store, pytest.raises(ValueError, match="JOURNAL_BUSY"):
            store.pin(SourceOperation(str(uuid4()), "other-app", "other-run"), first_physical, native)
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not failures
    monkeypatch.setattr(SourceStore, "_locked", locked)
    with source_mutex(refs[0]) as store:
        second = store.pin(SourceOperation(str(uuid4()), "other-app", "other-run"), first_physical, native)
    assert first == [second] and GCPClusterIdentitySource.objects.count() == 1
    assert GCPClusterIdentitySource.objects.get().organization_id is None and world.native.writes == 0


@pytest.mark.parametrize("stage", ["constructor", "before_create", "after_create", "readback"])
def test_cumulative_deadline_survives_reservation_and_retains_only_sent_effect_evidence(
    world, monkeypatch, stage
):
    from types import SimpleNamespace

    now = [0.0]
    monkeypatch.setattr(
        "astrolift_services.gcp_identity_source.time", SimpleNamespace(monotonic=lambda: now[0])
    )
    calls = []

    def response(name):
        calls.append(name)
        if (
            (stage == "before_create" and name == "GetServiceAccount" and not world.native.writes)
            or (stage == "after_create" and name == "CreateServiceAccount")
            or (stage == "readback" and name == "GetServiceAccount" and world.native.writes)
        ):
            now[0] = 121.0

    world.native.after = response
    factory = world.factory

    def delayed(declaration):
        native = factory(declaration)
        if stage == "constructor":
            now[0] = 121.0
        return native

    with pytest.raises(IdentitySourceError, match="SOURCE_DEADLINE_EXCEEDED"):
        bootstrap_original_identity(world.authority, world.operation, native_factory=delayed)
    assert world.native.writes == (stage in {"after_create", "readback"})
    if stage == "constructor":
        assert not calls and not GCPAppIdentitySource.objects.exists()
    else:
        row = GCPAppIdentitySource.objects.get()
        assert row.state == ("UNSENT" if stage == "before_create" else "EVIDENCE")
        assert row.unique_id == (None if stage == "before_create" else UID)
        assert calls[-1] == ("CreateServiceAccount" if stage == "after_create" else "GetServiceAccount")


@pytest.mark.parametrize("stage", ["before_create", "after_create"])
def test_nonidentity_config_drift_after_reservation_cannot_send_or_return_context(world, stage):
    calls = []

    def response(name):
        calls.append(name)
        if (stage == "before_create" and name == "GetServiceAccount" and not world.native.writes) or (
            stage == "after_create" and name == "CreateServiceAccount"
        ):
            world.cluster.auth_config["observability"] = {"enabled": True}
            type(world.cluster)._unscoped.filter(pk=world.cluster.pk).update(
                auth_config=world.cluster.auth_config
            )

    world.native.after = response
    with pytest.raises(IdentitySourceError, match="CURRENT_REGISTERED_SOURCE_CHANGED"):
        bootstrap(world)
    row = GCPAppIdentitySource.objects.get()
    assert row.state == ("UNSENT" if stage == "before_create" else "EVIDENCE")
    assert row.unique_id == (None if stage == "before_create" else UID)
    assert world.native.writes == (stage == "after_create")
    assert calls[-1] == ("GetServiceAccount" if stage == "before_create" else "CreateServiceAccount")


def test_native_replica_availability_convergence_preserves_original_source_identity(world):
    def response(name):
        if name == "CreateServiceAccount":
            world.native.endpoint.deployed_models[0].status.available_replica_count = 2

    world.native.after = response
    result = bootstrap(world)
    assert result.identity.service_account_unique_id == UID
    assert GCPAppIdentitySource.objects.get().state == "OBSERVED" and world.native.writes == 1


def test_read_only_context_budget_expiry_stops_at_held_native_response(world, monkeypatch):
    from types import SimpleNamespace

    bootstrap(world)
    now, reads = [0.0], []
    monkeypatch.setattr(
        "astrolift_services.gcp_identity_source.time", SimpleNamespace(monotonic=lambda: now[0])
    )

    def response(name):
        reads.append(name)
        if name == "GetServiceAccount":
            now[0] = 121.0

    world.native.after = response
    with pytest.raises(IdentitySourceError, match="SOURCE_DEADLINE_EXCEEDED"):
        original_identity_context(world.authority, native_factory=world.factory)
    assert reads[-1] == "GetServiceAccount" and world.native.writes == 1
    assert GCPAppIdentitySource.objects.get().state == "OBSERVED"


def test_read_only_context_complete_current_union_change_stops_after_response(world):
    bootstrap(world)
    reads = []

    def response(name):
        reads.append(name)
        if name == "GetEndpoint":
            from astrolift_lifecycle.models import AppEnvironment

            AppEnvironment._unscoped.filter(pk=world.env.pk).update(
                deploy_config={"identity_source": "changed"}
            )

    world.native.after = response
    with pytest.raises((IdentitySourceError, EndpointAppPlanError), match="CURRENT_COMPLETE_UNION_CHANGED"):
        original_identity_context(world.authority, native_factory=world.factory)
    assert reads[-1] == "GetEndpoint" and world.native.writes == 1
