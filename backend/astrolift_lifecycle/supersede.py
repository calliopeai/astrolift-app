"""Supersede in-flight deployments when a newer one starts (#1536).

A new deploy to the same (app, environment) used to stack on top of any
in-flight one: the Temporal workflow id collides (single-flight per
(app, env)), and the older Deployment row sat at PENDING/DEPLOYING
forever. Both deploy entry points (the CI notify endpoint and the
deployAstroliftApp mutation) call :func:`supersede_in_flight_deploys`
just before creating the new row.

Mechanics:

* Signal ``abort`` on the deterministic workflow id. A live
  DeployAppWorkflow observes the flag at its abort boundary and marks
  its own deployment FAILED (the abort-marks-failed patch), so its
  bookkeeping is exact.
* Rows whose workflow is already dead (worker restart, terminated run)
  can't self-mark, so they are failed out directly. When a live
  workflow was signalled, the newest in-flight row is left for it to
  self-mark — failing it here would race the workflow's own
  transitions.

Default behavior is supersede; per-app ``[deploy] on_new_deploy``
modes (queue / reject) are the follow-up on #1536.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SUPERSEDE_REASON = "superseded by a newer deploy"

# The reason a live DeployAppWorkflow writes on itself when it observes the
# abort signal (deploy_app.py, reprovision_app.py). Every other abort
# initiator (reject, abort mutation) writes its own reason first, and
# ``mark_failed`` keeps a reason already written, so a row carrying this text
# was stopped by a newer deploy.
_WORKFLOW_ABORT_REASON = "aborted: superseded by a newer deploy or cancelled"

# A superseded in-flight row is FAILED in the state machine (no in-flight
# status may move to SUPERSEDED), so readers classify it by this reason.
SUPERSEDED_REASONS = (_SUPERSEDE_REASON, _WORKFLOW_ABORT_REASON)

# In-flight = created but not yet terminal. REDEPLOYING is in-flight
# too (a redeploy that stalls blocks the next deploy the same way).
_IN_FLIGHT_STATUSES = ("pending", "deploying", "redeploying")


def is_superseded_failure(status: str, aborted_reason: str) -> bool:
    """True when a FAILED row was stopped by a newer deploy, not broken."""
    return status == "failed" and (aborted_reason or "").strip() in SUPERSEDED_REASONS


def display_status(status: str, aborted_reason: str) -> str:
    """The status a reader should see: a superseded failure reads ``superseded``."""
    return "superseded" if is_superseded_failure(status, aborted_reason) else status


def superseded_failure_q():
    """``Q`` matching FAILED rows that :func:`is_superseded_failure` reclassifies."""
    from django.db.models import Q

    return Q(status="failed", aborted_reason__in=SUPERSEDED_REASONS)


def supersede_in_flight_deploys(app, env, *, reason: str = _SUPERSEDE_REASON) -> int:
    """Abort + fail out in-flight deployments for ``(app, env)``.

    Returns the number of rows transitioned directly (not counting the
    live workflow's row, which self-marks on the abort signal). Never
    raises — a supersede failure must not block the new deploy.
    """
    from astrolift_lifecycle.models import Deployment
    from astrolift_workflows.client import signal_workflow

    wf_id = f"DeployAppWorkflow-{app.guid}-{env.guid}"
    try:
        live_signalled = signal_workflow(wf_id, "abort")
    except Exception:  # noqa: BLE001 — best-effort; the new deploy proceeds
        logger.warning("supersede: abort signal errored for %s", wf_id, exc_info=True)
        live_signalled = False

    stale = list(
        Deployment.objects.filter(
            registered_app=app,
            app_environment=env,
            status__in=_IN_FLIGHT_STATUSES,
            deleted_at__isnull=True,
        ).order_by("-created_at"),
    )
    if live_signalled and stale:
        # The newest in-flight row belongs to the live workflow we just
        # signalled; it marks itself FAILED with its own reason.
        stale = stale[1:]

    failed_out = 0
    for d in stale:
        try:
            if not d.aborted_reason:
                d.aborted_reason = reason
                d.save(update_fields=["aborted_reason", "updated_at", "version"])
            d.transition_to(Deployment.Status.FAILED)
            failed_out += 1
        except Exception:  # noqa: BLE001
            logger.warning("supersede: could not fail out deployment %s", d.pk, exc_info=True)
    if live_signalled or failed_out:
        logger.info(
            "supersede: app=%s env=%s signalled_live=%s failed_out=%d",
            app.slug,
            env.name,
            live_signalled,
            failed_out,
        )
    return failed_out
