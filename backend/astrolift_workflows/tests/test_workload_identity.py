"""Unit tests for workload-identity (IRSA) provisioning (#1011).

Covers the two pure pieces that carry the correctness of the feature:
* grant aggregation across managed-service bindings, and
* the deploy-render post-process (annotated ServiceAccount + the
  ``serviceAccountName`` injected onto pod templates).

Both run without a DB or a live IAM client.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from gcp.identity_wi import service_account_email_for

from astrolift_workflows.activities.workload_identity import (
    _permissions_from_bindings,
)
from core.app_deploy import (
    _inject_workload_identity,
    _workload_identity_annotations,
)


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


def test_gcp_permissions_translate_roles_and_deduplicate():
    bindings = [
        _Binding(
            iam_grants=[
                _Grant(
                    resource="projects/acme/topics/events",
                    actions=["roles/pubsub.publisher"],
                ),
                _Grant(
                    resource="projects/acme/subscriptions/jobs",
                    actions=["roles/pubsub.subscriber", "roles/pubsub.publisher"],
                ),
            ],
        ),
    ]
    assert _permissions_from_bindings(bindings, plugin_slug="gcp") == [
        {"role": "roles/pubsub.publisher"},
        {"role": "roles/pubsub.subscriber"},
    ]


def test_gcp_permissions_fail_closed_for_raw_permission():
    bindings = [
        _Binding(
            iam_grants=[
                _Grant(
                    resource="projects/acme/topics/events",
                    actions=["pubsub.topics.publish"],
                ),
            ],
        ),
    ]
    with pytest.raises(ValueError, match="must declare IAM roles"):
        _permissions_from_bindings(bindings, plugin_slug="gcp")


# ---- azure grant translation (#1367) ------------------------------------

_BLOB_SCOPE = (
    "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Storage"
    "/storageAccounts/acct/blobServices/default/containers/data"
)
_QUEUE_SCOPE = (
    "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.ServiceBus/namespaces/ns/queues/jobs"
)


def test_azure_permissions_resolve_roles_at_the_declared_scope():
    bindings = [
        _Binding(
            iam_grants=[_Grant(resource=_BLOB_SCOPE, actions=["Storage Blob Data Contributor"])],
        ),
        _Binding(
            iam_grants=[
                _Grant(
                    resource=_QUEUE_SCOPE,
                    actions=["Azure Service Bus Data Sender", "Azure Service Bus Data Receiver"],
                ),
            ],
        ),
    ]

    assert _permissions_from_bindings(bindings, plugin_slug="azure") == [
        {
            "role_definition_id": "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
            "role_name": "Storage Blob Data Contributor",
            "scope": _BLOB_SCOPE,
        },
        {
            "role_definition_id": "69a216fc-b8fb-44d8-bc22-1f3c2cd27a39",
            "role_name": "Azure Service Bus Data Sender",
            "scope": _QUEUE_SCOPE,
        },
        {
            "role_definition_id": "4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0",
            "role_name": "Azure Service Bus Data Receiver",
            "scope": _QUEUE_SCOPE,
        },
    ]


def test_azure_never_emits_an_aws_policy_statement():
    # The bug this closes: Azure received Effect/Action/Resource dicts that no
    # Azure API can consume, so the binding reported ready with no grant.
    bindings = [
        _Binding(iam_grants=[_Grant(resource=_BLOB_SCOPE, actions=["Storage Blob Data Reader"])]),
    ]
    perms = _permissions_from_bindings(bindings, plugin_slug="azure")
    assert perms and all(set(p) == {"role_definition_id", "role_name", "scope"} for p in perms)


def test_azure_deduplicates_the_same_role_and_scope_across_bindings():
    grant = _Grant(resource=_BLOB_SCOPE, actions=["Storage Blob Data Contributor"])
    bindings = [_Binding(iam_grants=[grant]), _Binding(iam_grants=[grant])]
    assert len(_permissions_from_bindings(bindings, plugin_slug="azure")) == 1


def test_azure_control_plane_grants_produce_no_workload_role():
    # Key Vault material reaches the pod as a projected Secret; a role
    # assignment for it would be over-permission, not least privilege.
    bindings = [
        _Binding(
            iam_grants=[
                _Grant(
                    resource="astrolift-pg-primary-password",
                    actions=["Microsoft.KeyVault/vaults/secrets/getSecret"],
                ),
                _Grant(
                    resource="/subscriptions/sub-1/resourceGroups/rg/providers"
                    "/Microsoft.DBforPostgreSQL/flexibleServers/pg",
                    actions=["Microsoft.DBforPostgreSQL/flexibleServers/read"],
                ),
            ],
        ),
    ]
    assert _permissions_from_bindings(bindings, plugin_slug="azure") == []


def test_azure_unclassified_management_action_fails_closed():
    bindings = [
        _Binding(
            iam_grants=[
                _Grant(resource=_BLOB_SCOPE, actions=["Microsoft.Storage/storageAccounts/write"]),
            ],
        ),
    ]
    with pytest.raises(ValueError, match="granting the workload nothing"):
        _permissions_from_bindings(bindings, plugin_slug="azure")


def test_azure_role_grant_on_a_non_arm_scope_fails_closed():
    bindings = [
        _Binding(
            iam_grants=[
                _Grant(resource="astrolift-blob-secret", actions=["Storage Blob Data Reader"]),
            ],
        ),
    ]
    with pytest.raises(ValueError, match="ARM resource scope"):
        _permissions_from_bindings(bindings, plugin_slug="azure")


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
        role_arn="arn:aws:iam::123456789012:role/astrolift-org-app",
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
        == "arn:aws:iam::123456789012:role/astrolift-org-app"
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
        [cron],
        sa_name="sa",
        role_arn="arn:x",
        namespace="org-app",
    )

    cj = next(r for r in out if r["kind"] == "CronJob")
    assert cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]["serviceAccountName"] == "sa"


def test_inject_accepts_gcp_workload_identity_annotation():
    annotations = {
        "iam.gke.io/gcp-service-account": "runtime@acme.iam.gserviceaccount.com",
    }
    out = _inject_workload_identity(
        [_deployment()],
        sa_name="runtime",
        namespace="org-app",
        annotations=annotations,
    )
    service_account = next(r for r in out if r["kind"] == "ServiceAccount")
    deployment = next(r for r in out if r["kind"] == "Deployment")
    assert service_account["metadata"]["annotations"] == annotations
    assert deployment["spec"]["template"]["spec"]["serviceAccountName"] == "runtime"


def test_gcp_annotations_use_same_canonical_service_account_as_driver():
    role_name = "astrolift-very-long-organization-very-long-application"
    assert _workload_identity_annotations(
        plugin_slug="gcp",
        provider_config={"project_id": "acme-prod"},
        auth_config={},
        role_name=role_name,
    ) == {
        "iam.gke.io/gcp-service-account": service_account_email_for(
            role_name,
            "acme-prod",
        ),
    }


def test_inject_leaves_non_workload_kinds_untouched():
    svc = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "web", "namespace": "org-app"},
        "spec": {"ports": [{"port": 80}]},
    }

    out = _inject_workload_identity(
        [svc],
        sa_name="sa",
        role_arn="arn:x",
        namespace="org-app",
    )

    service = next(r for r in out if r["kind"] == "Service")
    # No serviceAccountName injected into a Service's spec.
    assert "serviceAccountName" not in service["spec"]
    # The Service spec wasn't otherwise mutated.
    assert service["spec"]["ports"] == [{"port": 80}]
