"""Real HTTP/PG intents and stateful native IAM/Kubernetes protocol boundaries."""

from copy import deepcopy
from dataclasses import asdict, replace

import boto3
import pytest
from aws.bedrock_catalogue import BedrockCatalogueDetail, BedrockSourceKind, CatalogueState
from aws.identity_irsa import IRSAConfig
from aws.identity_native import NativeIRSADriver

from astrolift_registry.models import Workload
from astrolift_services.model_subscriptions import subscription_namespace, subscription_secret_name
from astrolift_services.models import ManagedServiceAttachment
from astrolift_services.tests.test_bedrock_connections_2269 import (
    ACCOUNT,
    ARN,
    DETAIL,
    PROFILE,
    register,
    register_input,
    world,  # noqa: F401 -- real database source fixture
)
from astrolift_services.tests.test_cluster_model_mutations_2213 import (
    allowed,
    subscription,
)
from astrolift_services.tests.test_cluster_model_mutations_2213 import (
    queue as queue_fixture,
)
from astrolift_services.tests.test_model_connection_2270 import graphql_http, http_token
from astrolift_services.tests.test_model_host_session_2269 import public
from astrolift_services.tests.test_shared_model_binding_runtime_2213 import (
    ConditionalCluster,
    workload_resource,
)
from astrolift_workflows.activities import native_model_connections as reconcile
from core.app_deploy import workload_identity_role_name
from core.cluster_credentials import credential_for_cluster

pytestmark = pytest.mark.django_db
SUBSCRIBE = "mutation($input:SubscribeClusterModelInput!){subscribeClusterModel(input:$input){ok errors{code} data{subscription{id desiredRevision status} deployment{id status ready} restartRequired}}}"
REVOKE = "mutation($input:RevokeModelSubscriptionInput!){revokeModelSubscription(input:$input){ok errors{code} data{subscription{id desiredRevision status}}}}"


class IAM:
    def __init__(self):
        client = boto3.client(
            "iam",
            region_name="us-east-1",
            aws_access_key_id="synthetic-key",
            aws_secret_access_key="synthetic-secret",
        )
        self.exceptions = client.exceptions
        self.roles, self.policies, self.effects = {}, {}, []
        self.external = {"operator-policy": {"Statement": []}}

    def missing(self):
        return self.exceptions.NoSuchEntityException({"Error": {"Code": "NoSuchEntity"}}, "GetRole")

    def get_role(self, RoleName):
        if RoleName not in self.roles:
            raise self.missing()
        return {"Role": deepcopy(self.roles[RoleName])}

    def create_role(self, **args):
        if args["RoleName"] in self.roles:
            raise self.exceptions.EntityAlreadyExistsException(
                {"Error": {"Code": "EntityAlreadyExists"}}, "CreateRole"
            )
        import json

        name = args["RoleName"]
        self.roles[name] = {
            "Arn": f"arn:aws:iam::{ACCOUNT}:role/{name}",
            "Tags": args["Tags"],
            "AssumeRolePolicyDocument": json.loads(args["AssumeRolePolicyDocument"]),
        }
        self.effects.append(("create", name))
        return {"Role": self.roles[name]}

    def update_assume_role_policy(self, RoleName, PolicyDocument):
        import json

        self.roles[RoleName]["AssumeRolePolicyDocument"] = json.loads(PolicyDocument)
        self.effects.append(("trust", RoleName))

    def put_role_policy(self, RoleName, PolicyName, PolicyDocument):
        import json

        self.policies[(RoleName, PolicyName)] = json.loads(PolicyDocument)
        self.effects.append(("put", RoleName, PolicyName))

    def delete_role_policy(self, RoleName, PolicyName):
        self.policies.pop((RoleName, PolicyName), None)
        self.effects.append(("delete", RoleName, PolicyName))

    def get_role_policy(self, RoleName, PolicyName):
        if (RoleName, PolicyName) not in self.policies:
            raise self.missing()
        return {"PolicyDocument": deepcopy(self.policies[(RoleName, PolicyName)])}

    def close(self):
        pass


