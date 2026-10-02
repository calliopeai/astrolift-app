"""Native MSK fixture with documented Moto ARN and readiness corrections."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import boto3
from moto import mock_aws
from moto.kafka.models import FakeKafkaCluster
from moto.utilities.utils import get_partition

from aws.managed.event_stream_msk import MSKConfig, MSKProvisionedDriver, MSKServerlessDriver


@contextmanager
def native_msk(monkeypatch, variant):
    def exact_arn(row):
        return (
            f"arn:{get_partition(row.region_name)}:kafka:{row.region_name}:{row.account_id}:"
            f"cluster/{row.cluster_name}/{row.cluster_id}"
        )

    original_init = FakeKafkaCluster.__init__

    def ready_fixture(row, *args, **kwargs):
        original_init(row, *args, **kwargs)
        # Moto has no asynchronous transition; this tests identity, not broker readiness.
        row.state = "ACTIVE"

    monkeypatch.setattr(FakeKafkaCluster, "_generate_arn", exact_arn)
    monkeypatch.setattr(FakeKafkaCluster, "__init__", ready_fixture)
    with mock_aws():
        api = boto3.client("kafka", region_name="us-west-2")
        cfg = MSKConfig(
            region="us-west-2",
            account_id="123456789012",
            subnet_ids=("subnet-a", "subnet-b", "subnet-c"),
            security_group_ids=("sg-fixture",),
            kafka_version_default="3.9.x",
            poll_delay_seconds=0,
            max_poll_attempts=2,
        )
        cls = MSKServerlessDriver if variant == "msk_serverless" else MSKProvisionedDriver
        yield SimpleNamespace(api=api, cfg=cfg, cls=cls, variant=variant)
