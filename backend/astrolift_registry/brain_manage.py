"""Managing semantic layers (Mimwell brains) end to end (#2424).

The resolvers in ``schema/mutations/brains.py`` and the MCP tools are thin:
the lookups, the status read and every step of ``BrainCreateWorkflow`` live
here, so GraphQL, MCP and the worker run the same code.

Creating a brain is four idempotent steps over one ``BrainCreation`` row:

1. **repo**: the repository must be missing (it is created with the asked
   visibility) or empty; anything else fails with ``CONFLICT``.
2. **scaffold**: the verified Mimwell edition plus ``client.config.json`` and
   ``astrolift.toml`` land as one commit, authored by the caller.
3. **register**: ``registerApp`` (undecorated; the mutation already checked
   ``app.create`` on the project), ``platform_build``, with the rendered
   manifest inline so its workloads exist before the connection can read it.
4. **deploy**: once the app is provisioned, a deployment of the scaffold
   commit. ``READY`` when it runs.

A step that finds its work done moves on, so a retried activity or a
re-joined workflow never repeats a side effect it can see.
"""

from __future__ import annotations

import contextvars
import inspect
import logging
from types import SimpleNamespace
from typing import Any

from astrolift_registry.models import BrainCreation, RegisteredApp

log = logging.getLogger(__name__)

INGEST_WORKFLOW = ".github/workflows/knowledge-refresh.yml"
BRAIN_MANIFEST_FILE = "app/brain-manifest.json"
#: Hosts whose CI dispatch takes a workflow file, so an ingest can be named.
_INGEST_HOSTS = ("github", "gitea")
_TOKEN_KINDS = {
    "github_pat",
    "github_oauth_user",
    "github_app_install",
    "gitlab_pat",
    "gitlab_oauth_user",
    "bitbucket_pat",
    "bitbucket_oauth_user",
    "gitea_pat",
    "gitea_oauth_user",
}


def find_brain(slug: str) -> RegisteredApp | None:
    """The tenant's brain ``slug`` if the caller can read it. Invisible,
    archived and not-a-brain all read as ``None``."""
    from astrolift_registry.brains import visible_brains
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    slug = (slug or "").strip()
    if not slug or tenant is None or tenant.organization_id is None:
        return None
    return visible_brains().filter(organization_id=tenant.organization_id, slug=slug).first()


def source_kind(connection) -> str:
    kind = connection.kind or ""
    return next((h for h in ("github", "gitlab", "bitbucket", "gitea") if kind.startswith(h)), "")


