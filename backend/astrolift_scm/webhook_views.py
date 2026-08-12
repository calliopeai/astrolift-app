"""GitHub PR webhook receiver — preview-env dispatch (#778, spec 27 §07 §14).

POST ``/api/webhooks/github/<app_guid>/``

Mounted at the project root from ``astrolift_scm.urls``. GitHub posts here
on ``pull_request`` events for any RegisteredApp whose source-host webhook
the platform installed via ``installAstroliftSourceWebhook`` (#385). The
receiver:

1. Resolves the app by its public guid (the per-app routing key embedded
   in the receiver URL when the webhook was installed).
2. Picks the canonical SourceConnection for the app's org using the same
   rank-by-kind logic ``services.webhooks._pick_source_connection``
   already uses — keeps the manifest read, the workflow dispatch, and
   the receiver pinned to one identity per app.
3. Decrypts the shared HMAC secret stored on the picked connection
   (``webhook_secret_backend_kind`` + ``webhook_secret_ciphertext``)
   and verifies the request's ``X-Hub-Signature-256`` via
   ``webhook_ingress.verify_signature``. The name is load-bearing: the
   ``test_webhook_signature_guard`` CI test (#529) AST-scans every
   ``/.../webhook/...`` view for one of the canonical verifier names.
4. Handles ``push`` and ``pull_request`` events. Both fan out to any
   WorkflowWebhook rows bound to the resolved RegisteredApp (the SCM →
   workflow trigger bridge); ``pull_request`` additionally drives the
   preview lifecycle below. Every other event header (``ping``,
   ``installation``, …) returns 200 with a ``not handled`` reason so
   GitHub stops retrying.
5. Projects the payload into the dispatcher's
   ``PrEventContext`` + ``AppPreviewContext`` and runs
   ``github_pr_dispatch.decide_dispatch`` — pure policy that classifies
   the action (opened/reopened/synchronize → BUILD_PREVIEW; closed
   merged/unmerged → TEARDOWN_PREVIEW; everything else IGNORE) and
   returns a deterministic Temporal workflow id so duplicate deliveries
   join the in-flight run rather than racing a parallel one.
6. For BUILD_PREVIEW: get-or-create the PreviewEnvironment row keyed on
   ``(registered_app, pr_number, is_manual=False)``, then start
   ``BuildPreviewWorkflow``. The row is the activity layer's
   source-of-truth — ``provision_preview_namespace`` reads
   ``namespace`` + ``app_environment.tenant_cluster`` directly off it,
   so the receiver must populate those fields at creation time.
7. For TEARDOWN_PREVIEW: look up the active preview for ``(app,
   pr_number)``; start ``TearDownPreviewWorkflow`` if one exists. A
   PR closed without an extant preview returns 200 — no work to do.

Wire-protocol contract
----------------------
* 401 on auth failures (no secret on connection, bad signature) — both
  return the same generic ``invalid signature`` body so an attacker
  probing the surface can't tell which check fired.
* 404 when the app guid doesn't resolve to a live RegisteredApp.
* 400 on malformed JSON.
* 200 for every other outcome — including ignored events, ignored
  actions, and the "PR closed but no preview existed" case. GitHub
  retries any non-2xx response; staying in the 2xx band keeps the
  retry queue empty on the host side.

The view returns synchronously and starts the workflow asynchronously
(via the sync Temporal client wrapper) so the receiver stays under the
spec's 1-second response budget even when the workflow does heavy
namespace + deploy work downstream.
"""

from __future__ import annotations

import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from astrolift_scm import github_pr_dispatch, webhook_ingress
from astrolift_scm.webhook_ingress import WebhookRejected, WebhookSource

logger = logging.getLogger(__name__)


