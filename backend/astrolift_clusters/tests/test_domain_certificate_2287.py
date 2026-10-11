"""The zone's recorded certificate in domain diagnostics (#2287)."""

from uuid import uuid4

import pytest
from _sdk.domain_route import DomainRouteCertificate
from django.utils import timezone

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, TenantCluster
from astrolift_clusters.tests import test_domain_diagnostics_2287 as diagnostics

pytestmark = pytest.mark.django_db(transaction=True)

ACCOUNT = "123456789012"
ARN = f"arn:aws:acm:us-west-2:{ACCOUNT}:certificate/0b8e7c1d-2f3a-4b5c-9d6e-7f8091a2b3c4"
QUERY = diagnostics.DIAGNOSTICS.replace(
    "routesTruncated", "routesTruncated certificate{state reason status validationRecords}"
)


@pytest.fixture
def world(monkeypatch):
    w = diagnostics.world.__wrapped__(monkeypatch)
    w.user.is_superuser = True
    w.user.save()
    return w


@pytest.fixture
def dns_wire(monkeypatch):
    yield from diagnostics.dns_wire.__wrapped__(monkeypatch)


def aws_cluster(world, *, verified=True):
    if not ProviderPlugin.objects.filter(slug="aws").exists():
        ProviderPlugin.objects.bulk_create(
            [
                ProviderPlugin(
                    name="AWS", slug="aws", plugin_version="0.0.1", capabilities_manifest={}, config_schema={}
                )
            ]
        )
    suffix = uuid4().hex[:8]
    cluster = TenantCluster.objects.create(
        organization=world.org,
        name=f"aws-{suffix}",
        slug=f"aws-{suffix}",
        provider_plugin=ProviderPlugin.objects.get(slug="aws"),
        provider_config={},
        region="us-west-2",
        cloud_account_id=ACCOUNT,
        cloud_account_verified_at=timezone.now() if verified else None,
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    ManagedDomain.objects.filter(pk=world.domain.pk).update(
        provision_cert_id=ARN, dns_config={"provision_cluster_id": cluster.pk}
    )
    world.domain.refresh_from_db()
    return cluster


@pytest.fixture
def acm(monkeypatch):
    state = {
        "calls": [],
        "answer": DomainRouteCertificate(ARN, "ISSUED", (("_a.example.test", "_b.acm-validations.aws"),)),
    }

    class Dns:
        def observe_reviewed_domain_certificate(self, **kwargs):
            state["calls"].append(kwargs)
            if isinstance(state["answer"], Exception):
                raise state["answer"]
            return state["answer"]

    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, capability: Dns())
    return state


def certificate(client, world, dns_wire):
    diagnostics.dns_answers(dns_wire)
    reply = diagnostics.ask(client, world, QUERY)
    assert not reply.get("errors"), reply.get("errors")
    return reply["data"]["astroliftManagedDomainDiagnostics"]["certificate"]


def test_an_issued_certificate_is_read_with_the_clusters_pinned_account(world, client, dns_wire, acm):
    aws_cluster(world)

    cert = certificate(client, world, dns_wire)

    assert cert == {"state": "OK", "reason": "CERTIFICATE_ISSUED", "status": "ISSUED", "validationRecords": 1}
    [call] = acm["calls"]
    assert (call["zone"], call["hostname"], call["arn"], call["expected_account_id"]) == (
        "example.test",
        "example.test",
        ARN,
        ACCOUNT,
    )


@pytest.mark.parametrize(
    "answer, expected",
    [
        (
            DomainRouteCertificate(ARN, "PENDING_VALIDATION", ()),
            ("UNKNOWN", "CERTIFICATE_PENDING_VALIDATION"),
        ),
        (DomainRouteCertificate(ARN, "EXPIRED", ()), ("ERROR", "CERTIFICATE_EXPIRED")),
        (ValueError("NATIVE_CERTIFICATE_HOST_MISMATCH"), ("ERROR", "NATIVE_CERTIFICATE_HOST_MISMATCH")),
        (RuntimeError("private provider body"), ("ERROR", "CERTIFICATE_UNAVAILABLE")),
    ],
)
def test_pending_failed_and_unreadable_certificates_stay_distinct(
    world, client, dns_wire, acm, answer, expected
):
    aws_cluster(world)
    acm["answer"] = answer

    cert = certificate(client, world, dns_wire)

    assert (cert["state"], cert["reason"]) == expected


def test_no_recorded_certificate_unverified_cluster_and_tenants_never_reach_the_provider(
    world, client, dns_wire, acm
):
    assert certificate(client, world, dns_wire)["reason"] == "CERTIFICATE_NOT_RECORDED"

    aws_cluster(world, verified=False)
    assert certificate(client, world, dns_wire)["reason"] == "CERTIFICATE_CLUSTER_UNAVAILABLE"

    aws_cluster(world)
    world.user.is_superuser = False
    world.user.save()
    assert certificate(client, world, dns_wire)["reason"] == "PLATFORM_OPERATOR_REQUIRED"
    assert acm["calls"] == []
