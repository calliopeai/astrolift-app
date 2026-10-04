"""Real scoped grants, durable distinct quorum and no connection effects before finalize."""

from contextlib import contextmanager
from dataclasses import asdict
from uuid import uuid4

import pytest

from astrolift_graphql import GUID
from astrolift_identity import abac
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.models import ApiToken, Member, Policy
from astrolift_services.models import (
    ManagedServiceAttachment,
    ModelConnectionApproval,
    ModelConnectionPolicy,
    ModelConnectionRequest,
)
from astrolift_services.schema.cluster_model_mutations import ClusterModelMutations
from astrolift_services.schema.model_connections import (
    DecideModelConnectionRequestInput,
    ModelConnectionsMutation,
    RequestModelConnectionInput,
)
from astrolift_services.tests.test_cluster_model_foundation_2213 import subject
from astrolift_services.tests.test_cluster_model_mutations_2213 import allowed, subscription
from astrolift_services.tests.test_cluster_model_mutations_2213 import queue as queue_fixture
from astrolift_services.tests.test_cluster_model_mutations_2213 import world as foundation_world
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    ScopeKind,
    check_permission,
    require_permission,
)
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import bind_role, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    w = foundation_world.__wrapped__(monkeypatch)
    allowed(w)
    return w


@pytest.fixture
def queue(monkeypatch):
    return queue_fixture.__wrapped__(monkeypatch)


def settings(w, mode="REQUIRE_APPROVAL", quorum=1, self_approval=False):
    return ModelConnectionPolicy.objects.create(
        organization=w.org, mode=mode, required_approvals=quorum, allow_self_approval=self_approval
    )


def proposal(w, **changes):
    from astrolift_services.model_connection_policy import effective_policy

    value = asdict(subscription(w)) | {
        "if_match_app_version": w.medops_app.version,
        "policy_version": effective_policy(w.model).version,
        "idempotency_key": GUID(str(uuid4())),
    }
    return RequestModelConnectionInput(**(value | changes))


def decision(row):
    return DecideModelConnectionRequestInput(id=GUID(str(row.guid)), if_match_version=row.version)


@contextmanager
def as_actor(w, actor, *, scopes=("read:apps", "write:apps", "admin")):
    token = ApiToken.objects.create(
        user=actor, organization=w.org, name="test", token_hash=uuid4().hex, scopes=list(scopes)
    )
    marker = set_current_api_token(token)
    try:
        with tenant_context(TenantContext(organization_id=w.org.pk, actor_user_id=actor.pk)):
            yield make_info(actor)
    finally:
        reset_current_api_token(marker)


def reviewer(w, suffix):
    user = make_user("connection-" + suffix)
    Member.objects.create(user=user, scope_kind="ORG", scope_id=w.org.pk)
    bind_role(
        user, permissions=[Permission.ORG_UPDATE], kind="ORG", scope_id=w.org.pk, slug="review-org-" + suffix
    )
    bind_role(
        user,
        permissions=[Permission.APP_APPROVE_DEPLOY],
        kind="APP",
        scope_id=w.medops_app.pk,
        slug="review-app-" + suffix,
    )
    return user


def create_request(w):
    with subject(w, scopes=["read:apps", "write:apps"]):
        result = ModelConnectionsMutation().request_model_connection(make_info(w.user), input=proposal(w))
    assert result.ok, result.errors
    return ModelConnectionRequest.objects.get(guid=str(result.data.id))


def vote(w, row, user):
    with as_actor(w, user) as info:
        result = ModelConnectionsMutation().approve_model_connection_request(info, input=decision(row))
    assert result.ok, result.errors
    row.refresh_from_db()
    return result


def test_request_has_no_subscription_or_queue_and_idempotency(world, queue):
    settings(world)
    with subject(world, scopes=["write:apps"]):
        input = proposal(world)
        first = ModelConnectionsMutation().request_model_connection(make_info(world.user), input=input)
        second = ModelConnectionsMutation().request_model_connection(make_info(world.user), input=input)
    assert first.ok and second.ok, (first.errors, second.errors)
    assert first.data.id == second.data.id and first.data.status.value == "pending"
    assert (
        ModelConnectionRequest.objects.count() == 1
        and not ManagedServiceAttachment.objects.exists()
        and queue == []
    )
    world.model.refresh_from_db()
    assert world.model.subscription_revision == 0 and world.model.status == "active"


