"""Bring the Envoy edge up on a cluster whose config asks for it (#2130).

An install that declares the Envoy edge (``ingress_class = envoy`` plus a
complete ``oidc_auth_config``) used to need an operator to open the cluster
page and run the recipe with Envoy Gateway checked. This starts that install
from the control plane instead, so a fresh install or a rebuild comes up on
central auth with nothing done by hand.

The run is **additive**: it applies the edge and deletes nothing. The recipe
install normally removes every release that is not selected, and an automatic
run that did that would take down whatever else the cluster runs. It never
fires for a cluster on any other class, so an existing install on nginx or ALB
auth is left exactly as it is.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

EDGE_INGRESS_CLASS = "envoy"


def edge_install_wanted(cluster: Any) -> bool:
    """The cluster is on the Envoy edge and still has no install of it."""
    from astrolift_clusters.models import ClusterBootstrapRun
    from providers.k8s_native.edge_gateway import EDGE_COMPONENT_KEY, edge_configured

    if (cluster.ingress_class or "") != EDGE_INGRESS_CLASS:
        return False
    if not edge_configured(cluster.oidc_auth_config):
        return False
    last = (
        ClusterBootstrapRun.objects.filter(
            tenant_cluster=cluster,
            status=ClusterBootstrapRun.Status.SUCCEEDED,
        )
        .order_by("-ended_at")
        .first()
    )
    if last is None:
        return True
    names = {str(r.get("name") if isinstance(r, dict) else r) for r in last.installed_releases or []}
    # The recipe records component keys; the CLI records release names.
    return not names & {EDGE_COMPONENT_KEY, f"astrolift-{EDGE_COMPONENT_KEY}"}


def ensure_edge_installed(cluster: Any) -> bool:
    """Start the additive edge install when ``edge_install_wanted``.

    Returns whether a run was started. Never raises: this runs on every
    control-plane start, and a Temporal outage must not keep the API down.
    """
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import Actor, InstallClusterPrereqsInput
    from providers.k8s_native.edge_gateway import EDGE_COMPONENT_KEY

    try:
        if not edge_install_wanted(cluster):
            return False
        # Its own workflow id: the operator's install id is started with
        # TERMINATE_IF_RUNNING, and a restart must not cut an install the
        # operator is watching.
        start_workflow(
            "InstallClusterPrereqsWorkflow",
            args=[
                InstallClusterPrereqsInput(
                    cluster_id=cluster.pk,
                    actor=Actor(kind="system", display="edge-install"),
                    selected_components=(EDGE_COMPONENT_KEY,),
                    option_overrides={},
                    organization_id=cluster.organization_id,
                    additive=True,
                ),
            ],
            workflow_id=f"InstallClusterPrereqsWorkflow-{cluster.guid}-edge",
        )
    except Exception:
        logger.exception("edge install for cluster %s could not be started", cluster.slug)
        return False
    logger.info("edge install started for cluster %s", cluster.slug)
    return True
