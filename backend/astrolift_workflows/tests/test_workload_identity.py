"""Unit tests for workload-identity (IRSA) provisioning (#1011).

Covers the two pure pieces that carry the correctness of the feature:
* grant aggregation across managed-service bindings, and
* the deploy-render post-process (annotated ServiceAccount + the
  ``serviceAccountName`` injected onto pod templates).

Both run without a DB or a live IAM client.
"""

from __future__ import annotations

from dataclasses import dataclass

from astrolift_workflows.activities.workload_identity import (
    _permissions_from_bindings,
)
from core.app_deploy import _inject_workload_identity


@dataclass
class _Grant:
    resource: str
    actions: list[str]


@dataclass
class _Binding:
    iam_grants: list[_Grant]


def test_permissions_aggregate_across_bindings():
    bindings = [
        _Binding(
            iam_grants=[
                _Grant(resource="arn:aws:s3:::b", actions=["s3:ListBucket"]),
                _Grant(resource="arn:aws:s3:::b/*", actions=["s3:GetObject", "s3:PutObject"]),
            ],
        ),
        _Binding(iam_grants=[_Grant(resource="arn:aws:s3:::c/*", actions=["s3:GetObject"])]),
    ]

    perms = _permissions_from_bindings(bindings)

    assert perms == [
        {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": "arn:aws:s3:::b"},
        {
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:PutObject"],
            "Resource": "arn:aws:s3:::b/*",
        },
        {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "arn:aws:s3:::c/*"},
    ]


def test_permissions_skip_none_and_grantless_bindings():
    # postgres/redis declare no iam_grants -> contribute nothing; None means
    # the driver wasn't resolvable. Neither should produce a statement.
    bindings = [None, _Binding(iam_grants=[]), _Binding(iam_grants=None)]  # type: ignore[arg-type]
    assert _permissions_from_bindings(bindings) == []


def test_permissions_drop_empty_actions_or_resource():
    bindings = [
        _Binding(iam_grants=[_Grant(resource="arn:aws:s3:::b", actions=[])]),
        _Binding(iam_grants=[_Grant(resource="", actions=["s3:GetObject"])]),
    ]
    assert _permissions_from_bindings(bindings) == []


# ---- render post-process ------------------------------------------------


def _deployment(name="web"):
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": "org-app"},
        "spec": {"template": {"spec": {"containers": [{"name": "app"}]}}},
    }


def test_inject_sets_service_account_on_deployment_and_emits_sa():
    resources = [_deployment()]

    out = _inject_workload_identity(
        resources,
        sa_name="astrolift-org-app",
        role_arn="arn:aws:iam::123456789012:role/astrolift/astrolift-org-app",
        namespace="org-app",
    )

    deploys = [r for r in out if r["kind"] == "Deployment"]
    sas = [r for r in out if r["kind"] == "ServiceAccount"]
    assert len(sas) == 1
    sa = sas[0]
    assert sa["metadata"]["name"] == "astrolift-org-app"
    assert sa["metadata"]["namespace"] == "org-app"
    assert (
        sa["metadata"]["annotations"]["eks.amazonaws.com/role-arn"]
        == "arn:aws:iam::123456789012:role/astrolift/astrolift-org-app"
    )
    assert deploys[0]["spec"]["template"]["spec"]["serviceAccountName"] == "astrolift-org-app"


def test_inject_handles_cronjob_nested_pod_spec():
    cron = {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {"name": "tick", "namespace": "org-app"},
        "spec": {"jobTemplate": {"spec": {"template": {"spec": {"containers": []}}}}},
    }

    out = _inject_workload_identity(
        [cron], sa_name="sa", role_arn="arn:x", namespace="org-app",
    )

    cj = next(r for r in out if r["kind"] == "CronJob")
    assert cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]["serviceAccountName"] == "sa"


def test_inject_leaves_non_workload_kinds_untouched():
    svc = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "web", "namespace": "org-app"},
        "spec": {"ports": [{"port": 80}]},
    }

    out = _inject_workload_identity(
        [svc], sa_name="sa", role_arn="arn:x", namespace="org-app",
    )

    service = next(r for r in out if r["kind"] == "Service")
    # No serviceAccountName injected into a Service's spec.
    assert "serviceAccountName" not in service["spec"]
    # The Service spec wasn't otherwise mutated.
    assert service["spec"]["ports"] == [{"port": 80}]
