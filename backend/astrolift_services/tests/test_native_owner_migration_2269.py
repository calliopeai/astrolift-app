"""Execute the real owner replacement forwards/backwards on isolated PostgreSQL.

Native rows are disposable fixtures only. Retained native rows, including soft
deletions, prevent production schema rollback; never delete customer history to
make a downgrade fit. The constraint is structural, not API admission proof.
"""

import pytest
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from astrolift_lifecycle.models import AppEnvironment
from core.tests.utils.scope_world import ScopeWorld, make_cluster

_APP = "astrolift_services"
_BEFORE = (_APP, "0036_model_connection_requests")
_AFTER = (_APP, "0037_native_bedrock_connection_owner")
_CONSTRAINT = "msvc_exactly_one_owner_scope"


def _constraint_definition(table):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = %s::regclass AND conname = %s",
            [table, _CONSTRAINT],
        )
        rows = cursor.fetchall()
    assert len(rows) == 1
    return rows[0][0]


def _snapshot(service):
    return list(service._base_manager.order_by("pk").values())


def _columns(table):
    with connection.cursor() as cursor:
        return connection.introspection.get_table_description(cursor, table)


def _refuse(service, **fields):
    with pytest.raises(IntegrityError, match=_CONSTRAINT), transaction.atomic():
        service._base_manager.create(name="invalid-native-fixture", **fields)


