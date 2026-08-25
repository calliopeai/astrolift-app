"""Gating a custom domain for real, per-domain and opt-in (#1631).

#1621 established that an external custom domain cannot be gated by the
central auth host's cookie: that cookie is scoped to the parent zone of
`auth.<base-zone>`, and a response from there cannot set a cookie for an
unrelated registrable domain. Stamping the managed-subdomain annotations
anyway turns "no gate" into an infinite redirect.

This is the actual gating. The domain gets a **first-party** `/oauth2`
endpoint -- `auth-signin` points at its own hostname, not the auth host --
so the cookie the proxy sets is first-party and the loop cannot happen.

Two properties carry the risk, and both have a test named for what breaks:

* **Default off.** Custom domains serve ungated today. Turning the gate on
  is a behaviour change for live traffic, and answering a silent security
  gap with a silent outage is not an improvement.
* **`auth-signin` must point at the domain, never the auth host.** Pointing
  it at the auth host is the obvious-looking fix and reintroduces exactly
  the redirect loop #1621 exists to prevent.
"""

from __future__ import annotations

import pytest

from astrolift_workflows.activities.app_lifecycle import _render_app_ingresses_and_tls
from core.app_deploy import (
    EDGE_AUTH_GATED,
    EDGE_AUTH_NO_GATE,
    EDGE_AUTH_UNGATED,
    custom_domain_edge_auth_state,
)

pytestmark = pytest.mark.django_db

OIDC_CONFIG = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc",
    "client_id": "1qqhi5irhften18gbbpailuo8v",
    "cookie_secret": "signing-key",
    "auth_proxy_host": "auth.apps.example.net",
}
AUTH_SIGNIN = "nginx.ingress.kubernetes.io/auth-signin"
AUTH_URL = "nginx.ingress.kubernetes.io/auth-url"
EDGE_AUTH_LABEL = "astrolift.dev/edge-auth"


def _manifest():
    from astrolift_manifest.types import (
        ContainerManifest,
        NormalizedManifest,
        WorkloadManifest,
    )

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


@pytest.fixture
def deployment(app, env):
    from astrolift_lifecycle.models import Deployment

    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user_id=None,
        trigger_kind="manual",
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
    )


@pytest.fixture
def gated_cluster(cluster):
    cluster.ingress_class = "nginx"
    cluster.oidc_auth_config = OIDC_CONFIG
    cluster.save()
    return cluster


@pytest.fixture
def domain(app):
    from astrolift_lifecycle.models import CustomDomain

    return CustomDomain.objects.create(
        registered_app=app,
        hostname="checkout.customer.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        is_active=True,
    )


def _ingresses(deployment):
    out = _render_app_ingresses_and_tls(deployment.pk, "ns", _manifest())
    return [
        r
        for r in out
        if r.get("kind") == "Ingress" and "astrolift.dev/custom-domain" in r["metadata"].get("labels", {})
    ]


# ---- the predicate ---------------------------------------------------


def test_opting_in_on_a_gated_cluster_is_gated(gated_cluster):
    assert (
        custom_domain_edge_auth_state(gated_cluster, "checkout.customer.com", opted_in=True)
        == EDGE_AUTH_GATED
    )


def test_not_opting_in_is_still_ungated(gated_cluster):
    """The #1621 state, unchanged. A domain nobody opted in stays outside
    the gate and says so."""
    assert custom_domain_edge_auth_state(gated_cluster, "checkout.customer.com") == EDGE_AUTH_UNGATED


def test_opting_in_on_a_cluster_with_no_gate_gates_nothing(cluster):
    """There is nothing to be inside of. Reporting `gated` here would be a
    UI badge promising a login that does not exist."""
    cluster.ingress_class = "nginx"
    cluster.save()

    assert custom_domain_edge_auth_state(cluster, "checkout.customer.com", opted_in=True) == EDGE_AUTH_NO_GATE


