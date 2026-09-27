"""An ACM certificate does not stop cert-manager on the nginx edge (#2055).

Every AWS-provisioned managed domain records its wildcard ACM ARN in
``dns_config["certificate_arn"]``. The ALB path presents it. The nginx path
took any recorded certificate as "provided", rendered no cert-manager issuer,
and named a TLS Secret nothing creates. Behind the edge's TLS-passthrough NLB
nginx then answers with its fake default certificate: every app moved off
ALB would break at the TLS handshake. An ACM ARN is not something nginx can
present, so both managed-subdomain renderers let cert-manager issue instead.
"""

from __future__ import annotations

import pytest

from astrolift_workflows.activities.app_lifecycle import _render_app_ingresses_and_tls
from core.app_deploy import _render_managed_subdomain_ingress, nginx_serves_domain_cert

pytestmark = pytest.mark.django_db

ACM_ARN = "arn:aws:acm:us-west-2:111122223333:certificate/0f0e0d0c-aaaa-bbbb-cccc-000000002055"
ISSUER = "cert-manager.io/cluster-issuer"


@pytest.mark.parametrize(
    ("cert", "served"),
    [
        (ACM_ARN, False),
        ("arn:aws-us-gov:acm:us-gov-west-1:111122223333:certificate/abc", False),
        ("arn:aws-cn:acm:cn-north-1:111122223333:certificate/abc", False),
        ("projects/p/locations/global/certificates/wildcard", True),
        ("", False),
        (None, False),
    ],
)
def test_which_domain_certificates_nginx_can_present(cert, served):
    assert nginx_serves_domain_cert(cert) is served


def _make_manifest():
    from astrolift_manifest.types import ContainerManifest, NormalizedManifest, WorkloadManifest

    return NormalizedManifest(
        name="hello-app",
        workloads=(
            WorkloadManifest(
                name="web",
                kind="deployment",
                replicas=1,
                is_public=True,
                containers=(ContainerManifest(name="web", port=8080, is_primary=True),),
            ),
        ),
        managed_services=(),
        defaults_applied=(),
        serialized={},
    )


def _deployment(org, env, cluster, certificate_arn):
    from astrolift_clusters.models import ManagedDomain
    from astrolift_lifecycle.models import Deployment

    domain = ManagedDomain.objects.create(
        organization=org,
        zone="apps.example.net",
        dns_driver="route53",
        dns_config={"zone_id": "Z123", "certificate_arn": certificate_arn},
    )
    env.managed_domain = domain
    env.save()
    cluster.ingress_class = "nginx"
    cluster.save()
    return Deployment.objects.create(
        registered_app=env.registered_app,
        app_environment=env,
        triggered_by_user_id=None,
        trigger_kind="manual",
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
    )


def _deploy_path(deployment, cluster):
    return _render_managed_subdomain_ingress(
        deployment, _make_manifest(), "ns", deployment.app_environment.managed_domain, cluster
    )


def _workflow_path(deployment):
    return [
        r
        for r in _render_app_ingresses_and_tls(deployment.pk, "ns", _make_manifest())
        if r.get("kind") == "Ingress"
        and r["metadata"].get("labels", {}).get("astrolift.dev/managed-subdomain") == "true"
    ]


def test_an_acm_domain_gets_a_cert_manager_certificate_on_both_paths(org, env, cluster):
    deployment = _deployment(org, env, cluster, ACM_ARN)

    for ingresses in (_deploy_path(deployment, cluster), _workflow_path(deployment)):
        assert ingresses
        for ingress in ingresses:
            assert ingress["metadata"]["annotations"][ISSUER] == "letsencrypt-prod"
            assert ingress["spec"]["tls"][0]["secretName"]


def test_a_certificate_nginx_can_present_is_still_used(org, env, cluster):
    """Unchanged for a non-ACM certificate: rendered as provided, no issuer."""
    deployment = _deployment(org, env, cluster, "projects/p/locations/global/certificates/wildcard")

    for ingresses in (_deploy_path(deployment, cluster), _workflow_path(deployment)):
        assert ingresses
        for ingress in ingresses:
            assert ISSUER not in ingress["metadata"]["annotations"]


def test_the_alb_path_still_presents_the_acm_certificate(org, env, cluster):
    deployment = _deployment(org, env, cluster, ACM_ARN)
    cluster.ingress_class = "alb"
    cluster.save()

    ingresses = _deploy_path(deployment, cluster)
    assert ingresses
    for ingress in ingresses:
        assert ingress["metadata"]["annotations"]["alb.ingress.kubernetes.io/certificate-arn"] == ACM_ARN
