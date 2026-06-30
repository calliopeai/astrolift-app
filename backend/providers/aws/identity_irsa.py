"""AWS IRSA WorkloadIdentityDriver (#33).

IAM Roles for Service Accounts: pods on EKS assume an IAM role
without static credentials by exchanging the projected SA token
for STS credentials via the cluster's OIDC provider.

Per #8's workload_identity policy, this driver:
- creates IAM roles with a trust policy bound to the cluster's
  OIDC issuer + a specific (namespace, SA) subject claim
- attaches inline / managed policies for the role's permissions
- annotates the SA with `eks.amazonaws.com/role-arn` so the EKS
  pod-identity webhook injects credentials

Naming convention (#766): tenant IAM roles use ``astrolift-tenant-<slug>``.
Mirrors the ``<platform>-tenant-<slug>`` pattern proven in production
AWS Django apps — clearly scoped, easy to grep in audit logs, fits
inside IAM's 64-char role-name limit for any reasonable slug.

Legacy / backward-compat: pre-#766 rows may have roles named
``astrolift-<slug>`` (no ``-tenant-`` infix); ``describe_identity``
looks up both shapes so the operability surface keeps working
across the rename.  No data migration of existing IAM roles is
attempted — they stay under their original names; new roles use
the canonical pattern.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.identity import IdentityBinding, WorkloadIdentityDriver
from aws._errors import NotFoundError, map_client_error

# The aws-ebs-csi-driver Helm chart runs its controller as the
# ``ebs-csi-controller-sa`` ServiceAccount in the platform bootstrap
# namespace ``astrolift-system`` (where install_cluster_prereqs deploys the
# HelmRelease) — NOT kube-system (#1024). The self-provisioned IRSA trust
# must bind that exact subject (system:serviceaccount:astrolift-system:
# ebs-csi-controller-sa) or the controller's AssumeRoleWithWebIdentity 403s
# and EBS CreateVolume fails, leaving StatefulSet PVCs Pending.
EBS_CSI_NAMESPACE = "astrolift-system"
EBS_CSI_CONTROLLER_SA = "ebs-csi-controller-sa"
# AWS-managed policy granting the EBS CSI controller the EC2 volume
# create/attach/detach/delete permissions it needs to provision PVs.
AMAZON_EBS_CSI_DRIVER_POLICY_ARN = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"

# The aws-load-balancer-controller + external-dns Helm charts run their
# controllers in the platform bootstrap namespace ``astrolift-system`` (where
# install_cluster_prereqs deploys every HelmRelease). The chart values pin the
# ServiceAccount names (``aws-load-balancer-controller`` / ``external-dns``,
# the chart defaults) so the IRSA trust subject is deterministic and doesn't
# drift with the Flux release name (#1044). The self-provisioned trust must
# bind these exact subjects or AssumeRoleWithWebIdentity 403s and ingress /
# DNS reconciliation never works.
ALB_CONTROLLER_NAMESPACE = "astrolift-system"
ALB_CONTROLLER_SA = "aws-load-balancer-controller"
EXTERNAL_DNS_NAMESPACE = "astrolift-system"
EXTERNAL_DNS_SA = "external-dns"

# Inline IAM policy statements for the AWS Load Balancer Controller. This is
# the canonical ``AWSLoadBalancerControllerIAMPolicy`` permission set (the
# upstream policy published alongside the v2.x controller): EC2/ELBv2 describe
# + create/modify, tag management scoped to the controller's cluster tag,
# WAFv2/WAF-regional/Shield association, ACM + IAM server-cert describe, and
# cognito-idp describe for OIDC-protected listeners. There is no AWS-managed
# policy for this controller, so it's attached as an inline policy.
ALB_CONTROLLER_POLICY_STATEMENTS: list[dict[str, Any]] = [
    {
        "Effect": "Allow",
        "Action": ["iam:CreateServiceLinkedRole"],
        "Resource": "*",
        "Condition": {"StringEquals": {"iam:AWSServiceName": "elasticloadbalancing.amazonaws.com"}},
    },
    {
        "Effect": "Allow",
        "Action": [
            "ec2:DescribeAccountAttributes",
            "ec2:DescribeAddresses",
            "ec2:DescribeAvailabilityZones",
            "ec2:DescribeInternetGateways",
            "ec2:DescribeVpcs",
            "ec2:DescribeVpcPeeringConnections",
            "ec2:DescribeSubnets",
            "ec2:DescribeSecurityGroups",
            "ec2:DescribeInstances",
            "ec2:DescribeNetworkInterfaces",
            "ec2:DescribeTags",
            "ec2:GetCoipPoolUsage",
            "ec2:DescribeCoipPools",
            "elasticloadbalancing:DescribeLoadBalancers",
            "elasticloadbalancing:DescribeLoadBalancerAttributes",
            "elasticloadbalancing:DescribeListeners",
            "elasticloadbalancing:DescribeListenerCertificates",
            "elasticloadbalancing:DescribeSSLPolicies",
            "elasticloadbalancing:DescribeRules",
            "elasticloadbalancing:DescribeTargetGroups",
            "elasticloadbalancing:DescribeTargetGroupAttributes",
            "elasticloadbalancing:DescribeTargetHealth",
            "elasticloadbalancing:DescribeTags",
            "elasticloadbalancing:DescribeTrustStores",
        ],
        "Resource": "*",
    },
    {
        "Effect": "Allow",
        "Action": [
            "cognito-idp:DescribeUserPoolClient",
            "acm:ListCertificates",
            "acm:DescribeCertificate",
            "iam:ListServerCertificates",
            "iam:GetServerCertificate",
            "waf-regional:GetWebACL",
            "waf-regional:GetWebACLForResource",
            "waf-regional:AssociateWebACL",
            "waf-regional:DisassociateWebACL",
            "wafv2:GetWebACL",
            "wafv2:GetWebACLForResource",
            "wafv2:AssociateWebACL",
            "wafv2:DisassociateWebACL",
            "shield:GetSubscriptionState",
            "shield:DescribeProtection",
            "shield:CreateProtection",
            "shield:DeleteProtection",
        ],
        "Resource": "*",
    },
    {
        "Effect": "Allow",
        "Action": ["ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress"],
        "Resource": "*",
    },
    {
        "Effect": "Allow",
        "Action": ["ec2:CreateSecurityGroup"],
        "Resource": "*",
    },
    {
        "Effect": "Allow",
        "Action": ["ec2:CreateTags"],
        "Resource": "arn:aws:ec2:*:*:security-group/*",
        "Condition": {
            "StringEquals": {"ec2:CreateAction": "CreateSecurityGroup"},
            "Null": {"aws:RequestTag/elbv2.k8s.aws/cluster": "false"},
        },
    },
    {
        "Effect": "Allow",
        "Action": ["ec2:CreateTags", "ec2:DeleteTags"],
        "Resource": "arn:aws:ec2:*:*:security-group/*",
        "Condition": {
            "Null": {
                "aws:RequestTag/elbv2.k8s.aws/cluster": "true",
                "aws:ResourceTag/elbv2.k8s.aws/cluster": "false",
            }
        },
    },
    {
        "Effect": "Allow",
        "Action": [
            "ec2:AuthorizeSecurityGroupIngress",
            "ec2:RevokeSecurityGroupIngress",
            "ec2:DeleteSecurityGroup",
        ],
        "Resource": "*",
        "Condition": {"Null": {"aws:ResourceTag/elbv2.k8s.aws/cluster": "false"}},
    },
    {
        "Effect": "Allow",
        "Action": ["elasticloadbalancing:CreateLoadBalancer", "elasticloadbalancing:CreateTargetGroup"],
        "Resource": "*",
        "Condition": {"Null": {"aws:RequestTag/elbv2.k8s.aws/cluster": "false"}},
    },
    {
        "Effect": "Allow",
        "Action": [
            "elasticloadbalancing:CreateListener",
            "elasticloadbalancing:DeleteListener",
            "elasticloadbalancing:CreateRule",
            "elasticloadbalancing:DeleteRule",
        ],
        "Resource": "*",
    },
    {
        "Effect": "Allow",
        "Action": ["elasticloadbalancing:AddTags", "elasticloadbalancing:RemoveTags"],
        "Resource": [
            "arn:aws:elasticloadbalancing:*:*:targetgroup/*/*",
            "arn:aws:elasticloadbalancing:*:*:loadbalancer/net/*/*",
            "arn:aws:elasticloadbalancing:*:*:loadbalancer/app/*/*",
        ],
        "Condition": {
            "Null": {
                "aws:RequestTag/elbv2.k8s.aws/cluster": "true",
                "aws:ResourceTag/elbv2.k8s.aws/cluster": "false",
            }
        },
    },
    {
        "Effect": "Allow",
        "Action": ["elasticloadbalancing:AddTags", "elasticloadbalancing:RemoveTags"],
        "Resource": [
            "arn:aws:elasticloadbalancing:*:*:listener/net/*/*/*",
            "arn:aws:elasticloadbalancing:*:*:listener/app/*/*/*",
            "arn:aws:elasticloadbalancing:*:*:listener-rule/net/*/*/*",
            "arn:aws:elasticloadbalancing:*:*:listener-rule/app/*/*/*",
        ],
    },
    {
        "Effect": "Allow",
        "Action": [
            "elasticloadbalancing:ModifyLoadBalancerAttributes",
            "elasticloadbalancing:SetIpAddressType",
            "elasticloadbalancing:SetSecurityGroups",
            "elasticloadbalancing:SetSubnets",
            "elasticloadbalancing:DeleteLoadBalancer",
            "elasticloadbalancing:ModifyTargetGroup",
            "elasticloadbalancing:ModifyTargetGroupAttributes",
            "elasticloadbalancing:DeleteTargetGroup",
        ],
        "Resource": "*",
        "Condition": {"Null": {"aws:ResourceTag/elbv2.k8s.aws/cluster": "false"}},
    },
    {
        "Effect": "Allow",
        "Action": ["elasticloadbalancing:AddTags"],
        "Resource": [
            "arn:aws:elasticloadbalancing:*:*:targetgroup/*/*",
            "arn:aws:elasticloadbalancing:*:*:loadbalancer/net/*/*",
            "arn:aws:elasticloadbalancing:*:*:loadbalancer/app/*/*",
        ],
        "Condition": {
            "StringEquals": {"elasticloadbalancing:CreateAction": ["CreateTargetGroup", "CreateLoadBalancer"]},
            "Null": {"aws:RequestTag/elbv2.k8s.aws/cluster": "false"},
        },
    },
    {
        "Effect": "Allow",
        "Action": ["elasticloadbalancing:RegisterTargets", "elasticloadbalancing:DeregisterTargets"],
        "Resource": "arn:aws:elasticloadbalancing:*:*:targetgroup/*/*",
    },
    {
        "Effect": "Allow",
        "Action": [
            "elasticloadbalancing:SetWebAcl",
            "elasticloadbalancing:ModifyListener",
            "elasticloadbalancing:AddListenerCertificates",
            "elasticloadbalancing:RemoveListenerCertificates",
            "elasticloadbalancing:ModifyRule",
        ],
        "Resource": "*",
    },
]

# Inline IAM policy statements for external-dns. ``ChangeResourceRecordSets``
# is scoped to hosted zones (record CRUD); the list actions need ``*`` because
# external-dns enumerates zones + record sets to discover what it manages.
EXTERNAL_DNS_POLICY_STATEMENTS: list[dict[str, Any]] = [
    {
        "Effect": "Allow",
        "Action": ["route53:ChangeResourceRecordSets"],
        "Resource": ["arn:aws:route53:::hostedzone/*"],
    },
    {
        "Effect": "Allow",
        "Action": [
            "route53:ListHostedZones",
            "route53:ListResourceRecordSets",
            "route53:ListTagsForResource",
        ],
        "Resource": ["*"],
    },
]


@dataclass(frozen=True)
class IRSAConfig:
    region: str
    account_id: str
    cluster_oidc_issuer: str
    """The EKS cluster's OIDC provider URL (without https://).
    e.g. ``oidc.eks.us-east-1.amazonaws.com/id/ABC123...``.
    Fetched from EKS DescribeCluster + cached on the cluster row."""

    role_path: str = "/"
    """IAM path for platform-managed roles. Defaults to root (``/``) so
    the role ARN is ``role/astrolift-<...>`` — matching the name-prefix
    (``astrolift-*``) the control-plane task role is scoped to grant
    iam:CreateRole on. A non-root path (e.g. ``/astrolift/``) would put
    the role at ``role/astrolift/astrolift-<...>``, which that prefix
    does NOT match, so CreateRole would be denied. Roles are still
    scoped/filterable via the ``astrolift-`` name prefix + the
    ``astrolift.io/managed-by`` tag. Override per-install only if the
    task-role policy is broadened to cover the chosen path."""


class IRSADriver(WorkloadIdentityDriver):
    def __init__(self, *, config: IRSAConfig, iam_client: Any | None = None) -> None:
        self._config = config
        if iam_client is not None:
            self._iam = iam_client
        else:
            import boto3

            self._iam = boto3.client("iam", region_name=config.region)

    @driver_op(cloud="aws", driver="identity", audit=True, sensitive_kind="identity.bind")
    def bind_service_account(
        self,
        cluster: str,
        namespace: str,
        sa_name: str,
        identity_role: str,
    ) -> dict[str, str]:
        """Returns the annotation map the platform applies to the
        ServiceAccount manifest."""
        role_arn = self._role_arn(identity_role)
        # Verify the role exists + has a trust policy that includes
        # this (namespace, sa_name) subject. If absent, update.
        self._ensure_trust_includes(
            role_name=identity_role,
            namespace=namespace,
            sa_name=sa_name,
        )
        return {"eks.amazonaws.com/role-arn": role_arn}

    @driver_op(cloud="aws", driver="identity", audit=True, sensitive_kind="identity.create_role")
    def create_identity_role(
        self,
        name: str,
        permissions: list[dict[str, Any]],
    ) -> str:
        """Creates a role with an OIDC-trust policy + an inline
        policy from the high-level permissions."""
        trust_policy = self._oidc_trust_policy()
        try:
            response = self._iam.create_role(
                Path=self._config.role_path,
                RoleName=name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description=f"Astrolift workload identity role for {name}",
                Tags=[
                    {
                        "Key": "astrolift.io/managed-by",
                        "Value": "platform",
                    }
                ],
            )
        except self._iam.exceptions.EntityAlreadyExistsException:
            # Idempotent + self-healing: the role exists, but its trust or
            # inline policy may be stale — e.g. the OIDC issuer was empty on
            # the first create (producing a broken oidc-provider/ principal),
            # or the service's grants changed. Re-assert both against the
            # current (correct) trust + permissions. bind_service_account
            # runs next and re-adds the (namespace, sa) subject, so resetting
            # to the subject-less base trust here is safe.
            try:
                self._iam.update_assume_role_policy(
                    RoleName=name,
                    PolicyDocument=json.dumps(trust_policy),
                )
                if permissions:
                    self._iam.put_role_policy(
                        RoleName=name,
                        PolicyName="astrolift-workload-policy",
                        PolicyDocument=json.dumps(
                            {
                                "Version": "2012-10-17",
                                "Statement": permissions,
                            }
                        ),
                    )
            except Exception as exc:
                raise map_client_error(exc) from exc
            return self._role_arn(name)
        except Exception as exc:
            raise map_client_error(exc) from exc

        # Attach inline policy from the permissions list.
        if permissions:
            try:
                self._iam.put_role_policy(
                    RoleName=name,
                    PolicyName="astrolift-workload-policy",
                    PolicyDocument=json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": permissions,
                        }
                    ),
                )
            except Exception as exc:
                raise map_client_error(exc) from exc

        return response["Role"]["Arn"]

    @driver_op(cloud="aws", driver="identity", audit=True, sensitive_kind="identity.attach_policy")
    def attach_policy(self, role: str, policy: str) -> None:
        """Attach a managed policy ARN."""
        try:
            self._iam.attach_role_policy(
                RoleName=role,
                PolicyArn=policy,
            )
        except self._iam.exceptions.NoSuchEntityException as exc:
            raise NotFoundError(f"role {role} or policy {policy} not found") from exc
        except Exception as exc:
            raise map_client_error(exc) from exc

    @driver_op(cloud="aws", driver="identity", audit=True, sensitive_kind="identity.provision_ebs_csi_role")
    def provision_ebs_csi_role(self, role_name: str) -> str:
        """Self-provision the IRSA role the aws-ebs-csi-driver controller assumes (#1024).

        Astrolift installs the EBS CSI driver in-cluster via Helm and mints
        its IAM role itself — no EKS managed addon, no out-of-band Terraform.
        Creates the OIDC-trust role, scopes its trust to the
        ``kube-system:ebs-csi-controller-sa`` subject, and attaches the
        AWS-managed ``AmazonEBSCSIDriverPolicy``.

        Idempotent: re-runs reconcile the trust subject + re-attach the
        managed policy (both AWS ops are safe to repeat), mirroring
        ``create_identity_role``'s self-healing. Returns the role ARN."""
        role_arn = self.create_identity_role(role_name, permissions=[])
        self._ensure_trust_includes(
            role_name=role_name,
            namespace=EBS_CSI_NAMESPACE,
            sa_name=EBS_CSI_CONTROLLER_SA,
        )
        self.attach_policy(role_name, AMAZON_EBS_CSI_DRIVER_POLICY_ARN)
        return role_arn

    @driver_op(cloud="aws", driver="identity", audit=True, sensitive_kind="identity.provision_alb_controller_role")
    def provision_alb_controller_role(self, role_name: str) -> str:
        """Self-provision the IRSA role the aws-load-balancer-controller assumes (#1044).

        The controller renders Kubernetes Ingress as ALBs / Service
        type=LoadBalancer as NLBs and needs the canonical
        ``AWSLoadBalancerControllerIAMPolicy`` permission set. There is no
        AWS-managed policy for it, so the permissions are attached inline.
        Creates the OIDC-trust role, scopes its trust to the
        ``astrolift-system:aws-load-balancer-controller`` subject, and (via
        ``create_identity_role``) writes the inline policy.

        Idempotent: re-runs reconcile the trust subject + re-write the inline
        policy. Returns the role ARN."""
        role_arn = self.create_identity_role(role_name, permissions=ALB_CONTROLLER_POLICY_STATEMENTS)
        self._ensure_trust_includes(
            role_name=role_name,
            namespace=ALB_CONTROLLER_NAMESPACE,
            sa_name=ALB_CONTROLLER_SA,
        )
        return role_arn

    @driver_op(cloud="aws", driver="identity", audit=True, sensitive_kind="identity.provision_external_dns_role")
    def provision_external_dns_role(self, role_name: str) -> str:
        """Self-provision the IRSA role external-dns assumes (#1044).

        external-dns syncs Route53 records from Ingress / Service annotations;
        it needs ``route53:ChangeResourceRecordSets`` on hosted zones plus the
        list actions to discover zones + record sets. Attached inline (no
        AWS-managed policy fits). Creates the OIDC-trust role, scopes its trust
        to the ``astrolift-system:external-dns`` subject, and writes the inline
        policy.

        Idempotent: re-runs reconcile the trust subject + re-write the inline
        policy. Returns the role ARN."""
        role_arn = self.create_identity_role(role_name, permissions=EXTERNAL_DNS_POLICY_STATEMENTS)
        self._ensure_trust_includes(
            role_name=role_name,
            namespace=EXTERNAL_DNS_NAMESPACE,
            sa_name=EXTERNAL_DNS_SA,
        )
        return role_arn

    @driver_op(cloud="aws", driver="identity", audit=True, sensitive_kind="identity.delete_role")
    def delete_identity_role(self, role: str) -> None:
        """Detach + delete in the right order. AWS rejects role
        deletion when policies still attached."""
        try:
            # Inline policies
            attached_inline = self._iam.list_role_policies(
                RoleName=role,
            )
            for policy_name in attached_inline.get("PolicyNames", []):
                self._iam.delete_role_policy(
                    RoleName=role,
                    PolicyName=policy_name,
                )
            # Managed policies
            attached_managed = self._iam.list_attached_role_policies(
                RoleName=role,
            )
            for entry in attached_managed.get("AttachedPolicies", []):
                self._iam.detach_role_policy(
                    RoleName=role,
                    PolicyArn=entry["PolicyArn"],
                )
            self._iam.delete_role(RoleName=role)
        except self._iam.exceptions.NoSuchEntityException:
            # Idempotent delete: the role is already absent, which is the
            # desired end state. Treat as success so teardown completes (and
            # re-runs are safe) instead of halting on a missing role — e.g. a
            # role that was never created, or already deleted (#998).
            return
        except Exception as exc:
            raise map_client_error(exc) from exc

    # ---- observability reads (#377) -------------------------------

    @driver_op(cloud="aws", driver="identity")
    def describe_identity(self, app_slug: str) -> IdentityBinding | None:
        """Return the IRSA binding for an app, or ``None`` when no
        matching role exists.

        Tries the canonical name first (``astrolift-tenant-<slug>`` per
        #766) then falls back to the legacy name (``astrolift-<slug>``)
        for pre-rename rows.  ``last_used_at`` comes from
        ``RoleLastUsed`` on ``GetRole`` — IAM populates that lazily, so
        a freshly-created role reports None until the first STS exchange.

        ``trust_policy_summary`` is a 1-line abstract built from the
        OIDC subject conditions in the trust policy: ``"OIDC trust:
        system:serviceaccount:<ns>:<sa>"`` for a single subject, or
        ``"OIDC trust: <N> service account(s)"`` for multi-subject roles.
        Operators who need the full policy click through to IAM."""
        # Canonical name first, then legacy fallback.
        response = None
        for role_name in (f"astrolift-tenant-{app_slug}", f"astrolift-{app_slug}"):
            try:
                response = self._iam.get_role(RoleName=role_name)
                break
            except self._iam.exceptions.NoSuchEntityException:
                continue
            except Exception as exc:
                raise map_client_error(exc) from exc
        if response is None:
            return None

        role = response["Role"]
        trust_doc = role.get("AssumeRolePolicyDocument") or {}
        if isinstance(trust_doc, str):
            from urllib.parse import unquote

            trust_doc = json.loads(unquote(trust_doc))

        last_used = (role.get("RoleLastUsed") or {}).get("LastUsedDate")
        last_used_iso: str | None
        if last_used is None:
            last_used_iso = None
        elif hasattr(last_used, "isoformat"):
            last_used_iso = last_used.isoformat()
        else:
            last_used_iso = str(last_used)

        return IdentityBinding(
            kind="irsa",
            role_arn_or_principal=role["Arn"],
            trust_policy_summary=_summarize_trust(
                trust_doc,
                self._config.cluster_oidc_issuer,
            ),
            last_used_at=last_used_iso,
        )

    def list_owned_roles(self) -> list[str]:
        """Enumerate IAM role names the platform owns (#995).

        Paginates ``list_roles`` under the configured ``role_path``, keeps
        only ``astrolift-``-prefixed roles (cheap filter), then confirms the
        ``astrolift.io/managed-by=platform`` tag before including each — we
        never treat a role as platform-owned on name alone, so a same-named
        role created out-of-band is not a reap candidate. Raises
        ``map_client_error`` on a list failure so the scan marks itself
        incomplete rather than implying a clean (empty) result.
        """
        owned: list[str] = []
        try:
            paginator = self._iam.get_paginator("list_roles")
            for page in paginator.paginate(PathPrefix=self._config.role_path):
                for role in page.get("Roles", []):
                    name = role.get("RoleName", "")
                    if not name.startswith("astrolift-"):
                        continue
                    if self._role_is_platform_owned(name):
                        owned.append(name)
        except Exception as exc:
            raise map_client_error(exc) from exc
        return owned

    def _role_is_platform_owned(self, role_name: str) -> bool:
        try:
            tags = self._iam.list_role_tags(RoleName=role_name).get("Tags", [])
        except self._iam.exceptions.NoSuchEntityException:
            return False
        except Exception as exc:
            raise map_client_error(exc) from exc
        return any(t.get("Key") == "astrolift.io/managed-by" and t.get("Value") == "platform" for t in tags)

    # ---- internals ------------------------------------------------

    def _role_arn(self, role_name: str) -> str:
        path = self._config.role_path.strip("/")
        path_segment = f"{path}/" if path else ""
        return f"arn:aws:iam::{self._config.account_id}:role/{path_segment}{role_name}"

    def _oidc_trust_policy(self) -> dict[str, Any]:
        """Trust policy template — initial form has no subject claim
        so create_identity_role doesn't need to know which (namespace,
        sa) to bind. bind_service_account adds the subject."""
        oidc_provider_arn = f"arn:aws:iam::{self._config.account_id}:oidc-provider/{self._config.cluster_oidc_issuer}"
        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Federated": oidc_provider_arn},
                    "Action": "sts:AssumeRoleWithWebIdentity",
                    "Condition": {
                        "StringEquals": {
                            f"{self._config.cluster_oidc_issuer}:aud": "sts.amazonaws.com",
                        },
                    },
                }
            ],
        }

    def _ensure_trust_includes(
        self,
        *,
        role_name: str,
        namespace: str,
        sa_name: str,
    ) -> None:
        """Update the trust policy's StringEquals condition to
        include this specific (namespace, sa) subject."""
        try:
            response = self._iam.get_role(RoleName=role_name)
        except self._iam.exceptions.NoSuchEntityException as exc:
            raise NotFoundError(f"role {role_name} not found") from exc
        except Exception as exc:
            raise map_client_error(exc) from exc

        trust_doc = response["Role"]["AssumeRolePolicyDocument"]
        # The doc may arrive as dict (moto) or url-encoded string (real AWS)
        if isinstance(trust_doc, str):
            from urllib.parse import unquote

            trust_doc = json.loads(unquote(trust_doc))

        sub_key = f"{self._config.cluster_oidc_issuer}:sub"
        sub_value = f"system:serviceaccount:{namespace}:{sa_name}"

        # Mutate in place
        for statement in trust_doc.get("Statement", []):
            condition = statement.setdefault(
                "Condition",
                {},
            ).setdefault("StringEquals", {})
            existing = condition.get(sub_key)
            if existing is None:
                condition[sub_key] = sub_value
            elif isinstance(existing, list):
                if sub_value not in existing:
                    existing.append(sub_value)
            elif existing != sub_value:
                # Single value -> upgrade to list
                condition[sub_key] = [existing, sub_value]

        try:
            self._iam.update_assume_role_policy(
                RoleName=role_name,
                PolicyDocument=json.dumps(trust_doc),
            )
        except Exception as exc:
            raise map_client_error(exc) from exc


