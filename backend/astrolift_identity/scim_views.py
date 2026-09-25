"""SCIM 2.0 provisioning endpoints (#78, #91 — RFC 7644 + spec 27 §4.2).

The wire surface for the policy layer in :mod:`astrolift_identity.scim`:
that module parses payloads, parses + evaluates filters, normalizes
paging and decides what counts as a deprovision request; this module
authenticates the IdP, reads and writes the rows, and renders SCIM
responses.

Mounted at the project root as ``/api/scim/v2/`` (never under the
``/app/`` UI prefix) because that base URL is what an operator pastes
into Okta / Entra and it has to resolve without a session cookie —
same mounting convention as the CLI device flow and the cluster
heartbeat. The paths carry no trailing slash: RFC 7644 fixes them.

Auth: ``Authorization: Bearer alft_st_…``. The token is org-scoped
(minted by ``manage.py issue_scim_token``; only its SHA-256 is stored,
in ``Organization.scim_token_hash``), so the organization is derived
from the *credential* and never from the request. An IdP cannot
provision into a tenant it was not handed a token for. The ``alft_st_``
prefix is the one ``auth_schemes`` already classifies as SCIM.

Endpoints:

    GET    /api/scim/v2/Users            list, ``?filter=`` + paging
    POST   /api/scim/v2/Users            provision
    GET    /api/scim/v2/Users/<id>       read one
    PUT    /api/scim/v2/Users/<id>       replace (incl. the active flip)
    PATCH  /api/scim/v2/Users/<id>       active flip
    DELETE /api/scim/v2/Users/<id>       deprovision

The SCIM resource ``id`` is the ``Member`` guid: the org-scoped
membership row *is* the provisioning record, it survives
deprovisioning (soft state, never deleted) so the id an IdP stored
stays resolvable, and it keeps integer PKs off the wire.

Deliberately not implemented here:

* ``/Groups``. Group-driven role assignment needs the reverse
  direction to be safe — when the IdP drops a user from a group, the
  bindings that group granted have to come back off, and
  ``RoleBinding`` carries no provenance column saying which group
  granted it. Granting without revoking is the wrong half to ship, so
  ``GroupRoleMapping`` and ``scim.role_assignments_for_user`` stay
  unwired until that column exists.
* ``externalId``. Nothing in the schema keeps the IdP's own user id,
  so we neither echo it back nor filter on it (see
  ``_SUPPORTED_FILTER_ATTRIBUTES``); the ``id`` we mint on POST is the
  handle the IdP holds onto.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from astrolift_identity.models import (
    ApiToken,
    AstroliftSession,
    DeviceFlowSession,
    Member,
    Organization,
    RevocationReason,
)
from astrolift_identity.scim import (
    SCHEMA_ERROR,
    SCHEMA_LIST_RESPONSE,
    SCHEMA_USER,
    ScimError,
    ScimUser,
    filter_matches,
    is_deprovision_request,
    normalize_pagination,
    parse_filter,
    parse_user_payload,
)
from astrolift_identity.sessions import revoke_session

# The base an operator configures in the IdP. Kept in one place because
# it appears in ``meta.location`` on every resource we render and in
# the token-issuing command's output.
SCIM_BASE_PATH = "/api/scim/v2/"

# Same prefix ``auth_schemes._TOKEN_PREFIXES`` maps to AuthScheme.SCIM,
# so a token minted here classifies as SCIM rather than as an
# unrecognized bearer.
SCIM_TOKEN_PREFIX = "alft_st_"

SCIM_CONTENT_TYPE = "application/scim+json"

# Filter attributes the projection below can actually answer. ``email``
# / ``emails.value``, ``userName``, ``displayName`` and ``active`` map
# onto real columns; ``externalId`` does not (see the module docstring)
# and is refused rather than answered from an empty field.
_SUPPORTED_FILTER_ATTRIBUTES = frozenset(
    {
        "userName",
        "email",
        "emails.value",
        "displayName",
        "active",
    }
)


# ---- token material -------------------------------------------------


def hash_scim_token(raw: str) -> str:
    """SHA-256 of a presented token, for constant-shape lookup."""
    return hashlib.sha256(raw.encode()).hexdigest()


def mint_scim_token() -> tuple[str, str]:
    """Return ``(plaintext, digest)`` for a fresh org SCIM token.

    The plaintext is returned to the caller exactly once — only the
    digest is ever persisted, the same shape the cluster agent key and
    the API token use.
    """
    raw = SCIM_TOKEN_PREFIX + secrets.token_hex(32)
    return raw, hash_scim_token(raw)


# ---- request plumbing -----------------------------------------------


def _authenticated_org(request: HttpRequest) -> Organization | None:
    """Resolve the organization the presented SCIM token belongs to.

    Both halves matter. The digest proves the credential; ``scim_enabled``
    is the operator's switch, so flipping SCIM off stops provisioning
    without having to rotate the token. Orgs with an empty
    ``scim_token_hash`` never match — the lookup value is always 64 hex
    characters.
    """
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    raw = header[7:].strip()
    if not raw.startswith(SCIM_TOKEN_PREFIX):
        return None
    return Organization.objects.filter(
        scim_token_hash=hash_scim_token(raw),
        scim_enabled=True,
    ).first()


def _error(detail: str, *, status: int, scim_type: str = "") -> JsonResponse:
    """RFC 7644 §3.12 error shape."""
    body: dict[str, Any] = {
        "schemas": [SCHEMA_ERROR],
        "status": str(status),
        "detail": detail,
    }
    if scim_type:
        body["scimType"] = scim_type
    return JsonResponse(body, status=status, content_type=SCIM_CONTENT_TYPE)


def _ok(body: dict[str, Any], *, status: int = 200) -> JsonResponse:
    return JsonResponse(body, status=status, content_type=SCIM_CONTENT_TYPE)


def _json_body(request: HttpRequest) -> dict[str, Any] | None:
    """Parse the request body, or ``None`` when it isn't a JSON object."""
    raw = request.body or b""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _int_param(request: HttpRequest, name: str) -> int | None:
    """Query param as an int; ``None`` when absent. Raises ValueError."""
    raw = request.GET.get(name)
    if raw in (None, ""):
        return None
    return int(raw)


