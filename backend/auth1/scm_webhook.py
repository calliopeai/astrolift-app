"""
SCM push-webhook receiver.

Each SourceConnection can have a webhook secret. The operator
copies it once into the SCM host's webhook config; from that
moment, the host POSTs push events to:

    /app/auth1/scm/<kind>/webhook/<connection-id>/

We:

  1. Look up the connection by guid; reject if missing / inactive.
  2. HMAC-verify the body against the stored secret. Different
     hosts use different headers + algorithms:
       - GitHub: X-Hub-Signature-256: sha256=<hex>
       - GitLab: X-Gitlab-Token: <plaintext>  (yes, plaintext —
         GitLab doesn't HMAC; we compare against the stored secret
         in constant time anyway).
  3. Parse the push payload, extract the repo full_name + branch +
     head commit.
  4. Attribute the delivery to the owning org (#1123). A GitHub App
     sends every installation's events to a SINGLE webhook URL, so
     under ``GITHUB_APP_CONNECTION_SCOPE=per_install`` (one App shared
     across orgs) the URL guid is the *creator* org's connection, not
     necessarily the org whose repo pushed. When the (verified) payload
     carries an ``installation.id`` we re-resolve the connection by that
     id — the true owner — and fail closed (ack + ignore) on an
     installation we don't recognise. Deliveries with no installation id
     (a per-repo OAuth/PAT hook) keep using the URL-guid connection.
  5. Resolve the matching ``RegisteredApp`` by ``source_repo`` scoped to
     the resolved connection's organization. If
     ``trigger_mode==auto_on_push`` and the pushed branch matches
     ``deploy_branch``, fire ``DeployAppWorkflow`` with
     ``trigger_kind=push``.

What this *doesn't* do (filed for follow-up):
  - Replay protection beyond HMAC (no nonce/timestamp window). For
    a v1 OSS demo the secret + HMAC is enough; production
    deployments behind a CDN/WAF can layer rate-limiting.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection, WebhookDelivery
from astrolift_workflows.client import start_workflow
from astrolift_workflows.inputs import Actor, DeployAppInput
from core.secrets import EncryptedSecret, decrypt

logger = logging.getLogger(__name__)


def _verify_github(request: HttpRequest, body: bytes, secret: bytes) -> bool:
    sig_header = request.headers.get("X-Hub-Signature-256", "")
    if not sig_header.startswith("sha256="):
        return False
    sent = sig_header[len("sha256=") :]
    mac = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sent, mac)


def _verify_gitlab(request: HttpRequest, _body: bytes, secret: bytes) -> bool:
    sent = (request.headers.get("X-Gitlab-Token", "") or "").encode("utf-8")
    return hmac.compare_digest(sent, secret)


_VERIFIERS = {
    "github": _verify_github,
    "gitlab": _verify_gitlab,
}


def verify_signature(
    *,
    kind: str,
    request: HttpRequest,
    body: bytes,
    secret: bytes,
) -> bool:
    """Canonical HMAC signature check for an SCM push webhook (#529).

    Dispatches to the per-host verifier (``_verify_github`` /
    ``_verify_gitlab``) and returns True only when the presented header
    matches the stored secret in constant time. Returns False for any
    failure mode — unknown kind, missing header, mismatched digest —
    so the caller turns False into a single generic 401 (avoiding
    leaking which check fired to an attacker probing the surface).

    Named ``verify_signature`` so the ``test_webhook_signature_guard``
    CI guard recognizes it as the auth boundary. Every new
    ``/.../webhook/...`` view in this codebase MUST call one of the
    canonical verifier names (or list itself in the guard's EXEMPT
    dict with a written reason) before reading the request body.
    """
    verifier = _VERIFIERS.get(kind)
    if verifier is None:
        return False
    return verifier(request, body, secret)


def _decrypt_webhook_secret(conn: SourceConnection) -> bytes | None:
    if not conn.webhook_secret_backend_kind or not conn.webhook_secret_ciphertext:
        return None
    return decrypt(
        EncryptedSecret(
            backend_kind=conn.webhook_secret_backend_kind,
            backend_ref=bytes(conn.webhook_secret_ciphertext),
        )
    )


def _parse_github_push(body: bytes) -> tuple[str, str, str] | None:
    """Returns (full_name, branch, head_sha) or None."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    full_name = ((payload.get("repository") or {}).get("full_name")) or ""
    ref = payload.get("ref") or ""
    if not ref.startswith("refs/heads/"):
        return None
    branch = ref[len("refs/heads/") :]
    head_sha = payload.get("after") or ""
    if not (full_name and branch):
        return None
    return full_name, branch, head_sha


def _parse_gitlab_push(body: bytes) -> tuple[str, str, str] | None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if payload.get("object_kind") != "push":
        return None
    full_name = ((payload.get("project") or {}).get("path_with_namespace")) or ""
    ref = payload.get("ref") or ""
    if not ref.startswith("refs/heads/"):
        return None
    branch = ref[len("refs/heads/") :]
    head_sha = payload.get("after") or payload.get("checkout_sha") or ""
    if not (full_name and branch):
        return None
    return full_name, branch, head_sha


_PARSERS = {
    "github": _parse_github_push,
    "gitlab": _parse_gitlab_push,
}


def _fire_deploy(app: RegisteredApp, branch: str, head_sha: str) -> Deployment | None:
    """Create a PENDING Deployment + start DeployAppWorkflow.

    Returns the deployment row when one was created, None when the
    push was for a non-deploy branch or all envs are paused.
    """
    if app.deploy_branch and app.deploy_branch != branch:
        return None

    env = (
        AppEnvironment.objects.filter(
            registered_app=app, deleted_at__isnull=True
        )
        .order_by("name")
        .first()
    )
    if env is None or env.deploys_paused:
        return None

    actor = Actor(kind="system", display=f"push:{branch}")
    deployment = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.PUSH.value,
        status=Deployment.Status.PENDING.value
        if env.required_approvals == 0
        else Deployment.Status.PENDING_APPROVAL.value,
        image_tag=head_sha[:128] if head_sha else "",
        approvals_required=env.required_approvals,
        approvals_received=0,
        # CI / VCS provenance (#166): the webhook is the canonical
        # source of these fields when the deploy didn't come from
        # a human in the UI.
        ci_actor_kind="bot",
        commit_sha=head_sha,
        branch=branch,
        ci_provider=app.source_kind or "",
    )

    if deployment.status == Deployment.Status.PENDING.value:
        start_workflow(
            "DeployAppWorkflow",
            args=[
                DeployAppInput(
                    registered_app_id=app.pk,
                    app_environment_id=env.pk,
                    deployment_id=deployment.pk,
                    image_tags={"app": deployment.image_tag},
                    trigger_kind=Deployment.TriggerKind.PUSH.value,
                    actor=actor,
                    commit_sha=deployment.commit_sha,
                )
            ],
            workflow_id=f"DeployAppWorkflow-{app.guid}-{env.guid}",
        )
    return deployment