def discover_oidc_issuer(region: str, cluster_name: str) -> str:
    """Return the EKS cluster's OIDC issuer (host + path, no ``https://``),
    or ``""`` if it can't be read.

    IRSA's trust policy binds to this issuer; Astrolift is BYOC, so rather
    than require it to be populated out-of-band the platform discovers it
    from ``eks:DescribeCluster`` and caches it on the cluster row. The IAM
    ``oidc-provider/<issuer>`` ARN + the ``<issuer>:sub`` trust condition are
    built from this, so an empty value yields a broken trust — callers
    should treat ``""`` as "cannot bind workload identity yet"."""
    import boto3

    eks = boto3.client("eks", region_name=region)
    try:
        cluster = eks.describe_cluster(name=cluster_name)["cluster"]
    except Exception:
        return ""
    issuer = (((cluster.get("identity") or {}).get("oidc") or {}).get("issuer")) or ""
    return issuer.removeprefix("https://")


def _summarize_trust(trust_doc: dict[str, Any], oidc_issuer: str) -> str:
    """Build the operator-facing 1-line trust summary for a binding.

    The trust doc is the IAM ``AssumeRolePolicyDocument`` shape; we
    look at the OIDC ``:sub`` condition under ``StringEquals`` and
    reduce to either ``"OIDC trust: <subject>"`` for a single binding
    or ``"OIDC trust: N service account(s)"`` for many. Anything else
    falls back to ``"trust policy: <N> statement(s)"`` which is still
    enough info for the operator to know there's something to inspect."""
    sub_key = f"{oidc_issuer}:sub"
    statements = trust_doc.get("Statement") or []
    subjects: list[str] = []
    for stmt in statements:
        cond = stmt.get("Condition") or {}
        eq = cond.get("StringEquals") or {}
        value = eq.get(sub_key)
        if isinstance(value, list):
            subjects.extend(str(v) for v in value)
        elif isinstance(value, str):
            subjects.append(value)
    if len(subjects) == 1:
        return f"OIDC trust: {subjects[0]}"
    if len(subjects) > 1:
        return f"OIDC trust: {len(subjects)} service account(s)"
    if statements:
        return f"trust policy: {len(statements)} statement(s)"
    return "trust policy: empty"
