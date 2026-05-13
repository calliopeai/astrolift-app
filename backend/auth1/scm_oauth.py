"""
SCM OAuth dance — per-user GitHub / GitLab connection.

The operator registers a host's OAuth-app config row (e.g.
``github_oauth_app`` or ``gitlab_oauth_app``) carrying the
client_id + an encrypted client_secret + redirect_uri. A signed-in
user clicks 'Connect my GitHub' (or '... GitLab') on
/settings/source-providers, which routes to the matching
``/app/auth1/scm/<host>/start?config_id=<guid>`` endpoint.

We:

  1. Resolve the OAuth-app config row, verify it's active and
     belongs to the active org.
  2. Generate a random state token, store ``(state, config_id,
     return_to, kind)`` on the user's session.
  3. 302 to the host's authorize URL with client_id, redirect_uri,
     scope, and state.

The host bounces the user back to
``/app/auth1/scm/<host>/callback`` with ``code`` + ``state``. We:

  1. Pop the matching state from session.
  2. POST to the host's token-exchange endpoint with code + client
     creds (client_secret comes out of core.secrets).
  3. Create / update a per-user SourceConnection row keyed to the
     current user, the org, and parent_oauth_app=config row.
  4. 302 back to ``return_to`` (default /settings/source-providers).

Failures redirect with ``?scm_error=<code>`` so the UI can show a
toast — no plaintext error pages, no leaked tokens.

GitLab specifics:
  - Self-hosted instances supply ``api_base_url`` on the
    SourceConnection (e.g. ``https://gitlab.acme.example``). Empty
    means SaaS gitlab.com.
  - Authorize endpoint: ``<base>/oauth/authorize``.
  - Token endpoint: ``<base>/oauth/token``.
  - Userinfo: ``GET <base>/api/v4/user`` → ``{ username, id, ... }``;
    we store ``username`` as ``account_login``.
"""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from astrolift_scm.models import SourceConnection
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_EXCHANGE = "https://github.com/login/oauth/access_token"
GITHUB_API_DEFAULT = "https://api.github.com"
GITLAB_DEFAULT_BASE = "https://gitlab.com"
GITLAB_DEFAULT_SCOPE = "read_api read_repository read_user"
GITHUB_DEFAULT_SCOPE = "read:user repo"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _safe_return_to(raw: str | None) -> str:
    """Limit ``return_to`` to same-origin paths so an attacker can't
    bounce a victim through our OAuth dance to a third-party URL."""
    if not raw or not raw.startswith("/"):
        return "/settings/source-providers"
    return raw


def _redirect_with_error(return_to: str, code: str) -> HttpResponseRedirect:
    sep = "&" if "?" in return_to else "?"
    return HttpResponseRedirect(f"{return_to}{sep}scm_error={code}")


def _redirect_with_ok(return_to: str, account_login: str) -> HttpResponseRedirect:
    sep = "&" if "?" in return_to else "?"
    return HttpResponseRedirect(f"{return_to}{sep}scm_connected={urllib.parse.quote(account_login)}")


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
        Member.objects.filter(user=request.user, scope_kind="ORG", is_active=True)
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


def _store_pending_state(
    request: HttpRequest,
    *,
    state: str,
    config_id: str,
    return_to: str,
    kind: str,
) -> None:
    request.session["scm_oauth_state"] = {
        "state": state,
        "config_id": config_id,
        "return_to": return_to,
        "kind": kind,
    }
    request.session.save()


def _pop_pending_state(request: HttpRequest, expected_kind: str) -> dict | None:
    pending = request.session.pop("scm_oauth_state", None)
    if not pending or pending.get("kind") != expected_kind:
        return None
    return pending


def _gitlab_base(config: SourceConnection) -> str:
    """Trim any trailing slash so concatenation is predictable."""
    return (config.api_base_url or GITLAB_DEFAULT_BASE).rstrip("/")


