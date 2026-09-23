"""HTTP views for the CLI / mobile device-flow surface (#475).

REST (not GraphQL) — the CLI ships a Go HTTP client and the wire
contract is documented in ``astrolift-cli/internal/auth/auth.go``.

Three public endpoints (no auth required to *call*; auth is
established by the flow itself):

* ``POST /api/cli/v1/auth/start`` — mint a device-flow session
* ``POST /api/cli/v1/auth/complete`` — poll for an issued token pair
* ``POST /api/cli/v1/auth/refresh`` — rotate the refresh token

And one auth1-protected browser surface:

* ``GET  /app/cli/auth/device/<session_guid>/`` — approval page
* ``POST /app/cli/auth/device/<session_guid>/`` — approve / deny

The browser page deliberately uses Django template rendering instead
of dispatching to the Next.js frontend: keeps the approval surface
inside the auth1 session-cookie boundary (so a freshly-authenticated
user is the one approving), and means a broken / unreachable frontend
build doesn't break ``astro auth login``.
"""

from __future__ import annotations

import json
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from astrolift_identity import device_flow
from astrolift_identity.api_tokens import (
    client_ip_from_request,
    user_agent_from_request,
)
from astrolift_identity.models import DeviceFlowSession, Member, Organization

# ---- shared helpers --------------------------------------------------


def _json_body(request: HttpRequest) -> dict[str, Any]:
    """Parse JSON body, returning ``{}`` on empty or malformed input.

    The CLI sends ``{}`` for /start. /complete + /refresh send a
    single string key. Either way we want a defensive parse that
    can't crash the handler — invalid input is surfaced as a 400 by
    the field-level checks downstream.
    """
    raw = request.body or b""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _error(message: str, *, status: int, code: str = "") -> JsonResponse:
    """Uniform JSON error envelope.

    ``code`` mirrors the OAuth device-flow vocabulary (``slow_down``,
    ``authorization_pending``, ``expired_token``, ``invalid_grant``)
    so callers can branch on it without parsing the message.
    """
    body: dict[str, Any] = {"error": message}
    if code:
        body["code"] = code
    return JsonResponse(body, status=status)


def _orgs_for_user(user) -> list[Organization]:
    """Return the user's ORG-scope memberships' Organizations.

    ``Member`` is a polymorphic scope row (``scope_kind`` +
    ``scope_id``) so we resolve org ids in one round-trip and fetch
    matching Organizations in a second.
    """
    org_ids = list(
        Member.objects.filter(
            user=user,
            scope_kind=Member.ScopeKind.ORG,
            deleted_at__isnull=True,
            is_active=True,
        ).values_list("scope_id", flat=True)
    )
    if not org_ids:
        return []
    return list(Organization.objects.filter(pk__in=org_ids, deleted_at__isnull=True).order_by("name"))


def _resolve_default_org_for_user(user) -> Organization | None:
    """Resolve the org we'll bind the issued token to.

    Picks the user's *single* org membership when there's exactly
    one; returns ``None`` when the user is in zero or multiple orgs.
    The approval page surfaces an org picker in the multi-org case.
    """
    orgs = _orgs_for_user(user)
    if len(orgs) == 1:
        return orgs[0]
    return None