def test_two_distinct_votes_then_current_owner_finalize_exactly_once(world, queue):
    settings(world, quorum=2)
    row = create_request(world)
    user1, user2 = reviewer(world, "one"), reviewer(world, "two")
    first = vote(world, row, user1)
    assert row.status == "pending" and first.data.approval_count == 1
    repeat = vote(world, row, user1)
    assert repeat.data.approval_count == 1 and ModelConnectionApproval.objects.count() == 1
    vote(world, row, user2)
    assert row.status == "approved" and queue == [] and not ManagedServiceAttachment.objects.exists()
    with subject(world, scopes=["read:apps", "write:apps"]):
        result = ModelConnectionsMutation().finalize_model_connection_request(
            make_info(world.user), input=decision(row)
        )
        replay = ModelConnectionsMutation().finalize_model_connection_request(
            make_info(world.user), input=decision(row)
        )
    assert result.ok and replay.ok, (result.errors, replay.errors)
    assert result.data.subscription_id == replay.data.subscription_id
    assert ManagedServiceAttachment.objects.count() == 1 and len(queue) == 1
    row.refresh_from_db()
    assert row.finalized_at is not None


@pytest.mark.parametrize("mode", ["REQUIRE_APPROVAL", "DENY"])
def test_legacy_direct_subscribe_cannot_bypass_org_policy(world, queue, mode):
    settings(world, mode)
    with subject(world):
        result = ClusterModelMutations().subscribe_cluster_model(
            make_info(world.user), input=subscription(world)
        )
    assert not result.ok and not ManagedServiceAttachment.objects.exists() and queue == []


def test_auto_legacy_still_works(world, queue):
    settings(world, "AUTO")
    with subject(world):
        result = ClusterModelMutations().subscribe_cluster_model(
            make_info(world.user), input=subscription(world)
        )
    assert result.ok and len(queue) == 1


def test_abac_approval_required_can_request_but_cannot_create_effect(world, queue):
    settings(world, "AUTO")
    Policy.objects.create(
        organization=world.org,
        name="quorum",
        slug="quorum",
        scope_level="ORG",
        effect="ALLOW",
        action_pattern="app.update",
        conditions=[{"kind": "approval_required", "min_approvers": 2}],
    )
    row = create_request(world)
    assert row.required_approvals == 2 and row.status == "pending"
    with subject(world, scopes=["read:apps", "write:apps"]):
        denied = ModelConnectionsMutation().finalize_model_connection_request(
            make_info(world.user), input=decision(row)
        )
    assert not denied.ok and not ManagedServiceAttachment.objects.exists() and queue == []
    vote(world, row, reviewer(world, "abac-one"))
    vote(world, row, reviewer(world, "abac-two"))
    with subject(world, scopes=["read:apps", "write:apps"]):
        result = ModelConnectionsMutation().finalize_model_connection_request(
            make_info(world.user), input=decision(row)
        )
    assert result.ok, result.errors
    assert len(queue) == 1


@pytest.mark.parametrize(
    "effect,conditions",
    [
        ("DENY", []),
        ("DENY", [{"kind": "approval_required", "min_approvers": 1}]),
        ("ALLOW", [{"kind": "approval_required", "min_approvers": 0}]),
        ("ALLOW", [{"kind": "approval_required", "min_approvers": 2}, {"kind": "unknown"}]),
        (
            "ALLOW",
            [
                {"kind": "approval_required", "min_approvers": 2},
                {"kind": "env_match", "env_in": ["different"]},
            ],
        ),
        (
            "ALLOW",
            [
                {"kind": "approval_required", "min_approvers": 2},
                {"kind": "freshness", "max_session_age_minutes": 1},
            ],
        ),
    ],
)
def test_request_admission_defers_no_other_deny_or_unknown(world, queue, effect, conditions):
    settings(world)
    Policy.objects.create(
        organization=world.org,
        name="deny",
        slug="deny",
        scope_level="ORG",
        effect=effect,
        action_pattern="app.update",
        conditions=conditions,
    )
    with subject(world, scopes=["write:apps"]):
        result = ModelConnectionsMutation().request_model_connection(
            make_info(world.user), input=proposal(world)
        )
    assert not result.ok and not ModelConnectionRequest.objects.exists() and queue == []


def test_deferral_is_removed_before_resolver_effect_and_cleans_context(world):
    Policy.objects.create(
        organization=world.org,
        name="need",
        slug="need",
        scope_level="ORG",
        effect="ALLOW",
        action_pattern="app.update",
        conditions=[{"kind": "approval_required", "min_approvers": 1}],
    )

    def scope(args):
        return PermissionScope(ScopeKind.APP, world.medops_app.pk)

    @require_permission(Permission.APP_UPDATE, scope=scope, approval_request=True)
    def pending_only():
        assert abac.current_attributes().approval_request is False
        check_permission(Permission.APP_UPDATE, scope=scope({}))

    with subject(world), abac.operation_attributes(approvals=0):
        before = abac.current_attributes()
        with pytest.raises(PermissionDenied):
            pending_only()
        assert abac.current_attributes() is before and not before.approval_request


