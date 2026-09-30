"""Live app/environment owners select domains before provider reads or writes."""

from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_lifecycle.models import CustomDomain, Deployment
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.activities.app_lifecycle import _render_app_ingresses_and_tls
from core.app_deploy import (
    AppDeployError,
    custom_domain_edge_auth_state,
    envoy_custom_domain_routes,
    namespace_for_environment,
)
from core.edge_access import record_environment, set_app_access

pytestmark = pytest.mark.django_db

CONFIG = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc",
    "client_id": "client",
    "auth_proxy_host": "auth.apps.example.net",
    "custom_domain_alb_group": "verified-edge",
}


@pytest.fixture
def deployment(app, env, cluster):
    plugin = cluster.provider_plugin
    plugin.slug = "aws"
    plugin.save()
    cluster.ingress_class = "envoy"
    cluster.oidc_auth_config = CONFIG
    cluster.save()
    return Deployment.objects.create(
        registered_app=app, app_environment=env, status="pending", trigger_kind="manual"
    )


@pytest.fixture
def manifest():
    from astrolift_manifest.types import ContainerManifest, NormalizedManifest, WorkloadManifest

    return NormalizedManifest(
        name="app",
        workloads=(
            WorkloadManifest(
                name="web",
                kind="deployment",
                is_public=True,
                replicas=1,
                containers=(ContainerManifest(name="web", port=8080, is_primary=True),),
            ),
        ),
        managed_services=(),
        defaults_applied=(),
        serialized={},
    )


@pytest.fixture
def domain(app):
    return CustomDomain.objects.create(
        registered_app=app,
        hostname="customer.example",
        validation_status="validated",
        certificate_state="active",
        certificate_id="arn:aws:acm:us-west-2:123:certificate/custom",
    )


@pytest.fixture
def driver(monkeypatch):
    calls = []
    fake = SimpleNamespace(validate_edge_custom_domain=lambda ctx, **kwargs: calls.append(kwargs))
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: fake)
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda cluster: SimpleNamespace(slug=cluster.slug)
    )
    return fake, calls


def render(deployment, manifest):
    env = deployment.app_environment
    return envoy_custom_domain_routes(
        deployment, manifest, namespace=namespace_for_environment(env), cluster=env.tenant_cluster
    )


def test_default_custom_domain_is_public_even_inside_central_cookie_zone(
    deployment, manifest, domain, driver
):
    domain.hostname = "custom.apps.example.net"
    domain.save()
    result = render(deployment, manifest)
    assert not any(item["kind"] == "SecurityPolicy" for item in result)
    assert driver[1][0]["gated"] is False
    assert (
        custom_domain_edge_auth_state(deployment.app_environment.tenant_cluster, domain.hostname) == "ungated"
    )


def test_workflow_and_apply_render_same_custom_policy_and_tls_front(deployment, manifest, domain, driver):
    domain.edge_auth_enabled = True
    domain.save()
    direct = render(deployment, manifest)
    workflow = _render_app_ingresses_and_tls(
        deployment.pk, namespace_for_environment(deployment.app_environment), manifest
    )
    assert direct == workflow
    assert any(item["kind"] == "SecurityPolicy" for item in direct)
    assert not any(
        "nginx.ingress.kubernetes.io/auth-url" in item.get("metadata", {}).get("annotations", {})
        for item in direct
    )
    assert all(call["hostname"] == "customer.example" and call["gated"] for call in driver[1])


def test_domain_collection_excludes_sibling_deleted_pending_and_inactive(
    deployment, manifest, domain, driver, app
):
    sibling = RegisteredApp.objects.create(
        organization=app.organization, project=app.project, team=app.team, name="Sibling", slug="sibling"
    )
    for host, fields in [
        ("sibling.example", {"registered_app": sibling}),
        ("deleted.example", {"deleted_at": timezone.now()}),
        ("pending.example", {"validation_status": "pending"}),
        ("inactive.example", {"is_active": False}),
        ("no-cert.example", {"certificate_state": "issuing"}),
    ]:
        CustomDomain.objects.create(
            **(
                {
                    "registered_app": app,
                    "hostname": host,
                    "validation_status": "validated",
                    "certificate_state": "active",
                    "certificate_id": "arn:cert",
                }
                | fields
            )
        )
    render(deployment, manifest)
    assert [call["hostname"] for call in driver[1]] == [domain.hostname]


def test_preview_never_duplicates_custom_domain_on_same_cluster(deployment, manifest, domain, driver):
    env = deployment.app_environment
    env.k8s_namespace = "preview-own"
    env.save()
    assert render(deployment, manifest) == []
    assert driver[1] == []


