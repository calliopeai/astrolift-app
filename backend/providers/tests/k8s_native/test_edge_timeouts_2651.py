"""The edge never cuts a response that is still sending (#2651).

Without a timeout of its own, an app route took Envoy's default of 15 s for
the whole response, streams included, and the ALB in front idled out at
60 s: AI answers, SSE streams and slow uploads were cut mid-response. Every
rule that reaches an app's Service now names its request timeout (none by
default); silence is bounded instead, at five minutes, by the listener's
stream idle timeout and by the ALB's idle timeout, which must agree across
every Ingress in an ALB group.
"""

from __future__ import annotations

import logging
import re

import pytest

from k8s_native.edge_gateway import (
    ALB_ATTRIBUTES_ANNOTATION,
    EDGE_IDLE_TIMEOUT_SECONDS,
    REQUEST_TIMEOUT_ENV,
    alb_attributes,
    alb_front,
    alb_host_ingress,
    edge_component,
    render_app_routes,
    render_custom_domain_routes,
    request_timeout,
)

COGNITO = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc/.well-known/openid-configuration",
    "client_id": "central-client-id",
    "auth_proxy_host": "auth.astro.example.net",
    "custom_domain_alb_group": "verified-edge",
    "gateway_secret": "gateway-proof",
}
BRAIN = {
    "brain_audience": "01a0-brain-guid",
    "brain_issuer": "https://astrolift.example.net",
    "brain_jwks_uri": "https://astrolift.example.net/api/brains/v1/jwks.json",
}
# Gateway API's Duration (GEP-2257), as the HTTPRoute CRD validates it.
GATEWAY_DURATION = re.compile(r"^([0-9]{1,5}(h|m|s|ms)){1,4}$")
IDLE = f"idle_timeout.timeout_seconds={EDGE_IDLE_TIMEOUT_SECONDS}"


@pytest.fixture(autouse=True)
def _no_timeout_setting(monkeypatch):
    monkeypatch.delenv(REQUEST_TIMEOUT_ENV, raising=False)


def _of(manifests, kind):
    return [m for m in manifests if m["kind"] == kind]


def _routes(**over):
    kwargs = dict(
        app_slug="studio",
        namespace="conflict-studio",
        workloads={"web": (["studio.astro.example.net"], 3000), "api": (["api.astro.example.net"], 8080)},
        gated=True,
        paused=False,
        gateway_secret="s3cret",
        brain=BRAIN,
    )
    kwargs.update(over)
    return render_app_routes(**kwargs)


def _service_rules(manifests):
    return [rule for route in _of(manifests, "HTTPRoute") for rule in route["spec"]["rules"] if "backendRefs" in rule]


def test_every_rule_that_reaches_a_service_has_no_request_cap_by_default():
    for gated in (True, False):
        out = _routes(gated=gated)
        rules = _service_rules(out)
        # Both workloads' app routes, and on a gated edge each brain /mcp route too.
        assert len(rules) == (4 if gated else 2)
        for rule in rules:
            assert rule["timeouts"] == {"request": "0s"}, rule


def test_the_brain_mcp_route_is_cut_no_sooner_than_the_app_route():
    routes = {r["metadata"]["name"]: r for r in _of(_routes(), "HTTPRoute")}
    app = routes["conflict-studio-web"]["spec"]["rules"][0]
    mcp = routes["conflict-studio-web-mcp"]["spec"]["rules"][0]
    assert mcp["matches"] == [{"path": {"type": "PathPrefix", "value": "/mcp"}}]
    assert mcp["timeouts"] == app["timeouts"] == {"request": "0s"}


def test_a_paused_route_answers_at_once_and_carries_no_timeout():
    [route] = _of(_routes(workloads={"web": (["studio.astro.example.net"], 3000)}, paused=True), "HTTPRoute")
    [rule] = route["spec"]["rules"]
    assert "backendRefs" not in rule
    assert "timeouts" not in rule


def test_a_custom_domain_route_carries_the_same_timeout():
    for gated in (True, False):
        out = render_custom_domain_routes(
            app_slug="studio",
            namespace="conflict-studio",
            hostname="studio.customer.example",
            service="web",
            port=3000,
            paused=False,
            gated=gated,
            config=COGNITO,
            access={"groups": ["staff"], "users": []},
            certificate_arn="arn:aws:acm:us-west-2:123:certificate/custom",
            alb_group="verified-edge",
            platform_namespace="astrolift-system",
        )
        [rule] = _service_rules(out)
        assert rule["timeouts"] == {"request": "0s"}
        [front] = _of(out, "Ingress")
        assert front["metadata"]["annotations"][ALB_ATTRIBUTES_ANNOTATION] == IDLE


@pytest.mark.parametrize(("raw", "rendered"), [("300", "300s"), (" 900 ", "900s"), ("0", "0s"), ("99999", "99999s")])
def test_an_operator_may_cap_requests_in_whole_seconds(monkeypatch, raw, rendered):
    monkeypatch.setenv(REQUEST_TIMEOUT_ENV, raw)
    assert request_timeout() == rendered
    for rule in _service_rules(_routes()):
        assert rule["timeouts"] == {"request": rendered}


