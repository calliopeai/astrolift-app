"""Environments that still render into one namespace (#2094).

#1922 gives an environment its own namespace only when it is created after
that change. Two live environments of one app on one cluster that both kept a
blank ``k8s_namespace`` still render into the app namespace, so each deploy
overwrites the other's Deployments, Services, Secrets and PVCs. This finds
them. It reads only; moving an environment is an operator decision.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from astrolift_lifecycle.models import AppEnvironment
from core.app_deploy import namespace_for_environment


@dataclass(frozen=True)
class SharedNamespace:
    organization: str
    app: str
    cluster: str
    namespace: str
    environments: tuple[str, ...]


def shared_environment_namespaces(*, organization_slug: str | None = None) -> list[SharedNamespace]:
    """Every (app, cluster, namespace) more than one live environment renders into."""
    rows = AppEnvironment.objects.filter(
        deleted_at__isnull=True,
        tenant_cluster__isnull=False,
        registered_app__deleted_at__isnull=True,
    ).select_related("registered_app__organization", "tenant_cluster")
    if organization_slug:
        rows = rows.filter(registered_app__organization__slug=organization_slug)
    groups: dict[tuple[int, int, str], list[AppEnvironment]] = defaultdict(list)
    for env in rows.order_by("pk"):
        groups[(env.registered_app_id, env.tenant_cluster_id, namespace_for_environment(env))].append(env)
    return sorted(
        (
            SharedNamespace(
                organization=envs[0].registered_app.organization.slug,
                app=envs[0].registered_app.slug,
                cluster=envs[0].tenant_cluster.slug,
                namespace=namespace,
                environments=tuple(sorted(env.name for env in envs)),
            )
            for (_, _, namespace), envs in groups.items()
            if len(envs) > 1
        ),
        key=lambda row: (row.organization, row.app, row.cluster),
    )
