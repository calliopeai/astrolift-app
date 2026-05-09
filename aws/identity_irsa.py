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
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from _sdk.identity import WorkloadIdentityDriver

from aws._errors import NotFoundError, map_client_error


@dataclass(frozen=True)
class IRSAConfig:
    region: str
    account_id: str
    cluster_oidc_issuer: str
    """The EKS cluster's OIDC provider URL (without https://).
    e.g. ``oidc.eks.us-east-1.amazonaws.com/id/ABC123...``.
    Fetched from EKS DescribeCluster + cached on the cluster row."""

    role_path: str = "/astrolift/"
    """IAM path for platform-managed roles. Lets operators filter
    in the AWS console + apply tag-based budgets."""


class IRSADriver(WorkloadIdentityDriver):
    def __init__(self, *, config: IRSAConfig, iam_client: Any | None = None) -> None:
        self._config = config
        if iam_client is not None:
            self._iam = iam_client
        else:
            import boto3

            self._iam = boto3.client("iam", region_name=config.region)

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
                Tags=[{
                    "Key": "astrolift.io/managed-by", "Value": "platform",
                }],
            )
        except self._iam.exceptions.EntityAlreadyExistsException:
            # Idempotent — return the existing ARN
            return self._role_arn(name)
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

        # Attach inline policy from the permissions list.
        if permissions:
            try:
                self._iam.put_role_policy(
                    RoleName=name,
                    PolicyName="astrolift-workload-policy",
                    PolicyDocument=json.dumps({
                        "Version": "2012-10-17",
                        "Statement": permissions,
                    }),
                )
            except Exception as exc:  # noqa: BLE001
                raise map_client_error(exc) from exc

        return response["Role"]["Arn"]

    def attach_policy(self, role: str, policy: str) -> None:
        """Attach a managed policy ARN."""
        try:
            self._iam.attach_role_policy(
                RoleName=role,
                PolicyArn=policy,
            )
        except self._iam.exceptions.NoSuchEntityException as exc:
            raise NotFoundError(f"role {role} or policy {policy} not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

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
                    RoleName=role, PolicyName=policy_name,
                )
            # Managed policies
            attached_managed = self._iam.list_attached_role_policies(
                RoleName=role,
            )
            for entry in attached_managed.get("AttachedPolicies", []):
                self._iam.detach_role_policy(
                    RoleName=role, PolicyArn=entry["PolicyArn"],
                )
            self._iam.delete_role(RoleName=role)
        except self._iam.exceptions.NoSuchEntityException as exc:
            raise NotFoundError(f"role {role} not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

    # ---- internals ------------------------------------------------

    def _role_arn(self, role_name: str) -> str:
        path = self._config.role_path.strip("/")
        path_segment = f"{path}/" if path else ""
        return f"arn:aws:iam::{self._config.account_id}:role/{path_segment}{role_name}"

    def _oidc_trust_policy(self) -> dict[str, Any]:
        """Trust policy template — initial form has no subject claim
        so create_identity_role doesn't need to know which (namespace,
        sa) to bind. bind_service_account adds the subject."""
        oidc_provider_arn = (
            f"arn:aws:iam::{self._config.account_id}:oidc-provider/"
            f"{self._config.cluster_oidc_issuer}"
        )
        return {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Federated": oidc_provider_arn},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        f"{self._config.cluster_oidc_issuer}:aud":
                            "sts.amazonaws.com",
                    },
                },
            }],
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
        except Exception as exc:  # noqa: BLE001
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
                "Condition", {},
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
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc
