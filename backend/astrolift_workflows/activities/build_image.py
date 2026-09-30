"""
BuildImageActivity — build a container image from source (#865, #867, #978).

Invoked by ``DeployAppWorkflow`` when ``RegisteredApp.build_strategy`` is not
``"off"``. The activity resolves the deployment's app + cluster, ensures the
app's registry repo + a registry-push identity role exist, then dispatches
the build through the cluster's ``BuildDriver`` (kaniko, in-cluster).

The build pushes to ``<registry_repo_uri>:<image_tag>`` — exactly the ref
``render_resources_for_deployment`` deploys (``{image_repository}:{image_tag}``)
— so a successful build flows straight into the rollout with no rewrite. The
pushed digest is read back from the registry and recorded on the Deployment
row (``image_digest``), which is the ``stub:false`` signal callers check.

When the app's cluster can't run a real build (no driver wired, non-AWS
provider whose registry-push identity isn't supported yet, or missing AWS
account id), the activity falls back to a no-op stub that returns the
caller-supplied ``image_tag`` unchanged — keeping the workflow runnable in
environments where build infrastructure isn't provisioned:

    {"ok": True, "image_ref": "<image_tag>", "stub": True}

A real build sets ``"stub": False`` and populates ``"digest"``.

Heartbeat contract: the activity heartbeats on entry; the kaniko driver
heartbeats once per poll cycle while the build Job runs (via
``maybe_heartbeat``). Callers set ``heartbeat_timeout`` on the
``execute_activity`` call to match the expected build duration (dockerfile
builds on a cold runner can take 10+ minutes).
"""

from __future__ import annotations

import dataclasses
import logging
import posixpath

from temporalio import activity

from astrolift_workflows.activities.image_digest import is_digest

log = logging.getLogger("astrolift_workflows.activities.build_image")

# Where the kaniko build Job runs — the platform namespace present on every
# managed cluster. Imported lazily inside the sync path to keep the providers
# tree out of the Temporal workflow-sandbox import graph.
_BUILD_NAMESPACE = "astrolift-system"


@dataclasses.dataclass(slots=True, frozen=True)
class BuildImageInput:
    """Payload for ``build_image``.

    ``deployment_id`` — the Deployment PK the build is for. The activity
    resolves the app, cluster, registry repo, and writes the pushed digest
    back onto this row.
    ``image_tag`` — the desired output image tag (the build pushes
    ``<registry_repo_uri>:<image_tag>``).
    ``commit_sha`` — the git commit SHA to check out and build from; empty
    falls back to the app's default branch.
    """

    deployment_id: int
    image_tag: str
    commit_sha: str


@dataclasses.dataclass(slots=True, frozen=True)
class _PreparedBuild:
    """Everything the activity needs to run + finalize one kaniko build."""

    driver: object
    registry_driver: object
    repo_name: str
    repo_uri: str


def _stub(image_tag: str) -> dict:
    return {"ok": True, "image_ref": image_tag, "stub": True}


