"""Project hold rows onto the policy module's input (#1602 step 2).

The join that did not exist. `observability_retention` is a pure policy
module -- it takes frozen `RetentionHold` dataclasses and answers whether a
timestamp is covered -- and `ObservabilityRetentionHold` is the table. This
turns the second into the first.

Separate from the model, and from the policy, on purpose: the same shape
`cert_expiry_monitor._snapshot_for` and `activities/scheduled.
_preview_gc_snapshot` already use, where a Django-aware module reads rows
and hands a pure function plain data. It keeps the policy testable without
a database and keeps the ORM out of the code that decides what "held"
means.
"""

from __future__ import annotations

from typing import Any

from astrolift_operations.observability_retention import RetentionHold


def active_holds_for(organization: Any) -> list[RetentionHold]:
    """Every live hold for ``organization``, as policy inputs.

    "Live" means not soft-deleted. It deliberately does **not** filter on
    the window being in the past or future: a hold's window bounds a slice
    of *data*, not a period during which the hold applies, so a hold over
    last Tuesday stays relevant indefinitely. Filtering by `ends_at >= now`
    -- the obvious reading, and what an `AlertMute`-style TTL would want --
    would expire every hold the moment its window closed and delete the
    exact data it was placed to keep.
    """
    from astrolift_operations.models import ObservabilityRetentionHold

    rows = ObservabilityRetentionHold.objects.filter(
        organization=organization,
        deleted_at__isnull=True,
    ).order_by("starts_at", "pk")

    return [
        RetentionHold(
            stream=row.stream,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            resource_kind=row.resource_kind or "",
            resource_id=row.resource_id or "",
            reason=row.reason or "",
        )
        for row in rows
    ]
