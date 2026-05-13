"""
GitHub App **manifest flow** — one-click operator-side App registration.

GitHub supports creating a GitHub App via a POST-form manifest:
the operator clicks "Connect to GitHub" on Astrolift, we render an
auto-submitting HTML form that posts a manifest JSON to
``https://github.com/{org-or-user}/settings/apps/new?state=<state>``,
GitHub creates the App on github.com, redirects the operator back to
us with a temporary ``code`` query param, and we exchange that ``code``
at ``POST https://api.github.com/app-manifests/<code>/conversions``
for the App's ``client_id``, ``client_secret``, ``webhook_secret``,
private-key ``pem``, and ``html_url``. We persist those as a new
``github_app_install`` ``SourceConnection`` row (the PEM goes in
``secret_ciphertext``, the App ID in ``oauth_client_id`` — same column
re-use already used by the manual-paste path).

The webhook URL inside the manifest references a stable guid we
pre-allocate (the new SourceConnection's ``guid``); the App is created
with its webhook pointing at
``<APP_BASE_URL>/app/auth1/scm/github/webhook/<connection_guid>/``
from minute one, so the existing webhook ingest layer needs no changes.

After persistence we redirect the operator to
``https://github.com/apps/<slug>/installations/new?state=<state>`` so
they can install the App on whichever org they actually want it on.
GitHub bounces back to our ``setup_url`` with
``?installation_id=<id>&setup_action=install`` — we attach the
installation id to the connection row at that point and the connection
becomes usable for repo listing + commit-status writes.

Refs:
  https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest

Security:
  - State CSRF token: 32-byte URL-safe random, stashed on the
    Django session before the redirect, popped on the callback.
  - ``return_to`` is clamped to same-origin paths so the dance can't
    bounce the browser into a third-party host.
  - Org slug is validated against the GitHub naming rules
    (alphanumeric + hyphen, max 39 chars) before being interpolated
    into the GitHub URL.
  - We never log the App private key or client secret.
"""

from __future__ import annotations

import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from astrolift_identity.models import Member
from astrolift_scm.models import SourceConnection
from core.secrets import encrypt_at_rest

GITHUB_API_DEFAULT = "https://api.github.com"

# GitHub org/user slug rule: alphanumeric + hyphen, no consecutive
# hyphens, max 39 chars. We only enforce the character class + length
# here — anything subtler is the operator's problem at GitHub.
_GH_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_return_to(raw: str | None) -> str:
    if not raw or not raw.startswith("/"):
        return "/settings/source-providers"
    return raw


def _redirect_with_error(return_to: str, code: str) -> HttpResponseRedirect:
    sep = "&" if "?" in return_to else "?"
    return HttpResponseRedirect(f"{return_to}{sep}scm_error={code}")


def _redirect_with_ok(return_to: str, label: str) -> HttpResponseRedirect:
    sep = "&" if "?" in return_to else "?"
    return HttpResponseRedirect(f"{return_to}{sep}scm_connected={urllib.parse.quote(label)}")


def _active_org_id(request: HttpRequest) -> int | None:
    """Resolve the active org the same way the OAuth dance does, so the
    pre-allocated connection ends up on the same org the rest of the
    GraphQL surface scopes to."""
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    if tenant and tenant.organization_id is not None:
        return tenant.organization_id

    member = (
        Member.objects.filter(user=request.user, scope_kind="ORG", is_active=True)
        .order_by("scope_id")
        .first()
    )
    return member.scope_id if member else None


def _app_base_url(request: HttpRequest) -> str:
    """Public URL of this Astrolift install — the operator's browser AND
    github.com need to be able to reach it. Prefer ``APP_BASE_URL`` from
    settings (set by the deploy), fall back to the request's own absolute
    URI base for local dev."""
    base = getattr(settings, "APP_BASE_URL", "") or ""
    if base:
        return base.rstrip("/")
    return request.build_absolute_uri("/").rstrip("/")


