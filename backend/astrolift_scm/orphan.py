"""
Mark SCM connections orphaned + the GitHub installation.deleted
webhook handler (#145).

Why a separate module:
- The orphan flip happens from two places (webhook ingest, manual
  CLI/UI) so it lives behind a single function with a consistent
  audit shape.
- The deploy + token mint paths read ``is_orphaned`` to refuse
  fresh activity against a dead connection. Reconnect clears it.
"""

from __future__ import annotations

import logging

from django.utils import timezone


log = logging.getLogger(__name__)


def mark_connection_orphaned(connection, *, reason: str) -> None:
    """Flip ``is_orphaned=True`` on a SourceConnection if it isn't
    already, stamp ``orphaned_at`` + ``orphaned_reason``. Idempotent.

    Doesn't soft-delete the row — the credential metadata stays
    around so the reconnect flow can match the new install/token
    against the old account_login + repo set without forcing the
    user to re-enter everything.
    """
    if connection.is_orphaned:
        return
    connection.is_orphaned = True
    connection.orphaned_at = timezone.now()
    connection.orphaned_reason = (reason or "").strip()[:255]
    connection.save(
        update_fields=[
            "is_orphaned",
            "orphaned_at",
            "orphaned_reason",
            "updated_at",
            "version",
        ]
    )
    log.info(
        "scm connection orphaned",
        extra={
            "connection_id": connection.pk,
            "kind": connection.kind,
            "reason": connection.orphaned_reason,
        },
    )


def reconnect_connection(connection) -> None:
    """Clear the orphan flags. Operator-initiated; the reconnect
    flow re-authorises the OAuth/install before calling this."""
    if not connection.is_orphaned:
        return
    connection.is_orphaned = False
    connection.orphaned_at = None
    connection.orphaned_reason = ""
    connection.save(
        update_fields=[
            "is_orphaned",
            "orphaned_at",
            "orphaned_reason",
            "updated_at",
            "version",
        ]
    )


def handle_github_installation_deleted(installation_id: str) -> int:
    """Webhook entrypoint: GitHub fired ``installation.deleted`` so
    every SourceConnection bound to ``installation_id`` becomes
    orphaned. Returns the number of rows flipped.

    Walks active rows only; previously-orphaned ones stay as they
    were (their orphan_reason already records why). Per spec 06 §4.23.
    """
    from astrolift_scm.models import SourceConnection

    if not installation_id:
        return 0
    qs = SourceConnection.objects.filter(
        installation_id=str(installation_id),
        is_orphaned=False,
        deleted_at__isnull=True,
    )
    count = 0
    for row in qs:
        mark_connection_orphaned(
            row,
            reason="github installation uninstalled by owner",
        )
        count += 1
    return count
