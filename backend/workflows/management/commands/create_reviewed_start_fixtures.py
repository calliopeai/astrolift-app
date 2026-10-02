"""Create explicitly targeted disposable workflow definitions for client acceptance."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from astrolift_identity.models import Organization
from core.run_input_contract import digest, no_input_schema
from workflows.models import WorkflowDefinition, WorkflowStage
from workflows.reviewed_starts import definition_revision


class Command(BaseCommand):
    help = "Prepare completed and failed disposable reviewed-start definitions in an explicitly named org"

    def add_arguments(self, parser):
        parser.add_argument("--organization", required=True, help="Exact organization GUID")
        parser.add_argument("--prefix", default="disposable-reviewed-start")
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Create the reviewed fixtures; default only reports the plan",
        )

    def handle(self, *args, **options):
        org = Organization.objects.filter(guid=options["organization"]).first()
        if org is None:
            raise CommandError("Organization not found")
        prefix = options["prefix"]
        if (
            not prefix.startswith("disposable-")
            or len(prefix) > 70
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in prefix)
        ):
            raise CommandError("Use a disposable- prefix with at most 70 lowercase slug characters")
        slugs = [f"{prefix}-completed", f"{prefix}-failed"]
        if not options["execute"]:
            self.stdout.write(json.dumps({"organization": str(org.guid), "create": slugs, "execute": False}))
            return
        proof = []
        with transaction.atomic():
            for outcome, slug in zip(("completed", "failed"), slugs, strict=True):
                if WorkflowDefinition.objects.filter(organization=org, slug=slug).exists():
                    raise CommandError("The disposable prefix already exists; use a fresh prefix")
                definition = WorkflowDefinition.objects.create(
                    organization=org,
                    name=f"Disposable reviewed start ({outcome})",
                    slug=slug,
                    model_label="",
                    input_schema=no_input_schema(),
                )
                WorkflowStage.objects.create(
                    definition=definition,
                    slug=f"{slug}-stage",
                    order=0,
                    kind="checkpoint" if outcome == "completed" else "human_gate",
                    timeout_seconds=1,
                )
                proof.append(
                    {
                        "definitionId": str(definition.guid),
                        "expectedRevision": definition_revision(definition),
                        "expectedInputSchemaDigest": digest(definition.input_schema),
                        "expectedOutcome": outcome,
                    }
                )
        self.stdout.write(json.dumps({"organization": str(org.guid), "fixtures": proof}))
