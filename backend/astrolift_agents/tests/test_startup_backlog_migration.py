import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from astrolift_agents.models import AgentTask
from astrolift_identity.models import Organization

APP = "astrolift_agents"
BASE = "0039_backfill_agent_spec_and_box_owner"
STARTUP = "0040_agent_startup_diagnostic"
BACKLOG = "0040_agent_task_backlog"
MERGE = "0041_merge_startup_and_backlog"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("starting_migration", [BASE, STARTUP, BACKLOG])
def test_combined_upgrade_preserves_tasks_from_either_branch(starting_migration):
    organization = Organization.objects.create(name="Migration integration", slug="migration-integration")
    diagnostic = {"reason": "Unschedulable", "message": "Insufficient cpu"}
    backlog = {"revision": 3, "items": []}
    try:
        MigrationExecutor(connection).migrate([(APP, BASE)])
        executor = MigrationExecutor(connection)
        executor.migrate([(APP, starting_migration)])
        historical_task = executor.loader.project_state([(APP, starting_migration)]).apps.get_model(
            APP, "AgentTask"
        )
        fields = {}
        if starting_migration == STARTUP:
            fields["startup_diagnostic"] = diagnostic
        if starting_migration == BACKLOG:
            fields["backlog_snapshot"] = backlog
        task = historical_task.objects.create(organization_id=organization.pk, **fields)

        MigrationExecutor(connection).migrate([(APP, MERGE)])
        restored = AgentTask.objects.get(pk=task.pk)
        assert restored.guid == task.guid
        assert restored.organization_id == organization.pk
        assert restored.startup_diagnostic == (diagnostic if starting_migration == STARTUP else {})
        assert restored.backlog_snapshot == (backlog if starting_migration == BACKLOG else None)

        # Reversing only the merge must leave both additive schemas and data intact.
        MigrationExecutor(connection).migrate([(APP, STARTUP), (APP, BACKLOG)])
        restored.refresh_from_db()
        assert restored.startup_diagnostic == (diagnostic if starting_migration == STARTUP else {})
        assert restored.backlog_snapshot == (backlog if starting_migration == BACKLOG else None)
    finally:
        MigrationExecutor(connection).migrate([(APP, MERGE)])
