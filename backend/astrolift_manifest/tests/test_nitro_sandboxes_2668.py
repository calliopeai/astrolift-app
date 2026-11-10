"""Nitro Sandbox selection survives parse/normalize/render without lowering isolation."""

from __future__ import annotations

import dataclasses

import pytest

from astrolift_manifest.normalize import manifest_hash, normalize
from astrolift_manifest.parser import ManifestError, parse_raw
from astrolift_manifest.render import render_manifests


def source(block: str = "", kind: str = "deployment", extra: str = "", container: str = "") -> str:
    return f"""
name = "worker"
[[workloads]]
name = "worker"
kind = "{kind}"
{extra}
{block}
[[workloads.containers]]
name = "worker"
image_ref = "worker@sha256:abc"
{container}
"""


def render(text: str):
    manifest = normalize(parse_raw(text))
    resources = render_manifests(
        manifest,
        namespace="team",
        image_tag="test",
        image_repository="worker",
        environment_name="test",
    )
    return manifest, resources


def template(resources):
    resource = next(r for r in resources if r["kind"] in {"Deployment", "StatefulSet", "Job", "CronJob"})
    spec = resource["spec"]
    if resource["kind"] == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    return spec["template"]


@pytest.mark.parametrize(
    "kind,extra",
    [
        ("deployment", ""),
        ("statefulset", 'storage_size = "1Gi"'),
        ("task", ""),
        ("cronjob", 'schedule = "0 * * * *"'),
        ("agent", 'run_family = "service"'),
        ("workflow", 'workflow_type = "work"\ntask_queue = "work"'),
    ],
)
@pytest.mark.parametrize("mode", ["isolated", "confidential"])
def test_isolation_reaches_every_supported_controller(kind, extra, mode):
    policy = ', policy = "generated=="' if mode == "confidential" else ""
    manifest, resources = render(source(f'nitro_sandbox = {{mode = "{mode}"{policy}}}', kind, extra))
    assert manifest.serialized["workloads"][0]["nitro_sandbox"]["mode"] == mode
    pod = template(resources)
    assert pod["spec"]["runtimeClassName"] == f"{mode}-sandbox"
    assert pod["spec"]["tolerations"] == [
        {"key": "astrolift.dev/nitro-only", "operator": "Equal", "value": "true", "effect": "NoSchedule"}
    ]
    annotations = pod["metadata"].get("annotations", {})
    assert annotations.get("sandbox.amazonaws.com/policy") == (
        "generated==" if mode == "confidential" else None
    )
    if kind in {"agent", "workflow"}:
        assert template(resources)["metadata"]["annotations"]["astrolift.dev/workload-kind"] == kind


def test_omitted_setting_preserves_normalized_shape_and_render():
    manifest, resources = render(source())
    assert "nitro_sandbox" not in manifest.serialized["workloads"][0]
    assert "runtimeClassName" not in template(resources)["spec"]
    assert "tolerations" not in template(resources)["spec"]
    opted_in, _ = render(source('nitro_sandbox = {mode = "isolated"}'))
    assert manifest_hash(manifest.serialized) != manifest_hash(opted_in.serialized)


def test_confidential_policy_survives_without_trimming_or_reencoding():
    policy = "  operator-generated-base64==  "
    manifest, resources = render(source(f'nitro_sandbox = {{mode = "confidential", policy = "{policy}"}}'))
    assert manifest.workloads[0].nitro_sandbox["policy"] == policy
    assert manifest.serialized["workloads"][0]["nitro_sandbox"]["policy"] == policy
    pod = template(resources)
    assert pod["metadata"]["annotations"]["sandbox.amazonaws.com/policy"] == policy
    assert pod["spec"]["runtimeClassName"] == "confidential-sandbox"


def test_mixed_workloads_do_not_inherit_the_neighbors_sandbox():
    raw = parse_raw(source('nitro_sandbox = {mode = "isolated"}'))
    ordinary = dataclasses.replace(raw.workloads[0], name="ordinary", nitro_sandbox=None)
    manifest = normalize(dataclasses.replace(raw, workloads=(*raw.workloads, ordinary)))
    resources = render_manifests(
        manifest, namespace="team", image_tag="test", image_repository="worker", environment_name="test"
    )
    pods = {
        r["metadata"]["name"]: r["spec"]["template"]["spec"] for r in resources if r["kind"] == "Deployment"
    }
    assert pods["worker"]["runtimeClassName"] == "isolated-sandbox"
    assert "runtimeClassName" not in pods["ordinary"]
    assert "tolerations" not in pods["ordinary"]
    assert pods["worker"]["tolerations"][0]["key"] == "astrolift.dev/nitro-only"