@pytest.mark.parametrize("raw", ["-1", "1.5", "5m", "300s", "abc", "100000"])
def test_a_value_the_api_server_would_refuse_renders_the_default_and_says_so(monkeypatch, caplog, raw):
    monkeypatch.setenv(REQUEST_TIMEOUT_ENV, raw)
    with caplog.at_level(logging.WARNING, logger="k8s_native.edge_gateway"):
        assert request_timeout() == "0s"
    assert REQUEST_TIMEOUT_ENV in caplog.text
    assert repr(raw) in caplog.text


def test_an_empty_setting_is_the_default_without_a_warning(monkeypatch, caplog):
    monkeypatch.setenv(REQUEST_TIMEOUT_ENV, "")
    with caplog.at_level(logging.WARNING, logger="k8s_native.edge_gateway"):
        assert request_timeout() == "0s"
    assert not caplog.records


def test_every_timeout_the_render_can_emit_is_a_gateway_api_duration():
    for raw in ("", "0", "1", "300", "99999", "100000", "-5", "junk"):
        assert GATEWAY_DURATION.match(request_timeout({REQUEST_TIMEOUT_ENV: raw})), raw
    assert GATEWAY_DURATION.match(f"{EDGE_IDLE_TIMEOUT_SECONDS}s")


def test_the_listener_bounds_silence_and_still_drops_impersonation_headers():
    component = edge_component(COGNITO, ingress_class="envoy")
    [ctp] = _of(component.post_install_manifests, "ClientTrafficPolicy")
    assert ctp["spec"]["timeout"] == {"http": {"streamIdleTimeout": "300s"}}
    assert "X-Astrolift-Id-Token" in ctp["spec"]["headers"]["earlyRequestHeaders"]["remove"]
    assert ctp["spec"]["clientIPDetection"] == {"xForwardedFor": {"numTrustedHops": 1}}


def test_the_edge_alb_and_each_apps_rule_idle_out_at_five_minutes_not_one():
    [front] = alb_front(COGNITO, namespace="astrolift-system", cluster_name="conflict-astrolift")
    assert front["metadata"]["annotations"][ALB_ATTRIBUTES_ANNOTATION] == "idle_timeout.timeout_seconds=300"
    rule = alb_host_ingress(
        app_slug="studio",
        namespace="conflict-studio",
        hostnames=["studio.astro.example.net"],
        platform_namespace="astrolift-system",
        group_annotations={"alb.ingress.kubernetes.io/group.name": "astrolift-conflict"},
    )
    annotations = rule["metadata"]["annotations"]
    assert annotations[ALB_ATTRIBUTES_ANNOTATION] == "idle_timeout.timeout_seconds=300"
    assert annotations["alb.ingress.kubernetes.io/group.name"] == "astrolift-conflict"


def test_the_alb_and_the_listener_agree_on_how_long_silence_lasts():
    [ctp] = _of(edge_component(COGNITO, ingress_class="envoy").post_install_manifests, "ClientTrafficPolicy")
    [front] = alb_front(COGNITO, namespace="astrolift-system")
    listener = int(ctp["spec"]["timeout"]["http"]["streamIdleTimeout"].removesuffix("s"))
    alb = int(front["metadata"]["annotations"][ALB_ATTRIBUTES_ANNOTATION].split("=")[1])
    assert listener == alb == EDGE_IDLE_TIMEOUT_SECONDS == 300


def test_attributes_already_set_are_kept_and_a_different_idle_timeout_is_replaced():
    assert alb_attributes() == IDLE
    assert alb_attributes("routing.http2.enabled=false") == f"routing.http2.enabled=false,{IDLE}"
    assert (
        alb_attributes(" idle_timeout.timeout_seconds=60 , deletion_protection.enabled=true ,")
        == f"deletion_protection.enabled=true,{IDLE}"
    )
    rule = alb_host_ingress(
        app_slug="studio",
        namespace="conflict-studio",
        hostnames=["studio.astro.example.net"],
        platform_namespace="astrolift-system",
        group_annotations={
            "alb.ingress.kubernetes.io/group.name": "astrolift-conflict",
            ALB_ATTRIBUTES_ANNOTATION: "idle_timeout.timeout_seconds=60,access_logs.s3.enabled=false",
        },
    )
    assert rule["metadata"]["annotations"][ALB_ATTRIBUTES_ANNOTATION] == f"access_logs.s3.enabled=false,{IDLE}"


def test_every_ingress_in_one_group_carries_the_same_idle_timeout():
    """The AWS Load Balancer Controller refuses a group whose Ingresses disagree on an attribute."""
    group = {"alb.ingress.kubernetes.io/group.name": "verified-edge"}
    fronts = [
        *alb_front(COGNITO, namespace="astrolift-system"),
        alb_host_ingress(
            app_slug="a",
            namespace="org-a",
            hostnames=["a.astro.example.net"],
            platform_namespace="astrolift-system",
            group_annotations=group,
        ),
        *_of(
            render_custom_domain_routes(
                app_slug="b",
                namespace="org-b",
                hostname="b.customer.example",
                service="web",
                port=80,
                paused=False,
                gated=False,
                config=COGNITO,
                access={},
                certificate_arn="arn:aws:acm:us-west-2:123:certificate/b",
                alb_group="verified-edge",
                platform_namespace="astrolift-system",
            ),
            "Ingress",
        ),
    ]
    assert {f["metadata"]["annotations"]["alb.ingress.kubernetes.io/group.name"] for f in fronts} == {"verified-edge"}
    assert {f["metadata"]["annotations"][ALB_ATTRIBUTES_ANNOTATION] for f in fronts} == {IDLE}