@pytest.mark.parametrize("withdrawal", ["policy", "app", "environment", "model", "cluster", "provider"])
def test_changed_review_targets_or_policy_never_finalize(world, queue, withdrawal):
    policy = settings(world)
    row = create_request(world)
    vote(world, row, reviewer(world, "withdraw"))
    target = {
        "policy": policy,
        "app": world.medops_app,
        "environment": world.env,
        "model": world.model,
        "cluster": world.cluster,
        "provider": world.cluster.provider_plugin,
    }[withdrawal]
    target.save()
    with subject(world, scopes=["write:apps"]):
        result = ModelConnectionsMutation().finalize_model_connection_request(
            make_info(world.user), input=decision(row)
        )
    assert result.ok and result.data.status.value == "stale", result.errors
    assert not ManagedServiceAttachment.objects.exists() and queue == []


@pytest.mark.parametrize("withdrawal", ["membership", "actor", "token", "scope", "role"])
def test_requester_authority_withdrawal_after_gate_never_finalizes(world, queue, monkeypatch, withdrawal):
    from astrolift_services.model_connection_requests import locked_targets as original

    settings(world)
    row = create_request(world)
    vote(world, row, reviewer(world, "actor-withdraw"))

    def withdraw(info, input, **kwargs):
        if withdrawal == "membership":
            Member.objects.filter(user=world.user, scope_kind="ORG").update(is_active=False)
        elif withdrawal == "actor":
            type(world.user).objects.filter(pk=world.user.pk).update(is_active=False)
        elif withdrawal == "token":
            ApiToken.objects.filter(pk=get_current_api_token().pk).update(is_revoked=True)
        elif withdrawal == "scope":
            ApiToken.objects.filter(pk=get_current_api_token().pk).update(scopes=["read:apps"])
        else:
            from astrolift_identity.models import RoleBinding

            RoleBinding.objects.filter(user=world.user, scope_kind="APP").update(
                deleted_at=__import__("django.utils.timezone", fromlist=["now"]).now()
            )
        return original(info, input, **kwargs)

    monkeypatch.setattr("astrolift_services.model_connection_requests.locked_targets", withdraw)
    with subject(world, scopes=["write:apps"]):
        result = ModelConnectionsMutation().finalize_model_connection_request(
            make_info(world.user), input=decision(row)
        )
    assert not result.ok and not ManagedServiceAttachment.objects.exists() and queue == []


@pytest.mark.parametrize("withdrawal", ["role", "membership", "actor", "token", "scope"])
def test_voter_withdrawal_invalidates_approved_quorum(world, queue, withdrawal):
    settings(world)
    row = create_request(world)
    user = reviewer(world, "voter-withdraw")
    vote(world, row, user)
    record = ModelConnectionApproval.objects.get(request=row)
    from astrolift_identity.models import RoleBinding

    if withdrawal == "role":
        RoleBinding.objects.filter(user=user).update(
            deleted_at=__import__("django.utils.timezone", fromlist=["now"]).now()
        )
    elif withdrawal == "membership":
        Member.objects.filter(user=user).update(is_active=False)
    elif withdrawal == "actor":
        type(user).objects.filter(pk=user.pk).update(is_active=False)
    elif withdrawal == "token":
        ApiToken.objects.filter(pk=record.api_token_id).update(is_revoked=True)
    else:
        ApiToken.objects.filter(pk=record.api_token_id).update(scopes=["read:apps"])
    with subject(world, scopes=["write:apps"]):
        result = ModelConnectionsMutation().finalize_model_connection_request(
            make_info(world.user), input=decision(row)
        )
    assert result.ok and result.data.status.value == "stale", result.errors
    assert not ManagedServiceAttachment.objects.exists() and queue == []


@pytest.mark.parametrize("self_approval", [False, True])
def test_self_approval_is_explicit_policy_not_role_shortcut(world, queue, self_approval):
    settings(world, self_approval=self_approval)
    bind_role(
        world.user,
        permissions=[Permission.ORG_UPDATE, Permission.APP_APPROVE_DEPLOY],
        kind="ORG",
        scope_id=world.org.pk,
        slug="self-review",
    )
    row = create_request(world)
    with subject(world, scopes=["admin"]):
        result = ModelConnectionsMutation().approve_model_connection_request(
            make_info(world.user), input=decision(row)
        )
    assert result.ok is self_approval
    assert (
        ModelConnectionApproval.objects.count() == int(self_approval)
        and not ManagedServiceAttachment.objects.exists()
        and queue == []
    )


def test_dedicated_other_app_cannot_request(world, queue):
    settings(world)
    world.model.config.update(sharing_mode="dedicated", dedicated_app_id=str(world.platform_app.guid))
    world.model.save()
    with subject(world, scopes=["write:apps"]):
        result = ModelConnectionsMutation().request_model_connection(
            make_info(world.user), input=proposal(world)
        )
    assert not result.ok and not ModelConnectionRequest.objects.exists() and queue == []


