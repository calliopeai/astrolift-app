"""Tests for auto-instrumentation hooks (#13)."""

from __future__ import annotations

import pytest

from _sdk.instrumentation import (
    OTEL_COLLECTOR_SIDECAR,
    VECTOR_SIDECAR,
    IngressTracingConfig,
    ServiceMeshConfig,
    SidecarSpec,
    annotate_for_mesh,
    annotate_ingress_tracing,
    compose_instrumentation,
    inject_sidecar,
)


def _deployment(name: str = "api", existing_containers: int = 1) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name},
        "spec": {
            "template": {
                "metadata": {},
                "spec": {
                    "containers": [
                        {"name": f"main-{i}", "image": "app:1"}
                        for i in range(existing_containers)
                    ],
                },
            },
        },
    }


def _ingress(name: str = "api") -> dict:
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {"name": name},
        "spec": {},
    }


def test_inject_sidecar_appends_container() -> None:
    out = inject_sidecar(
        [_deployment()], sidecar=OTEL_COLLECTOR_SIDECAR,
    )
    containers = (
        out[0]["spec"]["template"]["spec"]["containers"]
    )
    assert len(containers) == 2
    assert containers[1]["name"] == "otel-collector"


def test_inject_sidecar_idempotent() -> None:
    """Re-applying the same sidecar must not duplicate it."""
    once = inject_sidecar(
        [_deployment()], sidecar=OTEL_COLLECTOR_SIDECAR,
    )
    twice = inject_sidecar(once, sidecar=OTEL_COLLECTOR_SIDECAR)
    containers = (
        twice[0]["spec"]["template"]["spec"]["containers"]
    )
    assert len(containers) == 2  # NOT 3


def test_inject_sidecar_skips_non_workload_kinds() -> None:
    service = {"kind": "Service", "metadata": {"name": "x"}}
    out = inject_sidecar(
        [service], sidecar=OTEL_COLLECTOR_SIDECAR,
    )
    # Service unchanged
    assert out[0] == service


def test_inject_sidecar_does_not_mutate_input() -> None:
    deployment = _deployment()
    inject_sidecar([deployment], sidecar=OTEL_COLLECTOR_SIDECAR)
    # Original input has only the original container
    assert len(
        deployment["spec"]["template"]["spec"]["containers"]
    ) == 1


def test_vector_sidecar_uses_vector_image() -> None:
    out = inject_sidecar([_deployment()], sidecar=VECTOR_SIDECAR)
    sidecar_container = (
        out[0]["spec"]["template"]["spec"]["containers"][1]
    )
    assert sidecar_container["image"].startswith("timberio/vector")


def test_istio_mesh_annotation() -> None:
    out = annotate_for_mesh(
        [_deployment()],
        config=ServiceMeshConfig(mesh="istio"),
    )
    annos = (
        out[0]["spec"]["template"]["metadata"]["annotations"]
    )
    assert annos["sidecar.istio.io/inject"] == "true"


def test_linkerd_mesh_annotation() -> None:
    out = annotate_for_mesh(
        [_deployment()],
        config=ServiceMeshConfig(mesh="linkerd"),
    )
    annos = (
        out[0]["spec"]["template"]["metadata"]["annotations"]
    )
    assert annos["linkerd.io/inject"] == "enabled"


def test_consul_mesh_annotation() -> None:
    out = annotate_for_mesh(
        [_deployment()],
        config=ServiceMeshConfig(mesh="consul"),
    )
    annos = (
        out[0]["spec"]["template"]["metadata"]["annotations"]
    )
    assert annos["consul.hashicorp.com/connect-inject"] == "true"


def test_unknown_mesh_raises() -> None:
    with pytest.raises(ValueError, match="unknown service mesh"):
        annotate_for_mesh(
            [_deployment()],
            config=ServiceMeshConfig(mesh="not-a-mesh"),
        )


def test_mesh_disabled_emits_disabled_value() -> None:
    out = annotate_for_mesh(
        [_deployment()],
        config=ServiceMeshConfig(
            mesh="istio", automatic_injection=False,
        ),
    )
    annos = (
        out[0]["spec"]["template"]["metadata"]["annotations"]
    )
    assert annos["sidecar.istio.io/inject"] == "false"


def test_existing_mesh_annotation_not_overwritten() -> None:
    """If the workload already declares a mesh annotation (e.g.
    user explicitly opted out), respect that."""
    deployment = _deployment()
    deployment["spec"]["template"]["metadata"]["annotations"] = {
        "sidecar.istio.io/inject": "false",
    }
    out = annotate_for_mesh(
        [deployment],
        config=ServiceMeshConfig(mesh="istio", automatic_injection=True),
    )
    annos = (
        out[0]["spec"]["template"]["metadata"]["annotations"]
    )
    assert annos["sidecar.istio.io/inject"] == "false"  # preserved


def test_ingress_tracing_annotations() -> None:
    out = annotate_ingress_tracing(
        [_ingress()],
        config=IngressTracingConfig(),
    )
    annos = out[0]["metadata"]["annotations"]
    assert annos["astrolift.io/trace-propagation-format"] == "w3c"
    assert annos["astrolift.io/trace-sample-rate"] == "1.0"
    assert (
        annos["nginx.ingress.kubernetes.io/enable-opentracing"]
        == "true"
    )


def test_ingress_tracing_b3_format() -> None:
    out = annotate_ingress_tracing(
        [_ingress()],
        config=IngressTracingConfig(propagation_format="b3"),
    )
    annos = out[0]["metadata"]["annotations"]
    assert annos["astrolift.io/trace-propagation-format"] == "b3"


def test_compose_applies_all_hooks() -> None:
    out = compose_instrumentation(
        [_deployment(), _ingress()],
        sidecar=OTEL_COLLECTOR_SIDECAR,
        mesh=ServiceMeshConfig(mesh="istio"),
        ingress_tracing=IngressTracingConfig(),
    )
    deployment = next(m for m in out if m["kind"] == "Deployment")
    assert len(
        deployment["spec"]["template"]["spec"]["containers"]
    ) == 2
    assert (
        deployment["spec"]["template"]["metadata"]["annotations"][
            "sidecar.istio.io/inject"
        ] == "true"
    )
    ingress = next(m for m in out if m["kind"] == "Ingress")
    assert (
        "astrolift.io/trace-propagation-format"
        in ingress["metadata"]["annotations"]
    )


def test_compose_with_no_hooks_passes_through() -> None:
    deployment = _deployment()
    out = compose_instrumentation([deployment])
    assert out == [deployment]


def test_sidecar_spec_carries_resources() -> None:
    out = inject_sidecar(
        [_deployment()], sidecar=OTEL_COLLECTOR_SIDECAR,
    )
    sidecar_container = (
        out[0]["spec"]["template"]["spec"]["containers"][1]
    )
    assert sidecar_container["resources"]["requests"]["cpu"] == "50m"
    assert sidecar_container["resources"]["limits"]["memory"] == "512Mi"
