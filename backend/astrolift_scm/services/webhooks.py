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


# Mirror the priority used elsewhere in this app's CI plumbing
# (``services/workflows.py``): App-install first (no silent expiry,
# scoped per-install), then OAuth-user, then PAT. Picking the same
# winner keeps the manifest read, the workflow dispatch, and the
# webhook all pinned to one identity per app.
_KIND_PREFERENCE: dict[str, tuple[str, ...]] = {
    "github": (
        "github_app_install",
        "github_oauth_user",
        "github_pat",
    ),
}


def _pick_source_connection(app: RegisteredApp) -> SourceConnection | None:
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

    if app.source_kind in {"gitlab", "bitbucket", "gitea", "git_url"}:
        raise NotImplementedError(
            "Source-webhook install is GitHub-only for now; "
            "use the per-connection install_scm_webhook for other hosts."
        )
    if app.source_kind != "github":
        return InstallSourceWebhookResult(
            status="no_connection",
            error=f"unsupported source_kind {app.source_kind!r}",
        )

    connection = _pick_source_connection(app)
    if connection is None:
        return InstallSourceWebhookResult(
            status="no_connection",
            error=(
                "no active source connection for this app's org. " "Connect a GitHub identity, then retry."
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
        if exc.code == "APP_INSTALLED":
            # GitHub-App connections deliver via the App's own
            # webhook — no per-repo install is needed. Persist a
            # marker timestamp so the UI flips green and leave the
            # hook id empty; the receiver routes by App installation.
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
