"""
SCM OAuth dance — per-user GitHub (and friends) connection.

The operator registers a ``github_oauth_app`` SourceConnection
(client_id + encrypted client_secret + redirect_uri). A signed-in
user clicks 'Connect my GitHub' on /settings/source-providers,
which routes to ``/app/auth1/scm/github/start?config_id=<guid>``.
We:

  1. Resolve the github_oauth_app row, verify it's active and
     belongs to the active org.
  2. Generate a random state token, store ``(state, config_id,
     return_to)`` on the user's session.
  3. 302 to GitHub's authorize URL with client_id, redirect_uri,
     scope, and state.

GitHub bounces the user back to ``/app/auth1/scm/github/callback``
with ``code`` + ``state``. We:

  1. Pop the matching state from session.
  2. POST to GitHub's token-exchange endpoint with code + client
     creds (client_secret comes out of core.secrets).
  3. Create a ``github_oauth_user`` SourceConnection row keyed to
     the current user, the org, and parent_oauth_app=config row.
  4. 302 back to ``return_to`` (default /settings/source-providers).

Failures redirect with ``?scm_error=<code>`` so the UI can show a
toast — no plaintext error pages, no leaked tokens.
"""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from astrolift_identity.models import Organization
from astrolift_scm.models import SourceConnection
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest


GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_EXCHANGE = "https://github.com/login/oauth/access_token"


def _safe_return_to(raw: str | None) -> str:
    if not raw or not raw.startswith("/"):
        return "/settings/source-providers"
    return raw


def _redirect_with_error(return_to: str, code: str) -> HttpResponseRedirect:
    sep = "&" if "?" in return_to else "?"
    return HttpResponseRedirect(f"{return_to}{sep}scm_error={code}")


def _redirect_with_ok(return_to: str, account_login: str) -> HttpResponseRedirect:
    sep = "&" if "?" in return_to else "?"
    return HttpResponseRedirect(
        f"{return_to}{sep}scm_connected={urllib.parse.quote(account_login)}"
    )


def _active_org_id(request: HttpRequest) -> int | None:
    """Resolve the active org for the request — same path as
    TenantContextMiddleware uses, so the OAuth row gets attached to
    the same org the rest of the GraphQL surface scopes to."""
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    if tenant and tenant.organization_id is not None:
        return tenant.organization_id

    # Fallback: single-tenant install — first active membership.
    from astrolift_identity.models import Member

    member = (
        Member.objects.filter(
            user=request.user, scope_kind="ORG", is_active=True
        )
        .order_by("scope_id")
        .first()
    )
    return member.scope_id if member else None


def _decrypt_client_secret(config: SourceConnection) -> str:
    plaintext = decrypt(
        EncryptedSecret(
            backend_kind=config.secret_backend_kind,
            backend_ref=bytes(config.secret_ciphertext),
        )
    )
    return plaintext.decode("utf-8")


@login_required
@require_GET
def github_start(request: HttpRequest) -> Any:
    config_id = request.GET.get("config_id", "")
    return_to = _safe_return_to(request.GET.get("return_to"))

    if not config_id:
        return _redirect_with_error(return_to, "missing_config_id")

    org_id = _active_org_id(request)
    if org_id is None:
        return _redirect_with_error(return_to, "no_org")

    config = SourceConnection.objects.filter(
        organization_id=org_id,
        guid=config_id,
        kind="github_oauth_app",
        is_active=True,
        deleted_at__isnull=True,
    ).first()
    if config is None:
        return _redirect_with_error(return_to, "config_not_found")
    if not config.oauth_client_id:
        return _redirect_with_error(return_to, "config_incomplete")

    state = secrets.token_urlsafe(32)
    request.session["scm_oauth_state"] = {
        "state": state,
        "config_id": str(config.guid),
        "return_to": return_to,
        "kind": "github",
    }
    request.session.save()

    redirect_uri = config.oauth_redirect_uri or request.build_absolute_uri(
        reverse("scm_github_callback")
    )
    params = urllib.parse.urlencode(
        {
            "client_id": config.oauth_client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            # 'repo' covers private+public repos for the user; tighten
            # per-install via the github_oauth_app's repo_visibility
            # policy on the resolver side.
            "scope": "read:user repo",
            "allow_signup": "false",
        }
    )
    return HttpResponseRedirect(f"{GITHUB_AUTHORIZE}?{params}")


@login_required
@csrf_exempt  # GitHub redirect doesn't carry our CSRF token; state cookie is the auth.
@require_GET
def github_callback(request: HttpRequest) -> Any:
    state_in = request.GET.get("state", "")
    code = request.GET.get("code", "")
    pending = request.session.pop("scm_oauth_state", None)

    if not pending or pending.get("kind") != "github":
        return _redirect_with_error(
            "/settings/source-providers", "no_pending_state"
        )
    return_to = _safe_return_to(pending.get("return_to"))

    if not state_in or not secrets.compare_digest(state_in, pending["state"]):
        return _redirect_with_error(return_to, "state_mismatch")
    if not code:
        return _redirect_with_error(return_to, "no_code")

    config = SourceConnection.objects.filter(
        guid=pending["config_id"],
        kind="github_oauth_app",
        is_active=True,
        deleted_at__isnull=True,
    ).first()
    if config is None:
        return _redirect_with_error(return_to, "config_gone")

    org_id = _active_org_id(request)
    if org_id is None or org_id != config.organization_id:
        return _redirect_with_error(return_to, "org_mismatch")

    client_secret = _decrypt_client_secret(config)

    body = urllib.parse.urlencode(
        {
            "client_id": config.oauth_client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": config.oauth_redirect_uri or "",
        }
    ).encode()
    req = urllib.request.Request(
        GITHUB_TOKEN_EXCHANGE,
        data=body,
        headers={
            "Accept": "application/json",
            "User-Agent": "astrolift",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError):
        return _redirect_with_error(return_to, "exchange_failed")

    access_token = payload.get("access_token")
    if not access_token:
        return _redirect_with_error(return_to, "exchange_no_token")

    # Resolve the GitHub login of the connecting user so the row is
    # self-describing in the UI.
    account_login = _resolve_github_login(access_token)

    encrypted = encrypt_at_rest(access_token.encode("utf-8"))

    SourceConnection.objects.update_or_create(
        organization_id=config.organization_id,
        user=request.user,
        kind="github_oauth_user",
        parent_oauth_app=config,
        defaults={
            "display_name": (
                f"GitHub: {account_login}"
                if account_login
                else "GitHub (personal)"
            ),
            "account_login": account_login,
            "secret_backend_kind": encrypted.backend_kind,
            "secret_ciphertext": encrypted.backend_ref,
            "is_active": True,
        },
    )

    return _redirect_with_ok(return_to, account_login or "github")


def _resolve_github_login(token: str) -> str:
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return (json.loads(resp.read().decode("utf-8")).get("login") or "")
    except Exception:
        return ""
