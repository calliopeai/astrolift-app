"""The lifecycle lists on the list contract (spec 44 §5.1, #2155).

Deployments, job runs, command runs and previews are cursor lists (they
only grow), so each takes a ``filter`` input and a single-key ``sort``
over NOT NULL columns; see ``README.md`` in ``astrolift_graphql`` for why
multi-key sort waits on cursor lists. Environments are a small, stable set
and take the numbered path: ``filter``, ``search``, multi-key ``sort`` and
``page`` / ``pageSize``.

Every queryset reaching these helpers is already scoped to the caller's
org by the resolver; nothing here widens a set.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import strawberry
from django.db import models
from django.db.models import Case, Q, QuerySet, Value, When
from django.db.models.functions import Coalesce, Lower

from astrolift_graphql import FilterField, SortKey, UnsupportedSort, parse_sort_spec
from astrolift_lifecycle.schema.types import PRODUCTION_ENVIRONMENT_NAMES
from astrolift_lifecycle.services.preview_lineage import PREVIEW_ENV_PREFIX

# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------


def user_ids(values: Any) -> list[int]:
    """User pks from a filter value: ints (``"me"`` resolved) and digit strings.

    Anything else is dropped rather than handed to an integer column, where
    it would raise instead of matching nothing.
    """
    items = values if isinstance(values, list) else [values]
    out: list[int] = []
    for value in items:
        if isinstance(value, int):
            out.append(value)
        elif isinstance(value, str) and value.strip().isdigit():
            out.append(int(value.strip()))
    return out


def _users_q(path: str):
    return lambda values: Q(**{f"{path}__in": user_ids(values)})


def _iexact_any(path: str):
    def build(values: list[str]) -> Q:
        query = Q(pk__in=[])
        for value in values:
            query |= Q(**{f"{path}__iexact": value})
        return query

    return build


def cursor_sort(
    sort: str | None, keys: dict[str, str], *, default: str, list_name: str
) -> tuple[str, bool, str]:
    """``(sort_field, descending, cursor_scope)`` for a one-key cursor sort.

    ``keys`` maps the frontend's column key to a NOT NULL column (or a
    coalesced annotation). The default order keeps the unscoped cursor the
    list always issued, so a cursor minted before ``sort`` existed still
    continues; every other order is scoped, so a cursor from another order
    restarts. More than one key, or an undeclared one, is refused.
    """
    pairs = parse_sort_spec(sort) or parse_sort_spec(default)
    if len(pairs) > 1 or pairs[0][0] not in keys:
        offered = ", ".join(sorted(keys))
        raise UnsupportedSort(
            f"sort {sort!r} is not available on {list_name}; one key of: {offered}, either direction"
        )
    key, descending = pairs[0]
    spec = f"-{key}" if descending else key
    scope = "" if spec == default else f"{list_name}:{spec}"
    return keys[key], descending, scope


def _since_q(path: str, fallback: str):
    """``path >= value``, reading ``fallback`` where ``path`` is still NULL."""
    return lambda value: Q(**{f"{path}__gte": value}) | Q(
        **{f"{path}__isnull": True, f"{fallback}__gte": value}
    )


def _until_q(path: str, fallback: str):
    return lambda value: Q(**{f"{path}__lte": value}) | Q(
        **{f"{path}__isnull": True, f"{fallback}__lte": value}
    )


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------


@strawberry.input(
    name="AstroliftDeploymentsFilter",
    description="The deployments list's declared filters. Unset fields do not filter; list values match any.",
)
class DeploymentsListFilterInput:
    triggered_by: list[str] | None = strawberry.field(
        default=None, description='Who started the deploy, as triggeredByUserId; "me" is the viewer.'
    )
    trigger_kind: list[str] | None = strawberry.field(
        default=None, description="push, manual, ci, scheduled, rollback or promotion."
    )
    started_after: dt.datetime | None = strawberry.field(
        default=None, description="Started at or after, reading createdAt for a deploy not started yet."
    )
    started_before: dt.datetime | None = strawberry.field(
        default=None, description="Started at or before, reading createdAt for a deploy not started yet."
    )


DEPLOYMENTS_FILTERS: dict[str, FilterField] = {
    "triggered_by": FilterField(q=_users_q("triggered_by_user_id"), me=True),
    "trigger_kind": FilterField("trigger_kind"),
    "started_after": FilterField(q=_since_q("started_at", "created_at")),
    "started_before": FilterField(q=_until_q("started_at", "created_at")),
}

DEPLOYMENTS_DEFAULT_SORT = "-created"

#: ``started`` is the start time, else the creation time for a queued deploy,
#: so the seek column is never NULL.
DEPLOYMENTS_SORT_FIELDS = {"created": "created_at", "started": "_started_at"}


def annotate_deployments(qs: QuerySet) -> QuerySet:
    return qs.annotate(_started_at=Coalesce("started_at", "created_at"))


# ---------------------------------------------------------------------------
# Job runs and command runs
# ---------------------------------------------------------------------------


@strawberry.input(
    name="AstroliftScheduledJobRunsFilter",
    description="The job runs list's declared filters. Unset fields do not filter; list values match any.",
)
class ScheduledJobRunsFilterInput:
    status: list[str] | None = strawberry.field(
        default=None, description="running, succeeded, failed or superseded."
    )
    trigger: list[str] | None = strawberry.field(
        default=None, description="scheduled (the cron fired it) or manual (someone ran it now)."
    )
    triggered_by: list[str] | None = strawberry.field(
        default=None, description='Who ran it now, as triggeredByUserId; "me" is the viewer.'
    )


SCHEDULED_JOB_RUNS_FILTERS: dict[str, FilterField] = {
    "status": FilterField("status"),
    "trigger": FilterField("trigger_kind"),
    "triggered_by": FilterField(q=_users_q("triggered_by_id"), me=True),
}


@strawberry.input(
    name="AstroliftCommandRunsFilter",
    description="The command runs list's declared filters. Unset fields do not filter; list values match any.",
)
class CommandRunsFilterInput:
    invoked_by: list[str] | None = strawberry.field(
        default=None, description='Who ran the command, as invokedByUserId; "me" is the viewer.'
    )


COMMAND_RUNS_FILTERS: dict[str, FilterField] = {
    "invoked_by": FilterField(q=_users_q("invoked_by_id"), me=True),
}


# ---------------------------------------------------------------------------
# Previews
# ---------------------------------------------------------------------------


@strawberry.input(
    name="AstroliftPreviewEnvironmentsFilter",
    description="The previews list's declared filters. Unset fields do not filter; list values match any.",
)
class PreviewEnvironmentsFilterInput:
    status: list[str] | None = strawberry.field(
        default=None, description="building, running, failed or torn_down."
    )
    opened_by: list[str] | None = strawberry.field(
        default=None,
        description='Who opened it: an SCM or platform login, case-insensitive; "me" is the viewer.',
    )
    manual: bool | None = strawberry.field(
        default=None, description="true: branch previews started by hand. false: pull request previews."
    )


def _opened_by_q(values: list[Any]) -> Q:
    """``"me"`` arrives as the viewer's pk and matches the recorded creator; a login matches by name."""
    query = Q(created_by_id__in=[v for v in values if isinstance(v, int)])
    return query | _iexact_any("opened_by_login")([v for v in values if isinstance(v, str)])


