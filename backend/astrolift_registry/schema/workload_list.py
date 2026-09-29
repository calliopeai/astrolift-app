"""The Workloads list on the list contract (spec 44 §5.1, #2155).

Workloads, functions, tasks and cron jobs are one table filtered by kind:
a small, stable set, so the numbered path. ``astroliftWorkloadsPage`` takes
it when ``page``, ``pageSize`` or ``sort`` is given and the cursor walk
otherwise; ``filter`` applies to both.

The queryset reaching these helpers is already scoped to the caller's org
by ``_workloads_qs``; nothing here widens it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import strawberry
from django.db.models import Q
from django.db.models.functions import Lower

from astrolift_graphql import FilterField, SortKey
from astrolift_registry.models import Workload


@strawberry.input(
    name="AstroliftWorkloadsFilter",
    description="The workloads list's declared filters. Unset fields do not filter; list values match any.",
)
class WorkloadsListFilterInput:
    kind: list[str] | None = strawberry.field(
        default=None, description="Workload kinds: deployment, cronjob, task, agent, workflow, function, ..."
    )
    is_public: bool | None = strawberry.field(
        default=None, description="true: workloads with an ingress. false: internal only."
    )
    app: list[str] | None = strawberry.field(default=None, description="App slugs.")
    owner: list[str] | None = strawberry.field(
        default=None, description='The owner, as ownerUserId; "me" is the viewer.'
    )


def _owner_q(values: list[Any]) -> Q:
    """The workload's creator, else (when it recorded none) its app's creator.

    ``"me"`` arrives as the viewer's pk; other values are user pks as
    strings, and anything else matches nothing.
    """
    ids = [v if isinstance(v, int) else int(v) for v in values if isinstance(v, int) or str(v).isdigit()]
    return Q(created_by_id__in=ids) | Q(created_by__isnull=True, registered_app__created_by_id__in=ids)


WORKLOADS_FILTERS: dict[str, FilterField] = {
    "kind": FilterField("kind"),
    "is_public": FilterField("is_public"),
    "app": FilterField("registered_app__slug"),
    "owner": FilterField(q=_owner_q, me=True),
}

WORKLOADS_DEFAULT_SORT = "name"

WORKLOADS_SORT_KEYS: dict[str, SortKey] = {
    "name": SortKey(Lower("name")),
    "slug": SortKey("slug"),
    "kind": SortKey("kind"),
    "app": SortKey("registered_app__slug"),
    "public": SortKey("is_public"),
    "created": SortKey("created_at"),
}


def cron_last_runs(workloads: Iterable[Workload]) -> dict[int, Any]:
    """Each cron job's latest run, keyed by workload pk: one query per page."""
    from astrolift_lifecycle.models import ScheduledJobRun

    ids = [w.pk for w in workloads if w.kind == Workload.Kind.CRONJOB]
    if not ids:
        return {}
    runs = (
        ScheduledJobRun.objects.filter(workload_id__in=ids, deleted_at__isnull=True)
        .order_by("workload_id", "-created_at", "-guid")
        .distinct("workload_id")
    )
    return {run.workload_id: run for run in runs}
