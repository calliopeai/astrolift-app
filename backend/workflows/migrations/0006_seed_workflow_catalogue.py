"""Seed the platform workflow catalogue (spec 40 §4).

Idempotent / re-runnable: parses the bundled ``workflows/catalogue/*.toml``
manifests and upserts each as a null-org, read-only ``WorkflowDefinition``
+ its stages, so a fresh deploy ships the v0.1 starter set. Re-running
(via the migration or the ``seed_workflow_catalogue`` command) is a no-op
beyond in-place updates. The reverse removes the null-org catalogue rows.
"""

from __future__ import annotations

from django.db import migrations


def seed_catalogue(apps, schema_editor):
    WorkflowDefinition = apps.get_model("workflows", "WorkflowDefinition")
    WorkflowStage = apps.get_model("workflows", "WorkflowStage")

    # Imported at runtime (not load time) so the serializer + bundled TOML
    # parse against the live app registry. Uses the historical model classes
    # passed in, so a later model change can't break this migration's upsert.
    from workflows.catalogue_seed import seed_workflow_catalogue

    seed_workflow_catalogue(WorkflowDefinition, WorkflowStage)


def unseed_catalogue(apps, schema_editor):
    WorkflowDefinition = apps.get_model("workflows", "WorkflowDefinition")
    WorkflowStage = apps.get_model("workflows", "WorkflowStage")

    globals_qs = WorkflowDefinition.objects.filter(organization__isnull=True)
    WorkflowStage.objects.filter(definition__in=globals_qs).delete()
    globals_qs.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("workflows", "0005_workflowstage_prompt_approvers_fanout"),
    ]

    operations = [
        migrations.RunPython(seed_catalogue, unseed_catalogue),
    ]
