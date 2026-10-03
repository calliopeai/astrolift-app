"""Actual PostgreSQL and authenticated GraphQL admission; no cloud effects."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from astrolift_clusters import log_collector as collector
from astrolift_clusters.models import ClusterLogCollectorOperation, TenantCluster
from astrolift_clusters.tests import test_server_agent_install_1696 as agent_cases

world = agent_cases.world
no_search = agent_cases.no_search
pytestmark = pytest.mark.django_db(transaction=True)
FIELDS = "id clusterId requestId expectedVersion expectedSource retentionDays status stage retryable cleanupPending coverage workflowId deadline postLossVerifiedAt activatedAt activatedClusterVersion errorCode errorMessage readerPolicy"
REVIEW = "query($id:GUID!,$days:Int!){astroliftClusterLogCollectorReview(clusterId:$id,retentionDays:$days){clusterId version source supported refusalCode policy readerPolicy}}"
INSTALL = (
    "mutation($input:InstallClusterLogCollectorInput!){astroliftInstallClusterLogCollector(input:$input){ok errors{code message} data{"
    + FIELDS
    + "}}}"
)
STATUS = "query($id:GUID!){astroliftClusterLogCollectorOperation(operationId:$id){" + FIELDS + "}}"


@pytest.fixture
def ready(world, settings):
    settings.ASTROLIFT_COLLECTOR_PROBE_IMAGE = "registry.invalid/approved@sha256:" + "a" * 64
    world.cluster.lifecycle = TenantCluster.Lifecycle.MANAGED
    world.cluster.provider_config = {
        "cluster_name": "disposable-eks",
        "region": "us-west-2",
        "account_id": "000000000000",
    }
    world.cluster.save()
    return world


def review(world, days=30):
    return agent_cases.gql(world, REVIEW, {"id": str(world.cluster.guid), "days": days})["data"][
        "astroliftClusterLogCollectorReview"
    ]


def install(world, **changes):
    reviewed = review(world)
    request = {
        "clusterId": reviewed["clusterId"],
        "requestId": str(uuid4()),
        "expectedVersion": reviewed["version"],
        "expectedSource": reviewed["source"],
        "retentionDays": 30,
    }
    request.update(changes)
    return request, agent_cases.gql(world, INSTALL, {"input": request})["data"][
        "astroliftInstallClusterLogCollector"
    ]


def test_review_nonmutating_exact_group_only(ready):
    before = ready.cluster.version
    result = review(ready)
    assert result["supported"] is True and len(result["source"]) == 64
    assert result["readerPolicy"]["Statement"] == [
        {
            "Effect": "Allow",
            "Action": ["logs:FilterLogEvents"],
            "Resource": f"arn:aws:logs:us-west-2:000000000000:log-group:/astrolift/clusters/{ready.cluster.guid}/pods:*",
        }
    ]
    assert not ready.starts and not ready.server.calls
    ready.cluster.refresh_from_db()
    assert ready.cluster.version == before and not ClusterLogCollectorOperation.objects.exists()


def test_original_request_recovery_and_status(ready, monkeypatch):
    request, result = install(ready)
    assert result["ok"] is True
    dto = result["data"]
    assert dto["status"] == "QUEUED" and dto["activatedAt"] is None and dto["postLossVerifiedAt"] is None
    assert dto["readerPolicy"] == {} and dto["coverage"] == "unknown" and len(ready.starts) == 1
    monkeypatch.setattr(
        "astrolift_workflows.client.recover_workflow_once",
        lambda *a, **kw: SimpleNamespace(run_id="disposable-run"),
    )
    monkeypatch.setattr(
        "astrolift_workflows.client.describe_workflow_instance",
        lambda *a, **kw: {
            "run_id": "disposable-run",
            "workflow_type": "InstallClusterLogCollectorWorkflow",
            "status": "RUNNING",
        },
    )
    replay = agent_cases.gql(ready, INSTALL, {"input": request})["data"][
        "astroliftInstallClusterLogCollector"
    ]
    assert replay["ok"] and replay["data"]["id"] == dto["id"] and len(ready.starts) == 1
    assert ClusterLogCollectorOperation.objects.count() == 1
    row = ClusterLogCollectorOperation.objects.get()
    assert row.workflow_run_id == "disposable-run" and row.credential_ceiling["token_guid"] == str(
        ready.token.guid
    )
    assert (
        agent_cases.gql(ready, STATUS, {"id": dto["id"]})["data"]["astroliftClusterLogCollectorOperation"][
            "id"
        ]
        == dto["id"]
    )
    request["retentionDays"] = 7
    refusal = agent_cases.gql(ready, INSTALL, {"input": request})["data"][
        "astroliftInstallClusterLogCollector"
    ]
    assert refusal["errors"][0]["code"] == "REQUEST_CHANGED" and not ready.server.calls


@pytest.mark.parametrize(
    "field,value",
    [
        (
            "provider_config",
            {"account_id": "111111111111", "region": "us-west-2", "cluster_name": "different"},
        ),
        ("endpoint", "https://changed.invalid"),
    ],
)
def test_reviewed_source_cannot_recapture_new_configuration(ready, field, value):
    reviewed = review(ready)
    setattr(ready.cluster, field, value)
    ready.cluster.save()
    _, result = install(ready, expectedVersion=reviewed["version"], expectedSource=reviewed["source"])
    assert result["errors"][0]["code"] == "SOURCE_CHANGED"
    assert not ClusterLogCollectorOperation.objects.exists() and not ready.starts


def test_retention_and_probe_policy_bound_to_review(ready, settings):
    reviewed = review(ready)
    _, result = install(ready, retentionDays=7, expectedSource=reviewed["source"])
    assert result["errors"][0]["code"] == "SOURCE_CHANGED"
    settings.ASTROLIFT_COLLECTOR_PROBE_IMAGE = "registry.invalid/approved@sha256:" + "b" * 64
    _, result = install(ready, expectedSource=reviewed["source"])
    assert result["errors"][0]["code"] == "SOURCE_CHANGED" and not ready.starts


@pytest.mark.parametrize(
    "config,code",
    [
        (
            {"log_driver": "loki", "log_config": {"endpoint": "https://external.invalid"}},
            "EXTERNAL_BACKEND_CONFIGURED",
        ),
        (
            {"log_driver": "cloudwatch_logs", "log_config": {"region": "us-west-2", "log_group": "/foreign"}},
            "EXTERNAL_BACKEND_CONFIGURED",
        ),
    ],
)
def test_external_backend_preserved(ready, config, code):
    ready.cluster.provider_config.update(config)
    ready.cluster.save()
    before = ready.cluster.provider_config.copy()
    assert review(ready)["refusalCode"] == code
    _, result = install(ready)
    assert result["errors"][0]["code"] == code
    ready.cluster.refresh_from_db()
    assert ready.cluster.provider_config == before and not ready.starts


def test_exact_owned_name_without_activation_proof_refused(ready):
    ready.cluster.provider_config.update(collector._candidate(ready.cluster).candidate_reader_config())
    ready.cluster.save()
    assert review(ready)["refusalCode"] == "EXISTING_BINDING_UNPROVEN"


def test_account_disagreement_refused_before_dispatch(ready):
    ready.cluster.cloud_account_id = "111111111111"
    ready.cluster.save()
    assert review(ready)["refusalCode"] == "ACCOUNT_MISMATCH" and not ready.starts and not ready.server.calls


def test_probe_approval_required(ready, settings):
    settings.ASTROLIFT_COLLECTOR_PROBE_IMAGE = ""
    assert review(ready)["refusalCode"] == "PROBE_IMAGE_NOT_CONFIGURED" and not ready.starts


def test_original_authority_withdrawal_refuses_dispatch(ready):
    install(ready)
    row = ClusterLogCollectorOperation.objects.get()
    ready.token.is_revoked = True
    ready.token.save()
    previous = len(ready.starts)
    collector.dispatch_collector(row.pk)
    row.refresh_from_db()
    assert row.status == "refused" and len(ready.starts) == previous and not ready.server.calls


def test_dispatch_uncertain_keeps_original_tuple(ready, monkeypatch):
    def lost(*args, **kw):
        raise RuntimeError("synthetic-provider-body-not-public")

    monkeypatch.setattr("astrolift_workflows.client.start_workflow_once", lost)
    request, result = install(ready)
    assert result["data"]["status"] == "UNCERTAIN" and result["data"]["errorCode"] == "DISPATCH_UNCERTAIN"
    assert "synthetic" not in result["data"]["errorMessage"]
    replay = agent_cases.gql(ready, INSTALL, {"input": request})["data"][
        "astroliftInstallClusterLogCollector"
    ]
    assert replay["data"]["id"] == result["data"]["id"] and ClusterLogCollectorOperation.objects.count() == 1


def test_verified_legacy_account_without_declared_credential_is_not_supported(ready):
    ready.cluster.cloud_account_id = "000000000000"
    ready.cluster.provider_config.pop("account_id")
    ready.cluster.save()
    assert review(ready)["supported"] is False and review(ready)["refusalCode"] == "ACCOUNT_REQUIRED"
    assert not ready.starts and not ready.server.calls


def test_expired_receipt_does_not_advertise_retry(ready, monkeypatch):
    from django.utils import timezone

    from astrolift_clusters.schema.log_collector import operation_to_type

    install(ready)
    row = ClusterLogCollectorOperation.objects.get()
    monkeypatch.setattr(timezone, "now", lambda: row.deadline)
    assert operation_to_type(row).retryable is False
    assert row.status == "queued"


def test_original_request_token_cannot_be_replaced_by_wider_token(ready):
    from django.test import Client

    from astrolift_identity.api_tokens import mint_token
    from astrolift_identity.models import ApiToken

    request, result = install(ready)
    issued = mint_token()
    ApiToken.objects.create(
        user=ready.actor,
        organization=ready.org,
        name="Replacement",
        token_hash=issued.token_hash,
        scopes=["admin"],
    )
    ready.client = Client(HTTP_AUTHORIZATION=f"Bearer {issued.plaintext}")
    refusal = agent_cases.gql(ready, INSTALL, {"input": request})["data"][
        "astroliftInstallClusterLogCollector"
    ]
    assert refusal["errors"][0]["code"] == "REQUEST_CHANGED"
    assert ClusterLogCollectorOperation.objects.count() == 1 and len(ready.starts) == 1


def test_outer_transaction_rollback_discards_request_and_enqueue(ready):
    from django.db import transaction

    from astrolift_clusters import agent_install

    install(ready)
    original = ClusterLogCollectorOperation.objects.get()
    original.status = "refused"
    original.save()
    before = len(ready.starts)
    with agent_install._actor(original):
        reviewed = collector.review_collector(str(ready.cluster.guid))
        with pytest.raises(RuntimeError), transaction.atomic():
            collector.reserve_collector(
                cluster_id=str(ready.cluster.guid),
                request_id=str(uuid4()),
                expected_version=ready.cluster.version,
                expected_source=reviewed["source"],
            )
            raise RuntimeError("Rollback actual original request")
    assert ClusterLogCollectorOperation.objects.count() == 1 and len(ready.starts) == before
