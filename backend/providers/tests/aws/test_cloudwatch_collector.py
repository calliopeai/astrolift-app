"""Real boto3/Moto infrastructure lifecycle, not live ingestion acceptance."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import boto3
import pytest
import yaml
from moto import mock_aws

from _sdk.cloud_credentials import CloudCredential
from _sdk.cluster import ClusterContext
from aws._cloudwatch_collector import PIN, PROFILE
from aws.cloudwatch_collector import POLICY_NAME, CollectorPreparationError, CollectorSpec, prepare_collector
from aws.cluster_eks import EKSClusterDriver, EKSConfig
from aws.session import clear_credential_cache

GUID = "8b2e4a0c-b67b-4f98-b086-cf09cbb21f9c"
ACCOUNT = "123456789012"
REGION = "us-west-2"
NAME = "fixture-cluster"
ROLE = f"astrolift-{GUID}-fluent-bit"
GROUP = f"/astrolift/clusters/{GUID}/pods"
TAGS = {
    "astrolift.io/managed-by": "platform",
    "astrolift.io/cluster": GUID,
    "astrolift.io/component": "fluent-bit-cloudwatch",
}


class RecordingClient:
    def __init__(self, client, name, calls, failure=None):
        self.client, self.name, self.calls, self.failure = client, name, calls, failure

    def __getattr__(self, method):
        target = getattr(self.client, method)
        if not callable(target):
            return target

        def invoke(**kwargs):
            self.calls.append((self.name, method, kwargs))
            if self.failure and self.failure(self.name, method):
                raise RuntimeError("upstream-body-marker")
            return target(**kwargs)

        return invoke


@pytest.fixture
def fixture():
    with mock_aws():
        clear_credential_cache()
        clients = {name: boto3.client(name, region_name=REGION) for name in ("eks", "sts", "iam", "logs", "ec2")}
        cluster = clients["eks"].create_cluster(
            name=NAME,
            version="1.33",
            roleArn=f"arn:aws:iam::{ACCOUNT}:role/eks",
            resourcesVpcConfig={"subnetIds": ["subnet-fixture"]},
        )["cluster"]
        issuer = cluster["identity"]["oidc"]["issuer"]
        clients["iam"].create_open_id_connect_provider(
            Url=issuer, ClientIDList=["sts.amazonaws.com"], ThumbprintList=["a" * 40]
        )
        calls = []
        failure = [None]

        def factory(name, *, region, credential):
            assert region == REGION
            assert credential.declared_account == ACCOUNT
            return RecordingClient(clients[name], name, calls, lambda n, m: failure[0] == (n, m))

        spec = CollectorSpec(GUID, NAME, REGION, CloudCredential(cloud="aws", declared_account=ACCOUNT))
        yield spec, clients, calls, failure, factory
        clear_credential_cache()


def mutations(calls):
    return [
        (service, method)
        for service, method, _ in calls
        if method.startswith(("create_", "put_", "update_", "delete_", "tag_"))
    ]


def test_prepares_exact_owned_identity_and_never_claims_installation(fixture):
    spec, clients, calls, _, factory = fixture
    stage = prepare_collector(spec, client_factory=factory)
    assert stage.infrastructure_prepared and not stage.collector_installed and not stage.ingestion_verified
    assert stage.cluster_guid == GUID and stage.log_group == GROUP
    role = clients["iam"].get_role(RoleName=ROLE)["Role"]
    assert role["Path"] == "/astrolift/"
    assert {t["Key"]: t["Value"] for t in role["Tags"]} == TAGS
    [trust] = role["AssumeRolePolicyDocument"]["Statement"]
    assert trust["Principal"] == {"Federated": f"arn:aws:iam::{ACCOUNT}:oidc-provider/{stage.oidc_issuer}"}
    assert trust["Condition"]["StringEquals"] == {
        stage.oidc_issuer + ":sub": "system:serviceaccount:astrolift-system:fluent-bit",
        stage.oidc_issuer + ":aud": "sts.amazonaws.com",
    }
    policy = clients["iam"].get_role_policy(RoleName=ROLE, PolicyName=POLICY_NAME)["PolicyDocument"]
    assert policy["Statement"] == [
        {"Effect": "Allow", "Action": ["logs:DescribeLogStreams"], "Resource": stage.log_group_arn + ":*"},
        {
            "Effect": "Allow",
            "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": stage.log_group_arn + ":log-stream:*",
        },
    ]
    assert clients["logs"].describe_log_groups(logGroupNamePrefix=GROUP)["logGroups"][0]["retentionInDays"] == 30
    assert stage.reader_policy()["Statement"] == [
        {"Effect": "Allow", "Action": ["logs:FilterLogEvents"], "Resource": stage.log_group_arn + ":*"}
    ]
    assert "role_arn" not in stage.candidate_reader_config()["log_config"]
    assert stage.component().default_enabled is False
    assert "Fargate" in stage.component().rationale
    assert set(mutations(calls)) == {
        ("logs", "create_log_group"),
        ("logs", "put_retention_policy"),
        ("iam", "create_role"),
        ("iam", "put_role_policy"),
    }


def test_repeat_does_not_rewrite_owned_group_or_role(fixture):
    spec, _, calls, _, factory = fixture
    first = prepare_collector(spec, client_factory=factory)
    calls.clear()
    assert prepare_collector(spec, client_factory=factory) == first
    assert mutations(calls) == []


@pytest.mark.parametrize(
    "failure_point", [("logs", "put_retention_policy"), ("iam", "create_role"), ("iam", "put_role_policy")]
)
def test_partial_creation_failure_is_truthful_and_retry_reuses_identity(fixture, failure_point):
    spec, _, calls, failure, factory = fixture
    failure[0] = failure_point
    with pytest.raises(CollectorPreparationError, match="infrastructure preparation failed") as caught:
        prepare_collector(spec, client_factory=factory)
    assert "upstream-body-marker" not in str(caught.value)
    failure[0] = None
    calls.clear()
    stage = prepare_collector(spec, client_factory=factory)
    assert stage.log_group == GROUP and not stage.collector_installed
    assert ("logs", "create_log_group") not in mutations(calls)
    calls.clear()
    prepare_collector(spec, client_factory=factory)
    assert mutations(calls) == []


@pytest.mark.parametrize("target", ["role", "group"])
def test_unowned_collision_refuses_before_any_writes(fixture, target):
    spec, clients, calls, _, factory = fixture
    if target == "role":
        clients["iam"].create_role(
            RoleName=ROLE,
            Path="/astrolift/",
            AssumeRolePolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": []}),
        )
    else:
        clients["logs"].create_log_group(logGroupName=GROUP, tags={"astrolift.io/cluster": "foreign"})
    with pytest.raises(CollectorPreparationError, match="not owned"):
        prepare_collector(spec, client_factory=factory)
    assert mutations(calls) == []


def test_unrelated_owned_role_policy_preserved_and_refused(fixture):
    spec, clients, calls, _, factory = fixture
    prepare_collector(spec, client_factory=factory)
    extra = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Action": "s3:ListAllMyBuckets", "Resource": "*"}],
    }
    clients["iam"].put_role_policy(RoleName=ROLE, PolicyName="unrelated", PolicyDocument=json.dumps(extra))
    calls.clear()
    with pytest.raises(CollectorPreparationError, match="unrelated policies"):
        prepare_collector(spec, client_factory=factory)
    assert mutations(calls) == []
    assert clients["iam"].get_role_policy(RoleName=ROLE, PolicyName="unrelated")["PolicyDocument"] == extra


def test_boundary_is_preserved_and_a_changed_boundary_is_refused(fixture):
    spec, clients, calls, _, factory = fixture
    policy = clients["iam"].create_policy(
        PolicyName="fixture-boundary",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [{"Effect": "Allow", "Action": "logs:*", "Resource": "*"}],
            }
        ),
    )["Policy"]["Arn"]
    bound = replace(spec, permissions_boundary_arn=policy)
    prepare_collector(bound, client_factory=factory)
    assert clients["iam"].get_role(RoleName=ROLE)["Role"]["PermissionsBoundary"]["PermissionsBoundaryArn"] == policy
    calls.clear()
    prepare_collector(bound, client_factory=factory)
    assert mutations(calls) == []
    with pytest.raises(CollectorPreparationError, match="boundary is mismatched"):
        prepare_collector(spec, client_factory=factory)
    assert mutations(calls) == []


def test_missing_registered_oidc_provider_refuses_without_writes(fixture):
    spec, clients, calls, _, factory = fixture
    [provider] = clients["iam"].list_open_id_connect_providers()["OpenIDConnectProviderList"]
    clients["iam"].delete_open_id_connect_provider(OpenIDConnectProviderArn=provider["Arn"])
    with pytest.raises(CollectorPreparationError):
        prepare_collector(spec, client_factory=factory)
    assert mutations(calls) == []


@pytest.mark.parametrize(
    "change",
    [
        {"cluster_guid": "not-a-uuid"},
        {"cluster_name": "*"},
        {"region": "*"},
        {"retention_days": 2},
        {"credential": CloudCredential(cloud="gcp")},
        {"credential": CloudCredential(cloud="aws")},
        {"credential": CloudCredential(cloud="aws", mode="unsupported", declared_account=ACCOUNT)},
    ],
)
def test_bad_spec_refuses_before_client_construction(fixture, change):
    spec, _, calls, _, _ = fixture
    factory = MagicMock(side_effect=AssertionError("no client construction"))
    with pytest.raises(CollectorPreparationError):
        prepare_collector(replace(spec, **change), client_factory=factory)
    factory.assert_not_called()
    assert calls == []


def test_wrong_account_or_cluster_refuses_without_mutation(fixture):
    spec, _, calls, _, factory = fixture
    with pytest.raises(CollectorPreparationError):
        prepare_collector(replace(spec, cluster_name="missing-cluster"), client_factory=factory)
    assert mutations(calls) == []


def test_current_catalogue_does_not_expose_unadmitted_collector(fixture):
    _spec, clients, _, _, _ = fixture
    driver = EKSClusterDriver(
        config=EKSConfig(region=REGION, cluster_name=NAME),
        eks_client=clients["eks"],
        sts_client=clients["sts"],
        ec2_client=clients["ec2"],
    )
    ctx = ClusterContext(slug="fixture", auth_method="exec_plugin", auth_config={"cluster_name": NAME})
    assert "fluent-bit-cloudwatch" not in {c.key for c in driver.bootstrap_components(ctx)}


def test_frozen_profile_hash_and_native_upstream_render(fixture):
    spec, _, _, _, factory = fixture
    assert (
        hashlib.sha256((PROFILE / "values-aws.yaml").read_bytes()).hexdigest()
        == "a3e7e5e2ac13154a16ea7f278cdf78e928a04a8821e8787d5e24f3e8b779d11b"
    )
    stage = prepare_collector(spec, client_factory=factory)
    with tempfile.TemporaryDirectory(prefix="collector-helm-") as scratch:
        subprocess.run(
            [
                "helm",
                "pull",
                PIN["name"],
                "--repo",
                PIN["repository"],
                "--version",
                PIN["version"],
                "--destination",
                scratch,
                "--repository-cache",
                scratch,
                "--repository-config",
                str(Path(scratch) / "repositories.yaml"),
            ],
            check=True,
            capture_output=True,
        )
        archive = Path(scratch) / f"{PIN['name']}-{PIN['version']}.tgz"
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == PIN["archiveSha256"]
        values = Path(scratch) / "values.yaml"
        values.write_text(yaml.safe_dump(stage.component().helm_values))
        result = subprocess.run(
            ["helm", "template", "fluent-bit", str(archive), "-n", "astrolift-system", "-f", str(values)],
            check=True,
            capture_output=True,
            text=True,
        )
        manifests = [r for r in yaml.safe_load_all(result.stdout) if r]
        [account] = [r for r in manifests if r["kind"] == "ServiceAccount"]
        assert account["metadata"]["annotations"]["eks.amazonaws.com/role-arn"] == stage.irsa_role_arn
        [config] = [r for r in manifests if r["kind"] == "ConfigMap"]
        raw = config["data"]["fluent-bit.conf"]
        assert "Merge_Log Off" in raw and "Keep_Log On" in raw and "K8S-Logging.Parser Off" in raw
        [daemon] = [r for r in manifests if r["kind"] == "DaemonSet"]
        assert daemon["spec"]["template"]["spec"]["serviceAccountName"] == "fluent-bit"