def test_model_restriction_cannot_loosen_org_and_unknown_default_denies(world, queue, monkeypatch):
    from astrolift_services.model_connection_policy import effective_policy

    settings(world, "DENY", quorum=3)
    ModelConnectionPolicy.objects.create(
        organization=world.org,
        model_deployment=world.model,
        mode="AUTO",
        required_approvals=1,
        allow_self_approval=True,
    )
    policy = effective_policy(world.model)
    assert policy.mode == "DENY" and policy.required_approvals == 3 and not policy.allow_self_approval
    from constance import config

    original = config.MODEL_CONNECTION_DEFAULT_MODE
    config.MODEL_CONNECTION_DEFAULT_MODE = "unknown"
    try:
        with pytest.raises(PermissionDenied):
            effective_policy(world.model)
    finally:
        config.MODEL_CONNECTION_DEFAULT_MODE = original
    assert queue == []


def test_request_http_uses_actual_schema_and_write_only_app_token(world, queue, client):
    import json

    from astrolift_identity.api_tokens import mint_token

    settings(world)
    minted = mint_token()
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="HTTP",
        token_hash=minted.token_hash,
        scopes=["write:apps"],
    )
    with subject(world):
        input = proposal(world)
    wire = {
        "organizationId": str(input.organization_id),
        "modelDeploymentId": str(input.model_deployment_id),
        "expectedClusterId": str(input.expected_cluster_id),
        "expectedProviderId": str(input.expected_provider_id),
        "appEnvironmentId": str(input.app_environment_id),
        "alias": input.alias,
        "ifMatchVersion": input.if_match_version,
        "ifMatchEnvironmentVersion": input.if_match_environment_version,
        "ifMatchAppVersion": input.if_match_app_version,
        "policyVersion": input.policy_version,
        "idempotencyKey": str(input.idempotency_key),
    }
    query = "mutation($input:RequestModelConnectionInput!){requestModelConnection(input:$input){ok errors{code message currentVersion} data{id version status organizationId modelDeploymentId appId appEnvironmentId clusterId providerId alias policyVersion requiredApprovals approvalCount canApprove canCancel canFinalize subscriptionId modelName appName environmentName requesterUsername}}}"
    headers = {
        "HTTP_AUTHORIZATION": "Bearer " + minted.plaintext,
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid),
    }
    reply = client.post(
        "/app/gql/config/",
        json.dumps({"query": query, "variables": {"input": wire}}),
        content_type="application/json",
        **headers,
    )
    assert reply.status_code == 200 and not reply.json().get("errors"), reply.json()
    data = reply.json()["data"]["requestModelConnection"]
    assert data["ok"] and data["data"]["status"] == "PENDING" and data["data"]["subscriptionId"] is None
    assert (
        minted.plaintext not in reply.content.decode() and "credential" not in reply.content.decode().lower()
    )
    assert queue == [] and not ManagedServiceAttachment.objects.exists()


@pytest.mark.parametrize("scopes,team", [(["read:apps"], None), (["write:apps"], "foreign")])
def test_request_scope_or_team_ceiling_never_bypassed(world, queue, scopes, team):
    settings(world)
    with subject(world, scopes=scopes, token_team=world.platform.pk if team else None):
        result = ModelConnectionsMutation().request_model_connection(
            make_info(world.user), input=proposal(world)
        )
    assert not result.ok and not ModelConnectionRequest.objects.exists() and queue == []


def test_own_team_scoped_app_writer_can_request(world, queue):
    settings(world)
    with subject(world, scopes=["write:apps"], token_team=world.medops.pk):
        result = ModelConnectionsMutation().request_model_connection(
            make_info(world.user), input=proposal(world)
        )
    assert result.ok, result.errors
    assert queue == [] and not ManagedServiceAttachment.objects.exists()


def http_token(w, *, scopes=("write:apps",), team=None, actor=None):
    from astrolift_identity.api_tokens import mint_token

    minted = mint_token()
    token = ApiToken.objects.create(
        user=actor or w.user,
        organization=w.org,
        team=team,
        name="actual HTTP",
        token_hash=minted.token_hash,
        scopes=list(scopes),
    )
    return token, {
        "HTTP_AUTHORIZATION": "Bearer " + minted.plaintext,
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(w.org.guid),
    }


def graphql_http(client, headers, query, variables):
    import json

    reply = client.post(
        "/app/gql/config/",
        json.dumps({"query": query, "variables": variables}),
        content_type="application/json",
        **headers,
    )
    assert reply.status_code == 200, reply.status_code
    return reply.json()


def placement_wire(w):
    return {
        "organizationId": str(w.org.guid),
        "modelDeploymentId": str(w.model.guid),
        "expectedClusterId": str(w.cluster.guid),
        "expectedProviderId": str(w.cluster.provider_plugin.guid),
    }


TARGET_PAGE = """query($input:ModelConnectionPlacementInput!){modelConnectionTargetsPage(input:$input){totalCount items{environmentId environmentVersion appId appVersion eligible action reason policyVersion requiredApprovals}}}"""