# ---- POST /api/cli/v1/auth/start -------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def device_flow_start(request: HttpRequest) -> JsonResponse:
    """Mint a fresh ``DeviceFlowSession`` — or redeem a QR enrollment.

    Body (all optional)::

        {
          "client_label": "astro CLI on lmata-mbp",
          "client_kind": "cli",  // or "mobile" / "ide" / "browser"
          "enrollment_token": "alft_enroll_..."  // #494 mobile QR
        }

    When ``enrollment_token`` is present and valid, the response is
    the credential payload — no browser approval step. Otherwise the
    behaviour matches the original CLI device-flow contract.

    Standard response::

        {
          "session_id": "...",
          "login_url": "https://app.example/app/cli/auth/device/<id>/",
          "poll_interval_seconds": 2,
          "expires_in_seconds": 600
        }

    Enrollment-redeem response (200)::

        {
          "access_token": "alft_at_...",
          "refresh_token": "alft_rt_...",
          "expires_at": "...",
          "token_type": "Bearer"
        }
    """
    body = _json_body(request)
    label = body.get("client_label") if isinstance(body.get("client_label"), str) else ""
    kind = body.get("client_kind") if isinstance(body.get("client_kind"), str) else ""
    enrollment_token = body.get("enrollment_token")

    # ---- #494: enrollment_token path takes precedence ----------------
    if isinstance(enrollment_token, str) and enrollment_token:
        result = device_flow.consume_enrollment(
            enrollment_token,
            client_label=label or "",
            client_kind=kind or "mobile",
            user_agent=user_agent_from_request(request),
            client_ip=client_ip_from_request(request),
        )
        if result.status == "issued":
            creds = result.credentials
            assert creds is not None
            # Audit: enrollment consumption. Actor is the operator who
            # originally minted the QR (recorded on the row); the
            # mobile client itself is unauthenticated at this point.
            session = result.session
            from core.mutations import AuditEntry, emit_audit

            emit_audit(
                AuditEntry(
                    actor_user_id=getattr(session, "approved_user_id", None) if session else None,
                    organization_id=getattr(session, "organization_id", None) if session else None,
                    action="auth.enrollment.consumed",
                    decision="ALLOW",
                    target_kind="device_flow_session",
                    target_id=str(getattr(session, "guid", "")) if session else None,
                    duration_ms=0,
                    permissions=(),
                    extra={
                        "client_label": label or "",
                        "client_kind": kind or "mobile",
                        "client_ip": client_ip_from_request(request),
                    },
                )
            )
            return JsonResponse(
                {
                    "access_token": creds.access_token,
                    "refresh_token": creds.refresh_token,
                    "expires_at": creds.access_token_expires_at.isoformat(),
                    "token_type": "Bearer",
                },
                status=200,
            )
        if result.status == "expired":
            return _error("enrollment token expired", status=410, code="expired_token")
        # ``unknown`` (bad prefix, missing row, already burned)
        return _error("invalid enrollment token", status=401, code="invalid_grant")

    row, session_id = device_flow.create_session(
        client_label=label or "",
        client_kind=kind or "cli",
        user_agent=user_agent_from_request(request),
        client_ip=client_ip_from_request(request),
    )
    login_url = device_flow.build_login_url(session_id=session_id, request=request)
    expires_in = int((row.expires_at - timezone.now()).total_seconds())
    return JsonResponse(
        {
            "session_id": session_id,
            "login_url": login_url,
            "poll_interval_seconds": device_flow.POLL_INTERVAL_SECONDS,
            "expires_in_seconds": max(expires_in, 0),
        },
        status=200,
    )


# ---- POST /api/cli/v1/auth/complete ----------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def device_flow_complete(request: HttpRequest) -> JsonResponse | HttpResponse:
    """Poll for a state transition on a pending session.

    * 202 (empty body) while the user hasn't approved yet
    * 200 with credentials when issued — single-use; a second call
      returns 410
    * 410 on expired / denied / consumed-already
    * 404 on unknown session_id
    * 429 on per-session over-polling (``slow_down``)
    """
    body = _json_body(request)
    session_id = body.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return _error("session_id is required", status=400, code="invalid_request")

    result = device_flow.poll_complete(session_id)
    if result.status == "pending":
        # CLI's ``PollLogin`` switches on the bare status code; empty
        # body is the cheapest signal and matches the wire contract.
        return HttpResponse(status=202)
    if result.status == "issued":
        creds = result.credentials
        assert creds is not None  # poll_complete invariant
        return JsonResponse(
            {
                "access_token": creds.access_token,
                "refresh_token": creds.refresh_token,
                "expires_at": creds.access_token_expires_at.isoformat(),
                "token_type": "Bearer",
            },
            status=200,
        )
    if result.status == "denied":
        return _error("authorization denied", status=410, code="authorization_denied")
    if result.status == "expired":
        return _error("session expired or already consumed", status=410, code="expired_token")
    if result.status == "slow_down":
        # 429 per OAuth-style slow_down; CLI's poll loop already
        # paces by ``poll_interval_seconds``, so this only fires on
        # a misbehaving client.
        return _error(
            "polling too fast; honor poll_interval_seconds",
            status=429,
            code="slow_down",
        )
    # ``unknown`` and anything else
    return _error("unknown session_id", status=404, code="invalid_request")


# ---- POST /api/cli/v1/auth/refresh -----------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def device_flow_refresh(request: HttpRequest) -> JsonResponse:
    """Rotate the refresh token + reissue an access bearer.

    * 200 + new pair on success
    * 401 on unknown / wrong-prefix refresh token (also the replay
      case after rotation — the prior hash is gone from the row)
    * 410 on TTL-expired refresh chain, or once the approving user is
      no longer an active member of the chain's organization
    """
    body = _json_body(request)
    refresh_token = body.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        return _error("refresh_token is required", status=400, code="invalid_request")

    result = device_flow.refresh_credentials(refresh_token)
    if result.status == "issued":
        creds = result.credentials
        assert creds is not None
        return JsonResponse(
            {
                "access_token": creds.access_token,
                "refresh_token": creds.refresh_token,
                "expires_at": creds.access_token_expires_at.isoformat(),
                "token_type": "Bearer",
            },
            status=200,
        )
    if result.status == "expired":
        return _error("refresh chain expired", status=410, code="expired_token")
    return _error("invalid refresh token", status=401, code="invalid_grant")


