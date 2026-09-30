from copy import deepcopy
from types import SimpleNamespace

import pytest

from astrolift_agents.services.managed_box_manifest import (
    AUTHORITY_FILE,
    render_managed_box_job,
    render_managed_box_pvc,
)
from astrolift_dispatch.model_gateway import ModelGateway

IDE = "registry.example/ide@sha256:" + "a" * 64
BROWSER = "registry.example/browser@sha256:" + "b" * 64


def box(**spec):
    return SimpleNamespace(
        guid="12345678-1234-1234-1234-123456789abc",
        environment_spec=SimpleNamespace(
            **{"runtime": "calliope-managed-ide", "image_tag": IDE, "env_vars": {}, "gpu": 0, **spec}
        ),
    )


def render(**changes):
    return render_managed_box_job(
        **{
            "box": box(),
            "image": IDE,
            "browser_image": BROWSER,
            "namespace": "agents-org",
            "job_name": "managed-box",
            "claim_name": "managed-data",
            "authority_secret_name": "box-authority",
            **changes,
        }
    )


def pod(job):
    return job["spec"]["template"]["spec"]


def env(container):
    values = container["env"]
    assert len(values) == len({entry["name"] for entry in values})
    return {entry["name"]: entry for entry in values}


def test_actual_supervisors_and_health_require_extension_activation():
    job = render()
    runtime, browser = pod(job)["containers"]
    assert runtime["command"] == ["/bin/sh", "/opt/calliope/scripts/managed-runtime/server.sh"]
    assert browser["command"] == ["node", "/opt/calliope-managed-runtime/browser.cjs"]
    for container, health in (
        (runtime, ["/opt/calliope/vscode-server/node", "/opt/calliope/scripts/managed-runtime/runtime.cjs"]),
        (browser, ["node", "/opt/calliope-managed-runtime/runtime.cjs"]),
    ):
        for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
            assert container[probe]["exec"]["command"] == health
        assert (
            container["startupProbe"]["periodSeconds"] * container["startupProbe"]["failureThreshold"] == 180
        )
        assert "ports" not in container
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["parallelism"] == 1
    assert pod(job)["restartPolicy"] == "Never"
    assert "activeDeadlineSeconds" not in job["spec"]
    assert not pod(job).get("hostNetwork")


def test_one_rwop_claim_preserves_both_profiles_and_workspace_beyond_job_cleanup():
    claim = render_managed_box_pvc(
        box=box(),
        namespace="agents-org",
        claim_name="managed-data",
        storage_class_name="retained-csi",
        capacity="20Gi",
    )
    assert claim["spec"]["accessModes"] == ["ReadWriteOncePod"]
    assert claim["spec"]["storageClassName"] == "retained-csi"
    assert claim["spec"]["resources"]["requests"]["storage"] == "20Gi"
    assert "ownerReferences" not in claim["metadata"]
    job = render()
    runtime, browser = pod(job)["containers"]
    state = {"name": "managed-data", "mountPath": "/state", "subPath": "state"}
    assert state in runtime["volumeMounts"] and state in browser["volumeMounts"]
    assert {"name": "managed-data", "mountPath": "/workspace", "subPath": "workspace"} in runtime[
        "volumeMounts"
    ]
    assert not any(m["mountPath"] == "/workspace" for m in browser["volumeMounts"])
    assert [v for v in pod(job)["volumes"] if "persistentVolumeClaim" in v] == [
        {"name": "managed-data", "persistentVolumeClaim": {"claimName": claim["metadata"]["name"]}}
    ]
    assert [v for v in pod(job)["volumes"] if "emptyDir" in v] == [
        {"name": "browser-shm", "emptyDir": {"medium": "Memory", "sizeLimit": "512Mi"}}
    ]
    (init,) = pod(job)["initContainers"]
    assert init["image"] == IDE
    assert init["command"] == ["/bin/sh", "-ec", "umask 077; mkdir -p /data/state /data/workspace"]
    assert init["volumeMounts"] == [{"name": "managed-data", "mountPath": "/data"}]
    assert "env" not in init


