"""Prepare cluster-owned CloudWatch infrastructure; never install or activate.

Future orchestration must admit the current actor/cluster/connection before
calling this mutating helper, then prove readiness and synthetic ingestion
before persisting reader configuration. Existing external resources are not
adopted. This stage only covers EC2-node pod logs, not Fargate workloads.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from _sdk.cloud_credentials import CredentialMode
from aws._cloudwatch_collector import NAMESPACE, SERVICE_ACCOUNT, staged_component
from aws.session import aws_client

if TYPE_CHECKING:
    from collections.abc import Callable

    from _sdk.cloud_credentials import CloudCredential
    from _sdk.cluster import BootstrapComponent

POLICY_NAME = "astrolift-cloudwatch-collector"


class CollectorPreparationError(RuntimeError):
    """Safe stage refusal without upstream response bodies or credentials."""


@dataclass(frozen=True, slots=True)
class CollectorSpec:
    cluster_guid: str
    cluster_name: str
    region: str
    credential: CloudCredential
    permissions_boundary_arn: str = ""
    retention_days: int = 30


@dataclass(frozen=True, slots=True)
class CollectorStage:
    cluster_guid: str
    cluster_arn: str
    region: str
    log_group: str
    log_group_arn: str
    irsa_role_arn: str
    oidc_issuer: str
    infrastructure_prepared: bool = True
    collector_installed: bool = False
    ingestion_verified: bool = False

    def component(self) -> BootstrapComponent:
        return staged_component(irsa_role_arn=self.irsa_role_arn, region=self.region, log_group=self.log_group)

    def candidate_reader_config(self) -> dict[str, Any]:
        return {
            "log_driver": "cloudwatch_logs",
            "log_config": {
                "region": self.region,
                "log_group": self.log_group,
                "log_stream_name_prefix": "from-fluent-bit-",
            },
        }

    def reader_policy(self) -> dict[str, Any]:
        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["logs:FilterLogEvents"],
                    "Resource": self.log_group_arn + ":*",
                }
            ],
        }


def _code(exc: Exception) -> str:
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))


def _required(condition: bool, message: str) -> None:
    if not condition:
        raise CollectorPreparationError(message)


def _owned(tags: object, guid: str) -> bool:
    expected = {
        "astrolift.io/managed-by": "platform",
        "astrolift.io/cluster": guid,
        "astrolift.io/component": "fluent-bit-cloudwatch",
    }
    if isinstance(tags, list):
        keys = [entry.get("Key") for entry in tags if isinstance(entry, dict)]
        if len(keys) != len(tags) or len(set(keys)) != len(keys):
            return False
        tags = {entry["Key"]: entry.get("Value") for entry in tags}
    return isinstance(tags, dict) and all(tags.get(key) == value for key, value in expected.items())


def prepare_collector(
    spec: CollectorSpec,
    *,
    client_factory: Callable[..., Any] = aws_client,
) -> CollectorStage:
    """Retry-safe infrastructure stage with no Kubernetes or database writes.

    The injectable factory must follow aws_client's credential contract; its
    default is the shared credential/session cache, including ExternalId.
    Identity/ownership reads precede any write; concurrent name creation is
    refused and can retry only after fresh owned-resource inspection.
    """
    try:
        return _prepare(spec, client_factory)
    except CollectorPreparationError:
        raise
    except Exception:
        raise CollectorPreparationError("CloudWatch collector infrastructure preparation failed") from None


def _prepare(spec: CollectorSpec, client_factory: Callable[..., Any]) -> CollectorStage:
    _required(str(UUID(spec.cluster_guid)) == spec.cluster_guid, "Collector requires an immutable cluster UUID")
    _required(
        bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", spec.cluster_name)),
        "Collector cluster identity is invalid",
    )
    _required(bool(re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", spec.region)), "Collector region is invalid")
    credential = spec.credential
    _required(
        credential.cloud == "aws" and credential.mode in (CredentialMode.AMBIENT, CredentialMode.AWS_ASSUME_ROLE),
        "Collector credential mode is unsupported",
    )
    _required(bool(re.fullmatch(r"\d{12}", credential.declared_account)), "Collector requires a declared AWS account")
    _required(
        spec.retention_days in {1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653},
        "Collector retention is invalid",
    )
    clients = {
        name: client_factory(name, region=spec.region, credential=credential) for name in ("sts", "eks", "iam", "logs")
    }
    caller = clients["sts"].get_caller_identity()
    account = str(caller.get("Account", ""))
    caller_arn = str(caller.get("Arn", ""))
    _required(account == credential.declared_account, "Collector account does not match the registered credential")
    parts = caller_arn.split(":")
    _required(
        len(parts) >= 6
        and parts[0] == "arn"
        and parts[1] in {"aws", "aws-cn", "aws-us-gov"}
        and parts[2] in {"iam", "sts"}
        and parts[4] == account,
        "Collector caller identity is invalid",
    )
    partition = parts[1]
    eks = clients["eks"].describe_cluster(name=spec.cluster_name)["cluster"]
    cluster_arn = f"arn:{partition}:eks:{spec.region}:{account}:cluster/{spec.cluster_name}"
    _required(
        eks.get("arn") == cluster_arn and eks.get("status") == "ACTIVE",
        "Collector EKS target is unavailable or mismatched",
    )
    issuer = str(((eks.get("identity") or {}).get("oidc") or {}).get("issuer", ""))
    suffix = "amazonaws.com.cn" if partition == "aws-cn" else "amazonaws.com"
    _required(
        bool(
            re.fullmatch(rf"https://oidc\.eks\.{re.escape(spec.region)}\.{re.escape(suffix)}/id/[A-Za-z0-9]+", issuer)
        ),
        "Collector EKS OIDC issuer is unavailable or mismatched",
    )
    issuer = issuer.removeprefix("https://")
    iam = clients["iam"]
    provider_arn = f"arn:{partition}:iam::{account}:oidc-provider/{issuer}"
    provider = iam.get_open_id_connect_provider(OpenIDConnectProviderArn=provider_arn)
    _required(
        str(provider.get("Url", "")).removeprefix("https://") == issuer
        and "sts.amazonaws.com" in provider.get("ClientIDList", []),
        "Collector OIDC provider is not registered for this cluster",
    )
    boundary = spec.permissions_boundary_arn
    _required(
        not boundary or bool(re.fullmatch(rf"arn:{partition}:iam::{account}:policy/[A-Za-z0-9+=,.@_/-]+", boundary)),
        "Collector permissions boundary is mismatched",
    )
    role_name = f"astrolift-{spec.cluster_guid}-fluent-bit"
    role_arn = f"arn:{partition}:iam::{account}:role/astrolift/{role_name}"
    group = f"/astrolift/clusters/{spec.cluster_guid}/pods"
    group_arn = f"arn:{partition}:logs:{spec.region}:{account}:log-group:{group}"
    tags = {
        "astrolift.io/managed-by": "platform",
        "astrolift.io/cluster": spec.cluster_guid,
        "astrolift.io/component": "fluent-bit-cloudwatch",
    }
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
    except Exception as exc:
        if _code(exc) != "NoSuchEntity":
            raise
        role = None
    if role:
        _required(
            role.get("Arn") == role_arn
            and role.get("Path") == "/astrolift/"
            and _owned(role.get("Tags"), spec.cluster_guid),
            "Collector role is not owned by this cluster",
        )
        _required(
            (role.get("PermissionsBoundary") or {}).get("PermissionsBoundaryArn", "") == boundary,
            "Collector role permissions boundary is mismatched",
        )
        inline = iam.list_role_policies(RoleName=role_name)
        attached = iam.list_attached_role_policies(RoleName=role_name)
        _required(
            not inline.get("IsTruncated")
            and set(inline.get("PolicyNames", [])) <= {POLICY_NAME}
            and not attached.get("IsTruncated")
            and not attached.get("AttachedPolicies"),
            "Collector role has unrelated policies; operator review is required",
        )
    logs = clients["logs"]
    found = logs.describe_log_groups(logGroupNamePrefix=group, limit=1)
    groups = found.get("logGroups", [])
    existing = next((item for item in groups if item.get("logGroupName") == group), None)
    if existing:
        _required(
            str(existing.get("arn", "")).removesuffix(":*") == group_arn, "Collector log group identity is mismatched"
        )
        _required(
            _owned(logs.list_tags_for_resource(resourceArn=group_arn).get("tags"), spec.cluster_guid),
            "Collector log group is not owned by this cluster",
        )
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Principal": {"Federated": provider_arn},
                "Condition": {
                    "StringEquals": {
                        f"{issuer}:sub": f"system:serviceaccount:{NAMESPACE}:{SERVICE_ACCOUNT}",
                        f"{issuer}:aud": "sts.amazonaws.com",
                    }
                },
            }
        ],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["logs:DescribeLogStreams"], "Resource": group_arn + ":*"},
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": group_arn + ":log-stream:*",
            },
        ],
    }
    current_policy = None
    if role and POLICY_NAME in inline.get("PolicyNames", []):
        current_policy = iam.get_role_policy(RoleName=role_name, PolicyName=POLICY_NAME).get("PolicyDocument")
    if not existing:
        logs.create_log_group(logGroupName=group, tags=tags)
    if not existing or existing.get("retentionInDays") != spec.retention_days:
        logs.put_retention_policy(logGroupName=group, retentionInDays=spec.retention_days)
    if not role:
        args = {
            "RoleName": role_name,
            "Path": "/astrolift/",
            "AssumeRolePolicyDocument": json.dumps(trust),
            "Tags": [{"Key": key, "Value": value} for key, value in tags.items()],
        }
        if boundary:
            args["PermissionsBoundary"] = boundary
        _required(iam.create_role(**args)["Role"]["Arn"] == role_arn, "Collector created role identity is mismatched")
    elif role.get("AssumeRolePolicyDocument") != trust:
        iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(trust))
    if current_policy != policy:
        iam.put_role_policy(RoleName=role_name, PolicyName=POLICY_NAME, PolicyDocument=json.dumps(policy))
    return CollectorStage(spec.cluster_guid, cluster_arn, spec.region, group, group_arn, role_arn, issuer)