PREVIEW_FILTERS: dict[str, FilterField] = {
    "status": FilterField("status"),
    "opened_by": FilterField(q=_opened_by_q, me=True),
    "manual": FilterField("is_manual"),
}

PREVIEWS_DEFAULT_SORT = "-created"

#: ``deployed`` is the last deploy, else the creation time for a preview that
#: never went up, so the seek column is never NULL.
PREVIEW_SORT_FIELDS = {"created": "created_at", "deployed": "_deployed_at", "ttl": "ttl_until"}


def annotate_previews(qs: QuerySet) -> QuerySet:
    return qs.annotate(_deployed_at=Coalesce("last_deployed_at", "created_at"))


# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------


@strawberry.input(
    name="AstroliftEnvironmentsFilter",
    description="The environments list's declared filters. Unset fields do not filter; list values match any.",
)
class EnvironmentsListFilterInput:
    kind: list[str] | None = strawberry.field(default=None, description="production, preview or other.")
    app: list[str] | None = strawberry.field(default=None, description="App slugs.")
    cluster: list[str] | None = strawberry.field(default=None, description="Cluster slugs, case-insensitive.")
    region: list[str] | None = strawberry.field(
        default=None, description="The bound cluster's region, case-insensitive."
    )
    owner: list[str] | None = strawberry.field(
        default=None, description='The owner, as ownerUserId; "me" is the viewer.'
    )


def environment_kind_expr() -> Case:
    """``environment_kind`` in SQL, so the kind filters and sorts in the query."""
    return Case(
        When(name__startswith=PREVIEW_ENV_PREFIX, then=Value("preview")),
        When(_name_lower__in=PRODUCTION_ENVIRONMENT_NAMES, then=Value("production")),
        default=Value("other"),
        output_field=models.CharField(),
    )


def owned_by_q(values: Any, *, own: str = "created_by_id", app: str = "registered_app__created_by_id") -> Q:
    """The row's creator, else (when it recorded none) its app's creator."""
    ids = user_ids(values)
    return Q(**{f"{own}__in": ids}) | Q(**{f"{own}__isnull": True, f"{app}__in": ids})


ENVIRONMENTS_FILTERS: dict[str, FilterField] = {
    "kind": FilterField("_kind"),
    "app": FilterField("registered_app__slug"),
    "cluster": FilterField(q=_iexact_any("tenant_cluster__slug")),
    "region": FilterField(q=_iexact_any("tenant_cluster__region")),
    "owner": FilterField(q=owned_by_q, me=True),
}

ENVIRONMENTS_DEFAULT_SORT = "app,name"

ENVIRONMENTS_SORT_KEYS: dict[str, SortKey] = {
    "name": SortKey(Lower("name")),
    "app": SortKey("registered_app__slug"),
    "kind": SortKey("_kind"),
    "cluster": SortKey("tenant_cluster__slug"),
    "region": SortKey("tenant_cluster__region"),
    "created": SortKey("created_at"),
}


def annotate_environments(qs: QuerySet) -> QuerySet:
    return qs.annotate(_name_lower=Lower("name")).annotate(_kind=environment_kind_expr())