# ---- GET / POST /app/cli/auth/device/<id>/ ---------------------------


@login_required
@require_http_methods(["GET", "POST"])
def device_flow_approval(request: HttpRequest, session_guid: str) -> HttpResponse:
    """Browser approval surface.

    GET → render the approval page. POST → approve or deny based on
    the ``action`` field.

    Auth1 session cookie required (``@login_required``). The
    user-who-submits-POST becomes ``approved_user`` on the row — we
    never trust a session_guid stuffed into a URL on a freshly-
    opened tab to authorize an arbitrary user.
    """
    row = DeviceFlowSession.all_objects.filter(session_guid=session_guid).first()
    if row is None:
        # Render a 404 page with a clear message rather than a bare
        # 404 — operators land here when they paste a stale or
        # mis-copied URL and need to know to re-run ``astro auth
        # login``.
        return render(
            request,
            "astrolift_identity/device_flow_unknown.html",
            {},
            status=404,
        )

    # Always read the latest state from the DB on entry to the
    # approval page; a /complete poll may have already flipped the
    # row to expired or denied since the user opened the tab.
    device_flow.mark_expired_if_needed(row)

    multi_org_options: list[Organization] = []
    if row.state == DeviceFlowSession.STATE_PENDING:
        # Build the org picker for the multi-org case. We avoid
        # leaking the picker UI when the user only has one org —
        # the issuance happens transparently against that org.
        user_orgs = _orgs_for_user(request.user)
        if len(user_orgs) > 1:
            multi_org_options = user_orgs

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()
        if action == "deny":
            err = device_flow.deny_session(row)
            if err == "expired":
                return _render_terminal(request, row, status="expired")
            if err == "already_terminal":
                return _render_terminal(request, row, status=row.state)
            return _render_terminal(request, row, status="denied")

        if action == "approve":
            org_id = request.POST.get("organization_id")
            chosen_org: Organization | None = None
            if org_id:
                try:
                    chosen_org_id = int(org_id)
                except (TypeError, ValueError):
                    chosen_org_id = None
                if chosen_org_id is not None:
                    # Tenancy guard: only let the user pick an org
                    # they're a live member of. Drops silently
                    # otherwise so the picker UI handles the empty
                    # case uniformly.
                    user_org_ids = {o.id for o in _orgs_for_user(request.user)}
                    if chosen_org_id in user_org_ids:
                        chosen_org = Organization.objects.filter(pk=chosen_org_id).first()
            if chosen_org is None:
                chosen_org = _resolve_default_org_for_user(request.user)
            if chosen_org is None and multi_org_options:
                # User has multiple orgs and didn't pick one — re-
                # render the picker with an error message rather
                # than silently picking for them.
                return render(
                    request,
                    "astrolift_identity/device_flow_approve.html",
                    {
                        "session": row,
                        "multi_org_options": multi_org_options,
                        "requested_scopes": device_flow.token_scopes_for_client_kind(row.client_kind),
                        "error": "Choose the organization to authorize.",
                    },
                    status=400,
                )
            err = device_flow.approve_session(
                row,
                user=request.user,
                organization=chosen_org,
            )
            if err == "expired":
                return _render_terminal(request, row, status="expired")
            if err == "already_terminal":
                return _render_terminal(request, row, status=row.state)
            return _render_terminal(request, row, status="approved")

        # Unknown action falls through to a re-render
        return render(
            request,
            "astrolift_identity/device_flow_approve.html",
            {
                "session": row,
                "multi_org_options": multi_org_options,
                "requested_scopes": device_flow.token_scopes_for_client_kind(row.client_kind),
                "error": "Unknown action.",
            },
            status=400,
        )

    # GET
    if row.state != DeviceFlowSession.STATE_PENDING:
        return _render_terminal(request, row, status=row.state)
    return render(
        request,
        "astrolift_identity/device_flow_approve.html",
        {
            "session": row,
            "multi_org_options": multi_org_options,
            "requested_scopes": device_flow.token_scopes_for_client_kind(row.client_kind),
        },
    )


def _render_terminal(request: HttpRequest, row, *, status: str) -> HttpResponse:
    """Render the post-action page reporting the final state.

    Same template for approved / denied / expired so the user lands
    somewhere coherent and the CLI keeps polling /complete on its
    own schedule.
    """
    return render(
        request,
        "astrolift_identity/device_flow_terminal.html",
        {
            "session": row,
            "status": status,
        },
    )
