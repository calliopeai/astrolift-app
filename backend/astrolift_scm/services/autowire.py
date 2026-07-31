"""Autowire orchestration: chain the repo-wiring steps after register (#1108).

Registration promises an app goes from "created" to "auto-deploys on push"
with zero extra clicks: the platform pushes the CI workflow, installs the
source webhook, and pushes the deploy-token secret. Before this module the
three steps existed only as separate explicit mutations, so an app registered
without running them landed half-wired — no ``.github/workflows`` file, a
phantom webhook (``installed_at`` set, ``id`` empty), no deploy-token secret —
with no signal, and a git push did nothing. ``run_autowire`` is the single
entry point the register path and the standalone retry mutation both call.

Resilience is the whole point. A failure in one step must NOT roll back the
registration or abort the other steps: each step is isolated, its outcome
recorded, and the chain continues. The per-step verdicts are persisted on
``RegisteredApp.autowire_state`` so the app detail page can surface exactly
which step needs attention.

Idempotency comes from the underlying services (workflow sync no-ops when the
file already matches; webhook install refreshes rather than duplicating; the
phantom reconcile clears a stale marker before install so a re-run can't leave
two markers) — so re-running against an already-wired app is safe.

Identity: every write runs under an ORG-level connection resolved by purpose
(webhook / workflow → ORG_REPO_WRITE; secrets → PLATFORM_REPO_WRITE, i.e. the
org GitHub App) — never the requesting viewer's personal token. ``actor`` is
carried only for out-of-band attribution (who triggered the run).
"""

from __future__ import annotations

import dataclasses
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from astrolift_registry.models import RegisteredApp
from astrolift_scm.services.connection_resolver import (
    ORG_REPO_WRITE,
    PLATFORM_REPO_WRITE,
    ConnectionPurpose,
    ConnectionResolutionError,
    resolve_connection,
)

logger = logging.getLogger(__name__)

# Per-step status tokens, surfaced verbatim on the GraphQL autowire field.
OK = "ok"
ERROR = "error"
MISSING = "missing"

# The steps the chain runs, in order. Kept as a constant so the read-path
# derivation and the persisted keys can't drift from the orchestrator.
STEPS = ("ci_workflow", "webhook", "secrets")


@dataclasses.dataclass(frozen=True, slots=True)
class AutowireOutcome:
    """Result of one autowire run.

    ``connected`` is False for the "registered but no org connection —
    connect for auto-deploy" state; in that case ``steps`` is empty (nothing
    ran). Otherwise ``steps`` carries one ``OK`` / ``ERROR`` per step and
    ``errors`` carries a message for each failed step.
    """

    connected: bool
    steps: dict[str, str]
    errors: dict[str, str]

    @property
    def all_ok(self) -> bool:
        return self.connected and bool(self.steps) and all(v == OK for v in self.steps.values())


def _msg(exc: Exception) -> str:
    return getattr(exc, "message", None) or str(exc) or exc.__class__.__name__


def _has_connection(app: RegisteredApp, purpose: ConnectionPurpose) -> bool:
    """DB-only check that a connection satisfying ``purpose`` exists.

    Never hits the network — ``resolve_connection`` is a pure ORM lookup —
    so it's safe to call on the read path as well as before each write.
    """
    try:
        resolve_connection(app.organization_id, purpose=purpose, source_kind=app.source_kind)
        return True
    except ConnectionResolutionError:
        return False


def run_autowire(app: RegisteredApp, *, actor=None) -> AutowireOutcome:
    """Chain CI-workflow → webhook → secrets for ``app`` and persist the
    outcome. Never raises for a step failure — records it and moves on.
    """
    # No source repo → nothing to wire. Not an error, just unwired.
    if not (app.source_repo or "").strip():
        return _persist(app, AutowireOutcome(connected=False, steps={}, errors={}))

    # Gate: is there an org-level connection that can do repo ops at all?
    # If not, this is the "connect for auto-deploy" state — the app is
    # registered and deployable by hand; we don't fail, we just record it.
    if not _has_connection(app, ORG_REPO_WRITE):
        return _persist(app, AutowireOutcome(connected=False, steps={}, errors={}))

    steps: dict[str, str] = {}
    errors: dict[str, str] = {}

    for step, (status, message) in (
        ("ci_workflow", _step_workflow(app, actor=actor)),
        ("webhook", _step_webhook(app)),
        ("secrets", _step_secrets(app, actor=actor)),
    ):
        steps[step] = status
        if message:
            errors[step] = message

    return _persist(app, AutowireOutcome(connected=True, steps=steps, errors=errors))


