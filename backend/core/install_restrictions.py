"""What the install withholds from the control plane (calliope-installer#447).

An operator sharing an AWS account with other teams can withhold DNS,
databases, load balancers and the tenant cluster's lifecycle. The installer
removes those grants from the control plane's task role, adds an explicit
Deny for each, and sets ``ASTROLIFT_WITHHELD_CAPABILITIES`` to a comma list
of ``dns``, ``databases``, ``load_balancers`` and ``clusters``
(terraform/aws/astrolift-deployment-ecs/restrictions.tf). ``controllers``
means the control plane holds only the minimal Kubernetes RBAC in
``deploy/rbac/control-plane-minimal.yaml`` on the clusters it is handed,
not cluster admin (see :mod:`core.control_plane_rbac`). AWS would refuse
the calls anyway; this module lets the console refuse them first, with the
reason, instead of failing on AccessDenied halfway through a workflow.

Unset, the default and every install before the question, withholds nothing,
so every check here answers "allowed" and behaviour is exactly as before.
"""

from __future__ import annotations

import os

try:
    from temporalio.exceptions import ApplicationError as _ErrorBase

    _NON_RETRYABLE: dict = {"non_retryable": True}
except ImportError:  # the providers test environment has no Temporal
    _ErrorBase, _NON_RETRYABLE = Exception, {}

ENV_VAR = "ASTROLIFT_WITHHELD_CAPABILITIES"

#: In the order the installer writes them.
CAPABILITIES: tuple[str, ...] = ("dns", "databases", "load_balancers", "clusters", "controllers")

_REASONS: dict[str, str] = {
    "dns": (
        "DNS is withheld from Astrolift on this install: it makes no Route53 changes and runs no "
        "external-dns of its own. App records come from an external-dns the cluster's owner runs, "
        "or are made another way."
    ),
    "databases": (
        "Databases are withheld from Astrolift on this install: it holds no RDS or Aurora grant at "
        "all, so managed cloud databases are refused. Run an in-cluster variant or bind a database "
        "you bring."
    ),
    "load_balancers": (
        "Load balancers are withheld from Astrolift on this install: it makes no load balancers and "
        "runs no AWS Load Balancer Controller of its own. An app's load balancer comes from a "
        "controller the cluster's owner runs."
    ),
    "clusters": (
        "Cluster lifecycle is withheld from Astrolift on this install: it runs on clusters it is "
        "handed and may not create or delete one."
    ),
    "controllers": (
        "Cluster-wide changes are withheld from Astrolift on this install: on AWS clusters it holds "
        "the minimal Kubernetes RBAC in deploy/rbac/control-plane-minimal.yaml, not cluster admin. "
        "Controllers, CRDs, cluster roles, storage classes and persistent volumes are the cluster "
        "owner's to install."
    ),
}

#: Managed-service variants that are RDS underneath. DocumentDB and Neptune
#: are managed through the RDS API, so the installer's ``rds:*`` Deny refuses
#: them too. In-cluster variants (cnpg, the MySQL operator) are untouched.
RDS_BACKED: frozenset[tuple[str, str]] = frozenset(
    {
        ("postgres", "rds"),
        ("postgres", "aurora_postgres"),
        ("postgres", "aurora_postgres_serverless_v2"),
        ("mysql", "rds_mysql"),
        ("mysql", "aurora_mysql"),
        ("mysql", "aurora_mysql_serverless_v2"),
        ("mssql", "rds_sqlserver_express"),
        ("mssql", "rds_sqlserver_web"),
        ("mssql", "rds_sqlserver_standard"),
        ("mssql", "rds_sqlserver_enterprise"),
        ("database_proxy", "rds_proxy"),
        ("document_db", "documentdb"),
        ("document_db", "documentdb_serverless_v2"),
        ("graph_db", "neptune"),
        ("graph_db", "neptune_serverless"),
    }
)

#: Bootstrap recipe components that act for a withheld capability.
CONTROLLERS: dict[str, str] = {
    "external-dns": "dns",
    "aws-load-balancer-controller": "load_balancers",
}


def withheld() -> frozenset[str]:
    """The capabilities this install withholds. Unknown names are ignored."""
    raw = os.environ.get(ENV_VAR) or ""
    return frozenset(part.strip() for part in raw.split(",") if part.strip() in _REASONS)


def reason(capability: str) -> str:
    """Why ``capability`` is refused, or ``""`` when the install allows it."""
    return _REASONS[capability] if capability in withheld() else ""


def database_refusal(kind: str, variant: str) -> str:
    """Why provisioning ``(kind, variant)`` is refused, or ``""``."""
    if (str(kind), str(variant or "")) not in RDS_BACKED:
        return ""
    return reason("databases")


