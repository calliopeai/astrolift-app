"""REST views for the Calliope App Builder integration (#767, #768).

Three endpoints, all mounted under ``/api/builder/v1/``:

* ``POST   /dev-environments/``                 — create + provision a dev env.
* ``PUT    /dev-environments/<guid>/files/``    — sync a new file tree.
* ``POST   /dev-environments/<guid>/promote/``  — promote to a registered app.

Auth flows through :class:`astrolift_identity.middleware.ApiTokenAuthMiddleware`,
which is already in the middleware chain. An ``Authorization: Bearer
alft_at_...`` header resolves to ``request.user`` + attaches the row
on ``request._api_token``. Session-cookie auth (browser app) also works
because the middleware leaves ``request.user`` untouched when there's
no bearer.

Org resolution prefers the token's organization (so a token issued
under org A can never touch org B's data even if the user happens to
be a member of both); for session-cookie auth we fall back to the
user's *single* active ORG-scope ``Member`` row. Multi-org session
auth without a token is rejected because there's no safe default —
the App Builder always presents a token in practice.
"""

from __future__ import annotations

import json
import logging
import re

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

log = logging.getLogger("astrolift_lifecycle.builder_views")

_VALID_RUNTIMES = {"python", "node", "ruby", "go", "static"}
_VALID_PROFILES = {"small", "medium", "large"}
_MAX_FILES = 100
_FILE_SIZE_LIMIT = 512 * 1024  # 512 KiB total

# DNS-label shape for slugs (k8s names + ingress hostnames must match).
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$")

_TASK_QUEUE = "astrolift-main"


def _resolve_org(request: HttpRequest):
    """Resolve the tenant Organization for the request.

    Returns ``(org, None)`` on success, ``(None, JsonResponse)`` when
    we can't pick a single org confidently. The error response is
    pre-rendered so the caller can ``return err`` without re-creating
    the JSON envelope.
    """
    from astrolift_identity.models import Member, Organization

    api_token = getattr(request, "_api_token", None)
    if api_token is not None:
        org = Organization.objects.filter(
            pk=api_token.organization_id,
            deleted_at__isnull=True,
        ).first()
        if org is not None:
            return org, None
        return None, JsonResponse(
            {"detail": "token organization no longer exists"},
            status=403,
        )

    # Session-cookie path — pick the user's single active ORG-scope
    # membership. Multi-org without a token is ambiguous, single-org
    # is the happy path for browser-driven internal tooling.
    org_ids = list(
        Member.objects.filter(
            user=request.user,
            scope_kind=Member.ScopeKind.ORG,
            deleted_at__isnull=True,
            is_active=True,
        ).values_list("scope_id", flat=True)
    )
    if not org_ids:
        return None, JsonResponse(
            {"detail": "no organization found for this user"},
            status=403,
        )
    if len(org_ids) > 1:
        return None, JsonResponse(
            {"detail": "user belongs to multiple organizations; use an api token"},
            status=403,
        )
    org = Organization.objects.filter(pk=org_ids[0], deleted_at__isnull=True).first()
    if org is None:
        return None, JsonResponse(
            {"detail": "membership organization no longer exists"},
            status=403,
        )
    return org, None


def _load_json(request: HttpRequest):
    """Parse the request body. Returns ``(body, None)`` or ``(None, err_response)``."""
    try:
        return json.loads(request.body or "{}"), None
    except (ValueError, UnicodeDecodeError):
        return None, JsonResponse({"detail": "invalid JSON body"}, status=400)


def _cluster_q_for_org(org):
    """Build a Q matching ``organization=org`` OR ``organization IS NULL``.

    The cluster default-picker honors both org-scoped clusters and the
    shared (org=NULL) pool — matches what the rest of the platform's
    cluster picker does. Returned as a ``Q`` so it composes with the
    outer ``.filter(...)`` chain.
    """
    from django.db.models import Q

    return Q(organization=org) | Q(organization__isnull=True)


