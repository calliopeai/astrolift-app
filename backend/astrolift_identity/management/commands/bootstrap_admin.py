"""
``manage.py bootstrap_admin``

Brings a fresh production install to a usable state in one shot:

* Organization (default slug ``acme`` — override per install)
* User (the bootstrap admin)
* Member row at ORG scope so TenantContextMiddleware resolves cleanly
* RoleBinding(user, org_owner, ORG=org)

The 16 system roles (``org_owner``, ``org_admin``, ``team_developer``,
``app_viewer``, …) are already upserted by data migration
``astrolift_identity.0002_system_roles``; this command does NOT
re-define roles, it just wires the first human into the platform's
own RBAC.

Operators run this once after the initial migrate. Re-runnable: every
step is upsert-style. No dev-mode dependency — works for OIDC, SAML,
Cognito, and local-account installs.

Inputs (CLI flag overrides env var):

  --email     ASTROLIFT_ADMIN_EMAIL
  --org       ASTROLIFT_ORG_SLUG          (default 'acme')
  --org-name  ASTROLIFT_ORG_NAME          (default = slug.title())
  --password  ASTROLIFT_ADMIN_PASSWORD    (optional; only used if the
                                           local IdP is enabled)
  --is-superuser     ASTROLIFT_ADMIN_SUPERUSER ('true'/'1' to enable)

Why a flag *and* an env var: the env-var form makes this safe to
invoke from a deploy script without baking secrets into shell
history; the flag form is convenient for one-off ops.
"""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from astrolift_identity.models import (
    Member,
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
    help = "Bootstrap the first admin in a fresh install (org + user + role binding)."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=_env("ASTROLIFT_ADMIN_EMAIL"))
        # No fallback on slug or name — production installs must pick a real
        # org name; the operator finds out at deploy time, not after a
        # demo screen ships with "Acme".
        parser.add_argument("--org", default=_env("ASTROLIFT_ORG_SLUG"))
        parser.add_argument(
            "--org-name", default=_env("ASTROLIFT_ORG_NAME")
        )
        parser.add_argument(
            "--password", default=_env("ASTROLIFT_ADMIN_PASSWORD")
        )
        parser.add_argument(
            "--is-superuser",
            action="store_true",
            default=_truthy(_env("ASTROLIFT_ADMIN_SUPERUSER")),
            help=(
                "Mark the bootstrap user as a Django superuser. "
                "Recommended only for solo / single-operator installs; "
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
        password: str | None,
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
                    "website": f"https://{org}.example",
                },
            )
            if org_name and org_row.name != org_name:
                org_row.name = org_name
                org_row.save(update_fields=["name", "updated_at", "version"])

            User = get_user_model()
            user, user_created = User.objects.get_or_create(
                username=email,
                defaults={
                    "email": email,
                    "is_staff": is_superuser,
                    "is_superuser": is_superuser,
                },
            )
            # Always reapply the staff/superuser flags so they track
            # whatever the operator passed this run, not whatever was
            # set at first creation.
            user.is_staff = is_superuser or user.is_staff
            user.is_superuser = is_superuser
            if password:
                user.set_password(password)
            elif user_created:
                # User created via OIDC will set their own credentials;
                # leave the password unusable so plain-password login
                # can't accidentally back-door the account.
                user.set_unusable_password()
            user.save()

            try:
                org_owner = Role.objects.get(
                    slug="org_owner", is_system=True, organization=None
                )
            except Role.DoesNotExist as exc:
                raise CommandError(
                    "system role 'org_owner' missing — run `manage.py migrate` first."
                ) from exc

            binding, binding_created = RoleBinding.objects.get_or_create(
                user=user,
                role=org_owner,
                scope_kind="ORG",
                scope_id=org_row.id,
            )

            Member.objects.update_or_create(
                user=user,
                scope_kind="ORG",
                scope_id=org_row.id,
                defaults={"is_active": True, "lifecycle": "active"},
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"bootstrap admin wired: org={org_row.slug} "
                f"user={user.username} "
                f"org_created={org_created} user_created={user_created} "
                f"binding_created={binding_created} "
                f"superuser={is_superuser}"
            )
        )
