"""Plan a manifest's ``[[managed_services]]`` against the platform (#2606).

``plan_managed_service_match`` is a pure function over two inputs: what the
stored manifest declares (``ServiceSpec``) and what the platform holds
(``RecordedService`` and the project's shared rows). It returns the changes an
apply would make, without making any of them. No Django and no Temporal here,
so the comparison is unit-testable on its own; ``managed_service_match_db``
gathers the two sides from the database.

The actions mirror what ``reconcile_managed_services`` does:

* ``create``: no platform row; the apply creates it and starts provisioning.
* ``reconfigure``: an app-owned row whose desired state differs; the apply
  updates it and runs the update workflow.
* ``bind``: a project service attached to an environment, or re-bound to a
  different workload list (the apply attaches or rebinds it).
* ``retry``: the row is unchanged but its last provision attempt failed.
* ``remove`` / ``detach``: a manifest-managed row or attachment the manifest no
  longer declares; the apply deprovisions or detaches it.
* ``blocked``: the apply would refuse; ``reason`` says why. A plan with any
  blocked entry must not be applied.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

APP = "app"
PROJECT = "project"

CREATE = "create"
RECONFIGURE = "reconfigure"
BIND = "bind"
RETRY = "retry"
REMOVE = "remove"
DETACH = "detach"
BLOCKED = "blocked"


@dataclasses.dataclass(frozen=True, slots=True)
class ServiceSpec:
    """One managed service's desired (or recorded) state, keyed by ``key``.

    For a project service, ``environment`` is the attachment's environment and
    ``bind_workloads`` is that attachment's binding; the shared row's own
    fields are compared separately through ``project_rows``.
    """

    owner_scope: str
    environment: str
    kind: str
    name: str
    variant: str = ""
    isolation: str = ""
    config: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    bind_workloads: tuple[str, ...] = ("*",)
    deletion_policy: str = "retain"
    lifecycle_policy: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.owner_scope, self.environment, self.kind, self.name)


@dataclasses.dataclass(frozen=True, slots=True)
class RecordedService:
    """A platform row (app scope) or attachment (project scope) that exists."""

    spec: ServiceSpec
    manifest_managed: bool
    backend_ref: str = ""
    needs_retry: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class PlannedChange:
    action: str
    owner_scope: str
    environment: str
    kind: str
    name: str
    reason: str = ""


def _change(action: str, spec: ServiceSpec, reason: str = "") -> PlannedChange:
    return PlannedChange(action, spec.owner_scope, spec.environment, spec.kind, spec.name, reason)


def _app_fields_differ(recorded: ServiceSpec, declared: ServiceSpec) -> bool:
    return (
        recorded.variant != declared.variant
        or recorded.isolation != declared.isolation
        or dict(recorded.config) != dict(declared.config)
        or tuple(recorded.bind_workloads) != tuple(declared.bind_workloads)
        or recorded.deletion_policy != declared.deletion_policy
        or dict(recorded.lifecycle_policy) != dict(declared.lifecycle_policy)
    )


def _plan_app(spec: ServiceSpec, rec: RecordedService | None) -> list[PlannedChange]:
    if rec is None:
        return [_change(CREATE, spec)]
    if not rec.manifest_managed:
        return [_change(BLOCKED, spec, "already exists imperatively in this environment")]
    if rec.backend_ref and (rec.spec.variant != spec.variant or rec.spec.isolation != spec.isolation):
        return [_change(BLOCKED, spec, "changes variant or isolation; remove it, then add the replacement")]
    if _app_fields_differ(rec.spec, spec):
        return [_change(RECONFIGURE, spec)]
    if rec.needs_retry:
        return [_change(RETRY, spec)]
    return []


def _plan_project(
    spec: ServiceSpec,
    rec: RecordedService | None,
    shared: ServiceSpec | None,
) -> list[PlannedChange]:
    if rec is not None and not rec.manifest_managed:
        return [_change(BLOCKED, spec, "already attached imperatively to this environment")]
    if shared is not None and (
        shared.variant != spec.variant
        or shared.isolation != spec.isolation
        or dict(shared.config) != dict(spec.config)
    ):
        return [_change(BLOCKED, spec, "shared project service exists with a different desired state")]
    changes: list[PlannedChange] = []
    if shared is None:
        changes.append(_change(CREATE, spec))
    if rec is None or tuple(rec.spec.bind_workloads) != tuple(spec.bind_workloads):
        changes.append(_change(BIND, spec))
    if rec is not None and rec.needs_retry:
        changes.append(_change(RETRY, spec))
    return changes


def plan_managed_service_match(
    declared: Sequence[ServiceSpec],
    recorded: Sequence[RecordedService],
    project_rows: Mapping[tuple[str, str], ServiceSpec] | None = None,
) -> tuple[PlannedChange, ...]:
    """The changes an apply of ``declared`` would make, in declared order,
    then removals. ``project_rows`` maps ``(kind, name)`` to the shared project
    row's own state, for project services that already exist."""
    shared_rows = dict(project_rows or {})
    by_key = {rec.spec.key: rec for rec in recorded}
    declared_keys = {spec.key for spec in declared}
    changes: list[PlannedChange] = []
    for spec in declared:
        rec = by_key.get(spec.key)
        if spec.owner_scope == PROJECT:
            changes.extend(_plan_project(spec, rec, shared_rows.get((spec.kind, spec.name))))
        else:
            changes.extend(_plan_app(spec, rec))
    for rec in recorded:
        if rec.spec.key in declared_keys or not rec.manifest_managed:
            continue
        if rec.spec.owner_scope == PROJECT:
            changes.append(_change(DETACH, rec.spec))
        else:
            changes.append(_change(REMOVE, rec.spec))
    return tuple(changes)


def plan_digest(declared: Sequence[ServiceSpec], changes: Sequence[PlannedChange]) -> str:
    """A stable digest of the declared values and the planned changes, so an
    apply refuses a plan whose target moved since the operator reviewed it."""
    body = json.dumps(
        {
            "declared": [dataclasses.asdict(spec) for spec in declared],
            "changes": [dataclasses.asdict(c) for c in changes],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=list,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def plan_is_blocked(changes: Sequence[PlannedChange]) -> bool:
    return any(c.action == BLOCKED for c in changes)
