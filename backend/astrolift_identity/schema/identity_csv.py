"""Complete CSV reads of the same authorized identity list querysets."""

import csv
import datetime as dt
import io
import re
from itertools import islice

import strawberry

from astrolift_identity.schema import identity_lists as lists

_FORMULA = re.compile(r"^[=+\-@\t\r]")


@strawberry.type(name="AstroliftIdentityCsvExport")
class IdentityCsvExport:
    filename: str
    content: str
    row_count: int
    content_type: str = "text/csv; charset=utf-8"


def _cell(value):
    text = "" if value is None else value.isoformat() if isinstance(value, dt.datetime) else str(value)
    return "'" + text if _FORMULA.match(text) else text


def _export(filename, headers, values):
    content = io.StringIO(newline="")
    writer = csv.writer(content)
    writer.writerow(headers)
    count = 0
    for row in values:
        writer.writerow([_cell(value) for value in row])
        count += 1
    return IdentityCsvExport(filename=filename, content=content.getvalue(), row_count=count)


def _batches(qs):
    rows = qs.iterator(chunk_size=200)
    while batch := list(islice(rows, 200)):
        yield batch


def members_csv(qs, org_id):
    from astrolift_identity.schema.queries import _last_active_by_user_id, _role_bindings_qs

    def values():
        for batch in _batches(qs):
            active = _last_active_by_user_id(batch)
            _, teams = lists.member_teams(batch, org_id)
            bindings: dict[int, list[str]] = {}
            for binding in (
                _role_bindings_qs()
                .filter(user_id__in={m.user_id for m in batch})
                .order_by("role__slug", "scope_kind", "scope_id", "pk")
            ):
                bindings.setdefault(binding.user_id, []).append(f"{binding.role.slug}@{binding.scope_kind}")
            for member in batch:
                yield (
                    "user",
                    member.user.username,
                    member.user.email,
                    " ".join(bindings.get(member.user_id, [])),
                    " ".join(t.slug for t in teams.get(member.user_id, [])),
                    member.lifecycle,
                    active.get(member.user_id),
                    member.joined_at or member.created_at,
                )

    return _export(
        "members.csv",
        ("Kind", "Name", "Email", "Roles", "Teams", "Status", "Last active", "Joined"),
        values(),
    )


def invitations_csv(qs):
    from astrolift_identity.schema.queries import _tenant_ids

    def values():
        for batch in _batches(qs):
            lists.invitation_activity(batch, _tenant_ids()[0])
            for invitation in batch:
                yield (
                    "invitation",
                    invitation.email,
                    invitation.email,
                    invitation.role.slug if invitation.role_id else "",
                    "",
                    invitation.status,
                    getattr(invitation, "_last_active", None),
                    invitation.created_at,
                )

    return _export(
        "invitations.csv",
        (
            "Kind",
            "Name",
            "Email",
            "Roles",
            "Teams",
            "Status",
            "Last active",
            "Joined",
        ),
        values(),
    )


def bindings_csv(qs):
    from astrolift_identity.schema.queries import _last_active_by_user_id, _resolve_source_scope_labels

    def values():
        for batch in _batches(qs):
            labels = _resolve_source_scope_labels(batch)
            active = _last_active_by_user_id(batch)
            for binding in batch:
                yield (
                    binding.user.username if binding.user_id else f"group:{binding.group_external_id}",
                    binding.user.email if binding.user_id else "",
                    "user" if binding.user_id else "group",
                    binding.role.slug,
                    binding.scope_kind,
                    labels.get((binding.scope_kind, binding.scope_id), ""),
                    binding.granted_at,
                    binding.expires_at,
                    active.get(binding.user_id),
                    str(binding.guid),
                )

    return _export(
        "role-bindings.csv",
        (
            "Subject",
            "Email",
            "Kind",
            "Role",
            "Scope",
            "Source",
            "Granted",
            "Expires",
            "Last active",
            "Binding ID",
        ),
        values(),
    )