def test_actual_http_page_action_request_abac_roundtrip_without_app_read(world, queue, client):
    settings(world, "AUTO")
    Policy.objects.create(
        organization=world.org,
        name="request quorum",
        slug="request-quorum",
        scope_level="ORG",
        effect="ALLOW",
        action_pattern="app.update",
        conditions=[{"kind": "approval_required", "min_approvers": 2}],
    )
    _, headers = http_token(world, team=world.medops)
    targets = graphql_http(client, headers, TARGET_PAGE, {"input": placement_wire(world)})
    assert not targets.get("errors"), targets
    rows = targets["data"]["modelConnectionTargetsPage"]["items"]
    target = next(row for row in rows if row["environmentId"] == str(world.env.guid))
    assert target["eligible"] and target["action"] == "REQUEST" and target["requiredApprovals"] == 2
    assert target["appVersion"] == world.medops_app.version
    action_input = placement_wire(world) | {"appEnvironmentId": target["environmentId"]}
    action = graphql_http(
        client,
        headers,
        "query($input:ModelConnectionTargetInput!){modelConnectionAction(input:$input){action policyVersion requiredApprovals}}",
        {"input": action_input},
    )
    assert not action.get("errors"), action
    assert action["data"]["modelConnectionAction"]["action"] == "REQUEST"
    assert action["data"]["modelConnectionAction"]["policyVersion"] == target["policyVersion"]
    request_input = action_input | {
        "alias": "reviewed",
        "ifMatchVersion": world.model.version,
        "ifMatchEnvironmentVersion": target["environmentVersion"],
        "ifMatchAppVersion": target["appVersion"],
        "policyVersion": target["policyVersion"],
        "idempotencyKey": str(uuid4()),
    }
    pending = graphql_http(
        client,
        headers,
        "mutation($input:RequestModelConnectionInput!){requestModelConnection(input:$input){ok errors{code} data{id status}}}",
        {"input": request_input},
    )
    assert not pending.get("errors") and pending["data"]["requestModelConnection"]["ok"], pending
    assert pending["data"]["requestModelConnection"]["data"]["status"] == "PENDING"
    # The normal direct-effect gate remains at real approvals=0.
    direct_input = {
        key: value
        for key, value in request_input.items()
        if key not in {"policyVersion", "idempotencyKey", "ifMatchAppVersion"}
    }
    direct = graphql_http(
        client,
        headers,
        "mutation($input:SubscribeClusterModelInput!){subscribeClusterModel(input:$input){ok errors{code}}}",
        {"input": direct_input},
    )
    assert not direct["data"]["subscribeClusterModel"]["ok"], direct
    assert not ManagedServiceAttachment.objects.exists() and queue == []


@pytest.mark.parametrize(
    "withdrawal", ["org_deny", "abac_deny", "foreign_team", "read_only", "wrong_provider"]
)
def test_request_target_metadata_never_admits_denied_or_foreign_destination(world, queue, client, withdrawal):
    settings(world, "DENY" if withdrawal == "org_deny" else "REQUIRE_APPROVAL")
    if withdrawal == "abac_deny":
        Policy.objects.create(
            organization=world.org,
            name="hard deny",
            slug="hard-deny",
            scope_level="ORG",
            effect="DENY",
            action_pattern="app.update",
        )
    _, headers = http_token(
        world,
        scopes=("read:apps",) if withdrawal == "read_only" else ("write:apps",),
        team=world.platform if withdrawal == "foreign_team" else world.medops,
    )
    wire = placement_wire(world)
    if withdrawal == "wrong_provider":
        wire["expectedProviderId"] = str(uuid4())
    result = graphql_http(client, headers, TARGET_PAGE, {"input": wire})
    if not result.get("errors"):
        assert not any(
            row["eligible"] for row in result["data"]["modelConnectionTargetsPage"]["items"]
        ), result
    assert (
        not ModelConnectionRequest.objects.exists()
        and not ManagedServiceAttachment.objects.exists()
        and queue == []
    )


@pytest.mark.parametrize(
    "scopes,team,expected",
    [(("write:apps",), "own", "DENY"), (("write:apps",), None, "DENY"), (("admin",), None, "AUTO")],
)
def test_auto_picker_and_action_match_actual_direct_prerequisites(
    world, queue, client, scopes, team, expected
):
    settings(world, "AUTO")
    _, headers = http_token(world, scopes=scopes, team=world.medops if team else None)
    page = graphql_http(client, headers, TARGET_PAGE, {"input": placement_wire(world)})
    assert not page.get("errors"), page
    target = next(
        row
        for row in page["data"]["modelConnectionTargetsPage"]["items"]
        if row["environmentId"] == str(world.env.guid)
    )
    assert target["action"] == expected and target["eligible"] == (expected == "AUTO")
    action = graphql_http(
        client,
        headers,
        "query($input:ModelConnectionTargetInput!){modelConnectionAction(input:$input){action reason}}",
        {"input": placement_wire(world) | {"appEnvironmentId": str(world.env.guid)}},
    )
    assert not action.get("errors") and action["data"]["modelConnectionAction"]["action"] == expected, action
    assert not ManagedServiceAttachment.objects.exists() and queue == []


