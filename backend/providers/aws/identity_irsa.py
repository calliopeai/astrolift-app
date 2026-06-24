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
            # Idempotent — return the existing ARN
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
            f"arn:aws:iam::{self._config.account_id}:oidc-provider/" f"{self._config.cluster_oidc_issuer}"
        )
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
