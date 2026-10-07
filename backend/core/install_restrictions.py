"""What the install withholds from the control plane (calliope-installer#447).

An operator sharing an AWS account with other teams can withhold DNS,
databases, load balancers and the tenant cluster's lifecycle. The installer
removes those grants from the control plane's task role, adds an explicit
Deny for each, and sets ``ASTROLIFT_WITHHELD_CAPABILITIES`` to a comma list
of ``dns``, ``databases``, ``load_balancers`` and ``clusters``
(terraform/aws/astrolift-deployment-ecs/restrictions.tf). AWS would refuse
the calls anyway; this module lets the console refuse them first, with the
reason, instead of failing on AccessDenied halfway through a workflow.

Unset, the default and every install before the question, withholds nothing,
so every check here answers "allowed" and behaviour is exactly as before.
"""

from __future__ import annotations

import os

try:
    from temporalio.exceptions import ApplicationError
except ImportError:  # pragma: no cover - the provider test job imports core without the SDK

    class ApplicationError(Exception):  # type: ignore[no-redef]
        def __init__(self, message: str, *, non_retryable: bool = False) -> None:
            super().__init__(message)
            self.non_retryable = non_retryable


ENV_VAR = "ASTROLIFT_WITHHELD_CAPABILITIES"

#: In the order the installer writes them.
CAPABILITIES: tuple[str, ...] = ("dns", "databases", "load_balancers", "clusters")

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


def controller_refusal(component_key: str, cluster) -> str:
    """Why installing a bootstrap component on ``cluster`` is refused, or ``""``."""
    capability = CONTROLLERS.get(component_key)
    return reason(capability) if capability and _on_aws(cluster) else ""


def cluster_delete_refusal(cluster) -> str:
    """Why deleting ``cluster``'s cloud infrastructure is refused, or ``""``."""
    return reason("clusters") if _on_aws(cluster) else ""


class WithheldCapabilityError(ApplicationError):
    """A call the install withholds, refused before it reaches AWS.

    Non-retryable wherever it is raised: inside a Temporal activity it fails
    the activity at once instead of retrying, because AWS would refuse every
    attempt the same way. ``str()`` is the reason alone.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, non_retryable=True)


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
