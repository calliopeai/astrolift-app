"""One-click source-webhook install for a RegisteredApp (#385).

Registers (or refreshes) the push-event webhook on the configured
source repo so the platform's receiver hears pushes for an app. Sits
on top of the per-connection ``install_scm_webhook`` primitive but
narrows the surface to a single per-app action: the Settings page's
"Install webhook" button doesn't want to ask the operator to pick a
connection — it picks the highest-ranked active one in the app's
org and runs.

Idempotency:
* GitHub returns 422 with "Hook already exists" when the same URL is
  registered. We treat that as ``status="refreshed"``: we rotate the
  shared HMAC secret + persist a fresh ``installed_at`` so the FE
  reflects the operator's confirmation, without spawning a duplicate
  hook on the host. The hook id stays whatever the previous run
  recorded (we don't bother re-fetching from the host).
* Re-running after a successful first install simply re-creates the
  hook — GitHub keeps the URL unique and 422s; the refresh branch
  handles that path.

GitLab / Bitbucket are gated as ``NotImplementedError`` at the
dispatcher so the resolver can map the gap to a clean PRECONDITION.
"""

from __future__ import annotations

import dataclasses
import secrets

from django.conf import settings as django_settings
from django.db import transaction
from django.utils import timezone

from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import ProviderError, install_webhook
from core.secrets import encrypt_at_rest

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class InstallSourceWebhookResult:
    """Outcome of installing or refreshing an app's source-webhook.

    ``status`` codes:
    * ``created``: a brand-new hook landed on the host; ``hook_id``
      is the host-side identifier.
    * ``refreshed``: a hook with the same URL already existed; we
      rotated the HMAC secret + advanced ``installed_at``. ``hook_id``
      is whatever the app row carried before (the host's id stays
      the same).
    * ``app_delivers``: the connection is a github_app_install and the
      org App is installed on the target repo — its own webhook already
      delivers pushes, so no per-repo hook exists (``hook_id`` is empty
      by design, and the empty value is honest, not a masked hook). The
      status flips the UI green and records ``installed_at``.
    * ``not_installed``: the connection is a github_app_install but the
      App is NOT installed on the target repo, so nothing delivers
      pushes. A real precondition failure — the resolver maps it to
      PRECONDITION so the operator installs the App on that repo. We do
      NOT record ``installed_at`` (the app is not wired).
    * ``no_connection``: no active SourceConnection in the app's org
      for the app's source kind. The resolver maps this to a clean
      PRECONDITION so the FE can prompt to connect.
    * ``fetch_failed``: the host rejected the call (auth, network,
      generic 5xx). ``error`` carries the host's message.

    ``receiver_url`` is the canonical platform URL the host POSTs
    deliveries to — surfaced so the operator can paste it elsewhere
    if a manual install is needed.
    """

    status: str
    hook_id: str = ""
    receiver_url: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Connection picker
# ---------------------------------------------------------------------------
#
# DOCUMENTED EXCEPTION to the "platform writes are App-only" rule.
#
# Unlike the workflow-dispatch / manifest-read pickers (which resolve
# the org GitHub App via ``connection_resolver`` and nothing else), this
# picker keeps the App-first-then-OAuth-user-then-PAT preference. It has
# to: it resolves the connection that OWNS a repo's webhook, and the
# webhook secret lifecycle is a closed loop over one connection —
#
#   install  → mints + persists ``webhook_secret_*`` on the picked row
#   ingest   → webhook_views re-picks the same row to HMAC-verify a
#              delivery (astrolift_scm/webhook_views.py)
#   teardown → app_deregister re-picks it to delete the hook
#
# For a per-repo hook installed through a PAT / OAuth-user connection,
# the secret lives on THAT row. Forcing App-only here would strand every
# such hook: inbound deliveries would fail signature verification (401)
# and teardown couldn't find the hook to delete. App-delivered webhooks
# (github_app_install) are a separate mechanism owned elsewhere; this
# picker still prefers the App when one exists, so new installs pin to it.
_KIND_PREFERENCE: dict[str, tuple[str, ...]] = {
    "github": (
        "github_app_install",
        "github_oauth_user",
        "github_pat",
    ),
    "gitlab": (
        "gitlab_oauth_user",
        "gitlab_pat",
    ),
}


def _pick_source_connection(app: RegisteredApp) -> SourceConnection | None:
    """Resolve the connection that owns ``app``'s webhook (see the
    exception note above). App-first, then OAuth-user, then PAT; None
    when no usable row exists, which every caller maps to its own
    ``no_connection`` / 401 response."""
    accepted = _KIND_PREFERENCE.get(app.source_kind, ())
    if not accepted:
        return None
    rows = list(
        SourceConnection.objects.filter(
            organization_id=app.organization_id,
            kind__in=accepted,
            is_active=True,
            is_orphaned=False,
            deleted_at__isnull=True,
        )
    )
    if not rows:
        return None
    rank = {k: i for i, k in enumerate(accepted)}
    rows.sort(key=lambda r: (rank.get(r.kind, len(accepted)), r.pk))
    return rows[0]


# ---------------------------------------------------------------------------
# Receiver URL
# ---------------------------------------------------------------------------


# Per-source receiver paths mirror the documented routes in
# ``astrolift_scm/webhook_ingress.py``. The ``{app_guid}`` suffix
# scopes the delivery to a specific RegisteredApp so the receiver can
# dispatch without grepping the payload — keeps app-routing trivial
# when multiple apps share a source connection.
_RECEIVER_PATH_FOR_KIND: dict[str, str] = {
    "github": "/api/webhooks/github/{app_guid}/",
    "gitlab": "/api/webhooks/gitlab/{app_guid}/",
    "bitbucket": "/api/webhooks/bitbucket/{app_guid}/",
    "gitea": "/api/webhooks/gitea/{app_guid}/",
}