def usable_connection(connection_guid: str, *, organization_id: int, user_id: int | None):
    """The org's connection ``connection_guid`` when it can act for the
    caller: active, a token (not an OAuth app config row), and either the
    org's or the caller's own."""
    from astrolift_scm.models import SourceConnection

    row = (
        SourceConnection.objects.filter(
            guid=str(connection_guid), organization_id=organization_id, deleted_at__isnull=True
        )
        .select_related("user")
        .first()
    )
    if row is None:
        return None, "source connection not found"
    if row.user_id is not None and row.user_id != user_id:
        return None, "source connection not found"
    if row.kind not in _TOKEN_KINDS:
        return None, "this connection is an app configuration, not a credential; connect an account first"
    if not row.is_active or row.is_orphaned or row.reauth_required:
        return None, "this connection needs to be reconnected before it can create a repository"
    return row, ""


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def status(app: RegisteredApp, environment_name: str) -> dict[str, Any] | None:
    """What a brain is serving in one environment, and how fresh it is.
    ``None`` when the environment does not exist. Host reads that fail are
    reported in ``warnings`` and their fields stay null."""
    import json

    from astrolift_lifecycle.models import AppEnvironment, Deployment
    from astrolift_scm.providers import ProviderError, fetch_file
    from astrolift_scm.providers.repo_writes import branch_head_detail, latest_workflow_run

    env = AppEnvironment.objects.filter(
        registered_app=app, name=environment_name, deleted_at__isnull=True
    ).first()
    if env is None:
        return None
    rows = Deployment.objects.filter(registered_app=app, app_environment=env).order_by("-created_at", "-pk")
    latest = rows.first()
    running = rows.filter(status=Deployment.Status.RUNNING.value).first()
    served = (
        AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True).exclude(url="").exists()
    )
    out: dict[str, Any] = {
        "environment_name": env.name,
        "url": env.url or "",
        "served": served,
        "deployment": (
            {
                "id": str(latest.guid),
                "status": latest.status,
                "image_tag": latest.image_tag or "",
                "commit_sha": latest.commit_sha or "",
                "created_at": _iso(latest.created_at),
            }
            if latest is not None
            else None
        ),
        "deployed_commit": (running.commit_sha or "") if running is not None else "",
        "source_head": None,
        "behind": None,
        "compiled_at": None,
        "last_ingest": None,
        "warnings": [],
    }
    if not app.source_repo:
        return _with_substrate(out, app, None, None)
    from astrolift_scm.services.workflows import _pick_source_connection

    connection = _pick_source_connection(app)
    if connection is None:
        out["warnings"].append("no source connection can read the repository")
        return _with_substrate(out, app, None, None)
    branch = app.deploy_branch or app.default_branch or "main"
    try:
        sha, when = branch_head_detail(connection, app.source_repo, branch)
        out["source_head"] = {"commit_sha": sha, "committed_at": when or None, "branch": branch}
        if out["deployed_commit"]:
            out["behind"] = out["deployed_commit"] != sha
    except ProviderError as exc:
        out["warnings"].append(f"source head: {exc.message}")
    manifest_doc = None
    if out["deployed_commit"]:
        try:
            raw = fetch_file(
                connection,
                repo_full_name=app.source_repo,
                path=BRAIN_MANIFEST_FILE,
                ref=out["deployed_commit"],
            )
            if raw:
                manifest_doc = json.loads(raw) or {}
                generated = manifest_doc.get("generated")
                out["compiled_at"] = str(generated) if generated else None
        except ProviderError as exc:
            out["warnings"].append(f"compiled at: {exc.message}")
        except ValueError:
            out["warnings"].append(f"compiled at: {BRAIN_MANIFEST_FILE} is not JSON")
    try:
        out["last_ingest"] = latest_workflow_run(
            connection, app.source_repo, INGEST_WORKFLOW.rsplit("/", 1)[-1]
        )
    except ProviderError as exc:
        if exc.code != "UNSUPPORTED":
            out["warnings"].append(f"last ingest: {exc.message}")
    return _with_substrate(out, app, connection, manifest_doc)


def _with_substrate(out: dict, app: RegisteredApp, connection, manifest_doc) -> dict:
    out["substrate"] = substrate_status(
        app,
        deployed_commit=out["deployed_commit"],
        connection=connection,
        manifest_doc=manifest_doc,
        warnings=out["warnings"],
    )
    return out


def ingest_workflow_present(app: RegisteredApp) -> tuple[bool, str]:
    """Whether the brain's repository has the ingest workflow on its deploy
    branch, or why it cannot be told."""
    from astrolift_scm.providers import ProviderError, fetch_file
    from astrolift_scm.services.workflows import _pick_source_connection

    if app.source_kind not in _INGEST_HOSTS:
        return False, "ingest runs a named workflow; this brain's host dispatches pipelines without one"
    connection = _pick_source_connection(app)
    if connection is None:
        return False, "no source connection can read the repository"
    try:
        found = fetch_file(
            connection,
            repo_full_name=app.source_repo,
            path=INGEST_WORKFLOW,
            ref=app.deploy_branch or app.default_branch or "main",
        )
    except ProviderError as exc:
        return False, exc.message
    if found is None:
        return False, f"the repository has no {INGEST_WORKFLOW}"
    return True, ""


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def workflow_id(organization_guid: str, slug: str) -> str:
    return f"BrainCreateWorkflow-{organization_guid}-{slug}"