def _decrypt_webhook_secret(connection) -> bytes | None:
    """Decrypt the shared HMAC secret persisted on a SourceConnection.

    Returns the plaintext bytes, or None when the connection carries no
    secret (operator hasn't installed the webhook yet, or rotated and
    cleared the columns).
    """
    from core.secrets import EncryptedSecret, decrypt

    if not connection.webhook_secret_backend_kind or not connection.webhook_secret_ciphertext:
        return None
    return decrypt(
        EncryptedSecret(
            backend_kind=connection.webhook_secret_backend_kind,
            backend_ref=bytes(connection.webhook_secret_ciphertext),
        )
    )


def _build_pr_context(payload: dict) -> github_pr_dispatch.PrEventContext | None:
    """Project a GitHub pull_request webhook payload into the dispatcher's
    PrEventContext. Returns None when required fields are missing — the
    receiver maps that to a 200 ack so GitHub stops retrying instead of
    burning retry budget on a malformed delivery."""
    repository = payload.get("repository") or {}
    pull_request = payload.get("pull_request") or {}
    head = pull_request.get("head") or {}
    user = pull_request.get("user") or {}

    raw_action = payload.get("action") or ""
    repo_full_name = repository.get("full_name") or ""
    pr_number_raw = pull_request.get("number")
    head_sha = head.get("sha") or ""
    head_branch = head.get("ref") or ""

    if not raw_action or not repo_full_name or not isinstance(pr_number_raw, int):
        return None

    return github_pr_dispatch.PrEventContext(
        raw_action=raw_action,
        repo_full_name=repo_full_name,
        pr_number=int(pr_number_raw),
        head_sha=head_sha,
        head_branch=head_branch,
        is_merge=bool(pull_request.get("merged", False)) is True,
        is_bot_author=(user.get("type") or "") == "Bot",
    )


def _ensure_preview_environment(app, pr_ctx: github_pr_dispatch.PrEventContext):
    """Return (preview, created) for the (app, pr_number) auto path.

    Idempotent: a repeat opened/synchronize delivery for the same PR
    reuses the existing row; only the head_sha tracking on the
    BuildPreviewWorkflow side advances. Mirrors the
    ``create_preview_environment`` mutation's shape — an AppEnvironment
    is spun up alongside the preview because activities read the
    bound tenant_cluster off ``preview.app_environment``.
    """
    from django.db import transaction

    from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment
    from astrolift_workflows.preview_build import (
        env_slug_for_preview,
        namespace_for_preview,
    )

    existing = (
        PreviewEnvironment.objects.select_related("app_environment", "registered_app")
        .filter(
            registered_app=app,
            pr_number=pr_ctx.pr_number,
            is_manual=False,
            deleted_at__isnull=True,
        )
        .first()
    )
    if existing is not None:
        return existing, False

    cluster = app.default_tenant_cluster
    if cluster is None:
        # Auto-PR path requires a bound tenant cluster — the activity
        # layer can't provision a namespace without one. Surfacing the
        # gap as a 200-with-reason matches the "ignored: no cluster"
        # branch the FE renders when previews are misconfigured.
        return None, False

    org = app.organization
    org_slug = (getattr(org, "slug", "") or getattr(org, "name", "") or "org").lower()
    app_slug = app.slug

    namespace = namespace_for_preview(
        org_slug=org_slug,
        app_slug=app_slug,
        pr_number=pr_ctx.pr_number,
    )
    # Hostname matches the manual-preview convention
    # (``preview-<slug>.<app>.<org>``) so audits group both paths under
    # the same shape. The full FQDN with the install's base zone is
    # resolved by the BuildPreviewWorkflow at apply time from the
    # cluster's ingress config; the row stores the stable per-PR label.
    from astrolift_clusters.models import resolve_managed_domain

    _managed_domain = resolve_managed_domain(org, for_preview=True)
    # Stable per-PR label: ``pr-<n>.<app>.<org>``. When the install has a
    # managed DNS zone we append it to form the full FQDN; without one we
    # stop at the org slug rather than repeating it (the old fallback
    # ``base_zone = ... or org_slug`` produced ``…<org>.<org>``).
    base = f"pr-{pr_ctx.pr_number}.{app_slug}.{org_slug}"
    zone = getattr(_managed_domain, "zone", None)
    hostname = (f"{base}.{zone}" if zone else base).lower()
    env_name = env_slug_for_preview(pr_number=pr_ctx.pr_number)

    with transaction.atomic():
        env = AppEnvironment.objects.create(
            registered_app=app,
            tenant_cluster=cluster,
            name=env_name,
            url=f"https://{hostname}",
            managed_domain=_managed_domain,
            required_approvals=0,
        )
        preview = PreviewEnvironment.objects.create(
            registered_app=app,
            pr_number=pr_ctx.pr_number,
            branch=pr_ctx.head_branch,
            is_manual=False,
            commit_sha=pr_ctx.head_sha,
            status=PreviewEnvironment.Status.BUILDING,
            hostname=hostname,
            namespace=namespace,
            app_environment=env,
        )
    return preview, True


