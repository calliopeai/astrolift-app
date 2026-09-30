"""SCIM Group CRUD and atomic membership PATCH (RFC 7643/7644, #2164)."""

from __future__ import annotations

import re
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch, prefetch_related_objects
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from astrolift_identity.models import Member, Organization, ScimGroup
from astrolift_identity.scim import (
    SCHEMA_GROUP,
    SCHEMA_LIST_RESPONSE,
    ScimError,
    normalize_pagination,
    parse_filter,
)
from astrolift_identity.scim_views import (
    SCIM_BASE_PATH,
    _authenticated_org,
    _error,
    _int_param,
    _json_body,
    _ok,
)
from core.permissions import route_auth


def _resource(group):
    members = getattr(group, "_scim_members", None)
    if members is None:
        members = (
            group.members.filter(
                scope_kind="ORG", scope_id=group.organization_id, is_active=True, user__is_active=True
            )
            .select_related("user")
            .order_by("pk")
        )
    resource = {
        "schemas": [SCHEMA_GROUP],
        "id": str(group.guid),
        "displayName": group.display_name,
        "members": [
            {
                "value": str(member.guid),
                "display": member.user.username,
                "$ref": f"{SCIM_BASE_PATH}Users/{member.guid}",
                "type": "User",
            }
            for member in members
        ],
        "meta": {
            "resourceType": "Group",
            "created": group.created_at.isoformat(),
            "lastModified": group.updated_at.isoformat(),
            "location": f"{SCIM_BASE_PATH}Groups/{group.guid}",
            "version": f'W/"{group.version}"',
        },
    }
    if group.external_id:
        resource["externalId"] = group.external_id
    return resource


def _response(group, *, status=200):
    response = _ok(_resource(group), status=status)
    response["ETag"] = f'W/"{group.version}"'
    response["Content-Location"] = f"{SCIM_BASE_PATH}Groups/{group.guid}"
    if status == 201:
        response["Location"] = f"{SCIM_BASE_PATH}Groups/{group.guid}"
    return response


def _text(value, name, *, empty=False):
    if not isinstance(value, str) or len(value) > 255 or (not empty and not value.strip()):
        raise ScimError(
            f"{name} must be a {'possibly empty ' if empty else 'nonempty '}string of at most 255 characters"
        )
    return value.strip()


class ScimUniquenessError(ScimError):
    pass


def _members(org, raw):
    if not isinstance(raw, list):
        raise ScimError("members must be a list of User references")
    ids = set()
    for member in raw:
        if not isinstance(member, dict) or not isinstance(member.get("value"), str):
            raise ScimError("members must contain User reference objects with a value")
        try:
            ids.add(uuid.UUID(member["value"]))
        except ValueError as exc:
            raise ScimError("member not found in this organization") from exc
    members = list(
        Member.objects.select_for_update().filter(
            guid__in=ids, scope_kind="ORG", scope_id=org.pk, is_active=True, user__is_active=True
        )
    )
    if len(members) != len(ids):
        raise ScimError("member not found in this organization")
    return {member.pk for member in members}


def _compare(value, filt):
    left, right = (
        (value.casefold(), filt.value.casefold())
        if filt.attribute.casefold() == "displayname"
        else (value, filt.value)
    )
    return {
        "eq": lambda: left == right,
        "ne": lambda: left != right,
        "co": lambda: right in left,
        "sw": lambda: left.startswith(right),
        "ew": lambda: left.endswith(right),
    }[filt.operator]()


@csrf_exempt
@require_http_methods(["GET", "POST"])
@route_auth(credential="organization SCIM bearer", scope="credential organization")
def scim_groups(request):
    org = _authenticated_org(request)
    if org is None:
        return _error("invalid or disabled SCIM credential", status=401)
    if request.method == "GET":
        try:
            filt = parse_filter(request.GET.get("filter", ""))
            if filt and filt.attribute.casefold() not in {"displayname", "externalid", "id"}:
                raise ScimError("unsupported Group filter attribute")
            offset, size = normalize_pagination(
                start_index=_int_param(request, "startIndex"), count=_int_param(request, "count")
            )
        except (ScimError, ValueError) as exc:
            return _error(str(exc), status=400, scim_type="invalidFilter")
        rows = list(ScimGroup.objects.filter(organization=org).order_by("pk"))
        if filt:
            field = {"displayname": "display_name", "externalid": "external_id", "id": "guid"}[
                filt.attribute.casefold()
            ]
            rows = [row for row in rows if _compare(str(getattr(row, field)), filt)]
        page = rows[offset : offset + size]
        prefetch_related_objects(
            page,
            Prefetch(
                "members",
                to_attr="_scim_members",
                queryset=Member.objects.filter(
                    scope_kind="ORG", scope_id=org.pk, is_active=True, user__is_active=True
                )
                .select_related("user")
                .order_by("pk"),
            ),
        )
        return _ok(
            {
                "schemas": [SCHEMA_LIST_RESPONSE],
                "totalResults": len(rows),
                "startIndex": offset + 1,
                "itemsPerPage": len(page),
                "Resources": [_resource(row) for row in page],
            }
        )
    body = _json_body(request)
    if body is None:
        return _error("body must be a JSON object", status=400, scim_type="invalidSyntax")
    try:
        with transaction.atomic():
            # Serialize a create with the same external id before uniqueness
            # validation, including clients retrying parallel provisioning.
            Organization.objects.select_for_update().get(pk=org.pk)
            name = _text(body.get("displayName"), "displayName")
            external = _text(body.get("externalId", ""), "externalId", empty=True)
            members = _members(org, body.get("members", []))
            if external and ScimGroup.objects.filter(organization=org, external_id=external).exists():
                return _error("externalId is already in use", status=409, scim_type="uniqueness")
            group = ScimGroup.objects.create(organization=org, display_name=name, external_id=external)
            group.members.set(members)
            return _response(group, status=201)
    except ScimError as exc:
        return _error(str(exc), status=400, scim_type="invalidValue")


