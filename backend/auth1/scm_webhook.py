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
  4. Resolve the matching ``RegisteredApp`` by ``source_repo``
     scoped to this connection's organization. If
     ``trigger_mode==auto_on_push`` and the pushed branch matches
     ``deploy_branch``, fire ``DeployAppWorkflow`` with
     ``trigger_kind=push``.

What this *doesn't* do (filed for follow-up):
  - Replay protection beyond HMAC (no nonce/timestamp window). For
    a v1 OSS demo the secret + HMAC is enough; production
    deployments behind a CDN/WAF can layer rate-limiting.
  - GitHub App webhook (X-GitHub-Hook-Installation-Target-ID) —
    same handler shape but resolves the connection via
    installation_id rather than the URL guid.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
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
    )

    if deployment.status == Deployment.Status.PENDING.value:
        start_workflow(
            "DeployAppWorkflow",
            args=[
                DeployAppInput(
                    registered_app_id=app.pk,
                    app_environment_id=env.pk,
                    image_tags={"app": deployment.image_tag},
                    trigger_kind=Deployment.TriggerKind.PUSH.value,
                    actor=actor,
                )
            ],
            workflow_id=f"DeployAppWorkflow-{app.guid}-{env.guid}",
        )
    return deployment


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
    verifier = _VERIFIERS.get(kind)
    if verifier is None or not verifier(request, body, secret):
        # Don't tell the caller whether the signature was missing or
        # invalid — both leak the same useful info to a probe.
        return JsonResponse({"detail": "unauthorized"}, status=401)

    parser = _PARSERS.get(kind)
    parsed = parser(body) if parser else None
    if parsed is None:
        # Non-push event (ping, status, comment); ack and move on.
        return JsonResponse({"ok": True, "ignored": "non_push"}, status=202)

    full_name, branch, head_sha = parsed

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
    for app in apps:
        deploy = _fire_deploy(app, branch, head_sha)
        if deploy is not None:
            fired.append(
                {"app": app.slug, "deployment": str(deploy.guid)}
            )

    return JsonResponse(
        {"ok": True, "fired": fired, "repo": full_name, "branch": branch},
        status=202,
    )
