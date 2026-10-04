"""Independent PG transactions exercise actual workload writer/IAM lock order."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import DatabaseError, close_old_connections, connection, transaction

from astrolift_lifecycle.action_preconditions import locked_workload
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import Workload
from astrolift_services.gcp_workload_identity_journal import JournalError, journal_mutex
from astrolift_services.models import GCPWorkloadIdentityJournal
from astrolift_services.tests.test_gcp_workload_identity_journal_2278 import admitted, submit
from astrolift_services.tests.test_gcp_workload_identity_journal_2278 import world as journal_world
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world():
    return journal_world.__wrapped__()


def test_actual_workload_app_first_writer_and_cluster_first_journal_refuse_wait_then_retry(world):
    env = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=world.cluster, name="test"
    )
    workload = Workload.objects.create(
        registered_app=world.medops_app, name="api", slug="api", kind="deployment"
    )
    app_held, allow_cluster = Event(), Event()
    checkpoints = []

    def pause_cluster(execute, sql, params, many, context):
        if "FOR UPDATE" in sql and '"astrolift_clusters_tenantcluster"' in sql:
            app_held.set()
            assert allow_cluster.wait(5)
        return execute(sql, params, many, context)

    def writer():
        close_old_connections()
        try:
            with tenant_context(TenantContext(organization_id=world.org.pk)):
                with connection.execute_wrapper(pause_cluster):
                    with locked_workload(workload.guid, environment_guid=env.guid) as (current, selected):
                        assert current.pk == workload.pk and selected.pk == env.pk
                        current.replicas = 2
                        current.save()
        finally:
            close_old_connections()

    def reserve():
        close_old_connections()
        try:
            with journal_mutex(world.target) as store:
                try:
                    store.reserve(world.operation, checkpoint=lambda context: checkpoints.append(context))
                except JournalError as error:
                    assert not connection.in_atomic_block and connection.get_autocommit()
                    assert not connection.needs_rollback
                    assert not GCPWorkloadIdentityJournal.objects.exists()
                    return str(error)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(writer)
        assert app_held.wait(5)
        pending = pool.submit(reserve)
        try:
            assert pending.result(timeout=1) == "JOURNAL_BUSY"
            assert checkpoints == []
        finally:
            allow_cluster.set()
        holder.result(timeout=5)
    workload.refresh_from_db()
    assert workload.replicas == 2
    with journal_mutex(world.target) as store:
        reserved = store.reserve(world.operation, checkpoint=admitted)
        store.validate_current(reserved, checkpoint=admitted)


@pytest.mark.parametrize(
    "target", ["org", "cluster", "provider", "medops", "medops_project", "medops_app", "journal"]
)
@pytest.mark.parametrize("state", ["reserved", "sent"])
def test_every_parent_and_journal_row_collision_rolls_back_before_busy_and_preserves_receipt(
    world, target, state
):
    with journal_mutex(world.target) as store:
        reserved = store.reserve(world.operation, checkpoint=admitted)
        if state == "sent":
            ledger, _, _ = submit(store, reserved, world)
    selected = (
        GCPWorkloadIdentityJournal.objects.get(guid=reserved.journal_id)
        if target == "journal"
        else getattr(world, target)
    )
    before = GCPWorkloadIdentityJournal.objects.values().get(guid=reserved.journal_id)
    held, release = Event(), Event()

    def holder():
        close_old_connections()
        try:
            with transaction.atomic():
                type(selected)._base_manager.select_for_update().get(pk=selected.pk)
                held.set()
                assert release.wait(5)
        finally:
            close_old_connections()

    def contender():
        close_old_connections()
        try:
            with journal_mutex(world.target) as store:
                with pytest.raises(JournalError, match="^JOURNAL_BUSY$"):
                    store.validate_current(reserved, checkpoint=admitted)
                assert not connection.in_atomic_block and not connection.needs_rollback
                assert GCPWorkloadIdentityJournal.objects.values().get(guid=reserved.journal_id) == before
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        original = pool.submit(holder)
        assert held.wait(5)
        attempt = pool.submit(contender)
        try:
            attempt.result(timeout=1)
        finally:
            release.set()
        original.result(timeout=5)
    with journal_mutex(world.target) as store:
        store.validate_current(reserved, checkpoint=admitted)
        assert store.reserve(world.operation, checkpoint=admitted) == reserved
        if state == "sent":
            assert store.read(reserved, checkpoint=admitted) == ledger
            assert GCPWorkloadIdentityJournal.objects.values().get(guid=reserved.journal_id) == before


def test_non_lock_database_failure_is_not_misclassified_as_contention(world):
    def fail(context):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 / 0")

    with journal_mutex(world.target) as store:
        with pytest.raises(DatabaseError):
            store.reserve(world.operation, checkpoint=fail)
        assert not connection.in_atomic_block and not connection.needs_rollback
    assert not GCPWorkloadIdentityJournal.objects.exists()
