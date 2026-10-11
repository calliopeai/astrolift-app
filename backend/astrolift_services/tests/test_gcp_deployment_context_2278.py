"""Actual app-placement policy and protected deployment facts at native boundaries."""

from uuid import uuid4

import pytest
from gcp.identity_source import IdentitySourceError

from astrolift_identity.models import Policy
from astrolift_lifecycle.deployment_identity_origin import (
    deployment_app_identity_authority,
    deployment_origin,
)
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import APPROVE, reviewer, start_public
from astrolift_services.gcp_app_identity_plan import endpoint_app_checkpoint, produce_endpoint_app_plan
from astrolift_services.gcp_identity_source import bootstrap_original_identity, original_identity_context
from astrolift_services.models import GCPAppIdentitySource
from astrolift_services.native_identity_authority import current_app_identity_authority
from astrolift_services.tests.test_cluster_model_queries_2213 import grant
from astrolift_services.tests.test_gcp_app_identity_plan_2278 import world as endpoint_world
from astrolift_services.tests.test_gcp_identity_source_2278 import world as original_source_world
from astrolift_services.tests.test_model_connection_2270 import graphql_http, http_token
from astrolift_workflows.client import WorkflowHandle
from astrolift_workflows.native_identity_inputs import DeploymentAuthorityContext
from core.permissions import Permission, PermissionDenied
from providers.tests.gcp.test_identity_source_2278 import UID

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world(monkeypatch, client):
    w = endpoint_world.__wrapped__(monkeypatch, client)
    w.cluster.region = "us-central1-a"
    w.cluster.save()
    return w


def deny(w, environment, region):
    return Policy.objects.create(
        organization=w.org,
        slug="placement-region-deny",
        scope_level="ORG",
        effect="DENY",
        action_pattern="app.update",
        resource_pattern={"env": environment, "region": region},
        conditions=[],
    )


def test_sibling_app_placement_region_deny_refuses_before_native_reads(world):
    AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=world.cluster, name="production"
    )
    deny(world, "production", "us-central1-a")
    with pytest.raises(PermissionDenied):
        produce_endpoint_app_plan(world.authority, world.identity, observer_factory=world.source.observer)
    assert not world.source.calls


def test_vertex_source_region_cannot_substitute_for_app_placement(world):
    deny(world, world.env.name, "us-central1")
    plan = produce_endpoint_app_plan(world.authority, world.identity, observer_factory=world.source.observer)
    assert plan.identity.region == "us-central1"
    assert world.cluster.region == "us-central1-a" and plan.template.permissions


def test_selected_app_placement_region_deny_already_refuses_before_native_reads(world):
    deny(world, world.env.name, "us-central1-a")
    with pytest.raises(PermissionDenied):
        produce_endpoint_app_plan(world.authority, world.identity, observer_factory=world.source.observer)
    assert not world.source.calls


@pytest.fixture
def source_world(monkeypatch, client, tmp_path):
    w = original_source_world.__wrapped__(monkeypatch, client, tmp_path)
    w.medops_app.build_mode = "ci_pushed"
    w.medops_app.save()
    grant(w, Permission.APP_DEPLOY, "APP", w.medops_app.pk)
    grant(w, Permission.APP_APPROVE_DEPLOY, "APP", w.medops_app.pk)
    w.token, w.headers = http_token(w, scopes=("read:apps", "write:apps"))
    w.env.required_approvals = 1
    w.env.save()
    w.starts = []

    def start(name, args, *, workflow_id, **kwargs):
        w.starts.append(args[0])
        return WorkflowHandle(workflow_id, str(uuid4()), True)

    monkeypatch.setattr("astrolift_lifecycle.schema.mutations.helpers.start_workflow", start)
    monkeypatch.setattr("astrolift_workflows.client.signal_workflow", lambda *args, **kwargs: False)
    return w


def dispatch_context(w, client, *, approve=True):
    result = start_public(w, client)
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    reference = deployment_origin(row)
    assert not w.starts and reference.permission == Permission.APP_DEPLOY.value
    if approve:
        _, headers = reviewer(w, "source-context-reviewer-")
        result = graphql_http(client, headers, APPROVE, {"input": {"id": str(row.guid)}})["data"][
            "approveDeployment"
        ]
        assert result["ok"], result["errors"]
        row.refresh_from_db()
        assert row.approval_votes.count() == 1 and len(w.starts) == 1
        assert w.starts[0].identity_authority == reference
    return row, reference, DeploymentAuthorityContext(str(row.guid))


def approval_policy(w, *, environment=None):
    return Policy.objects.create(
        organization=w.org,
        slug="source-context-approval-policy",
        scope_level="APP",
        scope_id=w.medops_app.pk,
        effect="ALLOW",
        action_pattern=Permission.APP_DEPLOY.value,
        resource_pattern={"env": environment} if environment else {},
        conditions=[{"kind": "approval_required", "min_approvers": 1}],
    )


