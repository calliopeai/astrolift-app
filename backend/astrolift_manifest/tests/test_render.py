"""Tests for the NormalizedManifest → Kubernetes renderer (#5).

The renderer is pure (no DB, no I/O) so we test the dict shape
directly. The set of assertions is intentionally specific — every
field that downstream tooling depends on (selectors, ports, resource
requests, scheduler policy) gets a dedicated check so a future
refactor can't silently change the shape.
"""

from __future__ import annotations

import pytest

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


def test_no_http_probe_when_container_has_no_port():
    # #1033: a port-less container with an http healthcheck must get NO
    # probe — an httpGet on a fabricated port would CrashLoop the pod.
    out = _render((_deployment_workload(containers=(_container(port=0, healthcheck_kind="http"),)),))
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert "livenessProbe" not in container
    assert "readinessProbe" not in container


def test_http_probe_still_rendered_when_container_has_port():
    # Regression guard for #1033: a real port still gets its http probe
    # (the guard is on port==0, not all http probes).
    out = _render((_deployment_workload(containers=(_container(port=8080, healthcheck_kind="http"),)),))
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["livenessProbe"]["httpGet"]["port"] == 8080
    assert container["readinessProbe"]["httpGet"]["port"] == 8080


def test_explicit_exec_probe_honored_on_port_less_container():
    # #1033: the port-less guard is http-specific. An explicitly authored
    # exec probe needs no listening port, so a port-0 worker that defines one
    # must still get it — the fix must not strip non-http probes.
    out = _render(
        (
            _deployment_workload(
                containers=(_container(port=0, healthcheck_kind="exec", healthcheck_value="/bin/true"),)
            ),
        )
    )
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["livenessProbe"]["exec"]["command"] == ["/bin/true"]
    assert container["readinessProbe"]["exec"]["command"] == ["/bin/true"]


# ---- Env / command / args ---------------------------------------------


def test_env_pairs_render_as_name_value():
    w = _deployment_workload(containers=(_container(env=(("DATABASE_URL", "postgres://x"),)),))
    out = _render((w,))
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    # The platform also injects the commit (#1709); this asserts the manifest's
    # own pair survives rendering, which is what the test is about.
    assert {"name": "DATABASE_URL", "value": "postgres://x"} in container["env"]


def test_command_and_args_render_as_lists():
    w = _deployment_workload(containers=(_container(command=("/bin/sh",), args=("-c", "echo hi")),))
    out = _render((w,))
    container = next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["/bin/sh"]
    assert container["args"] == ["-c", "echo hi"]


# ---- HPA --------------------------------------------------------------


def test_hpa_renders_when_min_and_max_set():
    out = _render((_deployment_workload(hpa_min=2, hpa_max=10, hpa_target_cpu_pct=70),))
    dep = next(r for r in out if r["kind"] == "Deployment")
    assert "replicas" not in dep["spec"]
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
    assert next(r for r in out if r["kind"] == "Deployment")["spec"]["replicas"] == 1


@pytest.mark.parametrize("kind", ["deployment", "agent", "workflow"])
@pytest.mark.parametrize("hpa_min,hpa_max", [(None, None), (2, None), (None, 6)])
def test_fixed_and_incomplete_hpa_workloads_retain_explicit_replicas(kind, hpa_min, hpa_max):
    workload = WorkloadManifest(
        name="fixed",
        kind=kind,
        run_family="service",
        replicas=4,
        hpa_min=hpa_min,
        hpa_max=hpa_max,
        workflow_type="Worker",
        task_queue="workers",
        containers=(_container(),),
    )
    out = _render((workload,))
    assert all(r["kind"] != "HorizontalPodAutoscaler" for r in out)
    assert next(r for r in out if r["kind"] == "Deployment")["spec"]["replicas"] == 4


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


# ---- task → batch/v1 Job (#792) --------------------------------------


