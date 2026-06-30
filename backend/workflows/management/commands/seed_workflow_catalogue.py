"""``manage.py seed_workflow_catalogue``

Upsert the platform-global workflow catalogue (spec 40 §4) — the v0.1
starter definitions every org can clone or configure a Workflow from.

Parses the bundled ``workflows/catalogue/*.toml`` manifests via the #973
serializer and upserts each as a null-org, read-only ``WorkflowDefinition``
+ its ordered ``WorkflowStage`` rows. Idempotent / upsert-style: safe to
run on every container start. Superuser/seed path may write globals (§2.1).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from workflows.catalogue_seed import seed_workflow_catalogue
from workflows.models import WorkflowDefinition, WorkflowStage


class Command(BaseCommand):
    help = "Upsert the platform-global workflow catalogue (org=None, read-only)."

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        result = seed_workflow_catalogue(WorkflowDefinition, WorkflowStage)
        self.stdout.write(self.style.SUCCESS(f"seed_workflow_catalogue: {result.summary()}"))