def _manifest_json(
    request: HttpRequest,
    *,
    install_slug: str,
    connection_guid: str,
) -> dict[str, Any]:
    """Build the GitHub App manifest payload.

    ``install_slug`` is a short, human-meaningful suffix appended to
    ``astrolift-`` so an operator with multiple Astrolift installs can
    tell them apart in their GitHub-Apps list. GitHub will append a
    further suffix if the name already exists in the namespace.

    Permissions stay minimal: read repo contents/metadata, write PR
    comments + check runs. We don't ask for workflow scope or admin
    scope. Operator can broaden later by editing the App on GitHub.
    """
    base = _app_base_url(request)
    callback_url = f"{base}/app/auth1/scm/github/app-manifest/callback"
    setup_url = f"{base}/app/auth1/scm/github/app-manifest/setup"
    webhook_url = f"{base}/app/auth1/scm/github/webhook/{connection_guid}/"

    return {
        "name": f"astrolift-{install_slug}",
        "url": base,
        "hook_attributes": {
            "url": webhook_url,
            "active": True,
        },
        "redirect_url": callback_url,
        "setup_url": setup_url,
        "setup_on_update": True,
        "public": False,
        "default_permissions": {
            "contents": "read",
            "metadata": "read",
            "pull_requests": "write",
            "checks": "write",
        },
        "default_events": ["push", "pull_request"],
    }


def _install_slug_default(base: str) -> str:
    """Derive a short slug from the install's public hostname so the
    GitHub App name is recognizable. Falls back to a short random
    suffix when we can't extract a meaningful hostname."""
    try:
        host = urllib.parse.urlsplit(base).hostname or ""
    except ValueError:
        host = ""
    if host:
        slug = host.split(".")[0]
        slug = re.sub(r"[^a-z0-9-]", "-", slug.lower()).strip("-")
        if slug:
            return slug[:24]
    return secrets.token_hex(4)


def _validate_gh_slug(slug: str) -> bool:
    if not slug:
        return False
    return bool(_GH_SLUG_RE.match(slug))


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@login_required
@require_GET
def github_app_manifest_start(request: HttpRequest) -> Any:
    """Render an HTML form that auto-posts the App manifest to GitHub.

    GitHub requires the manifest to arrive as a form POST (not a
    cross-origin fetch), so we render a tiny self-submitting HTML page
    here. The form's action is either
    ``https://github.com/organizations/<org>/settings/apps/new?state=…``
    (org install) or ``https://github.com/settings/apps/new?state=…``
    (personal account install).
    """
    return_to = _safe_return_to(request.GET.get("return_to"))
    gh_org = (request.GET.get("org") or "").strip()
    if gh_org and not _validate_gh_slug(gh_org):
        return _redirect_with_error(return_to, "invalid_org_slug")

    org_id = _active_org_id(request)
    if org_id is None:
        return _redirect_with_error(return_to, "no_org")

    # Pre-allocate the SourceConnection row so the webhook URL embedded
    # in the manifest references its guid. The row is is_active=False
    # until the callback fills in client_id/client_secret/PEM. The
    # unique constraint is (organization, kind, account_login) WHERE
    # deleted_at IS NULL — account_login is blank on the pending row,
    # so we soft-delete any prior abandoned pending row first to keep
    # the constraint satisfied if the operator re-starts the flow.
    SourceConnection.objects.filter(
        organization_id=org_id,
        kind="github_app_install",
        account_login="",
        is_active=False,
    ).update(deleted_at=timezone.now())
    pending_guid = uuid.uuid4()
    pending = SourceConnection.objects.create(
        organization_id=org_id,
        guid=pending_guid,
        kind="github_app_install",
        display_name="GitHub App (pending registration)",
        is_active=False,
    )

    state = secrets.token_urlsafe(32)
    base = _app_base_url(request)
    install_slug = _install_slug_default(base)
    manifest = _manifest_json(
        request,
        install_slug=install_slug,
        connection_guid=str(pending.guid),
    )

    request.session["scm_github_manifest_state"] = {
        "state": state,
        "connection_guid": str(pending.guid),
        "return_to": return_to,
        "gh_org": gh_org,
        "install_slug": install_slug,
    }
    request.session.save()

    if gh_org:
        action_url = (
            f"https://github.com/organizations/{urllib.parse.quote(gh_org)}"
            f"/settings/apps/new?state={urllib.parse.quote(state)}"
        )
        target_label = f"organization “{gh_org}”"
    else:
        action_url = f"https://github.com/settings/apps/new?state={urllib.parse.quote(state)}"
        target_label = "your personal GitHub account"

    manifest_payload = json.dumps(manifest, separators=(",", ":"))
    html = _render_autosubmit_form(
        action_url=action_url,
        manifest_payload=manifest_payload,
        target_label=target_label,
    )
    return HttpResponse(html, content_type="text/html; charset=utf-8")