@pytest.fixture
def queue(monkeypatch):
    return queue_fixture.__wrapped__(monkeypatch)


@pytest.fixture
def runtime(world, client, monkeypatch):  # noqa: F811 -- imported real database fixture
    w = world
    w.cluster.auth_config = {"cluster_oidc_issuer": "oidc.eks.us-east-1.amazonaws.com/id/ABC123"}
    w.cluster.save()
    w.model, _ = register(w, client)
    allowed(w)
    w.user.is_superuser = False
    w.user.save()
    w.app_token, w.app_headers = http_token(w, scopes=["read:apps", "write:apps"])
    w.driver = ConditionalCluster()
    workloads = list(Workload.objects.filter(registered_app=w.env.registered_app))
    for workload in workloads:
        w.driver.put(subscription_namespace(w.env), workload_resource(w.env, workload))
    w.iam = IAM()
    monkeypatch.setattr(reconcile, "_destination_driver", lambda cluster: w.driver)

    def native_driver(cluster):
        return NativeIRSADriver(
            config=IRSAConfig(
                region="us-east-1",
                account_id=ACCOUNT,
                cluster_oidc_issuer=cluster.auth_config["cluster_oidc_issuer"],
                credential=credential_for_cluster(cluster),
            ),
            iam_client=w.iam,
        )

    monkeypatch.setattr(
        "astrolift_workflows.activities.workload_identity._native_identity_driver", native_driver
    )
    return w


def subscribe(w, client):
    reply = graphql_http(client, w.app_headers, SUBSCRIBE, {"input": public(asdict(subscription(w)))})
    assert not reply.get("errors"), reply
    value = reply["data"]["subscribeClusterModel"]
    assert value["ok"], value
    w.model.refresh_from_db()
    return ManagedServiceAttachment.objects.get(guid=value["data"]["subscription"]["id"]), value


def finish(w, row):
    w.driver.converge = True
    assert any(reconcile._finish_sync(w.model.pk, row.desired_revision) for _ in range(3))
    row.refresh_from_db()
    w.model.refresh_from_db()


def revoke(w, row, client):
    wire = {
        "organizationId": str(w.org.guid),
        "expectedClusterId": str(w.cluster.guid),
        "expectedProviderId": str(w.cluster.provider_plugin.guid),
        "id": str(row.guid),
        "ifMatchVersion": row.version,
        "ifMatchDeploymentVersion": w.model.version,
    }
    result = graphql_http(client, w.app_headers, REVOKE, {"input": wire})
    assert not result.get("errors"), result
    assert result["data"]["revokeModelSubscription"]["ok"], result
    row.refresh_from_db()
    w.model.refresh_from_db()


def test_http_auto_native_binding_then_observed_identity_without_inference_claim(runtime, client, queue):  # noqa: F811
    w = runtime
    row, reply = subscribe(w, client)
    assert reply["data"]["restartRequired"] is True and reply["data"]["deployment"]["ready"] is None
    assert row.credential_ref == "" and row.subscription_status == "pending"
    assert queue[0][0] == "NativeModelConnectionReconcileWorkflow"
    assert reconcile._apply_sync(w.model.pk, row.desired_revision) == "configured"
    assert not reconcile._finish_sync(w.model.pk, row.desired_revision)
    secret = w.driver.objects[("v1/Secret", subscription_namespace(w.env), subscription_secret_name(row))]
    values = secret["stringData"]
    assert values["MODEL_CHAT_DEPLOYMENT_NAME"] == ARN and values["MODEL_CHAT_AUTH_MODE"] == "cloud_identity"
    assert not any("API_KEY" in key or "TOKEN" in key for key in values)
    finish(w, row)
    assert row.subscription_status == "active" and row.applied_revision == row.desired_revision
    assert w.model.model_ready_observed_at is None and w.model.model_ready_generation is None
    role_name = workload_identity_role_name(w.env.registered_app)
    policy = w.iam.policies[(role_name, "astrolift-workload-policy")]
    assert {s["Resource"] for s in policy["Statement"]} == {ARN}
    assert w.iam.external == {"operator-policy": {"Statement": []}}