def preflight(
    *, organization, slug: str, name: str, repo: str, template_ref: str, selection, environment_name: str
) -> tuple[str, str, str] | None:
    """The refusals a creation would otherwise meet in ``BrainCreateWorkflow``
    after its repository exists, as ``(code, message, field)``: ``createBrain``
    answers them before it makes anything. They are the scaffold's (the
    edition at ``template_ref`` downloaded and verified, then the files
    rendered: a slug its schema cannot name, no portal Dockerfile) and
    ``registerApp``'s that depend only on the request (no managed cluster,
    the org's source policy, a taken namespace or hostname label). The steps
    keep their own checks, for a ref that moves in between."""
    from _sdk.k8s_naming import app_namespace
    from django.db.models import Q

    from astrolift_clusters.models import TenantCluster
    from astrolift_registry import brain_template
    from astrolift_registry.hostname_claims import hostname_label_refusal
    from astrolift_registry.namespaces import namespace_refusal
    from astrolift_scm.org_repo_policy import rejection_reason

    if not TenantCluster.objects.filter(
        Q(organization=organization) | Q(organization__isnull=True),
        deleted_at__isnull=True,
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    ).exists():
        return (
            "PRECONDITION",
            "No managed cluster connected. Visit /clusters and finish bringing a cluster into management "
            "before adding apps.",
            "",
        )
    refusal = rejection_reason(repo, organization)
    if refusal:
        return "VALIDATION", refusal, "repo"
    refusal = namespace_refusal(
        app_namespace(organization_slug=organization.slug, app_slug=slug), organization_id=organization.pk
    )
    if refusal:
        return "VALIDATION", refusal, "slug"
    refusal = hostname_label_refusal(slug, organization=organization)
    if refusal:
        return "CONFLICT", refusal, "slug"
    try:
        edition = brain_template.read_edition(brain_template.download(template_ref), ref=template_ref)
        brain_template.scaffold(
            edition, slug=slug, name=name, selection=selection, environment_name=environment_name
        )
    except brain_template.TemplateError as exc:
        return exc.code, exc.message, exc.field
    return None


def _load(creation_id: int) -> BrainCreation:
    return BrainCreation.objects.select_related(
        "organization", "project", "project__team", "created_by", "source_connection", "registered_app"
    ).get(pk=creation_id)


def _save(row: BrainCreation, **fields) -> None:
    for name, value in fields.items():
        setattr(row, name, value)
    row.save(update_fields=[*fields, "updated_at", "version"])


def fail(creation_id: int, code: str, message: str) -> dict[str, Any]:
    row = _load(creation_id)
    if row.state in (BrainCreation.State.READY, BrainCreation.State.FAILED):
        return {"state": row.state}
    _save(row, state=BrainCreation.State.FAILED, error_code=code[:32], error_message=message[:4000])
    log.info("brain creation %s failed: %s %s", row.guid, code, message)
    return {"state": row.state}


def _provider_code(exc) -> str:
    return {
        "PRECONDITION": "PRECONDITION",
        "CONFLICT": "CONFLICT",
        "VALIDATION": "VALIDATION",
        "NOT_FOUND": "NOT_FOUND",
        "AUTH_FAILED": "PRECONDITION",
    }.get(getattr(exc, "code", ""), "PRECONDITION")


def _connection(row: BrainCreation):
    conn = row.source_connection
    if conn is None or conn.deleted_at is not None or not conn.is_active:
        raise _StepFailed("PRECONDITION", "the source connection is gone; start the creation again")
    return conn


