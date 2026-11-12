"""Prepare one confidential task inside the on-demand preparation task (#2678)."""

from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Prepare the confidential image and Pod for the task named by "
        "ASTROLIFT_CONFIDENTIAL_PREPARATION. Started by the worker, never by hand."
    )

    def handle(self, *args, **options):
        from astrolift_clusters import nitro_preparation

        guid, _, run = os.environ.get(nitro_preparation.MARKER, "").partition(":")
        if not guid or not run:
            raise CommandError("This command runs only inside the confidential preparation task")
        if not nitro_preparation.perform(guid, run):
            raise CommandError("Confidential image preparation did not complete")
