"""
``manage.py bootstrap_idp``

Seeds (or re-syncs) the ``IdentityProvider`` row that ``active_idp.json``
hands to the login UI. Without a row, ``active_idp.json`` returns
``{"kind": null, "ready": false}`` and the UI shows
"No identity provider configured" — even when AUTH0_* env vars are
correctly wired and ``auth1/sessions.py`` could already do the OAuth
dance. This command bridges that gap.

Like ``bootstrap_admin``, every step is upsert-style — safe to run
on every container start. Pair with ``bootstrap_admin`` (which creates
the Organization) by calling that one first; this command requires
an existing org to bind the IdP to.

Inputs (CLI flag overrides env var):

  --org              ASTROLIFT_ORG_SLUG
  --kind             ASTROLIFT_IDP_KIND          (default 'oidc')
  --display-name     ASTROLIFT_IDP_DISPLAY_NAME  (default = kind)
  --client-id        ASTROLIFT_IDP_CLIENT_ID
  --discovery-url    ASTROLIFT_IDP_DISCOVERY_URL (OIDC ``/.well-known/openid-configuration``)
  --client-secret-ref ASTROLIFT_IDP_CLIENT_SECRET_REF (pointer to the
                                                     platform secrets
                                                     backend; not the
                                                     secret itself)
  --is-default       ASTROLIFT_IDP_IS_DEFAULT    ('true'/'1' to mark
                                                  as the org's default
                                                  IdP — what
                                                  active_idp.json
                                                  returns)

Cognito-on-AWS pattern: set ``--kind cognito`` (or ``oidc``), point
``--discovery-url`` at the user pool's OIDC discovery doc
(``https://cognito-idp.<region>.amazonaws.com/<pool-id>/.well-known/openid-configuration``),
and supply the user pool client's ID via ``--client-id``. The actual
client secret is consumed at login time from
``settings.AUTH0_CLIENT_SECRET`` (the authlib client) — this command
just stores the *reference* string so operators can audit which
secret a given IdP row binds to.

Skips if no kind or client_id is provided (silent no-op), so
startup scripts can include the call unconditionally without
forcing every install to configure an IdP.
"""

from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from astrolift_identity.models import IdentityProvider, Organization

_VALID_KINDS = {choice[0] for choice in IdentityProvider.Kind.choices}


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in {"1", "true", "yes", "y", "on"}


class Command(BaseCommand):
    help = "Upsert the IdentityProvider row bound to an organization (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--org", default=_env("ASTROLIFT_ORG_SLUG"))
        parser.add_argument("--kind", default=_env("ASTROLIFT_IDP_KIND", "oidc"))
        parser.add_argument("--display-name", default=_env("ASTROLIFT_IDP_DISPLAY_NAME"))
        parser.add_argument("--client-id", default=_env("ASTROLIFT_IDP_CLIENT_ID"))
        parser.add_argument("--discovery-url", default=_env("ASTROLIFT_IDP_DISCOVERY_URL"))
        parser.add_argument("--client-secret-ref", default=_env("ASTROLIFT_IDP_CLIENT_SECRET_REF", ""))
        parser.add_argument(
            "--is-default",
            action="store_true",
            default=_truthy(_env("ASTROLIFT_IDP_IS_DEFAULT", "true")),
            help="Mark this IdP as the org's default (drives active_idp.json).",
        )

    def handle(
        self,
        *,
        org: str | None,
        kind: str | None,
        display_name: str | None,
        client_id: str | None,
        discovery_url: str | None,
        client_secret_ref: str,
        is_default: bool,
        **opts,
    ):
        # Soft no-op when no configuration is provided so this command can
        # safely live in `ON_STARTUP` for all installs, not just SSO ones.
        if not kind or not client_id:
            self.stdout.write("[bootstrap_idp] no IDP kind+client_id provided — skipping")
            return

        if not org:
            raise CommandError(
                "missing --org (or ASTROLIFT_ORG_SLUG). "
                "Run bootstrap_admin first to create the organization, "
                "then re-run bootstrap_idp."
            )

        if kind not in _VALID_KINDS:
            raise CommandError(
                f"--kind '{kind}' is not one of {sorted(_VALID_KINDS)!r}. "
                "See astrolift_identity.IdentityProvider.Kind for the full list."
            )

        try:
            org_row = Organization.objects.get(slug=org)
        except Organization.DoesNotExist:
            raise CommandError(
                f"organization with slug={org!r} does not exist. "
                "Run `manage.py bootstrap_admin --org {slug} --org-name ...` first."
            ) from None

        defaults = {
            "display_name": display_name or kind,
            "client_id": client_id,
            "oidc_discovery_url": discovery_url or "",
            "client_secret_ref": client_secret_ref,
            "is_default": is_default,
        }

        with transaction.atomic():
            idp, created = IdentityProvider.objects.update_or_create(
                organization=org_row,
                kind=kind,
                defaults=defaults,
            )
            if is_default and org_row.identity_provider_id != idp.id:
                org_row.identity_provider = idp
                org_row.save(update_fields=["identity_provider", "updated_at", "version"])

        verb = "created" if created else "updated"
        self.stdout.write(
            f"[bootstrap_idp] {verb} IdP id={idp.id} kind={idp.kind} "
            f"client_id={idp.client_id} for org slug={org_row.slug} "
            f"(default={is_default})"
        )
