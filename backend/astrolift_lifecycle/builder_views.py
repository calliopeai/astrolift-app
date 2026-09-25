"""REST views for the Calliope App Builder integration (#767, #768).

Three endpoints, all mounted under ``/api/builder/v1/``:

* ``POST   /dev-environments/``                 — create + provision a dev env.
* ``PUT    /dev-environments/<guid>/files/``    — sync a new file tree.
* ``POST   /dev-environments/<guid>/promote/``  — promote to a registered app.

The wire contract, including binary files and the data file (#1858), is
documented in ``docs/builder-api.md``.

Auth flows through :class:`astrolift_identity.middleware.ApiTokenAuthMiddleware`,
which is already in the middleware chain. An ``Authorization: Bearer
alft_at_...`` header resolves to ``request.user`` + attaches the row
on ``request._api_token``. Nothing else is accepted, a browser session
included: the routes are CSRF-exempt, CORS answers any origin with
credentials by default, and previews and promoted apps serve user code
from the builder's base domain, so a page on a same-site host could
otherwise act as whoever is signed in. A browser never sends a bearer
token by itself.

The token's organization scopes every request, so a token issued under
org A can never touch org B's data even if its user belongs to both.

Belonging to the org is not enough to act in it (#1878). Past the
module gate, a token must carry ``write:apps`` and the caller needs
``app.create`` to create or sync, and both ``app.create`` and
``app.deploy`` on the target team to promote. Every cluster and managed
domain a request names resolves among the org's own rows or shared
(org NULL) ones (#1872).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from astrolift_identity.api_tokens import SCOPE_WRITE_APPS, enforce_scopes
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission
from core.tenancy import TenantContext, tenant_context

log = logging.getLogger("astrolift_lifecycle.builder_views")

_VALID_RUNTIMES = {"python", "node", "ruby", "go", "static"}
_VALID_PROFILES = {"small", "medium", "large"}
_MAX_FILES = 100
_FILE_SIZE_LIMIT = 512 * 1024  # 512 KiB total, decoded

# DNS-label shape for slugs (k8s names + ingress hostnames must match).
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$")

_TASK_QUEUE = "astrolift-main"

# In-tree provisioner names Kubernetes now serves through a CSI driver; a
# class naming one only provisions while that driver is installed.
_CSI_MIGRATED_PROVISIONERS = {
    "kubernetes.io/aws-ebs": "ebs.csi.aws.com",
    "kubernetes.io/gce-pd": "pd.csi.storage.gke.io",
    "kubernetes.io/azure-disk": "disk.csi.azure.com",
    "kubernetes.io/azure-file": "file.csi.azure.com",
}


def _token_required() -> JsonResponse:
    return JsonResponse(
        {
            "detail": "an api token is required: send Authorization: Bearer alft_at_...",
            "reason": "api_token_required",
        },
        status=401,
    )


def _resolve_org(token):
    """Resolve the token's Organization.

    Returns ``(org, None)`` on success, ``(None, JsonResponse)`` when the
    org is gone. The error response is pre-rendered so the caller can
    ``return err`` without re-creating the JSON envelope.
    """
    from astrolift_identity.models import Organization

    org = Organization.objects.filter(
        pk=token.organization_id,
        deleted_at__isnull=True,
    ).first()
    if org is not None:
        return org, None
    return None, JsonResponse(
        {"detail": "token organization no longer exists"},
        status=403,
    )


def _module_gate(org):
    """403 when the org cannot use the builder API, else ``None`` (#1859).

    The builder API is the Chat Studio integration module's surface, so it
    follows that module's per-org switch. ``reason`` lets the client say
    whether an org admin can fix it or only the install admin can.
    """
    from astrolift_identity.org_modules import (
        CHAT_STUDIO_INTEGRATION,
        REASON_DISABLED_BY_INSTALL,
        module_state,
    )

    enabled, reason = module_state(org.pk, CHAT_STUDIO_INTEGRATION)
    if enabled:
        return None
    if reason == REASON_DISABLED_BY_INSTALL:
        detail = "the chat_studio_integration module is turned off on this install"
    else:
        detail = "the chat_studio_integration module is not enabled for this organization"
    return JsonResponse(
        {"detail": detail, "reason": reason, "module": CHAT_STUDIO_INTEGRATION},
        status=403,
    )


def _authorize(token, org, permissions: tuple[Permission, ...], *, team=None):
    """403 unless the token's user holds every one of ``permissions`` in ``org``, else ``None`` (#1878).

    A token's scopes cap its user's grants, so a token without
    ``write:apps`` is refused whatever its user may do. The check reads the
    token and the named target only, never the tenant middleware's
    context, so request headers cannot widen it. ``team`` is the team an
    app lands in. Without one, a token issued for a team is checked on that
    team, which the resolver confirms is live and in ``org``: the bindings
    of a team deleted after the token was issued stop counting.
    """
    missing = enforce_scopes(token, (SCOPE_WRITE_APPS,))
    if missing:
        return JsonResponse(
            {
                "detail": f"the api token lacks the {missing} scope",
                "reason": "missing_scope",
                "scope": missing,
            },
            status=403,
        )

    if team is not None:
        scope, where = PermissionScope(kind=ScopeKind.TEAM, id=team.pk), f"on team {team.slug!r}"
    elif token.team_id is not None:
        scope, where = PermissionScope(kind=ScopeKind.TEAM, id=token.team_id), "on the api token's team"
    else:
        scope, where = None, "in this organization"

    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=token.user_id)):
        for permission in permissions:
            try:
                check_permission(permission, scope=scope)
            except PermissionDenied:
                return JsonResponse(
                    {
                        "detail": f"{permission.value} is required {where}",
                        "reason": "missing_permission",
                        "permission": permission.value,
                    },
                    status=403,
                )
    return None


def _outside_org(cluster, org) -> bool:
    """True for a cluster another org owns; shared clusters (org NULL) serve every org."""
    return cluster is not None and cluster.organization_id not in (None, org.pk)


def _load_json(request: HttpRequest, *, max_bytes: int | None = None):
    """Parse the request body. Returns ``(body, None)`` or ``(None, err_response)``.

    ``max_bytes`` is for bodies allowed past Django's
    ``DATA_UPLOAD_MAX_MEMORY_SIZE``, which ``request.body`` enforces for
    every endpoint: the declared length is checked against it first, then
    the stream is read directly.
    """
    if max_bytes is None:
        raw = request.body
    else:
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length > max_bytes:
            return None, JsonResponse({"detail": f"request body exceeds {max_bytes} bytes"}, status=413)
        raw = request.read(max_bytes + 1)
    try:
        return json.loads(raw or "{}"), None
    except (ValueError, UnicodeDecodeError):
        return None, JsonResponse({"detail": "invalid JSON body"}, status=400)


def _data_file_limit() -> int:
    from constance import config as constance_config

    return int(constance_config.BUILDER_DATA_FILE_MAX_BYTES)


def _max_sync_body_bytes(data_file_limit: int) -> int:
    """Largest files-sync body worth reading.

    The data file travels base64-encoded (4/3 of its size); JSON escaping
    can grow text files up to 6x (``\\u00XX``); 1 MiB covers paths and
    structure.
    """
    return 4 * -(-data_file_limit // 3) + 6 * _FILE_SIZE_LIMIT + 1024 * 1024


def _path_error(path) -> str:
    if not isinstance(path, str) or not path:
        return "file paths must be non-empty strings"
    if path.startswith("/") or ".." in path:
        return f"path {path!r} must be relative and must not contain .."
    return ""


def _decode_base64(entry) -> bytes | None:
    """Decoded bytes of a ``{"content", "encoding": "base64"}`` entry, or None.

    Strict standard alphabet with padding, no line breaks: the content goes
    to Kubernetes as-is, which decodes it the same way.
    """
    if not isinstance(entry, dict) or entry.get("encoding") != "base64":
        return None
    content = entry.get("content")
    if not isinstance(content, str):
        return None
    try:
        return base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError):
        return None


def _persistent_storage_class(cluster) -> str:
    """StorageClass a promoted app's data claim can bind through, or ``""``.

    Picks the default class, else the only one, as the #1023 autostamp
    does. A CSI-backed class whose driver is not installed does not count:
    that is how an EKS cluster without the EBS CSI driver looks
    (installer#315), and its claim would sit Pending with the app never
    starting. Any failure to ask the cluster also answers "no", which
    promotes with an emptyDir and ``data_persistent: false``.
    """
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    try:
        driver = _driver_for_cluster(cluster)
        ctx = _context_for_cluster(cluster)
        classes = driver.list_storage_classes(ctx.slug)
        chosen = next((sc for sc in classes if sc.is_default), None)
        if chosen is None and len(classes) == 1:
            chosen = classes[0]
        if chosen is None or chosen.provisioner == "kubernetes.io/no-provisioner":
            return ""
        provisioner = _CSI_MIGRATED_PROVISIONERS.get(chosen.provisioner, chosen.provisioner)
        if "csi" in provisioner and provisioner not in driver.list_csi_drivers(ctx.slug):
            return ""
        return chosen.name
    except Exception:  # noqa: BLE001 (any probe failure means no persistent volume)
        log.warning(
            "persistent volume probe failed for cluster %s; promoting with an emptyDir",
            cluster.slug,
            exc_info=True,
        )
        return ""


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
    token = getattr(request, "_api_token", None)
    if token is None:
        return _token_required()

    org, err = _resolve_org(token)
    if err:
        return err
    err = _module_gate(org)
    if err:
        return err
    err = _authorize(token, org, (Permission.APP_CREATE,))
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
    # the org, else a shared one, so the API works "out of the box"
    # without the App Builder having to enumerate clusters first. Knowing
    # a guid grants nothing: an explicit guid resolves among the same
    # org-or-shared clusters, and another org's answers 404 (#1872).
    # The org's own clusters come first because a tenant can publish a
    # cluster as shared (#1918), and a lower pk must not send this org's
    # code to it while the org has a cluster of its own.
    from django.db.models import F

    from astrolift_clusters.models import TenantCluster

    cluster_guid = body.get("cluster_guid")
    if cluster_guid:
        cluster = (
            TenantCluster.objects.filter(
                guid=cluster_guid,
                deleted_at__isnull=True,
                is_active=True,
            )
            .filter(_cluster_q_for_org(org))
            .first()
        )
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
            .order_by(F("organization").asc(nulls_last=True), "pk")
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
    """Sync a new file tree into a running dev environment (#767).

    ``files`` maps a relative path to UTF-8 text, or to
    ``{"content": <base64>, "encoding": "base64"}`` for a binary file
    (#1858). ``data_file`` declares the one data file, as
    ``{"path", "content", "encoding": "base64"}`` with its own size cap
    (Constance ``BUILDER_DATA_FILE_MAX_BYTES``); omitted keeps the stored
    one, ``null`` removes it. See ``docs/builder-api.md``.
    """
    token = getattr(request, "_api_token", None)
    if token is None:
        return _token_required()

    org, err = _resolve_org(token)
    if err:
        return err
    err = _module_gate(org)
    if err:
        return err
    err = _authorize(token, org, (Permission.APP_CREATE,))
    if err:
        return err

    from astrolift_lifecycle.models import DevEnvironment

    dev = (
        DevEnvironment.objects.defer("data_file")
        .select_related("tenant_cluster")
        .filter(guid=guid, organization=org, deleted_at__isnull=True)
        .first()
    )
    if dev is None:
        return JsonResponse({"detail": "dev environment not found"}, status=404)

    # Create could once bind another org's cluster (#1872); a row made
    # that way must not keep pushing code to it.
    if _outside_org(dev.tenant_cluster, org):
        log.warning("refusing to sync dev environment %s: its cluster belongs to another org", dev.guid)
        return JsonResponse(
            {"detail": "dev environment is bound to a cluster outside this organization"},
            status=409,
        )

    if dev.status != DevEnvironment.Status.RUNNING:
        return JsonResponse({"detail": "environment is not running"}, status=409)

    data_file_limit = _data_file_limit()
    body, err = _load_json(request, max_bytes=_max_sync_body_bytes(data_file_limit))
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
    total_bytes = 0
    for path_key, value in files.items():
        path_err = _path_error(path_key)
        if path_err:
            return JsonResponse({"detail": path_err}, status=400)
        if isinstance(value, dict):
            decoded = _decode_base64(value)
            if decoded is None:
                return JsonResponse(
                    {
                        "detail": (
                            f"file {path_key!r} must be text or "
                            '{"content": <base64>, "encoding": "base64"}'
                        )
                    },
                    status=400,
                )
            files[path_key] = {"content": value["content"], "encoding": "base64"}
            total_bytes += len(decoded)
        else:
            total_bytes += len(str(value).encode("utf-8"))

    if total_bytes > _FILE_SIZE_LIMIT:
        return JsonResponse(
            {"detail": f"total file size exceeds {_FILE_SIZE_LIMIT // 1024}KiB limit"},
            status=400,
        )

    update_fields = ["files", "status", "updated_at", "version"]
    if "data_file" in body:
        data_file = body["data_file"]
        if data_file is None:
            dev.data_file_path, dev.data_file = "", None
        else:
            if not isinstance(data_file, dict):
                return JsonResponse(
                    {"detail": "data_file must be an object with path, content and encoding, or null"},
                    status=400,
                )
            path_err = _path_error(data_file.get("path"))
            if not path_err and len(data_file["path"]) > 255:
                path_err = "path must be at most 255 characters"
            if path_err:
                return JsonResponse({"detail": f"data_file: {path_err}"}, status=400)
            decoded = _decode_base64(data_file)
            if decoded is None:
                return JsonResponse(
                    {"detail": 'data_file must carry base64 content with "encoding": "base64"'},
                    status=400,
                )
            if len(decoded) > data_file_limit:
                return JsonResponse(
                    {"detail": f"data_file exceeds the {data_file_limit} byte limit"},
                    status=400,
                )
            dev.data_file_path, dev.data_file = data_file["path"], decoded
        update_fields += ["data_file_path", "data_file"]

    dev.files = files
    dev.status = DevEnvironment.Status.SYNCING
    dev.save(update_fields=update_fields)

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
    """Promote a dev environment to a registered production app (#768).

    Besides onboarding the app, serves it from its own namespace (#1858).
    When the dev env declares a data file, ``data_persistent`` says whether
    it sits on a persistent volume (true) or an emptyDir that resets on
    restart (false); it is null without a data file.
    """
    token = getattr(request, "_api_token", None)
    if token is None:
        return _token_required()

    org, err = _resolve_org(token)
    if err:
        return err
    err = _module_gate(org)
    if err:
        return err

    body, err = _load_json(request)
    if err:
        return err

    # Resolve the team binding. Promote needs a Team because every
    # RegisteredApp row has a non-null ``team`` FK; explicit slug wins,
    # default is the first active team on the org. It is resolved before
    # the permission check because the check runs on this team: with no
    # target, a team-scoped token would pass on its own team's grants and
    # could then land the app in any team. An unknown team falls back to
    # the org-level check and answers 404 or 422 further down.
    from astrolift_identity.models import Team

    team_slug = body.get("team_slug")
    teams = Team.objects.filter(organization=org, deleted_at__isnull=True)
    team = teams.filter(slug=team_slug).first() if team_slug else teams.order_by("created_at").first()

    err = _authorize(token, org, (Permission.APP_CREATE, Permission.APP_DEPLOY), team=team)
    if err:
        return err

    from astrolift_lifecycle.models import DevEnvironment

    dev = (
        DevEnvironment.objects.defer("data_file")
        .filter(guid=guid, organization=org, deleted_at__isnull=True)
        .first()
    )
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

    if team is None:
        if team_slug:
            return JsonResponse(
                {"detail": f"team {team_slug!r} not found"},
                status=404,
            )
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

    # Create could once bind another org's cluster (#1872); promoting
    # such a row would onboard and serve a new app there.
    if _outside_org(cluster, org):
        log.warning("refusing to promote dev environment %s: its cluster belongs to another org", dev.guid)
        return JsonResponse(
            {"detail": "dev environment is bound to a cluster outside this organization"},
            status=409,
        )

    # ManagedDomain binding is optional — when the caller passes a
    # ``domain``, look it up by zone and bind it to the env; otherwise
    # leave ``managed_domain=NULL`` and let the org's default kick in.
    # Zone names are guessable, so only the org's own zones and shared
    # (org NULL) ones bind; another org's answers 404 (#1872).
    from django.db.models import Q

    from astrolift_clusters.models import ManagedDomain

    domain_zone = body.get("domain")
    managed_domain = None
    if domain_zone:
        managed_domain = ManagedDomain.objects.filter(
            Q(organization=org) | Q(organization__isnull=True),
            zone=domain_zone,
            deleted_at__isnull=True,
        ).first()
        if managed_domain is None:
            return JsonResponse(
                {"detail": f"managed domain {domain_zone!r} not found"},
                status=404,
            )

    from _sdk.k8s_naming import app_namespace

    from astrolift_registry.hostname_claims import hostname_label_refusal
    from astrolift_registry.namespaces import namespace_refusal

    refusal = namespace_refusal(
        app_namespace(organization_slug=org.slug, app_slug=app_slug), organization_id=org.pk
    ) or hostname_label_refusal(app_slug, organization=org, managed_domain=managed_domain)
    if refusal is not None:
        return JsonResponse({"detail": refusal}, status=409)

    storage_class = _persistent_storage_class(cluster) if dev.data_file_path else ""

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

    # Onboarding gives the app a namespace but runs nothing in it; this
    # serves the uploaded files there, independent of the dev env's own
    # workload and lifetime.
    from astrolift_workflows.activities.dev_environment import promoted_app_hostname
    from astrolift_workflows.inputs import DeployPromotedAppInput

    try:
        start_workflow(
            "DeployPromotedAppWorkflow",
            [DeployPromotedAppInput(dev_environment_id=dev.pk, storage_class=storage_class)],
            workflow_id=f"DeployPromotedAppWorkflow-{app.guid}",
            task_queue=_TASK_QUEUE,
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "failed to start DeployPromotedAppWorkflow for promoted app %s",
            app.slug,
        )

    return JsonResponse(
        {
            "id": dev.guid,
            "status": dev.status,
            "app_guid": app.guid,
            "app_slug": app.slug,
            "app_url": f"https://{promoted_app_hostname(app)}",
            "data_persistent": bool(storage_class) if dev.data_file_path else None,
        },
        status=202,
    )