def _on_aws(cluster) -> bool:
    """Whether ``cluster`` runs on AWS, the only cloud the installer's Denies reach.

    The Denies are IAM statements on the control plane's AWS task role. A
    GKE, AKS or on-prem cluster's external-dns, or deleting such a cluster,
    never calls AWS, so nothing is withheld there.
    """
    return getattr(getattr(cluster, "provider_plugin", None), "slug", "") == "aws"


def _on_eks(cluster) -> bool:
    """Whether the minimal RBAC contract holds on ``cluster``.

    An AWS cluster, or any cluster whose apiserver is an EKS endpoint (one
    registered through the kubeconfig path), the same test the transport
    guard in :mod:`core.control_plane_rbac` applies to every request.
    """
    from core.control_plane_rbac import host_is_eks

    return _on_aws(cluster) or host_is_eks(str(getattr(cluster, "endpoint", "") or ""))


def cluster_scope_refusal(cluster) -> str:
    """Why work beyond the minimal RBAC contract on ``cluster`` is refused, or ``""``.

    The bootstrap recipe, platform cluster roles, the keep-alive agent, the
    log collector and static PersistentVolumes all write cluster-scoped
    objects, which the minimal contract leaves to the cluster's owner.
    """
    return reason("controllers") if _on_eks(cluster) else ""


#: Managed-service variants whose every binding is a CSI volume, which the
#: workload mounts through a static PersistentVolume: cluster-scoped, so
#: beyond the minimal RBAC contract. S3 joins them when ``mount_path`` is set.
CSI_BACKED: frozenset[tuple[str, str]] = frozenset(
    {
        ("filesystem", "efs"),
        ("filesystem", "fsx_lustre"),
        ("filesystem", "fsx_openzfs"),
        ("filesystem", "fsx_windows"),
        ("filesystem", "nfs_csi"),
    }
)


def _csi_backed(kind: str, variant: str, config: dict | None = None) -> bool:
    if (str(kind), str(variant or "")) in CSI_BACKED:
        return True
    return (
        str(kind) == "object_store" and str(variant or "") == "s3" and bool((config or {}).get("mount_path"))
    )


def filesystem_refusal(kind: str, variant: str, config: dict | None, cluster) -> str:
    """Why provisioning a CSI-mounted service on ``cluster`` is refused, or ``""``.

    Refused before the cloud resource exists: every workload that mounts it
    would be refused at its binding preflight anyway.
    """
    return cluster_scope_refusal(cluster) if _csi_backed(kind, variant, config) else ""


def variant_refusal(kind: str, variant: str, plugin_slug: str) -> str:
    """Why the catalog offers ``(kind, variant)`` on ``plugin_slug`` disabled, or ``""``."""
    if refusal := database_refusal(kind, variant):
        return refusal
    if plugin_slug == "aws" and (str(kind), str(variant or "")) in CSI_BACKED:
        return reason("controllers")
    return ""


def controller_refusal(component_key: str, cluster) -> str:
    """Why installing a bootstrap component on ``cluster`` is refused, or ``""``."""
    capability = CONTROLLERS.get(component_key)
    if capability and _on_aws(cluster) and (why := reason(capability)):
        return why
    return cluster_scope_refusal(cluster)


def cluster_delete_refusal(cluster) -> str:
    """Why deleting ``cluster``'s cloud infrastructure is refused, or ``""``."""
    return reason("clusters") if _on_aws(cluster) else ""


class WithheldCapabilityError(_ErrorBase):
    """A call the install withholds, refused before it reaches AWS or the apiserver.

    Non-retryable: the restriction is fixed at install, so a Temporal retry
    would only refuse again.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, **_NON_RETRYABLE)


#: Route53Driver methods that change DNS: every one the WithheldDns Deny
#: refuses. Reads (zones, records, certificate status) stay allowed.
_DNS_WRITES = frozenset({"ensure_record", "delete_record", "provision_zone", "deprovision_zone"})


class _DnsWritesRefused:
    """A DNS driver whose writes raise the withheld reason instead of AccessDenied."""

    def __init__(self, driver, refusal: str) -> None:
        self._driver = driver
        self._refusal = refusal

    def __getattr__(self, name: str):
        if name in _DNS_WRITES:

            def _refused(*_args, **_kwargs):
                raise WithheldCapabilityError(self._refusal)

            return _refused
        return getattr(self._driver, name)


def guard_dns_driver(driver, plugin_slug: str):
    """``driver``, with its Route53 writes refused when the install withholds DNS.

    The control plane writes Route53 itself in several workflows (managed
    domains, custom-domain records, static sites); each gets the reason
    here rather than AccessDenied partway through.
    """
    refusal = reason("dns") if plugin_slug == "aws" else ""
    return _DnsWritesRefused(driver, refusal) if refusal else driver
