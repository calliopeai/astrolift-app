"""Recover a bounded batch of durable NVKE dispatches after an outage."""

from django.core.management.base import BaseCommand, CommandError

from astrolift_clusters.nvke_operations import sweep_outbox


class Command(BaseCommand):
    help = "Recover original NVKE operation dispatches (never replay accepted writes)"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= 100:
            raise CommandError("limit must be between 1 and 100")
        count = sweep_outbox(limit=limit)
        self.stdout.write(f"Examined {count} NVKE operation dispatches")
