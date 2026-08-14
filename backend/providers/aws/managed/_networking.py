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
