"""Tests for workload identity policy (#8, spec 12 §5.1, §5.3, §5.4)."""

from __future__ import annotations

import pytest

from astrolift_identity.workload_identity import (
    BoundServiceAccount,
    CloudKind,
    DelegationStep,
    PolicyResourceKind,
    PolicyStatement,
    ServiceBinding,
    WorkloadIdentityError,
    annotations_changed,
    aws_irsa_annotations,
    azure_workload_identity_annotations,
    derive_policies,
    gcp_workload_identity_annotations,
    serviceaccount_name_for_app,
    validate_delegation_chain,
)

# ---- policy statement validation -----------------------------------


def test_policy_statement_default():
    s = PolicyStatement(
        kind=PolicyResourceKind.OBJECT_STORE,
        resource_id="s3:my-bucket",
        actions=("read", "write"),
    )
    assert s.actions == ("read", "write")


def test_policy_statement_rejects_empty_actions():
    with pytest.raises(WorkloadIdentityError, match="actions"):
        PolicyStatement(
            kind=PolicyResourceKind.OBJECT_STORE,
            resource_id="s3:b", actions=(),
        )


def test_policy_statement_rejects_empty_resource_id():
    with pytest.raises(WorkloadIdentityError, match="resource_id"):
        PolicyStatement(
            kind=PolicyResourceKind.OBJECT_STORE,
            resource_id="", actions=("read",),
        )


def test_policy_statement_rejects_unsupported_action():
    """Action vocabularies are bounded — 'admin' isn't in the
    OBJECT_STORE set; refuse before driver translation."""
    with pytest.raises(WorkloadIdentityError, match="unsupported actions"):
        PolicyStatement(
            kind=PolicyResourceKind.OBJECT_STORE,
            resource_id="s3:b", actions=("read", "admin"),
        )


def test_policy_statement_database_only_connect():
    """DATABASE only allows 'connect' — IAM-auth pattern."""
    with pytest.raises(WorkloadIdentityError, match="unsupported"):
        PolicyStatement(
            kind=PolicyResourceKind.DATABASE,
            resource_id="rds:mydb", actions=("read",),
        )


def test_policy_statement_pubsub_only_publish():
    s = PolicyStatement(
        kind=PolicyResourceKind.PUBSUB_TOPIC,
        resource_id="sns:topic", actions=("publish",),
    )
    assert s.actions == ("publish",)
    with pytest.raises(WorkloadIdentityError, match="unsupported"):
        PolicyStatement(
            kind=PolicyResourceKind.PUBSUB_TOPIC,
            resource_id="sns:topic", actions=("subscribe",),
        )


# ---- policy derivation ---------------------------------------------


def test_derive_policies_default_actions():
    """Spec §5.1: roll bound services into policy statements."""
    bindings = (
        ServiceBinding(service_kind="object_store", resource_id="s3:bucket"),
        ServiceBinding(service_kind="queue", resource_id="sqs:work"),
    )
    policies = derive_policies(bindings=bindings)
    assert len(policies) == 2
    assert policies[0].kind == PolicyResourceKind.OBJECT_STORE
    assert "read" in policies[0].actions
    assert "write" in policies[0].actions
    assert "list" in policies[0].actions
    assert policies[1].kind == PolicyResourceKind.QUEUE


def test_derive_policies_custom_actions_override_default():
    """Manifest can narrow — read-only access to a bucket."""
    bindings = (
        ServiceBinding(
            service_kind="object_store",
            resource_id="s3:bucket",
            custom_actions=("read",),
        ),
    )
    policies = derive_policies(bindings=bindings)
    assert policies[0].actions == ("read",)


def test_derive_policies_unknown_service_kind():
    with pytest.raises(WorkloadIdentityError, match="unknown service kind"):
        derive_policies(bindings=(
            ServiceBinding(service_kind="quantum", resource_id="x"),
        ))


