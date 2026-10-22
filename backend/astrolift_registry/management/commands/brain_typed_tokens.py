"""
brain_typed_tokens: move one brain to typed read tokens and back (#2470).

    python manage.py brain_typed_tokens status
    python manage.py brain_typed_tokens stage <brain app guid> --operator <email> [--apply]
    python manage.py brain_typed_tokens enable <brain app guid> --operator <email> \\
        --attestation "<what you checked on every serving replica>" [--apply]
    python manage.py brain_typed_tokens disable <brain app guid> --operator <email> [--apply]
    python manage.py brain_typed_tokens unstage <brain app guid> --operator <email> [--apply]

Without ``--apply`` a step runs its checks and changes nothing. The operator
is the platform operator the step is audited as. The procedure, and what to
check between steps: docs/operators/brain-typed-read-tokens.md.
"""

from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

STEPS = ("stage", "enable", "disable", "unstage")


class Command(BaseCommand):
    help = "Stage, enable, disable or unstage typed read tokens for one brain (#2470)."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("status", *STEPS))
        parser.add_argument("brain", nargs="?", default="", help="The brain's app guid")
        parser.add_argument("--operator", default="", help="Email of the platform operator taking the step")
        parser.add_argument(
            "--attestation",
            default="",
            help="enable: what you checked on every serving replica (20-500 characters)",
        )
        parser.add_argument(
            "--apply", action="store_true", default=False, help="Take the step; default checks only"
        )

    def handle(self, *args, **options):
        from astrolift_registry import brain_token_rollout

        if options["action"] == "status":
            self._status()
            return
        brain = self._brain(options["brain"])
        operator = self._operator(options["operator"])
        step = getattr(brain_token_rollout, options["action"])
        kwargs = {"attestation": options["attestation"]} if options["action"] == "enable" else {}
        try:
            result = step(brain, operator=operator, apply=options["apply"], **kwargs)
        except brain_token_rollout.RolloutRefused as exc:
            raise CommandError(str(exc)) from None
        name = f"{brain.organization.slug}/{brain.slug}"
        if result.applied:
            self.stdout.write(f"{result.action}: {name} done; {result.detail}.")
        else:
            self.stdout.write(
                f"{result.action}: {name} passes its checks. Re-run with --apply to take the step."
            )

    def _brain(self, value: str):
        from astrolift_registry.models import RegisteredApp

        try:
            guid = uuid.UUID(value)
        except ValueError:
            raise CommandError("name the brain by its app guid") from None
        brain = RegisteredApp.all_objects.filter(guid=guid).select_related("organization").first()
        if brain is None:
            raise CommandError("brain not found")
        return brain

    def _operator(self, email: str):
        users = (
            list(get_user_model().objects.filter(email__iexact=email.strip(), is_active=True)[:2])
            if email
            else []
        )
        if len(users) != 1:
            raise CommandError("--operator must be the email of exactly one active user")
        return users[0]

    def _status(self) -> None:
        from astrolift_registry.models import BrainTypedTokenRollout

        rows = BrainTypedTokenRollout.objects.select_related(
            "brain", "organization", "created_by", "issuance_enabled_by"
        ).order_by("organization__slug", "brain__slug")
        if not rows:
            self.stdout.write("No brain is staged for typed read tokens.")
            return
        for row in rows:
            staged = f"staged {row.created_at.isoformat()} by {getattr(row.created_by, 'email', '?')}"
            if row.issuance_enabled_at is not None:
                state = (
                    f"issuing typed tokens since {row.issuance_enabled_at.isoformat()} "
                    f"by {getattr(row.issuance_enabled_by, 'email', '?')}: {row.attestation}"
                )
            elif row.issuance_disabled_at is not None:
                state = f"legacy tokens since {row.issuance_disabled_at.isoformat()}"
            else:
                state = "legacy tokens"
            self.stdout.write(f"{row.organization.slug}/{row.brain.slug} {row.brain.guid}: {staged}; {state}")
