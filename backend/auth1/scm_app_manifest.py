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
``github_app_install`` ``SourceConnection`` row: the PEM goes in
``secret_ciphertext``, the numeric **App ID** in ``oauth_client_id``
(used by webhook-payload App lookups), and the OAuth **Client ID** in
``app_client_id`` (used by the ``/login/oauth/authorize`` redirect +
the GitHub-recommended JWT ``iss`` claim per #525).

The webhook URL inside the manifest references a stable guid we
pre-allocate (the new SourceConnection's ``guid``); the App is created
with its webhook pointing at
``<APP_BASE_URL>/app/auth1/scm/github/webhook/<connection_guid>/``
from minute one, so the existing webhook ingest layer needs no changes.

After persistence we redirect the operator to
``https://github.com/apps/<slug>/installations/new?state=<state>`` so
they can install the App on whichever org they actually want it on.
GitHub bounces back to our ``setup_url`` with
``?installation_id=<id>&setup_action=install``. Once GitHub, asked with
the App's own key, confirms that id (#2631), we attach it to the connection
row and the connection becomes usable for repo listing + commit-status
writes.

The same three views register the factory gate App (#2589) when the start
URL carries ``purpose=factory_gate``: one GitHub organization, organization
Members read and nothing else, no webhook, no user OAuth. Its row is a
``github_factory_gate_app`` connection; see ``astrolift_scm.services.factory_gate``.

A start URL carrying ``connection=<guid>`` re-links that App connection
(#2601): GitHub's install page for its App, then the installation id GitHub
confirms on the row's account goes back on the same row.

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
import logging
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from astrolift_identity.models import Member
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers.github_app import INSTALL
from astrolift_scm.scopes import require_scm_connect
from astrolift_scm.services import connection_status, factory_gate
from auth1.return_urls import safe_relative_path
from core.mutations import AuditEntry, emit_audit
from core.permissions import Permission, route_auth
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

logger = logging.getLogger(__name__)

GITHUB_API_DEFAULT = "https://api.github.com"

RELINK = "scm.github_app.relink"

# Prefix for the ``display_name`` we stamp on a fully-created GitHub-App
# connection ("GitHub App: <slug>"). The App's URL slug is only persisted
# inside ``display_name`` — there's no dedicated column — so the reuse
# path recovers it from here (see ``_app_slug_from_connection``). Kept as
# one constant so the set-site (callback) and parse-site can't drift.
_APP_DISPLAY_PREFIX = "GitHub App: "

# GITHUB_APP_CONNECTION_SCOPE values. per_org: one App per org. per_install:
# one App shared across every org in the install. Anything else → per_org.
_CONNECTION_SCOPES = frozenset({"per_org", "per_install"})
_DEFAULT_CONNECTION_SCOPE = "per_org"

# GitHub org/user slug rule: alphanumeric + hyphen, no consecutive
# hyphens, max 39 chars. We only enforce the character class + length
# here — anything subtler is the operator's problem at GitHub.
_GH_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_return_to(raw: str | None, default: str = "/settings/source-providers") -> str:
    """``raw`` when it is a path on this host, else ``default``, so a redirect
    built from it cannot send the browser to another host. Callers that
    reconnect from a specific surface (the onboarding wizard) pass their own
    ``default``."""
    return safe_relative_path(raw) or default


def _with_param(return_to: str, param: str) -> str:
    """Add ``param`` to the query of ``return_to``, ahead of any fragment: the
    Providers page keeps its tab in the fragment (``/providers#source``), and a
    parameter after ``#`` would be read as part of it."""
    path, hash_mark, fragment = return_to.partition("#")
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}{param}{hash_mark}{fragment}"


def _redirect_with_error(return_to: str, code: str) -> HttpResponseRedirect:
    return HttpResponseRedirect(_with_param(return_to, f"scm_error={code}"))


def _redirect_with_ok(return_to: str, label: str) -> HttpResponseRedirect:
    return HttpResponseRedirect(_with_param(return_to, f"scm_connected={urllib.parse.quote(label)}"))


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


_LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _is_localhost_url(url: str) -> bool:
    """True when ``url`` resolves to a loopback host github.com can't reach."""
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return False
    return host.lower() in _LOCALHOST_HOSTS


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

    Permissions are what autowire uses, not a minimal set (#1713).

    This deliberately reverses an earlier decision to "stay minimal" and let
    the operator broaden later. The narrow set read as good practice but made
    the App unable to do any of the things autowire exists to do -- commit the
    CI workflow, push Actions secrets, trigger runs, install the webhook -- and
    the failure surfaced as a 403 from GitHub, which reads like the operator
    misconfigured something rather than the manifest having asked for the wrong
    set. "Broaden later" is not a fallback an operator can act on when nothing
    tells them which permission is missing.

    Minimal remains the right instinct; the mistake was measuring it against
    the API surface rather than against what the product then does with the
    App. Each grant below is tied to one autowire step, and anything no step
    uses stays out.
    """
    base = _app_base_url(request)
    # Manifest-completion redirect — fires once when the App is first
    # created from the manifest. Different from the OAuth callback.
    manifest_redirect_url = f"{base}/app/auth1/scm/github/app-manifest/callback"
    setup_url = f"{base}/app/auth1/scm/github/app-manifest/setup"
    webhook_url = f"{base}/app/auth1/scm/github/webhook/{connection_guid}/"
    # User-to-server OAuth callback — fires every time an operator
    # authorizes the App on their account.  Without this in the manifest
    # the created App's "Identifying and authorizing users" section is
    # empty, the OAuth dance can't redirect anywhere, and any path that
    # needs a user token (push CI workflow, push CI secrets, deploy
    # dispatch) silently falls back to no-creds and 403s.
    oauth_callback_url = f"{base}/app/auth1/scm/github/callback"

    # ``request_oauth_on_install=True`` causes GitHub to route the
    # operator through the OAuth dance during App install and land on
    # ``callback_urls[0]`` (our /callback endpoint) instead of
    # ``setup_url``.  GitHub sends ``code`` + ``installation_id`` +
    # ``setup_action=install`` together, so a single callback trip
    # both activates the connection row AND mints the per-user OAuth
    # token — no separate "Connect" click needed.  The unified
    # ``github_callback`` handler now detects the install-time params
    # and routes to ``_github_install_time_callback`` which skips the
    # session-state check (GitHub generated the state, not us) and
    # uses the ``scm_github_install_state`` cookie to anchor the row.
    return {
        "name": f"astrolift-{install_slug}",
        "url": base,
        "hook_attributes": {
            "url": webhook_url,
            "active": True,
        },
        "redirect_url": manifest_redirect_url,
        "callback_urls": [oauth_callback_url],
        "setup_url": setup_url,
        "setup_on_update": True,
        "request_oauth_on_install": True,
        "public": False,
        # Every permission autowire actually uses (#1713). The manifest asked
        # for four and autowire needs seven, so an operator who followed the
        # product's own App-creation flow got an App that could not do any of
        # the things autowire exists to do -- and the failure surfaced as a 403
        # from GitHub, which reads like their misconfiguration rather than the
        # manifest asking for the wrong set.
        #
        # Each of these is load-bearing for one step:
        #   contents: write        commit the CI workflow file
        #   workflows: write       GitHub refuses a push touching .github/workflows
        #                          without it, even with contents: write
        #   secrets: write         push Actions secrets
        #   actions: write         trigger and read workflow runs
        #   repository_hooks: write  install the source webhook -- the key the
        #                            SCM layer names in its own 403 message
        #
        # A widened manifest only affects Apps created from it after this
        # change. An App already installed keeps the permissions it was granted
        # until an org admin accepts the new set, so this does not repair an
        # existing install on its own -- see the note in the issue.
        "default_permissions": {
            "actions": "write",
            "checks": "write",
            "contents": "write",
            "metadata": "read",
            "pull_requests": "write",
            "repository_hooks": "write",
            "secrets": "write",
            "workflows": "write",
        },
        # ``repository`` carries the rename the receiver follows (#2478); it
        # needs only metadata read.
        "default_events": ["push", "pull_request", "repository"],
    }


def _factory_gate_manifest_json(request: HttpRequest) -> dict[str, Any]:
    """The factory gate App's manifest (#2589): organization Members read and
    nothing else, so its tokens can read org membership and nothing more. No
    webhook, no events and no user OAuth: the App only mints installation
    tokens. Private, so only its own organization can install it."""
    base = _app_base_url(request)
    return {
        "name": settings.FACTORY_GATE_APP_NAME,
        "url": base,
        "description": "Reads organization membership for the Calliope AI factory's quarantine gate.",
        "redirect_url": f"{base}/app/auth1/scm/github/app-manifest/callback",
        "setup_url": f"{base}/app/auth1/scm/github/app-manifest/setup",
        "setup_on_update": True,
        "public": False,
        "default_permissions": dict(factory_gate.PERMISSIONS),
        "default_events": [],
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
# App reuse (dedup) — reuse an existing GitHub App instead of minting a
# brand-new one on every "Connect GitHub" click, so an install stops
# accumulating duplicate Apps. Scope (GITHUB_APP_CONNECTION_SCOPE) decides
# how wide "existing" is: per-org or shared across the whole install.
# ---------------------------------------------------------------------------


def _connection_scope() -> str:
    """Resolve GITHUB_APP_CONNECTION_SCOPE, normalising unknown values to
    ``per_org`` (with a warning) so a typo in the install config can never
    silently widen App sharing to the whole install."""
    raw = (getattr(settings, "GITHUB_APP_CONNECTION_SCOPE", _DEFAULT_CONNECTION_SCOPE) or "").strip().lower()
    if raw not in _CONNECTION_SCOPES:
        if raw:
            logger.warning(
                "Unknown GITHUB_APP_CONNECTION_SCOPE %r; defaulting to %s",
                raw,
                _DEFAULT_CONNECTION_SCOPE,
            )
        return _DEFAULT_CONNECTION_SCOPE
    return raw


def _app_slug_from_connection(connection: SourceConnection) -> str:
    """Recover the GitHub App URL slug from a connection's display_name.

    The slug lives only inside ``display_name`` (``GitHub App: <slug>``);
    there's no dedicated column. Returns "" when the operator has renamed
    the connection away from that shape — callers treat that as "can't
    reuse" and fall back to the App-create flow rather than build a broken
    install URL."""
    name = connection.display_name or ""
    if not name.startswith(_APP_DISPLAY_PREFIX):
        return ""
    return name[len(_APP_DISPLAY_PREFIX) :].strip()


def _find_canonical_app(org_id: int, scope: str) -> SourceConnection | None:
    """Oldest reusable ``github_app_install`` connection in scope.

    Reusable = active, not orphaned, not soft-deleted (default manager),
    carrying real App credentials (numeric App ID + PEM) — i.e. an App that
    finished the manifest CREATE exchange, not an abandoned pending row.
    ``per_install`` searches the whole install; anything else scopes to the
    current org. When several match (the duplicate state we're converging
    away from) the oldest wins, deterministically."""
    qs = SourceConnection.objects.filter(
        kind="github_app_install",
        is_active=True,
        is_orphaned=False,
    ).exclude(oauth_client_id="")
    if scope != "per_install":
        qs = qs.filter(organization_id=org_id)
    # Oldest first; a stale pending row (blank App ID) is already excluded,
    # and we additionally require a stored PEM so a half-written row can't
    # be picked as canonical.
    for candidate in qs.order_by("created_at", "pk"):
        if bytes(candidate.secret_ciphertext):
            return candidate
    return None


def _current_org_installed_app(org_id: int, canonical: SourceConnection) -> SourceConnection | None:
    """The current org's own active, fully-installed App connection that
    corresponds to ``canonical`` — matched by numeric App ID, or by App
    owner login. The unique constraint already forbids two app_installs per
    (org, owner), so an active connection to the same owner *is* this org's
    App. Returns None when the org hasn't installed the App yet."""
    match = Q(oauth_client_id=canonical.oauth_client_id)
    owner = canonical.account_login
    if owner:
        match |= Q(account_login=owner)
    return (
        SourceConnection.objects.filter(
            organization_id=org_id,
            kind="github_app_install",
            is_active=True,
            is_orphaned=False,
        )
        .exclude(installation_id="")
        .filter(match)
        .order_by("created_at", "pk")
        .first()
    )


def _org_has_install_on(org_id: int, account_login: str) -> bool:
    """Whether this org already holds an installed App connection for the
    GitHub account ``account_login`` (case-insensitive)."""
    return (
        SourceConnection.objects.filter(
            organization_id=org_id,
            kind="github_app_install",
            is_active=True,
            is_orphaned=False,
            account_login__iexact=account_login,
        )
        .exclude(installation_id="")
        .exists()
    )


def _record_installed_accounts(connection: SourceConnection) -> None:
    """Relabel this org's rows for ``connection``'s App with the GitHub
    account each installation actually sits on (#2297).

    The manifest flow keys its row by the App's owner and a reuse install by
    the login the operator typed, but the resolver routes a repo by the
    installation's account. GitHub's ``/app/installations`` is the source of
    truth, so a setup callback backfills every row of this org for the App.
    A login another row already holds is left alone (unique key). Best
    effort: any failure keeps the stored logins and never breaks setup."""
    from astrolift_scm.providers import github_app as gh_app

    issuer = connection.app_client_id or connection.oauth_client_id
    if not issuer or not connection.oauth_client_id:
        return
    try:
        jwt_token = gh_app._mint_jwt(issuer, gh_app._decrypt_pem(connection))
    except Exception:  # noqa: BLE001 -- unreadable PEM: keep the stored logins
        logger.warning(
            "Cannot sign a JWT for connection %s; installation accounts not recorded", connection.guid
        )
        return
    installations, err = gh_app.list_app_installations(gh_app._api_base(connection), jwt_token)
    if err is not None:
        logger.warning("Cannot list installations for connection %s: %s", connection.guid, err.message)
        return
    accounts = {str(i.get("id")): (i.get("account") or {}).get("login") or "" for i in installations}

    org_rows = list(
        SourceConnection.objects.filter(organization_id=connection.organization_id, kind="github_app_install")
    )
    taken = {(r.account_login or "").lower() for r in org_rows}
    for row in org_rows:
        login = accounts.get(row.installation_id or "", "")
        if row.oauth_client_id != connection.oauth_client_id or not login or login == row.account_login:
            continue
        if login.lower() in taken and login.lower() != (row.account_login or "").lower():
            continue
        taken.discard((row.account_login or "").lower())
        taken.add(login.lower())
        row.account_login = login
        row.save(update_fields=["account_login"])


def _decrypt_optional(backend_kind: str, ciphertext: Any) -> bytes:
    """Decrypt a stored secret, tolerating the empty/unset case."""
    raw = bytes(ciphertext or b"")
    if not raw or not backend_kind:
        return b""
    return decrypt(EncryptedSecret(backend_kind=backend_kind, backend_ref=raw))


def _copied_app_secrets(canonical: SourceConnection) -> dict[str, Any]:
    """Re-encrypt the canonical App's secret material for a reuse copy.

    Every secret an App holds is App-level (identical across installations),
    so a faithful copy carries them all: the PEM signs installation tokens,
    the OAuth client_secret is required by request_oauth_on_install's
    install-time OAuth callback, and the webhook_secret verifies inbound
    payloads. We decrypt with the flow's own helpers and re-encrypt with the
    CURRENT backend (matches the manifest callback's storage; survives a key
    rotation). Never logged or returned in plaintext."""
    from astrolift_scm.providers.github_app import _decrypt_pem

    secrets_map = {
        ("secret_backend_kind", "secret_ciphertext"): _decrypt_pem(canonical),
        ("oauth_client_secret_backend_kind", "oauth_client_secret_ciphertext"): _decrypt_optional(
            canonical.oauth_client_secret_backend_kind,
            canonical.oauth_client_secret_ciphertext,
        ),
        ("webhook_secret_backend_kind", "webhook_secret_ciphertext"): _decrypt_optional(
            canonical.webhook_secret_backend_kind,
            canonical.webhook_secret_ciphertext,
        ),
    }
    out: dict[str, Any] = {}
    for (kind_field, ct_field), plaintext in secrets_map.items():
        if plaintext:
            enc = encrypt_at_rest(plaintext)
            out[kind_field] = enc.backend_kind
            out[ct_field] = enc.backend_ref
    return out


def _start_reuse_install(
    request: HttpRequest,
    canonical: SourceConnection,
    *,
    org_id: int,
    return_to: str,
    account_login: str = "",
) -> HttpResponseRedirect | None:
    """Case (b): the App exists elsewhere in the install but not on this
    org, or this org wants it on a further GitHub account
    (``account_login``, #2297). Create a pending connection that copies
    the canonical App's credentials and bounce the operator to GitHub's
    install page for the EXISTING App. The existing install/setup callback then attaches this
    org's ``installation_id`` to the pending row (anchored via the
    ``scm_github_install_state`` we prime here). Returns None (→ caller
    falls back to the create flow) when we can't recover the App slug to
    build the install URL."""
    slug = _app_slug_from_connection(canonical)
    if not _validate_gh_slug(slug):
        logger.warning(
            "Cannot recover a GitHub App slug from connection %s; falling back to the create flow",
            canonical.guid,
        )
        return None

    owner = account_login or canonical.account_login
    # Clear any row for this org that would collide with the copy's unique
    # key (organization, kind, account_login) and is not a live install: a
    # never-installed pending row, or a dead one (inactive, orphaned) left on
    # the same account. Same spirit as the create-path cleanup below.
    live = Q(is_active=True, is_orphaned=False) & ~Q(installation_id="")
    SourceConnection.objects.filter(
        organization_id=org_id,
        kind="github_app_install",
    ).filter(
        Q(account_login="", is_active=False, installation_id="") | Q(account_login__iexact=owner)
    ).exclude(live).update(deleted_at=timezone.now())

    pending = SourceConnection.objects.create(
        organization_id=org_id,
        guid=uuid.uuid4(),
        kind="github_app_install",
        is_active=False,
        display_name=canonical.display_name,
        account_login=owner,
        api_base_url=canonical.api_base_url,
        oauth_client_id=canonical.oauth_client_id,  # numeric App ID
        app_client_id=canonical.app_client_id,  # OAuth Client ID
        oauth_redirect_uri=canonical.oauth_redirect_uri,
        **_copied_app_secrets(canonical),
    )

    install_state = secrets.token_urlsafe(24)
    request.session["scm_github_install_state"] = {
        "state": install_state,
        "connection_guid": str(pending.guid),
        "return_to": return_to,
        # The return's installation must sit on the account the operator
        # named (#2631). Without one the row carries a default login, and the
        # account GitHub installed on replaces it (_record_installed_accounts).
        "account_login": account_login,
    }
    request.session.save()

    install_url = (
        f"https://github.com/apps/{urllib.parse.quote(slug)}/installations/new"
        f"?state={urllib.parse.quote(install_state)}"
    )
    return HttpResponseRedirect(install_url)


# ---------------------------------------------------------------------------
# Factory gate App (#2589): the same flow, one GitHub organization, Members
# read only. Each org has at most one; a start never creates a second.
# ---------------------------------------------------------------------------


def _factory_gate_install_redirect(
    request: HttpRequest, app: SourceConnection, *, slug: str, return_to: str
) -> HttpResponseRedirect:
    install_state = secrets.token_urlsafe(24)
    request.session["scm_github_install_state"] = {
        "state": install_state,
        "connection_guid": str(app.guid),
        "return_to": return_to,
        "purpose": factory_gate.PURPOSE,
    }
    request.session.save()
    return HttpResponseRedirect(
        f"https://github.com/apps/{urllib.parse.quote(slug)}/installations/new"
        f"?state={urllib.parse.quote(install_state)}"
    )


def _start_factory_gate(request: HttpRequest, *, org_id: int, gh_org: str, return_to: str) -> HttpResponse:
    if not gh_org:
        return _redirect_with_error(return_to, "factory_gate_org_required")
    app = factory_gate.current_app(org_id)
    state = factory_gate.state_of(app)
    if app is not None:
        if state is factory_gate.State.MISSING_KEY:
            return _redirect_with_error(return_to, "factory_gate_missing_key")
        if state is factory_gate.State.INSTALLED:
            return _redirect_with_ok(return_to, app.display_name or "GitHub App")
        # Registered but never installed: install that App rather than create another.
        if app.account_login.lower() != gh_org.lower():
            return _redirect_with_error(return_to, "factory_gate_org_mismatch")
        slug = _app_slug_from_connection(app)
        if not _validate_gh_slug(slug):
            return _redirect_with_error(return_to, "factory_gate_slug_unknown")
        return _factory_gate_install_redirect(request, app, slug=slug, return_to=return_to)

    # A pending row from an abandoned registration has no App id; clear it so
    # the new pending row fits the (organization, kind, account_login) key.
    SourceConnection.objects.filter(organization_id=org_id, kind=factory_gate.KIND, oauth_client_id="").update(
        deleted_at=timezone.now()
    )
    pending = SourceConnection.objects.create(
        organization_id=org_id,
        guid=uuid.uuid4(),
        kind=factory_gate.KIND,
        display_name="Factory gate App (pending registration)",
        is_active=False,
    )
    state_token = secrets.token_urlsafe(32)
    request.session["scm_github_manifest_state"] = {
        "state": state_token,
        "connection_guid": str(pending.guid),
        "return_to": return_to,
        "gh_org": gh_org,
        "purpose": factory_gate.PURPOSE,
    }
    request.session.save()
    html = _render_autosubmit_form(
        action_url=(
            f"https://github.com/organizations/{urllib.parse.quote(gh_org)}"
            f"/settings/apps/new?state={urllib.parse.quote(state_token)}"
        ),
        manifest_payload=json.dumps(_factory_gate_manifest_json(request), separators=(",", ":")),
        target_label=f"organization “{gh_org}”",
    )
    return HttpResponse(html, content_type="text/html; charset=utf-8")


def _finish_factory_gate_registration(
    request: HttpRequest,
    connection: SourceConnection,
    *,
    app_id: str,
    client_id: str,
    pem: bytes,
    app_slug: str,
    owner: dict[str, Any],
    expected_org: str,
    return_to: str,
) -> HttpResponseRedirect:
    """Keep what the factory gate App needs: its ids, its organization and its
    key, encrypted. The exchange also returns an OAuth client secret; this App
    has no user OAuth and no webhook, so no other secret is stored."""
    owner_login = (owner.get("login") or "").strip()
    if owner_login.lower() != expected_org.lower() or owner.get("type") not in (None, "Organization"):
        connection.soft_delete()
        factory_gate.audit(
            factory_gate.REGISTER,
            actor_user_id=request.user.pk,
            organization_id=connection.organization_id,
            app_id=app_id,
            decision="DENY",
            error_code="WRONG_OWNER",
            error_message=f"the App was created on {owner_login or 'an unknown account'}, not {expected_org}",
            extra={"owner_login": owner_login},
        )
        return _redirect_with_error(return_to, "factory_gate_owner_mismatch")

    pem_encrypted = encrypt_at_rest(pem)
    connection.oauth_client_id = app_id
    connection.app_client_id = client_id[:64]
    connection.account_login = owner_login[:200]
    connection.display_name = f"{_APP_DISPLAY_PREFIX}{app_slug}"[:200]
    connection.secret_backend_kind = pem_encrypted.backend_kind
    connection.secret_ciphertext = pem_encrypted.backend_ref
    connection.save()
    factory_gate.audit(
        factory_gate.REGISTER,
        actor_user_id=request.user.pk,
        organization_id=connection.organization_id,
        app_id=app_id,
        extra={"owner_login": owner_login, "app_slug": app_slug},
    )
    return _factory_gate_install_redirect(request, connection, slug=app_slug, return_to=return_to)


# ---------------------------------------------------------------------------
# Re-link (#2601): install a connection's App on that connection's account
# again and keep the row. Its guid is in the App's webhook URL and in the
# webhook installations and trigger tokens that point at it; replacing the
# row, as Connect to GitHub does, loses them.
# ---------------------------------------------------------------------------


def _start_relink(request: HttpRequest, *, org_id: int, raw_guid: str, return_to: str) -> HttpResponseRedirect:
    """Send the browser to GitHub's install page for the App of the active
    organization's connection ``raw_guid``. The install state carries the
    row's guid, so the return stores the installation on that row."""
    try:
        guid = uuid.UUID(raw_guid)
    except ValueError:
        return _redirect_with_error(return_to, "connection_missing")
    connection = SourceConnection.objects.filter(
        organization_id=org_id, guid=guid, kind="github_app_install", deleted_at__isnull=True
    ).first()
    if connection is None:
        return _redirect_with_error(return_to, "connection_missing")
    slug = _app_slug_from_connection(connection)
    if not (
        connection_status.can_relink(connection)
        and bytes(connection.secret_ciphertext)
        and _validate_gh_slug(slug)
    ):
        return _redirect_with_error(return_to, "relink_unavailable")

    install_state = secrets.token_urlsafe(24)
    request.session["scm_github_install_state"] = {
        "state": install_state,
        "connection_guid": str(connection.guid),
        "return_to": return_to,
        "relink": True,
    }
    request.session.save()
    return HttpResponseRedirect(
        f"https://github.com/apps/{urllib.parse.quote(slug)}/installations/new"
        f"?state={urllib.parse.quote(install_state)}"
    )


def _attach_installation(
    request: HttpRequest,
    connection: SourceConnection,
    installation_id: str,
    *,
    setup_action: str,
    action: str,
    account_login: str,
) -> str | None:
    """Store ``installation_id`` on ``connection`` once GitHub, asked with the
    App's own key, confirms it is this App's installation, on ``account_login``
    when one is expected, and no other organization's connection holds it
    (``github_app.confirm_installation``). The id arrives in the browser's
    redirect, so it is checked, not trusted. ``update`` is what GitHub sends
    when the App was already installed on the account; only a re-link passes
    it here. A refusal leaves the row as it was. Each outcome is audited as
    ``action``. Returns an error code for the redirect, or None."""
    from astrolift_scm.providers import github_app as gh_app

    def audit(
        decision: str = "ALLOW", error_code: str | None = None, error_message: str | None = None, **extra: Any
    ) -> None:
        emit_audit(
            AuditEntry(
                actor_user_id=request.user.pk,
                organization_id=connection.organization_id,
                action=action,
                decision=decision,
                target_kind="source_connection",
                target_id=str(connection.guid),
                duration_ms=0,
                permissions=(Permission.SCM_CONNECT,),
                error_code=error_code,
                error_message=error_message,
                extra={
                    "app_id": connection.oauth_client_id,
                    "account_login": connection.account_login,
                    "installation_id": installation_id,
                    "setup_action": setup_action,
                    **extra,
                },
            )
        )

    if setup_action not in ("install", "update") or not installation_id.isdigit():
        return "install_incomplete"
    code, message, installed_on = gh_app.confirm_installation(
        connection, installation_id, account_login=account_login
    )
    if code in (gh_app.WRONG_INSTALLATION, gh_app.INSTALLATION_IN_USE):
        audit("DENY", code, message, installed_on=installed_on)
        return "install_wrong_account" if code == gh_app.WRONG_INSTALLATION else "install_in_use"
    if code is not None:
        # GitHub was not asked or did not answer: an allowed attempt that failed.
        audit(error_code=code, error_message=message)
        return "install_unconfirmed"

    previous = connection.installation_id
    connection.installation_id = installation_id[:64]
    connection.is_active = True
    connection.is_orphaned = False
    connection.orphaned_at = None
    connection.orphaned_reason = ""
    # save() moves the row version, and connection_status keys its 60 s
    # verdict by version, so the next load checks GitHub again.
    connection.save()
    audit(installed_on=installed_on, previous_installation_id=previous)
    return None


def _finish_relink(
    request: HttpRequest, pending: dict[str, Any], *, installation_id: str, setup_action: str
) -> HttpResponseRedirect:
    """GitHub's return from a re-link start, through the setup URL or, for an
    App that asks for user authorization on install, the OAuth callback. Its
    OAuth code is not redeemed: a re-link puts the installation back, and
    Connect my GitHub stays the way to a user token."""
    return_to = _safe_return_to(pending.get("return_to"))
    connection = SourceConnection.objects.filter(
        guid=pending.get("connection_guid", ""), kind="github_app_install", deleted_at__isnull=True
    ).first()
    if connection is None:
        return _redirect_with_error(return_to, "connection_missing")
    if _active_org_id(request) != connection.organization_id:
        return _redirect_with_error(return_to, "org_mismatch")
    err = _attach_installation(
        request,
        connection,
        installation_id,
        setup_action=setup_action,
        action=RELINK,
        account_login=connection.account_login,
    )
    if err is not None:
        return _redirect_with_error(return_to, err)
    return _redirect_with_ok(return_to, connection.display_name or "GitHub App")


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@login_required
@require_GET
@route_auth(
    credential="session or API bearer", scope="active organization", permissions=[Permission.SCM_CONNECT]
)
def github_app_manifest_start(request: HttpRequest) -> Any:
    """Render an HTML form that auto-posts the App manifest to GitHub.

    GitHub requires the manifest to arrive as a form POST (not a
    cross-origin fetch), so we render a tiny self-submitting HTML page
    here. The form's action is either
    ``https://github.com/organizations/<org>/settings/apps/new?state=…``
    (org install) or ``https://github.com/settings/apps/new?state=…``
    (personal account install).
    """
    require_scm_connect()
    return_to = _safe_return_to(request.GET.get("return_to"))
    gh_org = (request.GET.get("org") or "").strip()
    if gh_org and not _validate_gh_slug(gh_org):
        return _redirect_with_error(return_to, "invalid_org_slug")

    org_id = _active_org_id(request)
    if org_id is None:
        return _redirect_with_error(return_to, "no_org")

    relink_guid = (request.GET.get("connection") or "").strip()
    if relink_guid:
        return _start_relink(request, org_id=org_id, raw_guid=relink_guid, return_to=return_to)

    if request.GET.get("purpose") == factory_gate.PURPOSE:
        return _start_factory_gate(request, org_id=org_id, gh_org=gh_org, return_to=return_to)

    # Dedup: before minting a brand-new App, look for a reusable one in
    # scope. Resolved here — ahead of the pending-row + manifest render —
    # so we never run the App-CREATE path when a reusable App exists.
    # Also ahead of the localhost guard below: cases (a)/(b) don't embed a
    # webhook URL, so they don't need APP_BASE_URL to be publicly reachable.
    canonical = _find_canonical_app(org_id, _connection_scope())
    if canonical is not None:
        existing = _current_org_installed_app(org_id, canonical)
        if existing is not None and (not gh_org or _org_has_install_on(org_id, gh_org)):
            # (a) This org already has the App installed — nothing to do.
            return _redirect_with_ok(return_to, existing.display_name or "GitHub App")
        # (b) The App exists elsewhere in the install but not on this org,
        # or this org names a GitHub account it has no installation on yet
        # (#2297); install the EXISTING App there instead of creating a
        # duplicate.
        reused = _start_reuse_install(
            request,
            canonical,
            org_id=org_id,
            return_to=return_to,
            account_login=gh_org,
        )
        if reused is not None:
            return reused
        # Slug unrecoverable → fall through to the create flow (c).

    # github.com must be able to reach the webhook URL we embed in the
    # manifest. Refuse the flow early if the base URL is loopback —
    # otherwise GitHub rejects the manifest with
    # "Hook url is not supported because it isn't reachable over the
    # public Internet (localhost)" after the operator has clicked
    # through everything. Set APP_BASE_URL to the install's public URL.
    if _is_localhost_url(_app_base_url(request)):
        return _redirect_with_error(return_to, "app_base_url_not_public")

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
@route_auth(
    credential="session or API bearer", scope="active organization", permissions=[Permission.SCM_CONNECT]
)
def github_app_manifest_callback(request: HttpRequest) -> Any:
    """Exchange the one-time code for App credentials, persist them.

    GitHub's spec: the code is single-use and expires after one hour.
    The exchange returns the App's id, client_id, client_secret, the
    webhook_secret GitHub generated for the manifest's hook, and the
    private-key PEM (RSA). We store the PEM in ``secret_ciphertext``
    (encrypted), the App ID in ``oauth_client_id``, the webhook secret
    in ``webhook_secret_ciphertext``, and the user-to-server OAuth
    ``client_secret`` in ``oauth_client_secret_ciphertext`` — that
    secret is what ``scm_oauth.github_callback`` needs to redeem the
    user's authorization code for an access token when they click
    "Connect my GitHub" against this App row.
    """
    require_scm_connect()
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

    purpose = pending.get("purpose") or ""
    connection = SourceConnection.objects.filter(
        guid=pending["connection_guid"],
        kind=factory_gate.KIND if purpose == factory_gate.PURPOSE else "github_app_install",
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
    # The user-to-server OAuth Client ID — distinct from the numeric
    # App ID above. Drives the /login/oauth/authorize redirect + the
    # JWT iss claim (per #525). Manifest payloads after 2022 include
    # both fields; we persist them separately.
    client_id = str(payload.get("client_id") or "").strip()
    pem = (payload.get("pem") or "").encode("utf-8")
    webhook_secret_plain = (payload.get("webhook_secret") or "").encode("utf-8")
    client_secret_plain = (payload.get("client_secret") or "").encode("utf-8")
    app_slug = (payload.get("slug") or "").strip()
    owner = payload.get("owner") or {}
    owner_login = (owner.get("login") or "").strip()

    if not app_id or not pem or not app_slug:
        connection.soft_delete()
        return _redirect_with_error(return_to, "exchange_no_credentials")

    if purpose == factory_gate.PURPOSE:
        return _finish_factory_gate_registration(
            request,
            connection,
            app_id=app_id,
            client_id=client_id,
            pem=pem,
            app_slug=app_slug,
            owner=owner,
            expected_org=pending.get("gh_org") or "",
            return_to=return_to,
        )

    pem_encrypted = encrypt_at_rest(pem)
    update_fields: dict[str, Any] = {
        "oauth_client_id": app_id,  # numeric App ID — webhook payload lookups
        "app_client_id": client_id,  # OAuth Client ID — /authorize + JWT iss
        "account_login": owner_login,
        "display_name": f"{_APP_DISPLAY_PREFIX}{app_slug}",
        "secret_backend_kind": pem_encrypted.backend_kind,
        "secret_ciphertext": pem_encrypted.backend_ref,
    }
    if webhook_secret_plain:
        webhook_encrypted = encrypt_at_rest(webhook_secret_plain)
        update_fields["webhook_secret_backend_kind"] = webhook_encrypted.backend_kind
        update_fields["webhook_secret_ciphertext"] = webhook_encrypted.backend_ref

    # The user-to-server OAuth client_secret is needed by the "Connect
    # my GitHub" dance (auth1.scm_oauth.github_callback) to redeem the
    # user's code at /login/oauth/access_token. Stored in a column
    # separate from ``secret_ciphertext`` (which holds the App's PEM)
    # so a single row can carry both credentials.
    if client_secret_plain:
        client_secret_encrypted = encrypt_at_rest(client_secret_plain)
        update_fields["oauth_client_secret_backend_kind"] = client_secret_encrypted.backend_kind
        update_fields["oauth_client_secret_ciphertext"] = client_secret_encrypted.backend_ref

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
@route_auth(
    credential="session or API bearer", scope="active organization", permissions=[Permission.SCM_CONNECT]
)
def github_app_manifest_setup(request: HttpRequest) -> Any:
    """Land here after the operator installs the App on an org.

    GitHub appends ``installation_id`` + ``setup_action``. We attach
    the installation id GitHub confirms (``_attach_installation``) to the
    pending SourceConnection row + flip it active so the repo-listing path
    picks it up. Any other value of ``setup_action`` (e.g. ``request``
    from a private-App "request install" flow) leaves the row inactive
    and surfaces an info toast. A re-link also takes ``update``.
    """
    require_scm_connect()
    state_in = request.GET.get("state", "")
    installation_id = (request.GET.get("installation_id") or "").strip()
    setup_action = (request.GET.get("setup_action") or "").strip()
    pending = request.session.pop("scm_github_install_state", None)
    if not pending:
        return _redirect_with_error("/settings/source-providers", "no_pending_install_state")
    return_to = _safe_return_to(pending.get("return_to"))

    if not state_in or not secrets.compare_digest(state_in, pending["state"]):
        return _redirect_with_error(return_to, "state_mismatch")

    if pending.get("relink"):
        return _finish_relink(request, pending, installation_id=installation_id, setup_action=setup_action)

    purpose = pending.get("purpose") or ""
    connection = SourceConnection.objects.filter(
        guid=pending["connection_guid"],
        kind=factory_gate.KIND if purpose == factory_gate.PURPOSE else "github_app_install",
        deleted_at__isnull=True,
    ).first()
    if connection is None:
        return _redirect_with_error(return_to, "connection_missing")

    if _active_org_id(request) != connection.organization_id:
        return _redirect_with_error(return_to, "org_mismatch")

    if purpose == factory_gate.PURPOSE:
        # ``update`` too: re-saving the installation on GitHub runs the
        # confirmation again after a first one failed.
        if setup_action not in ("install", "update") or not installation_id.isdigit():
            return _redirect_with_error(return_to, "install_incomplete")
        err = factory_gate.record_installation(connection, installation_id, actor_user_id=request.user.pk)
        if err is not None:
            return _redirect_with_error(return_to, err)
        return _redirect_with_ok(return_to, connection.display_name or "GitHub App")

    if setup_action != "install" or not installation_id.isdigit():
        # Operator may have cancelled or requested-rather-than-installed.
        # Leave the App credentials intact so they can re-try the install
        # from the GitHub App's settings later.
        return _redirect_with_error(return_to, "install_incomplete")

    err = _attach_installation(
        request,
        connection,
        installation_id,
        setup_action=setup_action,
        action=INSTALL,
        account_login=pending.get("account_login") or "",
    )
    if err is not None:
        return _redirect_with_error(return_to, err)
    _record_installed_accounts(connection)
    connection.refresh_from_db()
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
