"""Per-user step-up PIN (#2399).

A 6 to 8 digit PIN a person can step up with instead of a full SSO or
password re-verification. Three rules hold it in place:

* The org has to allow it (``Organization.allow_step_up_pin``, off by
  default). When the org does not, a PIN is never offered and never
  accepted, whatever the person has stored, and a PIN elevation taken
  elsewhere does not count there (``session_elevation.get_status``).
* Setting or changing a PIN needs a fresh SSO or password elevation, so
  a PIN can never be bootstrapped from a PIN (enforced by the mutation).
* ``MAX_FAILED_ATTEMPTS`` wrong PINs in a row lock it. A locked PIN is
  not offered and not checked; the person falls back to SSO or password
  and sets a new one.

The org is the request's tenant: the verifier hook has no org argument,
and the tenant is what every other org-scoped decision on the request
reads.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from astrolift_identity.session_elevation import METHOD_PIN
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)

PIN_PATTERN = re.compile(r"^\d{6,8}$")
MAX_FAILED_ATTEMPTS = 5


class StepUpPinError(ValueError):
    """A PIN that does not meet the format rule."""


def org_allows_pin(organization_id: int | None = None) -> bool:
    """True when the org (default: the request's tenant) allows PIN step-up."""
    from astrolift_identity.models import Organization

    if organization_id is None:
        tenant = get_current_tenant()
        organization_id = tenant.organization_id if tenant else None
    if organization_id is None:
        return False
    return Organization.objects.filter(pk=organization_id, allow_step_up_pin=True).exists()


def pin_state(user: Any) -> tuple[bool, bool]:
    """``(is_set, locked)`` for ``user``'s PIN."""
    from astrolift_identity.models import StepUpPin

    row = StepUpPin.objects.filter(user=user).only("locked_at").first()
    if row is None:
        return False, False
    return True, row.locked_at is not None


def pin_available(user: Any) -> bool:
    """True when ``user`` can step up with a PIN on this request right now."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    is_set, locked = pin_state(user)
    return is_set and not locked and org_allows_pin()


def set_pin(user: Any, pin: str) -> bool:
    """Hash and store ``pin``, clearing any lock. True when it replaced one."""
    from astrolift_identity.models import StepUpPin

    if not isinstance(pin, str) or not PIN_PATTERN.match(pin):
        raise StepUpPinError("PIN must be 6 to 8 digits")
    _row, created = StepUpPin.objects.update_or_create(
        user=user,
        defaults={"pin_hash": make_password(pin), "failed_attempts": 0, "locked_at": None},
    )
    return not created


def clear_pin(user: Any) -> bool:
    """Delete ``user``'s PIN. True when there was one."""
    from astrolift_identity.models import StepUpPin

    deleted, _ = StepUpPin.objects.filter(user=user).delete()
    return deleted > 0


def pin_verifier(user: Any, method: str, credential: Any) -> bool:
    """Credential verifier for ``method == "pin"``; composes with the others.

    The row is locked for the check so parallel guesses cannot each read
    the same count and slip past the limit. A malformed credential is
    refused without counting: it cannot match, so it buys a guesser
    nothing.
    """
    from astrolift_identity.models import StepUpPin

    if method != METHOD_PIN:
        return False
    if user is None or not getattr(user, "is_active", False):
        return False
    if not isinstance(credential, str) or not PIN_PATTERN.match(credential):
        return False
    tenant = get_current_tenant()
    organization_id = tenant.organization_id if tenant else None
    if organization_id is None or not org_allows_pin(organization_id):
        return False
    # The tenant header names an org without a membership check, so a
    # PIN is only taken under an org the person actually belongs to.
    from astrolift_identity.api_tokens import is_active_org_member

    if not is_active_org_member(user.pk, organization_id):
        return False
    with transaction.atomic():
        row = StepUpPin.objects.select_for_update().filter(user=user).first()
        if row is None or row.locked_at is not None:
            return False
        if check_password(credential, row.pin_hash):
            if row.failed_attempts:
                row.failed_attempts = 0
                row.save(update_fields=["failed_attempts", "updated_at"])
            return True
        row.failed_attempts += 1
        if row.failed_attempts >= MAX_FAILED_ATTEMPTS:
            row.locked_at = timezone.now()
        row.save(update_fields=["failed_attempts", "locked_at", "updated_at"])
        locked = row.locked_at is not None
    if locked:
        _audit_locked(user)
    return False


def _audit_locked(user: Any) -> None:
    from core.mutations import AuditEntry, ErrorCode, emit_audit

    tenant = get_current_tenant()
    try:
        emit_audit(
            AuditEntry(
                actor_user_id=user.pk,
                organization_id=tenant.organization_id if tenant else None,
                action="auth.step_up_pin.locked",
                decision="DENY",
                target_kind="user",
                target_id=user.pk,
                duration_ms=0,
                permissions=(),
                error_code=ErrorCode.PERMISSION_DENIED.value,
                error_message="step-up PIN locked after repeated wrong attempts",
                extra={"failed_attempts": MAX_FAILED_ATTEMPTS},
            )
        )
    except Exception:  # noqa: BLE001 -- audit must never break the deny
        log.exception("step_up_pin: lock audit emit failed for user %s", user.pk)