@pytest.mark.parametrize("mismatch", ["app", "cluster", "organization", "namespace"])
def test_incoherent_owner_refuses_before_provider_reads(deployment, manifest, domain, driver, mismatch, app):
    env = deployment.app_environment
    cluster = env.tenant_cluster
    namespace = namespace_for_environment(env)
    if mismatch == "app":
        env.registered_app_id = app.pk + 1000
    if mismatch == "cluster":
        env.tenant_cluster_id = cluster.pk + 1000
    if mismatch == "organization":
        cluster.organization_id = app.organization_id + 1000
    if mismatch == "namespace":
        namespace = "sibling-namespace"
    with pytest.raises(AppDeployError, match="incoherent|another organization|does not match"):
        envoy_custom_domain_routes(deployment, manifest, namespace=namespace, cluster=cluster)
    assert driver[1] == []


@pytest.mark.parametrize(
    "failure",
    [ValueError("callback not registered"), RuntimeError("provider echoed a private client secret")],
)
def test_callback_or_provider_failure_returns_no_manifest_set(deployment, manifest, domain, driver, failure):
    domain.edge_auth_enabled = True
    domain.save()

    def fail(*a, **k):
        raise failure

    driver[0].validate_edge_custom_domain = fail
    with pytest.raises(AppDeployError) as exc:
        render(deployment, manifest)
    assert "private client secret" not in str(exc.value)


def test_missing_front_configuration_refuses_before_driver_reads(deployment, manifest, domain, driver):
    deployment.app_environment.tenant_cluster.oidc_auth_config = {
        k: v for k, v in CONFIG.items() if k != "custom_domain_alb_group"
    }
    with pytest.raises(AppDeployError, match="install a custom_domain_alb_group"):
        render(deployment, manifest)
    assert driver[1] == []


def test_opted_in_domain_requires_an_actual_gate(deployment, manifest, domain, driver):
    domain.edge_auth_enabled = True
    domain.save()
    deployment.app_environment.tenant_cluster.oidc_auth_config = {"custom_domain_alb_group": "verified-edge"}
    with pytest.raises(AppDeployError, match="no OIDC gate"):
        render(deployment, manifest)
    assert driver[1] == []


def test_custom_access_refresh_and_teardown_preserve_independent_policy(
    deployment, manifest, domain, driver, monkeypatch
):
    starts = []
    monkeypatch.setattr("astrolift_workflows.client.start_workflow", lambda *a, **k: starts.append(k))
    domain.edge_auth_enabled = True
    domain.save()
    app = deployment.registered_app
    cluster = deployment.app_environment.tenant_cluster
    namespace = namespace_for_environment(deployment.app_environment)
    manifests = render(deployment, manifest)
    assert record_environment(cluster, app, namespace, manifests)
    entry = next(iter(cluster.edge_access_rules.values()))
    assert entry["hosts"] == []
    assert entry["custom_routes"][0]["hostname"] == domain.hostname
    set_app_access(app, groups=["staff"], users=[], source="ui")
    cluster.refresh_from_db()
    assert next(iter(cluster.edge_access_rules.values()))["groups"] == ["staff"]
    set_app_access(app, groups=[], users=[], source="ui")
    cluster.refresh_from_db()
    assert next(iter(cluster.edge_access_rules.values()))["custom_routes"]
    assert next(iter(cluster.edge_access_rules.values()))["groups"] == []
    assert record_environment(cluster, app, namespace, [])
    assert cluster.edge_access_rules == {}
    assert len(starts) == 4


