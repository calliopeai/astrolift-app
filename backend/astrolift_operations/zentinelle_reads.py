"""Read Zentinelle's usage, policies and audit stream for an organization (#1888).

The reads go out as the organization's install, with the credential
``zentinelle_connect`` holds, on the install channel
(``/api/zentinelle/v1/astrolift/...``, ``AstroliftInstallAuthentication``).
Compatible Zentinelle source implements these install-scoped reads. Deployed
availability and enrollment remain separate runtime checks; this module does
not establish that a particular installation serves them. Each read takes
``tenant_id``, one of the tenants the enrollment code scoped the install to,
and requests only agents this install minted keys for:

* ``GET .../astrolift/usage?since=``: Astrolift consumes ``deployment_id``,
  ``provider``, ``model``, ``requests``, ``input_tokens`` and ``output_tokens``
  from the response's ``usage`` rows. Additional fields, including monetary
  fields returned by Zentinelle, are ignored rather than relayed.
* ``GET .../astrolift/policies`` answers ``{"policies": [{id, name,
  policy_type, enforcement, scope_type, priority, description}]}``.
* ``GET .../astrolift/audit?since=&until=&cursor=&limit=`` answers
  ``{"events": [{id, timestamp, action, resource_type, resource_id,
  resource_name, deployment_id, agent_id, ext_user_id}], "next_cursor": ...}``,
  oldest first.

``deployment_id`` is what Astrolift sends when it mints a run's key: the
agent workload's slug. Repeating it narrows a read to those agents; leaving
it out reads the whole tenant. Astrolift decides which agents a viewer may
see before it asks, so Zentinelle never sees a viewer, only the install.

A deployment without these endpoints answers 404; that is reported as an
upgrade requirement rather than an empty usage result. Source support does
not prove deployed token telemetry.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from django.utils.dateparse import parse_datetime

from astrolift_operations.models import ZentinelleConnection
from astrolift_operations.zentinelle_connect import (
    ZentinelleConnectError,
    _install_call,
    _refused,
    _require_connected,
)
from core.mutations import ErrorCode

AUDIT_PAGE_MAX = 500


@dataclasses.dataclass(frozen=True, slots=True)
class UsageRow:
    agent_slug: str
    provider: str
    model: str
    requests: int
    input_tokens: int
    output_tokens: int


@dataclasses.dataclass(frozen=True, slots=True)
class Policy:
    id: str
    name: str
    policy_type: str
    enforcement: str
    scope: str
    priority: int
    description: str


@dataclasses.dataclass(frozen=True, slots=True)
class AuditEvent:
    id: str
    occurred_at: datetime | None
    action: str
    resource_type: str
    resource_id: str
    resource_name: str
    agent_slug: str
    agent_id: str
    actor: str


def connection_for(org_id: int | None) -> ZentinelleConnection:
    """The organization's live, connected Zentinelle connection, or the refusal to show."""
    connection = (
        ZentinelleConnection.objects.filter(organization_id=org_id).first() if org_id is not None else None
    )
    if connection is None:
        raise ZentinelleConnectError(
            ErrorCode.PRECONDITION, "this organization is not connected to Zentinelle"
        )
    _require_connected(connection)
    if not connection.tenant_ids:
        raise ZentinelleConnectError(
            ErrorCode.PRECONDITION,
            "Zentinelle scoped this organization's install to no tenant; connect again with a new enrollment code",
        )
    return connection


def _get(
    connection: ZentinelleConnection, path: str, what: str, params: list[tuple[str, str]]
) -> dict[str, Any]:
    query = urlencode([("tenant_id", connection.tenant_ids[0]), *params])
    reply = _install_call(connection, "GET", f"{path}?{query}")
    if reply.status == 404:
        raise ZentinelleConnectError(
            ErrorCode.PRECONDITION,
            f"this Zentinelle does not serve {what} to Astrolift (no {path} endpoint, "
            "calliopeai/zentinelle#390); upgrade it",
            status=reply.status,
        )
    if not reply.ok:
        raise _refused(reply, f"the {what} read")
    return reply.body


def _agents(agent_slugs: Iterable[str] | None) -> list[tuple[str, str]]:
    return [("deployment_id", slug) for slug in sorted(set(agent_slugs or ()))]


def _list(body: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = body.get(key)
    if not isinstance(rows, list):
        raise ZentinelleConnectError(ErrorCode.INTERNAL, f"Zentinelle's answer carried no {key} list")
    return [row for row in rows if isinstance(row, dict)]


def _str(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return "" if value is None else str(value)


def _int(row: dict[str, Any], key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def usage(
    connection: ZentinelleConnection, *, since: datetime, agent_slugs: Iterable[str] | None = None
) -> list[UsageRow]:
    """Model token usage since ``since``, per agent, provider and model."""
    body = _get(connection, "/usage", "usage", [("since", since.isoformat()), *_agents(agent_slugs)])
    return [
        UsageRow(
            agent_slug=_str(row, "deployment_id"),
            provider=_str(row, "provider"),
            model=_str(row, "model"),
            requests=_int(row, "requests"),
            input_tokens=_int(row, "input_tokens"),
            output_tokens=_int(row, "output_tokens"),
        )
        for row in _list(body, "usage")
    ]


def policies(connection: ZentinelleConnection, *, agent_slugs: Iterable[str] | None = None) -> list[Policy]:
    """The policies in effect for those agents, or for the tenant."""
    body = _get(connection, "/policies", "policies", _agents(agent_slugs))
    return [
        Policy(
            id=_str(row, "id"),
            name=_str(row, "name"),
            policy_type=_str(row, "policy_type"),
            enforcement=_str(row, "enforcement"),
            scope=_str(row, "scope_type"),
            priority=_int(row, "priority"),
            description=_str(row, "description"),
        )
        for row in _list(body, "policies")
    ]


def audit_page(
    connection: ZentinelleConnection,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str | None = None,
    limit: int = 100,
    agent_slugs: Iterable[str] | None = None,
) -> tuple[list[AuditEvent], str | None]:
    """One page of the evidence and audit stream, oldest first, and the next page's cursor."""
    params = [("limit", str(max(1, min(limit, AUDIT_PAGE_MAX))))]
    if since is not None:
        params.append(("since", since.isoformat()))
    if until is not None:
        params.append(("until", until.isoformat()))
    if cursor:
        params.append(("cursor", cursor))
    body = _get(connection, "/audit", "the audit stream", [*params, *_agents(agent_slugs)])
    events = []
    for row in _list(body, "events"):
        raw = row.get("timestamp")
        events.append(
            AuditEvent(
                id=_str(row, "id"),
                occurred_at=parse_datetime(raw) if isinstance(raw, str) else None,
                action=_str(row, "action"),
                resource_type=_str(row, "resource_type"),
                resource_id=_str(row, "resource_id"),
                resource_name=_str(row, "resource_name"),
                agent_slug=_str(row, "deployment_id"),
                agent_id=_str(row, "agent_id"),
                actor=_str(row, "ext_user_id"),
            )
        )
    next_cursor = body.get("next_cursor")
    return events, next_cursor if isinstance(next_cursor, str) and next_cursor else None