def test_actual_protected_quorum_reaches_bootstrap_and_readback_without_default_vote_facts(
    source_world, client
):
    w = source_world
    _, reference, context = dispatch_context(w, client)
    approval_policy(w)
    with pytest.raises(PermissionDenied):
        bootstrap_original_identity(reference, w.operation, native_factory=w.factory)
    assert not w.native.calls and not GCPAppIdentitySource.objects.exists()
    admitted = bootstrap_original_identity(
        reference, w.operation, native_factory=w.factory, deployment_context=context
    )
    assert admitted.identity.service_account_unique_id == UID and w.native.writes == 1
    assert (
        original_identity_context(reference, native_factory=w.factory, deployment_context=context) == admitted
    )
    w.native.calls.clear()
    with pytest.raises(PermissionDenied):
        original_identity_context(reference, native_factory=w.factory)
    assert not w.native.calls and w.native.writes == 1


def test_pending_protected_context_refuses_before_client_construction(source_world, client):
    w = source_world
    _, reference, context = dispatch_context(w, client, approve=False)
    with pytest.raises(PermissionDenied):
        bootstrap_original_identity(
            reference, w.operation, native_factory=w.factory, deployment_context=context
        )
    assert not w.native.calls and not GCPAppIdentitySource.objects.exists()


@pytest.mark.parametrize("boundary", ["before_create", "after_create", "read_only"])
def test_current_policy_withdrawal_stops_deeper_source_at_native_boundary(source_world, client, boundary):
    w = source_world
    _, reference, context = dispatch_context(w, client)
    policy = approval_policy(w)
    if boundary == "read_only":
        bootstrap_original_identity(
            reference, w.operation, native_factory=w.factory, deployment_context=context
        )
    seen = []

    def after(name):
        seen.append(name)
        selected = (
            name == "GetServiceAccount" and not w.native.writes
            if boundary == "before_create"
            else name == "CreateServiceAccount"
            if boundary == "after_create"
            else name == "GetEndpoint"
        )
        if selected:
            Policy.objects.filter(pk=policy.pk).update(
                conditions=[{"kind": "approval_required", "min_approvers": 2}]
            )

    w.native.after = after
    with pytest.raises(PermissionDenied):
        if boundary == "read_only":
            original_identity_context(reference, native_factory=w.factory, deployment_context=context)
        else:
            bootstrap_original_identity(
                reference, w.operation, native_factory=w.factory, deployment_context=context
            )
    assert (
        seen[-1]
        == {
            "before_create": "GetServiceAccount",
            "after_create": "CreateServiceAccount",
            "read_only": "GetEndpoint",
        }[boundary]
    )
    row = GCPAppIdentitySource.objects.get()
    assert (
        row.state
        == {"before_create": "UNSENT", "after_create": "EVIDENCE", "read_only": "OBSERVED"}[boundary]
    )
    assert row.unique_id == (None if boundary == "before_create" else UID)
    assert w.native.writes == (0 if boundary == "before_create" else 1)


def test_producer_context_retains_exact_deployment_and_rechecks_current_approval(source_world, client):
    w = source_world
    _, reference, context = dispatch_context(w, client)
    policy = approval_policy(w)
    source = w.source
    with pytest.raises(PermissionDenied):
        produce_endpoint_app_plan(reference, w.identity, observer_factory=source.observer)
    assert not source.calls
    plan = produce_endpoint_app_plan(
        reference, w.identity, observer_factory=source.observer, deployment_context=context
    )
    assert plan.deployment_context == context and plan.template.permissions
    Policy.objects.filter(pk=policy.pk).update(conditions=[{"kind": "approval_required", "min_approvers": 2}])
    with pytest.raises(PermissionDenied):
        endpoint_app_checkpoint(plan)()


def test_sibling_environment_never_borrows_selected_deployment_votes(source_world, client):
    w = source_world
    _, reference, context = dispatch_context(w, client)
    AppEnvironment.objects.create(registered_app=w.medops_app, tenant_cluster=w.cluster, name="production")
    approval_policy(w, environment="production")
    with current_app_identity_authority(reference, deployment_context=context):
        pass
    source = w.source
    with pytest.raises(PermissionDenied):
        produce_endpoint_app_plan(
            reference, w.identity, observer_factory=source.observer, deployment_context=context
        )
    assert not source.calls
    with pytest.raises(PermissionDenied):
        bootstrap_original_identity(
            reference, w.operation, native_factory=w.factory, deployment_context=context
        )
    assert w.native.writes == 0 and not GCPAppIdentitySource.objects.exists()


