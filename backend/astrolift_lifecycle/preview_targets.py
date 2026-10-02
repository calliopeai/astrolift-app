"""Persisted preview/environment identity, without hostname or name inference."""

from dataclasses import dataclass

from astrolift_lifecycle.visibility import cluster_owned_and_live
from core.cluster_observability import namespace_for_app


@dataclass(frozen=True)
class PreviewTarget:
    preview_id: str
    preview_version: int
    app_id: str
    app_version: int
    app_slug: str
    environment_id: str
    environment_version: int
    environment_name: str
    cluster_id: str
    cluster_version: int
    namespace: str


def preview_binding(preview) -> tuple[str, PreviewTarget | None]:
    """Return available/retired/unavailable using only the actual relation.

    An unavailable binding exposes no foreign environment metadata. A retired
    binding records its owned historical target but cannot authorize routing.
    Callers select_related the app/organization and environment/cluster.
    """
    app = preview.registered_app
    environment = preview.app_environment if preview.app_environment_id else None
    if (
        environment is None
        or environment.registered_app_id != preview.registered_app_id
        or app.deleted_at is not None
        or app.organization.deleted_at is not None
    ):
        return "unavailable", None
    cluster = environment.tenant_cluster
    namespace = (environment.k8s_namespace or "").strip()
    if (
        not namespace
        or namespace != preview.namespace
        or namespace == namespace_for_app(app)
        or not cluster_owned_and_live(cluster, app.organization_id)
    ):
        return "unavailable", None
    target = PreviewTarget(
        preview_id=str(preview.guid),
        preview_version=int(preview.version),
        app_id=str(app.guid),
        app_version=int(app.version),
        app_slug=app.slug,
        environment_id=str(environment.guid),
        environment_version=int(environment.version),
        environment_name=environment.name,
        cluster_id=str(cluster.guid),
        cluster_version=int(cluster.version),
        namespace=namespace,
    )
    retired = (
        preview.deleted_at is not None
        or environment.deleted_at is not None
        or preview.status == "torn_down"
        or preview.torn_down_at is not None
    )
    return ("retired" if retired else "available"), target


def check_preview_target(preview, *, environment_id, preview_version, environment_version):
    """Require the reviewed FK and versions before any log/deployment route."""
    state, target = preview_binding(preview)
    if (
        state != "available"
        or target is None
        or str(environment_id) != target.environment_id
        or preview_version != target.preview_version
        or environment_version != target.environment_version
    ):
        raise ValueError("The reviewed preview environment is unavailable or has changed")
    return target