@pytest.mark.parametrize("absence", ["deleted", "denied", "changed_destinations"])
def test_revoke_removes_owned_binding_and_last_grant_even_if_source_unreadable(
    runtime,
    client,
    queue,  # noqa: F811 -- imported pytest fixture
    absence,
):
    w = runtime
    if absence == "changed_destinations":
        # Registration with a genuine native profile snapshot before admitting its intent.
        w.user.is_superuser = True
        w.user.save()
        w.detail = replace(
            DETAIL,
            source=replace(
                DETAIL.source,
                kind=BedrockSourceKind.INFERENCE_PROFILE,
                identifier=PROFILE.rsplit("/", 1)[-1],
                arn=PROFILE,
                profile_type="SYSTEM_DEFINED",
                destination_model_arns=(ARN,),
            ),
        )
        wire = register_input(w) | {
            "name": "profile",
            "sourceKind": "INFERENCE_PROFILE",
            "sourceIdentifier": PROFILE,
        }
        from astrolift_services.tests.test_bedrock_connections_2269 import REGISTER

        result = graphql_http(client, w.headers, REGISTER, {"input": wire})
        assert result["data"]["registerBedrockModelConnection"]["ok"], result
        from astrolift_services.models import ManagedService

        w.model = ManagedService.objects.get(
            guid=result["data"]["registerBedrockModelConnection"]["data"]["id"]
        )
        w.user.is_superuser = False
        w.user.save()
    row, _ = subscribe(w, client)
    reconcile._apply_sync(w.model.pk, row.desired_revision)
    finish(w, row)
    if absence == "changed_destinations":
        w.detail = replace(
            w.detail,
            source=replace(w.detail.source, destination_model_arns=(ARN.replace("us-east-1", "us-west-2"),)),
        )
    else:
        w.detail = BedrockCatalogueDetail(
            CatalogueState.NOT_FOUND if absence == "deleted" else CatalogueState.DENIED
        )
    before_reads = len(w.reads)
    revoke(w, row, client)
    reconcile._apply_sync(w.model.pk, row.desired_revision)
    finish(w, row)
    assert len(w.reads) == before_reads, "removal cannot require current source metadata"
    assert row.subscription_status == "revoked"
    assert not w.iam.policies
    assert w.iam.external == {"operator-policy": {"Statement": []}}
    assert ("v1/Secret", subscription_namespace(w.env), subscription_secret_name(row)) not in w.driver.objects