def test_authority_is_a_live_projected_file_only_in_primary_and_pod_identity_cannot_be_overridden():
    overrides = {
        name: "attacker"
        for name in (
            "CALLIOPE_MANAGED_STATE_ROOT",
            "CALLIOPE_MANAGED_WORKSPACE",
            "CALLIOPE_MANAGED_PORT",
            "CALLIOPE_MANAGED_SERVER_ROOT",
            "CALLIOPE_MANAGED_RUNTIME_ID",
            "ASTROLIFT_POD_UID",
            "ASTROLIFT_POD_NAME",
            "ASTROLIFT_POD_NAMESPACE",
            "ASTROLIFT_MANAGED_AUTHORITY_FILE",
            "ASTROLIFT_CONTAINER_NAME",
        )
    }
    job = render(box=box(env_vars=overrides), secret_env_names=list(overrides))
    runtime, browser = pod(job)["containers"]
    values = env(runtime)
    assert values["CALLIOPE_MANAGED_STATE_ROOT"]["value"] == "/state"
    assert values["CALLIOPE_MANAGED_WORKSPACE"]["value"] == "/workspace"
    assert values["CALLIOPE_MANAGED_PORT"]["value"] == "8071"
    assert values["CALLIOPE_MANAGED_SERVER_ROOT"]["value"] == "/opt/calliope/vscode-server"
    assert "CALLIOPE_MANAGED_RUNTIME_ID" not in values
    assert values["ASTROLIFT_CONTAINER_NAME"]["value"] == "runtime"
    assert values["ASTROLIFT_MANAGED_AUTHORITY_FILE"]["value"] == AUTHORITY_FILE
    for name, field in (
        ("ASTROLIFT_POD_UID", "metadata.uid"),
        ("ASTROLIFT_POD_NAME", "metadata.name"),
        ("ASTROLIFT_POD_NAMESPACE", "metadata.namespace"),
    ):
        assert values[name]["valueFrom"] == {"fieldRef": {"apiVersion": "v1", "fieldPath": field}}
    (authority,) = (v for v in pod(job)["volumes"] if v["name"] == "runtime-authority")
    assert authority["projected"] == {
        "defaultMode": 0o440,
        "sources": [
            {
                "secret": {
                    "name": "box-authority",
                    "items": [{"key": "authority.json", "path": "authority.json"}],
                }
            }
        ],
    }
    (mount,) = (m for m in runtime["volumeMounts"] if m["name"] == "runtime-authority")
    assert mount == {"name": "runtime-authority", "mountPath": "/run/astrolift-authority", "readOnly": True}
    assert not any(m["name"] == "runtime-authority" for m in browser["volumeMounts"])
    assert all(entry["name"].startswith("CALLIOPE_MANAGED_") for entry in browser["env"])


def test_approved_model_env_and_secret_references_only_reach_primary_without_mutating_inputs():
    model = [{"name": "MODEL_REGION", "value": "region"}]
    resolved = [{"name": "PROJECT_SETTING", "value": "project"}]
    before = deepcopy((model, resolved))
    job = render(
        model_env=model,
        spec_env=resolved,
        secret_env_names=["MODEL_TOKEN"],
        model_service_account="model-runtime",
    )
    runtime, browser = pod(job)["containers"]
    assert env(runtime)["MODEL_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "managed-box-secrets",
        "key": "MODEL_TOKEN",
    }
    assert env(runtime)["MODEL_REGION"]["value"] == "region"
    assert env(runtime)["PROJECT_SETTING"]["value"] == "project"
    assert not ({"MODEL_TOKEN", "MODEL_REGION", "PROJECT_SETTING"} & env(browser).keys())
    assert pod(job)["serviceAccountName"] == "model-runtime"
    runtime["env"][0]["value"] = "changed"
    assert (model, resolved) == before


def test_existing_gateway_wiring_only_reaches_primary():
    gateway = ModelGateway(connection=None, providers=("openai",), spec_slug="managed")
    runtime, browser = pod(render(model_gateway=gateway))["containers"]
    expected = {item["name"]: item for item in gateway.env("managed-box-secrets")}
    assert expected.items() <= env(runtime).items()
    assert not (expected.keys() & env(browser).keys())


def test_shared_hardening_and_bounded_resources_include_init_without_default_agent_uid(settings):
    settings.AGENT_RUNTIME_CLASS = "sandboxed"
    job = render()
    spec = pod(job)
    assert spec["runtimeClassName"] == "sandboxed"
    assert spec["automountServiceAccountToken"] is False
    assert spec["securityContext"] == {
        "seccompProfile": {"type": "RuntimeDefault"},
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "runAsGroup": 1000,
        "fsGroup": 1000,
        "fsGroupChangePolicy": "OnRootMismatch",
    }
    for container in [*spec["containers"], *spec["initContainers"]]:
        assert container["securityContext"] == {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "runAsGroup": 1000,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
        }
        assert {"cpu", "memory"} <= container["resources"]["requests"].keys()
        assert {"cpu", "memory"} <= container["resources"]["limits"].keys()
    assert [c["resources"]["requests"]["memory"] for c in spec["containers"]] == ["1536Mi", "768Mi"]


@pytest.mark.parametrize("argument", ["image", "browser_image"])
@pytest.mark.parametrize(
    "image", ["", "registry.example/image:latest", "registry.example/image@sha256:short"]
)
def test_missing_or_mutable_images_fail_closed(argument, image):
    with pytest.raises(ValueError, match="immutable"):
        render(**{argument: image})


@pytest.mark.parametrize("spec", [{"runtime": "python"}, {"image_tag": ""}])
def test_managed_runtime_must_be_explicit(spec):
    with pytest.raises(ValueError, match="explicit managed runtime"):
        render(box=box(**spec))


@pytest.mark.parametrize(
    "field,value", [("claim_name", "../claim"), ("namespace", ""), ("authority_secret_name", "bad/name")]
)
def test_resource_references_cannot_escape_the_selected_namespace(field, value):
    with pytest.raises(ValueError, match="DNS labels"):
        render(**{field: value})


@pytest.mark.parametrize(
    "changes", [{"storage_class_name": ""}, {"capacity": "0Gi"}, {"capacity": "unbounded"}]
)
def test_storage_requires_explicit_class_and_positive_capacity(changes):
    with pytest.raises(ValueError):
        render_managed_box_pvc(
            **{
                "box": box(),
                "namespace": "agents-org",
                "claim_name": "managed-data",
                "storage_class_name": "retained-csi",
                "capacity": "20Gi",
                **changes,
            }
        )
