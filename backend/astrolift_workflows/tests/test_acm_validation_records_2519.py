"""A managed domain's certificate gets its DNS validation records (#2519).

``Route53Driver.request_wildcard_cert`` read a new certificate once, a second
after requesting it, before ACM had published its validation records. On
CONFLICT, ``conflict.sh`` recorded none, wrote none, and sat at
``configure_cert_policy`` with every poll answering ``pending``. The driver now
waits for the records (its own tests), and the poll writes any a pending
certificate still lacks, so a domain stuck that way recovers on its next poll.

A run that moves the row on also clears the step and reason an earlier failed
run recorded: ``astro.conflict.cloud`` reached ``mark_active`` still reporting
``provision_dns_zone`` / ``zone creation failed (ConflictError)``.

Real Postgres; the DNS driver is a recording fake. The Temporal test runs the
real workflow and activities on the time-skipping server.
"""

from __future__ import annotations

import asyncio

import pytest
from asgiref.sync import sync_to_async
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

from astrolift_clusters.models import ManagedDomain
from astrolift_workflows.activities import provision_managed_domain as pmd
from astrolift_workflows.inputs import Actor, ProvisionManagedDomainInput
from astrolift_workflows.workflows.provision_managed_domain import ProvisionManagedDomainWorkflow
from core import install_restrictions as ir

ZONE = "acm2519.example.com"
CERT = "arn:aws:acm:us-west-2:123456789012:certificate/6b1d3a8e-2f4c-4e1a-9c7d-0a5b8e2f1c3d"
NAMESERVERS = ["ns-1.awsdns-01.com", "ns-2.awsdns-02.net"]
# What ACM lists for *.<zone>, <zone> and *.pr.<zone>: the apex and its wildcard share one CNAME.
APEX = {"name": f"_a1.{ZONE}.", "type": "CNAME", "value": "_b1.acm-validations.aws."}
PREVIEW = {"name": f"_c2.pr.{ZONE}.", "type": "CNAME", "value": "_d2.acm-validations.aws."}
WRITTEN = [(f"_a1.{ZONE}", "CNAME", APEX["value"]), (f"_c2.pr.{ZONE}", "CNAME", PREVIEW["value"])]
STALE_FAILURE = {
    "provision_state": "failed",
    "provision_failed_step": "provision_dns_zone",
    "provision_failure_reason": "zone creation failed (ConflictError)",
}


class _Dns:
    """A DNS driver over one ACM certificate.

    The request hands back no validation records, as the driver did before
    #2519. The poll lists both records whatever the status, so the heal's own
    checks are what keep it from writing; with ``issue_once_written`` it
    reports ``issued`` once both are in the zone.
    """

    def __init__(self, *, status: str = "pending", issue_once_written: bool = False) -> None:
        self.status = status
        self.issue_once_written = issue_once_written
        self.written: list[tuple[str, str, str]] = []

    def request_wildcard_cert(self, zone, zone_id, sans=None):
        return {"cert_id": CERT, "validation_records": []}

    def poll_cert_status(self, zone, cert_id):
        assert cert_id == CERT
        issued = self.issue_once_written and len(self.written) == len(WRITTEN)
        status = "issued" if issued else self.status
        return {
            "status": status,
            "cert_arn": cert_id if status == "issued" else None,
            "validation_records": [dict(APEX), dict(PREVIEW)],
        }

    def ensure_record(self, **kwargs):
        self.written.append((kwargs["name"], kwargs["type"], kwargs["value"]))


@pytest.fixture
def dns(monkeypatch, cluster):
    fake = _Dns()
    monkeypatch.setattr(pmd, "_get_cluster_and_dns_driver", lambda cluster_id: (cluster, fake))
    return fake


def _domain(org, **fields) -> ManagedDomain:
    return ManagedDomain.objects.create(
        zone=ZONE, organization=org, dns_driver="route53", is_wildcard_managed=True, **fields
    )


def _row() -> ManagedDomain:
    return ManagedDomain.objects.get(zone=ZONE)


# ---- the poll writes what a pending certificate still lacks ---------------


@pytest.mark.django_db
def test_a_pending_certificate_gets_the_records_its_request_never_wrote(dns, org, cluster):
    """conflict.sh's row: the certificate recorded, no records, none in the zone.
    Written on the first poll and recorded, so later polls write nothing (each
    write is an audit entry)."""
    _domain(org, provision_cert_id=CERT, provision_state="configure_cert_policy")

    statuses = [pmd._poll_cert_issuance_sync(cluster.pk, ZONE, CERT)["status"] for _ in range(3)]

    assert statuses == ["pending"] * 3
    assert dns.written == WRITTEN
    assert _row().provision_validation_records == [APEX, PREVIEW]


@pytest.mark.django_db
def test_only_the_record_a_partial_read_missed_is_written(dns, org, cluster):
    """A read that caught the apex record but not the preview one."""
    _domain(org, provision_cert_id=CERT, provision_validation_records=[dict(APEX)])

    pmd._poll_cert_issuance_sync(cluster.pk, ZONE, CERT)

    assert dns.written == WRITTEN[1:]
    assert _row().provision_validation_records == [APEX, PREVIEW]


def _bound_to_a_domain_route(monkeypatch):
    from astrolift_lifecycle import domain_app_route

    def refuse(**kwargs):
        raise domain_app_route.DomainAppRouteError("PROTECTED_DOMAIN_ROUTE_MANAGE_REQUIRED")

    monkeypatch.setattr(domain_app_route, "require_unbound_zone", refuse)


