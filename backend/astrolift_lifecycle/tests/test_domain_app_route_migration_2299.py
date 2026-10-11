"""Real PostgreSQL full-graph restoration and retained-history rollback refusal."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

from astrolift_lifecycle.models import ManagedDomainAppBinding

from .test_domain_app_route_store_2299 import admitted as admitted_fixture  # noqa: F401
from .test_domain_app_route_store_2299 import (
    reserve,
    world,  # noqa: F401
)

admitted = admitted_fixture

pytestmark = pytest.mark.django_db(transaction=True)
LEAF = ("astrolift_lifecycle", "0047_managed_domain_app_binding")
PREVIOUS = ("astrolift_lifecycle", "0046_builder_artifact")


def test_empty_journal_actual_reverse_forward_restores_whole_graph():
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    initial = set(MigrationRecorder(connection).applied_migrations())
    assert LEAF in initial and not ManagedDomainAppBinding.all_objects.exists()
    try:
        executor.migrate([PREVIOUS])
        assert LEAF not in MigrationRecorder(connection).applied_migrations()
        assert "astrolift_lifecycle_manageddomainappbinding" not in connection.introspection.table_names()
    finally:
        MigrationExecutor(connection).migrate(targets)
    assert set(MigrationRecorder(connection).applied_migrations()) == initial
    assert not ManagedDomainAppBinding.all_objects.exists()


def test_retained_journal_reverse_refuses_rows_and_migration_record_intact(admitted):
    row = reserve(admitted)
    row.soft_delete()
    original = (row.guid, row.authority_reference, row.target_snapshot, row.request_id)
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    initial = set(MigrationRecorder(connection).applied_migrations())
    try:
        with pytest.raises(RuntimeError, match="empty retained"):
            executor.migrate([PREVIOUS])
        assert LEAF in MigrationRecorder(connection).applied_migrations()
        restored = ManagedDomainAppBinding.all_objects.get(pk=row.pk)
        assert (
            restored.guid,
            restored.authority_reference,
            restored.target_snapshot,
            restored.request_id,
        ) == original
        assert restored.deleted_at is not None
    finally:
        MigrationExecutor(connection).migrate(targets)
    assert set(MigrationRecorder(connection).applied_migrations()) == initial
    assert ManagedDomainAppBinding.all_objects.get(pk=row.pk).guid == row.guid
