"""
move_environment_namespace: give a shared environment its own namespace (#2094).

``report_shared_environment_namespaces`` lists the environments that still
overwrite each other. This moves one of them, in steps the operator runs:

    python manage.py move_environment_namespace <org>/<app> <environment>
        Print the plan: the primary that keeps the app namespace, the new
        namespace, and the Secret clean-up will remove. Changes nothing.

    python manage.py move_environment_namespace <org>/<app> <environment> --apply
        Record the new namespace. Nothing on the cluster changes yet.

    Then deploy <environment> (it renders into the new namespace) and redeploy
    the primary (the shared object names in the app namespace are its again).

    python manage.py move_environment_namespace <org>/<app> <environment> --clean-up [--apply]
        Delete <environment>'s literal [env] Secret from the app namespace.
        Refused while a workload there still references it.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from astrolift_lifecycle.services.environment_namespace_move import (
    MoveRefused,
    clean_up_move,
    plan_move,
    record_move,
)


class Command(BaseCommand):
    help = "Move one environment out of a namespace it shares with the app's primary (#2094)."

    def add_arguments(self, parser):
        parser.add_argument("app", help="<organization slug>/<app slug>")
        parser.add_argument("environment", help="Environment name, not the primary")
        parser.add_argument("--clean-up", action="store_true", help="Remove the moved environment's Secret")
        parser.add_argument("--apply", action="store_true", help="Make the change instead of printing it")

    def handle(self, *args, **options):
        organization, _, app = options["app"].partition("/")
        if not organization or not app:
            raise CommandError("app must be <organization slug>/<app slug>")
        environment = options["environment"]
        try:
            if options["clean_up"]:
                secret = clean_up_move(organization, app, environment, apply=options["apply"])
                verb = "Deleted" if options["apply"] else "Would delete"
                self.stdout.write(f"{verb} Secret {secret} from the app namespace.")
                return
            plan = plan_move(organization, app, environment)
        except MoveRefused as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(
            f"{plan.organization}/{plan.app} on {plan.cluster}: {plan.environment} moves from "
            f"{plan.from_namespace} to {plan.to_namespace}; {plan.primary} keeps {plan.from_namespace}."
        )
        if not options["apply"]:
            self.stdout.write("Dry run. Re-run with --apply to record the new namespace.")
            return
        try:
            record_move(plan)
        except MoveRefused as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(
            f"Recorded. Next: deploy {plan.environment}, redeploy {plan.primary}, then run this "
            f"with --clean-up to delete {plan.literal_secret} from {plan.from_namespace}."
        )