def _dispatch_workflow_webhooks(event_kind: str, app, payload: dict) -> int:
    """Fire every enabled WorkflowWebhook bound to ``app`` for an SCM
    push / pull_request delivery.

    The signature has already been verified by the caller, so the payload
    is trusted by the time we get here. Each matching WorkflowWebhook
    launches one WorkflowInstance via the shared
    ``trigger_workflow_instance`` entry point; the whole raw payload is
    handed through as the instance input (the workflow definition's own
    input schema does the field projection downstream).

    Routing failures are swallowed per-webhook: a misconfigured workflow
    trigger must never turn a 200 SCM ack into a non-2xx that makes the
    host retry the delivery. We return the count of webhooks we attempted
    to dispatch so the caller can surface it in the ack body for
    observability.

    Agent-bound webhooks (spec 33, PR-6): when a fired webhook targets an
    ``agent_definition`` Workload instead of a WorkflowDefinition, it
    dispatches an AgentTask through the PR-1 path (with ``input_mapping``
    applied) rather than launching a WorkflowInstance. A paused / torn-down
    agent is a no-op and is not counted as dispatched.
    """
    from astrolift_agents.models import WorkflowWebhook
    from astrolift_agents.services.workflow_triggers import (
        dispatch_agent_task_from_webhook,
        trigger_workflow_instance,
    )

    webhooks = WorkflowWebhook.objects.filter(
        registered_app=app,
        enabled=True,
    ).select_related("workflow_definition", "agent_definition__registered_app")

    dispatched = 0
    for wh in webhooks:
        try:
            if wh.agent_definition_id is not None:
                # Agent-bound: dispatch a Task, not a WorkflowInstance.
                task = dispatch_agent_task_from_webhook(wh, payload)
                if task is None:
                    # Paused / torn-down agent — not a fire; don't count it.
                    continue
            else:
                trigger_workflow_instance(
                    wh.workflow_definition,
                    payload,
                    trigger_kind=f"scm_{event_kind}",
                )
        except Exception:
            # Never let one bad workflow trigger break SCM ingest, and
            # never let it stop the sibling webhooks from firing.
            logger.exception(
                "workflow webhook dispatch failed: webhook=%s event=%s app=%s",
                wh.slug,
                event_kind,
                app.pk,
            )
            continue
        dispatched += 1
    return dispatched


