"""The source-hosted app's SQLite seed requires a real ephemeral mount."""

from dataclasses import replace

import pytest

from astrolift_manifest.render import _pod_spec
from astrolift_manifest.tests.test_render import _deployment_workload


def render(volumes=()):
    return _pod_spec(
        replace(_deployment_workload(), volumes=volumes),
        image_tag="v1",
        image_repository="registry.test/app",
        image_digest="",
    )


def volume(**kwargs):
    return {"name": "data", "kind": "empty_dir", "mount_path": "/data", **kwargs}


def test_no_volume_pod_is_byte_equivalent():
    import json

    from astrolift_manifest.render import _render_container, gpu_scheduling

    workload = _deployment_workload()
    previous = {
        **gpu_scheduling(workload),
        "containers": [
            _render_container(
                c,
                workload=workload,
                image_tag="v1",
                image_repository="registry.test/app",
                image_digest="",
                env_from_secret_refs=None,
            )
            for c in workload.containers
        ],
    }
    assert json.dumps(render()) == json.dumps(previous)


def test_empty_directory_has_actual_volume_and_mount():
    pod = render((volume(size_limit="1Gi"),))
    assert pod["volumes"] == [{"name": "data", "emptyDir": {"sizeLimit": "1Gi"}}]
    assert pod["containers"][0]["volumeMounts"] == [{"name": "data", "mountPath": "/data"}]
    assert not any("persistentVolumeClaim" in v for v in pod["volumes"])


def test_explicit_filesystem_group_makes_scratch_writable_to_image_user():
    pod = _pod_spec(
        replace(_deployment_workload(), volumes=(volume(),), fs_group=10001),
        image_tag="v1",
        image_repository="registry.test/app",
        image_digest="",
    )
    assert pod["securityContext"] == {"fsGroup": 10001}


@pytest.mark.parametrize(
    "declarations",
    [
        (volume(name="bad/name"),),
        (volume(name="UPPER"),),
        (volume(mount_path="/"),),
        (volume(mount_path="/data/../proc"),),
        (volume(mount_path="/var/run/secrets/kubernetes.io/serviceaccount"),),
        (volume(mount_path="/proc/1"),),
        (volume(mount_path="/var"),),
        (volume(mount_path="/data//nested"),),
        (volume(), volume(mount_path="/other")),
        (volume(), volume(name="other")),
    ],
)
def test_unsafe_or_duplicate_scratch_mount_refuses(declarations):
    with pytest.raises(ValueError, match="empty_dir"):
        render(declarations)


def test_no_new_reference_to_platform_secrets_or_persistent_claims():
    assert render((dict(volume(), kind="secret", source_name="brain-other-app"),)) == render()