def _github_installation_id(body: bytes) -> str | None:
    """The GitHub App installation id from a webhook body, or None.

    App-delivered webhooks carry a top-level ``installation.id`` (the
    per-org installation). Returns it as a string; None when the payload
    has no installation object (a per-repo OAuth/PAT hook) or isn't JSON.
    Best-effort: the signature is already verified by the caller, so an
    unparseable body here just means "nothing to attribute by".
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    installation = payload.get("installation")
    if not isinstance(installation, dict):
        return None
    inst_id = installation.get("id")
    if inst_id is None:
        return None
    return str(inst_id)


def _connection_for_installation(installation_id: str) -> SourceConnection | None:
    """The active ``github_app_install`` connection that owns a GitHub App
    installation (#1123).

    Under ``GITHUB_APP_CONNECTION_SCOPE=per_install`` a single shared App
    serves many orgs from one webhook URL, so the installation id — not the
    URL guid — identifies the owning org's connection. Deterministic
    (oldest first) if an installation id somehow maps to more than one row.
    """
    return (
        SourceConnection.objects.filter(
            kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
            installation_id=installation_id,
            is_active=True,
            is_orphaned=False,
            deleted_at__isnull=True,
        )
        .order_by("created_at", "pk")
        .first()
    )


@csrf_exempt
@require_POST
def github_webhook(request: HttpRequest, connection_id: str) -> HttpResponse:
    return _handle("github", request, connection_id)


@csrf_exempt
@require_POST
def gitlab_webhook(request: HttpRequest, connection_id: str) -> HttpResponse:
    return _handle("gitlab", request, connection_id)


def _handle(
    kind: str, request: HttpRequest, connection_id: str
) -> HttpResponse:
    conn = SourceConnection.objects.filter(
        guid=connection_id, is_active=True, deleted_at__isnull=True
    ).first()
    if conn is None:
        return JsonResponse({"detail": "unknown connection"}, status=404)

    secret = _decrypt_webhook_secret(conn)
    if secret is None:
        return JsonResponse(
            {"detail": "no webhook secret configured"}, status=400
        )

    body = request.body
    if not verify_signature(kind=kind, request=request, body=body, secret=secret):
        # Don't tell the caller whether the signature was missing or
        # invalid — both leak the same useful info to a probe.
        return JsonResponse({"detail": "unauthorized"}, status=401)

    # Per-org attribution for shared GitHub Apps (#1123). A GitHub App
    # delivers every installation's events to a SINGLE webhook URL; under
    # GITHUB_APP_CONNECTION_SCOPE=per_install that URL carries the canonical
    # (creator) org's connection guid, so ``conn`` above is that org — not
    # necessarily the org whose repo pushed. The installation id in the
    # now-verified payload identifies the real owner; re-scope ``conn`` to
    # it so every downstream step (app lookup, deploy, delivery dedup,
    # WorkflowWebhook routing) runs against the right org. Fail closed: an
    # installation with no matching connection is acked-and-ignored, never
    # processed under the receiver-URL org. Deliveries with no installation
    # id (a per-repo OAuth/PAT hook) keep the URL-guid connection.
    if kind == "github":
        installation_id = _github_installation_id(body)
        if installation_id:
            owner_conn = _connection_for_installation(installation_id)
            if owner_conn is None:
                logger.warning(
                    "scm_webhook: GitHub App installation %s (delivered to "
                    "connection %s) has no owning connection — ignoring",
                    installation_id,
                    connection_id,
                )
                return JsonResponse(
                    {"ok": True, "ignored": "unknown_installation"},
                    status=202,
                )
            conn = owner_conn

    parser = _PARSERS.get(kind)
    parsed = parser(body) if parser else None
    if parsed is None:
        # Non-push event (ping, status, comment); ack and move on.
        return JsonResponse({"ok": True, "ignored": "non_push"}, status=202)

    full_name, branch, head_sha = parsed

    # Replay protection: every host gives us a delivery identifier
    # we treat as opaque. Insert under a unique constraint scoped to
    # (connection, delivery_id); a duplicate INSERT raises IntegrityError
    # and the receiver bails with 202 + ignored="duplicate" without
    # firing a second deploy. Records older than 30 days get pruned by
    # the cron task — see WebhookDelivery docstring.
    delivery_id = (
        request.headers.get("X-GitHub-Delivery")
        or request.headers.get("X-Gitlab-Event-UUID")
        or ""
    )
    delivery_row: WebhookDelivery | None = None
    if delivery_id:
        try:
            with transaction.atomic():
                delivery_row = WebhookDelivery.objects.create(
                    connection=conn,
                    delivery_id=delivery_id,
                    host_event=request.headers.get(
                        "X-GitHub-Event"
                    ) or request.headers.get(
                        "X-Gitlab-Event"
                    ) or "",
                    repo_full_name=full_name,
                    branch=branch,
                    head_sha=head_sha,
                )
        except IntegrityError:
            return JsonResponse(
                {"ok": True, "ignored": "duplicate", "delivery_id": delivery_id},
                status=202,
            )

    # Touch the connection so the UI can show 'last received'.
    conn.webhook_last_received_at = timezone.now()
    conn.save(update_fields=["webhook_last_received_at"])

    # Find the app by source_repo within the connection's org.
    apps = list(
        RegisteredApp.objects.filter(
            organization_id=conn.organization_id,
            source_repo=full_name,
            is_active=True,
            deleted_at__isnull=True,
            trigger_mode=RegisteredApp.TriggerMode.AUTO_ON_PUSH.value,
        )
    )
    if not apps:
        return JsonResponse(
            {"ok": True, "ignored": "no_matching_app", "repo": full_name},
            status=202,
        )

    fired = []
    last_deploy: Deployment | None = None
    for app in apps:
        deploy = _fire_deploy(app, branch, head_sha)
        if deploy is not None:
            fired.append(
                {"app": app.slug, "deployment": str(deploy.guid)}
            )
            last_deploy = deploy

    # Stamp the delivery row with the last-fired deployment so the
    # UI can backtrack from a webhook to whatever it triggered.
    # If multiple apps shared the repo, this stamp follows the last
    # one — good enough for breadcrumbs; full fan-out lives in
    # AuditEvent.
    if delivery_row is not None and last_deploy is not None:
        delivery_row.triggered_deployment = last_deploy
        delivery_row.save(update_fields=["triggered_deployment"])

    # Route the push event to any matching WorkflowWebhook triggers (#863).
    # This is best-effort: failures are logged but do not affect the HTTP
    # response so an SCM host never sees a 5xx from the workflow layer.
    try:
        from astrolift_agents.services.workflow_triggers import (
            ScmEvent,
            route_scm_push_to_workflow_webhooks,
        )

        scm_event = ScmEvent(
            organization_id=conn.organization_id,
            repo_full_name=full_name,
            branch=branch,
            head_sha=head_sha,
            event_kind="push",
        )
        route_scm_push_to_workflow_webhooks(scm_event)
    except Exception:
        logger.exception(
            "scm_webhook: WorkflowWebhook routing failed for %s/%s", full_name, branch
        )

    return JsonResponse(
        {"ok": True, "fired": fired, "repo": full_name, "branch": branch},
        status=202,
    )