# ---- projection + rendering -----------------------------------------


def _org_members(org: Organization):
    # Ordered by the insertion sequence so a paged walk is stable
    # between requests; the ordering column is internal and never
    # rendered.
    return (
        Member.objects.select_related("user")
        .filter(scope_kind=Member.ScopeKind.ORG, scope_id=org.pk)
        .order_by("id")
    )


def _projection(member: Member, user) -> ScimUser:
    """``ScimUser`` view of one membership, for the filter evaluator.

    ``email`` falls back to the username because ``ScimUser`` requires
    an email and the User column is nullable on local-login installs;
    an empty value would raise out of the dataclass and take the whole
    list response down with it.
    """
    return ScimUser(
        user_name=user.username or user.email,
        email=user.email or user.username,
        display_name=(f"{user.first_name} {user.last_name}".strip() or user.username),
        active=bool(member.is_active and user.is_active),
    )


def _resource(member: Member, user) -> dict[str, Any]:
    projected = _projection(member, user)
    return {
        "schemas": [SCHEMA_USER],
        "id": str(member.guid),
        "userName": projected.user_name,
        "name": {"givenName": user.first_name, "familyName": user.last_name},
        "displayName": projected.display_name,
        "emails": [{"value": projected.email, "primary": True}],
        "active": projected.active,
        "meta": {
            "resourceType": "User",
            "created": member.created_at.isoformat() if member.created_at else "",
            "lastModified": member.updated_at.isoformat() if member.updated_at else "",
            "location": f"{SCIM_BASE_PATH}Users/{member.guid}",
        },
    }


