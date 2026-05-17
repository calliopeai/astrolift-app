"""Tests for the NormalizedManifest → Kubernetes renderer (#5).

The renderer is pure (no DB, no I/O) so we test the dict shape
directly. The set of assertions is intentionally specific — every
field that downstream tooling depends on (selectors, ports, resource
requests, scheduler policy) gets a dedicated check so a future
refactor can't silently change the shape.
"""

from __future__ import annotations

from astrolift_manifest.render import render_manifests
from astrolift_manifest.types import (
    ContainerManifest,
    NormalizedManifest,
    WorkloadManifest,
)


def _normalized(*workloads: WorkloadManifest) -> NormalizedManifest:
    """NormalizedManifest constructor convenience for tests."""
    return NormalizedManifest(
        name="hello",
        workloads=workloads,
        managed_services=(),
        defaults_applied=(),
        serialized={},
    )


def _container(
    name: str = "app",
    *,
    is_primary: bool = True,
    port: int = 8080,
    image_ref: str | None = None,
    healthcheck_kind: str = "none",
    healthcheck_value: str = "",
    healthcheck_port: int | None = None,
    env: tuple[tuple[str, str], ...] = (),
    command: tuple[str, ...] = (),
    args: tuple[str, ...] = (),
) -> ContainerManifest:
    return ContainerManifest(
        name=name,
        is_primary=is_primary,
        image_ref=image_ref,
        port=port,
        command=command,
        args=args,
        env=env,
        healthcheck_kind=healthcheck_kind,
        healthcheck_value=healthcheck_value,
        healthcheck_port=healthcheck_port,
    )


def _deployment_workload(
    name: str = "web",
    *,
    replicas: int = 1,
    cpu_request: str | None = "100m",
    cpu_limit: str | None = "500m",
    memory_request: str | None = "128Mi",
    memory_limit: str | None = "512Mi",
    hpa_min: int | None = None,
    hpa_max: int | None = None,
    hpa_target_cpu_pct: int = 80,
    containers: tuple[ContainerManifest, ...] = (_container(),),
) -> WorkloadManifest:
    return WorkloadManifest(
        name=name,
        kind="deployment",
        replicas=replicas,
        cpu_request=cpu_request,
        cpu_limit=cpu_limit,
        memory_request=memory_request,
        memory_limit=memory_limit,
        hpa_min=hpa_min,
        hpa_max=hpa_max,
        hpa_target_cpu_pct=hpa_target_cpu_pct,
        containers=containers,
    )


def _render(
    workloads: tuple[WorkloadManifest, ...],
    **kwargs,
) -> list[dict]:
    defaults = {
        "namespace": "hello-prod",
        "image_tag": "abc123",
        "image_repository": "ghcr.io/acme/hello",
        "environment_name": "prod",
    }
    defaults.update(kwargs)
    return render_manifests(_normalized(*workloads), **defaults)


# ---- baseline shape ---------------------------------------------------


def test_deployment_with_service_renders_two_resources():
    out = _render((_deployment_workload(),))
    kinds = [r["kind"] for r in out]
    assert kinds == ["Deployment", "Service"]


def test_render_is_deterministic_across_runs():
    """Same input → same output. Stability matters because the apply
    step diffs against last-rendered to decide what changed."""
    a = _render((_deployment_workload(),))
    b = _render((_deployment_workload(),))
    assert a == b


def test_render_sorts_by_kind_then_name():
    out = _render((_deployment_workload("web"), _deployment_workload("worker")))
    kind_name = [(r["kind"], r["metadata"]["name"]) for r in out]
    assert kind_name == sorted(kind_name)


# ---- Deployment fields -----------------------------------------------