@require_http_methods(["POST"])
def create_dev_environment(request: HttpRequest) -> JsonResponse:
    """Create + start a Calliope App Builder dev environment (#767)."""
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "authentication required"}, status=401)

    org, err = _resolve_org(request)
    if err:
        return err

    body, err = _load_json(request)
    if err:
        return err

    runtime = body.get("runtime", "python")
    if runtime not in _VALID_RUNTIMES:
        return JsonResponse(
            {"detail": f"runtime must be one of {sorted(_VALID_RUNTIMES)}"},
            status=400,
        )

    port_raw = body.get("port", 8080)
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "port must be an integer"}, status=400)
    if not (1 <= port <= 65535):
        return JsonResponse({"detail": "port must be in 1-65535"}, status=400)

    resource_profile = body.get("resource_profile", "small")
    if resource_profile not in _VALID_PROFILES:
        return JsonResponse(
            {"detail": f"resource_profile must be one of {sorted(_VALID_PROFILES)}"},
            status=400,
        )

    env_vars = body.get("env_vars", {})
    if not isinstance(env_vars, dict):
        return JsonResponse({"detail": "env_vars must be an object"}, status=400)

    # Resolve the target cluster. Explicit ``cluster_guid`` wins; the
    # default path picks the first MANAGED + active cluster bound to
    # the org (or shared) so the API works "out of the box" without
    # the App Builder having to enumerate clusters first.
    from astrolift_clusters.models import TenantCluster

    cluster_guid = body.get("cluster_guid")
    if cluster_guid:
        cluster = TenantCluster.objects.filter(
            guid=cluster_guid,
            deleted_at__isnull=True,
            is_active=True,
        ).first()
        if cluster is None:
            return JsonResponse({"detail": "cluster not found"}, status=404)
    else:
        cluster = (
            TenantCluster.objects.filter(
                deleted_at__isnull=True,
                is_active=True,
                lifecycle=TenantCluster.Lifecycle.MANAGED.value,
            )
            .filter(_cluster_q_for_org(org))
            .first()
        )
        if cluster is None:
            return JsonResponse(
                {"detail": "no ready cluster available; pass cluster_guid"},
                status=422,
            )

    from astrolift_lifecycle.models import DevEnvironment

    dev = DevEnvironment.objects.create(
        organization=org,
        creator=request.user,
        tenant_cluster=cluster,
        runtime=runtime,
        runtime_version=body.get("runtime_version", "") or "",
        start_command=body.get("start_command", "") or "",
        port=port,
        env_vars=env_vars,
        resource_profile=resource_profile,
        status=DevEnvironment.Status.CREATING,
    )

    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import Actor, CreateDevEnvironmentInput

    actor = Actor(kind="user", user_id=request.user.pk, display=str(request.user))
    try:
        start_workflow(
            "CreateDevEnvironmentWorkflow",
            [CreateDevEnvironmentInput(dev_environment_id=dev.pk, actor=actor)],
            workflow_id=f"CreateDevEnvironmentWorkflow-{dev.guid}",
            task_queue=_TASK_QUEUE,
        )
    except Exception:  # noqa: BLE001 — log + keep the row; operator can retry
        log.exception(
            "failed to start CreateDevEnvironmentWorkflow for dev env %s",
            dev.guid,
        )

    return JsonResponse(
        {
            "id": dev.guid,
            "status": dev.status,
            "preview_url": dev.preview_url or None,
        },
        status=201,
    )


@require_http_methods(["PUT"])
def sync_dev_environment_files(request: HttpRequest, guid: str) -> JsonResponse:
    """Sync a new file tree into a running dev environment (#767)."""
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "authentication required"}, status=401)

    org, err = _resolve_org(request)
    if err:
        return err

    from astrolift_lifecycle.models import DevEnvironment

    dev = DevEnvironment.objects.filter(guid=guid, organization=org, deleted_at__isnull=True).first()
    if dev is None:
        return JsonResponse({"detail": "dev environment not found"}, status=404)

    if dev.status != DevEnvironment.Status.RUNNING:
        return JsonResponse({"detail": "environment is not running"}, status=409)

    body, err = _load_json(request)
    if err:
        return err

    files = body.get("files")
    if not isinstance(files, dict):
        return JsonResponse(
            {"detail": "files must be an object mapping path to content"},
            status=400,
        )
    if len(files) > _MAX_FILES:
        return JsonResponse(
            {"detail": f"too many files (max {_MAX_FILES})"},
            status=400,
        )
    for path_key in files:
        if not isinstance(path_key, str) or not path_key:
            return JsonResponse(
                {"detail": "file paths must be non-empty strings"},
                status=400,
            )
        if path_key.startswith("/") or ".." in path_key:
            return JsonResponse(
                {"detail": f"path {path_key!r} must be relative and must not contain .."},
                status=400,
            )

    total_bytes = sum(len(str(v).encode("utf-8")) for v in files.values())
    if total_bytes > _FILE_SIZE_LIMIT:
        return JsonResponse(
            {"detail": f"total file size exceeds {_FILE_SIZE_LIMIT // 1024}KiB limit"},
            status=400,
        )

    dev.files = files
    dev.status = DevEnvironment.Status.SYNCING
    dev.save(update_fields=["files", "status", "updated_at", "version"])

    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import SyncDevEnvironmentFilesInput

    try:
        # Workflow id is timestamped so each sync run gets its own
        # Temporal history (don't join a still-running sync); the dev-
        # env row is the durable target so a re-sync is naturally
        # idempotent on the cluster side.
        start_workflow(
            "SyncDevEnvironmentFilesWorkflow",
            [SyncDevEnvironmentFilesInput(dev_environment_id=dev.pk)],
            workflow_id=(f"SyncDevEnvironmentFilesWorkflow-{dev.guid}-" f"{int(timezone.now().timestamp())}"),
            task_queue=_TASK_QUEUE,
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "failed to start SyncDevEnvironmentFilesWorkflow for %s",
            guid,
        )

    return JsonResponse({"id": dev.guid, "status": dev.status, "file_count": len(files)})


