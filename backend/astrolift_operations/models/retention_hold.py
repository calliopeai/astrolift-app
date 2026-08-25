"""ObservabilityRetentionHold -- the data source `is_held` never had (#1602).

`observability_retention.is_held()` takes a sequence of `RetentionHold`
dataclasses and applies the match rules. Nothing ever built that sequence:
no table, no query, no row. So the hold half of the retention policy was a
function with correct logic and no input, and the module's own test was the
only thing that ever passed it a hold.

That matters more than it sounds. Holds are the reason eviction can be
turned on at all: they are how an operator says "an incident is live, do
not delete this window yet". The spec sequences this **before** the
eviction driver deliberately -- shipping eviction first would mean the
first activation deletes data an incident is actively depending on, with no
mechanism to stop it.

Named `ObservabilityRetentionHold` rather than `RetentionHold` on purpose:
the policy module already owns that name for its frozen dataclass, and the
sweep projects rows onto it. One is a database row, one is the pure
policy's input, and collapsing the names would make it unclear which side
of that boundary a given `RetentionHold` is on.

Modelled on `AlertMute`, which solves the same shape (an operator-placed,
time-bounded suppression that must stay auditable):

* `reason` and `placed_by` so post-incident review can answer who held
  what, and why.
* Release is a **soft delete**, not a field flip, so the history of past
  holds stays queryable. Same as unmute.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel

# `"*"` is the policy module's wildcard for "every stream". Carried here as
# a storable choice so an operator can hold everything during an incident
# without placing four rows and risking three of them.
ANY_STREAM = "*"


class ObservabilityRetentionHold(BaseCoreModel):
    """A time-bounded suppression of eviction for one org and stream."""

    class Stream(models.TextChoices):
        # Mirrors `observability_retention.ALL_STREAMS` plus the wildcard.
        # Duplicated rather than derived because a migration would freeze
        # whatever the tuple said the day it ran, and a choices list that
        # silently disagrees with its migration is worse than one written
        # twice. The test asserts the two stay equal.
        LOG = "log"
        METRIC_RAW = "metric_raw"
        METRIC_ROLLUP = "metric_rollup"
        TRACE = "trace"
        ANY = ANY_STREAM

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="observability_retention_holds",
        on_delete=models.CASCADE,
    )
    stream = models.CharField(
        max_length=16,
        choices=Stream.choices,
        default=Stream.ANY,
        help_text="Which stream this holds, or '*' for all of them.",
    )
    starts_at = models.DateTimeField(
        help_text="Start of the held window, inclusive.",
    )
    ends_at = models.DateTimeField(
        help_text=(
            "End of the held window, inclusive. A hold does not auto-expire "
            "the way an AlertMute TTL does -- it bounds a window of *data*, "
            "not a window of time in which the hold applies, so a hold over "
            "last Tuesday stays relevant indefinitely."
        ),
    )
    resource_kind = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Scopes the hold, e.g. 'App'. Empty means any.",
    )
    resource_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Scopes the hold with resource_kind. Empty means any.",
    )
    reason = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Why the window is held. Blank-tolerant on the column and "
            "required by the mutation, matching AlertMute: a hold nobody "
            "can explain is a hold nobody will dare release."
        ),
    )
    placed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="observability_retention_holds_placed",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text=(
            "Who placed it. Distinct from BaseCoreModel.created_by, which "
            "tracks the row writer; this is semantically the hold's owner."
        ),
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "ends_at"],
                name="obs_ret_hold_org_ends_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"ObservabilityRetentionHold({self.organization_id}, {self.stream}, {self.starts_at}..{self.ends_at})"