@pytest.mark.parametrize("readiness", ["ready", "stale-policy", "stale-route", "guarded", "no-front"])
def test_legacy_custom_ingress_stays_until_current_route_policy_and_front_ready(
    deployment, manifest, domain, driver, readiness
):
    import copy

    from astrolift_workflows.tests.test_envoy_edge_deploy_2055 import PROGRAMMED, _Api
    from core.app_deploy import prune_edge_leftovers

    domain.edge_auth_enabled = True
    domain.save()
    rendered = render(deployment, manifest)
    route = copy.deepcopy(next(item for item in rendered if item["kind"] == "HTTPRoute"))
    policy = copy.deepcopy(next(item for item in rendered if item["kind"] == "SecurityPolicy"))
    name = route["metadata"]["name"]
    namespace = namespace_for_environment(deployment.app_environment)
    route["metadata"]["generation"] = 2
    route["status"] = {
        "parents": [
            {
                "parentRef": {"name": "edge", "namespace": "astrolift-edge"},
                "controllerName": "gateway.envoyproxy.io/gatewayclass-controller",
                "conditions": [
                    {"type": kind, "status": "True", "observedGeneration": 2}
                    for kind in ("Accepted", "ResolvedRefs")
                ],
            }
        ]
    }
    policy["metadata"]["generation"] = 3
    policy["status"] = {
        "ancestors": [
            {
                "ancestorRef": {"name": "edge", "namespace": "astrolift-edge"},
                "controllerName": "gateway.envoyproxy.io/gatewayclass-controller",
                "conditions": [{"type": "Accepted", "status": "True", "observedGeneration": 3}],
            }
        ]
    }
    front = {"status": {"loadBalancer": {"ingress": [{"hostname": "edge.elb.amazonaws.com"}]}}}
    if readiness == "stale-policy":
        policy["status"]["ancestors"][0]["conditions"][0]["observedGeneration"] = 2
    if readiness == "stale-route":
        route["status"]["parents"][0]["conditions"][0]["observedGeneration"] = 1
    if readiness == "guarded":
        route["spec"]["rules"][0]["filters"].append(
            {"type": "ExtensionRef", "extensionRef": {"name": "pending"}}
        )
    if readiness == "no-front":
        front = {}
    api = _Api(
        existing={
            **PROGRAMMED,
            ("astrolift-edge", "gateway.networking.k8s.io/v1/HTTPRoute", name): route,
            ("astrolift-edge", "gateway.envoyproxy.io/v1alpha1/SecurityPolicy", name): policy,
            ("astrolift-system", "networking.k8s.io/v1/Ingress", name): front,
        },
        listings={
            (namespace, "networking.k8s.io/v1/Ingress"): [
                {
                    "metadata": {
                        "name": "legacy-custom",
                        "labels": {
                            "astrolift.dev/app": deployment.registered_app.slug,
                            "astrolift.dev/custom-domain": domain.hostname,
                        },
                    }
                },
                {
                    "metadata": {
                        "name": "sibling-custom",
                        "labels": {
                            "astrolift.dev/app": "sibling",
                            "astrolift.dev/custom-domain": domain.hostname,
                        },
                    }
                },
            ]
        },
    )
    removed = prune_edge_leftovers(
        api, "c", app_slug=deployment.registered_app.slug, namespace=namespace, rendered=rendered
    )
    assert removed == (["Ingress/legacy-custom"] if readiness == "ready" else [])
    assert all("sibling-custom" not in names for _, names in api.deleted)


def test_teardown_prunes_only_environment_owned_custom_policies_and_grants():
    from astrolift_workflows.tests.test_envoy_edge_deploy_2055 import _Api
    from core.app_deploy import prune_edge_leftovers

    ours = {"astrolift.dev/namespace": "org-app"}
    sibling = {"astrolift.dev/namespace": "sibling-app"}
    listings = {}
    for namespace, kind in [
        ("astrolift-edge", "gateway.envoyproxy.io/v1alpha1/SecurityPolicy"),
        ("astrolift-edge", "gateway.envoyproxy.io/v1alpha1/BackendTrafficPolicy"),
        ("org-app", "gateway.networking.k8s.io/v1beta1/ReferenceGrant"),
    ]:
        listings[(namespace, kind)] = [
            {"metadata": {"name": "own", "labels": ours}},
            {"metadata": {"name": "sibling", "labels": sibling}},
            {"metadata": {"name": "shared", "labels": {}}},
        ]
    api = _Api(listings=listings)
    removed = prune_edge_leftovers(api, "c", app_slug="app", namespace="org-app", rendered=[])
    assert sorted(removed) == ["BackendTrafficPolicy/own", "ReferenceGrant/own", "SecurityPolicy/own"]


def test_managed_only_app_can_render_for_another_target_cluster(deployment, manifest, driver):
    import copy

    target = copy.copy(deployment.app_environment.tenant_cluster)
    target.pk += 1000
    assert envoy_custom_domain_routes(deployment, manifest, namespace="managed-target", cluster=target) == []
    assert driver[1] == []


@pytest.mark.parametrize("shape", ["wildcard", "path", "redirect"])
def test_unsupported_custom_shapes_refuse_before_provider_reads(deployment, manifest, domain, driver, shape):
    from astrolift_lifecycle.models import DomainPathRoute, DomainRedirectRule

    if shape == "wildcard":
        domain.is_wildcard = True
        domain.save()
    elif shape == "path":
        DomainPathRoute.objects.create(
            custom_domain=domain, path_prefix="/api", target_workload_slug="web", target_port=8080
        )
    else:
        DomainRedirectRule.objects.create(custom_domain=domain, kind="apex_to_www")
    with pytest.raises(AppDeployError, match="unsupported|does not support"):
        render(deployment, manifest)
    assert driver[1] == []


