"""Self-sufficient AWS DB networking for managed-service drivers (#1002).

RDS and ElastiCache instances must live in a subnet group (>=2 private
subnets across AZs) and a security group that admits the consuming
pods. Astrolift is BYOC — at a customer install there is no out-of-band
terraform to pre-create these, so the platform discovers the cluster's
VPC (the same VPC it runs EKS/the ALB in) and creates the subnet group
+ SG itself, idempotently. Both are per-cluster shared infra: created
once on first managed-service provision, reused by every service on the
cluster, tagged ``astrolift.io/managed=true`` for later reaping at
cluster decommission. Everything here is overridable via the cluster's
``provider_config`` (the install settings bundle) — see
``managed_config_for``.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("astrolift.providers.aws.managed.networking")

_MANAGED_TAGS = [
    {"Key": "astrolift.io/managed", "Value": "true"},
    {"Key": "astrolift.io/component", "Value": "managed-service-db-networking"},
]


def _clients(region: str, *, ec2=None, rds=None, elasticache=None, eks=None):
    import boto3

    return (
        ec2 or boto3.client("ec2", region_name=region),
        rds or boto3.client("rds", region_name=region),
        elasticache or boto3.client("elasticache", region_name=region),
        eks or boto3.client("eks", region_name=region),
    )


def discover_vpc(cluster, *, region: str, ec2, eks) -> tuple[str, list[str], str]:
    """Return ``(vpc_id, private_subnet_ids, vpc_cidr)`` for the cluster.

    VPC comes from ``provider_config['vpc_id']`` if pinned, else the EKS
    cluster's ``resourcesVpcConfig.vpcId``. Subnets prefer private
    (no auto-assigned public IP); we pick one per AZ so the subnet group
    spans >=2 AZs as RDS/ElastiCache require.
    """
    pc = cluster.provider_config or {}
    ac = cluster.auth_config or {}
    vpc_id = str(pc.get("vpc_id", ""))
    if not vpc_id:
        cluster_name = str(
            pc.get("cluster_name", ac.get("cluster_name", cluster.slug)),
        )
        resp = eks.describe_cluster(name=cluster_name)
        vpc_id = resp["cluster"]["resourcesVpcConfig"]["vpcId"]

    vpc = ec2.describe_vpcs(VpcIds=[vpc_id])["Vpcs"][0]
    vpc_cidr = vpc["CidrBlock"]

    subnets = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}],
    )["Subnets"]
    if not subnets:
        raise RuntimeError(f"vpc {vpc_id} has no subnets")
    private = [s for s in subnets if not s.get("MapPublicIpOnLaunch", False)]
    pool = private or subnets  # fall back to all if tagging is non-standard
    # One subnet per AZ for a clean multi-AZ group.
    by_az: dict[str, str] = {}
    for s in pool:
        by_az.setdefault(s["AvailabilityZone"], s["SubnetId"])
    subnet_ids = list(by_az.values())
    if len(subnet_ids) < 2:
        # RDS/ElastiCache need >=2 AZs; widen to all subnets if private
        # set was too small.
        by_az = {}
        for s in subnets:
            by_az.setdefault(s["AvailabilityZone"], s["SubnetId"])
        subnet_ids = list(by_az.values())
    return vpc_id, subnet_ids, vpc_cidr


def ensure_security_group(
    *,
    vpc_id: str,
    vpc_cidr: str,
    port: int,
    name: str,
    ec2,
) -> str:
    """Find-or-create a SG in ``vpc_id`` admitting ``port`` from the VPC
    CIDR (the pods that consume the DB live in this VPC). Idempotent."""
    existing = ec2.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": [name]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ],
    ).get("SecurityGroups", [])
    if existing:
        sg_id = existing[0]["GroupId"]
    else:
        sg_id = ec2.create_security_group(
            GroupName=name,
            Description="astrolift managed-service DB access",
            VpcId=vpc_id,
            TagSpecifications=[
                {"ResourceType": "security-group", "Tags": _MANAGED_TAGS},
            ],
        )["GroupId"]
    # Idempotent ingress: tolerate the duplicate-rule error.
    try:
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": port,
                    "ToPort": port,
                    "IpRanges": [
                        {"CidrIp": vpc_cidr, "Description": "astrolift pods"},
                    ],
                },
            ],
        )
    except Exception as exc:
        if "InvalidPermission.Duplicate" not in str(exc):
            raise
    return sg_id


def ensure_rds_subnet_group(*, name: str, subnet_ids: list[str], rds) -> str:
    try:
        rds.create_db_subnet_group(
            DBSubnetGroupName=name,
            DBSubnetGroupDescription="astrolift managed RDS subnets",
            SubnetIds=subnet_ids,
            Tags=_MANAGED_TAGS,
        )
    except Exception as exc:
        if "DBSubnetGroupAlreadyExists" not in type(exc).__name__ and "already exists" not in str(exc).lower():
            raise
    return name


def ensure_elasticache_subnet_group(
    *,
    name: str,
    subnet_ids: list[str],
    elasticache,
) -> str:
    try:
        elasticache.create_cache_subnet_group(
            CacheSubnetGroupName=name,
            CacheSubnetGroupDescription="astrolift managed ElastiCache subnets",
            SubnetIds=subnet_ids,
            Tags=_MANAGED_TAGS,
        )
    except Exception as exc:
        if "CacheSubnetGroupAlreadyExists" not in type(exc).__name__ and "already exists" not in str(exc).lower():
            raise
    return name


def ensure_db_networking(
    cluster,
    *,
    region: str,
    port: int,
    service: str,  # "rds" | "elasticache"
    clients: Any | None = None,
) -> tuple[str, list[str]]:
    """Resolve ``(subnet_group_name, [security_group_id])`` for a VPC-bound
    managed service, creating the shared per-cluster infra if absent.

    Overridable end-to-end via ``provider_config``: ``db_subnet_group`` /
    ``cache_subnet_group`` + ``db_security_group_ids`` short-circuit the
    discovery entirely for locked-down VPCs.
    """
    pc = cluster.provider_config or {}
    group_key = "cache_subnet_group" if service == "elasticache" else "db_subnet_group"
    if pc.get(group_key) and pc.get("db_security_group_ids"):
        return str(pc[group_key]), list(pc["db_security_group_ids"])

    ec2, rds, elasticache, eks = clients or _clients(region)
    vpc_id, subnet_ids, vpc_cidr = discover_vpc(
        cluster,
        region=region,
        ec2=ec2,
        eks=eks,
    )
    sg_name = f"astrolift-{cluster.slug}-db"[:255]
    sg_id = (
        str(pc["db_security_group_ids"][0])
        if pc.get("db_security_group_ids")
        else ensure_security_group(
            vpc_id=vpc_id,
            vpc_cidr=vpc_cidr,
            port=port,
            name=sg_name,
            ec2=ec2,
        )
    )
    grp_name = str(pc.get(group_key, f"astrolift-{cluster.slug}-db"))[:255]
    if service == "elasticache":
        ensure_elasticache_subnet_group(
            name=grp_name,
            subnet_ids=subnet_ids,
            elasticache=elasticache,
        )
    else:
        ensure_rds_subnet_group(name=grp_name, subnet_ids=subnet_ids, rds=rds)
    log.info(
        "ensured db networking cluster=%s service=%s group=%s sg=%s",
        cluster.slug,
        service,
        grp_name,
        sg_id,
    )
    return grp_name, [sg_id]


def ensure_db_proxy_networking(
    cluster,
    *,
    region: str,
    clients: Any | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve private subnet and security-group IDs for RDS Proxy.

    RDS Proxy consumes raw subnet IDs rather than an RDS subnet-group name.
    The shared proxy SG admits the three portable relational protocols from
    inside the VPC. Operators can pin both lists in locked-down installs with
    ``db_proxy_subnet_ids`` / ``db_proxy_security_group_ids``.
    """

    pc = cluster.provider_config or {}
    if pc.get("db_proxy_subnet_ids") and pc.get("db_proxy_security_group_ids"):
        return list(pc["db_proxy_subnet_ids"]), list(pc["db_proxy_security_group_ids"])

    ec2, _rds, _elasticache, eks = clients or _clients(region)
    vpc_id, subnet_ids, vpc_cidr = discover_vpc(
        cluster,
        region=region,
        ec2=ec2,
        eks=eks,
    )
    sg_name = f"astrolift-{cluster.slug}-db-proxy"[:255]
    if pc.get("db_proxy_security_group_ids"):
        security_group_ids = list(pc["db_proxy_security_group_ids"])
    else:
        sg_id = ""
        for port in (5432, 3306, 1433):
            sg_id = ensure_security_group(
                vpc_id=vpc_id,
                vpc_cidr=vpc_cidr,
                port=port,
                name=sg_name,
                ec2=ec2,
            )
        security_group_ids = [sg_id]
    return list(pc.get("db_proxy_subnet_ids") or subnet_ids), security_group_ids