@pytest.mark.parametrize("absence", ["deleted", "denied", "changed_destinations"])
def test_mixed_same_model_removal_cleans_grants_without_admitting_retained_alias(
    runtime,
    client,
    queue,
    absence,  # noqa: F811
):  # noqa: F811
    w = runtime
    if absence == "changed_destinations":
        from astrolift_services.native_model_connections import canonical_handle, connection_config

        w.detail = replace(
            DETAIL,
            source=replace(
                DETAIL.source,
                kind=BedrockSourceKind.INFERENCE_PROFILE,
                identifier=PROFILE.rsplit("/", 1)[-1],
                arn=PROFILE,
                profile_type="SYSTEM_DEFINED",
                destination_model_arns=(ARN,),
            ),
        )
        w.model.config = connection_config(w.cluster, w.org, w.detail)
        w.model.backend_ref = canonical_handle(w.model)
        w.model.applied_config = dict(w.model.config)
        w.model.save()
    first, _ = subscribe(w, client)
    reconcile._apply_sync(w.model.pk, first.desired_revision)
    finish(w, first)
    second_wire = public(asdict(subscription(w, alias="alternate")))
    result = graphql_http(client, w.app_headers, SUBSCRIBE, {"input": second_wire})
    assert result["data"]["subscribeClusterModel"]["ok"], result
    second = ManagedServiceAttachment.objects.get(
        guid=result["data"]["subscribeClusterModel"]["data"]["subscription"]["id"]
    )
    w.model.refresh_from_db()
    reconcile._apply_sync(w.model.pk, second.desired_revision)
    finish(w, second)
    first.refresh_from_db()
    if absence == "changed_destinations":
        w.detail = replace(
            w.detail,
            source=replace(w.detail.source, destination_model_arns=(ARN.replace("us-east-1", "us-west-2"),)),
        )
    else:
        w.detail = BedrockCatalogueDetail(
            CatalogueState.NOT_FOUND if absence == "deleted" else CatalogueState.DENIED
        )
    prior_applied = second.applied_revision
    revoke(w, first, client)
    assert reconcile._apply_sync(w.model.pk, first.desired_revision) == "cleanup_pending_source_unavailable"
    finish(w, first)
    second.refresh_from_db()
    assert first.subscription_status == "revoked" and not first.desired_enabled
    assert second.desired_enabled and second.subscription_status == "failed"
    assert second.applied_revision == prior_applied
    assert (
        w.model.status == "failed" and w.model.applied_subscription_revision < w.model.subscription_revision
    )
    assert not w.iam.policies and w.iam.external == {"operator-policy": {"Statement": []}}
    ns = subscription_namespace(w.env)
    assert ("v1/Secret", ns, subscription_secret_name(first)) not in w.driver.objects
    assert ("v1/Secret", ns, subscription_secret_name(second)) in w.driver.objects


def test_historical_revoked_alias_cannot_admit_new_intent_with_missing_source(runtime, client, queue):  # noqa: F811
    w = runtime
    old, _ = subscribe(w, client)
    reconcile._apply_sync(w.model.pk, old.desired_revision)
    finish(w, old)
    revoke(w, old, client)
    reconcile._apply_sync(w.model.pk, old.desired_revision)
    finish(w, old)
    result = graphql_http(
        client,
        w.app_headers,
        SUBSCRIBE,
        {"input": public(asdict(subscription(w, alias="new")))},
    )
    assert result["data"]["subscribeClusterModel"]["ok"], result
    current = ManagedServiceAttachment.objects.get(
        guid=result["data"]["subscribeClusterModel"]["data"]["subscription"]["id"]
    )
    w.model.refresh_from_db()
    w.detail = BedrockCatalogueDetail(CatalogueState.NOT_FOUND)
    effects = list(w.iam.effects)
    writes = list(w.driver.writes)
    with pytest.raises(ValueError):
        reconcile._apply_sync(w.model.pk, current.desired_revision)
    with pytest.raises(ValueError):
        reconcile._finish_sync(w.model.pk, current.desired_revision)
    current.refresh_from_db()
    assert current.subscription_status == "pending" and current.applied_revision == 0
    assert w.iam.effects == effects and w.driver.writes == writes


@pytest.mark.parametrize("mutation", ["foreign_app", "missing_owner", "foreign_role"])
def test_ambiguous_service_account_refuses_before_iam_effect(runtime, client, queue, mutation):  # noqa: F811
    from astrolift_services.model_subscriptions import _native_service_account

    w = runtime
    row, _ = subscribe(w, client)
    name = workload_identity_role_name(w.env.registered_app)
    arn = f"arn:aws:iam::{ACCOUNT}:role/{name}"
    identity = {"role": name, "annotation": {"eks.amazonaws.com/role-arn": arn}}
    manifest, _ = _native_service_account(row, w.driver, identity)
    if mutation == "foreign_app":
        manifest["metadata"]["labels"]["astrolift.io/app-id"] = "00000000-0000-4000-8000-000000000001"
    elif mutation == "missing_owner":
        manifest["metadata"]["labels"].pop("astrolift.io/organization-id")
    else:
        manifest["metadata"]["annotations"]["eks.amazonaws.com/role-arn"] = arn + "-foreign"
    w.driver.put(subscription_namespace(w.env), manifest)
    with pytest.raises(ValueError, match="ownership is unproved"):
        reconcile._apply_sync(w.model.pk, row.desired_revision)
    assert not w.iam.effects and not w.driver.writes