def _sync_workflow_dsl(app, connection, payload: dict) -> None:
    """Best-effort sync of in-repo WorkflowDefinitions from a push event.

    Reads the pushed ``after`` ref from the GitHub push payload and
    calls ``workflows.services.workflow_sync.sync_workflows_from_repo``.
    All exceptions are caught and logged; this must never raise or block
    the webhook ack.
    """
    try:
        from workflows.services.workflow_sync import sync_workflows_from_repo

        ref = payload.get("after") or payload.get("ref") or ""
        if not ref:
            return
        result = sync_workflows_from_repo(app, connection, ref)
        if result["errors"]:
            logger.warning(
                "_sync_workflow_dsl: errors for %s@%s: %s",
                getattr(app, "source_repo", "?"),
                ref,
                result["errors"],
            )
        else:
            logger.debug(
                "_sync_workflow_dsl: %s created=%d updated=%d",
                getattr(app, "source_repo", "?"),
                result["created"],
                result["updated"],
            )
    except Exception:
        logger.exception(
            "_sync_workflow_dsl: unexpected error for app=%s",
            getattr(app, "pk", "?"),
        )


def _reconcile_ci_workflow(app, payload: dict) -> None:
    """Observe-only drift reconcile of the managed CI workflow file (#1210).

    Reads the pushed branch + SHA off the GitHub push payload and hands them
    to ``ci_workflow_drift.reconcile_ci_workflow_on_push``, which fetches the
    managed file at that SHA (deploy branch only), classifies drift, and
    flags the result onto the app. FLAG ONLY — it never writes to the repo.

    All exceptions are swallowed: a drift check must never turn a 200 SCM ack
    into a retry-triggering non-2xx, nor block the workflow-trigger fan-out.
    """
    try:
        from astrolift_scm.services.ci_workflow_drift import (
            reconcile_ci_workflow_on_push,
        )

        # GitHub push: ``ref`` is ``refs/heads/<branch>``; ``after`` is the
        # new tip SHA. Branch-gating + the no-SHA fallback live in the
        # reconcile helper so this receiver stays a thin projection.
        ref = payload.get("ref") or ""
        branch = ref.split("refs/heads/", 1)[-1] if ref.startswith("refs/heads/") else ""
        pushed_sha = payload.get("after") or ""
        reconcile_ci_workflow_on_push(app, branch=branch, pushed_sha=pushed_sha)
    except Exception:
        logger.exception(
            "_reconcile_ci_workflow: unexpected error for app=%s",
            getattr(app, "pk", "?"),
        )


