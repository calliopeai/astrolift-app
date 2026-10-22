"""Moving one brain to typed read tokens and back (#2470).

The platform operator runs each step with ``manage.py brain_typed_tokens``
(docs/operators/brain-typed-read-tokens.md); each is audited as them:

1. ``stage``: the brain's next deploy stamps the typed verifier settings on
   its workloads. Tokens stay legacy.
2. Deploy every environment that serves the brain and check that each
   serving replica accepts a legacy and a typed token.
3. ``enable`` with an attestation of that check: every read token for the
   brain is typed from then on. Refused until each serving environment runs
   only deployments made after staging.
4. ``disable``: new tokens are legacy again. Typed user and run tokens live
   out their 15 minutes; hosted apps keep theirs until redeployed.
5. ``unstage``: refused while issuance is on, until those tokens have
   expired, and while a hosted app's published key signed a typed token.

A brain is locked for each step, so two operators cannot interleave.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from django.db import transaction
from django.utils import timezone

from astrolift_registry.brain_tokens import TOKEN_TTL, TYPED_CONTRACT
from astrolift_registry.models import BrainTypedTokenRollout, RegisteredApp

ATTESTATION_LENGTH = (20, 500)
# How long typed user and run tokens still verify once issuance stops: their
# lifetime, plus the 30 seconds of clock skew a verifier allows, rounded up.
TYPED_TOKEN_TAIL = TOKEN_TTL + dt.timedelta(minutes=1)


class RolloutRefused(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class RolloutStep:
    """A step that passed its checks: ``applied`` once written."""

    action: str
    brain: RegisteredApp
    applied: bool
    detail: str


def _check_operator(operator: Any) -> None:
    from core.permissions import is_platform_operator

    if not is_platform_operator(operator):
        raise RolloutRefused("the operator must be an active platform operator (a superuser)")


def _locked_brain(brain: RegisteredApp, *, live: bool) -> RegisteredApp:
    from astrolift_manifest.app_kinds import BRAIN_KINDS

    locked = RegisteredApp.all_objects.select_for_update().get(pk=brain.pk)
    if locked.app_kind not in BRAIN_KINDS:
        raise RolloutRefused(f"{locked.slug} is not a brain")
    if live and (locked.deleted_at is not None or locked.archived_at is not None):
        raise RolloutRefused(f"brain {locked.slug} is archived or deleted")
    return locked


def _live_row(brain: RegisteredApp) -> BrainTypedTokenRollout | None:
    return (
        BrainTypedTokenRollout.objects.select_for_update()
        .filter(brain=brain, organization_id=brain.organization_id)
        .first()
    )


def _audit(action: str, brain: RegisteredApp, operator: Any, **extra: Any) -> None:
    from core.mutations import AuditEntry, emit_audit

    emit_audit(
        AuditEntry(
            actor_user_id=operator.pk,
            organization_id=brain.organization_id,
            action=f"brain.typed_tokens.{action}",
            decision="ALLOW",
            target_kind="RegisteredApp",
            target_id=brain.slug,
            duration_ms=0,
            permissions=(),
            extra={"brain": str(brain.guid), "contract": TYPED_CONTRACT, **extra},
        )
    )


def _check_deployed_since(brain: RegisteredApp, staged_at: dt.datetime) -> None:
    """Every environment that serves ``brain`` runs only deployments made
    after ``staged_at``, so each one rendered the typed verifier settings."""
    from astrolift_lifecycle.models import AppEnvironment, Deployment

    served = list(
        AppEnvironment.objects.filter(registered_app=brain, deleted_at__isnull=True)
        .exclude(url="")
        .order_by("name")
    )
    if not served:
        raise RolloutRefused(f"no environment serves brain {brain.slug} yet")
    for environment in served:
        running = Deployment.objects.filter(
            registered_app=brain, app_environment=environment, status=Deployment.Status.RUNNING.value
        )
        if not running.exists() or running.filter(created_at__lt=staged_at).exists():
            raise RolloutRefused(
                f"environment {environment.name} of brain {brain.slug} does not run a deployment made "
                "after staging; deploy it and check its replicas first"
            )


def _hosts_with_typed_keys(brain: RegisteredApp) -> list[str]:
    """The apps hosted on ``brain`` whose published deployment keys signed a typed token."""
    from astrolift_registry.brain_apps import live_binding_keys

    return sorted(
        set(
            live_binding_keys()
            .filter(
                binding__brain=brain,
                binding__organization_id=brain.organization_id,
                typed_contract=TYPED_CONTRACT,
            )
            .values_list("binding__registered_app__slug", flat=True)
        )
    )


def stage(brain: RegisteredApp, *, operator: Any, apply: bool) -> RolloutStep:
    _check_operator(operator)
    with transaction.atomic():
        brain = _locked_brain(brain, live=True)
        if _live_row(brain) is not None:
            raise RolloutRefused(f"brain {brain.slug} is already staged")
        if apply:
            BrainTypedTokenRollout.objects.create(
                organization_id=brain.organization_id, brain=brain, created_by=operator
            )
            _audit("stage", brain, operator)
    return RolloutStep(
        "stage", brain, apply, "deploy every environment that serves it, then check its replicas"
    )


def enable(brain: RegisteredApp, *, operator: Any, attestation: str, apply: bool) -> RolloutStep:
    _check_operator(operator)
    attestation = (attestation or "").strip()
    low, high = ATTESTATION_LENGTH
    if not low <= len(attestation) <= high:
        raise RolloutRefused(
            f"say what you checked on every serving replica in {low} to {high} characters (--attestation)"
        )
    with transaction.atomic():
        brain = _locked_brain(brain, live=True)
        row = _live_row(brain)
        if row is None:
            raise RolloutRefused(f"brain {brain.slug} is not staged; stage it first")
        if row.issuance_enabled_at is not None:
            raise RolloutRefused(f"brain {brain.slug} already issues typed tokens")
        _check_deployed_since(brain, row.created_at)
        if apply:
            row.issuance_enabled_at = timezone.now()
            row.issuance_enabled_by = operator
            row.attestation = attestation
            row.save(
                update_fields=[
                    "issuance_enabled_at",
                    "issuance_enabled_by",
                    "attestation",
                    "updated_at",
                    "version",
                ]
            )
            _audit("enable", brain, operator, attestation=attestation)
    return RolloutStep("enable", brain, apply, "every read token for it is typed")


def disable(brain: RegisteredApp, *, operator: Any, apply: bool) -> RolloutStep:
    _check_operator(operator)
    with transaction.atomic():
        brain = _locked_brain(brain, live=False)
        row = _live_row(brain)
        if row is None or row.issuance_enabled_at is None:
            raise RolloutRefused(f"brain {brain.slug} does not issue typed tokens")
        if apply:
            row.issuance_enabled_at = None
            row.issuance_disabled_at = timezone.now()
            row.save(update_fields=["issuance_enabled_at", "issuance_disabled_at", "updated_at", "version"])
            _audit("disable", brain, operator)
    return RolloutStep(
        "disable", brain, apply, "new tokens are legacy; redeploy the apps hosted on it before unstaging"
    )


def unstage(
    brain: RegisteredApp, *, operator: Any, apply: bool, now: dt.datetime | None = None
) -> RolloutStep:
    _check_operator(operator)
    now = now or timezone.now()
    with transaction.atomic():
        brain = _locked_brain(brain, live=False)
        row = _live_row(brain)
        if row is None:
            raise RolloutRefused(f"brain {brain.slug} is not staged")
        if row.issuance_enabled_at is not None:
            raise RolloutRefused(f"brain {brain.slug} still issues typed tokens; disable it first")
        if row.issuance_disabled_at is not None and now < row.issuance_disabled_at + TYPED_TOKEN_TAIL:
            until = (row.issuance_disabled_at + TYPED_TOKEN_TAIL).isoformat()
            raise RolloutRefused(f"typed tokens issued for brain {brain.slug} still verify until {until}")
        hosts = _hosts_with_typed_keys(brain)
        if hosts:
            raise RolloutRefused(
                f"apps hosted on brain {brain.slug} still hold typed tokens: {', '.join(hosts)}; deploy "
                "each environment of each twice, since a deploy keeps the release it replaces readable"
            )
        if apply:
            row.soft_delete(by=operator)
            _audit("unstage", brain, operator)
    return RolloutStep("unstage", brain, apply, "its next deploy drops the typed verifier settings")