@pytest.mark.parametrize(
    "block",
    [
        'nitro_sandbox = "isolated"',
        "nitro_sandbox = {}",
        'nitro_sandbox = {mode = ["isolated"]}',
        'nitro_sandbox = {mode = "shared"}',
        'nitro_sandbox = {mode = "isolated", typo = true}',
        'nitro_sandbox = {mode = "confidential"}',
        'nitro_sandbox = {mode = "confidential", policy = "   "}',
        'nitro_sandbox = {mode = "isolated", network_isolated = "true"}',
        'nitro_sandbox = {mode = "confidential", policy = 12}',
    ],
)
def test_invalid_blocks_are_rejected_with_a_field_path(block):
    with pytest.raises(ManifestError, match="nitro_sandbox"):
        parse_raw(source(block))


@pytest.mark.parametrize("kind,extra", [("function", ""), ("job", "")])
def test_unimplemented_workload_paths_cannot_silently_drop_isolation(kind, extra):
    with pytest.raises(ManifestError, match="nitro_sandbox"):
        parse_raw(source('nitro_sandbox = {mode = "isolated"}', kind, extra))


@pytest.mark.parametrize(
    "extra,container",
    [
        ("gpu = 1", ""),
        ("is_public = true", ""),
        ("", "port = 8000"),
        ("", 'port = 8000\nhealthcheck = {kind = "http", value = "/health"}'),
    ],
)
def test_incompatible_sandbox_configurations_are_rejected(extra, container):
    with pytest.raises(ManifestError, match="nitro_sandbox"):
        parse_raw(
            source(
                'nitro_sandbox = {mode = "isolated", network_isolated = true}',
                extra=extra,
                container=container,
            )
        )


def test_network_isolation_annotation_and_exec_probe_reach_the_pod():
    _, resources = render(
        source(
            'nitro_sandbox = {mode = "isolated", network_isolated = true}',
            container='port = 8000\nhealthcheck = {kind = "exec", value = "test -f /tmp/ready"}',
        )
    )
    pod = template(resources)
    assert pod["metadata"]["annotations"]["sandbox.amazonaws.com/network-isolated"] == "true"
    assert "exec" in pod["spec"]["containers"][0]["readinessProbe"]


@pytest.mark.parametrize("alias", ["jobs", "tasks"])
def test_legacy_aliases_cannot_silently_discard_sandbox_selection(alias):
    text = f"""name = "worker"
[[{alias}]]
name = "worker"
schedule = "0 * * * *"
nitro_sandbox = {{mode = "isolated"}}
"""
    with pytest.raises(ManifestError, match="nitro_sandbox"):
        parse_raw(text)


def test_task_agent_selector_is_preserved_for_dispatch_not_standing_render():
    manifest, resources = render(source('nitro_sandbox = {mode = "isolated"}', kind="agent"))
    assert manifest.serialized["workloads"][0]["nitro_sandbox"]["mode"] == "isolated"
    assert resources == []


def test_confidential_task_selector_defers_policy_to_the_final_run_pod():
    manifest, resources = render(source('nitro_sandbox = {mode = "confidential"}', kind="agent"))
    assert manifest.serialized["workloads"][0]["nitro_sandbox"] == {
        "mode": "confidential",
        "network_isolated": False,
        "policy": "",
    }
    assert resources == []


@pytest.mark.parametrize(
    "block",
    [
        'nitro_sandbox = {mode = "confidential", policy = "generated=="}',
        'nitro_sandbox = {mode = "isolated", policy = "custom=="}',
        'nitro_sandbox = {mode = "isolated", network_isolated = true}',
    ],
)
def test_task_agent_variants_requiring_final_run_policy_or_network_workflows_are_rejected(block):
    with pytest.raises(ManifestError, match="task-family agents"):
        parse_raw(source(block, kind="agent"))


@pytest.mark.parametrize(
    "kind,extra",
    [
        ("deployment", ""),
        ("statefulset", 'storage_size = "1Gi"'),
        ("cronjob", 'schedule = "0 * * * *"'),
        ("agent", 'run_family = "service"'),
        ("workflow", 'workflow_type = "work"\ntask_queue = "work"'),
        ("task", ""),
    ],
)
def test_ordinary_controller_has_no_dedicated_nitro_scheduling(kind, extra):
    _, resources = render(source(kind=kind, extra=extra))
    pod = template(resources)["spec"]
    assert "runtimeClassName" not in pod
    assert "tolerations" not in pod


def test_final_manifest_selection_reuses_scheduling_without_duplicate_tolerations():
    from copy import deepcopy

    from astrolift_manifest.render import _apply_nitro_sandbox

    manifest, resources = render(source('nitro_sandbox = {mode = "confidential", policy = "generated=="}'))
    original = deepcopy(resources)
    _apply_nitro_sandbox(manifest.workloads[0], resources)
    assert resources == original