def _resolve_build_paths(app, deployment) -> tuple[str, str]:
    """Resolve the (dockerfile_path, build_context) pair a build should use.

    ``[[workloads.containers]]`` accepts per-container ``dockerfile_path`` /
    ``build_context`` (documented in the manifest reference) and ``persist.py``
    writes them onto the ``Container`` row, but the build only ever read
    ``RegisteredApp.dockerfile_path`` / ``build_context`` -- the app-level
    values set by ``astro app register --dockerfile-path/--build-context`` or
    derived by monorepo discovery. A manifest that set a container's
    ``build_context`` to reach a Dockerfile living above its own directory
    (the ConflictHQ/bdr#139 shape: two registrations of one repo) passed
    validation and was silently ignored (#1756).

    Precedence: an app-level field away from the parser's default was set
    deliberately -- an explicit register/update flag -- and wins outright;
    the manifest's container-level fields only get a say on the field(s)
    the app hasn't itself customized (adversarial review, #1756 follow-up:
    an explicit app-level choice must not be silently overridden by
    whatever a manifest, possibly authored by someone else, declares).
    A container value is resolved as an offset from the manifest's own
    directory (``dirname(app.manifest_path)``) and normalized
    (``posixpath``) -- the shape the issue's own example uses (``"../.."``
    from a manifest two directories deep reaching the repo root); a result
    that would climb above the repo root (or is itself absolute) is a hard
    failure, not a silent fall-back to the app-level value, so a wrong
    build never runs at all.

    Kaniko does not read ``--dockerfile`` from the repo root. It first
    tries the path against its own working directory (``/workspace``) and
    only then joins it onto the build context (``--context-sub-path``)
    (``resolveDockerfilePath`` in kaniko's ``cmd/executor/cmd/root.go``).
    So the Dockerfile is handed over relative to the effective build
    context (as a repo-root path it doubled the prefix under any context
    but the root: ``apps/web`` + ``apps/web/Dockerfile.prod``), and it must
    sit inside that context. A ``../`` path would be tried against the
    working directory first, where ``../../var/run/secrets/...`` is the
    build pod's own service-account token, which kaniko would then read as
    the Dockerfile. An app-level ``dockerfile_path`` is relative to
    the app-level build context, which is the repo root whenever a
    container's ``build_context`` can apply, so it is rebased the same way.

    Prefers ``deployment.workload``'s primary container when the deployment
    is scoped to one (task / static-site / cron paths set this); otherwise
    falls back to the app's first workload, mirroring the same "one build
    for the whole app" simplification ``DeployAppWorkflow`` already applies
    to ``image_tags`` (v1: one image, shared by every workload).
    """
    from astrolift_manifest.path_safety import resolve_repo_relative
    from astrolift_manifest.types import DEFAULT_BUILD_CONTEXT, DEFAULT_DOCKERFILE_PATH

    dockerfile_path = app.dockerfile_path or DEFAULT_DOCKERFILE_PATH
    build_context = app.build_context or DEFAULT_BUILD_CONTEXT
    dockerfile_is_default = dockerfile_path == DEFAULT_DOCKERFILE_PATH
    context_is_default = build_context == DEFAULT_BUILD_CONTEXT

    if not (dockerfile_is_default or context_is_default):
        # Both app-level fields were customized -- nothing left for a
        # container to override.
        return dockerfile_path, build_context

    workload = deployment.workload
    if workload is None:
        workload = app.workloads.filter(deleted_at__isnull=True).order_by("created_at", "pk").first()
    if workload is None:
        return dockerfile_path, build_context

    primary = workload.containers.filter(is_primary=True, deleted_at__isnull=True).first()
    if primary is None:
        return dockerfile_path, build_context

    manifest_dir = posixpath.dirname(app.manifest_path or "") or DEFAULT_BUILD_CONTEXT

    # The Dockerfile as a repo-root path, rebased onto the context below.
    # None leaves the default "Dockerfile", which is context-relative already.
    dockerfile_in_repo = None
    if not dockerfile_is_default:
        dockerfile_in_repo = resolve_repo_relative(DEFAULT_BUILD_CONTEXT, dockerfile_path)
        if dockerfile_in_repo is None:
            raise RuntimeError(
                f"app dockerfile_path {dockerfile_path!r} escapes the repository root -- refusing to build"
            )
    elif primary.dockerfile_path and primary.dockerfile_path != DEFAULT_DOCKERFILE_PATH:
        dockerfile_in_repo = resolve_repo_relative(manifest_dir, primary.dockerfile_path)
        if dockerfile_in_repo is None:
            raise RuntimeError(
                f"container dockerfile_path {primary.dockerfile_path!r} resolved against "
                f"{manifest_dir!r} escapes the repository root -- refusing to build"
            )

    if context_is_default and primary.build_context and primary.build_context != DEFAULT_BUILD_CONTEXT:
        resolved = resolve_repo_relative(manifest_dir, primary.build_context)
        if resolved is None:
            raise RuntimeError(
                f"container build_context {primary.build_context!r} resolved against "
                f"{manifest_dir!r} escapes the repository root -- refusing to build"
            )
        build_context = resolved

    if dockerfile_in_repo is not None:
        # Both are repo-root paths; anchoring them at "/" keeps relpath from
        # consulting this process's working directory.
        dockerfile_path = posixpath.relpath(f"/{dockerfile_in_repo}", f"/{build_context}")
        if dockerfile_path == ".." or dockerfile_path.startswith("../"):
            raise RuntimeError(
                f"dockerfile {dockerfile_in_repo!r} is outside the build context {build_context!r} "
                "-- kaniko would resolve it against its own working directory first; "
                "refusing to build"
            )

    return dockerfile_path, build_context