def _name_parts(body: dict[str, Any], projected: ScimUser) -> tuple[str, str]:
    """Given / family name for the User row.

    ``parse_user_payload`` collapses the name object into one display
    string, so read the object directly when the IdP sent one and fall
    back to splitting the display name for IdPs that only send
    ``displayName``.
    """
    name = body.get("name")
    if isinstance(name, dict) and (name.get("givenName") or name.get("familyName")):
        return str(name.get("givenName") or ""), str(name.get("familyName") or "")
    given, _, family = projected.display_name.partition(" ")
    return given, family


# ---- lifecycle ------------------------------------------------------

SHARED_ACCOUNT_REFUSAL = "the account is shared beyond this organization, so this credential cannot change it"


def _account_is_org_local(user, org_id: int) -> bool:
    """Whether this org's credential may rewrite or revive the account (#1979).

    The user row is install-wide while the token speaks for one org, and
    POST attaches any existing account that matches by userName or email.
    So the account itself is the org's to change only when nothing else
    relies on it: it is not the platform operator's, and it has no
    membership, active or not, in another org.
    """

    if user.is_superuser:
        return False
    elsewhere = Member.objects.filter(user=user, scope_kind=Member.ScopeKind.ORG).exclude(scope_id=org_id)
    return not elsewhere.exists()


def _deprovision(member: Member, user) -> None:
    """Deactivate, never delete (spec 04 §11).

    Dropping the ORG membership removes the person's API access to this
    org: a bearer token or CLI refresh chain is honoured only while its
    owner is an active member of the token's org (#1910). The
    credentials issued under this membership are also revoked here, so
    re-provisioning the person later does not revive a token that was
    copied somewhere during their first tenure; they sign in again.
    Browser sessions are not confined the same way yet: the tenant
    middleware still honours an ``X-Astrolift-Organization`` header
    naming this org (#1925).

    When that was their last active org membership there is nothing
    left for them anywhere on the install, so the account itself goes
    inactive and live sessions are cut — without that, a browser
    session opened before the IdP removed them would keep working
    until it expired. A person who is still active in another
    organization keeps both their account and those sessions, and so
    does the platform operator: one org's IdP cannot lock out the
    install (#1979).
    """
    member.is_active = False
    member.lifecycle = Member.Lifecycle.DEACTIVATED
    member.save(update_fields=["is_active", "lifecycle", "updated_at", "version"])
    _revoke_org_credentials(user, member.scope_id)

    if (
        user.is_superuser
        or Member.objects.filter(
            user=user,
            scope_kind=Member.ScopeKind.ORG,
            is_active=True,
        ).exists()
    ):
        return

    if user.is_active:
        user.is_active = False
        user.save(update_fields=["is_active"])
    for row in AstroliftSession.objects.filter(user=user, revoked_at__isnull=True):
        revoke_session(row=row, actor_user_id=None, reason=RevocationReason.SCIM_DEPROVISION)


def _revoke_org_credentials(user, organization_id: int) -> None:
    """End every API credential ``user`` holds for one org, or is about
    to collect for it.

    Besides minted ``alft_at_`` tokens and CLI / mobile refresh chains,
    this covers a device login already approved but not yet polled and
    an enrollment QR not yet scanned: either would otherwise mint a
    fresh token for this org after the person left it.
    """
    now = timezone.now()
    # Sessions first, tokens last: a poll or refresh already in flight
    # holds its session row lock, so these updates wait for it to commit,
    # and the token revoke below then also catches the token it minted.
    sessions = DeviceFlowSession.objects.filter(approved_user=user, organization_id=organization_id)
    sessions.filter(
        state__in=[DeviceFlowSession.STATE_APPROVED, DeviceFlowSession.STATE_PRE_APPROVED]
    ).update(
        state=DeviceFlowSession.STATE_EXPIRED,
        enrollment_token_hash="",
        updated_at=now,
        version=F("version") + 1,
    )
    sessions.exclude(refresh_token_hash="").update(
        refresh_token_hash="",
        refresh_token_last_4="",
        updated_at=now,
        version=F("version") + 1,
    )
    ApiToken.objects.filter(user=user, organization_id=organization_id, is_revoked=False).update(
        is_revoked=True, updated_at=now, version=F("version") + 1
    )