def _sync_agent_package_on_push(app, payload: dict, *, connection=None) -> dict:
    """Reconcile an independent member or its explicitly opted-in bundle.

    The default remains the exact registered manifest. A federation may set
    ``auto_register_new=true``; only its deterministic anchor member then
    performs a whole-bundle scan. This prevents every member webhook from
    repeating the same scan and prevents an ordinary monorepo push from
    registering unrelated sibling code.
    """
    from django.db import transaction

    from astrolift_registry.models import RegisteredApp, Workload

    workload = (
        app.workloads.filter(kind=Workload.Kind.AGENT, deleted_at__isnull=True)
        .select_related("brief")
        .first()
    )
    if workload is None:
        return {"status": "not_agent"}
    ref_name = str(payload.get("ref") or "")
    branch = ref_name.removeprefix("refs/heads/") if ref_name.startswith("refs/heads/") else ""
    deploy_branch = app.deploy_branch or app.default_branch or "main"
    if branch != deploy_branch:
        return {"status": "ignored_branch", "branch": branch, "deploy_branch": deploy_branch}
    commit_sha = str(payload.get("after") or "").strip()
    if not commit_sha or set(commit_sha) == {"0"}:
        return {"status": "ignored_ref"}
    if app.project_id is None:
        return {"status": "error", "error": "agent has no project binding"}

    try:
        from astrolift_registry.services.manifest_sync import register_agent_repo
        from astrolift_scm.providers import fetch_branch_head
        from astrolift_scm.services.webhooks import _pick_source_connection

        source_connection = connection or _pick_source_connection(app)
        if source_connection is None:
            return {"status": "error", "error": "agent source connection is unavailable"}

        # Serialize package reconciliation across every registered member of
        # this repo. A federation anchor can update siblings, so locking only
        # the routed app would still allow an independent sibling delivery to
        # race the bundle. Deterministic PK order also avoids cross-member
        # deadlocks. GitHub may retry or reorder otherwise-valid deliveries;
        # resolving the live head while holding these locks makes every waiter
        # reconcile the newest source instead of letting a slow old one win.
        with transaction.atomic():
            repo_apps = list(
                RegisteredApp.objects.select_for_update()
                .filter(
                    organization_id=app.organization_id,
                    source_kind=app.source_kind,
                    source_repo=app.source_repo,
                    deleted_at__isnull=True,
                )
                .order_by("pk")
            )
            locked_app = next((candidate for candidate in repo_apps if candidate.pk == app.pk), None)
            if locked_app is None:
                return {"status": "error", "error": "registered agent is no longer active"}
            locked_workload = (
                locked_app.workloads.filter(kind=Workload.Kind.AGENT, deleted_at__isnull=True)
                .select_related("brief")
                .first()
            )
            if locked_workload is None:
                return {"status": "not_agent"}

            locked_deploy_branch = locked_app.deploy_branch or locked_app.default_branch or "main"
            if branch != locked_deploy_branch:
                return {
                    "status": "ignored_branch",
                    "branch": branch,
                    "deploy_branch": locked_deploy_branch,
                }

            current_sha = fetch_branch_head(
                source_connection,
                repo_full_name=locked_app.source_repo,
                branch=locked_deploy_branch,
            )
            snapshot = (
                locked_workload.brief.manifest_snapshot
                if locked_workload.brief_id and isinstance(locked_workload.brief.manifest_snapshot, dict)
                else {}
            )
            package = snapshot.get("agent_package") if isinstance(snapshot.get("agent_package"), dict) else {}
            federation = package.get("federation") if isinstance(package.get("federation"), dict) else {}
            bundle_sync = (
                bool(federation.get("auto_register_new"))
                and str(federation.get("anchor_manifest") or "") == locked_app.manifest_path
            )
            manifest_paths = None if bundle_sync else [locked_app.manifest_path]

            result = register_agent_repo(
                project=locked_app.project,
                source_kind=locked_app.source_kind,
                source_repo=locked_app.source_repo,
                ref=current_sha,
                source_url=locked_app.source_url,
                default_branch=locked_app.default_branch or "main",
                deploy_branch=locked_deploy_branch,
                default_cluster=locked_app.default_tenant_cluster,
                manifest_paths=manifest_paths,
            )
    except Exception as exc:  # noqa: BLE001 — webhook must still ACK
        logger.exception("agent package auto-sync failed for app=%s", app.pk)
        return {"status": "error", "error": str(exc) or exc.__class__.__name__}
    return {
        "status": result.status,
        "error": result.error or "",
        "commit_sha": current_sha,
        "delivery_commit_sha": commit_sha,
        "superseded_delivery": current_sha != commit_sha,
        "mode": "bundle" if bundle_sync else "independent",
        "agents": [item.slug for item in result.agents],
        "notes": [note for item in result.agents for note in item.skill_notes],
    }