def _step_workflow(app: RegisteredApp, *, actor) -> tuple[str, str]:
    from astrolift_scm.services.workflow_sync import (
        WorkflowSyncError,
        sync_workflow_file_to_repo,
    )

    # ``sync_workflow_file_to_repo`` provisions + persists the OIDC push
    # role before rendering (#1219), so every caller — this step, the
    # Settings sync button, drift repair — heals a blank ``push_role_ref``.

    try:
        result = sync_workflow_file_to_repo(app, viewer_user=actor)
    except (WorkflowSyncError, NotImplementedError) as exc:
        return ERROR, _msg(exc)
    except Exception as exc:  # noqa: BLE001 — a step failure must not abort the chain
        logger.exception("autowire: CI-workflow step failed for %s", app.slug)
        return ERROR, _msg(exc)
    if result.status == "fetch_failed":
        return ERROR, result.error or "couldn't sync CI workflow to repo"
    # created / updated / in_sync / pr_opened all mean the file is wired.
    return OK, ""


def _step_webhook(app: RegisteredApp) -> tuple[str, str]:
    from astrolift_scm.services.webhooks import (
        install_astrolift_source_webhook,
        reconcile_source_webhook_state,
    )

    # Repair a phantom marker BEFORE installing so the install actually
    # re-does the work (an uncleared phantom would otherwise 422→refresh
    # and keep the empty id). Best-effort: if reconcile itself errors, the
    # install below still runs and lands truthful state.
    try:
        reconcile_source_webhook_state(app)
    except Exception:  # noqa: BLE001
        logger.exception("autowire: webhook reconcile failed for %s", app.slug)

    try:
        result = install_astrolift_source_webhook(app)
    except NotImplementedError as exc:
        return ERROR, _msg(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("autowire: webhook install step failed for %s", app.slug)
        return ERROR, _msg(exc)
    if result.status in ("created", "refreshed", "app_delivers"):
        return OK, ""
    # no_connection / not_installed / fetch_failed — nothing delivers pushes.
    return ERROR, result.error or f"webhook install returned {result.status!r}"


def _step_secrets(app: RegisteredApp, *, actor) -> tuple[str, str]:
    from astrolift_scm.services.secrets import (
        PushSecretsError,
        push_astrolift_ci_secrets,
        validate_astrolift_ci_secrets,
    )

    # Secrets are the one step that needs the org GitHub App
    # (PLATFORM_REPO_WRITE) — a user OAuth/PAT can't push Actions secrets. An
    # org that wired webhook+workflow through an OAuth/PAT connection but
    # never installed the App gets a clear secrets error, not a crash.
    if not _has_connection(app, PLATFORM_REPO_WRITE):
        return ERROR, "CI secrets need the org GitHub App installed; an org OAuth/PAT can't push them."

    platform_api_url = (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")
    try:
        result = push_astrolift_ci_secrets(
            app,
            viewer_user=actor,
            platform_api_url=platform_api_url,
        )
    except (PushSecretsError, NotImplementedError) as exc:
        return ERROR, _msg(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("autowire: CI-secrets step failed for %s", app.slug)
        return ERROR, _msg(exc)
    if not result.ok:
        return ERROR, result.error_message or "couldn't push CI secrets"

    # A successful push (every PUT 2xx) is authoritative that the secrets
    # landed. We still run the read-only validate as a safety net, but it can
    # only DOWNGRADE on an unambiguous "nothing registered" signal — never on
    # a transient/auth error — so eventual-consistency can't produce a false
    # failure right after a good push.
    try:
        v = validate_astrolift_ci_secrets(app, viewer_user=actor)
    except Exception:  # noqa: BLE001 — validate is a best-effort confirmation
        logger.exception("autowire: CI-secrets validate probe failed for %s", app.slug)
        return OK, ""
    if v.ok and v.results and all(not r.is_set for r in v.results):
        return ERROR, "pushed CI secrets but the repo reports none are set — check repo permissions."
    return OK, ""


def _persist(app: RegisteredApp, outcome: AutowireOutcome) -> AutowireOutcome:
    """Write the outcome to ``app.autowire_state`` and return it unchanged."""
    state: dict = {
        "connected": outcome.connected,
        "checked_at": timezone.now().isoformat(),
    }
    state.update(outcome.steps)
    if outcome.errors:
        state["errors"] = outcome.errors
    app.autowire_state = state
    with transaction.atomic():
        app.save(update_fields=["autowire_state", "updated_at", "version"])
    return outcome
