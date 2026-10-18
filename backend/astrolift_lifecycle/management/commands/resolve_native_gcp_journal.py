"""
resolve_native_gcp_journal — settle a native GKE journal a lost send left unresolved (#2278).

Usage:
    python manage.py resolve_native_gcp_journal --app <app guid> --cluster <cluster guid>
    python manage.py resolve_native_gcp_journal --app <guid> --cluster <guid> \\
        --operator <email> --attestation "<what you checked>" --apply

Without ``--apply`` it lists the unresolved journals and what each is waiting
on. With it, every unresolved journal of the app on that cluster is settled in
one transaction, or none is. Nothing calls the cloud or the cluster: before
applying, check that the old worker is gone and look at the namespace, the
KSA and the Endpoint policies yourself. The printed dropped intents name what
an unresolved send may have created; a stray namespace or KSA must be removed
before the next deploy, which refuses to adopt it.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Settle native GKE journals left SENT/UNKNOWN or incomplete by a lost worker (#2278)."

    def add_arguments(self, parser):
        parser.add_argument("--app", required=True, help="RegisteredApp guid")
        parser.add_argument("--cluster", required=True, help="TenantCluster guid")
        parser.add_argument("--operator", default="", help="Email of the active user settling the journals")
        parser.add_argument("--attestation", default="", help="What the operator checked (20-500 characters)")
        parser.add_argument("--apply", action="store_true", default=False, help="Settle; default lists only")

    def handle(self, *args, **options):
        from astrolift_clusters.models import TenantCluster
        from astrolift_registry.models import RegisteredApp
        from astrolift_services.gcp_native_journal_resolution import (
            JournalResolutionError,
            resolve_native_journals,
            unresolved_journals,
        )

        app = RegisteredApp.all_objects.filter(guid=options["app"]).first()
        cluster = TenantCluster.all_objects.filter(guid=options["cluster"]).first()
        if app is None or cluster is None:
            raise CommandError("app or cluster not found")
        rows = unresolved_journals(app, cluster)
        if not rows:
            self.stdout.write("No unresolved native journals.")
            return
        for row in rows:
            self.stdout.write(
                f"{type(row).__name__} {row.guid} state={row.state} operation={row.operation_id} "
                f"generation={row.generation} updated_at={row.updated_at.isoformat()}"
            )
        if not options["apply"]:
            self.stdout.write("Listed only. Re-run with --operator, --attestation and --apply to settle.")
            return
        user = (
            get_user_model().objects.filter(email__iexact=options["operator"].strip(), is_active=True).first()
        )
        try:
            resolved = resolve_native_journals(app, cluster, user=user, attestation=options["attestation"])
        except JournalResolutionError as exc:
            raise CommandError(str(exc)) from None
        for row in resolved:
            self.stdout.write(f"RESOLVED {row.kind} {row.journal_guid} (was {row.prior_state})")
            for dropped in row.unresolved:
                self.stdout.write(f"  dropped {dropped}")
