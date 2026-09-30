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


def edge_support_refusal(cluster: Any) -> str:
    """The current recipe needs an EKS ALB TLS front, not just the chart."""
    plugin = getattr(getattr(cluster, "provider_plugin", None), "slug", "")
    if plugin == "aws":
        return ""
    return (
        f"Envoy edge is unsupported for provider {plugin or 'unknown'!r}: "
        "AKS, GKE and k8s_native recipes have no supported TLS front or DNS integration. "
        "Keep the existing ingress class; installing the controller alone does not serve HTTPS."
    )


_LBC_KEY = "aws-load-balancer-controller"
_EXTERNAL_DNS_KEY = "external-dns"


def controllers_present(capabilities: dict[str, Any] | None) -> set[str]:
    """The edge's controllers a capability probe found running, by recipe key.

    Found means running at all, in any namespace, by any installer. A cluster
    with a hand-installed controller must not get a second one from the
    recipe, because two ALB controllers or two external-dns fight over the
    same load balancers and records.
    """
    from astrolift_clusters.recipe_detection import components_running

    return components_running(capabilities) & {_LBC_KEY, _EXTERNAL_DNS_KEY}


def edge_controllers_to_add(recipe_keys: set[str], capabilities: dict[str, Any] | None) -> set[str]:
    """The controllers an additive edge install brings along.

    Only the ones this cluster's recipe offers (the EKS recipe does; the
    vanilla one has no ALB) and the probe did not find. Without them a fresh
    cluster gets a Gateway and no load balancer or record in front of it.
    """
    wanted = {_LBC_KEY, _EXTERNAL_DNS_KEY} & recipe_keys
    return wanted - controllers_present(capabilities)


def edge_install_wanted(cluster: Any) -> bool:
    """The cluster is on the Envoy edge and still has no install of it."""
    from astrolift_clusters.recipe_detection import components_installed_by_recipe
    from providers.k8s_native.edge_gateway import EDGE_COMPONENT_KEY, edge_configured

    if (cluster.ingress_class or "") != EDGE_INGRESS_CLASS:
        return False
    refusal = edge_support_refusal(cluster)
    if refusal:
        logger.error("edge install refused for cluster %s: %s", cluster.slug, refusal)
        return False
    if not edge_configured(cluster.oidc_auth_config):
        return False
    return EDGE_COMPONENT_KEY not in components_installed_by_recipe(cluster)


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