def ensure_serverless_cache_networking(
    cluster,
    *,
    region: str,
    clients: Any | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve raw private network IDs for ElastiCache Serverless.

    A single per-cluster group admits both Redis-compatible and Memcached
    protocols. Operators can pin either list without giving up automatic
    discovery of the other.
    """

    pc = cluster.provider_config or {}
    if pc.get("serverless_cache_subnet_ids") and pc.get(
        "serverless_cache_security_group_ids",
    ):
        return list(pc["serverless_cache_subnet_ids"]), list(
            pc["serverless_cache_security_group_ids"],
        )

    ec2, _rds, _elasticache, eks = clients or _clients(region)
    vpc_id, subnet_ids, vpc_cidr = discover_vpc(
        cluster,
        region=region,
        ec2=ec2,
        eks=eks,
    )
    security_group_ids = list(pc.get("serverless_cache_security_group_ids") or [])
    if not security_group_ids:
        sg_name = f"astrolift-{cluster.slug}-serverless-cache"[:255]
        sg_id = ""
        for port in (6379, 11211):
            sg_id = ensure_security_group(
                vpc_id=vpc_id,
                vpc_cidr=vpc_cidr,
                port=port,
                name=sg_name,
                ec2=ec2,
            )
        security_group_ids = [sg_id]
    return list(pc.get("serverless_cache_subnet_ids") or subnet_ids), security_group_ids


def ensure_memorydb_networking(
    cluster,
    *,
    region: str,
    clients: Any | None = None,
) -> tuple[str, list[str]]:
    """Find or create the private subnet group and access group for MemoryDB."""

    pc = cluster.provider_config or {}
    if pc.get("memorydb_subnet_group") and pc.get("memorydb_security_group_ids"):
        return str(pc["memorydb_subnet_group"]), list(pc["memorydb_security_group_ids"])

    if clients is None:
        import boto3

        ec2 = boto3.client("ec2", region_name=region)
        memorydb = boto3.client("memorydb", region_name=region)
        eks = boto3.client("eks", region_name=region)
    else:
        ec2, memorydb, eks = clients
    vpc_id, subnet_ids, vpc_cidr = discover_vpc(
        cluster,
        region=region,
        ec2=ec2,
        eks=eks,
    )
    subnet_group = str(pc.get("memorydb_subnet_group") or f"astrolift-{cluster.slug}-memorydb")[:40]
    if not pc.get("memorydb_subnet_group"):
        try:
            memorydb.create_subnet_group(
                SubnetGroupName=subnet_group,
                Description="astrolift managed MemoryDB subnets",
                SubnetIds=subnet_ids,
                Tags=_MANAGED_TAGS,
            )
        except Exception as exc:
            if (
                "SubnetGroupAlreadyExists" not in type(exc).__name__
                and "already exists"
                not in str(
                    exc,
                ).lower()
            ):
                raise
    security_group_ids = list(pc.get("memorydb_security_group_ids") or [])
    if not security_group_ids:
        security_group_ids = [
            ensure_security_group(
                vpc_id=vpc_id,
                vpc_cidr=vpc_cidr,
                port=6379,
                name=f"astrolift-{cluster.slug}-memorydb"[:255],
                ec2=ec2,
            )
        ]
    return subnet_group, security_group_ids


def ensure_opensearch_serverless_networking(
    cluster,
    *,
    region: str,
    clients: Any | None = None,
) -> list[str]:
    """Find or create the cluster-scoped OpenSearch Serverless VPC endpoint."""

    pc = cluster.provider_config or {}
    if pc.get("opensearch_serverless_vpc_endpoint_ids"):
        return list(pc["opensearch_serverless_vpc_endpoint_ids"])

    if clients is None:
        import boto3

        ec2 = boto3.client("ec2", region_name=region)
        aoss = boto3.client("opensearchserverless", region_name=region)
        eks = boto3.client("eks", region_name=region)
    else:
        ec2, aoss, eks = clients
    vpc_id, subnet_ids, vpc_cidr = discover_vpc(
        cluster,
        region=region,
        ec2=ec2,
        eks=eks,
    )
    security_group_ids = list(pc.get("opensearch_serverless_security_group_ids") or [])
    if not security_group_ids:
        security_group_ids = [
            ensure_security_group(
                vpc_id=vpc_id,
                vpc_cidr=vpc_cidr,
                port=443,
                name=f"astrolift-{cluster.slug}-aoss"[:255],
                ec2=ec2,
            )
        ]
    endpoint_name = f"astrolift-{cluster.slug}-aoss"[:32].rstrip("-")
    next_token = ""
    while True:
        kwargs: dict[str, Any] = {"maxResults": 100}
        if next_token:
            kwargs["nextToken"] = next_token
        response = aoss.list_vpc_endpoints(**kwargs)
        for endpoint in response.get("vpcEndpointSummaries", []):
            if endpoint.get("name") == endpoint_name and endpoint.get("status") != "DELETING":
                return [str(endpoint["id"])]
        next_token = str(response.get("nextToken") or "")
        if not next_token:
            break
    response = aoss.create_vpc_endpoint(
        name=endpoint_name,
        vpcId=vpc_id,
        subnetIds=list(pc.get("opensearch_serverless_subnet_ids") or subnet_ids),
        securityGroupIds=security_group_ids,
    )
    detail = response.get("createVpcEndpointDetail") or {}
    endpoint_id = str(detail.get("id") or "")
    if not endpoint_id:
        raise RuntimeError("OpenSearch Serverless create_vpc_endpoint returned no endpoint id")
    return [endpoint_id]


def ensure_documentdb_networking(
    cluster,
    *,
    region: str,
    clients: Any | None = None,
) -> tuple[str, list[str]]:
    """Find or create a DocumentDB-specific subnet group and access SG."""

    pc = cluster.provider_config or {}
    if pc.get("documentdb_subnet_group") and pc.get("documentdb_security_group_ids"):
        return str(pc["documentdb_subnet_group"]), list(pc["documentdb_security_group_ids"])
    if clients is None:
        import boto3

        ec2 = boto3.client("ec2", region_name=region)
        docdb = boto3.client("docdb", region_name=region)
        eks = boto3.client("eks", region_name=region)
    else:
        ec2, docdb, eks = clients
    vpc_id, subnet_ids, vpc_cidr = discover_vpc(
        cluster,
        region=region,
        ec2=ec2,
        eks=eks,
    )
    subnet_group = str(pc.get("documentdb_subnet_group") or f"astrolift-{cluster.slug}-docdb")[:63]
    if not pc.get("documentdb_subnet_group"):
        try:
            docdb.create_db_subnet_group(
                DBSubnetGroupName=subnet_group,
                DBSubnetGroupDescription="astrolift managed DocumentDB subnets",
                SubnetIds=subnet_ids,
                Tags=_MANAGED_TAGS,
            )
        except Exception as exc:
            if (
                "DBSubnetGroupAlreadyExists" not in type(exc).__name__
                and "already exists"
                not in str(
                    exc,
                ).lower()
            ):
                raise
    security_group_ids = list(pc.get("documentdb_security_group_ids") or [])
    if not security_group_ids:
        security_group_ids = [
            ensure_security_group(
                vpc_id=vpc_id,
                vpc_cidr=vpc_cidr,
                port=27017,
                name=f"astrolift-{cluster.slug}-docdb"[:255],
                ec2=ec2,
            )
        ]
    return subnet_group, security_group_ids


def ensure_neptune_networking(
    cluster,
    *,
    region: str,
    clients: Any | None = None,
) -> tuple[str, list[str]]:
    """Find or create a Neptune-specific subnet group and access SG."""

    pc = cluster.provider_config or {}
    if pc.get("neptune_subnet_group") and pc.get("neptune_security_group_ids"):
        return str(pc["neptune_subnet_group"]), list(pc["neptune_security_group_ids"])
    if clients is None:
        import boto3

        ec2 = boto3.client("ec2", region_name=region)
        neptune = boto3.client("neptune", region_name=region)
        eks = boto3.client("eks", region_name=region)
    else:
        ec2, neptune, eks = clients
    vpc_id, subnet_ids, vpc_cidr = discover_vpc(
        cluster,
        region=region,
        ec2=ec2,
        eks=eks,
    )
    subnet_group = str(pc.get("neptune_subnet_group") or f"astrolift-{cluster.slug}-neptune")[:63]
    if not pc.get("neptune_subnet_group"):
        try:
            neptune.create_db_subnet_group(
                DBSubnetGroupName=subnet_group,
                DBSubnetGroupDescription="astrolift managed Neptune subnets",
                SubnetIds=list(pc.get("neptune_subnet_ids") or subnet_ids),
                Tags=_MANAGED_TAGS,
            )
        except Exception as exc:
            if "DBSubnetGroupAlreadyExists" not in type(exc).__name__ and "already exists" not in str(exc).lower():
                raise
    security_group_ids = list(pc.get("neptune_security_group_ids") or [])
    if not security_group_ids:
        security_group_ids = [
            ensure_security_group(
                vpc_id=vpc_id,
                vpc_cidr=vpc_cidr,
                port=8182,
                name=f"astrolift-{cluster.slug}-neptune"[:255],
                ec2=ec2,
            ),
        ]
    return subnet_group, security_group_ids


def ensure_redshift_networking(
    cluster,
    *,
    region: str,
    serverless: bool,
    clients: Any | None = None,
) -> tuple[str, list[str], list[str]]:
    """Resolve private Redshift subnet and security-group resources."""

    pc = cluster.provider_config or {}
    pinned_subnets = list(pc.get("redshift_subnet_ids") or [])
    pinned_groups = list(pc.get("redshift_security_group_ids") or [])
    pinned_subnet_group = str(pc.get("redshift_subnet_group") or "")
    if pinned_subnets and pinned_groups and (serverless or pinned_subnet_group):
        if serverless and len(set(pinned_subnets)) < 3:
            raise RuntimeError("Redshift Serverless requires at least three distinct subnets")
        return pinned_subnet_group, pinned_subnets, pinned_groups
    if clients is None:
        import boto3

        ec2 = boto3.client("ec2", region_name=region)
        redshift = boto3.client("redshift", region_name=region)
        eks = boto3.client("eks", region_name=region)
    else:
        ec2, redshift, eks = clients
    vpc_id, discovered_subnets, vpc_cidr = discover_vpc(
        cluster,
        region=region,
        ec2=ec2,
        eks=eks,
    )
    subnet_ids = pinned_subnets or discovered_subnets
    minimum_subnets = 3 if serverless else 2
    if len(set(subnet_ids)) < minimum_subnets:
        raise RuntimeError(
            f"Redshift {'Serverless' if serverless else 'provisioned'} requires subnets "
            f"in at least {minimum_subnets} Availability Zones",
        )
    security_group_ids = pinned_groups
    if not security_group_ids:
        security_group_ids = [
            ensure_security_group(
                vpc_id=vpc_id,
                vpc_cidr=vpc_cidr,
                port=5439,
                name=f"astrolift-{cluster.slug}-redshift"[:255],
                ec2=ec2,
            ),
        ]
    subnet_group = pinned_subnet_group or f"astrolift-{cluster.slug}-redshift"[:255]
    if not serverless and not pinned_subnet_group:
        try:
            redshift.create_cluster_subnet_group(
                ClusterSubnetGroupName=subnet_group,
                Description="astrolift managed Redshift subnets",
                SubnetIds=subnet_ids,
                Tags=_MANAGED_TAGS,
            )
        except Exception as exc:
            if "ClusterSubnetGroupAlreadyExists" not in type(exc).__name__ and "already exists" not in str(exc).lower():
                raise
    return subnet_group, subnet_ids, security_group_ids


def ensure_msk_networking(
    cluster,
    *,
    region: str,
    clients: Any | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve private subnets and a Kafka access SG for Amazon MSK.

    The four ingress ports cover provisioned plaintext/TLS/SCRAM/IAM and
    Serverless IAM bootstrap traffic. Operators can pin both lists to use
    narrower pre-created network policy.
    """

    pc = cluster.provider_config or {}
    pinned_subnets = list(pc.get("msk_subnet_ids") or [])
    pinned_groups = list(pc.get("msk_security_group_ids") or [])
    if pinned_subnets and pinned_groups:
        return pinned_subnets, pinned_groups
    if clients is None:
        import boto3

        ec2 = boto3.client("ec2", region_name=region)
        eks = boto3.client("eks", region_name=region)
    else:
        ec2, eks = clients
    vpc_id, discovered_subnets, vpc_cidr = discover_vpc(
        cluster,
        region=region,
        ec2=ec2,
        eks=eks,
    )
    # Provisioned MSK accepts two or three client subnets. Keep explicit
    # operator pins intact so invalid policy fails visibly, but choose three
    # AZs from broad EKS VPC discovery by default.
    subnet_ids = pinned_subnets or discovered_subnets[:3]
    if len(set(subnet_ids)) < 2:
        raise RuntimeError("Amazon MSK requires private subnets in at least two Availability Zones")
    security_group_ids = pinned_groups
    if not security_group_ids:
        sg_id = ensure_security_group(
            vpc_id=vpc_id,
            vpc_cidr=vpc_cidr,
            port=9092,
            name=f"astrolift-{cluster.slug}-msk"[:255],
            ec2=ec2,
        )
        for port in (9094, 9096, 9098):
            try:
                ec2.authorize_security_group_ingress(
                    GroupId=sg_id,
                    IpPermissions=[
                        {
                            "IpProtocol": "tcp",
                            "FromPort": port,
                            "ToPort": port,
                            "IpRanges": [
                                {"CidrIp": vpc_cidr, "Description": "astrolift Kafka clients"},
                            ],
                        },
                    ],
                )
            except Exception as exc:
                if "InvalidPermission.Duplicate" not in str(exc):
                    raise
        security_group_ids = [sg_id]
    return subnet_ids, security_group_ids


def ensure_mq_networking(
    cluster,
    *,
    region: str,
    clients: Any | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve private multi-AZ subnets and TLS broker ingress for Amazon MQ."""

    pc = cluster.provider_config or {}
    pinned_subnets = list(pc.get("mq_subnet_ids") or [])
    pinned_groups = list(pc.get("mq_security_group_ids") or [])
    if pinned_subnets and pinned_groups:
        return pinned_subnets, pinned_groups
    if clients is None:
        import boto3

        ec2 = boto3.client("ec2", region_name=region)
        eks = boto3.client("eks", region_name=region)
    else:
        ec2, eks = clients
    vpc_id, discovered_subnets, vpc_cidr = discover_vpc(
        cluster,
        region=region,
        ec2=ec2,
        eks=eks,
    )
    subnet_ids = pinned_subnets or discovered_subnets[:3]
    if not subnet_ids:
        raise RuntimeError("Amazon MQ requires at least one subnet")
    security_group_ids = pinned_groups
    if not security_group_ids:
        ports = (443, 5671, 61614, 61617, 61619, 8883)
        sg_id = ensure_security_group(
            vpc_id=vpc_id,
            vpc_cidr=vpc_cidr,
            port=ports[0],
            name=f"astrolift-{cluster.slug}-mq"[:255],
            ec2=ec2,
        )
        for port in ports[1:]:
            try:
                ec2.authorize_security_group_ingress(
                    GroupId=sg_id,
                    IpPermissions=[
                        {
                            "IpProtocol": "tcp",
                            "FromPort": port,
                            "ToPort": port,
                            "IpRanges": [
                                {"CidrIp": vpc_cidr, "Description": "astrolift MQ clients"},
                            ],
                        },
                    ],
                )
            except Exception as exc:
                if "InvalidPermission.Duplicate" not in str(exc):
                    raise
        security_group_ids = [sg_id]
    return subnet_ids, security_group_ids


def ensure_keyspaces_networking(
    cluster,
    *,
    region: str,
    clients: Any | None = None,
) -> str:
    """Find or create a private Amazon Keyspaces interface VPC endpoint."""

    pc = cluster.provider_config or {}
    if pc.get("keyspaces_vpc_endpoint_id"):
        return str(pc["keyspaces_vpc_endpoint_id"])
    if clients is None:
        import boto3

        ec2 = boto3.client("ec2", region_name=region)
        eks = boto3.client("eks", region_name=region)
    else:
        ec2, eks = clients
    vpc_id, subnet_ids, vpc_cidr = discover_vpc(
        cluster,
        region=region,
        ec2=ec2,
        eks=eks,
    )
    security_group_ids = list(pc.get("keyspaces_security_group_ids") or [])
    if not security_group_ids:
        security_group_ids = [
            ensure_security_group(
                vpc_id=vpc_id,
                vpc_cidr=vpc_cidr,
                port=9142,
                name=f"astrolift-{cluster.slug}-keyspaces"[:255],
                ec2=ec2,
            ),
        ]
    service_name = f"com.amazonaws.{region}.cassandra"
    endpoint_name = f"astrolift-{cluster.slug}-keyspaces"[:255]
    existing = ec2.describe_vpc_endpoints(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "service-name", "Values": [service_name]},
        ],
    ).get("VpcEndpoints", [])
    for endpoint in existing:
        if endpoint.get("State") not in {"deleted", "deleting", "failed", "rejected"}:
            return str(endpoint["VpcEndpointId"])
    response = ec2.create_vpc_endpoint(
        VpcEndpointType="Interface",
        VpcId=vpc_id,
        ServiceName=service_name,
        SubnetIds=list(pc.get("keyspaces_subnet_ids") or subnet_ids),
        SecurityGroupIds=security_group_ids,
        PrivateDnsEnabled=True,
        TagSpecifications=[
            {
                "ResourceType": "vpc-endpoint",
                "Tags": [
                    *_MANAGED_TAGS,
                    {"Key": "Name", "Value": endpoint_name},
                ],
            },
        ],
    )
    endpoint_id = str((response.get("VpcEndpoint") or {}).get("VpcEndpointId") or "")
    if not endpoint_id:
        raise RuntimeError("Amazon Keyspaces create_vpc_endpoint returned no endpoint id")
    return endpoint_id
