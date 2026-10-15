"""StepUpPin: a person's step-up verification PIN (#2399).

A quick re-verify for an operator who already has a live session, so a
production deploy on an SSO install does not cost a full IdP round-trip
every time. Only the Django password hash is stored. One row per user,
created when the PIN is first set and deleted when it is cleared.

``locked_at`` is set once ``failed_attempts`` reaches the limit in
``astrolift_identity.step_up_pin``. A locked PIN is never checked again;
the person steps up with SSO or password and sets a new PIN, which is
the only way out of the lock.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class StepUpPin(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="step_up_pin",
        primary_key=True,
    )
    pin_hash = models.CharField(max_length=128)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover - admin display only
        return f"StepUpPin(user={self.user_id}, locked={self.locked_at is not None})"