@require_http_methods(["POST"])
def promote_dev_environment(request: HttpRequest, guid: str) -> JsonResponse:
    """Promote a dev environment to a registered production app (#768)."""
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "authentication required"}, status=401)

    org, err = _resolve_org(request)
    if err:
        return err

    from astrolift_lifecycle.models import DevEnvironment

    dev = DevEnvironment.objects.filter(guid=guid, organization=org, deleted_at__isnull=True).first()
    if dev is None:
        return JsonResponse({"detail": "dev environment not found"}, status=404)

    if dev.status not in (
        DevEnvironment.Status.RUNNING,
        DevEnvironment.Status.FAILED,
    ):
        return JsonResponse(
            {"detail": "can only promote a running or failed environment"},
            status=409,
        )

    body, err = _load_json(request)
    if err:
        return err

    app_name = (body.get("app_name") or "").strip()
    if not app_name:
        return JsonResponse({"detail": "app_name is required"}, status=400)

    explicit_slug = (body.get("app_slug") or "").strip()
    app_slug = explicit_slug or re.sub(r"[^a-z0-9-]", "-", app_name.lower()).strip("-")
    if not _SLUG_PATTERN.match(app_slug):
        return JsonResponse(
            {"detail": ("app_slug must be a valid DNS label " "(lowercase alphanumeric + hyphens)")},
            status=400,
        )

    environment_name = (body.get("environment_name") or "production").strip() or "production"

    # Resolve the team binding. Promote needs a Team because every
    # RegisteredApp row has a non-null ``team`` FK; explicit slug wins,
    # default is the first active team on the org.
    from astrolift_identity.models import Team

    team_slug = body.get("team_slug")
    if team_slug:
        team = Team.objects.filter(slug=team_slug, organization=org, deleted_at__isnull=True).first()
        if team is None:
            return JsonResponse(
                {"detail": f"team {team_slug!r} not found"},
                status=404,
            )
    else:
        team = Team.objects.filter(organization=org, deleted_at__isnull=True).order_by("created_at").first()
        if team is None:
            return JsonResponse(
                {"detail": "no team found; pass team_slug"},
                status=422,
            )

    from astrolift_registry.models import RegisteredApp

    if RegisteredApp.objects.filter(slug=app_slug, organization=org, deleted_at__isnull=True).exists():
        return JsonResponse(
            {"detail": f"app slug {app_slug!r} already exists in this org"},
            status=409,
        )

    cluster = dev.tenant_cluster
    if cluster is None:
        return JsonResponse(
            {
                "detail": (
                    "dev environment is not bound to a cluster; cannot " "promote without a runtime target"
                ),
            },
            status=409,
        )

    # ManagedDomain binding is optional — when the caller passes a
    # ``domain``, look it up by zone and bind it to the env; otherwise
    # leave ``managed_domain=NULL`` and let the org's default kick in.
    from astrolift_clusters.models import ManagedDomain

    domain_zone = body.get("domain")
    managed_domain = None
    if domain_zone:
        managed_domain = ManagedDomain.objects.filter(zone=domain_zone, deleted_at__isnull=True).first()
        if managed_domain is None:
            return JsonResponse(
                {"detail": f"managed domain {domain_zone!r} not found"},
                status=404,
            )

    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name=app_name,
        slug=app_slug,
        source_kind=RegisteredApp.SourceKind.DIRECT_UPLOAD,
        provisioning_status=RegisteredApp.ProvisioningStatus.PENDING,
        default_tenant_cluster=cluster,
    )

    from astrolift_lifecycle.models import AppEnvironment

    AppEnvironment.objects.create(
        registered_app=app,
        name=environment_name,
        tenant_cluster=cluster,
        managed_domain=managed_domain,
    )

    dev.status = DevEnvironment.Status.PROMOTING
    dev.promoted_app = app
    dev.save(update_fields=["status", "promoted_app", "updated_at", "version"])

    # Onboard the new app through the standard pipeline so it goes
    # through the same provisioning steps (registry repo, namespace)
    # as a normally-registered app. The ``OnboardAppWorkflow`` is the
    # entry point the deploy mutations also use.
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import Actor, OnboardAppInput

    actor = Actor(kind="user", user_id=request.user.pk, display=str(request.user))
    try:
        start_workflow(
            "OnboardAppWorkflow",
            [
                OnboardAppInput(
                    registered_app_id=app.pk,
                    actor=actor,
                    provider_plugin_id=cluster.provider_plugin_id or 0,
                    tenant_cluster_id=cluster.pk,
                )
            ],
            workflow_id=f"OnboardAppWorkflow-{app.guid}",
            task_queue=_TASK_QUEUE,
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "failed to start OnboardAppWorkflow for promoted app %s",
            app.slug,
        )

    return JsonResponse(
        {
            "id": dev.guid,
            "status": dev.status,
            "app_guid": app.guid,
            "app_slug": app.slug,
        },
        status=202,
    )
