"""
TaskToken — short-lived, task-scoped credential for agent → Controller
callbacks (#62).

One token per AgentRun. The plaintext is generated once, shown once (at
Brief assembly time, injected as ASTROLIFT_TASK_TOKEN), and never stored
— only its SHA-256 hash is persisted. On any terminal transition the token
is revoked by stamping ``revoked_at``.

Scope: the token authorises only:
  - POST /api/dispatch/v1/tasks/<own-id>/telemetry
  - GET  /api/dispatch/v1/tasks/<own-id>/
  - POST /api/dispatch/v1/tasks/<own-id>/complete

Enforcement is in DispatchTaskTokenAuthMiddleware (not in views). This
model is the persistence layer only.
"""

from __future__ import annotations

from django.db import models


class TaskToken(models.Model):
    """Hashed, revocable per-task credential."""

    agent_run = models.OneToOneField(
        "astrolift_lifecycle.AgentRun",
        on_delete=models.CASCADE,
        related_name="task_token",
        db_index=True,
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SHA-256 hex digest of the plaintext token; never store plaintext.",
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        db_index=True,
        help_text="Hard expiry; at most 72h after issuance.",
    )
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Set on Task terminal transition; null while task is live.",
    )

    class Meta:
        indexes = [
            models.Index(fields=["expires_at"], name="tasktoken_expires_idx"),
        ]

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def __str__(self) -> str:
        state = "revoked" if self.is_revoked else "live"
        return f"TaskToken(run={self.agent_run_id}, {state}, exp={self.expires_at})"