def test_derive_policies_secret_default_read_only():
    """Secrets default to read-only; write is for explicit
    rotation flows only."""
    bindings = (
        ServiceBinding(service_kind="secret", resource_id="sm:db-creds"),
    )
    policies = derive_policies(bindings=bindings)
    assert policies[0].actions == ("read",)


# ---- ServiceAccount conventions ------------------------------------


def test_sa_name_equals_app_slug():
    """Spec §5.3: SA name == app slug, single source of truth."""
    assert serviceaccount_name_for_app(app_slug="my-api") == "my-api"


def test_sa_name_rejects_uppercase():
    """RFC 1123 + k8s SA name: must be lowercase."""
    with pytest.raises(WorkloadIdentityError, match="RFC 1123"):
        serviceaccount_name_for_app(app_slug="My-API")


def test_sa_name_rejects_starting_dash():
    with pytest.raises(WorkloadIdentityError, match="RFC 1123"):
        serviceaccount_name_for_app(app_slug="-api")


def test_sa_name_rejects_underscore():
    with pytest.raises(WorkloadIdentityError, match="RFC 1123"):
        serviceaccount_name_for_app(app_slug="my_api")


def test_sa_name_rejects_empty():
    with pytest.raises(WorkloadIdentityError):
        serviceaccount_name_for_app(app_slug="")


# ---- AWS IRSA annotations ------------------------------------------


def test_aws_irsa_annotation_shape():
    out = aws_irsa_annotations(
        role_arn="arn:aws:iam::123456789012:role/my-role",
    )
    assert out == {
        "eks.amazonaws.com/role-arn":
            "arn:aws:iam::123456789012:role/my-role",
    }


def test_aws_irsa_rejects_non_role_arn():
    """ARN must be an IAM role; refuse user/policy ARNs that
    won't IRSA correctly."""
    with pytest.raises(WorkloadIdentityError):
        aws_irsa_annotations(
            role_arn="arn:aws:iam::123:user/me",
        )


def test_aws_irsa_rejects_bad_arn():
    with pytest.raises(WorkloadIdentityError):
        aws_irsa_annotations(role_arn="not-an-arn")


# ---- GCP Workload Identity annotations -----------------------------


def test_gcp_wi_annotation_shape():
    out = gcp_workload_identity_annotations(
        gcp_sa_email="my-sa@my-proj.iam.gserviceaccount.com",
    )
    assert out == {
        "iam.gke.io/gcp-service-account":
            "my-sa@my-proj.iam.gserviceaccount.com",
    }


def test_gcp_wi_rejects_bad_email():
    with pytest.raises(WorkloadIdentityError):
        gcp_workload_identity_annotations(gcp_sa_email="not-an-email")


# ---- Azure Workload Identity annotations ---------------------------


def test_azure_wi_annotation_basic():
    out = azure_workload_identity_annotations(
        client_id="01234567-89ab-cdef-0123-456789abcdef",
    )
    assert "azure.workload.identity/client-id" in out
    assert "tenant-id" not in {k.split("/")[-1] for k in out}


def test_azure_wi_with_tenant():
    out = azure_workload_identity_annotations(
        client_id="01234567-89ab-cdef-0123-456789abcdef",
        tenant_id="76543210-abcd-ef01-2345-6789abcdef01",
    )
    assert (
        out["azure.workload.identity/tenant-id"]
        == "76543210-abcd-ef01-2345-6789abcdef01"
    )


def test_azure_wi_rejects_bad_client_id():
    with pytest.raises(WorkloadIdentityError, match="UUID"):
        azure_workload_identity_annotations(client_id="not-a-uuid")


def test_azure_wi_rejects_bad_tenant_id():
    with pytest.raises(WorkloadIdentityError, match="UUID"):
        azure_workload_identity_annotations(
            client_id="01234567-89ab-cdef-0123-456789abcdef",
            tenant_id="oops",
        )


# ---- annotation rotation -------------------------------------------


