"""Custom domains report their edge-auth state instead of hiding it (#1621).

#1539 put the app's managed subdomain behind the cluster's central auth
host. The custom-domain Ingresses rendered by the same function got
nothing, so on a gated cluster ``app.<org>.<base-zone>`` asked for a login
and ``app.customer.com`` served the identical backend to anyone.

The fix is deliberately NOT to stamp the same annotations, and these tests
pin that. The auth host's session cookie is scoped to the parent zone of
its own hostname, and the response that sets it comes from
``auth.<base-zone>``; a response from one registrable domain cannot set a
cookie for an unrelated one. So on ``customer.com`` the auth sub-request
would see no session on every request, redirect, come back with still no
cookie, and loop. Stamping would turn "no gate" into "infinite redirect".

What lands instead is the state, on the Ingress as a label and on the API
for the UI, so the gap is something an operator reads rather than
discovers by opening the URL.
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
ALB_CONFIG = {
    "user_pool_arn": "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_abc",
    "user_pool_client_id": "client-abc",
    "user_pool_domain": "acme-auth",
}

EDGE_AUTH_LABEL = "astrolift.dev/edge-auth"
AUTH_ANNOTATION_PREFIX = "nginx.ingress.kubernetes.io/auth-"
ALB_AUTH_ANNOTATION_PREFIX = "alb.ingress.kubernetes.io/auth-"


def _make_manifest():
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
def external_domain(app):
    """A genuinely external domain — the case the gap is about."""
    from astrolift_lifecycle.models import CustomDomain

    return CustomDomain.objects.create(
        registered_app=app,
        hostname="checkout.customer.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        is_active=True,
    )


def _custom_domain_ingresses(deployment):
    out = _render_app_ingresses_and_tls(deployment.pk, "ns", _make_manifest())
    return [
        r
        for r in out
        if r.get("kind") == "Ingress" and "astrolift.dev/custom-domain" in r["metadata"].get("labels", {})
    ]


# ---- the predicate ---------------------------------------------------


class TestEdgeAuthState:
    def test_no_cluster_is_no_gate(self):
        assert custom_domain_edge_auth_state(None, "customer.com") == EDGE_AUTH_NO_GATE

    def test_cluster_without_a_gate_is_no_gate(self, cluster):
        cluster.ingress_class = "nginx"
        cluster.oidc_auth_config = None
        assert custom_domain_edge_auth_state(cluster, "customer.com") == EDGE_AUTH_NO_GATE

    def test_external_domain_on_a_gated_cluster_is_ungated(self, cluster):
        cluster.ingress_class = "nginx"
        cluster.oidc_auth_config = OIDC_CONFIG
        assert custom_domain_edge_auth_state(cluster, "checkout.customer.com") == EDGE_AUTH_UNGATED

    def test_domain_inside_the_cookie_scope_is_gated(self, cluster):
        """A "custom" domain that happens to sit under the auth host's
        parent zone IS covered — the cookie reaches it."""
        cluster.ingress_class = "nginx"
        cluster.oidc_auth_config = OIDC_CONFIG
        assert custom_domain_edge_auth_state(cluster, "shop.apps.example.net") == EDGE_AUTH_GATED

    def test_the_cookie_scope_zone_itself_is_gated(self, cluster):
        cluster.ingress_class = "nginx"
        cluster.oidc_auth_config = OIDC_CONFIG
        assert custom_domain_edge_auth_state(cluster, "apps.example.net") == EDGE_AUTH_GATED

    def test_suffix_lookalike_is_not_gated(self, cluster):
        """``notexample.net`` must not match ``.apps.example.net``, and
        ``evil-apps.example.net`` must not either — a bare ``endswith`` on
        the undotted zone would pass both."""
        cluster.ingress_class = "nginx"
        cluster.oidc_auth_config = OIDC_CONFIG
        assert custom_domain_edge_auth_state(cluster, "evil-apps.example.net") == EDGE_AUTH_UNGATED

    def test_trailing_dot_and_case_do_not_change_the_answer(self, cluster):
        cluster.ingress_class = "nginx"
        cluster.oidc_auth_config = OIDC_CONFIG
        assert custom_domain_edge_auth_state(cluster, "Shop.APPS.Example.NET.") == EDGE_AUTH_GATED

    def test_auth_host_with_no_parent_zone_gates_nothing(self, cluster):
        """``cookie_scope_for`` returns "" for a host with fewer than three
        labels and the flag is left off, so nothing is covered."""
        cluster.ingress_class = "nginx"
        cluster.oidc_auth_config = {**OIDC_CONFIG, "auth_proxy_host": "auth.local"}
        assert custom_domain_edge_auth_state(cluster, "auth.local") == EDGE_AUTH_UNGATED

    def test_alb_with_cognito_is_ungated_for_custom_domains(self, cluster):
        """Cookie scope is not the ALB obstacle — an exact Cognito callback
        registration per domain is. Until one exists, it is ungated."""
        cluster.ingress_class = "alb"
        cluster.alb_auth_config = ALB_CONFIG
        assert custom_domain_edge_auth_state(cluster, "checkout.customer.com") == EDGE_AUTH_UNGATED

    def test_alb_without_cognito_is_no_gate(self, cluster):
        cluster.ingress_class = "alb"
        cluster.alb_auth_config = None
        assert custom_domain_edge_auth_state(cluster, "checkout.customer.com") == EDGE_AUTH_NO_GATE

    def test_partial_alb_config_counts_as_no_gate(self, cluster):
        """Truthiness, not presence — same rule as cognito_auth_for_cluster."""
        cluster.ingress_class = "alb"
        cluster.alb_auth_config = {**ALB_CONFIG, "user_pool_arn": ""}
        assert custom_domain_edge_auth_state(cluster, "checkout.customer.com") == EDGE_AUTH_NO_GATE


# ---- the renderer ----------------------------------------------------


class TestRendererRecordsTheState:
    def test_custom_domain_ingress_is_labelled_ungated_on_a_gated_cluster(
        self,
        deployment,
        cluster,
        external_domain,
    ):
        cluster.ingress_class = "nginx"
        cluster.oidc_auth_config = OIDC_CONFIG
        cluster.save()

        ingresses = _custom_domain_ingresses(deployment)
        assert ingresses, "expected a custom-domain Ingress"
        for ingress in ingresses:
            assert ingress["metadata"]["labels"][EDGE_AUTH_LABEL] == EDGE_AUTH_UNGATED

    def test_custom_domain_ingress_is_labelled_no_gate_on_an_open_cluster(
        self,
        deployment,
        cluster,
        external_domain,
    ):
        cluster.ingress_class = "nginx"
        cluster.oidc_auth_config = None
        cluster.save()

        for ingress in _custom_domain_ingresses(deployment):
            assert ingress["metadata"]["labels"][EDGE_AUTH_LABEL] == EDGE_AUTH_NO_GATE

    def test_custom_domain_carries_no_auth_annotations(
        self,
        deployment,
        cluster,
        external_domain,
    ):
        """The anti-regression, and the point of the whole issue.

        Stamping the managed-subdomain annotations onto an external host
        is the obvious-looking fix and it produces an infinite redirect,
        because the session cookie is never presented on that host. If a
        later change adds them here, this fails.
        """
        cluster.ingress_class = "nginx"
        cluster.oidc_auth_config = OIDC_CONFIG
        cluster.save()

        for ingress in _custom_domain_ingresses(deployment):
            annotations = ingress["metadata"].get("annotations", {})
            offenders = [k for k in annotations if k.startswith(AUTH_ANNOTATION_PREFIX)]
            assert not offenders, f"custom domain must not carry {offenders}"

    def test_alb_custom_domain_carries_no_cognito_annotations(
        self,
        deployment,
        cluster,
        external_domain,
    ):
        cluster.ingress_class = "alb"
        cluster.alb_auth_config = ALB_CONFIG
        cluster.save()

        for ingress in _custom_domain_ingresses(deployment):
            annotations = ingress["metadata"].get("annotations", {})
            offenders = [k for k in annotations if k.startswith(ALB_AUTH_ANNOTATION_PREFIX)]
            assert not offenders, f"custom domain must not carry {offenders}"

    def test_the_managed_subdomain_is_still_gated(self, deployment, cluster, external_domain):
        """Guard on #1539: recording the custom-domain gap must not have
        cost the managed subdomain its gate."""
        cluster.ingress_class = "nginx"
        cluster.oidc_auth_config = OIDC_CONFIG
        cluster.save()

        from astrolift_clusters.models import ManagedDomain

        env = deployment.app_environment
        env.managed_domain = ManagedDomain.objects.create(
            organization=deployment.registered_app.organization,
            zone="apps.example.net",
            dns_driver="route53",
            dns_config={},
        )
        env.save()

        out = _render_app_ingresses_and_tls(deployment.pk, "ns", _make_manifest())
        managed = [
            r
            for r in out
            if r.get("kind") == "Ingress"
            and r["metadata"].get("labels", {}).get("astrolift.dev/managed-subdomain") == "true"
        ]
        assert managed, "expected a managed-subdomain Ingress"
        for ingress in managed:
            annotations = ingress["metadata"]["annotations"]
            assert annotations["nginx.ingress.kubernetes.io/auth-url"].startswith("https://")


# ---- the API surface -------------------------------------------------


class TestApiSurface:
    def test_domain_type_reports_the_state(self, cluster, external_domain):
        from astrolift_lifecycle.schema.types import app_domain_to_type

        cluster.ingress_class = "nginx"
        cluster.oidc_auth_config = OIDC_CONFIG

        out = app_domain_to_type(external_domain, cluster=cluster)
        assert out.edge_auth_state == EDGE_AUTH_UNGATED

    def test_domain_type_defaults_to_no_gate_without_a_cluster(self, external_domain):
        """A caller that could not establish the cluster reports the
        conservative answer rather than guessing "gated"."""
        from astrolift_lifecycle.schema.types import app_domain_to_type

        assert app_domain_to_type(external_domain).edge_auth_state == EDGE_AUTH_NO_GATE