def _reactivate(member: Member, user) -> bool:
    """Reactivate the membership, and the account if it is inactive.

    Returns False, changing nothing, when the account is inactive and not
    this org's to revive: another org or the operator switched it off.
    """
    if not user.is_active and not _account_is_org_local(user, member.scope_id):
        return False
    member.is_active = True
    member.lifecycle = Member.Lifecycle.ACTIVE
    if member.joined_at is None:
        member.joined_at = timezone.now()
    member.save(update_fields=["is_active", "lifecycle", "joined_at", "updated_at", "version"])
    if not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])
    return True


def _reactivation_requested(body: dict[str, Any]) -> bool:
    """Mirror of :func:`scim.is_deprovision_request` for ``active=true``.

    The policy module answers only the deactivation direction; an IdP
    re-enabling a person sends the same two shapes with ``true``.
    """
    if body.get("active") is True:
        return True
    for op in body.get("Operations", []) or []:
        if not isinstance(op, dict):
            continue
        if (
            str(op.get("op", "")).lower() == "replace"
            and op.get("path") == "active"
            and op.get("value") is True
        ):
            return True
    return False


# ---- /Users ---------------------------------------------------------


@csrf_exempt
@require_http_methods(["GET", "POST"])
def scim_users(request: HttpRequest) -> HttpResponse:
    """List (GET) or provision (POST) users in the token's organization."""
    org = _authenticated_org(request)
    if org is None:
        return _error("invalid or disabled SCIM credential", status=401)
    if request.method == "GET":
        return _list_users(request, org)
    return _provision_user(request, org)


def _list_users(request: HttpRequest, org: Organization) -> HttpResponse:
    try:
        parsed_filter = parse_filter(request.GET.get("filter", ""))
    except ScimError as exc:
        return _error(str(exc), status=400, scim_type="invalidFilter")

    if parsed_filter is not None and parsed_filter.attribute not in _SUPPORTED_FILTER_ATTRIBUTES:
        # ``filter_matches`` would answer an ``externalId`` filter by
        # comparing against the empty projection field and return an
        # empty page; an IdP reads that as "no such user" and
        # provisions a duplicate. Refuse instead of answering wrongly.
        return _error(
            f"filter attribute {parsed_filter.attribute!r} not supported",
            status=400,
            scim_type="invalidFilter",
        )

    try:
        offset, page_size = normalize_pagination(
            start_index=_int_param(request, "startIndex"),
            count=_int_param(request, "count"),
        )
    except ValueError:
        return _error("startIndex and count must be integers", status=400, scim_type="invalidValue")

    matched: list[tuple[Member, Any]] = []
    for member in _org_members(org):
        projected = _projection(member, member.user)
        if parsed_filter is not None and not filter_matches(filt=parsed_filter, user=projected):
            continue
        matched.append((member, member.user))

    window = matched[offset : offset + page_size]
    return _ok(
        {
            "schemas": [SCHEMA_LIST_RESPONSE],
            "totalResults": len(matched),
            "startIndex": offset + 1,
            "itemsPerPage": len(window),
            "Resources": [_resource(member, user) for member, user in window],
        }
    )