class _StepFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _step(fn):
    """Run one step; a refusal marks the creation failed instead of raising."""

    def run(creation_id: int) -> dict[str, Any]:
        from astrolift_registry.brain_template import TemplateError
        from astrolift_scm.providers import ProviderError

        row = _load(creation_id)
        if row.state == BrainCreation.State.FAILED:
            return {"state": row.state}
        try:
            fn(row)
        except _StepFailed as exc:
            return fail(creation_id, exc.code, exc.message)
        except TemplateError as exc:
            return fail(creation_id, exc.code, exc.message)
        except ProviderError as exc:
            if exc.code in ("NETWORK", "RATE_LIMITED", "API_ERROR"):
                raise  # transient: the activity retries
            return fail(creation_id, _provider_code(exc), exc.message)
        row.refresh_from_db()
        return {"state": row.state}

    run.__name__ = fn.__name__
    return run


@_step
def ensure_repo(row: BrainCreation) -> None:
    from astrolift_scm.providers.repo_writes import create_repository, repository_state

    if row.state != BrainCreation.State.PENDING:
        return
    conn = _connection(row)
    state = repository_state(conn, row.repo)
    if not state.exists:
        owner, name = row.repo.split("/", 1)
        state = create_repository(conn, owner, name, row.visibility)
    elif not state.empty:
        raise _StepFailed("CONFLICT", f"{row.repo} already has commits; use an empty or new repository")
    _save(row, state=BrainCreation.State.REPO_READY, branch=state.default_branch or "main")


def _author(user) -> dict[str, str] | None:
    email = (getattr(user, "email", "") or "").strip()
    if not email:
        return None
    name = (user.get_full_name() or "").strip() or user.get_username() or email
    return {"name": name, "email": email}


@_step
def scaffold(row: BrainCreation) -> None:
    from astrolift_registry import brain_template
    from astrolift_registry.brain_substrate import Selection
    from astrolift_scm.providers.repo_writes import commit_tree, repository_state

    if row.state != BrainCreation.State.REPO_READY:
        return
    conn = _connection(row)
    state = repository_state(conn, row.repo)
    if not state.empty:
        if row.commit_sha and state.head_sha == row.commit_sha:
            _save(row, state=BrainCreation.State.SCAFFOLDED)
            return
        raise _StepFailed("CONFLICT", f"{row.repo} gained commits before the scaffold landed")
    edition = brain_template.read_edition(brain_template.download(row.template_ref), ref=row.template_ref)
    files = brain_template.scaffold(
        edition,
        slug=row.slug,
        name=row.name,
        selection=Selection.from_dict(row.substrate),
        environment_name=row.environment_name,
    )
    version = f" {edition.version}" if edition.version else ""
    sha = commit_tree(
        conn,
        row.repo,
        state.default_branch or row.branch or "main",
        files,
        f"Create the {row.name} semantic layer from Mimwell{version} ({row.template_ref})",
        state=state,
        author=_author(row.created_by) if row.created_by_id else None,
    )
    _save(
        row, state=BrainCreation.State.SCAFFOLDED, commit_sha=sha, branch=state.default_branch or row.branch
    )


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))


