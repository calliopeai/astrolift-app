"""
BuildImageActivity — build a container image from source (#865, #867).

Invoked by ``DeployAppWorkflow`` when ``RegisteredApp.build_strategy`` is not
``"off"``. The activity locates the app's source connection, constructs a
``BuildSpec``, and dispatches through the cluster's registered
``BuildDriver`` plugin.

Until a real cluster-bound BuildDriver plugin lands, the activity falls
back to a no-op stub that logs the request and returns the caller-supplied
``image_tag`` unchanged. The stub signals its intent via the returned dict
so callers can detect whether a real build was performed:

    {"ok": True, "image_ref": "<image_tag>", "stub": True}

A real driver sets ``"stub": False`` and populates ``"digest"``.

Heartbeat contract: the activity heartbeats on entry and once per driver
poll cycle. Temporal's default heartbeat timeout is not set here — callers
set ``heartbeat_timeout`` on the ``execute_activity`` call to match the
expected build duration (build times vary widely by strategy and repo size;
dockerfile builds on a cold runner can take 10+ minutes).
"""

from __future__ import annotations

import dataclasses
import logging

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.build_image")


@dataclasses.dataclass(slots=True, frozen=True)
class BuildImageInput:
    """Payload for ``build_image``.

    ``app_guid`` — the RegisteredApp's GUID (string form of UUID).
    ``image_tag`` — the desired output image tag (e.g. ``registry/repo:sha``).
    ``commit_sha`` — the git commit SHA to check out and build from.
    """

    app_guid: str
    image_tag: str
    commit_sha: str


def _build_image_sync(inp: BuildImageInput) -> dict:
    """Resolve the app, attempt a build via the registered BuildDriver.

    Falls back to a no-op stub when no BuildDriver plugin is wired to
    the app's cluster — this keeps the workflow runnable in environments
    where the build infrastructure is not yet provisioned.
    """
    from astrolift_registry.models import RegisteredApp

    try:
        app = RegisteredApp.objects.select_related(
            "organization",
            "default_tenant_cluster",
        ).get(guid=inp.app_guid, deleted_at__isnull=True)
    except RegisteredApp.DoesNotExist:
        raise RuntimeError(f"RegisteredApp with guid={inp.app_guid!r} not found")

    build_strategy = app.build_strategy or "off"
    if build_strategy == "off":
        # Caller should not reach here when build_strategy is "off", but be
        # defensive and return early rather than erroring.
        log.warning(
            "build_image called for app %s with build_strategy='off' — skipping",
            inp.app_guid,
        )
        return {"ok": True, "image_ref": inp.image_tag, "stub": True}

    cluster = app.default_tenant_cluster
    build_driver = _resolve_build_driver(cluster)

    if build_driver is None:
        log.info(
            "no BuildDriver registered for cluster %s (app=%s build_strategy=%s) — using stub",
            getattr(cluster, "slug", "none"),
            inp.app_guid,
            build_strategy,
        )
        return {"ok": True, "image_ref": inp.image_tag, "stub": True}

    source_url = _resolve_source_url(app, inp.commit_sha)
    log.info(
        "build_image start app=%s build_strategy=%s source_url=%s image_tag=%s",
        inp.app_guid,
        build_strategy,
        source_url,
        inp.image_tag,
    )

    try:
        import asyncio

        image_ref = asyncio.get_event_loop().run_until_complete(
            build_driver.build(
                source_url=source_url,
                context_path=".",
                dockerfile_path="Dockerfile",
                image_tag=inp.image_tag,
            )
        )
    except Exception as exc:
        log.error(
            "build_image failed app=%s: %s",
            inp.app_guid,
            exc,
        )
        raise RuntimeError(f"BuildDriver.build failed: {exc}") from exc

    log.info("build_image success app=%s image_ref=%s", inp.app_guid, image_ref)
    return {"ok": True, "image_ref": image_ref or inp.image_tag, "stub": False}


def _resolve_build_driver(cluster):
    """Look up the BuildDriver for the given TenantCluster.

    Returns ``None`` when no plugin or no BuildDriver capability is wired.
    """
    if cluster is None:
        return None

    try:
        from astrolift_drivers.registry import plugins
        from core.cluster_observability import _config_for  # type: ignore[attr-defined]

        plugin = plugins.get(getattr(cluster, "provider_plugin_id", None))
        if plugin is None:
            return None
        if not hasattr(plugin, "build_driver"):
            return None
        config = _config_for(cluster)
        return plugin.build_driver(config)
    except Exception as exc:  # noqa: BLE001
        log.debug("_resolve_build_driver: could not load driver: %s", exc)
        return None


def _resolve_source_url(app, commit_sha: str) -> str:
    """Build a best-effort source URL for the BuildDriver.

    Prefers a git+https form from the SCM SourceConnection when available;
    falls back to the raw ``source_repo`` + commit SHA.
    """
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
        if conn is not None and hasattr(conn, "clone_url"):
            url = conn.clone_url or ""
            if url:
                return f"git+{url}#{commit_sha}" if commit_sha else f"git+{url}"
    except Exception:  # noqa: BLE001
        pass

    repo = (app.source_repo or "").strip()
    if repo and commit_sha:
        return f"git+https://github.com/{repo}#{commit_sha}"
    if repo:
        return f"git+https://github.com/{repo}"
    return ""


def _fetch_app_build_strategy_sync(registered_app_id: int) -> str:
    """Return the ``build_strategy`` value for the given RegisteredApp PK."""
    from astrolift_registry.models import RegisteredApp

    try:
        app = RegisteredApp.all_objects.only("build_strategy").get(pk=registered_app_id)
        return app.build_strategy or "off"
    except RegisteredApp.DoesNotExist:
        return "off"


@activity.defn(name="astrolift.build.fetch_app_build_strategy")
async def fetch_app_build_strategy(registered_app_id: int) -> str:
    """Return the ``build_strategy`` for a RegisteredApp.

    Called by ``DeployAppWorkflow`` before the build step so the workflow
    can branch without embedding Django model access in workflow code.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_fetch_app_build_strategy_sync)(registered_app_id)


@activity.defn(name="astrolift.build.build_image")
async def build_image(inp: BuildImageInput) -> dict:
    """Build a container image from source via the registered BuildDriver.

    Returns ``{"ok": bool, "image_ref": str, "stub": bool}``.
    ``stub=True`` means no real build was performed (driver not wired or
    ``build_strategy`` is "off"). ``image_ref`` is always the usable image
    reference — callers can treat it as the image to deploy regardless of
    whether a real build ran.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_build_image_sync)(inp)
    activity.heartbeat()
    return result