def test_an_alb_cluster_is_not_gated_by_opting_in(cluster):
    """ALB gates per listener-rule via authenticate-cognito, which needs an
    exact callback registration for the domain. Opting in does not conjure
    one, so claiming `gated` would be false."""
    cluster.ingress_class = "alb"
    cluster.oidc_auth_config = OIDC_CONFIG
    cluster.save()

    state = custom_domain_edge_auth_state(cluster, "checkout.customer.com", opted_in=True)

    assert state != EDGE_AUTH_GATED


# ---- the render ------------------------------------------------------


def test_an_opted_in_domain_gets_a_first_party_signin(deployment, gated_cluster, domain):
    """The whole point. `auth-signin` on this hostname, not the auth host:
    a response from `auth.<base-zone>` cannot set a cookie for an unrelated
    registrable domain, so pointing there loops forever."""
    domain.edge_auth_enabled = True
    domain.save()

    (ingress,) = _ingresses(deployment)
    annotations = ingress["metadata"]["annotations"]

    assert annotations[AUTH_SIGNIN].startswith("https://checkout.customer.com/oauth2/start")
    assert annotations[AUTH_URL] == "https://checkout.customer.com/oauth2/auth"


def test_the_signin_never_points_at_the_auth_host(deployment, gated_cluster, domain):
    """The regression that reintroduces #1621's infinite redirect."""
    domain.edge_auth_enabled = True
    domain.save()

    (ingress,) = _ingresses(deployment)

    for value in ingress["metadata"]["annotations"].values():
        assert OIDC_CONFIG["auth_proxy_host"] not in value


def test_the_default_is_off(deployment, gated_cluster, domain):
    """Load-bearing. Custom domains serve ungated today, so a default-on
    flag would gate live traffic the moment this migration ran."""
    assert domain.edge_auth_enabled is False

    (ingress,) = _ingresses(deployment)
    annotations = ingress["metadata"].get("annotations", {})

    assert AUTH_SIGNIN not in annotations
    assert AUTH_URL not in annotations


def test_the_label_reports_the_opted_in_state(deployment, gated_cluster, domain):
    """`kubectl get ingress -L astrolift.dev/edge-auth` has to agree with
    what the Ingress actually carries."""
    domain.edge_auth_enabled = True
    domain.save()

    (ingress,) = _ingresses(deployment)

    assert ingress["metadata"]["labels"][EDGE_AUTH_LABEL] == EDGE_AUTH_GATED


def test_opting_in_on_an_ungated_cluster_stamps_nothing(deployment, cluster, domain):
    """Belt and braces: the annotations follow the predicate, not the flag,
    so a domain opted into a cluster with no gate gets no half-configured
    auth-url pointing at an endpoint nothing serves."""
    cluster.ingress_class = "nginx"
    cluster.save()
    domain.edge_auth_enabled = True
    domain.save()

    (ingress,) = _ingresses(deployment)
    annotations = ingress["metadata"].get("annotations", {})

    assert AUTH_SIGNIN not in annotations


def test_the_flag_is_per_domain_not_per_app(app, gated_cluster, deployment):
    """An app routinely carries several hostnames -- a marketing site and an
    admin console are the same app -- and gating is the decision most likely
    to differ between them. A per-app flag would force the public hostname
    behind a login to protect the private one.
    """
    from astrolift_lifecycle.models import CustomDomain

    public = CustomDomain.objects.create(
        registered_app=app,
        hostname="www.customer.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        is_active=True,
        edge_auth_enabled=False,
    )
    CustomDomain.objects.create(
        registered_app=app,
        hostname="admin.customer.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        is_active=True,
        edge_auth_enabled=True,
    )

    by_host = {i["metadata"]["labels"]["astrolift.dev/custom-domain"]: i for i in _ingresses(deployment)}

    assert AUTH_SIGNIN not in by_host[public.hostname]["metadata"].get("annotations", {})
    assert AUTH_SIGNIN in by_host["admin.customer.com"]["metadata"]["annotations"]