@_step
def register(row: BrainCreation) -> None:
    from astrolift_graphql import GUID
    from astrolift_registry import brain_substrate
    from astrolift_registry.schema.mutations.registration import RegistrationMutations
    from astrolift_registry.schema.mutations.types import RegisterAppInput
    from core.tenancy import TenantContext, tenant_context

    if row.state != BrainCreation.State.SCAFFOLDED:
        return
    conn = _connection(row)
    selection = brain_substrate.Selection.from_dict(row.substrate)
    existing = RegisteredApp.objects.filter(organization_id=row.organization_id, slug=row.slug).first()
    if existing is not None:
        if row.registered_app_id == existing.pk:
            _save(row, state=BrainCreation.State.REGISTERED)
            return
        raise _StepFailed(
            "CONFLICT", f"an app with slug {row.slug!r} was registered while this brain was created"
        )
    if row.created_by is None:
        raise _StepFailed("PRECONDITION", "the person who asked for this brain is gone")
    branch = row.branch or "main"
    context = TenantContext(
        organization_id=row.organization_id, team_id=row.project.team_id, actor_user_id=row.created_by_id
    )
    with tenant_context(context):
        result = inspect.unwrap(RegistrationMutations.register_app)(
            RegistrationMutations(),
            _info(row.created_by),
            input=RegisterAppInput(
                project_id=GUID(str(row.project.guid)),
                name=row.name,
                slug=row.slug,
                source_kind=source_kind(conn),
                source_repo=row.repo,
                default_branch=branch,
                deploy_branch=branch,
                # A given image deploys as is; otherwise the platform builds the
                # edition's portal Dockerfile at each commit.
                build_mode=(
                    RegisteredApp.BuildMode.NONE
                    if selection.image
                    else RegisteredApp.BuildMode.PLATFORM_BUILD
                ).value,
                build_strategy=(
                    RegisteredApp.BuildStrategy.OFF
                    if selection.image
                    else RegisteredApp.BuildStrategy.DOCKERFILE
                ).value,
                dockerfile_path=brain_substrate.DOCKERFILE,
                build_context=".",
                manifest_raw=brain_substrate.render_manifest(
                    selection, slug=row.slug, display_name=row.name, environment_name=row.environment_name
                ),
            ),
        )
    if not result.ok or result.data is None:
        error = (result.errors or [None])[0]
        code = str(
            getattr(getattr(error, "code", None), "value", getattr(error, "code", "")) or "PRECONDITION"
        )
        raise _StepFailed(code, getattr(error, "message", "") or "registering the brain failed")
    app = RegisteredApp.objects.get(organization_id=row.organization_id, slug=row.slug)
    record_substrate(app, selection, user=row.created_by)
    _save(row, state=BrainCreation.State.REGISTERED, registered_app=app)


@_step
def deploy(row: BrainCreation) -> None:
    """Start the first deployment, or finish a creation that asked for none.
    Waits (returns in ``REGISTERED``) while the app is still provisioning."""
    from astrolift_lifecycle.deployment_identity_origin import (
        DeploymentOriginError,
        refuse_unsupported_origin,
    )
    from astrolift_lifecycle.models import AppEnvironment, Deployment
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import Actor, DeployAppInput

    if row.state != BrainCreation.State.REGISTERED:
        return
    if not row.deploy:
        _save(row, state=BrainCreation.State.READY)
        return
    app = RegisteredApp.objects.get(pk=row.registered_app_id)
    if app.provisioning_status == RegisteredApp.ProvisioningStatus.FAILED:
        raise _StepFailed("PRECONDITION", f"provisioning the app failed: {app.provisioning_error[:300]}")
    if app.provisioning_status != RegisteredApp.ProvisioningStatus.READY:
        return
    env = AppEnvironment.objects.filter(
        registered_app=app, name=row.environment_name, deleted_at__isnull=True
    ).first()
    if env is None:
        raise _StepFailed("PRECONDITION", f"the app has no {row.environment_name!r} environment")
    if env.deploys_paused:
        raise _StepFailed("PRECONDITION", f"environment {env.name!r} has deploys paused")
    try:
        refuse_unsupported_origin(app, env)
    except DeploymentOriginError as exc:
        raise _StepFailed("PRECONDITION", str(exc)) from exc
    pending = env.required_approvals == 0
    deployment = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user_id=row.created_by_id,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=(Deployment.Status.PENDING if pending else Deployment.Status.PENDING_APPROVAL).value,
        # A platform build tags the image with its commit; a given image deploys as declared.
        image_tag=row.commit_sha[:128] if app.build_mode == RegisteredApp.BuildMode.PLATFORM_BUILD else "",
        approvals_required=env.required_approvals,
        approvals_received=0,
        commit_sha=row.commit_sha,
        branch=row.branch or app.deploy_branch,
        commit_message="Create the semantic layer",
        ci_provider=app.source_kind or "",
    )
    _save(row, state=BrainCreation.State.DEPLOYING, deployment=deployment)
    if pending:
        start_workflow(
            "DeployAppWorkflow",
            args=[
                DeployAppInput(
                    registered_app_id=app.pk,
                    app_environment_id=env.pk,
                    deployment_id=deployment.pk,
                    image_tags={"app": deployment.image_tag},
                    trigger_kind=Deployment.TriggerKind.MANUAL.value,
                    actor=Actor(kind="user", user_id=row.created_by_id, display="createBrain"),
                    commit_sha=deployment.commit_sha,
                )
            ],
            workflow_id=f"DeployAppWorkflow-{app.guid}-{env.guid}",
        )