def test_approver_inbox_and_review_do_not_require_app_update(world, queue, client):
    settings(world)
    row = create_request(world)
    actor = reviewer(world, "inbox")
    _, headers = http_token(world, actor=actor, scopes=("admin",))
    page = graphql_http(
        client,
        headers,
        "query($org:GUID!){modelConnectionApprovalRequestsPage(organizationId:$org){totalCount items{id status canApprove canReject canFinalize}}}",
        {"org": str(world.org.guid)},
    )
    assert not page.get("errors"), page
    assert page["data"]["modelConnectionApprovalRequestsPage"]["items"] == [
        {
            "id": str(row.guid),
            "status": "PENDING",
            "canApprove": True,
            "canReject": True,
            "canFinalize": False,
        }
    ]
    review = graphql_http(
        client,
        headers,
        "query($input:DecideModelConnectionRequestInput!){modelConnectionReviewRequest(input:$input){id canApprove}}",
        {"input": {"id": str(row.guid), "ifMatchVersion": row.version}},
    )
    assert not review.get("errors") and review["data"]["modelConnectionReviewRequest"]["canApprove"], review
    assert not ManagedServiceAttachment.objects.exists() and queue == []


@pytest.mark.parametrize("terminal", ["cancelled", "rejected"])
def test_cancel_reject_and_replays_never_connect(world, queue, terminal):
    settings(world)
    row = create_request(world)
    if terminal == "cancelled":
        with subject(world, scopes=["write:apps"]):
            first = ModelConnectionsMutation().cancel_model_connection_request(
                make_info(world.user), input=decision(row)
            )
            repeat = ModelConnectionsMutation().cancel_model_connection_request(
                make_info(world.user), input=decision(row)
            )
    else:
        with as_actor(world, reviewer(world, "reject")) as info:
            first = ModelConnectionsMutation().reject_model_connection_request(info, input=decision(row))
            repeat = ModelConnectionsMutation().reject_model_connection_request(info, input=decision(row))
    assert first.ok and repeat.ok, (first.errors, repeat.errors)
    row.refresh_from_db()
    assert row.status == terminal
    with subject(world, scopes=["write:apps"]):
        blocked = ModelConnectionsMutation().finalize_model_connection_request(
            make_info(world.user), input=decision(row)
        )
    assert not blocked.ok and not ManagedServiceAttachment.objects.exists() and queue == []


def test_idempotency_key_cannot_be_rebound_or_foreign_request_finalized(world, queue):
    settings(world)
    with subject(world, scopes=["write:apps"]):
        original = proposal(world)
        result = ModelConnectionsMutation().request_model_connection(make_info(world.user), input=original)
        changed = ModelConnectionsMutation().request_model_connection(
            make_info(world.user),
            input=RequestModelConnectionInput(**(asdict(original) | {"alias": "other"})),
        )
    assert result.ok and not changed.ok and changed.errors[0].code == "CONFLICT"
    row = ModelConnectionRequest.objects.get()
    vote(world, row, reviewer(world, "not-owner"))
    actor = reviewer(world, "foreign-finalizer")
    bind_role(
        actor,
        permissions=[Permission.APP_UPDATE],
        kind="APP",
        scope_id=world.medops_app.pk,
        slug="another-writer",
    )
    with as_actor(world, actor) as info:
        denied = ModelConnectionsMutation().finalize_model_connection_request(info, input=decision(row))
    assert not denied.ok and not ManagedServiceAttachment.objects.exists() and queue == []


def test_org_policy_write_and_model_restriction_distinct_authority(world, queue):
    from astrolift_services.schema.model_connections import (
        ModelConnectionMode,
        SetModelConnectionRestrictionInput,
        UpdateOrganizationModelConnectionPolicyInput,
    )

    policy_input = UpdateOrganizationModelConnectionPolicyInput(
        organization_id=GUID(str(world.org.guid)),
        if_match_version=0,
        mode=ModelConnectionMode.REQUIRE_APPROVAL,
        required_approvals=2,
        allow_self_approval=False,
    )
    with subject(world, scopes=["write:apps"]):
        denied = ModelConnectionsMutation().update_organization_model_connection_policy(
            make_info(world.user), input=policy_input
        )
    assert not denied.ok and not ModelConnectionPolicy.objects.exists()
    actor = reviewer(world, "org-admin")
    with as_actor(world, actor) as info:
        allowed = ModelConnectionsMutation().update_organization_model_connection_policy(
            info, input=policy_input
        )
        restriction = SetModelConnectionRestrictionInput(
            **asdict(policy_input),
            model_deployment_id=GUID(str(world.model.guid)),
            expected_cluster_id=GUID(str(world.cluster.guid)),
            expected_provider_id=GUID(str(world.cluster.provider_plugin.guid)),
            if_match_deployment_version=world.model.version,
        )
        denied = ModelConnectionsMutation().set_model_connection_restriction(info, input=restriction)
    assert allowed.ok and allowed.data.required_approvals == 2, allowed.errors
    assert not denied.ok and ModelConnectionPolicy.objects.count() == 1
    assert not ManagedServiceAttachment.objects.exists() and queue == []


