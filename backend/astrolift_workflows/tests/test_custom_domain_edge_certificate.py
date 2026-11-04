"""A platform-managed domain's ACM certificate reaches the NLB's TLS listener.

The load balancer controller owns the listener's certificate list and
reconciles it from the ingress controller Service's
``aws-load-balancer-ssl-cert`` annotation (first ARN is the default, the rest
are SNI). The platform adds the domain's ARN to that annotation and never
calls elbv2. The cluster read and patch are stubbed here.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import CustomDomain
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.activities.capability_deprovision import _deprovision_certificate_sync
from astrolift_workflows.activities.custom_domain import (
    _SSL_CERT_ANNOTATION,
    _ensure_edge_certificate_sync,
    _ssl_cert_annotation_with,
)

DEFAULT = "arn:aws:acm:us-west-2:111111111111:certificate/default"
SNI_A = "arn:aws:acm:us-west-2:111111111111:certificate/aaa"
SNI_B = "arn:aws:acm:us-west-2:111111111111:certificate/bbb"


# ---- helper --------------------------------------------------------------


@pytest.mark.parametrize(
    "current,arn,present,expected",
    [
        (DEFAULT, SNI_A, True, f"{DEFAULT},{SNI_A}"),
        (f"{DEFAULT},{SNI_A}", SNI_B, True, f"{DEFAULT},{SNI_A},{SNI_B}"),
        # already present: returned untouched
        (f"{DEFAULT},{SNI_A}", SNI_A, True, f"{DEFAULT},{SNI_A}"),
        # the default is never added again or moved
        (f"{DEFAULT},{SNI_A}", DEFAULT, True, f"{DEFAULT},{SNI_A}"),
        # spaces and empty entries are tolerated, and normalised on a change
        (f" {DEFAULT} , ,{SNI_A},", SNI_B, True, f"{DEFAULT},{SNI_A},{SNI_B}"),
        (f" {DEFAULT} , {SNI_A} ", SNI_A, True, f" {DEFAULT} , {SNI_A} "),
        # a duplicate already in the list is collapsed when the value changes
        (f"{DEFAULT},{SNI_A},{SNI_A}", SNI_B, True, f"{DEFAULT},{SNI_A},{SNI_B}"),
        # removal
        (f"{DEFAULT},{SNI_A},{SNI_B}", SNI_A, False, f"{DEFAULT},{SNI_B}"),
        (f"{DEFAULT},{SNI_A}", SNI_A, False, DEFAULT),
        # removing the default or an absent ARN is a no-op
        (f"{DEFAULT},{SNI_A}", DEFAULT, False, f"{DEFAULT},{SNI_A}"),
        (DEFAULT, SNI_A, False, DEFAULT),
        # no annotation: TLS is not terminated at the load balancer
        (None, SNI_A, True, None),
        ("", SNI_A, True, None),
        (" , ", SNI_A, True, None),
        (None, SNI_A, False, None),
        (DEFAULT, "", True, None),
    ],
)
def test_ssl_cert_annotation_with(current, arn, present, expected):
    assert _ssl_cert_annotation_with(current, arn, present) == expected


# ---- activity ------------------------------------------------------------


class _Cluster:
    """Stub for the aws cluster driver: one Service, one recorded patch list."""

    def __init__(self, annotation, fail=None):
        self.annotation = annotation
        self.fail = fail
        self.patches: list[tuple] = []

    def get_manifest(self, cluster, namespace, kind, name):
        assert (namespace, kind, name) == (
            "astrolift-system",
            "v1/Service",
            "astrolift-ingress-nginx-controller",
        )
        if self.fail == "get":
            raise RuntimeError("forbidden")
        annotations = {} if self.annotation is None else {_SSL_CERT_ANNOTATION: self.annotation}
        return {"metadata": {"annotations": annotations}}

    def patch_service_annotations(self, cluster, namespace, name, annotations):
        if self.fail == "patch":
            raise RuntimeError("services is forbidden")
        self.patches.append((namespace, name, annotations))
        self.annotation = annotations[_SSL_CERT_ANNOTATION]


_DRIVER = "core.cluster_management._driver_for_cluster"


def _domain(slug="aws", **kw):
    org = Organization.objects.create(name="Acme", slug="acme-edge")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-edge")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-edge")
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=slug,
                slug=slug,
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="edge-cluster",
        name="Edge",
        provider_plugin=ProviderPlugin.objects.get(slug=slug),
        endpoint="https://example.invalid",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Web",
        slug="web-edge",
        default_tenant_cluster=cluster,
    )
    fields = {
        "hostname": "shop.example.com",
        "is_active": True,
        "is_platform_managed_zone": True,
        "validation_status": CustomDomain.ValidationStatus.VALIDATED,
        "certificate_id": SNI_A,
        "certificate_state": CustomDomain.CertificateState.ACTIVE,
    }
    fields.update(kw)
    return CustomDomain.objects.create(registered_app=app, **fields)


@pytest.mark.django_db
def test_attaches_an_already_issued_certificate():
    """The state is ACTIVE already, as after a reprovision: the issuance step
    returns early, this step still runs."""
    d = _domain()
    stub = _Cluster(DEFAULT)
    with patch(_DRIVER, return_value=stub):
        result = _ensure_edge_certificate_sync(d.pk)
    assert result["result"] == "attached"
    assert stub.patches == [
        (
            "astrolift-system",
            "astrolift-ingress-nginx-controller",
            {_SSL_CERT_ANNOTATION: f"{DEFAULT},{SNI_A}"},
        )
    ]


@pytest.mark.django_db
def test_already_attached_sends_no_patch():
    d = _domain()
    stub = _Cluster(f"{DEFAULT},{SNI_A}")
    with patch(_DRIVER, return_value=stub):
        result = _ensure_edge_certificate_sync(d.pk)
    assert result["result"] == "unchanged"
    assert stub.patches == []


@pytest.mark.django_db
def test_no_ssl_cert_annotation_is_not_applicable_and_records_nothing():
    d = _domain()
    stub = _Cluster(None)
    with patch(_DRIVER, return_value=stub):
        result = _ensure_edge_certificate_sync(d.pk)
    assert result["result"] == "not_applicable"
    assert stub.patches == []
    d.refresh_from_db()
    assert d.last_certificate_error == ""


@pytest.mark.django_db
@pytest.mark.parametrize("fail", ["get", "patch"])
def test_failure_is_recorded_and_not_raised(fail):
    d = _domain()
    stub = _Cluster(DEFAULT, fail=fail)
    with patch(_DRIVER, return_value=stub):
        result = _ensure_edge_certificate_sync(d.pk)
    assert result["ok"] is False
    d.refresh_from_db()
    assert d.last_certificate_error.startswith("edge certificate attach failed: ")
    assert d.certificate_state == CustomDomain.CertificateState.ACTIVE


@pytest.mark.django_db
def test_success_clears_the_error_it_set():
    d = _domain(last_certificate_error="edge certificate attach failed: boom")
    with patch(_DRIVER, return_value=_Cluster(f"{DEFAULT},{SNI_A}")):
        _ensure_edge_certificate_sync(d.pk)
    d.refresh_from_db()
    assert d.last_certificate_error == ""


@pytest.mark.django_db
def test_success_leaves_an_error_it_did_not_set():
    d = _domain(last_certificate_error="ensure_certificate: throttled")
    with patch(_DRIVER, return_value=_Cluster(DEFAULT)):
        _ensure_edge_certificate_sync(d.pk)
    d.refresh_from_db()
    assert d.last_certificate_error == "ensure_certificate: throttled"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kw,slug",
    [
        ({"is_platform_managed_zone": False}, "aws"),
        ({"certificate_state": CustomDomain.CertificateState.ISSUING}, "aws"),
        ({"certificate_state": CustomDomain.CertificateState.BYO}, "aws"),
        ({"certificate_id": ""}, "aws"),
        ({}, "k8s_native"),
    ],
)
def test_not_attempted_outside_platform_managed_active_aws(kw, slug):
    d = _domain(slug=slug, **kw)
    stub = _Cluster(DEFAULT)
    with patch(_DRIVER, return_value=stub):
        result = _ensure_edge_certificate_sync(d.pk)
    assert result["result"] == "skipped"
    assert stub.patches == []


# ---- removal -------------------------------------------------------------


class _Tls:
    def __init__(self, calls):
        self.calls = calls

    def revoke_certificate(self, cert_id):
        self.calls.append(("revoke", cert_id))


_RESOLVE = "astrolift_workflows.activities.capability_deprovision._resolve_capability_driver"


@pytest.mark.django_db
def test_deprovision_detaches_the_certificate_before_revoking_it():
    d = _domain()
    calls: list[tuple] = []
    stub = _Cluster(f"{DEFAULT},{SNI_A}")
    original = stub.patch_service_annotations
    stub.patch_service_annotations = lambda *a: (calls.append(("detach", a[3])), original(*a))[1]
    with patch(_DRIVER, return_value=stub), patch(_RESOLVE, return_value=_Tls(calls)):
        _deprovision_certificate_sync(custom_domain_id=d.pk)
    assert calls == [("detach", {_SSL_CERT_ANNOTATION: DEFAULT}), ("revoke", SNI_A)]


@pytest.mark.django_db
def test_deprovision_still_revokes_when_the_detach_fails():
    d = _domain()
    calls: list[tuple] = []
    with (
        patch(_DRIVER, return_value=_Cluster(f"{DEFAULT},{SNI_A}", fail="patch")),
        patch(_RESOLVE, return_value=_Tls(calls)),
    ):
        result = _deprovision_certificate_sync(custom_domain_id=d.pk)
    assert calls == [("revoke", SNI_A)]
    assert result["revoked"] is True