def test_deployment_carries_image_tag():
    out = _render((_deployment_workload(),))
    dep = next(r for r in out if r["kind"] == "Deployment")
    container = dep["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "ghcr.io/acme/hello:abc123"


def test_container_image_ref_overrides_repository():
    """A sidecar can pin its own image without the deploy-tag override."""
    sidecar = _container(name="proxy", is_primary=False, port=0, image_ref="envoyproxy/envoy:v1.30.0")
    out = _render((_deployment_workload(containers=(_container(), sidecar)),))
    dep = next(r for r in out if r["kind"] == "Deployment")
    images = [c["image"] for c in dep["spec"]["template"]["spec"]["containers"]]
    assert images == ["ghcr.io/acme/hello:abc123", "envoyproxy/envoy:v1.30.0"]


def test_replica_count_propagates():
    out = _render((_deployment_workload(replicas=3),))
    dep = next(r for r in out if r["kind"] == "Deployment")
    assert dep["spec"]["replicas"] == 3


def test_deployment_selector_matches_template_labels():
    """The selector and pod-template labels must agree or the
    Deployment will never own the ReplicaSet it creates."""
    out = _render((_deployment_workload(),))
    dep = next(r for r in out if r["kind"] == "Deployment")
    selector = dep["spec"]["selector"]["matchLabels"]
    template_labels = dep["spec"]["template"]["metadata"]["labels"]
    assert selector.items() <= template_labels.items()


def test_namespace_propagates_to_every_resource():
    out = _render((_deployment_workload(),), namespace="my-ns")
    assert {r["metadata"]["namespace"] for r in out} == {"my-ns"}


def test_app_and_environment_labels_are_set():
    out = _render((_deployment_workload(),), environment_name="staging")
    for r in out:
        labels = r["metadata"]["labels"]
        assert labels["astrolift.dev/app"] == "hello"
        assert labels["astrolift.dev/environment"] == "staging"


# ---- Resources --------------------------------------------------------


def test_resources_render_when_set():
    out = _render((_deployment_workload(),))
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["resources"] == {
        "requests": {"cpu": "100m", "memory": "128Mi"},
        "limits": {"cpu": "500m", "memory": "512Mi"},
    }


def test_resources_only_request_no_limit():
    """Partial resource specs must not synthesize the missing half."""
    w = _deployment_workload(cpu_limit=None, memory_limit=None)
    out = _render((w,))
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["resources"] == {"requests": {"cpu": "100m", "memory": "128Mi"}}


def test_resources_omitted_entirely():
    w = _deployment_workload(cpu_request=None, cpu_limit=None, memory_request=None, memory_limit=None)
    out = _render((w,))
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert "resources" not in container


# ---- Service ----------------------------------------------------------


def test_service_targets_primary_container_port():
    out = _render((_deployment_workload(containers=(_container(port=9090),)),))
    svc = next(r for r in out if r["kind"] == "Service")
    assert svc["spec"]["ports"][0]["port"] == 9090
    assert svc["spec"]["ports"][0]["targetPort"] == 9090


def test_no_service_when_primary_has_no_port():
    out = _render((_deployment_workload(containers=(_container(port=0),)),))
    assert all(r["kind"] != "Service" for r in out)


def test_service_selector_matches_workload_label():
    out = _render((_deployment_workload(),))
    svc = next(r for r in out if r["kind"] == "Service")
    assert svc["spec"]["selector"]["astrolift.dev/workload"] == "web"


# ---- Probes -----------------------------------------------------------


def test_http_probe_rendered_with_default_path():
    out = _render((_deployment_workload(containers=(_container(healthcheck_kind="http"),)),))
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    probe = container["livenessProbe"]
    assert probe["httpGet"]["path"] == "/health"
    assert probe["httpGet"]["port"] == 8080
    # liveness + readiness are intentionally identical until per-probe
    # tuning lands.
    assert container["readinessProbe"] == probe


def test_http_probe_uses_value_path_when_set():
    out = _render(
        (
            _deployment_workload(
                containers=(_container(healthcheck_kind="http", healthcheck_value="/healthz"),)
            ),
        )
    )
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"


def test_tcp_probe_uses_container_port_by_default():
    out = _render((_deployment_workload(containers=(_container(healthcheck_kind="tcp", port=5432),)),))
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["livenessProbe"]["tcpSocket"]["port"] == 5432


def test_exec_probe_splits_command_string():
    out = _render(
        (
            _deployment_workload(
                containers=(
                    _container(
                        healthcheck_kind="exec",
                        healthcheck_value="cat /tmp/healthy",
                    ),
                )
            ),
        )
    )
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["livenessProbe"]["exec"]["command"] == ["cat", "/tmp/healthy"]


def test_no_probe_when_kind_is_none():
    out = _render((_deployment_workload(),))
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" not in container
    assert "readinessProbe" not in container


# ---- Env / command / args ---------------------------------------------


def test_env_pairs_render_as_name_value():
    w = _deployment_workload(containers=(_container(env=(("DATABASE_URL", "postgres://x"),)),))
    out = _render((w,))
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["env"] == [{"name": "DATABASE_URL", "value": "postgres://x"}]


def test_command_and_args_render_as_lists():
    w = _deployment_workload(containers=(_container(command=("/bin/sh",), args=("-c", "echo hi")),))
    out = _render((w,))
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["/bin/sh"]
    assert container["args"] == ["-c", "echo hi"]


# ---- HPA --------------------------------------------------------------


def test_hpa_renders_when_min_and_max_set():
    out = _render((_deployment_workload(hpa_min=2, hpa_max=10, hpa_target_cpu_pct=70),))
    hpa = next(r for r in out if r["kind"] == "HorizontalPodAutoscaler")
    assert hpa["spec"]["minReplicas"] == 2
    assert hpa["spec"]["maxReplicas"] == 10
    assert hpa["spec"]["metrics"][0]["resource"]["target"]["averageUtilization"] == 70
    assert hpa["spec"]["scaleTargetRef"]["name"] == "web"


def test_hpa_not_rendered_when_only_min_set():
    """Half-set HPA bounds is suspicious — render none and let the
    operator notice rather than silently extrapolating."""
    out = _render((_deployment_workload(hpa_min=2),))
    assert all(r["kind"] != "HorizontalPodAutoscaler" for r in out)


# ---- CronJob ----------------------------------------------------------


def test_cronjob_renders_with_schedule():
    w = WorkloadManifest(
        name="nightly",
        kind="cronjob",
        schedule="0 0 * * *",
        cpu_request="50m",
        memory_request="64Mi",
        containers=(_container(name="job", port=0, command=("/bin/run-job",)),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="hello-prod",
        image_tag="abc123",
        image_repository="ghcr.io/acme/hello",
        environment_name="prod",
    )
    cj = next(r for r in out if r["kind"] == "CronJob")
    assert cj["spec"]["schedule"] == "0 0 * * *"
    assert cj["spec"]["concurrencyPolicy"] == "Forbid"
    pod = cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "OnFailure"
    assert pod["containers"][0]["command"] == ["/bin/run-job"]
    # CronJob should not get a Service (no port).
    assert all(r["kind"] != "Service" for r in out)


# ---- envFrom injection (#353) ----------------------------------------


def test_env_from_absent_when_no_refs():
    out = _render((_deployment_workload(),))
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert "envFrom" not in container


def test_env_from_injects_secret_refs_into_primary_container():
    out = _render(
        (_deployment_workload(),),
        env_from_secret_refs=[
            "app-shared",
            "astrolift-bindings-hello",
        ],
    )
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["envFrom"] == [
        {"secretRef": {"name": "app-shared"}},
        {"secretRef": {"name": "astrolift-bindings-hello"}},
    ]


def test_env_from_threads_into_every_container():
    """Sidecars need the same bindings — the deploy lane assumes
    every container in the pod gets the connection envelope."""
    side = _container(name="sidecar", is_primary=False, port=0)
    out = _render(
        (_deployment_workload(containers=(_container(), side)),),
        env_from_secret_refs=["astrolift-bindings-hello"],
    )
    containers = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 2
    for c in containers:
        assert c["envFrom"] == [
            {"secretRef": {"name": "astrolift-bindings-hello"}},
        ]


def test_env_from_propagates_to_cronjob_pods():
    w = WorkloadManifest(
        name="nightly",
        kind="cronjob",
        schedule="0 0 * * *",
        containers=(_container(name="job", port=0),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="hello-prod",
        image_tag="abc123",
        image_repository="ghcr.io/acme/hello",
        environment_name="prod",
        env_from_secret_refs=["astrolift-bindings-hello"],
    )
    pod = next(r for r in out if r["kind"] == "CronJob")["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert pod["containers"][0]["envFrom"] == [
        {"secretRef": {"name": "astrolift-bindings-hello"}},
    ]


def test_env_from_preserves_inline_env():
    """envFrom is additive — inline ``env:`` literals on the
    container survive alongside the injected envFrom refs."""
    c = _container(env=(("FOO", "bar"),))
    out = _render(
        (_deployment_workload(containers=(c,)),),
        env_from_secret_refs=["app-shared"],
    )
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["env"] == [{"name": "FOO", "value": "bar"}]
    assert container["envFrom"] == [{"secretRef": {"name": "app-shared"}}]


def test_cronjob_concurrency_policy_defaults_to_forbid():
    """A cronjob workload that omits ``concurrency_policy`` keeps the
    pre-#427 hard-coded ``Forbid`` so existing manifests don't change
    behaviour after the migration."""
    w = WorkloadManifest(
        name="nightly",
        kind="cronjob",
        schedule="0 0 * * *",
        containers=(_container(name="job", port=0),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="hello-prod",
        image_tag="abc123",
        image_repository="ghcr.io/acme/hello",
        environment_name="prod",
    )
    cj = next(r for r in out if r["kind"] == "CronJob")
    assert cj["spec"]["concurrencyPolicy"] == "Forbid"


def test_cronjob_concurrency_policy_maps_queue_to_k8s_allow():
    """``queue`` is the platform name for K8s ``Allow`` — the K8s
    enum is unintuitive ("Allow" means stack overlapping runs) so
    the platform exposes the friendlier verb."""
    w = WorkloadManifest(
        name="nightly",
        kind="cronjob",
        schedule="0 0 * * *",
        concurrency_policy="queue",
        containers=(_container(name="job", port=0),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="hello-prod",
        image_tag="abc123",
        image_repository="ghcr.io/acme/hello",
        environment_name="prod",
    )
    cj = next(r for r in out if r["kind"] == "CronJob")
    assert cj["spec"]["concurrencyPolicy"] == "Allow"


def test_cronjob_concurrency_policy_maps_replace_passthrough():
    w = WorkloadManifest(
        name="nightly",
        kind="cronjob",
        schedule="0 0 * * *",
        concurrency_policy="replace",
        containers=(_container(name="job", port=0),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="hello-prod",
        image_tag="abc123",
        image_repository="ghcr.io/acme/hello",
        environment_name="prod",
    )
    cj = next(r for r in out if r["kind"] == "CronJob")
    assert cj["spec"]["concurrencyPolicy"] == "Replace"


def test_cronjob_without_schedule_raises():
    """Belt-and-suspenders: parser guarantees this can't happen via
    the public API, but the renderer's invariant still holds."""
    w = WorkloadManifest(
        name="bad",
        kind="cronjob",
        schedule=None,
        containers=(_container(name="x", port=0),),
    )
    import pytest

    with pytest.raises(ValueError, match="cronjob"):
        render_manifests(
            _normalized(w),
            namespace="ns",
            image_tag="t",
            image_repository="r",
            environment_name="e",
        )