def _build_image_sync(inp: BuildImageInput) -> dict:
    """Resolve the deployment, attempt a real build via the BuildDriver.

    Falls back to a no-op stub when the cluster can't run a build — this
    keeps the workflow runnable where build infrastructure isn't yet
    provisioned.
    """
    from astrolift_lifecycle.models import Deployment

    try:
        deployment = Deployment.objects.select_related(
            "registered_app__organization",
            "app_environment__tenant_cluster__provider_plugin",
        ).get(pk=inp.deployment_id)
    except Deployment.DoesNotExist:
        raise RuntimeError(f"Deployment with pk={inp.deployment_id!r} not found") from None

    app = deployment.registered_app
    build_strategy = app.effective_build_strategy
    if build_strategy == "off":
        # Caller should not reach here, but be defensive and return early
        # rather than erroring. Reads the effective strategy so an app
        # whose build_mode does not ask for a platform build is skipped
        # here too, not just at the workflow branch (#1687).
        log.warning(
            "build_image called for deployment %s (app=%s) with build_mode=%s "
            "build_strategy=%s — nothing to build, skipping",
            inp.deployment_id,
            app.slug,
            app.build_mode,
            app.build_strategy,
        )
        return _stub(inp.image_tag)

    from core.app_deploy import AppDeployError, cluster_for_deployment

    try:
        cluster = cluster_for_deployment(deployment)
    except AppDeployError as exc:
        log.info("build_image: no cluster for deployment %s (%s) — using stub", inp.deployment_id, exc)
        return _stub(inp.image_tag)

    prepared = _prepare_build(app, cluster, deployment.pk, inp.image_tag, inp.commit_sha)
    if prepared is None:
        log.info(
            "no BuildDriver available for cluster %s (app=%s build_strategy=%s) — using stub",
            getattr(cluster, "slug", "none"),
            app.slug,
            build_strategy,
        )
        return _stub(inp.image_tag)

    source_url = _resolve_source_url(app, inp.commit_sha)
    from providers._sdk.build import BuildSpec  # noqa: PLC0415

    dockerfile_path, build_context = _resolve_build_paths(app, deployment)
    spec = BuildSpec(
        source_uri=source_url,
        dockerfile_path=dockerfile_path,
        context_path=build_context,
        build_args={str(k): str(v) for k, v in (app.build_args or {}).items()},
    )
    log.info(
        "build_image start deployment=%s app=%s strategy=%s source=%s dest=%s:%s",
        inp.deployment_id,
        app.slug,
        build_strategy,
        source_url,
        prepared.repo_uri,
        inp.image_tag,
    )

    from astrolift_lifecycle.run_history import record

    record(deployment.pk, "build", "started")
    try:
        result = prepared.driver.build(spec, prepared.repo_uri, inp.image_tag)
    except Exception as exc:
        record(deployment.pk, "build", "failed", "Build driver failed.")
        log.error("build_image failed deployment=%s: %s", inp.deployment_id, exc)
        raise RuntimeError(f"BuildDriver.build failed: {exc}") from exc

    if not getattr(result, "success", False):
        parts = [str(e) for e in (getattr(result, "errors", []) or [])] or ["unknown build failure"]
        # The Job condition is the first entry and the only single-line
        # one; the driver appends the build pod's own output after it
        # (#1686). ``aborted_reason`` keeps one line, so the full text is
        # persisted on the deploy row rather than truncated away there.
        _record_build_failure(deployment, "\n".join(parts))
        record(deployment.pk, "build", "failed", "\n".join(parts))
        raise RuntimeError(f"image build failed: {parts[0]}")

    record(deployment.pk, "build", "completed", "Build/push job reported successful completion.")
    record(
        deployment.pk,
        "push",
        "completed",
        "Build/push job confirmed registry push; separate push start unavailable.",
    )
    digest = result.digest or _read_pushed_digest(prepared.registry_driver, prepared.repo_name, inp.image_tag)
    _record_build_outcome(deployment, image_tag=inp.image_tag, digest=digest)

    log.info(
        "build_image success deployment=%s image=%s digest=%s",
        inp.deployment_id,
        result.image_uri,
        digest or "(unavailable)",
    )
    return {"ok": True, "image_ref": result.image_uri, "digest": digest, "stub": False}


