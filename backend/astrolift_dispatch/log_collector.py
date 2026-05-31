"""
Agent log collection — receive and store log lines forwarded by the
Dispatch Service (#51).

The Dispatch Service tails container stdout/stderr (kubectl logs, CloudWatch,
docker logs) and batches lines to the Controller via:

    POST /api/dispatch/v1/tasks/<task_id>/logs/

Lines are appended to AgentRun.log_excerpt (a capped ring buffer of up to
LOG_RING_BUFFER_LINES lines). Older lines beyond the cap are trimmed from
the head. The excerpt is the live-view source; the full log is uploaded to
object storage on task terminal transition by the Dispatch Service.

Only the authenticated Dispatch Service caller may write logs (enforced by
the DispatchServiceAuth middleware that gates the entire /api/dispatch/v1/
prefix).
"""

from __future__ import annotations

import logging

from astrolift_lifecycle.models import AgentRun

log = logging.getLogger(__name__)

# Ring-buffer cap in lines. Matches the Redis cap described in #51.
LOG_RING_BUFFER_LINES = 10_000


def store_agent_log_lines(task_guid: str, lines: list[str]) -> int:
    """Append *lines* to AgentRun.log_excerpt for the given task GUID.

    Trims from the head when the buffer exceeds LOG_RING_BUFFER_LINES so
    the stored excerpt is always the most recent lines. Returns the number
    of lines stored after the update.

    Raises ``AgentRun.DoesNotExist`` if *task_guid* is unknown (the caller
    should return 404).
    """
    if not lines:
        return 0

    run = AgentRun.all_objects.select_for_update().get(guid=task_guid)

    existing = run.log_excerpt or ""
    existing_lines = existing.splitlines() if existing else []

    combined = existing_lines + [str(line) for line in lines]
    if len(combined) > LOG_RING_BUFFER_LINES:
        combined = combined[-LOG_RING_BUFFER_LINES:]

    run.log_excerpt = "\n".join(combined)
    run.save(update_fields=["log_excerpt", "updated_at"])

    log.debug(
        "stored %d log lines for task %s (total %d)",
        len(lines),
        task_guid,
        len(combined),
    )
    return len(combined)