@pytest.mark.django_db(transaction=True)
def test_real_native_owner_migration_preserves_rows_and_replaces_only_reviewed_constraint():
    assert connection.vendor == "postgresql", "owner-check acceptance requires PostgreSQL"
    executor = MigrationExecutor(connection)
    original_targets = executor.loader.graph.leaf_nodes()
    try:
        # Later journal migrations must enforce their own reverse guards. This
        # isolated test starts with no journals and never removes their rows.
        executor.migrate([_AFTER])
        executor = MigrationExecutor(connection)
        assert [
            (migration.name, backwards) for migration, backwards in executor.migration_plan([_BEFORE])
        ] == [(_AFTER[1], True)]
        executor.migrate([_BEFORE])
        old_apps = MigrationExecutor(connection).loader.project_state([_BEFORE]).apps
        service = old_apps.get_model(_APP, "ManagedService")
        world = ScopeWorld("native-owner-migration-2269")
        cluster = make_cluster(world, "native-owner-migration-2269")
        environment = AppEnvironment.objects.create(
            registered_app=world.medops_app, tenant_cluster=cluster, name="production"
        )
        app_owner = {"registered_app_id": world.medops_app.pk, "app_environment_id": environment.pk}
        project_owner = {"project_id": world.medops_project.pk, "tenant_cluster_id": cluster.pk}
        cluster_owner = {"organization_id": world.org.pk, "tenant_cluster_id": cluster.pk}
        for name, owner, kind, variant, config, deleted_at in (
            ("app", app_owner, "redis", "redis", {"retained": "app"}, None),
            ("project", project_owner, "postgres", "rds", {"retained": "project"}, None),
            ("cluster", cluster_owner, "model_endpoint", "vllm", {"model_source": "huggingface"}, None),
            (
                "cluster-history",
                cluster_owner,
                "model_endpoint",
                "vllm",
                {"retained": "history"},
                timezone.now(),
            ),
            ("legacy-app-native", app_owner, "model_endpoint", "bedrock", {"mode": "provisioned"}, None),
            (
                "legacy-project-native",
                project_owner,
                "model_endpoint",
                "bedrock",
                {"mode": "provisioned"},
                None,
            ),
        ):
            service._base_manager.create(
                name=name,
                kind=kind,
                variant=variant,
                config=config,
                applied_config={"retained": name},
                backend_ref=f"fixture-{name}",
                version=7,
                deleted_at=deleted_at,
                **owner,
            )
        before = _snapshot(service)
        assert len(before) == 6 and sum(row["deleted_at"] is not None for row in before) == 1
        original_constraint = _constraint_definition(service._meta.db_table)
        original_columns = _columns(service._meta.db_table)
        original_field = service._meta.get_field("organization")
        assert original_field.null and original_field.remote_field.on_delete is models.PROTECT
        guarded = {"existing_connection_only": True, "model_source": "bedrock"}
        _refuse(service, kind="model_endpoint", variant="bedrock", config=guarded, **cluster_owner)

        executor = MigrationExecutor(connection)
        assert [
            (migration.name, backwards) for migration, backwards in executor.migration_plan([_AFTER])
        ] == [(_AFTER[1], False)]
        executor.migrate([_AFTER])
        assert _AFTER in MigrationRecorder(connection).applied_migrations()
        current = (
            MigrationExecutor(connection)
            .loader.project_state([_AFTER])
            .apps.get_model(_APP, "ManagedService")
        )
        assert _snapshot(current) == before
        assert _columns(current._meta.db_table) == original_columns
        new_field = current._meta.get_field("organization")
        assert new_field.null and new_field.remote_field.on_delete is models.PROTECT
        assert new_field.help_text != original_field.help_text
        forward_constraint = _constraint_definition(current._meta.db_table)
        assert forward_constraint != original_constraint

        native = current._base_manager.create(
            name="guarded-native-fixture",
            kind="model_endpoint",
            variant="bedrock",
            config=guarded,
            **cluster_owner,
        )
        for invalid_config in (
            {},
            {"existing_connection_only": True},
            {"model_source": "bedrock"},
            {"existing_connection_only": False, "model_source": "bedrock"},
            {"existing_connection_only": "true", "model_source": "bedrock"},
            {"existing_connection_only": None, "model_source": "bedrock"},
            {"existing_connection_only": True, "model_source": "huggingface"},
        ):
            _refuse(current, kind="model_endpoint", variant="bedrock", config=invalid_config, **cluster_owner)
        for invalid_owner in (
            {},
            {"organization_id": world.org.pk},
            {"tenant_cluster_id": cluster.pk},
            {**cluster_owner, "project_id": world.medops_project.pk},
            {**cluster_owner, **app_owner},
            {"registered_app_id": world.medops_app.pk},
            {"app_environment_id": environment.pk},
        ):
            _refuse(current, kind="model_endpoint", variant="bedrock", config=guarded, **invalid_owner)
        for invalid_kind, invalid_variant in (
            ("redis", "bedrock"),
            ("model_endpoint", "vertex_ai"),
            ("model_endpoint", "azure_foundry"),
        ):
            _refuse(current, kind=invalid_kind, variant=invalid_variant, config=guarded, **cluster_owner)

        # Both live and tombstoned native rows refuse reverse DDL atomically.
        for deleted_at in (None, timezone.now()):
            current._base_manager.filter(pk=native.pk).update(deleted_at=deleted_at)
            before_refusal = _snapshot(current)
            with pytest.raises(IntegrityError, match=_CONSTRAINT):
                MigrationExecutor(connection).migrate([_BEFORE])
            assert _AFTER in MigrationRecorder(connection).applied_migrations()
            assert current._base_manager.filter(pk=native.pk).exists()
            assert _snapshot(current) == before_refusal
            assert _constraint_definition(current._meta.db_table) == forward_constraint
            assert [row for row in _snapshot(current) if row["id"] != native.pk] == before

        # Only this exact disposable native fixture is removed, not retained
        # production history or baseline app/project/vLLM rows.
        assert current._base_manager.filter(pk=native.pk).delete()[0] == 1
        MigrationExecutor(connection).migrate([_BEFORE])
        assert _AFTER not in MigrationRecorder(connection).applied_migrations()
        assert _snapshot(service) == before
        assert _constraint_definition(service._meta.db_table) == original_constraint
        assert _columns(service._meta.db_table) == original_columns
        _refuse(service, kind="model_endpoint", variant="bedrock", config=guarded, **cluster_owner)
        assert _snapshot(service) == before
    finally:
        MigrationExecutor(connection).migrate(original_targets)
    assert all(target in MigrationRecorder(connection).applied_migrations() for target in original_targets)