def test_annotations_changed_value_drift():
    """Spec §5.3: re-apply when role ARN changes."""
    assert annotations_changed(
        old={"eks.amazonaws.com/role-arn": "arn:aws:iam::1:role/old"},
        new={"eks.amazonaws.com/role-arn": "arn:aws:iam::1:role/new"},
    ) is True


def test_annotations_changed_key_added():
    """Adding tenant_id to an Azure SA → must re-apply."""
    assert annotations_changed(
        old={"azure.workload.identity/client-id": "abc"},
        new={
            "azure.workload.identity/client-id": "abc",
            "azure.workload.identity/tenant-id": "def",
        },
    ) is True


def test_annotations_unchanged_returns_false():
    """No drift → no re-apply, idempotent reconcile."""
    same = {"eks.amazonaws.com/role-arn": "arn:aws:iam::1:role/x"}
    assert annotations_changed(old=same, new=same) is False


# ---- delegation chain validation -----------------------------------


def test_delegation_chain_single_hop():
    validate_delegation_chain(chain=(
        DelegationStep(cloud=CloudKind.AWS, role_id="arn:aws:iam::1:role/x"),
    ))


def test_delegation_chain_two_hop():
    validate_delegation_chain(chain=(
        DelegationStep(cloud=CloudKind.AWS, role_id="arn:aws:iam::1:role/x"),
        DelegationStep(cloud=CloudKind.AWS, role_id="arn:aws:iam::2:role/y"),
    ))


def test_delegation_chain_max_three_hops():
    with pytest.raises(WorkloadIdentityError, match="max 3"):
        validate_delegation_chain(chain=tuple(
            DelegationStep(cloud=CloudKind.AWS, role_id=f"arn:aws:iam::{i}:role/x")
            for i in (1, 2, 3, 4)
        ))


def test_delegation_chain_rejects_empty():
    """Spec: omit the chain entirely rather than passing []."""
    with pytest.raises(WorkloadIdentityError, match="empty"):
        validate_delegation_chain(chain=())


def test_delegation_chain_rejects_mixed_clouds():
    """Workload identity is per-cloud; cross-cloud is workforce
    federation, not workload."""
    with pytest.raises(WorkloadIdentityError, match="mixes clouds"):
        validate_delegation_chain(chain=(
            DelegationStep(cloud=CloudKind.AWS, role_id="arn:aws:iam::1:role/x"),
            DelegationStep(cloud=CloudKind.GCP, role_id="sa@p.iam.gserviceaccount.com"),
        ))


def test_delegation_chain_rejects_empty_role_id():
    with pytest.raises(WorkloadIdentityError, match="missing role_id"):
        validate_delegation_chain(chain=(
            DelegationStep(cloud=CloudKind.AWS, role_id="arn:aws:iam::1:role/x"),
            DelegationStep(cloud=CloudKind.AWS, role_id=""),
        ))


# ---- BoundServiceAccount -------------------------------------------


def test_bound_sa_happy_path():
    bsa = BoundServiceAccount(cluster_id=1, namespace="acme", sa_name="api")
    assert bsa.namespace == "acme"


def test_bound_sa_requires_namespace():
    with pytest.raises(WorkloadIdentityError, match="namespace"):
        BoundServiceAccount(cluster_id=1, namespace="", sa_name="api")


def test_bound_sa_requires_sa_name():
    with pytest.raises(WorkloadIdentityError, match="sa_name"):
        BoundServiceAccount(cluster_id=1, namespace="acme", sa_name="")


def test_bound_sa_validates_sa_name_rfc1123():
    with pytest.raises(WorkloadIdentityError, match="sa_name"):
        BoundServiceAccount(cluster_id=1, namespace="acme", sa_name="My_SA")


def test_bound_sa_validates_namespace_rfc1123():
    with pytest.raises(WorkloadIdentityError, match="namespace"):
        BoundServiceAccount(cluster_id=1, namespace="ACME", sa_name="api")