def test_long_hostname_stays_in_annotation_and_access_entry_with_valid_short_labels(
    deployment, manifest, domain, driver, monkeypatch
):
    monkeypatch.setattr("core.edge_access.reapply_edge", lambda cluster: True)
    domain.hostname = "a" * 45 + "." + "b" * 45 + ".example"
    domain.edge_auth_enabled = True
    domain.save()
    manifests = render(deployment, manifest)
    assert all(len(item["metadata"]["labels"]["astrolift.dev/custom-domain"]) <= 63 for item in manifests)
    route = next(item for item in manifests if item["kind"] == "HTTPRoute")
    assert route["spec"]["hostnames"] == [domain.hostname]
    cluster = deployment.app_environment.tenant_cluster
    assert record_environment(
        cluster, deployment.registered_app, namespace_for_environment(deployment.app_environment), manifests
    )
    assert next(iter(cluster.edge_access_rules.values()))["custom_routes"][0]["hostname"] == domain.hostname


@pytest.mark.parametrize("error_type", ["CustomDomainAuthPending", "CustomDomainAuthApplyFailed"])
def test_policy_refresh_is_retried_instead_of_recorded_as_success(error_type):
    from _sdk.cluster import ApplyError, ApplyResult
    from temporalio.exceptions import ApplicationError

    from astrolift_workflows.activities.install_prereqs import _apply_post_install_manifests
    from providers.k8s_native.edge_gateway import edge_component

    failure = ApplyResult(
        created=[],
        updated=[],
        unchanged=[],
        errors=[
            ApplyError(
                kind="SecurityPolicy",
                name="custom",
                namespace="astrolift-edge",
                exception_type=error_type,
                exception_message="pending",
                is_retryable=True,
            )
        ],
    )
    fake = SimpleNamespace(apply_manifests=lambda *a, **k: failure)
    component = edge_component(CONFIG, ingress_class="envoy")
    with pytest.raises(ApplicationError) as exc:
        _apply_post_install_manifests(fake, "c", [component], {component.key}, "astrolift-system")
    assert not exc.value.non_retryable
    assert "must retry" in str(exc.value)


def test_pending_custom_policy_does_not_hold_up_shared_edge_apply(deployment, manifest, domain, driver):
    from _sdk.cluster import ApplyError, ApplyResult
    from temporalio.exceptions import ApplicationError

    from astrolift_workflows.activities.install_prereqs import _apply_post_install_manifests
    from providers.k8s_native.edge_gateway import edge_component

    domain.edge_auth_enabled = True
    domain.save()
    custom_policy = next(item for item in render(deployment, manifest) if item["kind"] == "SecurityPolicy")
    entry = {
        "hosts": [],
        "groups": [],
        "users": [],
        "custom_routes": [
            {
                "name": custom_policy["metadata"]["name"],
                "hostname": domain.hostname,
                "labels": custom_policy["metadata"]["labels"],
            }
        ],
    }
    component = edge_component(CONFIG, ingress_class="envoy", access_rules=[entry])
    batches = []

    def apply(cluster, namespace, manifests):
        batches.append(manifests)
        if any(item["metadata"]["labels"].get("astrolift.dev/custom-domain") for item in manifests):
            return ApplyResult(
                created=[],
                updated=[],
                unchanged=[],
                errors=[
                    ApplyError(
                        kind="SecurityPolicy",
                        name="custom",
                        namespace="astrolift-edge",
                        exception_type="CustomDomainAuthPending",
                        exception_message="pending",
                        is_retryable=True,
                    )
                ],
            )
        return ApplyResult(
            created=[item["metadata"]["name"] for item in manifests], updated=[], unchanged=[], errors=[]
        )

    with pytest.raises(ApplicationError):
        _apply_post_install_manifests(
            SimpleNamespace(apply_manifests=apply), "c", [component], {component.key}, "astrolift-system"
        )
    assert len(batches) == 2
    assert any(item["kind"] == "Gateway" for item in batches[0])
    assert any(
        item["kind"] == "SecurityPolicy" and item["metadata"]["name"] == "edge-oidc" for item in batches[0]
    )
    assert all(item["metadata"]["labels"].get("astrolift.dev/custom-domain") for item in batches[1])


def test_custom_renderer_selects_public_service_even_after_private_portless_workload(
    deployment, manifest, domain, driver
):
    from dataclasses import replace

    private = replace(
        manifest.workloads[0],
        name="internal",
        is_public=False,
        containers=(replace(manifest.workloads[0].containers[0], port=0),),
    )
    mixed = replace(manifest, workloads=(private, *manifest.workloads))
    expected = render(deployment, mixed)
    assert (
        _render_app_ingresses_and_tls(
            deployment.pk, namespace_for_environment(deployment.app_environment), mixed
        )
        == expected
    )
    route = next(item for item in expected if item["kind"] == "HTTPRoute")
    assert route["spec"]["rules"][0]["backendRefs"][0]["name"] == "web"
