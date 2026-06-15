"""
Per-task agent credential service (#62).

Each AgentRun gets a single short-lived TaskToken issued at Brief assembly
time. The plaintext is injected as ``ASTROLIFT_TASK_TOKEN`` env var into
the agent container. Only the SHA-256 hash is persisted.

Token lifecycle:
  - Issued: at Brief assembly (``issue_task_credential``).
  - Valid:  until task terminates OR ``expires_at`` is reached (max 72h).
  - Revoked: ``revoke_task_credential`` stamps ``revoked_at`` on the row;
    called automatically on any terminal AgentRun transition.

Validation (``validate_task_credential``) checks in order:
  1. Hash lookup — must match a known TaskToken row.
  2. ``task_guid`` — token must be scoped to the requested task.
  3. Revoked — ``revoked_at`` must be null.
  4. Expired — ``expires_at`` must be in the future.

Reuses ``core.service_tokens`` HMAC-JWT machinery for the token format so
the validation path is consistent with other bearer credentials in the
platform.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from django.utils import timezone

from astrolift_agents.models.task_token import TaskToken
from astrolift_lifecycle.models import AgentRun

log = logging.getLogger(__name__)

# Maximum lifetime. The spec says "Task timeout + buffer, or 72h max".
MAX_TTL_HOURS = 72
PLAINTEXT_PREFIX = "alft_tk_"


def issue_task_credential(task: AgentRun) -> str:
    """Issue a short-lived credential scoped to *task*.

    Creates a ``TaskToken`` row (hash only). Returns the plaintext bearer
    string (shown once; never stored). Raises ``ValueError`` if the task
    already has a live token.

    The TTL is the lesser of the workload's ``result_ttl_hours`` and
    ``MAX_TTL_HOURS``.
    """
    if TaskToken.objects.filter(agent_run=task).exists():
        raise ValueError(
            f"AgentRun {task.guid} already has a TaskToken. " "Revoke the existing token before re-issuing."
        )

    ttl_hours = min(
        getattr(task, "result_ttl_hours", MAX_TTL_HOURS) or MAX_TTL_HOURS,
        MAX_TTL_HOURS,
    )
    expires_at = timezone.now() + timedelta(hours=ttl_hours)

    plaintext = PLAINTEXT_PREFIX + secrets.token_urlsafe(32)
    token_hash = _hash(plaintext)

    TaskToken.objects.create(
        agent_run=task,
        token_hash=token_hash,
        expires_at=expires_at,
    )

    log.info(
        "issued TaskToken for AgentRun %s (expires %s)",
        task.guid,
        expires_at.isoformat(),
    )
    return plaintext


def validate_task_credential(token: str, task_guid: str) -> bool:
    """Return True iff *token* is a valid, live credential for *task_guid*.

    Performs all checks without short-circuiting on the first failure to
    avoid timing side-channels. Returns False on any check failure.
    """
    if not token or not task_guid:
        return False

    token_hash = _hash(token)

    try:
        task_token = TaskToken.objects.select_related("agent_run").get(token_hash=token_hash)
    except TaskToken.DoesNotExist:
        return False

    # Scope check: token must belong to the claimed task.
    if str(task_token.agent_run.guid) != str(task_guid):
        log.warning(
            "TaskToken hash matched but task_guid mismatch: claimed=%s actual=%s",
            task_guid,
            task_token.agent_run.guid,
        )
        return False

    # Revocation check.
    if task_token.is_revoked:
        return False

    # Expiry check.
    if timezone.now() >= task_token.expires_at:
        return False

    return True


def revoke_task_credential(task: AgentRun) -> bool:
    """Revoke the TaskToken for *task*, if one exists.

    Stamps ``revoked_at`` with the current time. Returns True if a token
    was found and revoked, False if none existed.

    Called automatically on terminal AgentRun transition.
    """
    try:
        task_token = TaskToken.objects.get(agent_run=task)
    except TaskToken.DoesNotExist:
        return False

    if task_token.is_revoked:
        return False

    task_token.revoked_at = timezone.now()
    task_token.save(update_fields=["revoked_at"])

    log.info("revoked TaskToken for AgentRun %s", task.guid)
    return True


# ── Internal helpers ──────────────────────────────────────────────────────────


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()
