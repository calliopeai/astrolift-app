"""Delete agent messages past their organization's retention (#2434)."""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Delete agent messages, with their recipients and replies, older than each "
        "organization's audit retention (Organization.audit_log_retention_days)."
    )

    def handle(self, *args, **options):
        from astrolift_agents.services.agent_messages import purge_expired_messages

        removed = purge_expired_messages()
        self.stdout.write(f"purged {removed} agent message(s)")
