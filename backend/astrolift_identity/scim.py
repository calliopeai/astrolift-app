"""
SCIM 2.0 policy + payload contract (#78, #91 — RFC 7644 + spec 27 §4.2).

Pure-Python module covering the SCIM 2.0 policy bits that wire
into the REST endpoint handlers:

* **Resource schemas** — User + Group SCIM payload shapes per
  RFC 7643 + RFC 7644.
* **Filter parser** — ``userName eq "alice"``, ``email co
  "@acme"``, ``displayName eq "ops"`` etc. (the minimum subset
  RFC 7644 requires).
* **Group-to-role mapping** — translate IdP group IDs to platform
  Role + scope assignments per spec 27 §4.2.
* **Pagination** — RFC 7644 ``startIndex`` (1-indexed) + ``count``.
* **Deprovisioning policy** — SCIM DELETE / ``active=false``
  deactivates rather than hard-deletes (matches the platform's
  soft-delete contract).
* **Rate limit** — 240/min/org per spec 27 §8.

The actual Django views + token auth + DB writes live in the
endpoint module; this is the policy + parser layer.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping, Sequence
from enum import Enum


# Spec 27 §8 + §9 — SCIM-specific rate limit + auth.
SCIM_RATE_LIMIT_PER_MIN = 240


# RFC 7644 SCIM Core Schemas.
SCHEMA_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCHEMA_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCHEMA_LIST_RESPONSE = (
    "urn:ietf:params:scim:api:messages:2.0:ListResponse"
)
SCHEMA_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"


# ---- payloads ------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ScimUser:
    """Subset of RFC 7643 User the platform actually consumes.

    SCIM payloads carry many optional fields the platform ignores
    (PhoneNumbers, Photos, X509Certificates). We never reject
    unknown fields — IdPs send them anyway. Caller copies into a
    Django User row using only this projection.
    """

    user_name: str
    email: str
    display_name: str
    active: bool = True
    external_id: str = ""
    """The IdP's stable user id. The platform stores this on the
    User row so subsequent SCIM PATCH/DELETE can target the
    correct row even if email changes."""

    def __post_init__(self) -> None:
        if not self.user_name:
            raise ValueError("user_name is required (RFC 7643)")
        if not self.email:
            raise ValueError("email is required for SCIM User")


@dataclasses.dataclass(frozen=True, slots=True)
class ScimGroup:
    """Subset of RFC 7643 Group."""

    display_name: str
    members_external_ids: tuple[str, ...] = ()
    external_id: str = ""

    def __post_init__(self) -> None:
        if not self.display_name:
            raise ValueError("display_name is required for SCIM Group")


# ---- parsing -------------------------------------------------------


class ScimError(ValueError):
    """SCIM-shaped error. Endpoint handler renders these as the
    SCIM Error response shape."""


def parse_user_payload(payload: Mapping) -> ScimUser:
    """Project a raw SCIM User JSON payload into ``ScimUser``.

    Handles the two common email shapes (RFC 7643 lets the IdP
    send either): ``emails: [{value, primary}]`` or
    ``emails: ["alice@acme.com"]`` (legacy/loose). Picks the
    primary entry when present, else the first.
    """
    if not isinstance(payload, dict):
        raise ScimError("payload must be a JSON object")

    user_name = str(payload.get("userName", "") or "").strip()
    name_obj = payload.get("name") or {}
    if isinstance(name_obj, dict):
        given = name_obj.get("givenName", "")
        family = name_obj.get("familyName", "")
        display = (
            payload.get("displayName")
            or f"{given} {family}".strip()
            or user_name
        )
    else:
        display = payload.get("displayName") or user_name

    email = _pick_primary_email(payload.get("emails", []))

    return ScimUser(
        user_name=user_name,
        email=email,
        display_name=str(display),
        active=bool(payload.get("active", True)),
        external_id=str(payload.get("externalId", "") or ""),
    )


def _pick_primary_email(emails: object) -> str:
    if isinstance(emails, str):
        return emails
    if not isinstance(emails, list):
        return ""
    primary: str = ""
    first: str = ""
    for e in emails:
        if isinstance(e, str) and not first:
            first = e
        elif isinstance(e, dict):
            value = e.get("value", "")
            if not first and value:
                first = value
            if e.get("primary") and value:
                primary = value
                break
    return primary or first


def parse_group_payload(payload: Mapping) -> ScimGroup:
    if not isinstance(payload, dict):
        raise ScimError("payload must be a JSON object")
    display = str(payload.get("displayName", "") or "")
    members = []
    for m in payload.get("members", []) or []:
        if isinstance(m, dict):
            value = m.get("value")
            if value:
                members.append(str(value))
    return ScimGroup(
        display_name=display,
        members_external_ids=tuple(members),
        external_id=str(payload.get("externalId", "") or ""),
    )


# ---- filter parser -------------------------------------------------
#
# RFC 7644 §3.4.2.2 minimum subset: <attribute> <op> <value>
# where op is one of: eq | ne | co (contains) | sw (starts-with)
# | ew (ends-with). Spec 27 says we support at least
# 'userName eq', 'email eq', 'displayName co'.

_FILTER_RE = re.compile(
    r'^\s*'
    r'(?P<attr>[A-Za-z][A-Za-z0-9_.]*)\s+'
    r'(?P<op>eq|ne|co|sw|ew)\s+'
    r'"(?P<value>[^"]*)"\s*$'
)


@dataclasses.dataclass(frozen=True, slots=True)
class ScimFilter:
    attribute: str
    operator: str
    value: str


def parse_filter(filter_str: str) -> ScimFilter | None:
    """Parse a SCIM filter expression. Returns None for empty
    input (caller treats as 'no filter')."""
    if not filter_str:
        return None
    m = _FILTER_RE.match(filter_str)
    if m is None:
        raise ScimError(
            f"unsupported filter syntax: {filter_str!r} "
            "(supported: <attr> {eq|ne|co|sw|ew} \"<value>\")"
        )
    return ScimFilter(
        attribute=m.group("attr"),
        operator=m.group("op"),
        value=m.group("value"),
    )


def filter_matches(*, filt: ScimFilter, user: ScimUser) -> bool:
    """Apply a parsed filter to a user. Caller iterates over a
    queryset and uses this to keep matches."""
    attr = filt.attribute
    if attr == "userName":
        target = user.user_name
    elif attr == "email" or attr == "emails.value":
        target = user.email
    elif attr == "displayName":
        target = user.display_name
    elif attr == "externalId":
        target = user.external_id
    elif attr == "active":
        # Bool comparison
        return (
            (filt.operator == "eq")
            == (str(user.active).lower() == filt.value.lower())
        )
    else:
        raise ScimError(
            f"filter attribute {attr!r} not supported"
        )
    op = filt.operator
    v = filt.value
    if op == "eq":
        return target == v
    if op == "ne":
        return target != v
    if op == "co":
        return v in target
    if op == "sw":
        return target.startswith(v)
    if op == "ew":
        return target.endswith(v)
    raise ScimError(f"unsupported operator {op!r}")


# ---- pagination ----------------------------------------------------


# RFC 7644 §3.4.2: server may cap count.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def normalize_pagination(
    *,
    start_index: int | None,
    count: int | None,
) -> tuple[int, int]:
    """RFC 7644 paging: 1-indexed start_index, count optional.
    Returns (zero_indexed_offset, page_size)."""
    si = start_index if start_index is not None else 1
    if si < 1:
        si = 1
    cnt = count if count is not None else DEFAULT_PAGE_SIZE
    if cnt < 0:
        cnt = 0
    if cnt > MAX_PAGE_SIZE:
        cnt = MAX_PAGE_SIZE
    # Convert 1-indexed startIndex to 0-indexed offset
    return si - 1, cnt


# ---- group -> role mapping -----------------------------------------


class ScopeKind(str, Enum):
    ORG = "org"
    TEAM = "team"
    PROJECT = "project"
    APP = "app"


@dataclasses.dataclass(frozen=True, slots=True)
class GroupRoleMapping:
    """Per spec 27 §4.2 — one IdP group maps to one Astrolift
    role with a scope. Multiple mappings stack (an IdP group
    can grant several roles)."""

    organization_id: int
    group_external_id: str
    role_id: int
    scope_kind: ScopeKind
    scope_id: int | None = None
    """None when scope_kind == ORG (the org itself is the scope)."""

    def __post_init__(self) -> None:
        if self.organization_id <= 0:
            raise ValueError("organization_id must be positive")
        if not self.group_external_id:
            raise ValueError("group_external_id is required")
        if self.scope_kind == ScopeKind.ORG and self.scope_id not in (None, self.organization_id):
            raise ValueError(
                "ORG scope must have scope_id None or the org_id"
            )
        if self.scope_kind != ScopeKind.ORG and self.scope_id is None:
            raise ValueError(
                f"{self.scope_kind.value} scope requires scope_id"
            )


def role_assignments_for_user(
    *,
    user_group_external_ids: Sequence[str],
    mappings: Sequence[GroupRoleMapping],
) -> tuple[GroupRoleMapping, ...]:
    """Resolve which (role, scope) pairs a user gets when they're
    in a given set of IdP groups.

    Multiple groups → multiple mappings → multiple role bindings.
    Deduplicated by (role_id, scope_kind, scope_id) so two groups
    granting the same role don't produce duplicate binding rows.
    """
    seen: set[tuple[int, ScopeKind, int | None]] = set()
    out: list[GroupRoleMapping] = []
    user_groups = set(user_group_external_ids)
    for m in mappings:
        if m.group_external_id not in user_groups:
            continue
        key = (m.role_id, m.scope_kind, m.scope_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return tuple(out)


# ---- deprovisioning ------------------------------------------------


def is_deprovision_request(*, payload: Mapping) -> bool:
    """RFC 7644 deprovisioning is either a DELETE (caller decides
    based on HTTP method) or a PATCH/PUT setting ``active=false``.
    Returns True for the latter shape."""
    if not isinstance(payload, dict):
        return False
    if payload.get("active") is False:
        return True
    # SCIM PATCH op shape: ``Operations: [{op:replace, path:active, value:false}]``
    for op in payload.get("Operations", []) or []:
        if not isinstance(op, dict):
            continue
        if (
            str(op.get("op", "")).lower() == "replace"
            and op.get("path") == "active"
            and op.get("value") is False
        ):
            return True
    return False