def test_write_only_team_requester_can_list_refresh_cancel_and_finalize_hints(world, queue, client):
    settings(world)
    row = create_request(world)
    _, headers = http_token(world, team=world.medops)
    query = "query($org:GUID!){modelConnectionRequestsPage(organizationId:$org){totalCount items{id version status canCancel canFinalize approvalCount}}}"
    pending = graphql_http(client, headers, query, {"org": str(world.org.guid)})
    assert not pending.get("errors"), pending
    item = pending["data"]["modelConnectionRequestsPage"]["items"][0]
    assert item["id"] == str(row.guid) and item["canCancel"] and not item["canFinalize"]
    exact = graphql_http(
        client,
        headers,
        "query($input:DecideModelConnectionRequestInput!){modelConnectionRequest(input:$input){id status canCancel canFinalize}}",
        {"input": {"id": str(row.guid), "ifMatchVersion": row.version}},
    )
    assert not exact.get("errors") and exact["data"]["modelConnectionRequest"]["canCancel"], exact
    voter = reviewer(world, "page-voter")
    vote(world, row, voter)
    approved = graphql_http(client, headers, query, {"org": str(world.org.guid)})
    assert not approved.get("errors"), approved
    item = approved["data"]["modelConnectionRequestsPage"]["items"][0]
    assert (
        item["status"] == "APPROVED"
        and item["canFinalize"]
        and item["canCancel"]
        and item["approvalCount"] == 1
    )
    from astrolift_identity.models import RoleBinding

    RoleBinding.objects.filter(user=voter).update(
        deleted_at=__import__("django.utils.timezone", fromlist=["now"]).now()
    )
    withdrawn = graphql_http(client, headers, query, {"org": str(world.org.guid)})
    assert not withdrawn.get("errors"), withdrawn
    item = withdrawn["data"]["modelConnectionRequestsPage"]["items"][0]
    assert not item["canFinalize"] and item["approvalCount"] == 0
    assert not ManagedServiceAttachment.objects.exists() and queue == []


def test_review_inbox_policy_change_disables_actions(world, queue, client):
    policy = settings(world)
    row = create_request(world)
    _, headers = http_token(world, actor=reviewer(world, "stale-inbox"), scopes=("admin",))
    policy.mode = "DENY"
    policy.save()
    page = graphql_http(
        client,
        headers,
        "query($org:GUID!){modelConnectionApprovalRequestsPage(organizationId:$org){items{id status canApprove canReject}}}",
        {"org": str(world.org.guid)},
    )
    assert not page.get("errors"), page
    assert page["data"]["modelConnectionApprovalRequestsPage"]["items"] == [
        {"id": str(row.guid), "status": "STALE", "canApprove": False, "canReject": False}
    ]
    assert not ManagedServiceAttachment.objects.exists() and queue == []


def test_request_display_omits_email_username_and_deleted_target_label(world, queue):
    from astrolift_services.schema.model_connections import _request_type

    settings(world)
    row = create_request(world)
    world.user.username = "private-user@example.invalid"
    world.user.save(update_fields=["username"])
    row.requester = world.user
    row.model_deployment.deleted_at = __import__("django.utils.timezone", fromlist=["now"]).now()
    dto = _request_type(row)
    assert dto.requester_username is None and dto.model_name is None
    assert dto.app_name == world.medops_app.name and dto.environment_name == world.env.name


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", ["coroutine", "stream"])
def test_request_only_gate_does_not_lend_deferral_to_async_effects(world, kind):
    import asyncio

    from asgiref.sync import sync_to_async

    Policy.objects.create(
        organization=world.org,
        name="async quorum",
        slug="async-quorum",
        scope_level="ORG",
        effect="ALLOW",
        action_pattern="app.update",
        conditions=[{"kind": "approval_required", "min_approvers": 1}],
    )

    def scope(args):
        return PermissionScope(ScopeKind.APP, world.medops_app.pk)

    async def ordinary_effect():
        assert not abac.current_attributes().approval_request
        with pytest.raises(PermissionDenied):
            await sync_to_async(check_permission)(Permission.APP_UPDATE, scope=scope({}))

    @require_permission(Permission.APP_UPDATE, scope=scope, approval_request=True)
    async def coroutine():
        await ordinary_effect()
        return "pending"

    @require_permission(Permission.APP_UPDATE, scope=scope, approval_request=True)
    async def stream():
        await ordinary_effect()
        yield "pending"

    async def run():
        before = abac.current_attributes()
        if kind == "coroutine":
            assert await coroutine() == "pending"
        else:
            generator = stream()
            try:
                assert await anext(generator) == "pending"
            finally:
                await generator.aclose()
        assert abac.current_attributes() is before and not before.approval_request

    with subject(world, scopes=["write:apps"]), abac.operation_attributes(approvals=0):
        asyncio.run(run())