@pytest.mark.parametrize(
    "context", [{"deployment_guid": "x", "approvals": 99}, DeploymentAuthorityContext("x")]
)
def test_untrusted_lookup_context_cannot_supply_approval_facts(world, context):
    with pytest.raises(PermissionDenied):
        produce_endpoint_app_plan(
            world.authority,
            world.identity,
            observer_factory=world.source.observer,
            deployment_context=context,
        )
    assert not world.source.calls
    with pytest.raises(PermissionDenied):
        with deployment_app_identity_authority(world.authority, context):
            pytest.fail("untrusted context admitted")


def pending_approved_source(w, client):
    row, reference, context = dispatch_context(w, client)
    policy = approval_policy(w)

    def withdraw(name):
        if name == "GetServiceAccount" and not w.native.writes:
            Policy.objects.filter(pk=policy.pk).update(
                conditions=[{"kind": "approval_required", "min_approvers": 2}]
            )

    w.native.after = withdraw
    with pytest.raises(PermissionDenied):
        bootstrap_original_identity(
            reference, w.operation, native_factory=w.factory, deployment_context=context
        )
    source = GCPAppIdentitySource.objects.get()
    assert source.state == "UNSENT" and source.unique_id is None and not w.native.writes
    w.native.after = None
    Policy.objects.filter(pk=policy.pk).update(conditions=[{"kind": "approval_required", "min_approvers": 1}])
    return row, reference, context, source


def later_dispatch_context(w, client):
    policy = Policy.objects.get(organization=w.org, slug="source-context-approval-policy")
    conditions = policy.conditions
    Policy.objects.filter(pk=policy.pk).update(conditions=[])
    w.starts.clear()
    try:
        return dispatch_context(w, client)
    finally:
        Policy.objects.filter(pk=policy.pk).update(conditions=conditions)


def test_same_environment_approved_deployment_cannot_take_over_pending_source(source_world, client):
    w = source_world
    first, reference, context, source = pending_approved_source(w, client)
    second, later_reference, later_context = later_dispatch_context(w, client)
    assert first.guid != second.guid and reference == later_reference and context != later_context
    with pytest.raises(IdentitySourceError, match="ORIGINAL_PENDING_OPERATION_REQUIRES_REVIEW"):
        bootstrap_original_identity(
            later_reference, w.operation, native_factory=w.factory, deployment_context=later_context
        )
    source.refresh_from_db()
    assert source.state == "UNSENT" and source.unique_id is None and not w.native.writes


def test_original_pending_deployment_context_can_resume_after_policy_restoration(source_world, client):
    w = source_world
    _, reference, context, source = pending_approved_source(w, client)
    admitted = bootstrap_original_identity(
        reference, w.operation, native_factory=w.factory, deployment_context=context
    )
    source.refresh_from_db()
    assert source.state == "OBSERVED" and admitted.identity.service_account_unique_id == UID
    assert w.native.writes == 1


def test_observed_original_source_can_be_reused_by_fresh_later_deployment(source_world, client):
    w = source_world
    first, reference, context = dispatch_context(w, client)
    approval_policy(w)
    original = bootstrap_original_identity(
        reference, w.operation, native_factory=w.factory, deployment_context=context
    )
    source = GCPAppIdentitySource.objects.get()
    accepted = source.authority_reference.copy()
    second, later_reference, later_context = later_dispatch_context(w, client)
    assert first.guid != second.guid and reference == later_reference
    assert (
        original_identity_context(later_reference, native_factory=w.factory, deployment_context=later_context)
        == original
    )
    assert (
        bootstrap_original_identity(
            later_reference, w.operation, native_factory=w.factory, deployment_context=later_context
        )
        == original
    )
    source.refresh_from_db()
    assert source.authority_reference == accepted and source.state == "OBSERVED" and w.native.writes == 1


def test_legacy_pending_source_cannot_acquire_a_deployment_context(source_world, client):
    w = source_world
    _, reference, context = dispatch_context(w, client)
    policy = approval_policy(w)
    Policy.objects.filter(pk=policy.pk).update(conditions=[])

    def withdraw(name):
        if name == "GetServiceAccount" and not w.native.writes:
            Policy.objects.filter(pk=policy.pk).update(
                conditions=[{"kind": "approval_required", "min_approvers": 1}]
            )

    w.native.after = withdraw
    with pytest.raises(PermissionDenied):
        bootstrap_original_identity(reference, w.operation, native_factory=w.factory)
    source = GCPAppIdentitySource.objects.get()
    assert source.state == "UNSENT" and not w.native.writes
    w.native.after = None
    with pytest.raises(IdentitySourceError, match="ORIGINAL_PENDING_OPERATION_REQUIRES_REVIEW"):
        bootstrap_original_identity(
            reference, w.operation, native_factory=w.factory, deployment_context=context
        )
    source.refresh_from_db()
    assert source.state == "UNSENT" and source.unique_id is None and not w.native.writes