# ECR push needs an auth token (account-wide; not resource-scopable) plus the
# layer push + image put actions scoped to the one repo, and the read actions
# kaniko uses to pull cache layers / a base image from the same repo.
_ECR_PUSH_ACTIONS = [
    "ecr:BatchCheckLayerAvailability",
    "ecr:GetDownloadUrlForLayer",
    "ecr:BatchGetImage",
    "ecr:InitiateLayerUpload",
    "ecr:UploadLayerPart",
    "ecr:CompleteLayerUpload",
    "ecr:PutImage",
]


def _prepare_build(app, cluster, deployment_pk: int, image_tag: str, commit_sha: str):
    """Provision the registry repo + push identity, return a ``_PreparedBuild``.

    Returns ``None`` (→ stub) when the cluster can't run a real build:
    a non-AWS provider whose in-cluster registry-push identity isn't wired
    yet, or a missing AWS account id. The kaniko driver itself is
    cloud-agnostic; the registry-push role minted here is the AWS/IRSA path
    (mirrors the workload-identity provisioning the deploy does for S3).
    """
    from core.app_deploy import (
        AppDeployError,
        build_identity_role_name,
        driver_for_capability,
    )
    from core.cluster_management import (
        ClusterManagementError,
        _context_for_cluster,
        _driver_for_cluster,
    )

    plugin_slug = getattr(getattr(cluster, "provider_plugin", None), "slug", "")
    if plugin_slug != "aws":
        # Only the AWS/EKS registry-push identity path is wired today.
        return None

    pc = cluster.provider_config or {}
    account_id = str(pc.get("account_id", ""))
    region = str(pc.get("region", (cluster.auth_config or {}).get("region", cluster.region or "")))
    if not account_id:
        return None

    # Registry repo (idempotent) — same canonical name onboarding uses.
    try:
        registry_driver = driver_for_capability(cluster, "registry")
    except AppDeployError:
        return None
    repo_name = (
        app.registry_repo_uri.split("/", 1)[1]
        if "/" in (app.registry_repo_uri or "")
        else (f"{app.organization.slug}/{app.slug}")
    )
    repo = registry_driver.ensure_repo(repo_name)
    repo_uri = repo.uri
    if not (app.registry_repo_uri or "").strip():
        app.registry_repo_uri = repo_uri
        app.save(update_fields=["registry_repo_uri", "updated_at", "version"])

    # The cluster's OIDC issuer must be populated for the IRSA trust policy.
    _ensure_cluster_oidc_issuer(cluster)

    # Mint (or refresh) a net-new, build-scoped IRSA role and bind it to the
    # build ServiceAccount in the platform namespace. Distinct from the app's
    # runtime workload-identity role: this one grants ECR push to the app's
    # repo, not the runtime managed-service grants.
    identity_driver = driver_for_capability(cluster, "identity")
    sa_name = build_identity_role_name(app)
    repo_arn = f"arn:aws:ecr:{region}:{account_id}:repository/{repo_name}"
    permissions = [
        {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
        {"Effect": "Allow", "Action": list(_ECR_PUSH_ACTIONS), "Resource": repo_arn},
    ]
    identity_driver.create_identity_role(sa_name, permissions)
    annotation = identity_driver.bind_service_account(cluster.slug, _BUILD_NAMESPACE, sa_name, sa_name)
    role_arn = annotation.get("eks.amazonaws.com/role-arn", "")

    try:
        cluster_driver = _driver_for_cluster(cluster)
    except ClusterManagementError:
        return None
    ctx = _context_for_cluster(cluster)

    from providers.k8s_native.build_kaniko import KanikoBuildDriver  # noqa: PLC0415

    git_username, git_password = _clone_credential(app)
    driver = KanikoBuildDriver(
        cluster_driver=cluster_driver,
        cluster_slug=ctx.slug,
        service_account=sa_name,
        service_account_role_arn=role_arn,
        namespace=_BUILD_NAMESPACE,
        build_id=f"{deployment_pk}-{(commit_sha or image_tag)}",
        git_username=git_username,
        git_password=git_password,
        log_observer=lambda message: _record_build_output(deployment_pk, message),
    )
    return _PreparedBuild(
        driver=driver,
        registry_driver=registry_driver,
        repo_name=repo_name,
        repo_uri=repo_uri,
    )


def _ensure_cluster_oidc_issuer(cluster) -> None:
    """Populate ``cluster.auth_config['cluster_oidc_issuer']`` if absent.

    Mirrors the workload-identity path: rather than require the issuer to be
    set out-of-band, discover it from EKS and cache it on the row, so the
    IRSA trust policy isn't malformed by an empty issuer. No-op when already
    set or when the provider isn't AWS.
    """
    ac = cluster.auth_config or {}
    if ac.get("cluster_oidc_issuer"):
        return
    if getattr(getattr(cluster, "provider_plugin", None), "slug", "") != "aws":
        return
    from aws.identity_irsa import discover_oidc_issuer  # noqa: PLC0415

    pc = cluster.provider_config or {}
    region = str(pc.get("region", ac.get("region", cluster.region or "")))
    cluster_name = str(pc.get("cluster_name", ac.get("cluster_name", cluster.slug)))
    from core.cluster_credentials import credential_for_cluster

    issuer = discover_oidc_issuer(region, cluster_name, credential=credential_for_cluster(cluster))
    if not issuer:
        return
    new_ac = dict(ac)
    new_ac["cluster_oidc_issuer"] = issuer
    cluster.auth_config = new_ac
    cluster.save(update_fields=["auth_config"])


def _read_pushed_digest(registry_driver, repo_name: str, image_tag: str) -> str:
    """Return the ``sha256:...`` digest the build pushed for ``image_tag``.

    Reads it from the registry (``list_tags``) — the driver doesn't query
    the registry itself, so this is the authoritative source. Best-effort:
    a read failure leaves the digest empty rather than failing a build that
    actually succeeded.
    """
    try:
        for tag in registry_driver.list_tags(repo_name):
            if getattr(tag, "name", "") == image_tag and getattr(tag, "digest", ""):
                if not is_digest(tag.digest):
                    # The render pins containers to whatever lands here, so a
                    # malformed digest would produce an unpullable image ref.
                    # Better to fall back to the tag than to ship garbage.
                    log.warning(
                        "registry returned malformed digest %r for %s:%s — ignoring",
                        tag.digest,
                        repo_name,
                        image_tag,
                    )
                    return ""
                return tag.digest
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read pushed digest for %s:%s — %s", repo_name, image_tag, exc)
    return ""


def _record_build_outcome(deployment, *, image_tag: str, digest: str) -> None:
    """Persist the built image's tag + digest on the Deployment row.

    ``image_tag`` already drives the render (``deployment.image_tag``); the
    digest is the content-addressable record the UI surfaces as ``stub:false``
    proof and rollback pins to."""
    fields: list[str] = []
    if deployment.image_tag != image_tag:
        deployment.image_tag = image_tag
        fields.append("image_tag")
    if digest and deployment.image_digest != digest:
        deployment.image_digest = digest
        fields.append("image_digest")
    if fields:
        fields += ["updated_at", "version"]
        deployment.save(update_fields=fields)


def _clone_credential(app) -> tuple[str, str]:
    """A (username, password) pair the builder can clone ``app``'s repo with.

    The build pod got a bare clone URL and no credential, so on any
    private repo kaniko could not clone and the pod died immediately
    (#1685) -- reported as "Job has reached the specified backoff limit"
    and nothing more. The install already holds a GitHub App that can
    mint an installation token with ``contents: read``; the build path
    simply never asked for one.

    ``x-access-token`` is the username GitHub documents for an
    installation token, and works for a PAT through the same basic-auth
    pair. Returns empty strings when there is nothing to mint from -- a
    public repo clones anonymously exactly as before, and a private one
    fails with the clone error in its logs rather than a credential the
    platform guessed at.
    """

    if not (app.source_repo or "").strip():
        return "", ""
    if (app.source_kind or "") != "github":
        # Only the GitHub token path is wired; other hosts keep the
        # anonymous clone until their provider grows the same surface.
        return "", ""
    try:
        # The same picker the manifest fetch uses -- reading this app's
        # repo is the operation, and it already prefers the org App over
        # an OAuth user or a PAT.
        from astrolift_registry.services.manifest_sync import _pick_source_connection
        from astrolift_scm.providers.github import _token as github_token

        connection = _pick_source_connection(app)
        if connection is None:
            return "", ""
        token = github_token(connection)
    except Exception:
        log.warning(
            "build_image: could not mint a clone credential for app=%s — " "the build will clone anonymously",
            app.slug,
            exc_info=True,
        )
        return "", ""
    return ("x-access-token", token) if token else ("", "")


def _record_build_output(deployment_id: int, message: str) -> None:
    from astrolift_lifecycle.run_history import record

    record(deployment_id, "build", "output", message)


def _record_build_failure(deployment, detail: str) -> None:
    """Persist why the build failed, pod output included (#1686).

    Best-effort: the deploy is already failing, and losing the
    diagnostic must not replace that failure with a different one.
    """

    try:
        deployment.build_error = detail
        deployment.save(update_fields=["build_error", "updated_at", "version"])
    except Exception:
        log.warning("could not persist build_error for deployment %s", deployment.pk, exc_info=True)


def _resolve_source_url(app, commit_sha: str) -> str:
    """Build a git source URL (``git+https://host/path#<ref>``) for the build.

    Prefers a clone URL from the SCM SourceConnection; falls back to
    ``source_repo`` on GitHub. The ref is the commit SHA when known, else the
    app's default branch (as a ``refs/heads/...`` ref kaniko can fetch).
    """
    ref = commit_sha.strip() if commit_sha else ""
    if not ref:
        branch = (getattr(app, "default_branch", "") or "main").strip()
        ref = f"refs/heads/{branch}"

    try:
        from astrolift_scm.models import SourceConnection

        conn = (
            SourceConnection.objects.filter(
                organization=app.organization,
                repo_full_name=app.source_repo,
                deleted_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if conn is not None and getattr(conn, "clone_url", ""):
            return f"git+{conn.clone_url}#{ref}"
    except Exception:  # noqa: BLE001
        pass

    repo = (app.source_repo or "").strip()
    if repo:
        return f"git+https://github.com/{repo}#{ref}"
    return ""


def _fetch_app_build_strategy_sync(registered_app_id: int) -> str:
    """Return the *effective* build strategy for the given RegisteredApp PK.

    Reads both build axes via ``RegisteredApp.effective_build_strategy``:
    an app whose ``build_mode`` is not ``platform_build`` resolves to
    ``off`` however its ``build_strategy`` column happens to read
    (#1687).
    """
    from astrolift_registry.models import RegisteredApp

    try:
        app = RegisteredApp.all_objects.only("build_mode", "build_strategy").get(pk=registered_app_id)
        return app.effective_build_strategy
    except RegisteredApp.DoesNotExist:
        return "off"


@activity.defn(name="astrolift.build.fetch_app_build_strategy")
async def fetch_app_build_strategy(registered_app_id: int) -> str:
    """Return the effective build strategy for a RegisteredApp.

    Called by ``DeployAppWorkflow`` before the build step so the workflow
    can branch without embedding Django model access in workflow code.
    Effective, not raw: ``off`` unless the app's ``build_mode`` actually
    asks the platform to build (#1687).
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_fetch_app_build_strategy_sync)(registered_app_id)


@activity.defn(name="astrolift.build.build_image")
async def build_image(inp: BuildImageInput) -> dict:
    """Build a container image from source via the registered BuildDriver.

    Returns ``{"ok": bool, "image_ref": str, "stub": bool, "digest"?: str}``.
    ``stub=True`` means no real build was performed (no driver available or
    ``build_strategy`` is "off"). ``image_ref`` is always the usable image
    reference — callers can treat it as the image to deploy regardless of
    whether a real build ran.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_build_image_sync)(inp)
    activity.heartbeat()
    return result
