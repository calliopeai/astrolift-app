"""Scoped metadata-only proposal walks; changing queues require a fresh walk."""

from __future__ import annotations

import hashlib
import json
import uuid

from django.core import signing
from django.db.models import BinaryField, Count, DecimalField, Func, Max, Q, Sum, TextField, Value
from django.db.models.functions import Cast, Coalesce, Concat, Substr
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware
from graphql import GraphQLError

from astrolift_agents.visibility import _token_allows_org, _token_apps
from astrolift_identity.api_tokens import get_current_api_token
from astrolift_identity.operation_visibility import visible_operation_rows
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import live_app_owners
from astrolift_services.models import SecretChangeApproval, SecretChangeProposal
from core.permissions import Permission, PermissionDenied
from core.tenancy import get_current_tenant

CURSOR_SALT = "astrolift.secret-proposals.page.v1"
CURSOR_MAX_AGE = 15 * 60
METADATA_FIELDS = (
    "id",
    "guid",
    "registered_app_id",
    "registered_app__slug",
    "environment_name",
    "proposer_id",
    "proposer__first_name",
    "proposer__last_name",
    "proposer__username",
    "proposer__email",
    "op",
    "status",
    "required_approver_count",
    "expires_at",
    "decided_at",
    "applied_at",
    "created_at",
    "updated_at",
)


def metadata_proposals(org_id, app_slug=None):
    if not _token_allows_org(org_id, Permission.APP_READ):
        raise PermissionDenied(Permission.APP_READ, None, "credential cannot access the current organization")
    apps = _token_apps(
        live_app_owners(
            RegisteredApp.objects.filter(organization_id=org_id, organization__deleted_at__isnull=True)
        ),
        Permission.APP_READ,
    )
    qs = SecretChangeProposal.objects.filter(
        registered_app__organization_id=org_id,
        registered_app_id__in=apps.values("pk"),
        registered_app__deleted_at__isnull=True,
        deleted_at__isnull=True,
    )
    if app_slug is not None:
        qs = qs.filter(registered_app__slug=app_slug)
    qs = qs.annotate(
        _policy_approvals=Count(
            "approvals__approver_id",
            filter=Q(approvals__decision="approved", approvals__deleted_at__isnull=True),
            distinct=True,
        )
    )
    return visible_operation_rows(
        qs,
        Permission.APP_READ,
        approvals_field="_policy_approvals",
        app_wide_operations_field="op",
    )


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _identity_sums(identity):
    # PostgreSQL's built-in SHA-256 needs no extension. Hash each fixed-size
    # metadata identity in the database, then sum all four independent limbs.
    # Numeric sums avoid bigint overflow; neither GUID arrays nor whole-queue
    # concatenations are allocated or returned. Do not DISTINCT hash limbs:
    # only the scoped source rows are deduplicated, not their hash values.
    fingerprint = Func(
        Func(
            Func(identity, Value("UTF8"), function="pg_catalog.convert_to", output_field=BinaryField()),
            function="pg_catalog.sha256",
            output_field=BinaryField(),
        ),
        Value("hex"),
        function="pg_catalog.encode",
        output_field=TextField(),
    )
    return {
        f"identity_{index}": Sum(
            Func(
                Substr(fingerprint, index * 16 + 1, 16),
                template="(('x' || %(expressions)s)::bit(64)::bigint)::numeric",
                output_field=DecimalField(),
            )
        )
        for index in range(4)
    }


def _revision(qs):
    # IN subqueries preserve present visibility while making each proposal
    # and each vote contribute exactly once, independent of authorization
    # joins. Aggregate queries select metadata only and return fixed-size
    # lifecycle facts and fingerprint sums to Python.
    visible_ids = qs.order_by().values("pk")
    proposals = SecretChangeProposal.objects.filter(pk__in=visible_ids).aggregate(
        count=Count("pk", distinct=True),
        updated=Max("updated_at"),
        versions=Sum("version"),
        **{
            status: Count("pk", distinct=True, filter=Q(status=status))
            for status in SecretChangeProposal.Status.values
        },
        **_identity_sums(Cast("guid", TextField())),
    )
    vote_identity = Concat(
        Cast("proposal__guid", TextField()),
        Value(":"),
        Cast("guid", TextField()),
        Value(":"),
        Cast("approver_id", TextField()),
        Value(":"),
        "decision",
        Value(":"),
        Coalesce(Cast("deleted_at", TextField()), Value(""), output_field=TextField()),
        output_field=TextField(),
    )
    votes = SecretChangeApproval.objects.filter(proposal_id__in=visible_ids).aggregate(
        updated=Max("updated_at"),
        versions=Sum("version"),
        **_identity_sums(vote_identity),
    )
    return _digest({"proposals": proposals, "votes": votes})


def _error(code):
    message = (
        "Secret proposal queue changed or the cursor expired. Restart from the first page."
        if code == "STALE_CURSOR"
        else "Invalid secret proposal cursor. Restart from the first page."
    )
    raise GraphQLError(message, extensions={"code": code})


def proposal_page(qs, *, app_slug, status, limit, after):
    if not 1 <= limit <= 200:
        raise GraphQLError("limit must be between 1 and 200", extensions={"code": "INVALID_ARGUMENT"})
    if status is not None and status not in SecretChangeProposal.Status.values:
        raise GraphQLError("Unknown secret proposal status", extensions={"code": "INVALID_ARGUMENT"})
    tenant = get_current_tenant()
    token = get_current_api_token()
    scope = _digest([tenant, str(token.guid) if token else None, app_slug, status])
    cursor = None
    if after is not None:
        if not isinstance(after, str) or len(after) > 2048:
            _error("INVALID_CURSOR")
        try:
            cursor = signing.loads(after, salt=CURSOR_SALT, max_age=CURSOR_MAX_AGE)
            if not isinstance(cursor, dict) or set(cursor) != {"v", "scope", "revision", "created", "guid"}:
                _error("INVALID_CURSOR")
            if cursor["v"] != 1 or cursor["scope"] != scope:
                _error("INVALID_CURSOR")
            stamp = parse_datetime(cursor["created"])
            ident = uuid.UUID(cursor["guid"])
            if not stamp or not is_aware(stamp) or not ident.int or str(ident) != cursor["guid"]:
                _error("INVALID_CURSOR")
        except signing.SignatureExpired:
            _error("STALE_CURSOR")
        except (signing.BadSignature, TypeError, ValueError, KeyError):
            _error("INVALID_CURSOR")
    revision = _revision(qs)
    if cursor and cursor["revision"] != revision:
        _error("STALE_CURSOR")
    filtered = qs.filter(status=status) if status is not None else qs
    total = filtered.count()
    if cursor:
        if not filtered.filter(created_at=stamp, guid=ident).exists():
            _error("STALE_CURSOR")
        filtered = filtered.filter(Q(created_at__lt=stamp) | Q(created_at=stamp, guid__lt=ident))
    rows = list(
        filtered.select_related("registered_app", "proposer")
        .only(*METADATA_FIELDS)
        .order_by("-created_at", "-guid")[: limit + 1]
    )
    if _revision(qs) != revision:
        _error("STALE_CURSOR")
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if more:
        last = rows[-1]
        next_cursor = signing.dumps(
            {
                "v": 1,
                "scope": scope,
                "revision": revision,
                "created": last.created_at.isoformat(),
                "guid": str(last.guid),
            },
            salt=CURSOR_SALT,
        )
    return rows, next_cursor, total, not more
