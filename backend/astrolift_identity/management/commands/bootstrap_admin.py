"""
``manage.py bootstrap_admin``

Brings a fresh production install to a usable state in one shot:

* Organization (default slug ``acme`` — override per install)
* A pending FirstAdministrator naming the operator's email (installer #503)

No user, password or role binding is created here. The first sign-in whose
provider-verified email matches claims the administrator
(``astrolift_identity.first_admin``): the account, its ``org_owner`` binding
and its Member row are made then, and audited. Once claimed, later runs do
nothing with the email: removing that administrator or changing the variable
never re-creates or re-grants anyone. An organization that already has an
owner (an install bootstrapped before the claim existed) is recorded as
claimed for the same reason.

The system roles (``org_owner``, ``org_admin``, ``team_developer``,
``app_viewer``, …) are already upserted by data migration
``astrolift_identity.0002_system_roles``; this command does NOT
re-define roles; the claim binds the first human to ``org_owner``.

It runs on every container start. Re-runnable: every step is upsert-style.
The claim needs a sign-in through the configured provider (OIDC, SAML,
Cognito); a local-account install makes its first owner by hand.

Inputs (CLI flag overrides env var):

  --email        ASTROLIFT_ADMIN_EMAIL
  --org          ASTROLIFT_ORG_SLUG          (default 'acme')
  --org-name     ASTROLIFT_ORG_NAME          (default = slug.title())
  --org-website  ASTROLIFT_ORG_WEBSITE       (optional; left blank if omitted)
  --is-superuser ASTROLIFT_ADMIN_SUPERUSER   ('true'/'1' to enable; applied
                                              at the claim)

Why a flag *and* an env var: the env-var form makes this safe to
invoke from a deploy script without baking secrets into shell
history; the flag form is convenient for one-off ops.
"""

from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from astrolift_identity.first_admin import normalise
from astrolift_identity.models import (
    FirstAdministrator,
    Organization,
    Role,
    RoleBinding,
)


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in {"1", "true", "yes", "y", "on"}


class Command(BaseCommand):
    help = "Bootstrap a fresh install: the org and its pending first administrator."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=_env("ASTROLIFT_ADMIN_EMAIL"))
        # No fallback on slug or name — production installs must pick a real
        # org name; the operator finds out at deploy time, not after a
        # demo screen ships with "Acme".
        parser.add_argument("--org", default=_env("ASTROLIFT_ORG_SLUG"))
        parser.add_argument("--org-name", default=_env("ASTROLIFT_ORG_NAME"))
        parser.add_argument("--org-website", default=_env("ASTROLIFT_ORG_WEBSITE"))
        parser.add_argument(
            "--is-superuser",
            action="store_true",
            default=_truthy(_env("ASTROLIFT_ADMIN_SUPERUSER")),
            help=(
                "Mark the bootstrap user as a Django superuser. "
                "Recommended only for single-operator installs; "
                "leave off and rely on the org_owner RoleBinding for "
                "multi-operator deployments."
            ),
        )

    def handle(
        self,
        *,
        email: str | None,
        org: str | None,
        org_name: str | None,
        org_website: str | None,
        is_superuser: bool,
        **opts,
    ):
        if not email:
            raise CommandError(
                "missing --email (or ASTROLIFT_ADMIN_EMAIL). "
                "Provide the operator email; this is who gets org_owner."
            )
        if not org:
            raise CommandError(
                "missing --org (or ASTROLIFT_ORG_SLUG). "
                "Pick a slug for your organization (e.g. 'acmecorp'). "
                "This is what every URL and audit row scopes to."
            )
        if not org_name:
            raise CommandError(
                "missing --org-name (or ASTROLIFT_ORG_NAME). "
                "Pick a display name for your organization "
                "(e.g. 'Acme Corp'). This is what users see in the "
                "header and on every page."
            )

        with transaction.atomic():
            org_row, org_created = Organization.objects.get_or_create(
                slug=org,
                defaults={
                    "name": org_name or org.title(),
                    # Use the provided website, or leave blank — never store a
                    # placeholder like https://{org}.example that leaks into UI.
                    #
                    # Blank is "", not None. Organization.website is
                    # `URLField(blank=True, default="")`, which is NOT NULL at
                    # the database: blank means an empty string, and only the
                    # form layer reads `blank`. Passing None sent an INSERT with
                    # a null and Postgres refused it --
                    # `NotNullViolation: null value in column "website"` -- so
                    # bootstrap_admin could not create an Organization on a
                    # fresh database at all. bootstrap_idp runs next and binds
                    # the IdP to that org, so it failed too ("organization with
                    # slug=... does not exist"), and the install came up with
                    # active_idp.json answering {"kind": "local"} however
                    # completely its identity variables were set.
                    "website": org_website or "",
                },
            )
            update_fields: list[str] = []
            if org_name and org_row.name != org_name:
                org_row.name = org_name
                update_fields.append("name")
            if org_website and org_row.website != org_website:
                org_row.website = org_website
                update_fields.append("website")
            if update_fields:
                org_row.save(update_fields=[*update_fields, "updated_at", "version"])

            try:
                org_owner = Role.objects.get(slug="org_owner", is_system=True, organization=None)
            except Role.DoesNotExist as exc:
                raise CommandError(
                    "system role 'org_owner' missing — run `manage.py migrate` first."
                ) from exc

            record = FirstAdministrator.objects.select_for_update().filter(organization=org_row).first()
            if record is None:
                owner = (
                    RoleBinding.objects.filter(
                        role=org_owner, scope_kind="ORG", scope_id=org_row.id, user__isnull=False
                    )
                    .order_by("id")
                    .first()
                )
                record = FirstAdministrator.objects.create(
                    organization=org_row,
                    email=normalise(email),
                    is_superuser=is_superuser,
                    claimed_at=timezone.now() if owner else None,
                    claimed_user_id=owner.user_id if owner else None,
                )
            elif record.claimed_at is None:
                record.email = normalise(email)
                record.is_superuser = is_superuser
                record.save(update_fields=["email", "is_superuser"])

        self.stdout.write(
            self.style.SUCCESS(
                f"bootstrap admin: org={org_row.slug} org_created={org_created} "
                f"first_administrator={'claimed' if record.claimed_at else 'pending'}"
            )
        )
