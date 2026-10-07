"""connect_zentinelle: connect one organization to Zentinelle from inside the install.

The installer's agent runs this as a one-off task in the customer account
(calliopeai/calliope-installer#433): it mints an enrollment code from
Zentinelle with the bootstrap secret, then runs

    python manage.py connect_zentinelle --org <slug> --url <zentinelle url> --code-env <ENV NAME>

with the code in that environment variable. The code is never an argument,
so it is not in the task's command line, its logs or a process listing.

The exchange is the org-admin ``connectZentinelle`` mutation's
(``zentinelle_connect.connect``): the install credential is sealed in the
organization's ``ZentinelleConnection`` and never printed. Then the
Constance flags ``ZENTINELLE_ENABLED`` and ``ZENTINELLE_GATEWAY_ENABLED``
are turned on.

Idempotent. A live connection to the same Zentinelle is left as it is and
the run succeeds without reading the code; live means Zentinelle still
accepts its credential, asked each run. A connection Zentinelle has revoked
is disconnected here and connected again with the code. A live
connection to a different Zentinelle is refused: rewiring an organization's
governance is an org admin's call.
"""

from __future__ import annotations

import os
import re
import time

from django.core.management.base import BaseCommand, CommandError

_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class Command(BaseCommand):
    help = "Connect an organization to Zentinelle with an enrollment code read from an environment variable."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Slug of the organization to connect.")
        parser.add_argument("--url", required=True, help="Zentinelle's URL.")
        parser.add_argument(
            "--code-env",
            required=True,
            help="Name of the environment variable holding the one-time enrollment code.",
        )

    def handle(self, *args, **options):
        from astrolift_identity.models import Organization
        from astrolift_operations import zentinelle_connect
        from astrolift_operations.models import ZentinelleConnection

        code_env = options["code_env"]
        if not _ENV_NAME.match(code_env):
            raise CommandError("--code-env must name an environment variable, not hold the code")
        organization = Organization.objects.filter(slug=options["org"]).first()
        if organization is None:
            raise CommandError(f"no organization {options['org']!r}")
        try:
            url = zentinelle_connect.normalize_base_url(options["url"])
        except zentinelle_connect.ZentinelleConnectError as exc:
            raise CommandError(exc.message) from None

        existing = ZentinelleConnection.objects.filter(organization=organization).first()
        if existing is not None and existing.status == ZentinelleConnection.Status.CONNECTED:
            if existing.base_url != url:
                raise CommandError(
                    f"organization {organization.slug!r} is connected to {existing.base_url}; "
                    "an org admin disconnects it before it connects elsewhere"
                )
            try:
                live = zentinelle_connect.credential_accepted(existing)
            except zentinelle_connect.ZentinelleConnectError as exc:
                raise CommandError(exc.message) from None
            if not live:
                existing.refresh_from_db()
        if existing is not None and existing.status == ZentinelleConnection.Status.CONNECTED:
            self._enable_flags()
            self.stdout.write(f"organization {organization.slug} already connected to {url}; nothing to do")
            return

        code = os.environ.get(code_env, "").strip()
        if not code:
            raise CommandError(f"{code_env} is empty: no enrollment code to connect with")

        started = time.monotonic()
        try:
            if existing is not None:
                outcome = zentinelle_connect.disconnect(connection=existing, force=True)
                for warning in outcome.warnings:
                    self.stderr.write(warning)
            connection = zentinelle_connect.connect(
                organization=organization,
                url=url,
                code=code,
                install_url=zentinelle_connect.install_base_url(),
            )
        except zentinelle_connect.ZentinelleConnectError as exc:
            raise CommandError(exc.message) from None
        self._audit(organization, connection, started)
        self._enable_flags()
        self.stdout.write(
            f"organization {organization.slug} connected to {url} "
            f"(Zentinelle install {connection.zentinelle_install_id or 'unknown'})"
        )

    @staticmethod
    def _enable_flags():
        from constance import config

        if not config.ZENTINELLE_ENABLED:
            config.ZENTINELLE_ENABLED = True
        if not config.ZENTINELLE_GATEWAY_ENABLED:
            config.ZENTINELLE_GATEWAY_ENABLED = True

    @staticmethod
    def _audit(organization, connection, started):
        from core.mutations import AuditEntry, emit_audit

        emit_audit(
            AuditEntry(
                actor_user_id=None,
                organization_id=organization.pk,
                action="zentinelle.connect",
                decision="ALLOW",
                target_kind="ZentinelleConnection",
                target_id=str(connection.guid),
                duration_ms=int((time.monotonic() - started) * 1000),
                permissions=(),
                extra={
                    "via": "manage.py connect_zentinelle",
                    "zentinelle_url": connection.base_url,
                    "zentinelle_install_id": connection.zentinelle_install_id,
                },
            )
        )