def _receiver_url(app: RegisteredApp) -> str:
    base = (getattr(django_settings, "PLATFORM_API_URL", "") or "").rstrip("/")
    path_template = _RECEIVER_PATH_FOR_KIND.get(app.source_kind, "")
    if not path_template:
        return ""
    return f"{base}{path_template.format(app_guid=app.guid)}"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def install_astrolift_source_webhook(app: RegisteredApp) -> InstallSourceWebhookResult:
    """Register (or refresh) the push-event webhook on ``app``'s source repo.

    GitHub-first. GitLab and the other source kinds raise
    ``NotImplementedError`` at the dispatcher so the resolver maps
    them to a clean PRECONDITION envelope rather than a 500.

    Mints a fresh HMAC secret on each call, encrypts it via
    ``core.secrets.encrypt_at_rest``, and persists it on the picked
    connection's existing ``webhook_secret_*`` columns. The secret
    is shared at the connection level (not per-hook) so a single
    receiver-side verifier can authenticate every delivery the
    connection drives.
    """
    if not app.source_repo:
        return InstallSourceWebhookResult(
            status="no_connection",
            error="app has no source repo configured",
        )

    if app.source_kind in {"bitbucket", "gitea", "git_url"}:
        raise NotImplementedError(
            f"Source-webhook install is not supported for source_kind={app.source_kind!r} yet; "
            "see the per-host follow-ups for the missing driver work."
        )
    if app.source_kind not in {"github", "gitlab"}:
        return InstallSourceWebhookResult(
            status="no_connection",
            error=f"unsupported source_kind {app.source_kind!r}",
        )

    connection = _pick_source_connection(app)
    if connection is None:
        host_label = "GitLab" if app.source_kind == "gitlab" else "GitHub"
        return InstallSourceWebhookResult(
            status="no_connection",
            error=(
                f"no active source connection for this app's org. Connect a {host_label} identity, then retry."
            ),
        )

    target_url = _receiver_url(app)
    secret = secrets.token_urlsafe(32)

    try:
        result = install_webhook(
            connection,
            repo_full_name=app.source_repo,
            target_url=target_url,
            secret=secret,
        )
    except ProviderError as exc:
        if exc.code == "ALREADY_EXISTS":
            # Refresh branch: GitHub already has a hook for this URL.
            # Rotate the shared secret + advance the timestamp so the
            # operator's "click to refresh" is observable in the UI.
            # The hook id stays whatever was previously recorded; we
            # never minted a new one, so the host-side id hasn't
            # moved.
            _persist_secret(connection, secret)
            now = timezone.now()
            with transaction.atomic():
                app.source_webhook_installed_at = now
                app.save(
                    update_fields=[
                        "source_webhook_installed_at",
                        "updated_at",
                        "version",
                    ]
                )
            return InstallSourceWebhookResult(
                status="refreshed",
                hook_id=app.source_webhook_id or "",
                receiver_url=target_url,
            )
        if exc.code == "APP_DELIVERS":
            # github_app_install and the App IS installed on this repo:
            # its own org-level webhook already delivers pushes. There is
            # no per-repo hook, so we DON'T fabricate a hook_id — the
            # empty value is the truth. Persist the marker timestamp so
            # the UI flips green; the receiver routes by App installation.
            now = timezone.now()
            with transaction.atomic():
                app.source_webhook_installed_at = now
                app.save(
                    update_fields=[
                        "source_webhook_installed_at",
                        "updated_at",
                        "version",
                    ]
                )
            return InstallSourceWebhookResult(
                status="app_delivers",
                hook_id="",
                receiver_url=target_url,
            )
        if exc.code == "APP_NOT_INSTALLED":
            # github_app_install but the App is NOT on this repo: nothing
            # delivers pushes. Report the truth as a precondition failure
            # and DON'T advance installed_at — the app isn't wired.
            return InstallSourceWebhookResult(
                status="not_installed",
                receiver_url=target_url,
                error=exc.message,
            )
        return InstallSourceWebhookResult(
            status="fetch_failed",
            receiver_url=target_url,
            error=exc.message,
        )

    _persist_secret(connection, secret)
    now = timezone.now()
    with transaction.atomic():
        app.source_webhook_id = result.hook_id
        app.source_webhook_installed_at = now
        app.save(
            update_fields=[
                "source_webhook_id",
                "source_webhook_installed_at",
                "updated_at",
                "version",
            ]
        )
    return InstallSourceWebhookResult(
        status="created",
        hook_id=result.hook_id,
        receiver_url=target_url,
    )


def _persist_secret(connection: SourceConnection, plaintext: str) -> None:
    """Encrypt + persist the HMAC secret on the connection.

    The receiver-side verifier reads from
    ``SourceConnection.webhook_secret_*`` (the column pair already
    used by the per-connection ``install_scm_webhook`` flow) so there
    is exactly one place delivery signatures are validated against.
    """
    encrypted = encrypt_at_rest(plaintext.encode("utf-8"))
    connection.webhook_secret_backend_kind = encrypted.backend_kind
    connection.webhook_secret_ciphertext = encrypted.backend_ref
    connection.save(
        update_fields=[
            "webhook_secret_backend_kind",
            "webhook_secret_ciphertext",
            "updated_at",
            "version",
        ]
    )
