"""A refusal for what the install withholds is never retried (calliope-installer#447).

``WithheldCapabilityError`` is a non-retryable ``ApplicationError``, so an
activity that raises it fails at once: AWS's Deny answers the same on every
attempt. The managed-domain certificate activities used to swallow the
refused validation-record write and leave a certificate that could never
validate; they now let it through. Unset, they write exactly as before.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.api.failure.v1 import Failure
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.converter import DataConverter
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from astrolift_clusters.models import ManagedDomain
from astrolift_workflows.activities import provision_managed_domain as pmd
from core import install_restrictions as ir

ZONE = "withheld447.example"


class _Dns:
    """A DNS driver that records what it was asked to do."""

    def __init__(self) -> None:
        self.cert_requests = 0
        self.written: list[str] = []

    def request_wildcard_cert(self, zone, zone_id, sans=None):
        self.cert_requests += 1
        return {"cert_id": "arn:cert", "validation_records": [{"name": f"_v.{zone}.", "value": "_t.acm."}]}

    def revoke_cert(self, zone, cert_id):
        return None

    def ensure_record(self, **kw):
        self.written.append(kw["name"])

    def provision_zone(self, zone):
        self.written.append(zone)
        return {"zone_id": "Z1", "nameservers": []}


@pytest.fixture
def dns(monkeypatch):
    raw = _Dns()
    # Resolved per call, as driver_for_capability does, so the env var set by
    # each test decides whether the guard wraps it.
    monkeypatch.setattr(
        pmd, "_get_cluster_and_dns_driver", lambda _cid: (None, ir.guard_dns_driver(raw, "aws"))
    )
    monkeypatch.delenv(ir.ENV_VAR, raising=False)
    return raw


def test_the_refusal_is_a_non_retryable_failure_carrying_the_reason():
    exc = ir.WithheldCapabilityError(ir._REASONS["dns"])
    assert isinstance(exc, ApplicationError) and exc.non_retryable
    assert str(exc) == ir._REASONS["dns"]
    failure = Failure()
    converter = DataConverter.default
    converter.failure_converter.to_failure(exc, converter.payload_converter, failure)
    assert failure.application_failure_info.non_retryable
    assert failure.message == ir._REASONS["dns"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "run",
    [
        lambda: pmd._provision_dns_zone_sync(1, ZONE),
        lambda: pmd._request_wildcard_cert_sync(1, ZONE, "Z1"),
        lambda: pmd._reissue_cert_sync(1, ZONE, "arn:old", "Z1"),
    ],
    ids=["provision_zone", "request_cert", "reissue_cert"],
)
def test_managed_domain_activities_refuse_dns_writes(dns, monkeypatch, run):
    ManagedDomain.objects.create(zone=ZONE, dns_driver="route53", is_wildcard_managed=True)
    monkeypatch.setenv(ir.ENV_VAR, "dns")
    with pytest.raises(ir.WithheldCapabilityError, match="DNS is withheld"):
        run()
    assert dns.written == []


@pytest.mark.django_db
def test_unset_writes_the_validation_records_as_before(dns):
    ManagedDomain.objects.create(zone=ZONE, dns_driver="route53", is_wildcard_managed=True)
    pmd._request_wildcard_cert_sync(1, ZONE, "Z1")
    pmd._reissue_cert_sync(1, ZONE, "arn:old", "Z1")
    assert dns.written == [f"_v.{ZONE}", f"_v.{ZONE}"]


@workflow.defn
class _RequestCertWithRetries:
    @workflow.run
    async def run(self, zone: str) -> str:
        return await workflow.execute_activity(
            pmd.request_wildcard_cert_for_zone,
            args=[1, zone, "Z1"],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(initial_interval=timedelta(milliseconds=10), maximum_attempts=5),
        )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_real_activity_is_not_retried_when_dns_is_withheld(temporal_env, dns, monkeypatch):
    monkeypatch.setenv(ir.ENV_VAR, "dns")
    queue = f"withheld-447-{uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[_RequestCertWithRetries],
        activities=[pmd.request_wildcard_cert_for_zone],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        with pytest.raises(WorkflowFailureError) as caught:
            await temporal_env.client.execute_workflow(
                _RequestCertWithRetries.run, ZONE, id=queue, task_queue=queue
            )
    activity_error = caught.value.cause
    assert isinstance(activity_error, ActivityError)
    cause = activity_error.cause
    assert isinstance(cause, ApplicationError) and cause.non_retryable
    assert "DNS is withheld" in cause.message
    # One attempt of five: the certificate was requested once and nothing written.
    assert dns.cert_requests == 1
    assert dns.written == []