def test_native_request_two_distinct_voters_then_current_owner_finalize(runtime, client, queue):  # noqa: F811
    from astrolift_services.models import ModelConnectionRequest
    from astrolift_services.tests.test_model_connection_2270 import proposal, reviewer, settings

    w = runtime
    settings(w, "REQUIRE_APPROVAL", quorum=2)
    direct = graphql_http(client, w.app_headers, SUBSCRIBE, {"input": public(asdict(subscription(w)))})
    assert not direct["data"]["subscribeClusterModel"]["ok"]
    request_query = "mutation($input:RequestModelConnectionInput!){requestModelConnection(input:$input){ok errors{code} data{id version status approvalCount subscriptionId}}}"
    variables = {"input": public(asdict(proposal(w)))}
    intake = graphql_http(client, w.app_headers, request_query, variables)
    replay = graphql_http(client, w.app_headers, request_query, variables)
    assert intake["data"]["requestModelConnection"]["ok"], intake
    data = intake["data"]["requestModelConnection"]["data"]
    assert replay["data"]["requestModelConnection"]["data"]["id"] == data["id"]
    assert data["status"] == "PENDING" and data["subscriptionId"] is None
    assert (
        not ManagedServiceAttachment.objects.exists()
        and queue == []
        and not w.iam.effects
        and not w.driver.writes
    )
    row = ModelConnectionRequest.objects.get(guid=data["id"])
    approve = "mutation($input:DecideModelConnectionRequestInput!){approveModelConnectionRequest(input:$input){ok errors{code} data{id version status approvalCount}}}"
    reviewer_headers = None
    for suffix, expected in (("native-one", 1), ("native-one-repeat", 1), ("native-two", 2)):
        if suffix == "native-one-repeat":
            headers = reviewer_headers
        else:
            user = reviewer(w, suffix)
            _, reviewer_headers = http_token(w, actor=user, scopes=["admin"])
            headers = reviewer_headers
        result = graphql_http(
            client, headers, approve, {"input": {"id": str(row.guid), "ifMatchVersion": row.version}}
        )
        assert result["data"]["approveModelConnectionRequest"]["ok"], result
        assert result["data"]["approveModelConnectionRequest"]["data"]["approvalCount"] == expected
        row.refresh_from_db()
        assert not ManagedServiceAttachment.objects.exists() and queue == []
    assert row.status == "approved"
    final = graphql_http(
        client,
        w.app_headers,
        "mutation($input:DecideModelConnectionRequestInput!){finalizeModelConnectionRequest(input:$input){ok errors{code} data{id status subscriptionId}}}",
        {"input": {"id": str(row.guid), "ifMatchVersion": row.version}},
    )
    assert final["data"]["finalizeModelConnectionRequest"]["ok"], final
    w.model.refresh_from_db()
    attached = ManagedServiceAttachment.objects.get()
    assert attached.credential_ref == "" and attached.subscription_status == "pending"
    assert final["data"]["finalizeModelConnectionRequest"]["data"]["subscriptionId"] == str(attached.guid)
    assert w.model.model_ready_observed_at is None
    assert len(queue) == 1 and queue[0][0] == "NativeModelConnectionReconcileWorkflow"
    assert not w.iam.effects and not w.driver.writes


