"""Which recipe components a cluster already runs, and who installed them.

The capability probe sees a controller whoever put it there. The recipe needs
to know more than that: a component the recipe installed must stay selected
(an operator run deletes every release it is not given), while one running
outside the recipe must not be pre-selected, or accepting the defaults
installs a second copy that fights the first over the same load balancers,
records or certificates (#2119, #2130).
"""

from __future__ import annotations

from typing import Any

# How the probe reports each component, by recipe key.
_LBC_CRD = "targetgroupbindings.elbv2.k8s.aws"


def components_running(capabilities: dict[str, Any] | None) -> set[str]:
    """Recipe keys whose controller a capability probe found running."""
    caps = capabilities or {}
    running: set[str] = set()
    if _LBC_CRD in set(caps.get("installed_crds") or ()):
        running.add("aws-load-balancer-controller")
    if (caps.get("external_dns") or {}).get("installed"):
        running.add("external-dns")
    if (caps.get("cert_manager") or {}).get("installed"):
        running.add("cert-manager")
    if caps.get("metrics_server") is True:
        running.add("metrics-server")
    if caps.get("prometheus") is True:
        running.add("kube-prometheus-stack")
    return running


def components_installed_by_recipe(cluster: Any) -> set[str]:
    """Recipe keys the cluster's last successful recipe run applied."""
    from astrolift_clusters.models import ClusterBootstrapRun

    last = (
        ClusterBootstrapRun.objects.filter(
            tenant_cluster=cluster,
            status=ClusterBootstrapRun.Status.SUCCEEDED,
        )
        .order_by("-ended_at")
        .first()
    )
    if last is None:
        return set()
    names = {str(r.get("name") if isinstance(r, dict) else r) for r in last.installed_releases or []}
    # The recipe records component keys; the CLI records release names.
    return {n.removeprefix("astrolift-") for n in names}
