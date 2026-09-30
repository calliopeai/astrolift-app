"""Preview or apply an explicitly reviewed owner namespace migration (#2102)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from astrolift_agents.management.commands.audit_agent_secret_namespace import Command as AuditCommand
from astrolift_agents.services.secret_owner_migration import apply_plan, build_plan, plan_digest


class Command(BaseCommand):
    help = "Preview legacy agent secret owner copies; apply a reviewed plan with writers paused. Never deletes sources."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Organization slug or guid.")
        parser.add_argument(
            "--plan-file", required=True, help="Metadata-only preview output or reviewed apply input."
        )
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm-plan", default="", help="SHA256 printed by the reviewed preview.")
        parser.add_argument(
            "--operator", default="", help="Active platform operator user ID, required to apply."
        )
        parser.add_argument(
            "--writers-paused",
            action="store_true",
            help="Attest all API, worker and external secret writers are paused.",
        )

    def handle(self, *args, **options):
        organization = AuditCommand()._organization(options["org"])
        path = Path(options["plan_file"])
        try:
            if options["apply"]:
                if not options["confirm_plan"] or not options["operator"] or not options["writers_paused"]:
                    raise CommandError("apply requires --confirm-plan, --operator and --writers-paused")
                plan = json.loads(path.read_text())
                if plan_digest(plan) != options["confirm_plan"]:
                    raise CommandError("reviewed plan digest does not match")
                operator = get_user_model().objects.filter(pk=options["operator"]).first()
                completed = apply_plan(organization, plan, operator=operator, writers_paused=True)
                self.stdout.write(f"Completed {completed} spec(s); source secrets retained")
            else:
                plan = build_plan(organization)
                # Refuse to replace an earlier review artifact, and keep even
                # secret metadata private on a shared operator host.
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w") as output:
                    json.dump(plan, output, indent=2, sort_keys=True)
                    output.write("\n")
                self.stdout.write(f"Preview {len(plan['specs'])} spec(s); plan SHA256 {plan_digest(plan)}")
                self.stdout.write(
                    f"Legacy locations used by multiple owners: {len(plan['sources_used_by_multiple_owners'])}; review copy ownership explicitly"
                )
        except CommandError:
            raise
        except Exception:
            # Never reflect store responses or malformed input containing values.
            raise CommandError(
                "owner migration refused or failed; inspect metadata and provider audit logs"
            ) from None