@pytest.mark.parametrize(
    ("status", "row", "setup"),
    [
        ("issued", {"provision_cert_id": CERT}, None),
        ("failed", {"provision_cert_id": CERT}, None),
        (
            "pending",
            {"provision_cert_id": "arn:aws:acm:us-west-2:123456789012:certificate/an-earlier-one"},
            None,
        ),
        ("pending", {"provision_cert_id": CERT}, _bound_to_a_domain_route),
    ],
    ids=["issued", "failed", "not-the-rows-certificate", "zone-bound-to-a-domain-route"],
)
@pytest.mark.django_db
def test_the_poll_writes_nothing_where_the_request_step_would_not(
    status, row, setup, dns, org, cluster, monkeypatch
):
    """Nothing to heal on a finished certificate; another certificate's records
    are not this row's to write; a reviewed domain route owns its zone."""
    _domain(org, **row)
    dns.status = status
    if setup is not None:
        setup(monkeypatch)

    assert pmd._poll_cert_issuance_sync(cluster.pk, ZONE, CERT)["status"] == status

    assert dns.written == []
    assert _row().provision_validation_records == []


@pytest.mark.django_db
def test_a_poll_without_a_row_still_reports_the_status(dns, cluster):
    assert pmd._poll_cert_issuance_sync(cluster.pk, ZONE, CERT)["status"] == "pending"
    assert dns.written == []


@pytest.mark.django_db
def test_withheld_dns_leaves_the_records_on_the_row_for_the_operator(monkeypatch, org, cluster):
    """As the request step does under installer #447: nothing written, every
    record the certificate waits on recorded for the operator to publish."""
    fake = _Dns()
    monkeypatch.setattr(
        pmd, "_get_cluster_and_dns_driver", lambda cluster_id: (cluster, ir.guard_dns_driver(fake, "aws"))
    )
    monkeypatch.setenv(ir.ENV_VAR, "dns")
    _domain(org, provision_cert_id=CERT)

    pmd._poll_cert_issuance_sync(cluster.pk, ZONE, CERT)

    assert fake.written == []
    assert _row().provision_validation_records == [APEX, PREVIEW]


# ---- a run that moves the row on clears the last run's failure ------------


@pytest.mark.parametrize(
    "step",
    [
        lambda cluster_id: pmd._provision_dns_zone_sync(cluster_id, ZONE),
        lambda cluster_id: pmd._request_wildcard_cert_sync(cluster_id, ZONE, "Z1"),
        lambda cluster_id: pmd._mark_active_sync(cluster_id, ZONE),
    ],
    ids=["provision_dns_zone", "request_wildcard_cert", "mark_active"],
)
@pytest.mark.django_db
def test_a_step_that_moves_the_row_on_clears_the_last_runs_failure(step, dns, org, cluster):
    _domain(org, provision_zone_id="Z1", provision_nameservers=list(NAMESERVERS), **STALE_FAILURE)

    step(cluster.pk)

    row = _row()
    assert row.provision_state != "failed"
    assert (row.provision_failed_step, row.provision_failure_reason) == ("", "")


# ---- a stuck domain recovers through the real workflow ---------------------


@pytest.mark.django_db(transaction=True)
async def test_a_stuck_domain_recovers_on_its_next_poll_and_ends_active(
    temporal_env, monkeypatch, org, cluster
):
    """CONFLICT's two zones in one run: a platform-made zone whose certificate
    request hands back no records (conflict.sh), on a row still carrying an
    earlier run's failure (astro.conflict.cloud)."""
    fake = _Dns(issue_once_written=True)
    monkeypatch.setattr(pmd, "_get_cluster_and_dns_driver", lambda cluster_id: (cluster, fake))
    monkeypatch.setattr(pmd, "_observed_nameservers", lambda zone: (frozenset(NAMESERVERS), ""))
    await sync_to_async(_domain)(
        org, provision_zone_id="Z1", provision_nameservers=list(NAMESERVERS), **STALE_FAILURE
    )

    async with Worker(
        temporal_env.client,
        task_queue="acm-records-2519",
        workflows=[ProvisionManagedDomainWorkflow],
        activities=[
            pmd.provision_dns_zone,
            pmd.delegate_parent_zone,
            pmd.request_wildcard_cert_for_zone,
            pmd.poll_cert_issuance,
            pmd.register_managed_domain_row,
            pmd.validate_ns_delegation,
            pmd.mark_managed_domain_active,
            pmd.mark_managed_domain_provision_failed,
        ],
        workflow_runner=SandboxedWorkflowRunner(),
    ):
        handle = await temporal_env.client.start_workflow(
            ProvisionManagedDomainWorkflow.run,
            ProvisionManagedDomainInput(
                cluster_id=cluster.pk, zone=ZONE, is_platform_managed_zone=True, actor=Actor(kind="system")
            ),
            id="provision-domain-heal-2519",
            task_queue="acm-records-2519",
        )
        result = await asyncio.wait_for(handle.result(), timeout=300)

    assert result.ok is True, result.message
    assert fake.written == WRITTEN
    row = await sync_to_async(_row)()
    assert row.provision_state == "mark_active"
    assert row.provision_cert_id == CERT
    assert row.provision_validation_records == [APEX, PREVIEW]
    assert (row.provision_failed_step, row.provision_failure_reason) == ("", "")