def observe(creation_id: int) -> dict[str, Any]:
    """Settle a deploying creation from its deployment: ``READY`` once it
    runs, ``FAILED`` when it fails. Returns the (possibly unchanged) state."""
    from astrolift_lifecycle.models import Deployment

    row = _load(creation_id)
    if row.state != BrainCreation.State.DEPLOYING or row.deployment_id is None:
        return {"state": row.state}
    status_value = Deployment.objects.filter(pk=row.deployment_id).values_list("status", flat=True).first()
    if status_value == Deployment.Status.RUNNING.value:
        _save(row, state=BrainCreation.State.READY)
    elif status_value in (
        Deployment.Status.FAILED.value,
        Deployment.Status.SUPERSEDED.value,
        Deployment.Status.ROLLED_BACK.value,
        None,
    ):
        return fail(creation_id, "PRECONDITION", f"the first deployment ended {status_value or 'missing'}")
    return {"state": row.state}


def visible_creation(creation_guid: str) -> BrainCreation | None:
    """A creation in the tenant the caller asked for, or whose brain the
    caller can read."""
    import uuid

    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None:
        return None
    try:
        guid = str(uuid.UUID(str(creation_guid)))
    except ValueError:
        return None
    row = (
        BrainCreation.objects.filter(guid=guid, organization_id=tenant.organization_id)
        .select_related("registered_app", "deployment")
        .first()
    )
    if row is None:
        return None
    if tenant.actor_user_id is not None and row.created_by_id == tenant.actor_user_id:
        return row
    if row.registered_app_id and find_brain(row.registered_app.slug) is not None:
        return row
    return None


# ---------------------------------------------------------------------------
# Surface: who is asking, for the audit row
# ---------------------------------------------------------------------------

_surface: contextvars.ContextVar[str] = contextvars.ContextVar("astrolift_brain_surface", default="graphql")


class surface:
    """``with surface("mcp"):`` marks the brain calls inside as an agent's."""

    def __init__(self, name: str):
        self.name = name
        self._token = None

    def __enter__(self):
        self._token = _surface.set(self.name)
        return self

    def __exit__(self, *exc):
        _surface.reset(self._token)
        return False


def audit_context() -> dict[str, str]:
    from astrolift_identity.api_tokens import get_current_api_token

    token = get_current_api_token()
    out = {"surface": _surface.get()}
    if token is not None:
        out["token"] = str(getattr(token, "guid", ""))
    return out


# ---------------------------------------------------------------------------
# Substrate
# ---------------------------------------------------------------------------


def record_substrate(app: RegisteredApp, selection, *, user=None):
    """Record ``selection`` as what was last applied to ``app``."""
    from astrolift_registry.models import BrainSubstrate

    fields = {
        **selection.cell,
        "persistence": list(selection.persistence),
        "replicas": selection.replicas,
        "image": selection.image,
        "port": selection.port,
        "updated_by": user,
    }
    row = BrainSubstrate.objects.filter(brain=app).first()
    if row is None:
        return BrainSubstrate.objects.create(
            organization_id=app.organization_id, brain=app, created_by=user, **fields
        )
    for name, value in fields.items():
        setattr(row, name, value)
    row.save()
    return row


