"""Move one environment out of a shared app namespace (#2094).

Environments created before #1922 kept a blank ``k8s_namespace`` and still
render into the app namespace, so each one's deploy overwrites the other's
objects. The first live environment of the app on the cluster (the primary)
keeps the app namespace; any other one can be moved, in three operator steps:

1. ``record_move`` gives the environment its own namespace. Nothing on the
   cluster changes; its next deploy renders there.
2. The operator deploys the moved environment, then redeploys the primary so
   the shared object names in the app namespace are the primary's again.
3. ``clean_up_move`` deletes the moved environment's literal ``[env]`` Secret
   from the app namespace, the one object there named for it. It reads the
   cluster first and refuses while any workload in the app namespace still
   references that Secret, which is the state before step 2.

Every other object in the app namespace carries a name both environments
render, so it belongs to the primary after step 2 and is left alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from django.db import transaction

from astrolift_lifecycle.models import AppEnvironment

_WORKLOAD_KINDS = (
    "apps/v1/Deployment",
    "apps/v1/StatefulSet",
    "apps/v1/DaemonSet",
    "batch/v1/CronJob",
    "batch/v1/Job",
)


class MoveRefused(Exception):
    """The move cannot be made as asked; the message says why."""


@dataclass(frozen=True)
class MovePlan:
    organization: str
    app: str
    environment: str
    cluster: str
    primary: str
    from_namespace: str
    to_namespace: str
    literal_secret: str


def _environment(organization: str, app: str, environment: str, *, lock: bool = False) -> AppEnvironment:
    rows = AppEnvironment.objects.select_related("registered_app__organization", "tenant_cluster")
    if lock:
        rows = rows.select_for_update(of=("self",))
    env = rows.filter(
        registered_app__organization__slug=organization,
        registered_app__slug=app,
        registered_app__deleted_at__isnull=True,
        name=environment,
        deleted_at__isnull=True,
    ).first()
    if env is None:
        raise MoveRefused(f"{organization}/{app} has no live environment {environment!r}")
    return env


def _literal_secret(env: AppEnvironment) -> str:
    from astrolift_workflows.activities.app_lifecycle import _app_env_secret_name

    return _app_env_secret_name(env.registered_app.slug, env.name)


def plan_move(organization: str, app: str, environment: str, *, lock: bool = False) -> MovePlan:
    """What moving ``environment`` would do, or why it cannot be moved."""
    from _sdk.k8s_naming import dns_label

    from astrolift_registry.namespaces import unique_environment_namespace
    from core.app_deploy import namespace_for_app

    env = _environment(organization, app, environment, lock=lock)
    if (env.k8s_namespace or "").strip():
        raise MoveRefused(f"{environment} already renders into its own namespace {env.k8s_namespace}")
    if env.tenant_cluster is None:
        raise MoveRefused(f"{environment} has no cluster")
    sharing = list(
        AppEnvironment.objects.filter(
            registered_app=env.registered_app,
            tenant_cluster=env.tenant_cluster,
            k8s_namespace="",
            deleted_at__isnull=True,
        ).order_by("pk")
    )
    if len(sharing) < 2:
        raise MoveRefused(f"{environment} is the only environment in its namespace; there is nothing to move")
    primary = sharing[0]
    if primary.pk == env.pk:
        others = ", ".join(e.name for e in sharing[1:])
        raise MoveRefused(f"{environment} is the primary and keeps the app namespace; move {others} instead")
    app_row = env.registered_app
    return MovePlan(
        organization=organization,
        app=app,
        environment=environment,
        cluster=env.tenant_cluster.slug,
        primary=primary.name,
        from_namespace=namespace_for_app(app_row),
        to_namespace=unique_environment_namespace(
            dns_label(namespace_for_app(app_row), env.name),
            seed=f"{app_row.guid}:{env.name}",
            exclude_environment_id=env.pk,
        ),
        literal_secret=_literal_secret(env),
    )


def record_move(plan: MovePlan) -> MovePlan:
    """Record the environment's own namespace, rechecked under a row lock."""
    with transaction.atomic():
        current = plan_move(plan.organization, plan.app, plan.environment, lock=True)
        if current != plan:
            raise MoveRefused("the environment or its namespace changed since the plan; plan again")
        env = _environment(plan.organization, plan.app, plan.environment, lock=True)
        env.k8s_namespace = plan.to_namespace
        env.save(update_fields=["k8s_namespace", "updated_at", "version"])
    return plan


def clean_up_move(organization: str, app: str, environment: str, *, apply: bool = False) -> str:
    """Delete the moved environment's literal Secret from the app namespace.

    Returns the Secret's name. With ``apply`` false it only checks.
    """
    from core.app_deploy import namespace_for_app
    from core.cluster_management import _driver_for_cluster

    env = _environment(organization, app, environment)
    if not (env.k8s_namespace or "").strip():
        raise MoveRefused(f"{environment} has not been moved; record the move first")
    if env.tenant_cluster is None:
        raise MoveRefused(f"{environment} has no cluster")
    app_namespace = namespace_for_app(env.registered_app)
    if env.k8s_namespace == app_namespace:
        raise MoveRefused(f"{environment} records the app namespace itself; nothing to clean up")
    secret = _literal_secret(env)
    cluster = env.tenant_cluster
    driver = _driver_for_cluster(cluster)
    referencing = []
    for kind in _WORKLOAD_KINDS:
        for row in driver.list_manifests(cluster.slug, app_namespace, kind) or []:
            if f'"{secret}"' in json.dumps(row.get("spec") or {}):
                referencing.append(
                    f"{kind.rsplit('/', 1)[-1]}/{(row.get('metadata') or {}).get('name', '?')}"
                )
    if referencing:
        raise MoveRefused(
            f"{', '.join(sorted(referencing))} in {app_namespace} still reference {secret}; "
            f"redeploy the primary environment first"
        )
    if apply:
        driver.delete_manifests(
            cluster.slug,
            app_namespace,
            [
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": secret, "namespace": app_namespace},
                }
            ],
        )
    return secret