def _render_autosubmit_form(*, action_url: str, manifest_payload: str, target_label: str) -> str:
    """Tiny self-contained HTML page that auto-submits the manifest.

    Kept inline (not a Django template) so the auth1 app stays
    template-free for SCM flows — same shape as the OAuth-callback
    views. The page also offers a manual submit button so an operator
    on a JS-disabled browser (or paranoid sandbox) can still proceed."""
    safe_action = escape(action_url)
    safe_manifest = escape(manifest_payload)
    safe_target = escape(target_label)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Connecting Astrolift to GitHub…</title>
<meta name="robots" content="noindex">
<style>
  body {{ font: 14px/1.5 system-ui, sans-serif; max-width: 540px; margin: 6em auto; padding: 0 1em; color: #111; }}
  .card {{ border: 1px solid #d4dce4; border-radius: 8px; padding: 1.5em; }}
  button {{ font: inherit; padding: .6em 1.1em; border-radius: 6px; border: 1px solid #0a5; background: #0a5; color: #fff; cursor: pointer; }}
  .muted {{ color: #555; font-size: 13px; }}
</style>
</head>
<body>
<div class="card">
  <h1>Sending you to GitHub…</h1>
  <p>Astrolift is asking GitHub to create an App on {safe_target}. You stay in control — GitHub will show you the App's name and permissions before anything is created.</p>
  <form id="manifest-form" action="{safe_action}" method="post">
    <input type="hidden" name="manifest" value="{safe_manifest}">
    <button type="submit">Continue on GitHub →</button>
  </form>
  <p class="muted">If you're not redirected automatically, click the button above.</p>
</div>
<script>document.getElementById('manifest-form').submit();</script>
</body>
</html>
"""


@login_required
@csrf_exempt  # GitHub bounces the browser back here; the state cookie is the auth.
@require_GET
def github_app_manifest_callback(request: HttpRequest) -> Any:
    """Exchange the one-time code for App credentials, persist them.

    GitHub's spec: the code is single-use and expires after one hour.
    The exchange returns the App's id, client_id, client_secret, the
    webhook_secret GitHub generated for the manifest's hook, and the
    private-key PEM (RSA). We store the PEM in ``secret_ciphertext``
    (encrypted), the App ID in ``oauth_client_id``, the webhook secret
    in ``webhook_secret_ciphertext``, and the client_secret in a new
    field we don't currently model — for now we don't need the OAuth
    user flow off this App, so we drop the client_secret on the floor.
    (If we want per-user OAuth via the App later, add another column.)
    """
    state_in = request.GET.get("state", "")
    code = request.GET.get("code", "")
    pending = request.session.pop("scm_github_manifest_state", None)
    if not pending:
        return _redirect_with_error("/settings/source-providers", "no_pending_state")
    return_to = _safe_return_to(pending.get("return_to"))

    if not state_in or not secrets.compare_digest(state_in, pending["state"]):
        return _redirect_with_error(return_to, "state_mismatch")
    if not code:
        return _redirect_with_error(return_to, "no_code")

    connection = SourceConnection.objects.filter(
        guid=pending["connection_guid"],
        kind="github_app_install",
        deleted_at__isnull=True,
    ).first()
    if connection is None:
        return _redirect_with_error(return_to, "connection_missing")

    org_id = _active_org_id(request)
    if org_id is None or org_id != connection.organization_id:
        return _redirect_with_error(return_to, "org_mismatch")

    payload, err = _exchange_manifest_code(code)
    if err is not None:
        # Roll back the pre-allocated row so a failed registration
        # doesn't leave an orphaned inactive connection lying around.
        connection.soft_delete()
        return _redirect_with_error(return_to, err)
    assert payload is not None  # narrow for type-checkers

    app_id = str(payload.get("id") or "")
    pem = (payload.get("pem") or "").encode("utf-8")
    webhook_secret_plain = (payload.get("webhook_secret") or "").encode("utf-8")
    app_slug = (payload.get("slug") or "").strip()
    owner = payload.get("owner") or {}
    owner_login = (owner.get("login") or "").strip()

    if not app_id or not pem or not app_slug:
        connection.soft_delete()
        return _redirect_with_error(return_to, "exchange_no_credentials")

    pem_encrypted = encrypt_at_rest(pem)
    update_fields: dict[str, Any] = {
        "oauth_client_id": app_id,  # github_app provider reads app_id from this column
        "account_login": owner_login,
        "display_name": f"GitHub App: {app_slug}",
        "secret_backend_kind": pem_encrypted.backend_kind,
        "secret_ciphertext": pem_encrypted.backend_ref,
    }
    if webhook_secret_plain:
        webhook_encrypted = encrypt_at_rest(webhook_secret_plain)
        update_fields["webhook_secret_backend_kind"] = webhook_encrypted.backend_kind
        update_fields["webhook_secret_ciphertext"] = webhook_encrypted.backend_ref

    # We deliberately drop the OAuth client_secret — the manifest flow's
    # client_secret is for an OAuth user flow we don't use here. If we
    # ever add "Sign in with GitHub via this App" we'll bring it back;
    # today it would just sit decrypt-able-in-the-DB for no win.
    for k, v in update_fields.items():
        setattr(connection, k, v)
    connection.save()

    # Now bounce the operator over to the install URL so they can pick
    # which org/repos to grant the App access to. After install GitHub
    # redirects to setup_url with installation_id + setup_action=install.
    install_state = secrets.token_urlsafe(24)
    request.session["scm_github_install_state"] = {
        "state": install_state,
        "connection_guid": str(connection.guid),
        "return_to": return_to,
    }
    request.session.save()

    install_url = (
        f"https://github.com/apps/{urllib.parse.quote(app_slug)}/installations/new"
        f"?state={urllib.parse.quote(install_state)}"
    )
    return HttpResponseRedirect(install_url)


@login_required
@csrf_exempt
@require_GET
def github_app_manifest_setup(request: HttpRequest) -> Any:
    """Land here after the operator installs the App on an org.

    GitHub appends ``installation_id`` + ``setup_action``. We attach
    the installation id to the pending SourceConnection row + flip it
    active so the repo-listing path picks it up. Any other value of
    ``setup_action`` (e.g. ``request`` from a private-App "request
    install" flow) leaves the row inactive and surfaces an info toast.
    """
    state_in = request.GET.get("state", "")
    installation_id = (request.GET.get("installation_id") or "").strip()
    setup_action = (request.GET.get("setup_action") or "").strip()
    pending = request.session.pop("scm_github_install_state", None)
    if not pending:
        return _redirect_with_error("/settings/source-providers", "no_pending_install_state")
    return_to = _safe_return_to(pending.get("return_to"))

    if not state_in or not secrets.compare_digest(state_in, pending["state"]):
        return _redirect_with_error(return_to, "state_mismatch")

    connection = SourceConnection.objects.filter(
        guid=pending["connection_guid"],
        kind="github_app_install",
        deleted_at__isnull=True,
    ).first()
    if connection is None:
        return _redirect_with_error(return_to, "connection_missing")

    if setup_action != "install" or not installation_id.isdigit():
        # Operator may have cancelled or requested-rather-than-installed.
        # Leave the App credentials intact so they can re-try the install
        # from the GitHub App's settings later.
        return _redirect_with_error(return_to, "install_incomplete")

    connection.installation_id = installation_id[:64]
    connection.is_active = True
    connection.is_orphaned = False
    connection.orphaned_at = None
    connection.orphaned_reason = ""
    connection.save()
    label = connection.display_name or connection.account_login or "GitHub App"
    return _redirect_with_ok(return_to, label)


# ---------------------------------------------------------------------------
# GitHub API: code → credentials
# ---------------------------------------------------------------------------


def _exchange_manifest_code(code: str) -> tuple[dict[str, Any] | None, str | None]:
    """POST the manifest code to GitHub's conversion endpoint.

    Returns ``(payload, None)`` on success or ``(None, err_code)`` on
    failure. ``err_code`` is a short slug the UI translates to a toast
    via the ``scm_error=…`` query param — same convention as the
    OAuth-dance views in ``scm_oauth.py``.
    """
    url = f"{GITHUB_API_DEFAULT}/app-manifests/{urllib.parse.quote(code)}/conversions"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "astrolift",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 422):
            return None, "exchange_expired"
        return None, "exchange_failed"
    except urllib.error.URLError:
        return None, "exchange_failed"
    except (ValueError, json.JSONDecodeError):
        return None, "exchange_unexpected_shape"

    if not isinstance(data, dict):
        return None, "exchange_unexpected_shape"
    return data, None
