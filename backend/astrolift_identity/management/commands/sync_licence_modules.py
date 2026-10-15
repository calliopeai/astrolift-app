"""Turn off, in every organization, the modules the signed licence withholds (#1879).

Run on every container start (``startup.py``): the licence reaches Astrolift
through the task's environment, resolved at task start, so a start is when it
changes. A grant never turns a module on. Idempotent, and a no-op when the
licence has no say.
"""

from django.core.management.base import BaseCommand

from astrolift_identity.org_modules import sync_from_licence


class Command(BaseCommand):
    help = "Turn off the OrganizationModule rows the install's signed licence withholds."

    def handle(self, *args, **options):
        changes = sync_from_licence()
        for org_id, key, enabled in changes:
            self.stdout.write(f"org {org_id}: {key} {'on' if enabled else 'off'} (licence)")
        self.stdout.write(f"{len(changes)} module change(s) from the licence")