def test_native_org_deny_blocks_intake_and_direct_effect(runtime, client, queue):  # noqa: F811
    from astrolift_services.models import ModelConnectionRequest
    from astrolift_services.tests.test_model_connection_2270 import proposal, settings

    w = runtime
    settings(w, "DENY")
    direct = graphql_http(client, w.app_headers, SUBSCRIBE, {"input": public(asdict(subscription(w)))})
    intake = graphql_http(
        client,
        w.app_headers,
        "mutation($input:RequestModelConnectionInput!){requestModelConnection(input:$input){ok errors{code}}}",
        {"input": public(asdict(proposal(w)))},
    )
    assert not direct["data"]["subscribeClusterModel"]["ok"]
    assert not intake["data"]["requestModelConnection"]["ok"]
    assert not ModelConnectionRequest.objects.exists() and not ManagedServiceAttachment.objects.exists()
    assert queue == [] and not w.iam.effects and not w.driver.writes


def test_native_subscription_metrics_unsupported_without_prometheus_transport(
    runtime, client, queue, monkeypatch
):  # noqa: F811
    from datetime import timedelta

    from django.utils import timezone

    from astrolift_services.tests.test_cluster_model_queries_2213 import grant
    from core.permissions import Permission

    w = runtime
    row, _ = subscribe(w, client)
    grant(w, Permission.APP_READ_METRICS, "APP", w.medops_app.pk)

    def forbidden(*args, **kwargs):
        pytest.fail("Native subscription metrics entered local Prometheus transport")

    monkeypatch.setattr("astrolift_services.model_subscription_observations.prom.query_range", forbidden)
    now = timezone.now()
    result = graphql_http(
        client,
        w.app_headers,
        "query($org:GUID!,$model:GUID!,$sub:GUID!,$cluster:GUID!,$provider:GUID!,$start:DateTime!,$end:DateTime!){astroliftModelSubscriptionMetrics(organizationId:$org,serviceId:$model,subscriptionId:$sub,expectedClusterId:$cluster,expectedProviderId:$provider,start:$start,end:$end){subscriptionId metrics{key state value source samples{value}}}}",
        {
            "org": str(w.org.guid),
            "model": str(w.model.guid),
            "sub": str(row.guid),
            "cluster": str(w.cluster.guid),
            "provider": str(w.cluster.provider_plugin.guid),
            "start": (now - timedelta(minutes=5)).isoformat(),
            "end": now.isoformat(),
        },
    )
    assert not result.get("errors"), result
    data = result["data"]["astroliftModelSubscriptionMetrics"]
    assert data["subscriptionId"] == str(row.guid)
    assert all(
        m["state"] == "UNSUPPORTED"
        and m["value"] is None
        and m["samples"] == []
        and m["source"] == "native_connection_unsupported"
        for m in data["metrics"]
    )


def test_density_excludes_native_connections_and_preserves_local_inventory(runtime, client):
    from datetime import timedelta

    from django.utils import timezone

    from astrolift_services.models import ManagedService
    from astrolift_services.tests.test_cluster_model_queries_2213 import grant
    from core.permissions import Permission

    w = runtime
    grant(w, Permission.CLUSTER_REGISTER)
    now = timezone.now()
    local_count = ManagedService.objects.filter(
        organization=w.org, tenant_cluster=w.cluster, variant="vllm"
    ).count()
    result = graphql_http(
        client,
        w.headers,
        "query($cluster:GUID!,$provider:GUID!,$start:DateTime!,$end:DateTime!){astroliftClusterModelDensity(clusterId:$cluster,expectedProviderId:$provider,start:$start,end:$end){modelCount}}",
        {
            "cluster": str(w.cluster.guid),
            "provider": str(w.cluster.provider_plugin.guid),
            "start": (now - timedelta(minutes=5)).isoformat(),
            "end": now.isoformat(),
        },
    )
    assert not result.get("errors"), result
    assert result["data"]["astroliftClusterModelDensity"]["modelCount"] == local_count
