"""Django signals for astrolift_pipelines — wires model saves to schedule sync (#74)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def on_trigger_save(sender, instance, created: bool, **kwargs) -> None:
    """Sync Temporal schedule after a Trigger is created or updated.

    Only acts on ``kind="schedule"`` triggers. For other kinds (push,
    pull_request, manual, api) this is a no-op — those are event-driven
    and don't need a Temporal schedule.
    """
    if instance.kind != "schedule":
        return

    from astrolift_pipelines.schedule_sync import create_or_update_schedule, delete_schedule

    if instance.deleted_at is not None:
        # Soft-deleted — remove the Temporal schedule
        delete_schedule(instance)
    else:
        create_or_update_schedule(instance)
