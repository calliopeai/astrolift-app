"""
``manage.py issue_scim_token``

Issues (or rotates, or revokes) the organization's SCIM provisioning
credential — the producer for ``Organization.scim_token_hash``, which
until now no code path wrote, leaving ``/api/scim/v2/`` with nothing to
authenticate against.

    manage.py issue_scim_token --org acme
    manage.py issue_scim_token --org acme --revoke

Issuing turns ``scim_enabled`` on and prints the plaintext token once;
only its SHA-256 is stored, so a lost token is re-issued (rotated), not
recovered. Re-running rotates: the previous token stops authenticating
the moment the new digest lands. ``--revoke`` clears the digest and
turns ``scim_enabled`` back off, which stops provisioning outright.

A command rather than a mutation because this is a per-install operator
action, alongside ``bootstrap_admin`` and ``bootstrap_idp``: it is done
once when the IdP is wired up, and the plaintext belongs in the
operator's terminal rather than in a GraphQL response body that a
browser would cache.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from astrolift_identity.models import Organization
from astrolift_identity.scim_views import SCIM_BASE_PATH, mint_scim_token


class Command(BaseCommand):
    help = "Issue, rotate or revoke an organization's SCIM provisioning token."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            required=True,
            help="Organization slug the token provisions into.",
        )
        parser.add_argument(
            "--revoke",
            action="store_true",
            help="Clear the stored token and turn SCIM off for the org.",
        )

    def handle(self, *args, **options):
        slug = (options["org"] or "").strip()
        org = Organization.objects.filter(slug=slug).first()
        if org is None:
            raise CommandError(f"no organization with slug {slug!r}")

        if options["revoke"]:
            org.scim_token_hash = ""
            org.scim_enabled = False
            org.save(update_fields=["scim_token_hash", "scim_enabled", "updated_at", "version"])
            self.stdout.write(self.style.SUCCESS(f"SCIM disabled for {org.slug}; token revoked"))
            return

        rotated = bool(org.scim_token_hash)
        plaintext, digest = mint_scim_token()
        org.scim_token_hash = digest
        org.scim_enabled = True
        org.save(update_fields=["scim_token_hash", "scim_enabled", "updated_at", "version"])

        verb = "rotated" if rotated else "issued"
        self.stdout.write(self.style.SUCCESS(f"SCIM token {verb} for {org.slug}"))
        self.stdout.write(f"  base url: {SCIM_BASE_PATH}")
        self.stdout.write(f"  token:    {plaintext}")
        self.stdout.write("Copy the token now; only its hash is stored.")