@require_http_methods(["POST"])
def pr_webhook(request: HttpRequest, app_guid: str) -> JsonResponse:
    """POST /api/webhooks/github/<app_guid>/ — GitHub push / PR receiver.

    Two responsibilities share this endpoint because the platform installs
    a single host-side webhook subscribed to both ``push`` and
    ``pull_request`` (see ``install_github_webhook``):

    1. ``pull_request`` events drive the preview-environment lifecycle
       (BUILD / TEARDOWN), as documented in the module docstring.
    2. ``push`` and ``pull_request`` events both fan out to any
       :class:`WorkflowWebhook` rows bound to the resolved RegisteredApp,
       launching workflow instances. This is the SCM → workflow trigger
       bridge.

    Every other event header (``ping``, ``installation``, …) is acked
    with 200 / ``event not handled`` so the host stops retrying.
    """
    # Imports inside the view per project convention so the URL module
    # stays importable without triggering Django ORM at import time.
    from astrolift_registry.models import RegisteredApp
    from astrolift_scm.services.webhooks import _pick_source_connection
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import Actor, BuildPreviewInput, TearDownPreviewInput

    app = (
        RegisteredApp.objects.filter(guid=str(app_guid), deleted_at__isnull=True)
        .select_related("organization", "default_tenant_cluster")
        .first()
    )
    if app is None:
        return JsonResponse({"detail": "app not found"}, status=404)

    connection = _pick_source_connection(app)
    if connection is None:
        # No active source connection on the app's org — without one we
        # have no secret to validate the signature against, so the only
        # safe response is 401. The verifier check below would land in
        # the same place; bail early to make the intent obvious in logs.
        return JsonResponse({"detail": "webhook secret not configured"}, status=401)

    secret = _decrypt_webhook_secret(connection)
    if secret is None:
        return JsonResponse({"detail": "webhook secret not configured"}, status=401)

    raw_body = request.body
    try:
        webhook_ingress.verify_signature(
            source=WebhookSource.GITHUB,
            secret=secret,
            raw_body=raw_body,
            headers=dict(request.headers),
        )
    except WebhookRejected:
        # Generic message — never bubble which check fired so a probing
        # caller can't distinguish "wrong secret" from "no header".
        return JsonResponse({"detail": "invalid signature"}, status=401)

    event = request.headers.get("X-GitHub-Event", "")
    if event not in ("push", "pull_request"):
        return JsonResponse({"detail": "event not handled"}, status=200)

    try:
        payload = json.loads(
            raw_body.decode("utf-8") or "{}",
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return JsonResponse({"detail": f"invalid JSON: {exc}"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"detail": "invalid payload shape"}, status=400)

    # SourceConnection webhook secrets are organization-scoped. Bind every
    # otherwise-valid signature to the app's exact repository so a delivery
    # captured from (or redirected by an admin of) one repo cannot be replayed
    # against another app URL that shares the same connection secret.
    repository = payload.get("repository")
    delivered_repo = str(repository.get("full_name") or "") if isinstance(repository, dict) else ""
    if delivered_repo.casefold() != str(app.source_repo or "").casefold():
        return JsonResponse({"detail": "repository does not match webhook target"}, status=403)

    # SCM → workflow trigger bridge: both push and PR deliveries fan out
    # to any WorkflowWebhook rows bound to this app. Independent of the
    # preview-env dispatch below — a push has no preview path, and a PR
    # may fire workflows whether or not it builds a preview.
    dispatched = _dispatch_workflow_webhooks(event, app, payload)

    if event == "push":
        # Agent packages have their own deploy contract: a deploy-branch push
        # freezes a new package snapshot at the exact pushed commit. This does
        # not build an image; CI may still publish the runtime image, and each
        # subsequent Task pulls mutable tags such as ``latest`` with Always.
        agent_sync = _sync_agent_package_on_push(app, payload, connection=connection)
        # Best-effort DSL sync: fetch .astrolift/workflows.yaml from the
        # pushed ref and upsert WorkflowDefinitions. Never blocks the ack.
        _sync_workflow_dsl(app, connection, payload)
        # Best-effort drift reconcile (#1210): re-read the managed CI
        # workflow at the pushed SHA and flag its drift state. Observe-only —
        # never writes to the repo. Never blocks the ack.
        _reconcile_ci_workflow(app, payload)
        # Push has no preview-environment lifecycle; the workflow
        # dispatch above is the whole job. Ack so GitHub stops retrying.
        return JsonResponse(
            {
                "detail": "push handled",
                "workflows_dispatched": dispatched,
                "agent_package_sync": agent_sync,
            },
            status=200,
        )

    pr_ctx = _build_pr_context(payload)
    if pr_ctx is None:
        return JsonResponse({"detail": "missing required pull_request fields"}, status=200)

    # Route the pull_request event to WorkflowWebhook triggers (#863).
    # Runs before the preview dispatch so the workflow fire is not
    # gated on whether preview environments are enabled on the app.
    _route_pr_to_workflow_webhooks(app, pr_ctx)

    app_ctx = github_pr_dispatch.AppPreviewContext(
        registered_app_id=app.pk,
        preview_enabled=app.preview_enabled,
        skip_bot_authors=False,
    )

    try:
        decision = github_pr_dispatch.decide_dispatch(event=pr_ctx, app_context=app_ctx)
    except github_pr_dispatch.GitHubDispatchError as exc:
        # opened-without-head_sha edge case — the synchronize event will
        # retrigger once GitHub populates HEAD, so a 200 ack here saves
        # retries on the host side.
        logger.info("pr_webhook: dispatch declined: %s", exc)
        return JsonResponse({"detail": str(exc)}, status=200)

    if decision.kind == github_pr_dispatch.DispatchKind.IGNORE:
        return JsonResponse({"detail": decision.reason}, status=200)

    actor = Actor(kind="system", user_id=None, display=f"github-pr:{pr_ctx.pr_number}")

    if decision.kind == github_pr_dispatch.DispatchKind.BUILD_PREVIEW:
        preview, _created = _ensure_preview_environment(app, pr_ctx)
        if preview is None:
            return JsonResponse(
                {"detail": "app has no default tenant cluster bound — cannot provision preview"},
                status=200,
            )
        start_workflow(
            "BuildPreviewWorkflow",
            args=[BuildPreviewInput(preview_environment_id=preview.pk, actor=actor)],
            workflow_id=decision.workflow_id,
        )
        return JsonResponse(
            {"detail": decision.reason, "workflow_id": decision.workflow_id},
            status=200,
        )

    if decision.kind == github_pr_dispatch.DispatchKind.TEARDOWN_PREVIEW:
        from astrolift_lifecycle.models import PreviewEnvironment

        preview = PreviewEnvironment.objects.filter(
            registered_app=app,
            pr_number=pr_ctx.pr_number,
            is_manual=False,
            deleted_at__isnull=True,
        ).first()
        if preview is None:
            return JsonResponse({"detail": "no preview to tear down"}, status=200)
        start_workflow(
            "TearDownPreviewWorkflow",
            args=[TearDownPreviewInput(preview_environment_id=preview.pk, actor=actor)],
            workflow_id=decision.workflow_id,
        )
        return JsonResponse(
            {"detail": decision.reason, "workflow_id": decision.workflow_id},
            status=200,
        )

    # Defensive: dispatcher only ever returns one of the three kinds
    # above. Treat anything else as an ack so GitHub doesn't retry.
    return JsonResponse({"detail": "no-op"}, status=200)


def _route_pr_to_workflow_webhooks(
    app,
    pr_ctx,
) -> None:
    """Fan-out a pull_request event to matching WorkflowWebhook triggers (#863).

    Best-effort; exceptions are logged but never propagate to the caller
    so GitHub's retry queue stays clean.
    """
    try:
        from astrolift_agents.services.workflow_triggers import (
            ScmEvent,
            route_scm_push_to_workflow_webhooks,
        )

        scm_event = ScmEvent(
            organization_id=app.organization_id,
            repo_full_name=pr_ctx.repo_full_name,
            branch=pr_ctx.head_branch,
            head_sha=pr_ctx.head_sha,
            event_kind="pull_request",
            raw_payload={
                "pr_number": pr_ctx.pr_number,
                "action": pr_ctx.raw_action,
            },
        )
        route_scm_push_to_workflow_webhooks(scm_event)
    except Exception:
        logger.exception(
            "pr_webhook: WorkflowWebhook routing failed for %s PR#%s",
            getattr(pr_ctx, "repo_full_name", "?"),
            getattr(pr_ctx, "pr_number", "?"),
        )