def current_selection(app: RegisteredApp, config: dict | None = None):
    """What was last applied to ``app``; for a brain Astrolift did not create,
    read from its ``client.config.json`` (``deploy.*``) and the files cell."""
    from astrolift_registry.brain_substrate import Selection
    from astrolift_registry.models import BrainSubstrate

    row = BrainSubstrate.objects.filter(brain=app).first()
    if row is not None:
        return row.selection()
    deploy = (config or {}).get("deploy") or {}
    return Selection(
        persistence=tuple(p for p in deploy.get("persistence") or () if p != "none"),
        replicas=int(deploy.get("replicas") or 1),
        image=str(deploy.get("image") or ""),
        port=int(deploy.get("port") or 8080),
    )


def _read_json(connection, app: RegisteredApp, path: str, ref: str):
    import json

    raw = fetch_file_at(connection, app, path, ref)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def fetch_file_at(connection, app: RegisteredApp, path: str, ref: str):
    from astrolift_scm.providers import fetch_file

    return fetch_file(connection, repo_full_name=app.source_repo, path=path, ref=ref)


def substrate_status(
    app: RegisteredApp, *, deployed_commit: str, connection, manifest_doc=None, warnings: list[str]
) -> dict:
    """The cell asked for, what the brain has adopted at its running commit,
    and each resource the cell needs with its state."""
    from astrolift_registry.brain_substrate import cell_name, resources
    from astrolift_scm.providers import ProviderError

    config = None
    manifest = manifest_doc
    if connection is not None and deployed_commit:
        try:
            config = _read_json(connection, app, "client.config.json", deployed_commit)
        except ProviderError as exc:
            warnings.append(f"substrate: {exc.message}")
    selection = current_selection(app, config)
    authority = ((config or {}).get("brain") or {}).get("authority") or {}
    graduation = (((manifest or {}).get("substrate") or {}).get("graduation") or {}) if manifest else {}
    needed = graduation.get("needed") if isinstance(graduation, dict) else None
    return {
        **selection.cell,
        "cell": cell_name(selection.cell),
        "persistence": list(selection.persistence),
        "replicas": selection.replicas,
        "image": selection.image,
        "adopted_store": str(authority.get("store") or "") if isinstance(authority, dict) else "",
        "adopted": None if config is None else bool(authority),
        "graduation_needed": bool(needed) if needed is not None else None,
        "resources": resources(app, selection),
    }


