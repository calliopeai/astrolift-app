"""Auto-instrumentation hooks (#13).

Three injection points:
- sidecar: append a sidecar container to a Pod spec (OTEL collector,
  Vector log shipper, otel-instrumentation-injector)
- service_mesh: annotate the Pod spec to opt into mesh injection
  (Istio, Linkerd, Consul Connect)
- ingress_tracing: add tracing-related annotations to the Ingress /
  HTTPRoute (W3C trace context propagation, OTEL trace headers)

Each hook is a manifest mutator: it takes a list of Manifests and
returns a list of Manifests. Hooks compose — the workflow chains
them in a deterministic order so a Pod ends up with both an OTEL
sidecar AND mesh injection annotations when the tenant configures
both.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

Manifest = dict[str, Any]


@dataclass(frozen=True)
class SidecarSpec:
    name: str
    image: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    volume_mounts: tuple[dict[str, str], ...] = ()
    resources: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceMeshConfig:
    mesh: str
    """istio | linkerd | consul. Lowercase."""

    automatic_injection: bool = True
    extra_annotations: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class IngressTracingConfig:
    propagation_format: str = "w3c"
    """w3c | b3 | jaeger. Defaults to W3C trace context (the
    OpenTelemetry-native format)."""

    sample_rate: float = 1.0
    """0.0 - 1.0. Default 1.0 = sample every request; ingress
    tracing is the cheapest place to sample."""


# Pre-configured sidecars for common observability stacks.
OTEL_COLLECTOR_SIDECAR = SidecarSpec(
    name="otel-collector",
    image="otel/opentelemetry-collector-contrib:0.108.0",
    args=("--config=/conf/otel-collector-config.yaml",),
    env={
        "OTEL_RESOURCE_ATTRIBUTES": (
            "k8s.namespace.name=$(K8S_NAMESPACE),k8s.pod.name=$(K8S_POD_NAME),service.name=$(SERVICE_NAME)"
        ),
    },
    volume_mounts=({"name": "otel-config", "mountPath": "/conf"},),
    resources={
        "requests": {"cpu": "50m", "memory": "128Mi"},
        "limits": {"cpu": "200m", "memory": "512Mi"},
    },
)

VECTOR_SIDECAR = SidecarSpec(
    name="vector-agent",
    image="timberio/vector:0.39.0-alpine",
    args=("--config=/etc/vector/vector.yaml",),
    volume_mounts=({"name": "vector-config", "mountPath": "/etc/vector"},),
    resources={
        "requests": {"cpu": "20m", "memory": "64Mi"},
        "limits": {"cpu": "100m", "memory": "256Mi"},
    },
)


def inject_sidecar(
    manifests: list[Manifest],
    *,
    sidecar: SidecarSpec,
    target_kinds: tuple[str, ...] = ("Deployment", "StatefulSet"),
) -> list[Manifest]:
    """Append a sidecar container to every workload manifest.
    Returns a new list — input is not mutated."""
    out: list[Manifest] = []
    for raw in manifests:
        m = deepcopy(raw)
        if m.get("kind") not in target_kinds:
            out.append(m)
            continue
        containers = (
            m.setdefault("spec", {}).setdefault("template", {}).setdefault("spec", {}).setdefault("containers", [])
        )
        if any(c.get("name") == sidecar.name for c in containers):
            out.append(m)  # already injected — idempotent
            continue
        containers.append(
            {
                "name": sidecar.name,
                "image": sidecar.image,
                "args": list(sidecar.args),
                "env": [{"name": k, "value": v} for k, v in sidecar.env.items()],
                "volumeMounts": [dict(v) for v in sidecar.volume_mounts],
                "resources": deepcopy(sidecar.resources),
            }
        )
        out.append(m)
    return out


def annotate_for_mesh(
    manifests: list[Manifest],
    *,
    config: ServiceMeshConfig,
    target_kinds: tuple[str, ...] = (
        "Deployment",
        "StatefulSet",
        "DaemonSet",
    ),
) -> list[Manifest]:
    """Add mesh-injection annotations to the Pod template."""
    annotations = _mesh_annotations(config=config)
    out: list[Manifest] = []
    for raw in manifests:
        m = deepcopy(raw)
        if m.get("kind") not in target_kinds:
            out.append(m)
            continue
        meta = m.setdefault("spec", {}).setdefault("template", {}).setdefault("metadata", {})
        existing = meta.setdefault("annotations", {})
        for key, value in annotations.items():
            existing.setdefault(key, value)
        out.append(m)
    return out


def annotate_ingress_tracing(
    manifests: list[Manifest],
    *,
    config: IngressTracingConfig,
    target_kinds: tuple[str, ...] = ("Ingress", "HTTPRoute"),
) -> list[Manifest]:
    """Add tracing-related annotations to Ingress / HTTPRoute
    manifests so the controller propagates trace context."""
    out: list[Manifest] = []
    for raw in manifests:
        m = deepcopy(raw)
        if m.get("kind") not in target_kinds:
            out.append(m)
            continue
        meta = m.setdefault("metadata", {})
        annos = meta.setdefault("annotations", {})
        annos.setdefault(
            "astrolift.io/trace-propagation-format",
            config.propagation_format,
        )
        annos.setdefault(
            "astrolift.io/trace-sample-rate",
            str(config.sample_rate),
        )
        # nginx-ingress controller-specific
        annos.setdefault(
            "nginx.ingress.kubernetes.io/enable-opentracing",
            "true",
        )
        annos.setdefault(
            "nginx.ingress.kubernetes.io/opentracing-trust-incoming-span",
            "true",
        )
        out.append(m)
    return out


def _mesh_annotations(*, config: ServiceMeshConfig) -> dict[str, str]:
    if config.mesh == "istio":
        return {
            "sidecar.istio.io/inject": ("true" if config.automatic_injection else "false"),
            **config.extra_annotations,
        }
    if config.mesh == "linkerd":
        return {
            "linkerd.io/inject": ("enabled" if config.automatic_injection else "disabled"),
            **config.extra_annotations,
        }
    if config.mesh == "consul":
        return {
            "consul.hashicorp.com/connect-inject": ("true" if config.automatic_injection else "false"),
            **config.extra_annotations,
        }
    raise ValueError(f"unknown service mesh {config.mesh!r}")


def compose_instrumentation(
    manifests: list[Manifest],
    *,
    sidecar: SidecarSpec | None = None,
    mesh: ServiceMeshConfig | None = None,
    ingress_tracing: IngressTracingConfig | None = None,
) -> list[Manifest]:
    """Apply all configured hooks in a deterministic order:
    sidecar → mesh → ingress tracing. The order matters — mesh
    annotations sit on the Pod template that the sidecar already
    landed in."""
    out = list(manifests)
    if sidecar is not None:
        out = inject_sidecar(out, sidecar=sidecar)
    if mesh is not None:
        out = annotate_for_mesh(out, config=mesh)
    if ingress_tracing is not None:
        out = annotate_ingress_tracing(out, config=ingress_tracing)
    return out