def test_task_renders_as_one_shot_job():
    w = WorkloadManifest(
        name="migrate",
        kind="task",
        cpu_request="50m",
        memory_request="64Mi",
        containers=(_container(name="job", port=0, command=("/bin/migrate",)),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="hello-prod",
        image_tag="abc123",
        image_repository="ghcr.io/acme/hello",
        environment_name="prod",
    )
    job = next(r for r in out if r["kind"] == "Job")
    assert job["apiVersion"] == "batch/v1"
    assert job["metadata"]["name"] == "migrate"
    # One-shot: run exactly once, no retries.
    assert job["spec"]["completions"] == 1
    assert job["spec"]["backoffLimit"] == 0
    # No CronJob-only fields and no jobTemplate wrapper — the pod spec
    # sits directly under spec.template.
    assert "schedule" not in job["spec"]
    assert "concurrencyPolicy" not in job["spec"]
    assert "jobTemplate" not in job["spec"]
    pod = job["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["containers"][0]["command"] == ["/bin/migrate"]
    assert pod["containers"][0]["resources"]["requests"] == {"cpu": "50m", "memory": "64Mi"}
    # A task should not get a Service or an HPA.
    assert all(r["kind"] not in ("Service", "HorizontalPodAutoscaler") for r in out)


def test_env_from_propagates_to_task_pods():
    w = WorkloadManifest(
        name="migrate",
        kind="task",
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
    pod = next(r for r in out if r["kind"] == "Job")["spec"]["template"]["spec"]
    assert pod["containers"][0]["envFrom"] == [
        {"secretRef": {"name": "astrolift-bindings-hello"}},
    ]


# ---- agent → Deployment + annotations + env (#795) -------------------


def test_agent_renders_deployment_service_hpa_with_annotation_and_env():
    w = WorkloadManifest(
        name="data-pipeline-agent",
        kind="agent",
        run_family="service",
        replicas=2,
        cpu_request="500m",
        memory_request="1Gi",
        hpa_min=1,
        hpa_max=5,
        max_retries=8,
        tool_timeout_seconds=120,
        result_ttl_hours=24,
        containers=(_container(name="agent", port=8080, env=(("MY_FLAG", "1"),)),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="hello-prod",
        image_tag="abc123",
        image_repository="ghcr.io/acme/hello",
        environment_name="prod",
    )

    # Agent renders the same trio as a deployment.
    kinds = sorted(r["kind"] for r in out)
    assert kinds == ["Deployment", "HorizontalPodAutoscaler", "Service"]

    dep = next(r for r in out if r["kind"] == "Deployment")
    assert dep["apiVersion"] == "apps/v1"
    assert "replicas" not in dep["spec"]

    # Pod-template annotation marks this as an agent for the control plane.
    annotations = dep["spec"]["template"]["metadata"]["annotations"]
    assert annotations["astrolift.dev/workload-kind"] == "agent"

    # Dispatch env vars are injected ahead of the manifest-authored env,
    # so an explicit operator override (later entry) wins under k8s.
    container = dep["spec"]["template"]["spec"]["containers"][0]
    # The ordering contract, stated as ordering rather than as an exact list so
    # the platform's own injections (#1709) do not read as a violation of it.
    names = [e["name"] for e in container["env"]]
    assert names.index("ASTROLIFT_WORKLOAD_KIND") < names.index("MY_FLAG")
    assert names.index("ASTROLIFT_MAX_RETRIES") < names.index("MY_FLAG")
    assert names.index("ASTROLIFT_TOOL_TIMEOUT") < names.index("MY_FLAG")
    by_name = {e["name"]: e["value"] for e in container["env"]}
    assert by_name["ASTROLIFT_WORKLOAD_KIND"] == "agent"
    assert by_name["ASTROLIFT_MAX_RETRIES"] == "8"
    assert by_name["ASTROLIFT_TOOL_TIMEOUT"] == "120"
    assert by_name["MY_FLAG"] == "1"

    # HPA targets the agent's Deployment by name.
    hpa = next(r for r in out if r["kind"] == "HorizontalPodAutoscaler")
    assert hpa["spec"]["scaleTargetRef"]["name"] == "data-pipeline-agent"


def test_task_family_agent_emits_no_resources():
    # A task-family agent is dispatched as a one-shot Job by the agent
    # dispatch path; the renderer must emit NOTHING for it (#1027) — an
    # always-on Deployment would CrashLoop a run-to-completion agent.
    w = WorkloadManifest(
        name="batch-agent",
        kind="agent",
        run_family="task",
        replicas=2,
        hpa_min=1,
        hpa_max=5,
        containers=(_container(name="agent", port=8080),),
    )
    out = _render((w,))
    assert out == []


def test_agent_defaults_to_task_family_emits_no_resources():
    # The WorkloadManifest default run_family is "task", so an agent
    # constructed without an explicit family also renders nothing.
    w = WorkloadManifest(
        name="batch-agent",
        kind="agent",
        hpa_min=1,
        hpa_max=5,
        containers=(_container(name="agent", port=8080),),
    )
    out = _render((w,))
    assert out == []


def test_service_family_agent_renders_deployment_service_hpa():
    w = WorkloadManifest(
        name="chat-agent",
        kind="agent",
        run_family="service",
        replicas=3,
        hpa_min=2,
        hpa_max=6,
        containers=(_container(name="agent", port=8080),),
    )
    out = _render((w,))
    kinds = sorted(r["kind"] for r in out)
    assert kinds == ["Deployment", "HorizontalPodAutoscaler", "Service"]
    dep = next(r for r in out if r["kind"] == "Deployment")
    assert "replicas" not in dep["spec"]
    assert dep["spec"]["template"]["metadata"]["annotations"]["astrolift.dev/workload-kind"] == "agent"


def test_service_family_agent_without_hpa_renders_deployment_and_service_only():
    w = WorkloadManifest(
        name="chat-agent",
        kind="agent",
        run_family="service",
        containers=(_container(name="agent", port=8080),),
    )
    out = _render((w,))
    kinds = sorted(r["kind"] for r in out)
    assert kinds == ["Deployment", "Service"]


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
    assert {"name": "FOO", "value": "bar"} in container["env"]
    assert container["envFrom"] == [{"secretRef": {"name": "app-shared"}}]


def test_workload_scoped_binding_secret_is_not_exposed_to_other_workloads():
    out = _render(
        (_deployment_workload("api"), _deployment_workload("worker")),
        env_from_secret_refs=["operator-bundle", "bindings-universal"],
        workload_env_from_secret_refs={"api": ["bindings-api-only"]},
    )
    deployments = {row["metadata"]["name"]: row for row in out if row["kind"] == "Deployment"}
    api = deployments["api"]["spec"]["template"]["spec"]["containers"][0]
    worker = deployments["worker"]["spec"]["template"]["spec"]["containers"][0]

    assert api["envFrom"] == [
        {"secretRef": {"name": "operator-bundle"}},
        {"secretRef": {"name": "bindings-universal"}},
        {"secretRef": {"name": "bindings-api-only"}},
    ]
    assert worker["envFrom"] == [
        {"secretRef": {"name": "operator-bundle"}},
        {"secretRef": {"name": "bindings-universal"}},
    ]


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


# ---- workflow → Deployment + Temporal annotations + env (#796) --------


def test_workflow_worker_renders_deployment_service_hpa_with_annotation_and_env():
    w = WorkloadManifest(
        name="approval-worker",
        kind="workflow",
        replicas=2,
        cpu_request="500m",
        memory_request="1Gi",
        hpa_min=1,
        hpa_max=5,
        workflow_type="ApprovalWorkflow",
        task_queue="approvals",
        temporal_namespace="prod",
        max_concurrent_activities=50,
        max_concurrent_workflows=25,
        containers=(_container(name="worker", port=8080, env=(("MY_FLAG", "1"),)),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="hello-prod",
        image_tag="abc123",
        image_repository="ghcr.io/acme/hello",
        environment_name="prod",
    )

    # A workflow worker renders the same trio as a deployment.
    kinds = sorted(r["kind"] for r in out)
    assert kinds == ["Deployment", "HorizontalPodAutoscaler", "Service"]

    dep = next(r for r in out if r["kind"] == "Deployment")
    assert dep["apiVersion"] == "apps/v1"
    assert "replicas" not in dep["spec"]

    # Pod-template annotation marks this as a workflow worker for the
    # control plane.
    annotations = dep["spec"]["template"]["metadata"]["annotations"]
    assert annotations["astrolift.dev/workload-kind"] == "workflow"

    # Temporal env vars are injected ahead of the manifest-authored env,
    # so an explicit operator override (later entry) wins under k8s.
    # The poller concurrency caps are not rendered as K8s output.
    container = dep["spec"]["template"]["spec"]["containers"][0]
    # Ordering contract, stated as ordering rather than as an exact list so the
    # platform's own injections (#1709) do not read as a violation of it.
    names = [e["name"] for e in container["env"]]
    for injected in ("ASTROLIFT_WORKFLOW_TYPE", "ASTROLIFT_TASK_QUEUE", "TEMPORAL_NAMESPACE"):
        assert names.index(injected) < names.index("MY_FLAG")
    by_name = {e["name"]: e["value"] for e in container["env"]}
    assert by_name["ASTROLIFT_WORKFLOW_TYPE"] == "ApprovalWorkflow"
    assert by_name["ASTROLIFT_TASK_QUEUE"] == "approvals"
    assert by_name["TEMPORAL_NAMESPACE"] == "prod"
    assert by_name["MY_FLAG"] == "1"

    # HPA targets the worker's Deployment by name.
    hpa = next(r for r in out if r["kind"] == "HorizontalPodAutoscaler")
    assert hpa["spec"]["scaleTargetRef"]["name"] == "approval-worker"


# ---- statefulset → StatefulSet + headless Service (#798) ---------------


def test_statefulset_basic_renders_statefulset_and_headless_service():
    w = WorkloadManifest(
        name="postgres",
        kind="statefulset",
        replicas=3,
        containers=(_container("pg", port=5432),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="pg-prod",
        image_tag="14.9",
        image_repository="ghcr.io/acme/postgres",
        environment_name="prod",
    )

    kinds = sorted(r["kind"] for r in out)
    # StatefulSet + headless Service + regular Service (port 5432 is set)
    assert kinds == ["Service", "Service", "StatefulSet"]

    sts = next(r for r in out if r["kind"] == "StatefulSet")
    assert sts["apiVersion"] == "apps/v1"
    assert sts["spec"]["replicas"] == 3
    assert sts["spec"]["serviceName"] == "postgres-headless"
    # No volumeClaimTemplates when storage_size is unset
    assert "volumeClaimTemplates" not in sts["spec"]

    headless = next(r for r in out if r["kind"] == "Service" and r["metadata"]["name"] == "postgres-headless")
    assert headless["spec"]["clusterIP"] == "None"
    assert headless["spec"]["ports"][0]["port"] == 5432


def test_statefulset_with_storage_emits_volume_claim_template():
    w = WorkloadManifest(
        name="redis",
        kind="statefulset",
        replicas=1,
        storage_class="gp3",
        storage_size="10Gi",
        containers=(_container("redis", port=0),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="cache",
        image_tag="7",
        image_repository="ghcr.io/acme/redis",
        environment_name="prod",
    )

    sts = next(r for r in out if r["kind"] == "StatefulSet")
    vct = sts["spec"]["volumeClaimTemplates"]
    assert len(vct) == 1
    assert vct[0]["metadata"]["name"] == "redis-data"
    assert vct[0]["spec"]["resources"]["requests"]["storage"] == "10Gi"
    assert vct[0]["spec"]["storageClassName"] == "gp3"
    assert vct[0]["spec"]["accessModes"] == ["ReadWriteOnce"]


def test_statefulset_mounts_volume_claim_template():
    # Regression for #989: the per-pod volumeClaimTemplate must be MOUNTED into
    # the primary container at its declared mount_path, else the app's writes
    # land on the pod's ephemeral FS and don't survive a restart.
    w = WorkloadManifest(
        name="store",
        kind="statefulset",
        replicas=1,
        storage_size="1Gi",
        volumes=({"name": "data", "kind": "pvc", "mount_path": "/var/lib/store", "size": "1Gi"},),
        containers=(_container("app", port=8080),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="ns",
        image_tag="v1",
        image_repository="ghcr.io/acme/store",
        environment_name="prod",
    )
    sts = next(r for r in out if r["kind"] == "StatefulSet")
    assert sts["spec"]["volumeClaimTemplates"][0]["metadata"]["name"] == "store-data"
    mounts = sts["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
    assert {"name": "store-data", "mountPath": "/var/lib/store"} in mounts


def test_statefulset_storage_size_defaults_mount_path():
    # storage_size with no explicit volumes mount_path still mounts (at /data),
    # so the claim is never orphaned.
    w = WorkloadManifest(
        name="db",
        kind="statefulset",
        replicas=1,
        storage_size="2Gi",
        containers=(_container("app", port=0),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="ns",
        image_tag="v1",
        image_repository="ghcr.io/acme/db",
        environment_name="prod",
    )
    sts = next(r for r in out if r["kind"] == "StatefulSet")
    mounts = sts["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
    assert mounts == [{"name": "db-data", "mountPath": "/data"}]


def test_statefulset_volume_declaration_alone_emits_claim():
    # Regression: a workload that declares [[workloads.volumes]] and no
    # top-level storage_size rendered NO claim and NO mount, while still being
    # scheduled as a StatefulSet -- the app came up with its data path pointing
    # at an unmounted directory. Every pre-existing test set storage_size AND
    # volumes together, so the declaration-only path was never covered.
    w = WorkloadManifest(
        name="web",
        kind="statefulset",
        replicas=1,
        volumes=({"name": "data", "kind": "pvc", "mount_path": "/app/data", "size": "5Gi"},),
        containers=(_container("web", port=3000),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="ns",
        image_tag="v1",
        image_repository="ghcr.io/acme/web",
        environment_name="prod",
    )
    sts = next(r for r in out if r["kind"] == "StatefulSet")
    vct = sts["spec"]["volumeClaimTemplates"]
    assert len(vct) == 1
    assert vct[0]["metadata"]["name"] == "web-data"
    assert vct[0]["spec"]["resources"]["requests"]["storage"] == "5Gi"
    mounts = sts["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
    assert mounts == [{"name": "web-data", "mountPath": "/app/data"}]


def test_statefulset_volume_declaration_honours_access_mode_and_class():
    w = WorkloadManifest(
        name="shared",
        kind="statefulset",
        replicas=1,
        volumes=(
            {
                "name": "data",
                "kind": "pvc",
                "mount_path": "/data",
                "size": "20Gi",
                "access_mode": "ReadWriteMany",
                "storage_class": "efs-sc",
            },
        ),
        containers=(_container("app", port=0),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="ns",
        image_tag="v1",
        image_repository="ghcr.io/acme/shared",
        environment_name="prod",
    )
    sts = next(r for r in out if r["kind"] == "StatefulSet")
    spec = sts["spec"]["volumeClaimTemplates"][0]["spec"]
    assert spec["accessModes"] == ["ReadWriteMany"]
    assert spec["storageClassName"] == "efs-sc"


def test_statefulset_storage_size_still_wins_over_declaration():
    # Back-compat: the top-level key keeps its meaning for workloads that
    # already set it, and the claim name stays <workload>-data so an existing
    # StatefulSet's PVCs are never orphaned by a rename.
    w = WorkloadManifest(
        name="legacy",
        kind="statefulset",
        replicas=1,
        storage_size="10Gi",
        storage_class="gp3",
        volumes=({"name": "data", "kind": "pvc", "mount_path": "/srv", "size": "5Gi"},),
        containers=(_container("app", port=0),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="ns",
        image_tag="v1",
        image_repository="ghcr.io/acme/legacy",
        environment_name="prod",
    )
    sts = next(r for r in out if r["kind"] == "StatefulSet")
    vct = sts["spec"]["volumeClaimTemplates"][0]
    assert vct["metadata"]["name"] == "legacy-data"
    assert vct["spec"]["resources"]["requests"]["storage"] == "10Gi"
    assert vct["spec"]["storageClassName"] == "gp3"
    mounts = sts["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
    assert mounts == [{"name": "legacy-data", "mountPath": "/srv"}]


def test_statefulset_pvc_gets_default_fsgroup():
    # A PVC arrives root-owned, so a container running as a non-root uid
    # cannot write to it and dies on its own data directory. fsGroup is added
    # to the container's supplementary groups, so one default works for every
    # image without the app declaring the uid it runs as.
    w = WorkloadManifest(
        name="web",
        kind="statefulset",
        replicas=1,
        volumes=({"name": "data", "kind": "pvc", "mount_path": "/app/data", "size": "5Gi"},),
        containers=(_container("web", port=3000),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="ns",
        image_tag="v1",
        image_repository="ghcr.io/acme/web",
        environment_name="prod",
    )
    sts = next(r for r in out if r["kind"] == "StatefulSet")
    assert sts["spec"]["template"]["spec"]["securityContext"]["fsGroup"] == 1000


def test_statefulset_explicit_fs_group_wins():
    w = WorkloadManifest(
        name="web",
        kind="statefulset",
        replicas=1,
        fs_group=2000,
        volumes=({"name": "data", "kind": "pvc", "mount_path": "/app/data", "size": "5Gi"},),
        containers=(_container("web", port=0),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="ns",
        image_tag="v1",
        image_repository="ghcr.io/acme/web",
        environment_name="prod",
    )
    sts = next(r for r in out if r["kind"] == "StatefulSet")
    assert sts["spec"]["template"]["spec"]["securityContext"]["fsGroup"] == 2000


def test_statefulset_without_pvc_gets_no_fsgroup():
    # fsGroup relabels every volume the pod carries, so a workload that
    # mounts no claim must not acquire one just by being a StatefulSet.
    w = WorkloadManifest(
        name="plain",
        kind="statefulset",
        replicas=1,
        containers=(_container("app", port=0),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="ns",
        image_tag="v1",
        image_repository="ghcr.io/acme/plain",
        environment_name="prod",
    )
    sts = next(r for r in out if r["kind"] == "StatefulSet")
    assert "fsGroup" not in (sts["spec"]["template"]["spec"].get("securityContext") or {})


def test_statefulset_non_pvc_volume_emits_no_claim():
    # An emptyDir declaration is not persistent storage; it must not conjure a
    # PVC. Guards the new lookup against matching any volume kind.
    w = WorkloadManifest(
        name="scratch",
        kind="statefulset",
        replicas=1,
        volumes=({"name": "tmp", "kind": "empty_dir", "mount_path": "/tmp/work"},),
        containers=(_container("app", port=0),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="ns",
        image_tag="v1",
        image_repository="ghcr.io/acme/scratch",
        environment_name="prod",
    )
    sts = next(r for r in out if r["kind"] == "StatefulSet")
    assert "volumeClaimTemplates" not in sts["spec"]


def test_function_renders_knative_service():
    # Regression for #988: kind=function previously raised NameError
    # (_resource_spec undefined) at render time.
    w = WorkloadManifest(
        name="fn",
        kind="function",
        min_scale=0,
        max_scale=5,
        function_concurrency=10,
        function_timeout_seconds=60,
        containers=(_container("fn", port=8080),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="ns",
        image_tag="v1",
        image_repository="ghcr.io/acme/fn",
        environment_name="prod",
    )
    ksvc = next(r for r in out if str(r.get("apiVersion", "")).startswith("serving.knative"))
    assert ksvc["kind"] == "Service"
    container = ksvc["spec"]["template"]["spec"]["containers"][0]
    assert "resources" in container  # the field whose helper used to crash


def test_statefulset_no_port_omits_headless_ports():
    w = WorkloadManifest(
        name="etcd",
        kind="statefulset",
        replicas=3,
        containers=(_container("etcd", port=0),),
    )
    out = render_manifests(
        _normalized(w),
        namespace="infra",
        image_tag="v3.5",
        image_repository="ghcr.io/acme/etcd",
        environment_name="prod",
    )
    headless = next(r for r in out if r["kind"] == "Service" and r["metadata"]["name"] == "etcd-headless")
    assert headless["spec"]["ports"] == []
    # No regular Service for a port-less workload
    non_headless = [r for r in out if r["kind"] == "Service" and r["metadata"]["name"] != "etcd-headless"]
    assert non_headless == []