def _resolve_user_token_via_post(
    url: str,
    payload: dict[str, str],
) -> tuple[str | None, str | None]:
    """POST ``payload`` form-encoded, accept JSON; return (token, err_code)."""
    body = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "User-Agent": "astrolift",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError):
        return None, "exchange_failed"
    token = data.get("access_token")
    if not token:
        return None, "exchange_no_token"
    return token, None


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


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
    _store_pending_state(
        request,
        state=state,
        config_id=str(config.guid),
        return_to=return_to,
        kind="github",
    )

    redirect_uri = config.oauth_redirect_uri or request.build_absolute_uri(reverse("scm_github_callback"))
    params = urllib.parse.urlencode(
        {
            "client_id": config.oauth_client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            # 'repo' covers private+public repos for the user; tighten
            # per-install via the github_oauth_app's repo_visibility
            # policy on the resolver side.
            "scope": GITHUB_DEFAULT_SCOPE,
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
    pending = _pop_pending_state(request, "github")
    if pending is None:
        return _redirect_with_error("/settings/source-providers", "no_pending_state")
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
    access_token, err = _resolve_user_token_via_post(
        GITHUB_TOKEN_EXCHANGE,
        {
            "client_id": config.oauth_client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": config.oauth_redirect_uri or "",
        },
    )
    if err is not None or access_token is None:
        return _redirect_with_error(return_to, err or "exchange_failed")

    account_login = _resolve_github_login(access_token)
    _upsert_user_connection(
        request,
        config,
        access_token,
        account_login,
        kind="github_oauth_user",
        display_template="GitHub: {}",
        anonymous_label="GitHub (personal)",
    )
    return _redirect_with_ok(return_to, account_login or "github")


def _resolve_github_login(token: str) -> str:
    req = urllib.request.Request(
        f"{GITHUB_API_DEFAULT}/user",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8")).get("login") or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


@login_required
@require_GET
def gitlab_start(request: HttpRequest) -> Any:
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
        kind="gitlab_oauth_app",
        is_active=True,
        deleted_at__isnull=True,
    ).first()
    if config is None:
        return _redirect_with_error(return_to, "config_not_found")
    if not config.oauth_client_id:
        return _redirect_with_error(return_to, "config_incomplete")

    state = secrets.token_urlsafe(32)
    _store_pending_state(
        request,
        state=state,
        config_id=str(config.guid),
        return_to=return_to,
        kind="gitlab",
    )

    redirect_uri = config.oauth_redirect_uri or request.build_absolute_uri(reverse("scm_gitlab_callback"))
    base = _gitlab_base(config)
    params = urllib.parse.urlencode(
        {
            "client_id": config.oauth_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": GITLAB_DEFAULT_SCOPE,
        }
    )
    return HttpResponseRedirect(f"{base}/oauth/authorize?{params}")


@login_required
@csrf_exempt  # GitLab redirect doesn't carry our CSRF token; state cookie is the auth.
@require_GET
def gitlab_callback(request: HttpRequest) -> Any:
    state_in = request.GET.get("state", "")
    code = request.GET.get("code", "")
    pending = _pop_pending_state(request, "gitlab")
    if pending is None:
        return _redirect_with_error("/settings/source-providers", "no_pending_state")
    return_to = _safe_return_to(pending.get("return_to"))

    if not state_in or not secrets.compare_digest(state_in, pending["state"]):
        return _redirect_with_error(return_to, "state_mismatch")
    if not code:
        return _redirect_with_error(return_to, "no_code")

    config = SourceConnection.objects.filter(
        guid=pending["config_id"],
        kind="gitlab_oauth_app",
        is_active=True,
        deleted_at__isnull=True,
    ).first()
    if config is None:
        return _redirect_with_error(return_to, "config_gone")

    org_id = _active_org_id(request)
    if org_id is None or org_id != config.organization_id:
        return _redirect_with_error(return_to, "org_mismatch")

    client_secret = _decrypt_client_secret(config)
    base = _gitlab_base(config)
    redirect_uri = config.oauth_redirect_uri or request.build_absolute_uri(reverse("scm_gitlab_callback"))
    access_token, err = _resolve_user_token_via_post(
        f"{base}/oauth/token",
        {
            "client_id": config.oauth_client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )
    if err is not None or access_token is None:
        return _redirect_with_error(return_to, err or "exchange_failed")

    account_login = _resolve_gitlab_login(access_token, base)
    _upsert_user_connection(
        request,
        config,
        access_token,
        account_login,
        kind="gitlab_oauth_user",
        display_template="GitLab: {}",
        anonymous_label="GitLab (personal)",
        api_base_url=config.api_base_url,
    )
    return _redirect_with_ok(return_to, account_login or "gitlab")


def _resolve_gitlab_login(token: str, api_base_url: str) -> str:
    base = (api_base_url or GITLAB_DEFAULT_BASE).rstrip("/")
    req = urllib.request.Request(
        f"{base}/api/v4/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8")).get("username") or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Token persistence
# ---------------------------------------------------------------------------


def _upsert_user_connection(
    request: HttpRequest,
    config: SourceConnection,
    access_token: str,
    account_login: str,
    *,
    kind: str,
    display_template: str,
    anonymous_label: str,
    api_base_url: str = "",
) -> None:
    """Encrypt + persist the per-user token row.

    Carries ``api_base_url`` forward from the OAuth-app config so a
    self-hosted GitLab instance keeps the same base URL on the
    per-user row (the repo-listing dispatcher reads it from there)."""
    encrypted = encrypt_at_rest(access_token.encode("utf-8"))
    display = display_template.format(account_login) if account_login else anonymous_label
    SourceConnection.objects.update_or_create(
        organization_id=config.organization_id,
        user=request.user,
        kind=kind,
        parent_oauth_app=config,
        defaults={
            "display_name": display,
            "account_login": account_login,
            "secret_backend_kind": encrypted.backend_kind,
            "secret_ciphertext": encrypted.backend_ref,
            "is_active": True,
            "api_base_url": api_base_url or "",
        },
    )