class SubstrateChangeError(Exception):
    def __init__(self, code: str, message: str, *, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


def update_substrate(app: RegisteredApp, requested, *, user) -> dict:
    """Graduate a brain to ``requested`` (Mimwell's term): write the selection
    into its ``client.config.json`` and ``astrolift.toml`` in one commit on the
    deploy branch, then apply the manifest, which provisions what the cell
    needs. The push deploys it. Persistence that holds data is never dropped
    here: going down a tier is a knowledge-store cutover."""
    from astrolift_manifest.normalize import NormalizationDefaults, normalize
    from astrolift_manifest.parser import ManifestError, parse_raw
    from astrolift_manifest.persist import (
        actor_may_attach_project_services,
        allow_project_attach,
        persist_manifest,
    )
    from astrolift_registry import brain_substrate as bs
    from astrolift_registry.brain_template import CONFIG_SCHEMA, dump_config
    from astrolift_scm.providers import ProviderError
    from astrolift_scm.providers.repo_writes import TreeFile, update_files
    from astrolift_scm.services.workflows import _pick_source_connection

    if not app.source_repo:
        raise SubstrateChangeError("PRECONDITION", "the brain has no source repository")
    connection = _pick_source_connection(app)
    if connection is None:
        raise SubstrateChangeError("PRECONDITION", "no source connection can write the repository")
    branch = app.deploy_branch or app.default_branch or "main"
    try:
        config = _read_json(connection, app, "client.config.json", branch)
        current_manifest = fetch_file_at(connection, app, "astrolift.toml", branch) or app.manifest_raw or ""
    except ProviderError as exc:
        raise SubstrateChangeError("PRECONDITION", exc.message) from exc
    if not isinstance(config, dict):
        raise SubstrateChangeError("PRECONDITION", "the repository has no readable client.config.json")
    before = current_selection(app, config)
    try:
        after = bs.validate(requested)
    except bs.SubstrateError as exc:
        raise SubstrateChangeError("VALIDATION", exc.message, field=exc.field) from exc
    dropped = bs.dropped_data(before, after)
    if dropped:
        raise SubstrateChangeError(
            "PRECONDITION",
            f"dropping {', '.join(dropped)} would leave its data behind; moving down a tier is a "
            "knowledge-store cutover in the brain, not a substrate change",
            field="persistence",
        )
    if bool(after.image) != (app.build_mode == RegisteredApp.BuildMode.NONE):
        raise SubstrateChangeError(
            "PRECONDITION",
            "switching between a built brain and a given image means registering it again",
            field="image",
        )
    try:
        new_config = bs.apply_to_config(config, after, slug=app.slug)
        environments = list(app.environments.filter(deleted_at__isnull=True).values_list("name", flat=True))
        env = "production" if "production" in environments or not environments else sorted(environments)[0]
        manifest = bs.merge_manifest(
            current_manifest,
            bs.render_manifest(after, slug=app.slug, display_name=app.name, environment_name=env),
        )
        normalized = normalize(parse_raw(manifest), defaults=NormalizationDefaults())
    except bs.SubstrateError as exc:
        raise SubstrateChangeError("VALIDATION", exc.message, field=exc.field) from exc
    except ManifestError as exc:
        raise SubstrateChangeError("VALIDATION", f"the rendered manifest does not parse: {exc}") from exc
    if bs.adds_app_slug(config, new_config):
        # A Mimwell version without deploy.app_slug refuses the key and would
        # rename the app <client.slug>-brain on its next make astrolift-manifest.
        try:
            schema = _read_json(connection, app, CONFIG_SCHEMA, branch)
        except ProviderError as exc:
            raise SubstrateChangeError("PRECONDITION", exc.message) from exc
        if not bs.declares_app_slug(schema):
            raise SubstrateChangeError(
                "PRECONDITION",
                f"keeping the name {app.slug!r} needs deploy.app_slug, which this brain's Mimwell "
                "version does not have; update the brain's engine first",
                field="slug",
            )
    if new_config == config and manifest == current_manifest:
        record_substrate(app, after, user=user)
        return {"before": before, "after": after, "commit_sha": ""}
    try:
        sha = update_files(
            connection,
            app.source_repo,
            branch,
            [
                TreeFile("client.config.json", dump_config(new_config)),
                TreeFile("astrolift.toml", manifest.encode("utf-8")),
            ],
            f"Graduate the substrate: {bs.cell_name(after.cell)}; persistence "
            f"{', '.join(after.persistence) or 'none'}; {after.replicas} replica(s)",
            author=_author(user) if user is not None else None,
        )
    except ProviderError as exc:
        raise SubstrateChangeError("PRECONDITION", exc.message) from exc
    with allow_project_attach(actor_may_attach_project_services(app)):
        persist_manifest(app, normalized, raw_text=manifest)
    record_substrate(app, after, user=user)
    return {"before": before, "after": after, "commit_sha": sha}


def rebuild_projections(app: RegisteredApp) -> list[str]:
    """What a redeploy rebuilds, from the brain's substrate."""
    selection = current_selection(app)
    out = ["the projections the brain rebuilds at deploy"]
    if "sqlite-volume" in selection.persistence:
        out.append(f"app/brain.db, rebuilt by each of {selection.replicas} replica(s) on its own /data claim")
    return out