def _provision_user(request: HttpRequest, org: Organization) -> HttpResponse:
    body = _json_body(request)
    if body is None:
        return _error("body must be a JSON object", status=400, scim_type="invalidSyntax")
    try:
        incoming = parse_user_payload(body)
    except (ScimError, ValueError) as exc:
        return _error(str(exc), status=400, scim_type="invalidValue")

    user_model = get_user_model()
    # Match on the login identity first, then on email: an IdP that
    # provisions the same person into a second org must attach to the
    # existing account rather than mint a duplicate.
    user = (
        user_model.objects.filter(username=incoming.user_name).first()
        or user_model.objects.filter(email__iexact=incoming.email).first()
    )
    member = (
        Member.objects.filter(
            user=user,
            scope_kind=Member.ScopeKind.ORG,
            scope_id=org.pk,
        ).first()
        if user is not None
        else None
    )
    if member is not None and member.is_active:
        return _error(
            f"user {incoming.user_name!r} is already provisioned",
            status=409,
            scim_type="uniqueness",
        )
    if member is None and user is not None and user.is_superuser:
        # Attaching would hand the platform operator's account to this
        # org's IdP (#1979).
        return _error(
            "userName or email belongs to an account this organization cannot provision",
            status=409,
            scim_type="uniqueness",
        )

    given, family = _name_parts(body, incoming)
    with transaction.atomic():
        if user is None:
            user = user_model(
                username=incoming.user_name,
                email=incoming.email,
                first_name=given,
                last_name=family,
            )
            # SCIM-provisioned people authenticate through the IdP;
            # there is no local password to set and leaving the field
            # blank would leave a hash a caller could try to match.
            user.set_unusable_password()
            user.save()
        if member is None:
            member = Member.objects.create(
                user=user,
                scope_kind=Member.ScopeKind.ORG,
                scope_id=org.pk,
                is_active=True,
                lifecycle=Member.Lifecycle.ACTIVE,
                joined_at=timezone.now(),
            )
        else:
            # A previously deprovisioned person coming back. The IdP
            # POSTs rather than PATCHing an id it has forgotten, so
            # reuse the membership row — a second one would collide
            # with ``member_unique_active`` anyway.
            if not _reactivate(member, user):
                return _error(SHARED_ACCOUNT_REFUSAL, status=403)

    return _ok(_resource(member, user), status=201)


# ---- /Users/<id> ----------------------------------------------------


@csrf_exempt
@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
def scim_user_detail(request: HttpRequest, member_guid: str) -> HttpResponse:
    """Read, replace, patch or deprovision one provisioned user."""
    org = _authenticated_org(request)
    if org is None:
        return _error("invalid or disabled SCIM credential", status=401)

    try:
        member = (
            Member.objects.select_related("user")
            .filter(
                guid=member_guid,
                scope_kind=Member.ScopeKind.ORG,
                scope_id=org.pk,
            )
            .first()
        )
    except (ValidationError, ValueError):
        # A non-uuid id is a 404, not a 500: IdPs retry stale ids and
        # the guid column refuses to compare against arbitrary text.
        member = None
    if member is None:
        # Scoped to the token's org, so another tenant's id is
        # indistinguishable from one that never existed.
        return _error("user not found", status=404)

    user = member.user
    if request.method == "GET":
        return _ok(_resource(member, user))
    if request.method == "DELETE":
        _deprovision(member, user)
        return HttpResponse(status=204)

    body = _json_body(request)
    if body is None:
        return _error("body must be a JSON object", status=400, scim_type="invalidSyntax")

    if is_deprovision_request(payload=body):
        _deprovision(member, user)
        return _ok(_resource(member, user))

    if request.method == "PUT":
        try:
            incoming = parse_user_payload(body)
        except (ScimError, ValueError) as exc:
            return _error(str(exc), status=400, scim_type="invalidValue")
        # ``userName`` is deliberately not replaced: it is the login
        # identity every session and audit row already references, so a
        # rename arrives as a no-op on that field rather than stranding
        # them.
        given, family = _name_parts(body, incoming)
        rewrite = (incoming.email, given, family) != (user.email, user.first_name, user.last_name)
        revive = not member.is_active and not user.is_active
        # Refused before anything is written, so a refusal changes nothing.
        if (rewrite or revive) and not _account_is_org_local(user, org.pk):
            return _error(SHARED_ACCOUNT_REFUSAL, status=403)
        if rewrite:
            user.email = incoming.email
            user.first_name, user.last_name = given, family
            user.save(update_fields=["email", "first_name", "last_name"])
        if not member.is_active:
            _reactivate(member, user)
        return _ok(_resource(member, user))

    if _reactivation_requested(body):
        if not _reactivate(member, user):
            return _error(SHARED_ACCOUNT_REFUSAL, status=403)
        return _ok(_resource(member, user))

    return _error(
        "only the 'active' attribute can be patched; send a PUT to replace attributes",
        status=400,
        scim_type="invalidValue",
    )