def _replace_external_id(group, value):
    value = _text(value, "externalId", empty=True)
    if value != group.external_id:
        if (
            value
            and ScimGroup.objects.filter(organization_id=group.organization_id, external_id=value)
            .exclude(pk=group.pk)
            .exists()
        ):
            raise ScimUniquenessError("externalId is already in use")
        group.retired_external_ids = sorted(set(group.retired_external_ids) | {group.group_external_id})
        group.external_id = value


def _patch(group, org, body):
    operations = body.get("Operations")
    if not isinstance(operations, list) or not operations:
        raise ScimError("Operations must be a nonempty list")
    members = set(group.members.values_list("pk", flat=True))
    for operation in operations:
        if not isinstance(operation, dict):
            raise ScimError("each operation must be an object")
        op = str(operation.get("op", "")).casefold()
        if op not in {"add", "replace", "remove"}:
            raise ScimError("unsupported PATCH operation")
        path = operation.get("path", "")
        if not isinstance(path, str):
            raise ScimError("path must be a string")
        path = path.removeprefix(f"{SCHEMA_GROUP}:")
        value = operation.get("value")
        if not path:
            if op == "remove" or not isinstance(value, dict) or not value:
                raise ScimError("an operation without path requires an attribute object")
            for field, item in value.items():
                members = _patch_field(group, org, members, op, field, item)
        else:
            members = _patch_field(group, org, members, op, path, value)
    return members


def _patch_field(group, org, members, op, path, value):
    lower = path.casefold()
    if lower == "displayname" and op in {"add", "replace"}:
        group.display_name = _text(value, "displayName")
    elif lower == "externalid" and op in {"add", "replace"}:
        _replace_external_id(group, value)
    elif lower == "members":
        if op == "remove" and value is None:
            return set()
        incoming = _members(org, value)
        if op == "replace":
            return incoming
        return members | incoming if op == "add" else members - incoming
    else:
        match = re.fullmatch(r'members\[value\s+eq\s+"([^"]+)"\]', path, flags=re.IGNORECASE)
        if op != "remove" or match is None:
            raise ScimError("unsupported Group PATCH path")
        # Removal is idempotent, even after the referenced user was removed.
        try:
            guid = uuid.UUID(match[1])
        except ValueError as exc:
            raise ScimError("invalid member reference") from exc
        ids = Member.objects.filter(guid=guid, scope_kind="ORG", scope_id=org.pk).values_list("pk", flat=True)
        return members - set(ids)
    return members


@csrf_exempt
@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
@route_auth(credential="organization SCIM bearer", scope="credential organization")
def scim_group_detail(request, group_guid):
    org = _authenticated_org(request)
    if org is None:
        return _error("invalid or disabled SCIM credential", status=401)
    try:
        with transaction.atomic():
            try:
                if request.method != "GET":
                    Organization.objects.select_for_update().get(pk=org.pk)
                group = (
                    ScimGroup.objects.select_for_update().filter(guid=group_guid, organization=org).first()
                )
            except (ValidationError, ValueError):
                group = None
            if group is None:
                return _error("group not found", status=404)
            if request.method == "GET":
                return _response(group)
            etag = request.headers.get("If-Match")
            if etag and etag not in {"*", f'W/"{group.version}"'}:
                return _error("group version changed", status=412)
            if request.method == "DELETE":
                group.members.clear()
                group.soft_delete()
                return HttpResponse(status=204)
            body = _json_body(request)
            if body is None:
                return _error("body must be a JSON object", status=400, scim_type="invalidSyntax")
            if request.method == "PUT":
                group.display_name = _text(body.get("displayName"), "displayName")
                _replace_external_id(group, body.get("externalId", group.external_id))
                members = _members(org, body.get("members", []))
            else:
                members = _patch(group, org, body)
            group.save(
                update_fields=["display_name", "external_id", "retired_external_ids", "updated_at", "version"]
            )
            group.members.set(members)
            return _response(group)
    except ScimUniquenessError as exc:
        return _error(str(exc), status=409, scim_type="uniqueness")
    except ScimError as exc:
        return _error(str(exc), status=400, scim_type="invalidValue")