def test_request_deferral_cannot_authorize_a_review_vote_effect(world, queue):
    settings(world)
    row = create_request(world)
    actor = reviewer(world, "review-quorum-policy")
    Policy.objects.create(
        organization=world.org,
        name="review action quorum",
        slug="review-action-quorum",
        scope_level="ORG",
        effect="ALLOW",
        action_pattern="app.approve_deploy",
        conditions=[{"kind": "approval_required", "min_approvers": 1}],
    )
    # Refresh the reviewed fingerprint to isolate the review-action condition,
    # rather than merely proving old request invalidation on a policy change.
    from astrolift_services.model_connection_policy import effective_policy

    row.policy_version = effective_policy(world.model).version
    row.save()
    with as_actor(world, actor) as info:
        denied = ModelConnectionsMutation().approve_model_connection_request(info, input=decision(row))
    assert not denied.ok and not ModelConnectionApproval.objects.exists()
    assert not ManagedServiceAttachment.objects.exists() and queue == []


RESTRICTION_READ = """query($input:ModelConnectionPlacementInput!){modelConnectionRestriction(input:$input){id version mode requiredApprovals allowSelfApproval}}"""
RESTRICTION_WRITE = """mutation($input:SetModelConnectionRestrictionInput!){setModelConnectionRestriction(input:$input){ok errors{code message} data{id version mode requiredApprovals allowSelfApproval}}}"""


def test_actual_http_restriction_neutral_absence_write_reload_versioned_edit(world, queue, client):
    settings(world, "DENY", quorum=3, self_approval=False)
    world.user.is_superuser = True
    world.user.save(update_fields=["is_superuser"])
    _, headers = http_token(world, scopes=["admin"])
    wire = placement_wire(world)
    empty = graphql_http(client, headers, RESTRICTION_READ, {"input": wire})
    assert not empty.get("errors"), empty
    assert empty["data"]["modelConnectionRestriction"] == {
        "id": None,
        "version": 0,
        "mode": "AUTO",
        "requiredApprovals": 1,
        "allowSelfApproval": True,
    }
    payload = wire | {
        "ifMatchVersion": 0,
        "ifMatchDeploymentVersion": world.model.version,
        "mode": "REQUIRE_APPROVAL",
        "requiredApprovals": 2,
        "allowSelfApproval": False,
    }
    written = graphql_http(client, headers, RESTRICTION_WRITE, {"input": payload})
    assert not written.get("errors"), written
    outcome = written["data"]["setModelConnectionRestriction"]
    assert outcome["ok"], outcome
    current = graphql_http(client, headers, RESTRICTION_READ, {"input": wire})
    assert not current.get("errors"), current
    assert current["data"]["modelConnectionRestriction"] == outcome["data"]
    old_version = outcome["data"]["version"]
    payload.update(ifMatchVersion=old_version, mode="DENY", requiredApprovals=3)
    edited = graphql_http(client, headers, RESTRICTION_WRITE, {"input": payload})
    assert not edited.get("errors"), edited
    result = edited["data"]["setModelConnectionRestriction"]
    assert result["ok"] and result["data"]["version"] > old_version, result
    refreshed = graphql_http(client, headers, RESTRICTION_READ, {"input": wire})
    assert refreshed["data"]["modelConnectionRestriction"] == result["data"]
    stale = graphql_http(client, headers, RESTRICTION_WRITE, {"input": payload})
    assert not stale["data"]["setModelConnectionRestriction"]["ok"], stale
    assert not ManagedServiceAttachment.objects.exists() and queue == []


@pytest.mark.parametrize(
    "case",
    [
        "ordinary_admin",
        "narrow_operator",
        "team_operator",
        "foreign_org",
        "foreign_model",
        "wrong_cluster",
        "wrong_provider",
    ],
)
def test_actual_http_restriction_read_refuses_unadmitted_or_foreign_placement(world, queue, client, case):
    actor = reviewer(world, "restriction-admin") if case == "ordinary_admin" else world.user
    if case != "ordinary_admin":
        actor.is_superuser = True
        actor.save(update_fields=["is_superuser"])
    scopes = ["write:apps"] if case == "narrow_operator" else ["admin"]
    team = world.medops if case == "team_operator" else None
    _, headers = http_token(world, actor=actor, scopes=scopes, team=team)
    wire = placement_wire(world)
    field = {
        "foreign_org": "organizationId",
        "foreign_model": "modelDeploymentId",
        "wrong_cluster": "expectedClusterId",
        "wrong_provider": "expectedProviderId",
    }.get(case)
    if field:
        wire[field] = str(uuid4())
    result = graphql_http(client, headers, RESTRICTION_READ, {"input": wire})
    assert result.get("errors") and not result.get("data"), result
    assert not ModelConnectionPolicy.objects.exists()
    assert not ManagedServiceAttachment.objects.exists() and queue == []
