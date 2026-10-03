"""Resolve the persisted app/environment placement before telemetry dispatch."""

import hashlib
import json

from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.scopes import live_app_owners
from core.cluster_observability import namespace_for_environment


def resolve_environment(app, environment_name, environment_id=None):
    from astrolift_registry.models import RegisteredApp

    if not live_app_owners(
        RegisteredApp.objects.filter(
            pk=app.pk,
            organization_id=app.organization_id,
            organization__deleted_at__isnull=True,
            deleted_at__isnull=True,
        )
    ).exists():
        return None
    rows = AppEnvironment.objects.select_related("tenant_cluster").filter(
        registered_app=app, deleted_at__isnull=True
    )
    if environment_name is not None:
        rows = rows.filter(name=environment_name)
    if environment_id is not None:
        rows = rows.filter(guid=str(environment_id))
    env = rows.order_by("name", "id").first()
    if env is None:
        return None
    cluster = env.tenant_cluster
    if (
        cluster.deleted_at is not None
        or not cluster.is_active
        or cluster.organization_id not in {None, app.organization_id}
        or cluster.lifecycle in {"decommissioning", "decommissioned"}
    ):
        return None
    return env


def environment_namespace(env):
    return namespace_for_environment(env)


def placement_fingerprint(app, env):
    return hashlib.sha256(
        json.dumps(
            [
                app.organization_id,
                app.team_id,
                app.project_id,
                str(app.guid),
                env.tenant_cluster_id,
                str(env.guid),
                env.name,
                environment_namespace(env),
                env.tenant_cluster.provider_config,
            ],
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()


def placement_is_current(app, env, fingerprint):
    from astrolift_registry.models import RegisteredApp

    fresh_app = live_app_owners(
        RegisteredApp.objects.select_related("organization").filter(
            pk=app.pk,
            organization_id=app.organization_id,
            organization__deleted_at__isnull=True,
            deleted_at__isnull=True,
        )
    ).first()
    if fresh_app is None:
        return False
    fresh_env = resolve_environment(fresh_app, env.name)
    return (
        fresh_env is not None
        and fresh_env.pk == env.pk
        and placement_fingerprint(fresh_app, fresh_env) == fingerprint
    )
